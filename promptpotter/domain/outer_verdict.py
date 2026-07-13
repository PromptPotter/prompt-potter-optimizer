"""The L4 outer reads: one sample's proxy vector, and the round's blocked-paired verdict.

An L4 outer round scores each meta-prompt variant across the panel's cells (one inner
cycle per (variant, cell)). Each cell yields a :class:`OuterSampleProxies` — the raw
signals the outer scoring formula composes. The round then pairs them:
the **cached round-0 origin** is the within-panel control — no config is ever re-measured
mid-run. :func:`compute_outer_verdict` computes, for the round's target variant, the paired
``(variant − origin)`` composite difference per cell, pooled across cells into an effect +
CI, and a three-way decision. Cells are the blocks; the pooling treats them as
exchangeable (a flat paired posterior across cells — per-cell n is 1, so inverse-variance
weighting degenerates to this; a random-effects refinement is the documented next step).

Pure domain: no I/O. The projection (`round_summary`) reads the cached round-0 origin
cells off disk and passes them in; this module never touches the filesystem.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from promptpotter.shared.statistics import (
    min_detectable_effect,
    paired_diff_posterior,
    t_critical,
)

DECISION_ADOPT = "adopt"
DECISION_REJECT = "reject"
DECISION_INCONCLUSIVE = "inconclusive"


class OuterSampleProxies(BaseModel):
    """One outer sample's observation vector — what a finished inner cycle says about the
    meta-prompt that ran it. The type IS the governing law that used to be prose in
    ``datasets/promptpotter-self/inner_tasks.json::description``.

    **Bounded** is enforced where it is provable: ``normalized_gain`` divides a move by the
    room available to make it (``max(origin, 1−origin) ≥ 0.5``), so ``[-1, 1]`` holds by
    construction and the ``ge``/``le`` below is a statement of fact rather than a clamp;
    the two quality terms are ``1 − mean(rate ∈ [0,1])``, and ``rounds_improved_frac`` is a
    share. The endpoint deltas and the three efficiency ratios are genuinely unbounded —
    a regressing meta-prompt goes negative, and lift-per-dollar has no natural ceiling — so
    they carry the one bound that always holds: **finite**. ``allow_inf_nan=False`` is what
    keeps a ``0/0`` or an ``x/0`` from reaching the scoring clamp, where ``min(1.0, nan)``
    short-circuits to ``1.0`` and a sample that measured nothing scores perfect.

    Every field is a *measurement*. There is no field that can be defaulted, because the
    absence of a measurement is not a value — a cycle that cannot fill this in is excluded
    (``InnerCycleUnscoreableError``), never scored on zeros.

    ``extra="forbid"`` + ``frozen`` make the emitted key set exactly the declared one, so it
    can be diffed against the outer dataset's ``observation_mappings``
    (``verify_outer_observation_contract``) rather than drifting from it silently."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # `after_N_rounds_delta` / `rounds_to_N` are wire keys: they name the pipeline_data
    # observation the outer scoring formula reads, so their spelling is not ours to normalize.
    first_round_delta: float = Field(allow_inf_nan=False)
    after_N_rounds_delta: float = Field(allow_inf_nan=False)  # noqa: N815
    rounds_to_N: int = Field(ge=0)  # noqa: N815
    normalized_gain: float = Field(ge=-1.0, le=1.0)
    cleanliness: float = Field(ge=0.0, le=1.0)
    diversity_health: float = Field(ge=0.0, le=1.0)
    rounds_improved_frac: float = Field(ge=0.0, le=1.0)
    delta_per_dollar: float = Field(allow_inf_nan=False)
    delta_per_candidate: float = Field(allow_inf_nan=False)
    delta_per_second: float = Field(allow_inf_nan=False)


# The observation keys one outer sample emits — DERIVED from the model, never hand-listed.
# Three lists used to drift independently (a `PROXY_KEYS` tuple with no importers, the literal
# dict `_compute_proxies` returned, and the dataset's `observation_mappings`); whether a drift
# bit loudly or silently depended on whether the scoring formula happened to name the drifted key.
OUTER_PROXY_KEYS: tuple[str, ...] = tuple(OuterSampleProxies.model_fields)


class CandidateInfo(BaseModel):
    """The minimal per-candidate facts the verdict needs (no RoundResult import)."""

    model_config = ConfigDict(frozen=True)

    candidate_id: str
    label: str
    changes_description: str
    composite_fitness: float
    is_winner: bool


