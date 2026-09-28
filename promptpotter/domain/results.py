from __future__ import annotations

import math
from collections.abc import Callable, Collection, Mapping, Sequence
from enum import StrEnum
from typing import Any, Literal, NamedTuple, NotRequired, TypedDict, overload

from pydantic import ConfigDict, Field, computed_field

from promptpotter.domain.bench import BenchScore
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.optimizer_state import OptimizerState
from promptpotter.domain.phases import StopReason
from promptpotter.domain.pipeline_schema import stable_hash
from promptpotter.domain.round_diagnostics import RoundDiagnostics
from promptpotter.domain.ruler import AbilityReading, ThetaCaveat
from promptpotter.domain.run_records import ErrorRecord
from promptpotter.domain.scoring import is_answer_collapsed, is_hit
from promptpotter.domain.search_point import strip_rendered_prompt
from promptpotter.domain.spend import SpendRollup
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.wounds import (
    INVARIANT_REASONS,
    RuntimeFailure,
    ValidationFailure,
)
from promptpotter.shared.errors import is_error_result
from promptpotter.shared.hashing import shapes_optimizer_prompt

__all__ = [
    "CEILING_FRACTION",
    "ArmOutcome",
    "CandidateProposal",
    "CellDelta",
    "CycleResult",
    "DegradationContext",
    "DegradationHealth",
    "DiagnosticRunRecord",
    "HardSampleOrder",
    "HeadlineMetric",
    "LineStep",
    "OverlapMember",
    "OverlapReading",
    "ReferenceReading",
    "RoundClocks",
    "RoundResult",
    "ScoreboardRankKey",
    "ScoreboardRow",
    "ScoredCandidate",
    "WarningDict",
    "best_line",
    "best_round_on_shared_cells",
    "candidate_label",
    "diagnostic_held",
    "invariant_collapses",
    "is_electable",
    "is_floor_pinned",
    "is_leader_eligible",
    "measured_cells",
    "merge_known_outcomes",
    "origin_panel",
    "overlap_row",
    "overlap_series",
    "parent_key",
    "parse_candidate_label",
    "resolved_fitness",
    "round_clocks",
    "scoreboard_rank_key",
    "unscoreable_cells",
]


class ArmOutcome(StrEnum):
    """How an arm's measurement ended, and whose decision ended it: the bench's checks say
    ``BROKEN``, the optimizer's eliminator ``ELIMINATED`` or ``LOCKED_IN``, the operator ``SKIPPED``."""

    MEASURED = "measured"  # walked its whole panel
    INVALID = "invalid"  # rejected before it cost a cell; the scores beside it are synthetic
    SKIPPED = "skipped"  # the operator cut it short
    # Errors kept repeating on THIS arm while the others measured: its fault, counted against it.
    BROKEN = "broken"
    ELIMINATED = "eliminated"  # the eliminator stopped buying it
    LOCKED_IN = "locked_in"  # the eliminator stopped it far enough ahead to call

    @property
    def cut_short(self) -> bool:
        """Stopped before its panel against the arm — by the operator, the bench or a cut."""
        return self in (ArmOutcome.SKIPPED, ArmOutcome.BROKEN, ArmOutcome.ELIMINATED)


class DegradationContext(TypedDict, total=False):
    """The bench's reading of a ``BROKEN`` arm, empty on every other — beside the eliminator's own
    ``elimination_context``, which a broken arm leaves empty."""

    degraded_rate: float
    degraded_count: int
    total_scored: int
    dominant_warning: str
    fatal: bool
    warning_types: dict[str, int]
    source: str


def candidate_label(round_num: int, idx: int) -> str:
    """Sole writer of the label, and readers take it off the row. The browser re-derives the format
    only for the in-flight slot that carries none yet (``webapp/lib/candidate-label.ts``), where it
    diverges deliberately at round 0 — ``C0`` here, ``C0.{n}!`` there — so that string is never a
    join key."""
    if round_num == 0:
        return "C0"
    return f"C{round_num}.{idx + 1}"


def parse_candidate_label(label: str) -> tuple[int, int]:
    """``candidate_label``'s inverse: ``C0`` -> ``(0, 0)``, ``C{round}.{n}`` -> ``(round, n - 1)``
    (labels are 1-indexed, the on-disk candidate list is 0-indexed). Raises ``ValueError``: the
    shell with a user in front of it decides what a bad label costs, and this layer has none."""
    if label == "C0":
        return 0, 0
    round_part, _, idx_part = label[1:].partition(".")
    if not label.startswith("C") or not idx_part:
        raise ValueError(f"bad candidate label {label!r}; expected C0 or C{{round}}.{{n}}.")
    try:
        round_num, idx_one_based = int(round_part), int(idx_part)
    except ValueError:
        raise ValueError(
            f"bad candidate label {label!r}; expected C0 or C{{round}}.{{n}}."
        ) from None
    if idx_one_based < 1:
        raise ValueError(f"candidate index in {label!r} must be >= 1.")
    return round_num, idx_one_based - 1


def overlap_row(overlap: OverlapReading | None) -> dict[str, float | None]:
    """The pair ``best_round_on_shared_cells`` elects on, flattened out of a round's reading.
    Members are ordered by round, so the last is this round's own subject and the first is C0.
    Every carrier of a round row projects through here, or the cycle index and the resume rebuild
    read different shapes of one fact."""
    members = overlap.members if overlap else []
    if not members:
        return {"overlap_accuracy": None, "overlap_origin_accuracy": None}
    return {
        "overlap_accuracy": members[-1].accuracy,
        "overlap_origin_accuracy": members[0].accuracy,
    }


def best_round_on_shared_cells(
    rounds: list[dict[str, Any]],
) -> tuple[float, int | None]:
    """Sole definition of the headline-best derivation, so the cycle index and the resume rebuild
    agree by construction. NOT the winner export, which argmaxes ``composite_fitness`` — §0.5.

    Elected on ``overlap_accuracy``, the one per-round number that is neither confounded nor
    biased: ``accuracy`` rides whatever subset the acquisition bought, and θ is the elected arm's
    own maximum draw and so carries the winner's curse.

    The ORIGIN competes like any round, and round 0 is FILLED here rather than read — C0 alone has
    nothing to read against, and a headline blind to it crowns rounds the shared cells put BELOW
    the origin. Newest wins, because rows compared against each other must be on one set. The fill
    lands on the caller's rows, which is what persists it into the cycle index. A cycle whose line
    shares no measurable cell has run only its origin, and answers with it."""
    # A row without the flattened pair skipped `overlap_row` — the one failure this derivation
    # cannot survive quietly, since the fall-through below then crowns C0 on every resume.
    unprojected = [r.get("round") for r in rounds if "overlap_accuracy" not in r]
    if unprojected:
        raise ValueError(
            f"rounds {unprojected} carry no `overlap_accuracy`: this row shape reached the "
            "election without `results.py::overlap_row`, which would collapse the whole "
            "trajectory onto round 0. Project every carrier's rows through it."
        )
    # By ROUND, not by list order: the append path rewrites one row in place, so position does not
    # order this list. `is not None` because a 0.0 origin is a measurement, not an absence.
    read = [r for r in rounds if r.get("overlap_origin_accuracy") is not None]
    if read:
        newest = max(read, key=lambda r: int(r.get("round") or 0))
        for r in rounds:
            if r.get("round") == 0:
                r["overlap_accuracy"] = newest["overlap_origin_accuracy"]
    # `or 0.0` would rank a round that never recorded a score alongside one that genuinely scored
    # 0% — and could crown it. A round with no number doesn't back the headline.
    shared = [r for r in rounds if r.get("overlap_accuracy") is not None]
    if shared:
        best = max(shared, key=lambda r: float(r["overlap_accuracy"]))
        return (float(best["overlap_accuracy"]), best.get("round"))
    origin = next((r for r in rounds if r.get("accuracy") is not None), None)
    return (float(origin["accuracy"]), origin.get("round")) if origin else (0.0, None)


