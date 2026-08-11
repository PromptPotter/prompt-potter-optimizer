from __future__ import annotations

import logging
from typing import Any

from pydantic import ConfigDict, Field

from promptpotter.domain.phases import StopOutcome, stop_reason_outcome
from promptpotter.domain.results import L1_PARSE_FAILURE_TOOLING, CycleResult, RoundResult
from promptpotter.domain.strict_model import StrictModel

logger = logging.getLogger(__name__)


class InnerCycleUnscoreableError(RuntimeError):
    """Also raised by the panel resolver on a missing declaration, not only by the law here.
    The caller drops the cell loudly — an excluded cycle is never scored on zeros."""


class OuterSampleProxies(StrictModel):
    """One field, and it may NOT be defaulted — the absence of a measurement is not a value, so a cycle that cannot fill it
    is floored or excluded, never scored on zeros."""

    model_config = ConfigDict(frozen=True)

    # A PLAUSIBILITY bound, not a structural one: a difference of two abilities in LOGITS is
    # unbounded, and ±1 is a value a strongly-regressing inner cycle really can exceed — where
    # the raise would kill the outer sample rather than record the regression. ±4 spans well
    # past the ruler's own reach, so it never binds on live data while still stopping a runaway
    # fit at the scoring clamp.
    mean_round_delta: float = Field(ge=-4.0, le=4.0)


# The observation keys one outer sample emits — DERIVED from the model, never hand-listed.
OUTER_PROXY_KEYS: tuple[str, ...] = tuple(OuterSampleProxies.model_fields)

# ONE spelling of the `pipeline_data` key, shared by the emit site (`runner/inner/spawn.py`),
# the infra-key allow-list keeping it off the scoring formula, and `panel_precision` below.
# Deliberately NOT derived as f"{OUTER_PROXY_KEYS[0]}_se": it is the SE of the arm's own
# adopted level, not of the delta, and a derived name would assert an identity that is false.
ADOPTED_LEVEL_SE_KEY = "mean_adopted_level_se"


def held_levels(result: CycleResult) -> list[float]:
    """ONE denominator for every cell on a panel: dividing by the series length instead makes the
    denominator a per-cell quantity, and the panel then compares two estimands rather than one."""
    levels = result.round_adopted_levels
    if not levels:
        return []
    n = max(result.round_budget, len(levels))
    return levels + [levels[-1]] * (n - len(levels))


def mean_adopted_level_se(result: CycleResult) -> float | None:
    """This arm's OWN half of a paired cell difference: two of these in quadrature give the
    difference's error. A PRECISION, never a penalty — no ``mean - λ·se``, never a rank key."""
    ses = result.round_adopted_level_ses
    if not ses:
        return None
    # `origin_level` is deliberately absent: every arm on a cell replays the same round-0 rows,
    # so it is ONE shared measurement that cancels in `variant - origin`, and folding it into
    # each side counts it twice in a quantity it vanishes from.
    #
    # Over the DISTINCT levels — `held_levels`' padding carries no measurement, so a 2-of-4-round
    # cell contributes the precision of 2 rounds. Mean of the SEs, not `σ/√n`: the levels NEST,
    # so dividing by n would manufacture power.
    return float(sum(ses) / len(ses))


def _is_evidential(rnd: RoundResult) -> bool:
    """``L1_PARSE_FAILURE_TOOLING`` means the round lost its candidates to an empty optimizer
    response — missing data, not a bad mutation. Scoring it dirty grades provider flakiness."""
    return rnd.l1_parse_failure != L1_PARSE_FAILURE_TOOLING


def no_evidence_reason(result: CycleResult) -> str | None:
    """Only a SUCCESS ``StopOutcome`` is a measurement — anything else was cut short from outside
    the search, and an unexercised optimizer prompt reads as flawless to every aggregate."""
    outcome = stop_reason_outcome(result.stop_reason)
    if outcome is not StopOutcome.SUCCESS:
        return (
            f"it did not end on its own terms — {outcome} (stop_reason={result.stop_reason}); "
            "its trajectory was cut short by something the optimizer prompt does not own"
        )
    if not result.rounds:
        return f"it ran no L1 rounds (stop_reason={result.stop_reason})"
    if result.origin_level is None:
        return "its origin was never scored, so there is no floor to difference its rounds against"
    if not result.round_adopted_levels:
        return "it adopted no levels to difference against its origin"
    return None


def floor_reason(result: CycleResult) -> str | None:
    """The one optimizer prompt-OWNED no-evidence shape, so it is FLOORED rather than excluded:
    every round losing its candidates to empty content is reproducible, hence evidence."""
    if stop_reason_outcome(result.stop_reason) is not StopOutcome.SUCCESS:
        return None
    if result.rounds and not any(_is_evidential(r) for r in result.rounds):
        return (
            f"every one of its {len(result.rounds)} L1 round(s) lost its candidates to an "
            "empty optimizer response"
        )
    return None


def _floor_proxies() -> OuterSampleProxies:
    """``-1`` sits at the bottom of the scoring formula's re-anchoring window, so the composed
    fitness is exactly 0.0 — and this ASSIGNED value is the only route to a zeroed cell."""
    return OuterSampleProxies(mean_round_delta=-1.0)


