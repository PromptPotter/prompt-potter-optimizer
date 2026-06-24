"""Cross-cycle PoBB comparison on Rasch ability θ with adaptive top-up.

``elevate_to_decisive(arms, session, dataset, ...)`` — given a group of named
configs, estimate each arm's difficulty-adjusted ability θ from
``MeasurementArchive`` against the dataset-stable δ bank, run PoBB on θ, and top
up under-resolved arms via ``score_search_point`` until the joint posterior is
decisive (``max P(best) >= 1 - epsilon``) or the topup budget is exhausted.

θ on the **fixed** δ bank is cross-cycle comparable where raw subset accuracy is
not (fitness-comparability slice 2): two cycles' winners measured on different
sample subsets rank by ability, not by who drew the easier samples. The bank is
the per-dataset grade-A δ ruler, persisted under ``measurements/delta_bank_*``
(``_resolve_delta_bank``); below the cold-start floor it degrades to a **flat
ruler** (δ≡0 ⇒ θ == logit accuracy) so there is one code path, not two.

Engine: ``shared.statistics.posterior_best_from_normals`` over the θ posteriors.
Top-up rule: ``n_min_per_arm`` banked-observation floor first, then the arm with
the widest θ posterior — measuring the banked sample whose difficulty sits
nearest that arm's θ (peak Fisher information, p≈½), not a random sample.

``discover_compare_arms`` resolves the input arms — either from explicit
cycle ids or by walking the active family — and is the natural front-end
to ``elevate_to_decisive`` for the CLI's ``compare`` command.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import numpy as np

from promptpotter.application.intelligence.exploration import (
    DELTA_RULER_MIN_SAMPLES,
    Observation,
    fit_rasch,
    fit_theta_given_delta,
)
from promptpotter.application.intelligence.hard_sample_archive import build_archive_observations
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.config.settings import POBB_DEFAULT_EPSILON
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.infrastructure.store import archive_views, root_cycle_id
from promptpotter.shared.statistics import posterior_best_from_normals

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.store import Stores

logger = logging.getLogger(__name__)

__all__ = [
    "CompareArmsResult",
    "ElevationResult",
    "discover_compare_arms",
    "elevate_to_decisive",
]


@dataclass(frozen=True)
class CompareArmsResult:
    """``error is None`` ⇒ ``arms`` is non-empty and ready for ``elevate_to_decisive``.

    Otherwise ``error`` carries an operator-facing message; the CLI prefixes
    it with ``ERROR:``. ``cycle_ids`` is the resolved list (post-family
    discovery) for logging / diagnostics.
    """

    arms: dict[str, JobSearchPoint]
    cycle_ids: list[str] = field(default_factory=list)
    error: str | None = None


def discover_compare_arms(
    session: Session,
    active_cycle_id: str,
    cycle_ids: list[str],
    *,
    all_family: bool,
) -> CompareArmsResult:
    """Resolve the ``compare`` arm set from explicit ids or by family walk.

    Two modes, picked by ``all_family or not cycle_ids``:

    - **Discovery**: walk every cycle whose ``root_cycle_id`` matches the
      active cycle's family, skip ones missing an index or
      ``final.winner_pipeline_params`` (logged, not fatal).
    - **Explicit**: every id must resolve to an index with a winner — first
      miss returns the error path.

    Either way, an empty ``arms`` returns ``error="no cycles ..."`` so the
    caller doesn't have to recheck.
    """
    discover_family = all_family or not cycle_ids

    if discover_family:
        family_root = root_cycle_id(active_cycle_id)
        summaries = session.store.campaigns.list_all()
        cycle_ids = [
            s["cycle_id"]
            for s in summaries
            if s["campaign_id"] == session.campaign_id
            and root_cycle_id(s["cycle_id"]) == family_root
        ]
        if not cycle_ids:
            return CompareArmsResult(
                arms={},
                cycle_ids=[],
                error=f"no cycles found under family {family_root}",
            )
        logger.info("compare: discovered %d cycle(s) under family %s", len(cycle_ids), family_root)

    arms: dict[str, JobSearchPoint] = {}
    for cid in cycle_ids:
        idx = session.store.campaigns.load(session.campaign_id, cid)
        if idx is None:
            if discover_family:
                logger.info("compare: skipping %s (not found)", cid)
                continue
            return CompareArmsResult(arms={}, cycle_ids=cycle_ids, error=f"cycle {cid} not found")
        winner_pp = (idx.get("final") or {}).get("winner_pipeline_params")
        if not winner_pp:
            if discover_family:
                logger.info("compare: skipping %s (no final.winner_pipeline_params)", cid)
                continue
            return CompareArmsResult(
                arms={},
                cycle_ids=cycle_ids,
                error=f"cycle {cid} has no final.winner_pipeline_params",
            )
        arms[cid] = JobSearchPoint(pipeline_params=winner_pp)

    if not arms:
        return CompareArmsResult(
            arms={},
            cycle_ids=cycle_ids,
            error="no cycles with final.winner_pipeline_params to compare",
        )

    return CompareArmsResult(arms=arms, cycle_ids=cycle_ids)


_COLD_ARM_THETA_SE = 2.0  # diffuse θ posterior for an arm with no banked observation


@dataclass(frozen=True)
class ElevationResult:
    decision: Literal["decisive", "exhausted", "aborted"]
    best_arm: str
    p_best: dict[str, float]
    arm_theta: dict[str, float]
    arm_theta_se: dict[str, float]
    arm_n: dict[str, int]
    topups_per_arm: dict[str, int]
    note: str


def _resolve_delta_bank(session: Session) -> dict[int, float] | None:
    """The dataset-stable δ ruler θ is compared on — load the persisted bank, else
    calibrate it once from the grade-A archive and persist it.

    ``None`` (cold start) when the dataset is unscoped or the grade-A corpus is
    below ``DELTA_RULER_MIN_SAMPLES`` — the caller then pins a flat ruler so the
    comparison degrades to logit-accuracy on one path. Pinning to the *persisted*
    bank (not a fresh fit every call) is what makes the scale stable across
    cycles; the bank is also the clean corpus L4 later ingests. Incremental
    anchor-refresh as the archive grows is the durable follow-up — for now a
    refresh means deleting the file (the next compare recalibrates).
    """
    dataset = session.dataset_name
    if dataset is None:
        return None
    stores, backend_id = session.store, session.backend_id
    banked = stores.archive.load_delta_bank(backend_id, dataset)
    if banked is not None:
        return {int(k): float(v) for k, v in banked["delta"].items()}

    obs = build_archive_observations(stores, backend_id, dataset_name=dataset, min_grade="A")
    if not obs:
        return None
    ruler = fit_rasch(obs)
    if len(ruler.delta) < DELTA_RULER_MIN_SAMPLES:
        return None
    stores.archive.save_delta_bank(
        backend_id,
        dataset,
        {
            "dataset_name": dataset,
            "backend_id": backend_id,
            "min_grade": "A",
            "n_candidates": len(ruler.theta),
            "n_samples": len(ruler.delta),
            "delta": {str(sid): round(d, 6) for sid, d in ruler.delta.items()},
        },
    )
    logger.info("elevate: calibrated δ bank for %s (%d samples)", dataset, len(ruler.delta))
    return ruler.delta


def _load_arm_hits(
    jsp: JobSearchPoint,
    stores: Stores,
    backend_id: str,
    pipeline_schema: PipelineSchema,
    *,
    dataset_name: str | None,
) -> dict[int, bool]:
    """Pull every archived ``(sample_id, hit)`` for *jsp* across cycles → ``{sid: hit}``.

    Identity is exact-prefix node-config match plus ``dataset_name`` scope — the
    same JSP measured in two cycles of the same dataset contributes both, but a
    cross-dataset sibling with identical node-configs is excluded (different
    sample population, same integer ids). Dedup by ``sample_id`` (first wins) —
    same sample × same JSP × same dataset is deterministic. Hits, not fitness:
    θ is fit on per-sample outcomes against the δ ruler.
    """
    chain = pipeline_schema.node_configs(jsp.pipeline_params or {})
    chain_len = len(chain)
    seen: dict[int, bool] = {}
    for entry, match_len in archive_views.find_by_prefix(
        stores, backend_id, chain, dataset_name=dataset_name
    ):
        if match_len < chain_len:
            break
        detail = archive_views.load_run(stores, backend_id, entry["run_id"])
        if detail is None:
            continue
        for item in detail.get("measurements", []):
            sid = item.get("sample_id")
            if sid is None or item.get("predicted") == "ERROR":
                continue
            seen.setdefault(int(sid), bool(item.get("hit")))
    return seen


def _fit_arm_thetas(
    arm_hits: dict[str, dict[int, bool]], delta: dict[int, float]
) -> dict[str, tuple[float, float]]:
    """θ + θ_se per arm on the fixed ruler ``delta``; an arm with no banked
    observation gets a diffuse posterior so it never spuriously wins and is
    floored into top-up first."""
    obs = [
        Observation(name, sid, hit) for name, hits in arm_hits.items() for sid, hit in hits.items()
    ]
    fit = fit_theta_given_delta(obs, delta)
    return {name: fit.get(name, (0.0, _COLD_ARM_THETA_SE)) for name in arm_hits}


def _banked_n(arm_hits: dict[int, bool], bank_sids: set[int]) -> int:
    return len(arm_hits.keys() & bank_sids)


def _pick_topup(
    thetas: dict[str, tuple[float, float]],
    arm_hits: dict[str, dict[int, bool]],
    attempted: dict[str, set[int]],
    delta: dict[int, float],
    scorable: set[int],
    n_min: int,
    rng: np.random.Generator,
) -> tuple[str, int] | None:
    """Choose ``(arm, sample_id)`` to measure next, or ``None`` when no arm has an
    unmeasured banked sample left. Floor first (least-resolved arm under ``n_min``
    banked obs), then the widest-θ arm; the sample is the one whose difficulty sits
    nearest that arm's θ — peak Fisher information on θ (a flat ruler ties, broken
    randomly, recovering uniform top-up for the cold case)."""
    bank_sids = set(delta)
    pools = {a: scorable - arm_hits[a].keys() - attempted[a] for a in arm_hits}
    eligible = {a: p for a, p in pools.items() if p}
    if not eligible:
        return None
    banked_n = {a: _banked_n(arm_hits[a], bank_sids) for a in eligible}
    under = [a for a in eligible if banked_n[a] < n_min]
    arm = (
        min(under, key=lambda a: banked_n[a])
        if under
        else max(eligible, key=lambda a: thetas[a][1])
    )
    theta = thetas[arm][0]
    sid = min(eligible[arm], key=lambda s: (abs(delta[s] - theta), rng.random()))
    return arm, sid


def _format_note(
    decision: str,
    best: str,
    p_best: dict[str, float],
    thetas: dict[str, tuple[float, float]],
    arm_n: dict[str, int],
    topups: dict[str, int],
) -> str:
    parts = []
    for a in thetas:
        theta, se = thetas[a]
        marker = "*" if a == best else " "
        parts.append(
            f"{marker}{a}: n={arm_n[a]} +{topups[a]}tu θ={theta:+.2f}±{se:.2f} P={p_best.get(a, 0.0):.2f}"
        )
    return f"{decision} winner={best} | " + " | ".join(parts)


async def elevate_to_decisive(
    arms: dict[str, JobSearchPoint],
    session: Session,
    dataset: list[Sample],
    *,
    epsilon: float = POBB_DEFAULT_EPSILON,
    max_topups: int = 16,
    n_min_per_arm: int = 4,
    rng: np.random.Generator | None = None,
    stream: bool = False,
) -> ElevationResult:
    """PoBB-compare *arms* on Rasch ability θ with adaptive top-up.

    θ is fit per arm against the dataset-stable δ bank (``_resolve_delta_bank``;
    flat ruler ⇒ logit accuracy on cold start), making cycles measured on
    different subsets comparable. Top-up rule: any arm under ``n_min_per_arm``
    banked observations floors first, then the widest-θ arm; the chosen sample is
    the banked one nearest that arm's θ (peak Fisher information). Each top-up
    measures it via ``score_search_point``; new measurements land in the archive
    (cache-aware) and sharpen that arm's θ. Loop until ``max P(best) >= 1 -
    epsilon`` (decisive) or ``sum(topups) >= max_topups`` (exhausted).
    ``max_topups < 0`` removes the cap — the loop runs until decisive or
    ``KeyboardInterrupt`` / ``CancelledError`` aborts it. ``stream=True`` logs one
    line per topup.
    """
    if not arms:
        raise ValueError("elevate_to_decisive requires at least one arm")
    if session.pipeline_schema is None:
        raise ValueError("session.pipeline_schema required")
    if rng is None:
        rng = np.random.default_rng(0)

    schema = session.pipeline_schema
    stores = session.store
    backend_id = session.backend_id

    delta = _resolve_delta_bank(session)
    if delta is None:
        # Cold start: a flat ruler over every dataset sample ⇒ θ == logit accuracy,
        # so the comparison degrades to subset-relative accuracy on one code path.
        delta = {s.id: 0.0 for s in dataset}
    bank_sids = set(delta)
    by_id = {s.id: s for s in dataset}
    scorable = bank_sids & by_id.keys()

    arm_hits: dict[str, dict[int, bool]] = {
        name: _load_arm_hits(jsp, stores, backend_id, schema, dataset_name=session.dataset_name)
        for name, jsp in arms.items()
    }
    attempted: dict[str, set[int]] = {name: set() for name in arms}
    topups = dict.fromkeys(arms, 0)

    def _arm_n() -> dict[str, int]:
        return {a: _banked_n(arm_hits[a], bank_sids) for a in arms}

    if len(arms) == 1:
        only = next(iter(arms))
        thetas = _fit_arm_thetas(arm_hits, delta)
        return ElevationResult(
            decision="decisive",
            best_arm=only,
            p_best={only: 1.0},
            arm_theta={only: thetas[only][0]},
            arm_theta_se={only: thetas[only][1]},
            arm_n={only: _banked_n(arm_hits[only], bank_sids)},
            topups_per_arm=topups,
            note=f"single arm {only} (n={_banked_n(arm_hits[only], bank_sids)})",
        )

    def _result(
        decision: str, best: str, p_best: dict[str, float], extra: str = ""
    ) -> ElevationResult:
        thetas = _fit_arm_thetas(arm_hits, delta)
        arm_n = _arm_n()
        return ElevationResult(
            decision=decision,  # type: ignore[arg-type]
            best_arm=best,
            p_best=p_best,
            arm_theta={a: thetas[a][0] for a in arms},
            arm_theta_se={a: thetas[a][1] for a in arms},
            arm_n=arm_n,
            topups_per_arm=topups,
            note=_format_note(decision, best, p_best, thetas, arm_n, topups) + extra,
        )

    while True:
        thetas = _fit_arm_thetas(arm_hits, delta)
        p_best = posterior_best_from_normals(thetas, rng=rng)
        best = max(p_best, key=lambda k: p_best[k])
        if p_best[best] >= 1.0 - epsilon:
            return _result("decisive", best, p_best)
        if max_topups >= 0 and sum(topups.values()) >= max_topups:
            return _result("exhausted", best, p_best)

        pick = _pick_topup(thetas, arm_hits, attempted, delta, scorable, n_min_per_arm, rng)
        if pick is None:
            return _result("exhausted", best, p_best, extra=" (bank coverage)")
        arm, sid = pick
        attempted[arm].add(sid)
        logger.info("elevate: arm=%s topup sample_id=%d", arm, sid)

        try:
            # ``on_sample_scored=None`` is deliberate, not an omission — the
            # ``elevate_to_decisive`` flow is driven by ``promptpotter compare``
            # (a one-shot CLI verb outside the optimize loop). Its operator
            # signal is the per-topup ``stream`` line below plus the
            # ``logger.info("elevate: arm=… topup sample_id=…")`` line above.
            # The required-keyword guardrail on ``score_search_point`` forces
            # this site to declare its intent explicitly so the silence is
            # documented, not accidental.
            results, _scores, _esc = await score_search_point(
                arms[arm],
                [by_id[sid]],
                session,
                label="elevate",
                on_sample_scored=None,
                on_sample_starting=None,
            )
        except (KeyboardInterrupt, asyncio.CancelledError):
            return _result("aborted", best, p_best)
        if results and results[0].get("hit") is not None and results[0].get("predicted") != "ERROR":
            arm_hits[arm][sid] = bool(results[0].get("hit"))
            topups[arm] += 1
            if stream:
                # Recompute θ + p_best for the streamed line so the operator sees
                # the leaderboard converge in real time. Cheap, once per topup.
                cur_thetas = _fit_arm_thetas(arm_hits, delta)
                cur_p = posterior_best_from_normals(cur_thetas, rng=rng)
                cur_best = max(cur_p, key=lambda k: cur_p[k])
                logger.debug(
                    "[topup #%d] arm=%s n=%d θ=%+.2f P_best=%.2f (%s)",
                    sum(topups.values()),
                    arm,
                    _banked_n(arm_hits[arm], bank_sids),
                    cur_thetas[arm][0],
                    cur_p[cur_best],
                    cur_best,
                )
        else:
            logger.warning(
                "elevate: arm=%s sample_id=%d returned no score; skipped (attempted, not banked)",
                arm,
                sid,
            )