class ScoredCandidate(StrictModel):
    """One candidate's L1 score report — the single shape for round-file scores.
    ``model_dump()`` IS the wire format; ``accuracy`` IS mean fitness, so there is no ``hits``."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    candidate_id: str
    label: str
    changes_description: str = ""
    # ``None`` is UNSCOREABLE and is not ``0.0``: a candidate that ran and produced nothing usable
    # scored zero, one whose every row errored was never read. ``composite_fitness`` beside it
    # keeps its floor, so this moves no election — only what a surface may report.
    accuracy: float | None
    composite_fitness: float
    total: int
    evaluators: dict[str, float] = Field(default_factory=dict)
    pipeline_overlay: dict[str, Any] | None = None
    # Origin floor ⊕ this candidate's delta, served so the OBSERVE view reads the effective
    # config verbatim and never re-merges client-side. Distinct from the sparse
    # ``pipeline_overlay`` above (the fork transport) — two data classes, not a stitch.
    resolved_pipeline_params: dict[str, Any] | None = None
    # THE join to the archive, stored there on every row as ``prompt_fields_id``. Stamped, never
    # recomputed downstream: it covers each node's rendered ``prompt`` and the field above has
    # that stripped, so a re-derivation addresses no row and nothing raises. ``""`` where no
    # schema was in scope (the unmeasured origin) or the searchpoint configures no node.
    sp_hash: str = ""
    # The archive RUN this report's rows were filed under — ``sp_hash`` names the configuration,
    # this names the one reading of it on this subset, so ``(run_id, sample_id)`` addresses each
    # of the candidate's cells (``GET /cells/{run_id}/{sample_id}``). ``None`` where nothing was
    # walked — rejected before it ran, or the unmeasured origin.
    run_id: str | None
    # Paired with ``pipeline_overlay``, the full searchpoint an operator selects to seed
    # an operator-steered fork.
    prompt_fields: dict[str, Any] = Field(default_factory=dict)
    outcome: ArmOutcome
    scored_samples: int = 0
    expected_samples: int = 0
    # Of ``scored_samples``, how many were replayed from the MeasurementArchive rather than
    # measured. Non-zero off the origin means the searchpoint already existed — a duplicate.
    cached_samples: int = 0
    # What measuring this searchpoint CONSUMED (``TokenAccount.from_measured_rows``). BACKEND
    # tokens only, and the label rendering them says so: judge spend rides a ``TokenUsageRecord``
    # carrying no candidate, and optimizer spend is per round, not per candidate.
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    validation_failures: list[ValidationFailure] = Field(default_factory=list)
    runtime_failures: list[RuntimeFailure] = Field(default_factory=list)
    # The eliminator's own reading of an arm it stopped, opaque to the bench: its keys are the
    # eliminator's (potter's are `pobb/checks.py::EliminationContext`).
    elimination_context: dict[str, Any] = Field(default_factory=dict)
    degradation_context: DegradationContext = Field(default_factory=DegradationContext)
    # The individual this arm's lift is read against, over the cells it touched — which one is
    # `OptimizationConfig.lift_reference`. ``None`` where the arm was never read against one.
    reference_id: str | None = None
    # That reference as this candidate's comparison floor. ``None`` unless the candidate covered
    # the reference's whole panel. MUST NOT default to 0.0: an unstamped 0.0 is indistinguishable
    # from a reference that scored nothing.
    reference_accuracy: float | None = None
    reference_composite: float | None = None
    # The BLOCKED lift over that floor: mean per-cell ``(candidate − parent)`` over the cells both
    # measured, Student-t bracketed. Sharper than ``mean_fitness_ci_*`` because pairing removes the
    # parent's variation. ``None`` below two shared cells — an interval from one pair is a fiction.
    reference_lift: float | None = None
    reference_lift_ci_lo: float | None = None
    reference_lift_ci_hi: float | None = None
    # Difficulty-adjusted Rasch ability (+ Laplace SE) on the round's joint-fit scale — what the
    # election ranks by. Unlike subset-relative `accuracy` it discounts for *which* samples this
    # candidate saw, so it explains a lower-accuracy winner. `None` outside the election fit.
    theta: float | None = None
    theta_se: float | None = None
    # SERVED, never re-derived: this ARM's own reason θ is not ability, or ``None``. Only ever
    # ``FLOOR_PINNED`` — the other three ``ThetaCaveat`` members are facts about the round's scale
    # and ride ``RoundResult.ability`` instead, once, rather than copied onto every arm. Stamped at
    # the election beside ``theta``, from the same rows the fit read.
    theta_caveat: ThetaCaveat | None = None
    # Normal-CLT CI on the mean per-cell FITNESS (``scoring/selection.py::mean_fitness_ci``) —
    # accuracy's own fold, so it brackets accuracy whatever the active composite formula is, which
    # is why it is not named for the composite. Present for any candidate with ≥1 scored cell,
    # unlike ``theta_se``; the blocked ``reference_lift_ci_*`` above is sharper on these rows.
    mean_fitness_ci_lo: float | None = None
    mean_fitness_ci_hi: float | None = None


def is_leader_eligible(cs: ScoredCandidate) -> bool:
    """A VALIDITY predicate, never a ranking one — an eliminator's stop is a budget decision and
    disqualifies nothing. Whether the round can READ the arm is :func:`is_electable`."""
    return cs.outcome not in (ArmOutcome.BROKEN, ArmOutcome.SKIPPED)


def is_electable(cs: ScoredCandidate, rows: Sequence[Mapping[str, object]]) -> bool:
    """Whether the round can READ this arm — the half of admission that needs no budget: filtering
    on :func:`is_leader_eligible` alone lets a collapsed arm top a round that refused to crown it.

    NOT the whole election rule, which adds a COVERAGE floor invisible from here (this is domain;
    counting scoreable cells is `scoring/selection.py::distinct_valid_cells`). A caller treating it
    as the whole rule over-reports how many arms could win."""
    return is_leader_eligible(cs) and bool(rows) and not is_answer_collapsed(rows)


def round_document_digest(rr: RoundResult) -> str:
    """The WHOLE document, never a chosen subset — anything a round records can reach the next
    round's package. Over-firing costs a regeneration; under-firing goes stale in silence."""
    return stable_hash(rr.model_dump(mode="json"))[:12]