class OuterCellEffect(BaseModel):
    """One cell's paired (variant − origin) composite difference."""

    model_config = ConfigDict(frozen=True)

    cell: str
    variant_fitness: float
    origin_fitness: float
    diff: float


class OuterVerdict(BaseModel):
    """The pooled blocked-paired verdict for a round's target variant."""

    model_config = ConfigDict(frozen=True)

    variant_id: str
    variant_label: str
    per_cell: list[OuterCellEffect]
    effect: float  # pooled mean paired (variant − origin) across cells
    se: float
    ci_lo: float
    ci_hi: float
    n_cells: int
    decision: str  # adopt | reject | inconclusive
    mde_remaining: float  # min detectable effect at the current cell count


def cell_fitness(rows: list[dict[str, Any]]) -> dict[str, float]:
    """``{cell_query: mean composite_fitness}`` from a candidate's per-cell rows, averaging
    REPLICATE rows per cell (``replicate_survivors``) so the blocked-paired diff carries one
    point per cell at any replication depth. Identity with last-wins at the n=1 default.

    The one shared pure extraction — callers reading a fresh round (``compute_outer_verdict``
    below) and callers reading an archived round doc off disk
    (``application/meta_champion/reducer.py``) both walk the same row shape.
    """
    acc: dict[str, list[float]] = {}
    for r in rows:
        cell = r.get("query")
        fit = r.get("fitness")
        if isinstance(cell, str) and isinstance(fit, int | float):
            acc.setdefault(cell, []).append(float(fit))
    return {cell: sum(v) / len(v) for cell, v in acc.items()}


def _pick_variant(candidates: list[CandidateInfo]) -> CandidateInfo | None:
    """The variant the verdict scores: the round's elected winner, or nothing.

    It used to fall back to ``max(composite_fitness)`` when no candidate was crowned — so
    ``OuterVerdict.decision`` could read ``adopt`` for an arm the θ election specifically
    declined, on the very surface the operator reads to judge a meta-prompt edit. A round
    that crowned nothing has no verdict; ``None`` is already a legal return.
    """
    return next((c for c in candidates if c.is_winner), None)


def compute_outer_verdict(
    all_candidate_results: dict[str, list[dict[str, Any]]],
    candidates: list[CandidateInfo],
    origin_cells: dict[str, float],
) -> OuterVerdict | None:
    """The round's blocked-paired verdict against the **cached round-0 origin**
    (*origin_cells*, supplied by the caller — round 0 is never re-measured), or ``None``
    when there are no origin cells to pair against (a non-L4 round, or round 0 itself —
    the origin is the control, not a verdict subject)."""
    if not origin_cells:
        return None
    variant = _pick_variant(candidates)
    if variant is None:
        return None
    var_cells = cell_fitness(all_candidate_results.get(variant.candidate_id, []))

    shared = sorted(c for c in var_cells if c in origin_cells)
    if not shared:
        return None
    per_cell = [
        OuterCellEffect(
            cell=c,
            variant_fitness=var_cells[c],
            origin_fitness=origin_cells[c],
            diff=var_cells[c] - origin_cells[c],
        )
        for c in shared
    ]
    effect, se, n = paired_diff_posterior(
        [var_cells[c] for c in shared], [origin_cells[c] for c in shared]
    )
    # Student-t, not z: the SE is estimated from the same ~7 paired cells it widens. At a
    # single shared cell there is no df — treat as df=1, which is already indecisive.
    crit = t_critical(max(n - 1, 1))
    ci_lo, ci_hi = effect - crit * se, effect + crit * se
    if ci_lo > 0:
        decision = DECISION_ADOPT
    elif ci_hi < 0:
        decision = DECISION_REJECT
    else:
        decision = DECISION_INCONCLUSIVE
    return OuterVerdict(
        variant_id=variant.candidate_id,
        variant_label=variant.label,
        per_cell=per_cell,
        effect=effect,
        se=se,
        ci_lo=ci_lo,
        ci_hi=ci_hi,
        n_cells=n,
        decision=decision,
        mde_remaining=min_detectable_effect(n),
    )


__all__ = [
    "OUTER_PROXY_KEYS",
    "CandidateInfo",
    "OuterCellEffect",
    "OuterSampleProxies",
    "OuterVerdict",
    "cell_fitness",
    "compute_outer_verdict",
]
