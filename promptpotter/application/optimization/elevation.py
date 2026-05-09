"""Cross-cycle PoBB comparison with adaptive top-up.

``elevate_to_decisive(arms, session, dataset, ...)`` — given a group of named
configs, score each from ``MeasurementArchive``, run PoBB, and top up
under-measured arms via ``score_search_point`` until the joint posterior is
decisive (``max P(best) >= 1 - epsilon``) or the topup budget is exhausted.

Engine: ``shared.statistics.posterior_best_probabilities``. Top-up rule:
``n_min_per_arm`` floor first, then the arm with largest posterior SE.

``discover_compare_arms`` resolves the input arms — either from explicit
cycle ids or by walking the active family — and is the natural front-end
to ``elevate_to_decisive`` for the CLI's ``compare`` command.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import numpy as np

from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.infrastructure.store import root_cycle_id
from promptpotter.shared.statistics import posterior_best_probabilities

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive

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
        summaries = session.store.campaigns.list_all(session.backend_id)
        cycle_ids = [
            s["campaign_id"] for s in summaries if root_cycle_id(s["campaign_id"]) == family_root
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
        idx = session.store.campaigns.load(session.backend_id, cid)
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


@dataclass(frozen=True)
class ElevationResult:
    decision: Literal["decisive", "exhausted", "aborted"]
    best_arm: str
    p_best: dict[str, float]
    score_histories: dict[str, list[float]]
    topups_per_arm: dict[str, int]
    note: str


def _load_arm_history(
    jsp: JobSearchPoint,
    archive: MeasurementArchive,
    backend_id: str,
    pipeline_schema: PipelineSchema,
) -> tuple[list[float], set[int]]:
    """Pull every archived ``(sample_id, score)`` for *jsp* across cycles.

    Identity is exact-prefix node-config match (dataset-independent), so the
    same JSP measured against differently-shaped scoring sets in two cycles
    contributes both. Dedup by ``sample_id`` — same sample × same JSP is
    deterministic, redundant duplicates are dropped on the first encounter.
    """
    chain = pipeline_schema.node_configs(jsp.pipeline_params or {})
    chain_len = len(chain)
    seen: dict[int, float] = {}
    for entry, match_len in archive.find_by_node_configs(backend_id, chain):
        if match_len < chain_len:
            break
        detail = archive.load_by_id(backend_id, entry["run_id"])
        if detail is None:
            continue
        for item in detail.get("measurements", []):
            sid = item.get("sample_id")
            fitness = item.get("fitness")
            if sid is None or fitness is None or item.get("predicted") == "ERROR":
                continue
            seen.setdefault(int(sid), float(fitness))
    return list(seen.values()), set(seen.keys())


def _arm_se(scores: list[float]) -> float:
    n = len(scores)
    if n == 0:
        return 1.0
    if n == 1:
        return 0.5
    arr = np.asarray(scores, dtype=np.float64)
    return float(math.sqrt(arr.var(ddof=1) / n))


def _pick_arm_to_topup(histories: dict[str, list[float]], n_min: int) -> str:
    under = [a for a, h in histories.items() if len(h) < n_min]
    if under:
        return min(under, key=lambda a: len(histories[a]))
    return max(histories, key=lambda a: _arm_se(histories[a]))


def _format_note(
    decision: str,
    best: str,
    p_best: dict[str, float],
    histories: dict[str, list[float]],
    topups: dict[str, int],
) -> str:
    parts = []
    for a in histories:
        h = histories[a]
        n = len(h)
        mean = sum(h) / n if n else 0.0
        marker = "*" if a == best else " "
        parts.append(
            f"{marker}{a}: n={n} +{topups[a]}tu mean={mean:.3f} P={p_best.get(a, 0.0):.2f}"
        )
    return f"{decision} winner={best} | " + " | ".join(parts)


async def elevate_to_decisive(
    arms: dict[str, JobSearchPoint],
    session: Session,
    dataset: list[Sample],
    *,
    epsilon: float = 0.05,
    max_topups: int = 16,
    n_min_per_arm: int = 4,
    rng: np.random.Generator | None = None,
    stream: bool = False,
) -> ElevationResult:
    """PoBB-compare *arms* with adaptive top-up.

    Top-up rule: any arm under ``n_min_per_arm`` floor first, then the arm
    with largest posterior SE. Each top-up measures one previously-unseen
    sample on the chosen arm via ``score_search_point``; new measurements
    land in the archive (cache-aware, archive-aware) and grow that arm's
    history. Loop until ``max P(best) >= 1 - epsilon`` (decisive) or
    ``sum(topups) >= max_topups`` (exhausted). ``max_topups < 0`` removes
    the cap — the loop runs until decisive or ``KeyboardInterrupt`` /
    ``CancelledError`` aborts it. ``stream=True`` prints one line per topup.
    """
    if not arms:
        raise ValueError("elevate_to_decisive requires at least one arm")
    if session.pipeline_schema is None:
        raise ValueError("session.pipeline_schema required")
    if rng is None:
        rng = np.random.default_rng(0)

    schema = session.pipeline_schema
    archive = session.store.archive
    backend_id = session.backend_id

    histories: dict[str, list[float]] = {}
    measured: dict[str, set[int]] = {}
    for name, jsp in arms.items():
        h, sids = _load_arm_history(jsp, archive, backend_id, schema)
        histories[name] = h
        measured[name] = sids
    topups = dict.fromkeys(arms, 0)

    if len(arms) == 1:
        only = next(iter(arms))
        return ElevationResult(
            decision="decisive",
            best_arm=only,
            p_best={only: 1.0},
            score_histories=histories,
            topups_per_arm=topups,
            note=f"single arm {only} (n={len(histories[only])})",
        )

    while True:
        p_best = posterior_best_probabilities(histories, rng=rng)
        best = max(p_best, key=lambda k: p_best[k])
        if p_best[best] >= 1.0 - epsilon:
            return ElevationResult(
                decision="decisive",
                best_arm=best,
                p_best=p_best,
                score_histories=histories,
                topups_per_arm=topups,
                note=_format_note("decisive", best, p_best, histories, topups),
            )
        if max_topups >= 0 and sum(topups.values()) >= max_topups:
            return ElevationResult(
                decision="exhausted",
                best_arm=best,
                p_best=p_best,
                score_histories=histories,
                topups_per_arm=topups,
                note=_format_note("exhausted", best, p_best, histories, topups),
            )

        chosen = _pick_arm_to_topup(histories, n_min_per_arm)
        unmeasured = [s for s in dataset if s.id not in measured[chosen]]
        if not unmeasured:
            others = [
                a for a in arms if a != chosen and any(s.id not in measured[a] for s in dataset)
            ]
            if not others:
                return ElevationResult(
                    decision="exhausted",
                    best_arm=best,
                    p_best=p_best,
                    score_histories=histories,
                    topups_per_arm=topups,
                    note=_format_note("exhausted", best, p_best, histories, topups)
                    + " (dataset coverage)",
                )
            chosen = others[0]
            unmeasured = [s for s in dataset if s.id not in measured[chosen]]

        sample = unmeasured[int(rng.integers(0, len(unmeasured)))]
        logger.info("elevate: arm=%s topup sample_id=%d", chosen, sample.id)

        try:
            results, _scores, _flag, _esc = await score_search_point(
                arms[chosen], [sample], session, label="elevate"
            )
        except (KeyboardInterrupt, asyncio.CancelledError):
            return ElevationResult(
                decision="aborted",
                best_arm=best,
                p_best=p_best,
                score_histories=histories,
                topups_per_arm=topups,
                note=_format_note("aborted", best, p_best, histories, topups),
            )
        measured[chosen].add(sample.id)
        if results and (new_fitness := results[0].get("fitness")) is not None:
            histories[chosen].append(float(new_fitness))
            topups[chosen] += 1
            if stream:
                # Recompute p_best for the streamed line so the operator sees
                # the leaderboard converge in real time. Cheap (Monte-Carlo
                # over a few thousand draws), runs once per topup.
                cur_p = posterior_best_probabilities(histories, rng=rng)
                cur_best = max(cur_p, key=lambda k: cur_p[k])
                logger.debug(
                    "[topup #%d] arm=%s n=%d fitness=%.3f P_best=%.2f (%s)",
                    sum(topups.values()),
                    chosen,
                    len(histories[chosen]),
                    float(new_fitness),
                    cur_p[cur_best],
                    cur_best,
                )
        else:
            logger.warning(
                "elevate: arm=%s sample_id=%d returned no score; counted toward measured set",
                chosen,
                sample.id,
            )