def unscoreable_cells(results: Sequence[Mapping[str, Any]]) -> int:
    """A hole is a cell ATTEMPTED that came back empty, read off the typed ``error_category`` —
    never ``scored_samples - total``, which counts a PoBB stop and a deprecated row as holes."""
    return sum(1 for r in results if is_error_result(r))


@shapes_optimizer_prompt
def merge_known_outcomes(
    prior: list[dict[str, Any]], incoming: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """This pool is NOT a score and must never be scored: its rows are measured by DIFFERENT
    configurations, so an accuracy over it belongs to no individual. It decides what runs next."""
    by_sid: dict[Any, dict[str, Any]] = {
        r.get("sample_id"): r for r in prior if r.get("sample_id") is not None
    }
    for r in incoming:
        sid = r.get("sample_id")
        if sid is not None:
            by_sid[sid] = r
    return list(by_sid.values())


@overload
def resolved_fitness(composite_fitness: float | None, accuracy: float) -> float: ...
@overload
def resolved_fitness(composite_fitness: float | None, accuracy: float | None) -> float | None: ...
def resolved_fitness(composite_fitness: float | None, accuracy: float | None) -> float | None:
    """THE composite-or-accuracy rule, one implementation: an honest ``0.0`` is a real score, so
    only genuine absence degrades to ``accuracy``. Every display and ranking site routes here.

    ``None`` out only when BOTH are absent — an unscoreable candidate has no number rather than a
    low one. Overloaded so a caller that has already established a real accuracy keeps a ``float``
    and needs no cast: the two arms are a fact about the input, not something to re-assert."""
    return composite_fitness if composite_fitness is not None else accuracy


# Declared once: a caller restating the tuple misses the next term added to the key.
ScoreboardRankKey = tuple[bool, bool, float, float, float]


def scoreboard_rank_key(
    composite_fitness: float | None,
    accuracy: float | None,
    theta: float | None = None,
    *,
    is_selected: bool = False,
    is_partial: bool = False,
) -> ScoreboardRankKey:
    """``resolved_fitness``'s argmax form: the order ``RoundResult.scoreboard`` persists in.

    On a warm round rank 1 IS the crown, by construction: the round is won on Rasch θ-lift over
    the parent (``elect_round_winner``), so a table ordered on the composite could seat the winner
    anywhere and offer no column that explained it. Both leading terms DEFAULT OFF, so a cold
    round — no candidate carrying a θ, nothing crowned yet — orders on the composite alone.

    ⚠️ A mask lens must keep passing two arguments (``mask/verdicts.py``). It exists to show a
    DIFFERENT ordering under a masked formula, and pinning the active-formula winner to rank 1
    there would leave it unable to disagree."""
    # An UNSCOREABLE arm is no score, not a low one, so it sorts to the bottom on the device a
    # missing θ uses — it must never outrank a candidate that was actually read.
    shown = resolved_fitness(composite_fitness, accuracy)
    return (
        is_selected,
        # A rate the operator CUT SHORT never outranks one measured on the whole panel: the round
        # order is stratified, so the cells a stopped walk kept are a biased slice rather than a
        # smaller sample of the same thing.
        not is_partial,
        theta if theta is not None else -math.inf,
        shown if shown is not None else -math.inf,
        accuracy if accuracy is not None else -math.inf,
    )


# ``ScoredCandidate``'s display subset, spelled once and deliberately narrower than the
# ``candidate_scores`` dump beside it in the same file: scoreboard = the display table,
# candidate_scores = the complete record.
_SCOREBOARD_INCLUDE: set[str] = {
    "candidate_id",
    "changes_description",
    "accuracy",
    "composite_fitness",
    "total",
    "mean_fitness_ci_lo",
    "mean_fitness_ci_hi",
    # Without it the table cannot tell a candidate REJECTED before it cost a sample from one that
    # got everything wrong, nor a broken arm from one the eliminator stopped.
    "outcome",
    "reference_accuracy",
    "reference_composite",
    # The election's own number and the margin it was decided on. Without these the table can
    # seat a winner it has no column able to explain — the state that sent an operator hunting
    # for a bug in a round that was decided correctly.
    "theta",
    "theta_se",
    "theta_caveat",
    "reference_lift",
    "reference_lift_ci_lo",
    "reference_lift_ci_hi",
}


class ScoreboardRow(StrictModel):
    """One rank-ordered row of ``RoundResult.scoreboard`` — the round file's display table."""

    model_config = ConfigDict(frozen=True)

    rank: int
    candidate_id: str
    changes_description: str
    # ``None`` is UNSCOREABLE and is not ``0.0`` — see ``ScoredCandidate.accuracy``: a candidate
    # whose every row errored was never read. Omitted here, the round document's own
    # ``model_dump()`` raised building this row out of exactly such a candidate.
    accuracy: float | None
    composite_fitness: float
    total: int
    # ``INVALID`` means the ``accuracy`` / ``composite_fitness`` beside it are ``INVALID_SCORES``'
    # synthetic 0.0, which nothing may render as a rate.
    outcome: ArmOutcome
    # ``None`` for a row that did not cover the parent's panel — see ``ScoredCandidate``: the
    # file carries the absence rather than a 0.0 that reads as a verdict the parent never gave.
    reference_accuracy: float | None
    reference_composite: float | None
    mean_fitness_ci_lo: float | None
    mean_fitness_ci_hi: float | None
    # What the round was actually WON on, and by how much over the parent. See ``ScoredCandidate``
    # for what each ``None`` means — θ is absent outside the election fit, the lift below two
    # shared cells. Ranking reads ``theta``, so the row and its rank cannot disagree.
    theta: float | None
    theta_se: float | None
    # Carried so the round FILE and the live dashboard answer the same way: `round-candidates.ts`
    # reads this table when the round has closed, so an absence here would show a floor-pinned arm
    # disclaimed while in flight and clean once persisted.
    theta_caveat: ThetaCaveat | None
    reference_lift: float | None
    reference_lift_ci_lo: float | None
    reference_lift_ci_hi: float | None
    is_selected: bool


class CandidateProposal(StrictModel):
    """The child OSP carries the resulting prompt, so its prompt edit is ``candidate_delta``
    against the parent; the overlay and this candidate's own failures ride here because nothing
    else carries them."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    opt_sp: OptSearchPoint
    pipeline_overlay: dict[str, dict[str, Any]] = Field(default_factory=dict)
    validation_failures: list[ValidationFailure] = Field(default_factory=list)
    runtime_failures: list[RuntimeFailure] = Field(default_factory=list)


class ReferenceReading(StrictModel):
    """A round's reference individual re-read on the round's panel — potter's is its parent: the
    origin at round 0, the prior winner after it. Its measurement is a ``ScoredCandidate`` from
    the scoring gateway, so a re-score cannot drop the evaluators."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    opt_sp: OptSearchPoint
    report: ScoredCandidate
    # Per-sample ``QueryMeasurement`` rows plus open-ended stale-data markers — kept ``dict``
    # so the markers survive serialization, which a closed model would strip.
    results: list[dict[str, Any]] = Field(default_factory=list)


class OverlapMember(StrictModel):
    """One member of the best-so-far line, read on the round's overlap set."""

    model_config = ConfigDict(frozen=True)

    # The round this individual became the bench's best so far; 0 is the origin.
    round: int
    candidate_id: str
    label: str
    # Its rate over the set — every member's on the SAME cells, which is the whole point.
    # ``RoundResult.accuracy`` is read on whatever the acquisition bought that round, so two
    # rounds' headline rates sat different exams and differencing them measures the exam.
    accuracy: float
    # Scoreable cells of the set this member holds. Equal to ``len(sample_ids)`` unless one came
    # back unscoreable for this individual — a fact about it, not about the set.
    total: int


class CellDelta(NamedTuple):
    """ONE edit against its parent, cell by cell, on the cells both were scored on this round.
    ``kept`` are the parent's hits the edit hit too; a cell both missed is none of the three."""

    gained: tuple[int, ...]
    lost: tuple[int, ...]
    kept: tuple[int, ...]


class OverlapReading(StrictModel):
    """The cells EVERY member of the best-so-far line has answered, and each one's rate over them.

    The comparison no other surface can make. Not a second fitness: a round's own accuracy is
    read on the subset that round bought, and the acquisition maximises information about one
    ability rather than spread, so consecutive rounds can share almost no cells at all. This is
    one exam, sat by C0 and by each new best since — the bench's ranking, whatever the optimizer.

    REPORT-ONLY, and that is what makes measuring OUTSIDE the election unbiased. These rows reach
    no election, no parent floor, no lift, no ruler and no acquisition — fed to any of them the
    parent would be better-identified than the arms it was judged against. The rows live on
    ``RoundResult.overlap_results``, outside ``results`` and
    ``all_candidate_results``, because those two are exactly where every one of those paths reads.
    """

    model_config = ConfigDict(frozen=True)

    # Ascending, and FIXED for the life of the cycle — :func:`origin_panel`, so every round asks
    # "is this winner better than C0?" on the same exam. Every member below has answered all of it.
    sample_ids: list[int] = Field(default_factory=list)
    # The order each became best — C0 first.
    members: list[OverlapMember] = Field(default_factory=list)
    # What this round PAID to put the line back on the whole panel — usually the new best alone,
    # and more only where an earlier one predates the panel it is now read on. Zero on a round
    # whose line already sat it. Sole count of those rows — nothing re-derives it from the rows.
    measured: int = 0


class LineStep(NamedTuple):
    """One member of the best-so-far line, every cell the cycle has measured it on, and what it
    takes to measure another.

    ``key`` is :func:`parent_key` — the identity a caller must match a round against, since
    ``candidate_id`` is the id this configuration FIRST arrived as and a later round can carry
    the same configuration under a new one.

    ``opt_sp`` + ``pipeline_params`` are the pair ``to_job_search_point`` needs: the overlap pass
    re-measures ANY member, and one measured under another arm's prompt is that arm's reading
    wearing this one's label.
    """

    key: str
    round: int
    candidate_id: str
    label: str
    rows: list[dict[str, Any]]
    opt_sp: OptSearchPoint | None
    pipeline_params: dict[str, Any]


def overlap_series(overlap: OverlapReading | None) -> str:
    """The best-so-far line on one line — every member's rate over the SAME cells, plus what the
    round paid to keep the set whole. Empty when there is no reading, so a caller appends
    nothing rather than printing a header over an absence."""
    if overlap is None or not overlap.members:
        return ""
    arms = "  →  ".join(f"{m.label} {m.accuracy:.1%}" for m in overlap.members)
    paid = f", +{overlap.measured} measured" if overlap.measured else ""
    return f"{len(overlap.sample_ids)} shared cells{paid}: {arms}"


def measured_cells(rows: Sequence[Mapping[str, Any]]) -> set[int]:
    """Which samples a row set carries a SCOREABLE verdict for. An errored cell is not coverage —
    counting it would put a member on the overlap set holding a hole."""
    return {
        int(sid) for r in rows if (sid := r.get("sample_id")) is not None and not is_error_result(r)
    }


def is_floor_pinned(rows: Sequence[Mapping[str, Any]]) -> bool:
    """Whether every cell this arm ANSWERED graded 0.0 — :class:`ThetaCaveat.FLOOR_PINNED`.

    A response vector with no variance carries no information about ability, so the fit falls back
    on the prior and θ settles wherever the δ vector and n put it. That value is a constant of the
    CELLS, not a reading of the arm, and the damage lands on the lift: every difference taken
    against a floor constant reads `0.000` however the arm actually behaved.

    Distinct from ``scoring.py::is_answer_collapsed``, which is about the arm saying ONE THING and
    is a PoBB cut. An arm can be floor-pinned while answering differently every time — that is
    simply an arm getting everything wrong, which is measurable, electable, and still not a θ.
    Errored cells are excluded: they are absence, and ``graded_response`` raises on an unstamped
    row rather than reading it as a zero, so a 0.0 reaching here was really scored 0.0.

    **It reads ``objective``, so it inherits one property of the per-cell formula: that the
    composite is zero exactly where ``fitness`` is.** Every shipped ``per_cell`` SCALES
    (``fitness * anchor / (anchor + penalty)``), so the product is zero iff the fitness is and this
    reads the arm. A formula that instead SUBTRACTS a cost would clamp an expensive-but-correct
    cell to 0.0 (``formula/compiler.py::clamp_unit_score``, which gates per-cell as well as
    per-sample), and this would report an arm that answered everything right as having got
    everything wrong; one that ADDS an unconditional bonus term breaks it the other way, staying
    positive on a cell the arm failed and suppressing a caveat that should fire. Keep the composite
    multiplicative in ``fitness``, or give this its own ``fitness``-keyed read.
    """
    graded = [r for r in rows if not is_error_result(r) and "objective" in r]
    return bool(graded) and all(float(r["objective"] or 0.0) <= 0.0 for r in graded)


def parent_key(rr: RoundResult) -> str:
    """What makes two rounds' parents the SAME measurable individual: the TARGET PROMPT they are
    scored under, plus the node params that are not that prompt.

    **The six fields AND the shots.** Two winners can carry byte-identical fields and still send
    different prompts through ``shot_ids``, which name rows of the campaign's one demo pool. The
    campaign's framing is left out: it is one value for the whole cycle, so it separates nothing.

    **NOT ``lineage.id``.** An L2/L3 transition mints a fresh ``OptSearchPoint`` from the same six
    prompt strings — the optimizer state it moves never reaches ``render()`` — so the parent's id
    changes while the measured thing does not. Empty only on a round that never closed, which
    ``best_line`` has already skipped for want of an individual it ended on.
    """
    # The node's own `prompt` is dropped because it is that render one step stale: on a WINNING
    # round the round file records the render the round STARTED with, not the elected winner's.
    # Every other param — temperature, effort — is a real axis and stays in the key.
    return stable_hash(
        [
            rr.opt_sp.render() if rr.opt_sp else "",
            rr.opt_sp.shot_ids if rr.opt_sp else [],
            strip_rendered_prompt(rr.pipeline_params),
        ]
    )


def best_line(rounds: Sequence[RoundResult]) -> list[LineStep]:
    """The campaign's best-so-far line — C0, then each individual that became the best the bench
    has measured, in the order it did — each member carrying the union of every cell the cycle
    measured it on. One line per campaign, the same for every optimizer.

    "Best" is the bench's own ranking, the one ``Cycle.absorb_round`` keeps: the high-water of each
    round's headline composite, strictly exceeded. ``results`` belongs to the individual the round
    ended on, so a round that re-reads a member WIDENS its coverage instead of losing it. The overlap
    rows an earlier round paid for join it too — that individual's own measurement, quarantined
    from the decisions and from nothing else.
    """
    rows: dict[str, list[dict[str, Any]]] = {}
    # key → the candidate this configuration first arrived as.
    first: dict[str, str] = {}
    labels: dict[str, str] = {}
    config: dict[str, tuple[OptSearchPoint | None, dict[str, Any]]] = {}
    # key → the round it became best, in that order.
    became: dict[str, int] = {}
    best: float | None = None
    for rr in rounds:
        for cs in rr.candidate_scores:
            labels.setdefault(cs.candidate_id, cs.label)
        if rr.opt_sp is None:
            continue
        key = parent_key(rr)
        first.setdefault(key, rr.opt_sp.lineage.id)
        config.setdefault(key, (rr.opt_sp, dict(rr.pipeline_params or {})))
        rows[key] = merge_known_outcomes(rows.get(key, []), list(rr.results))
        if best is None or rr.composite_fitness > best:
            best = rr.composite_fitness
            became.setdefault(key, rr.round)
    # Attributed to the individual they MEASURED, never to the round that bought them: one round
    # tops up several members, so folding them into the round's own key publishes one arm's cells
    # under another's label — a rate over two arms' answers.
    by_candidate = {cid: key for key, cid in first.items()}
    for rr in rounds:
        for cid, bought in (rr.overlap_results or {}).items():
            if (owner := by_candidate.get(cid)) is not None:
                rows[owner] = merge_known_outcomes(rows[owner], list(bought))
    # `R{n}` only if a configuration was never a scored candidate at all — with the key above that
    # is a genuine anomaly rather than the routine L2 case, and a truncated id in its place would
    # be a hash the operator cannot join to anything on screen.
    return [
        LineStep(
            key=key,
            round=rnd,
            candidate_id=first[key],
            label=labels.get(first[key]) or f"R{rnd}",
            rows=rows[key],
            opt_sp=config[key][0],
            pipeline_params=config[key][1],
        )
        for key, rnd in became.items()
    ]


def origin_panel(
    origin_cells: Collection[int], *, poolable: Collection[int], size: int
) -> list[int]:
    """The cells every green bar is read on: C0's own, FIXED for the life of the cycle.

    The acquisition keeps its complete freedom to move the subset it decides rounds on — this is
    the other half of that bargain, the standing exam every winner also sits. Fixing it at the
    origin is what makes the bars comparable at all:

    - **It cannot shrink.** An intersection over what the members happen to share contracts as the
      line grows, which re-asks "is this winner better than C0?" on a different exam every round
      and leaves a winner sharing too little with no bar at all.
    - **Every winner can always reach it.** A member joining late is topped up onto the same
      cells rather than narrowing the set for everyone who came before it.
    - **C0 never pays.** The panel is drawn from cells the origin already answered, so the only
      arm that could be asked to re-measure is one that has not sat the exam yet.

    Pure and total off the origin's own rows, so a resume, a fork and a re-read all re-derive the
    identical panel with nothing stamped on disk to drift. *poolable* excludes what this cycle can
    no longer buy, or a member could be short a cell with no way to be topped up.
    """
    return sorted(set(origin_cells) & set(poolable))[:size]


# The share of a dataset's declared accuracy ceiling that counts as having reached it.
CEILING_FRACTION = 0.95


class RoundClocks(NamedTuple):
    """When the campaign reached each of three marks, in ROUNDS, plus the ceiling the third was
    read against. The wall-clock beside them is ``WallClock.round_ended_s`` keyed by the same
    round number — banked in the same ``index.json::final`` block, so the seconds are a join a
    reader makes and never a second copy this record carries.

    ``rounds_to_improved`` says when the loop ADOPTED an arm, on ``lift > 0.0`` with no interval
    and no multiplicity correction, so ``rounds_to_separable`` beside it is the one a result
    quotes."""

    rounds_to_separable: int | None
    rounds_to_improved: int | None
    rounds_to_ceiling: int | None
    accuracy_ceiling: float | None


def round_clocks(rounds: Sequence[RoundResult], *, accuracy_ceiling: float | None) -> RoundClocks:
    """Every round clock a cycle reports, from this one function: finalize banks it, and
    ``review.md``, which renders at every round close before any banked block exists, calls it
    against the cycle's own ceiling.

    An undeclared ceiling leaves ``rounds_to_ceiling`` unset rather than reading
    ``CEILING_FRACTION`` as an absolute bar — that would be a target no dataset owner chose."""

    def first(holds: Callable[[RoundResult], bool]) -> int | None:
        return next((r.round for r in rounds if holds(r)), None)

    to_ceiling: int | None = None
    if accuracy_ceiling is not None:
        target = CEILING_FRACTION * accuracy_ceiling
        to_ceiling = first(lambda r: r.accuracy is not None and r.accuracy >= target)
    return RoundClocks(
        rounds_to_separable=first(lambda r: r.separable is True),
        rounds_to_improved=first(lambda r: r.improved),
        rounds_to_ceiling=to_ceiling,
        accuracy_ceiling=accuracy_ceiling,
    )


@shapes_optimizer_prompt
def invariant_collapses(candidate_scores: Sequence[ScoredCandidate]) -> dict[str, int]:
    """How many proposals each ``INVARIANT_REASONS`` member collapsed — DERIVED from the arms: a
    collapsed candidate rides them as ``ArmOutcome.INVALID``, never dropped. One reason per
    candidate, or the parts would sum past the population."""
    counts: dict[str, int] = {}
    for cand in candidate_scores:
        if cand.outcome is not ArmOutcome.INVALID:
            continue
        reason = next(
            (vf.reason for vf in cand.validation_failures if vf.reason in INVARIANT_REASONS),
            None,
        )
        if reason:
            counts[reason] = counts.get(reason, 0) + 1
    return counts


class RoundResult(StrictModel):
    """Per-round outcome — and the round document itself.
    ``model_dump()`` IS ``rounds/round_NNNN.json`` — declare a field here and it reaches disk."""

    # `extra="ignore"`: `round_id`/`scoreboard` are computed fields — `model_dump()` writes
    # them into the round file, `model_validate()` must not reject them coming back.
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="ignore")

    # --- checkpoint-critical scalars (no raw payloads) ---
    round: int
    # This document's ``Cut`` — the offset of its own ``round:complete`` record, so a reader can
    # ask for the state AT this round. ``None`` where no ledger was bound (a diagnostic replay, an
    # in-memory repair): absent, never 0, which is a real offset naming the cycle's first record.
    at_offset: int | None = None
    label: str
    # ``None`` where the round measured nothing readable — see ``ScoredCandidate.accuracy``. Not
    # defaulted: a MISSING key must fail rather than quietly become a rate.
    accuracy: float | None
    composite_fitness: float = 0.0
    total: int
    improved: bool
    # One-sided two-proportion p-value vs the parent; drives the IMPROVED gate. None ⇒ no test ran.
    p_value: float | None = None
    # WHY this round ended the way it did, in the numbers it was decided on — the elected arm's θ,
    # the parent's, the margin and its SE, or on a held round the best arm that still failed to
    # clear. Written on EVERY round, won or held: its predecessor was one constant sentence
    # emitted only on failure, so a round that crowned somebody explained nothing and the
    # operator's "why did THIS one win?" had no surface to answer it. `None` only before the
    # election runs.
    verdict_reason: str | None = None
    degraded_samples: int = 0
    # Cells of the winner's panel never sent, copied from its ``ScoredCandidate``. Read by the
    # round's degradation verdict, which without it cannot tell a round that measured badly from
    # one that barely measured at all.
    not_attempted: int = 0
    # Cells of the winner's panel that WERE measured and could not be graded — the formula named a
    # term the row did not carry. Beside ``not_attempted`` because the two are the only ways a round
    # ends with fewer verdicts than cells, and they call for opposite remedies: a cell never sent is
    # re-run, an ungraded one is re-graded off the row already banked. Without it a round that
    # graded six of ten reads exactly like one that graded ten.
    unscored: int = 0
    # Fatal-warning samples discarded from total/accuracy on the winner's run.
    deprecated: int = 0
    # Did ANY electable arm's lift interval clear 0? Not `improved` beside it, which is the point
    # estimate: a round can crown a winner out of arms none of which separated from the parent.
    # Escalation reads BOTH, so a round that resolved nothing stalls instead of resetting patience.
    # `None` when no arm carries an interval — not the same fact as measuring cleanly and tying.
    separable: bool | None = None
    # Subset-invariant peer of this round's `accuracy`: the cumulative frontier's ability with the
    # scale it was read on, so a drifting subset cannot inflate the outer signal. `None` = never
    # fit. The SE inside is a precision, never a penalty — forbidden in the election rank key and
    # as a `mean - λ·se` haircut. No `cumulative_accuracy` beside it: a plain mean over rows of
    # mixed provenance silently attributes one configuration's score to another.
    ability: AbilityReading | None = None
    # --- raw payload ---
    prompt_fields: dict[str, Any]
    pipeline_params: dict[str, Any] | None = None
    # Per-sample rows — ``QueryMeasurement`` + stale-data markers (see ``ReferenceReading.results``).
    results: list[dict[str, Any]] = Field(default_factory=list)
    # Per-candidate scored results — lets resume rescore under a changed scorer + replay decisions.
    all_candidate_results: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    # Each REFERENCE's rows on THIS round's subset, keyed by the individual an arm's
    # `reference_id` names — one bar for every arm, or under `lift_reference: parents` each
    # arm's parent on the cells its children measured. **Subsets move between
    # rounds**, so reconstructing a bar from an earlier round reads it on cells this round never
    # bought — which is how a sample-set mask came to re-score every arm on the selected cells
    # while leaving the bar they must clear at its full-set value (`mask/load.py`). Empty at round
    # 0, whose reference is C0 itself. A repair re-measures the ARMS and not the bar, so on a
    # repaired round this stays the reading the round was actually decided under.
    #
    # On a HELD round `results` already IS the parent's rows, so the panel is banked twice there.
    # Deliberately: a reader wanting the bar must not first have to work out whether the round
    # promoted.
    #
    # Its own field rather than reserved keys in `all_candidate_results`, for the reason
    # `OverlapReading` states about `overlap_results`: that map is where the election, the floor,
    # the lift, the ruler and the acquisition all read, walking `.values()` as arms. A
    # pseudo-candidate there is a silent extra arm in every one of them.
    reference_results: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    candidates_scored: int
    # How many candidates actually entered the election — measured, leader-eligible, not
    # answer-collapsed. `candidates_scored` counts one step earlier, so the gap is exactly the
    # candidates carrying no measurement of ability at all. Zero is a DIFFERENT round from
    # "everyone lost": nothing was compared against the parent, so it says l1_generate
    # produced no testable variant rather than that the search has stalled — which is what the
    # life bank reads it for.
    electable_count: int = 0
    candidate_scores: list[ScoredCandidate] = Field(default_factory=list)
    # The individuals the next round derives from, by LABEL — a resume re-mints candidate ids.
    # Empty when the round HELD; round 0 selects the ``C0`` it adopted. What CHOSE them is the
    # optimizer's own selector; the bench reads nothing into how.
    selected_labels: list[str]
    evaluators: dict[str, float] = Field(default_factory=dict)
    # The 1-to-1 reading of the best-so-far line on one shared set of cells, and the rows this round
    # bought to keep it whole. Two fields for the same reason `accuracy` and `results` are two:
    # one is what a reader is told, the other is what it was read off. The rows are HERE and not
    # in `results` / `all_candidate_results` by design — see `OverlapReading`. `None` before
    # the line has a second member, since C0 alone has nothing to be compared against.
    overlap: OverlapReading | None = None
    # Keyed by the MEASURED individual's candidate id, never flat: one round tops up whichever
    # members are short of the panel, and a flat list lands on the round's own parent — one arm's
    # cells under another's label, at a rate neither of them scored.
    overlap_results: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    # --- computed post-scoring ---
    diagnostics: RoundDiagnostics | None = None
    # The bench's AxisIndex peaked set at close, which no round document could otherwise rebuild.
    axis_memory_peaked: list[str] = Field(default_factory=list)
    # Stamped at round close — the sole compute site; every surface renders this one.
    health: DegradationHealth | None = None
    # --- stamped as the round closes (the document's own fields) ---
    # Resume rebuilds `Cycle.opt_sp` from it and review/sibling-wounds read its lineage, so it
    # is round state, not a rendering detail. None only on a round that never closed.
    opt_sp: OptSearchPoint | None = None
    # The optimizer's own state as the round ended on it — restored on resume and fork, and read
    # by nothing outside that optimizer.
    optimizer_state: OptimizerState
    # "generation_only" for a diag round (L1 variants generated, never scored — every
    # scoring scalar below is a structural zero, not a measurement); "" for a scored round.
    status: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def round_id(self) -> str:
        return f"round_{self.round}"

    @property
    def selected_scores(self) -> list[ScoredCandidate]:
        """The selected arms' rows, in ``selected_labels`` order."""
        by_label = {c.label: c for c in self.candidate_scores}
        return [by_label[label] for label in self.selected_labels if label in by_label]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def scoreboard(self) -> list[ScoreboardRow]:
        """Rank-ordered display table — the selection first, then θ, then composite.

        Derived, never stored: it cannot drift from `candidate_scores` the way a
        hand-built twin could. On a warm round rank 1 IS the selection, by construction; on a cold
        one no row carries a θ and the order falls back to the composite it always had.
        """
        selected = set(self.selected_labels)
        ranked = sorted(
            self.candidate_scores,
            key=lambda c: scoreboard_rank_key(
                c.composite_fitness,
                c.accuracy,
                c.theta,
                is_selected=c.label in selected,
                is_partial=c.outcome is ArmOutcome.SKIPPED,
            ),
            reverse=True,
        )
        return [
            ScoreboardRow(
                rank=i,
                is_selected=c.label in selected,
                **c.model_dump(include=_SCOREBOARD_INCLUDE),
            )
            for i, c in enumerate(ranked, start=1)
        ]

    def cell_delta(self, candidate_id: str) -> CellDelta:
        """What one edit did to its parent's cells, paired: the cells it GAINED and the ones it
        LOST. An accuracy nets the two into one number, so an edit that cracks a cell its parent
        cannot solve and breaks one it could reads as a tie — and on a small near-deterministic
        panel those two cells are the round's whole signal. An errored row on either side pairs
        nothing, since a failed measurement is not an outcome; empty where no reference was banked."""
        arm = next((c for c in self.candidate_scores if c.candidate_id == candidate_id), None)
        reference = self.reference_results.get(arm.reference_id or "", []) if arm else []
        parent_hit = {
            sid: is_hit(r.get("fitness"))
            for r in reference
            if (sid := r.get("sample_id")) is not None and not is_error_result(r)
        }
        gained: list[int] = []
        lost: list[int] = []
        kept: list[int] = []
        for r in self.all_candidate_results.get(candidate_id) or []:
            sid = r.get("sample_id")
            if not isinstance(sid, int) or sid not in parent_hit or is_error_result(r):
                continue
            hit = is_hit(r.get("fitness"))
            if hit and not parent_hit[sid]:
                gained.append(sid)
            elif parent_hit[sid]:
                (kept if hit else lost).append(sid)
        return CellDelta(tuple(gained), tuple(lost), tuple(kept))