def compute_outer_proxies(result: CycleResult) -> OuterSampleProxies:
    """Raises :class:`InnerCycleUnscoreableError` on a no-fault evidence kill; an optimizer
    prompt-OWNED one returns the floor. Origin and rounds share one fit, so the ruler cancels."""
    if (floor := floor_reason(result)) is not None:
        logger.warning("inner cycle scored at the floor: %s", floor)
        return _floor_proxies()
    if (reason := no_evidence_reason(result)) is not None:
        # Loud, never silent: this drops a panel cell, and a dropped cell that reads as "covered"
        # is worse than no cell at all.
        logger.warning("inner cycle EXCLUDED (no evidence about the optimizer prompt): %s", reason)
        raise InnerCycleUnscoreableError(reason)

    assert result.origin_level is not None  # guaranteed by no_evidence_reason
    # Every level is an ability in LOGITS on the fixed ruler, so a delta is a difference of two
    # unbounded quantities. The only divisor is the round budget the mean is taken over;
    # nothing normalizes for difficulty.
    levels = held_levels(result)
    return OuterSampleProxies(
        mean_round_delta=sum(levels) / len(levels) - result.origin_level,
    )


class PanelPrecision(StrictModel):
    """How sharply each cell was measured, against how far apart the cells landed — the two bars
    pick opposite next levers, and one interval alone cannot tell them apart."""

    # THE SEED-PANEL LAYER, and the one thing L4 legitimately measures that the shared engine
    # cannot: an ordinary sample is graded so it carries no error bar, while an outer cell is a
    # whole inner campaign whose level was ESTIMATED. Everything else the outer level reports is
    # the engine's own (`ScoredCandidate.matched_origin_lift*`). In the measurand's own units —
    # θ logits — never fitness: a cell's precision cannot cross the user-editable scoring
    # formula, and two scales inside one object publish an effect and a variance that cannot be
    # compared to each other.
    model_config = ConfigDict(frozen=True)

    # sqrt(mean over cells of (se_variant² + se_origin²)) — the spread the panel would show if
    # every cell measured the SAME true value and differed only by measurement error. The two
    # arms are separate inner campaigns on one cell, so their errors add in quadrature: this is
    # the DIFFERENCE's error, not one arm's, and it is correct only because each input is that
    # arm's own half with the shared origin level excluded upstream.
    estimation_sd: float
    # Sample SD (n−1) of the per-cell paired diffs. Contains `estimation_sd` plus any real spread.
    observed_sd: float
    n_cells: int
    # No RATIO of the two is served: a clamped share rounds an impossible value — noise claiming
    # to exceed the total spread it is a component of — into a tidy "100% measurement noise",
    # eating the finding. Two SDs are directly legible.


def cell_values(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
    """``{cell: mean of `key` over that cell's rows}``, keyed by QUERY — the cell identity that
    survives across campaigns, where a per-campaign ``sample_id`` names a different cell."""
    # `key` is one of this module's two constants, NAMED at the call site — never derived one
    # from the other (`f"{key}_se"`), which would assert the SE belongs to the measurand.
    acc: dict[str, list[float]] = {}
    for r in rows:
        cell, pd = r.get("query"), r.get("pipeline_data")
        if isinstance(cell, str) and isinstance(pd, dict) and isinstance(pd.get(key), int | float):
            acc.setdefault(cell, []).append(float(pd[key]))
    return {cell: sum(v) / len(v) for cell, v in acc.items()}


def panel_precision(
    variant_rows: list[dict[str, Any]], origin_rows: list[dict[str, Any]]
) -> PanelPrecision | None:
    """``None`` below two cells both arms measured AND both priced — one cell has no spread to
    decompose, and a fabricated 0.0 would read as a perfect instrument."""
    # Level and SE are two reads, not one tuple. A FLOORED cell carries a real `mean_round_delta`
    # of -1.0 and no adopted levels to have an SE over, so bundling them makes the SE optional —
    # and the corpus ranking, which wants only the level, then filters on a field it never asked
    # about, dropping exactly the cells that record the worst optimizer prompts.
    level, se = OUTER_PROXY_KEYS[0], ADOPTED_LEVEL_SE_KEY
    v_level, o_level = cell_values(variant_rows, level), cell_values(origin_rows, level)
    v_se, o_se = cell_values(variant_rows, se), cell_values(origin_rows, se)
    cells = sorted(v_level.keys() & o_level.keys() & v_se.keys() & o_se.keys())
    if len(cells) < 2:
        return None
    diffs = [v_level[c] - o_level[c] for c in cells]
    mean = sum(diffs) / len(diffs)
    observed = (sum((d - mean) ** 2 for d in diffs) / (len(diffs) - 1)) ** 0.5
    estimation = (sum(v_se[c] ** 2 + o_se[c] ** 2 for c in cells) / len(cells)) ** 0.5
    return PanelPrecision(
        estimation_sd=float(estimation), observed_sd=float(observed), n_cells=len(cells)
    )


__all__ = [
    "ADOPTED_LEVEL_SE_KEY",
    "OUTER_PROXY_KEYS",
    "InnerCycleUnscoreableError",
    "OuterSampleProxies",
    "PanelPrecision",
    "cell_values",
    "compute_outer_proxies",
    "floor_reason",
    "held_levels",
    "mean_adopted_level_se",
    "no_evidence_reason",
    "panel_precision",
]