class CycleResult(StrictModel):
    rounds: list[RoundResult]
    # Origin-EXCLUSIVE, unlike the persisted `index.json::n_rounds`, which counts round 0.
    n_l1_rounds: int
    result_accuracy: float | None
    result_round: int
    # They travel together because a consumer reading one against a composite computed on some
    # other basis is comparing two different measurements.
    origin_accuracy: float | None
    # `None`, not 0.0, on a cycle that never started — the rule every accuracy and level here
    # follows. A stand-in 0.0 becomes round 0's lift bar in `l1/stats.py::_top_lifts`, which
    # reports the first round's whole composite as its improvement over an origin nothing scored.
    origin_composite_fitness: float | None = None
    # The L4 outer proxy's inner-search signal: the origin's level and the ability each round
    # The PARENT each round ended on — the winner it crowned, or the one carried forward when it
    # crowned nobody — never the proposals, which turn the metric NEGATIVE for exactly the
    # generators that explore. Both live in ONE space, so no proxy delta subtracts across scales,
    # and levels are NOT floored at origin or the outer loses the gradient away from a regressing
    # prompt. `origin_level` is `None`, not `0.0`, when the origin was never scored: a fabricated
    # 0.0 reports the climb as an enormous improvement over nothing.
    origin_level: float | None = None
    round_levels: list[float] = Field(default_factory=list)
    # Index-aligned with the two above: a round that did not move the parent did not sharpen the
    # reading of it either. The WITHIN-cell precision an L4 panel needs to tell estimation noise
    # from between-cell heterogeneity. Precision only — never a penalty term.
    #
    # `origin_level_se` has NO production reader BY DESIGN — do not delete it as dead. It is the
    # term `l4/proxies.py::mean_parent_level_se` must not fold in (the origin cancels in
    # `variant - origin`, and counting it twice once read out as "100% noise"), and supplying it is
    # what makes that negative control discriminating in `test_numerics.py`. Delete the field and
    # the guarantee stops being proven and starts being merely unreachable.
    origin_level_se: float | None = None
    round_level_ses: list[float] = Field(default_factory=list)
    # The denominator the L4 law averages over, and it must come from the config rather than
    # ``len(round_levels)``: a cycle stopped early by ``lives`` holds fewer levels, so
    # a mean over "rounds that happened" compares two estimands — and it points the wrong way,
    # since ``lives`` stops a STALLING cycle and the shorter series pays it for quitting once it
    # had lifted. 0 = never declared, and the law falls back to the series length.
    round_budget: int = 0
    result_prompt_fields: dict[str, Any]
    result_pipeline_params: dict[str, Any] | None = None
    stop_reason: StopReason
    started_at: str
    finished_at: str
    langfuse_trace_id: str | None = None
    cycle_id: str | None = None
    session_id: str | None = None
    resumed_from_round: int = 1
    # This cycle's total spend, captured from the live dashboard state at
    # finalize. ``None`` only on an init-crash before any observer wired up.
    spend: SpendRollup | None = None
    # Set when ``stop_reason`` ∈ ``{CRASHED, RENDER_ERROR, DIVERGED}``. The runner's ``except``
    # sites carry ``emit_error_record``'s return straight here — the same record the ledger
    # holds, no twin model.
    error: ErrorRecord | None = None
    # The headline, on the held-out bench set (`domain/bench.py`). `None` where the campaign holds
    # nothing out, and where the cycle ended before a selection could be graded.
    bench: BenchScore | None = None


class DiagnosticRunRecord(StrictModel):
    """One on-demand workspace-scope diagnostic run — the ``verify`` and ``noise-floor``
    CLI verbs' shared sidecar shape.

    Per-sample data lands in `measurements/`; this record carries the workspace-scope
    verdict. ``verify`` populates the base fields (did the source-campaign composite
    hold on more samples); ``noise-floor`` additionally populates the ``noise_floor_*``
    fields (the run-to-run spread of ``--k`` ``force_fresh`` re-scores of the SAME
    config) and leaves ``samples_added``/``source_campaign_*`` at the origin round's
    recorded values (there is nothing new to "add" — every re-score targets the same
    already-measured set).
    """

    model_config = ConfigDict(frozen=True)

    ts: str
    dataset: str
    source_campaign: str
    source_cycle: str
    source_label: str
    source_candidate_id: str
    config_hash: str
    samples_requested: int
    samples_added: int
    workspace_n: int
    workspace_accuracy: float
    workspace_composite: float
    source_campaign_accuracy: float | None
    source_campaign_composite: float
    source_campaign_n: int
    held: bool | None = Field(
        description="Did the verdict HOLD on the wider set — `workspace_accuracy` at or above "
        "`source_campaign_accuracy`, under this layer's float tolerance. `None` where the source "
        "carries no rate to compare against. Stored rather than left to each reader: the "
        "tolerance is a decision about when two measured rates count as equal, and a surface "
        "picking its own epsilon is a surface that can disagree with this one about whether a "
        "candidate survived."
    )
    # ``noise-floor`` only: the backend's own run-to-run noise, not a comparison to history.
    noise_floor_k: int | None = None
    noise_floor_mean: float | None = None
    noise_floor_ci_lo: float | None = None
    noise_floor_ci_hi: float | None = None
    noise_floor_raw: list[float] | None = None


def diagnostic_held(
    workspace_accuracy: float, source_campaign_accuracy: float | None
) -> bool | None:
    """:attr:`DiagnosticRunRecord.held`, from the two rates it compares — ``None`` where the source
    carries no rate. The tolerance absorbs the float error of two means taken over different row
    counts; a strict ``>=`` calls an unchanged candidate dropped once in a while."""
    if source_campaign_accuracy is None:
        return None
    return workspace_accuracy + 1e-9 >= source_campaign_accuracy


class WarningDict(TypedDict):
    """``kind`` is source-stamped by the BACKEND and PromptPotter keeps no shadow code→kind
    taxonomy: an absent or unrecognized ``kind`` is SKIPPED, never guessed."""

    step: str
    code: str
    message: str
    kind: str
    details: NotRequired[list[Any]]
    stats: NotRequired[dict[str, Any]]


HealthGrade = Literal["healthy", "degraded", "critical"]

# WHY a round graded below healthy — one cause, closed set. Closed so that a reader branching on
# a cause no producer emits is a type error rather than a notice that silently never renders.
HealthCause = Literal[
    "origin_unmeasured",
    # The origin measured SOME of its cells — distinct from `origin_unmeasured` (none) and `holed`
    # (a rate, any round): the baseline every later round reads against is permanently short.
    "origin_incomplete",
    "structural",
    "unscoreable",
    "holed",
    "evidence_starved",
    "structural_untested",
    "persistent",
    "degraded",
]

# Which fitness number headlines the operator's surfaces. ONE owner, so `CampaignConfig` and
# `LiveDashboardState` cannot drift into a wide `str` on one side and a closed union on the other.
HeadlineMetric = Literal["accuracy", "composite", "ability"]

# Which key ranks the hard-sample leaderboard: `info_gain` is the queue's own acquisition score,
# `difficulty` the Rasch ruler δ_s alone. Same one-owner rule. Ranks what the operator READS and
# nothing else — the order the engine scores in is `build_round_order`, which no knob reaches.
HardSampleOrder = Literal["info_gain", "difficulty"]


class DegradationHealth(StrictModel):
    """Context-aware degradation verdict for a round (origin included), computed
    PP-side at round close from the backend's warning stamps. It never stops the run."""

    model_config = ConfigDict(frozen=True)

    grade: HealthGrade
    # ONE cause, not a list: every producer branch assigns exactly one, and `None` is the
    # `healthy` grade. A list invited readers to scan it for a name nobody wrote.
    cause: HealthCause | None = None
    samples: int
    structural_count: int
    transient_count: int
    # Samples whose pipeline SUCCEEDED but emitted no extractable prediction. PP-owned — the
    # backend stamps no warning, since from its side generation succeeded. A high share is a
    # structurally-unscoreable floor, distinct from a wrong-but-extractable miss.
    no_result_count: int = 0
    # HOLES — cells attempted that came back carrying no measurement at all, so every
    # warning-based classifier is blind to them. Separate from ``no_result_count`` because the
    # remedy differs: a NO_RESULT row means the pipeline RAN and emitted nothing parseable (fix
    # the answer format), a hole means the cell never reported (re-run it). Uncounted, such a
    # row joins ``samples`` with no numerator and grades a round HEALTHIER the more it has.
    hole_count: int = 0
    # Cells of the panel that were never SENT, because the walk stopped early. They are NOT
    # measurements and carry no row: ``samples`` counts what was dispatched, and the panel this
    # round meant to measure is ``samples + not_attempted``. The distinction is the whole reason
    # a verdict can say "the origin was not measured" instead of grading a pipeline on cells that
    # never ran — which is what the abort's fabricated error rows made it do.
    not_attempted: int = 0
    # Cells that WERE sent and measured and carry no verdict, because the active formula named a
    # term the row did not carry. In ``samples`` — they were attempted — and absent from every rate
    # above, so without this field a round holding four of them reads exactly like one that graded
    # everything. Threaded from ``RoundResult`` rather than recounted here, the way
    # ``not_attempted`` is: one owner (`runner/round.py`), one number.
    unscored: int = 0
    # Share of this round's predictions on its single commonest label; ``None`` where the answer
    # space makes collapse meaningless. REPORTED, never graded — hedging to one label is the
    # addressable failure the loop exists to correct, so grading it critical would halt the
    # optimization that fixes it. The round-over-round series is the point: falling = working.
    answer_modal_share: float | None = None
    degraded_rate: float
    consecutive_degraded_rounds: int
    prior_clean_rounds: int
    dominant_node: str | None = None
    node_failure_rates: dict[str, float] = Field(default_factory=dict)
    # Verbatim upstream reasons per node, harvested from the connector's StepWarnings — the
    # evidence behind the verdict, connector-agnostic.
    node_warnings: dict[str, list[str]] = Field(default_factory=dict)
    suggested_action: str | None = None
    # The verbatim ``error`` of the LAST errored cell of the round, which on an abort is the cell
    # that triggered it — the walk stops there. Named for the position rather than for the role,
    # because outside an abort there is no trigger and a field called ``first_error`` was answering
    # with a different cell than the one it claimed. Every ``suggested_action`` that names a hole
    # tells the operator to "read the row's error text before changing anything", and until this
    # field there was no surface in the product that showed it: the text is in the round file, and
    # the operator was left to open it by hand or guess. ``None`` when no cell errored.
    last_error: str | None = None
