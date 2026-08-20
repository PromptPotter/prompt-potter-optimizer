from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any, Literal, NamedTuple, NotRequired, TypedDict

from pydantic import ConfigDict, Field, computed_field

from promptpotter.domain.escalation_signals import (
    INVARIANT_REASONS,
    EscalationSignal,
    RuntimeFailure,
    ValidationFailure,
)
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import StopReason
from promptpotter.domain.pipeline_schema import stable_hash
from promptpotter.domain.round_diagnostics import RoundDiagnostics
from promptpotter.domain.ruler import CalibrationModel
from promptpotter.domain.run_records import ErrorRecord
from promptpotter.domain.scoring import is_answer_collapsed
from promptpotter.domain.spend import SpendRollup
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.errors import is_error_result

__all__ = [
    "L1_PARSE_FAILURE_MALFORMED",
    "L1_PARSE_FAILURE_TOOLING",
    "L1_PARSE_FAILURE_WRONG_TYPE",
    "CalibrationModel",
    "CandidateProposal",
    "CritiqueReadout",
    "CycleResult",
    "DegradationContext",
    "DegradationHealth",
    "DiagnosticRunRecord",
    "EliminationContext",
    "HardSampleOrder",
    "HeadlineMetric",
    "OverlapMember",
    "OverlapReading",
    "PayloadOutcome",
    "RoundParent",
    "RoundResult",
    "ScoreboardRow",
    "ScoredCandidate",
    "SweepBatchResult",
    "TrajectoryStep",
    "WarningDict",
    "best_round_by_measured_accuracy",
    "candidate_label",
    "choose_overlap_set",
    "incumbent_key",
    "is_electable",
    "is_leader_eligible",
    "is_round_winner",
    "measured_cells",
    "merge_known_outcomes",
    "overlap_series",
    "unscoreable_cells",
    "winner_trajectory",
]


class EliminationContext(TypedDict, total=False):
    """Written by ``decode_signal_effect`` when the elimination check fires; empty when the
    candidate was not cut. Disjoint from :class:`DegradationContext`."""

    p_best: float
    epsilon: float
    leader_id: str
    queries_scored: int
    total_queries: int
    n_priors: int
    leader_locked: bool
    leader_label: str


class DegradationContext(TypedDict, total=False):
    """Empty when the candidate was not degradation-cut. Disjoint from
    :class:`EliminationContext` — the renderer branches on which of the two is non-empty."""

    degraded_rate: float
    degraded_count: int
    total_scored: int
    dominant_warning: str
    fatal: bool
    warning_types: dict[str, int]
    source: str


class CritiqueReadout(TypedDict, total=False):
    """A domain-local mirror, so a round file round-trips without the optimization layer's
    schema in scope; the optimizer node's full Pydantic shape stays in ``dispatch/schemas.py``."""

    priority_fix: str
    suggested_axes: list[str]
    failure_highlights: list[str]


def candidate_label(round_num: int, idx: int) -> str:
    """Sole writer of the label — every surface reads this rather than re-deriving the format."""
    if round_num == 0:
        return "C0"
    return f"C{round_num}.{idx + 1}"


def best_round_by_measured_accuracy(
    rounds: list[dict[str, Any]],
) -> tuple[float, int | None]:
    """Sole definition of the headline-best derivation, so the cycle index and the resume rebuild
    agree by construction. NOT the winner export, which argmaxes ``composite_fitness`` — §0.5."""
    # `or 0.0` would rank a round that never recorded an accuracy alongside one that
    # genuinely scored 0% — and could crown it. A round with no number doesn't back the headline.
    scored = [r for r in rounds if r.get("accuracy") is not None]
    best = max(scored, key=lambda r: float(r["accuracy"]), default=None)
    if best is None:
        return (0.0, None)
    return (float(best["accuracy"]), best.get("round"))


def is_round_winner(candidate_id: str, winner_id: str) -> bool:
    """Matched by IDENTITY, never prose: ``changes_description`` can be empty, and can repeat
    across candidates. The elected id is already on disk twice — ask it, never re-derive it."""
    return bool(winner_id) and candidate_id == winner_id


class ScoredCandidate(StrictModel):
    """One candidate's L1 score report — the single shape for round-file scores.
    ``model_dump()`` IS the wire format; ``accuracy`` IS mean fitness, so there is no ``hits``."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    candidate_id: str
    label: str
    changes_description: str = ""
    accuracy: float
    composite_fitness: float
    total: int
    evaluators: dict[str, float] = Field(default_factory=dict)
    pipeline_params_override: dict[str, Any] | None = None
    # Origin floor ⊕ this candidate's delta, served so the OBSERVE view reads the effective
    # config verbatim and never re-merges client-side. Distinct from the sparse
    # ``pipeline_params_override`` above (the fork transport) — two data classes, not a stitch.
    resolved_pipeline_params: dict[str, Any] | None = None
    # Paired with ``pipeline_params_override``, the full searchpoint an operator selects to seed
    # an operator-steered fork.
    prompt_fields: dict[str, Any] = Field(default_factory=dict)
    escalation_aborted: bool = False
    elimination_stopped: bool = False
    scored_samples: int = 0
    expected_samples: int = 0
    # Of ``scored_samples``, how many were replayed from the MeasurementArchive rather than
    # measured. Non-zero off the origin means the searchpoint already existed — a duplicate.
    cached_samples: int = 0
    # Why ``scored_samples < expected_samples``: "" (not partial) | "skip" (operator
    # early-abort, which marks the cycle ``human_intervened``).
    partial_reason: str = ""
    invalid: bool = False
    validation_failures: list[ValidationFailure] = Field(default_factory=list)
    runtime_failures: list[RuntimeFailure] = Field(default_factory=list)
    elimination_context: EliminationContext = Field(default_factory=EliminationContext)
    degradation_context: DegradationContext = Field(default_factory=DegradationContext)
    # The PARENT as this candidate's comparison floor — the origin at round 0 and the prior winner
    # after it (``RoundParent``), which is why these are not named for the origin. ``None`` unless
    # the candidate covered the parent's whole panel, and NOT the population ``theta`` is ``None``
    # for (θ is subset-invariant, an accuracy is not). These MUST NOT default to 0.0: an unstamped
    # 0.0 is indistinguishable from a parent that scored nothing, and reads as "this candidate beat
    # its parent by its whole accuracy".
    matched_parent_accuracy: float | None = None
    matched_parent_composite: float | None = None
    # The BLOCKED lift over that floor: mean per-cell ``(candidate − parent)`` across the cells
    # both measured, Student-t bracketed (``scoring/selection.py::matched_parent_lift``). Sharper
    # than ``mean_fitness_ci_*`` on the same rows because pairing removes the parent's cell-to-cell
    # variation instead of carrying it as noise. ``None`` below two shared cells — an interval
    # from one pair is a fiction. It reads at EVERY level: inner cells are samples, outer cells
    # whole inner campaigns, and the arithmetic does not care which.
    matched_parent_lift: float | None = None
    matched_parent_lift_ci_lo: float | None = None
    matched_parent_lift_ci_hi: float | None = None
    # Difficulty-adjusted Rasch ability (+ Laplace SE) on the round's joint-fit scale — what the
    # election ranks by. Unlike subset-relative `accuracy` it discounts for *which* samples this
    # candidate saw, so it explains a lower-accuracy winner. `None` outside the election fit.
    theta: float | None = None
    theta_se: float | None = None
    # Normal-CLT CI on the mean per-cell FITNESS (``scoring/selection.py::mean_fitness_ci``) —
    # accuracy's own fold, so it brackets accuracy whatever the active composite formula is, which
    # is why it is not named for the composite. Present for any candidate with ≥1 scored cell,
    # unlike ``theta_se``; the blocked ``matched_parent_lift_ci_*`` above is sharper on these rows.
    mean_fitness_ci_lo: float | None = None
    mean_fitness_ci_hi: float | None = None


def is_leader_eligible(cs: ScoredCandidate) -> bool:
    """A VALIDITY predicate, never a ranking one — a PoBB stop is a budget decision and
    disqualifies nothing. Whether the round can READ the arm is :func:`is_electable`."""
    if cs.escalation_aborted and not cs.elimination_stopped:
        return False
    return not cs.degradation_context


def is_electable(cs: ScoredCandidate, rows: Sequence[Mapping[str, object]]) -> bool:
    """The election's admission rule, and the outer verdict must use it too: filtering on
    :func:`is_leader_eligible` alone lets a collapsed arm top a round that refused to crown it."""
    return is_leader_eligible(cs) and bool(rows) and not is_answer_collapsed(rows)


def round_document_digest(rr: RoundResult) -> str:
    """The WHOLE document, never a chosen subset — anything a round records can reach the next
    round's package. Over-firing costs a regeneration; under-firing goes stale in silence."""
    return stable_hash(rr.model_dump(mode="json"))[:12]


def unscoreable_cells(results: Sequence[Mapping[str, Any]]) -> int:
    """A hole is a cell ATTEMPTED that came back empty, read off the typed ``error_category`` —
    never ``scored_samples - total``, which counts a PoBB stop and a deprecated row as holes."""
    return sum(1 for r in results if is_error_result(r))


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
    "escalation_aborted",
    "matched_parent_accuracy",
    "matched_parent_composite",
    # The election's own number and the margin it was decided on. Without these the table can
    # seat a winner it has no column able to explain — the state that sent an operator hunting
    # for a bug in a round that was decided correctly.
    "theta",
    "theta_se",
    "matched_parent_lift",
    "matched_parent_lift_ci_lo",
    "matched_parent_lift_ci_hi",
}


class ScoreboardRow(StrictModel):
    """One rank-ordered row of ``RoundResult.scoreboard`` — the round file's display table."""

    model_config = ConfigDict(frozen=True)

    rank: int
    candidate_id: str
    changes_description: str
    accuracy: float
    composite_fitness: float
    total: int
    escalation_aborted: bool
    # ``None`` for a row that did not cover the origin's panel — see ``ScoredCandidate``: the
    # file carries the absence rather than a 0.0 that reads as a verdict the origin never gave.
    matched_parent_accuracy: float | None
    matched_parent_composite: float | None
    mean_fitness_ci_lo: float | None
    mean_fitness_ci_hi: float | None
    # What the round was actually WON on, and by how much over the parent. See ``ScoredCandidate``
    # for what each ``None`` means — θ is absent outside the election fit, the lift below two
    # shared cells. Ranking reads ``theta``, so the row and its rank cannot disagree.
    theta: float | None
    theta_se: float | None
    matched_parent_lift: float | None
    matched_parent_lift_ci_lo: float | None
    matched_parent_lift_ci_hi: float | None
    is_winner: bool


class CandidateProposal(StrictModel):
    """Both deltas ride here, not just the merged result — the OSP carries the RESULTING prompt
    fields, which cannot answer what L1 actually proposed this round."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    opt_sp: OptSearchPoint
    pipeline_params_override: dict[str, dict[str, Any]] = Field(default_factory=dict)
    prompt_fields_override: dict[str, str] = Field(default_factory=dict)


class RoundParent(StrictModel):
    """The origin only at round 0; the prior winner after it. Its measurement is a
    ``ScoredCandidate`` from the scoring gateway, so a re-score cannot drop the evaluators."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    opt_sp: OptSearchPoint
    report: ScoredCandidate
    # Per-sample ``QueryMeasurement`` rows plus open-ended stale-data markers — kept ``dict``
    # so the markers survive serialization, which a closed model would strip.
    results: list[dict[str, Any]] = Field(default_factory=list)


class OverlapMember(StrictModel):
    """One adopted incumbent, read on the round's overlap set."""

    model_config = ConfigDict(frozen=True)

    # The round this individual was crowned; 0 is the origin.
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


class OverlapReading(StrictModel):
    """The cells EVERY adopted incumbent has answered, and each one's rate over them.

    The comparison no other surface can make. Not a second fitness: a round's own accuracy is
    read on the subset that round bought, and the acquisition maximises information about one
    ability rather than spread, so consecutive rounds can share almost no cells at all. This is
    one exam, sat by C0 and by every winner since.

    REPORT-ONLY, and that is what makes measuring the winner ALONE unbiased. These rows reach no
    election, no parent floor, no lift, no ruler and no acquisition — fed to any of them the
    incumbent would be better-identified than the arms it was judged against. The rows live on
    ``RoundResult.overlap_results``, outside ``results`` and
    ``all_candidate_results``, because those two are exactly where every one of those paths reads.
    """

    model_config = ConfigDict(frozen=True)

    # Ascending, and re-chosen each round: it may swap cells as the line grows, preferring ones
    # the new member has already answered so a swap costs nothing. Whatever it holds at a given
    # round, every member listed below has answered all of it.
    sample_ids: list[int] = Field(default_factory=list)
    # Adoption order — C0 first.
    members: list[OverlapMember] = Field(default_factory=list)
    # What this round PAID: the new winner on the cells its line had answered and it had not.
    # Zero on a held round, since the retained incumbent is already on the set. Sole count of
    # those rows — nothing re-derives it from the row list beside it.
    measured: int = 0


class TrajectoryStep(NamedTuple):
    """One adopted incumbent and every cell the cycle has measured it on.

    ``key`` is :func:`incumbent_key` — the identity a caller must match a round against, since
    ``candidate_id`` is the id this configuration FIRST arrived as and a later round can carry
    the same configuration under a new one.
    """

    key: str
    round: int
    candidate_id: str
    label: str
    rows: list[dict[str, Any]]


def overlap_series(overlap: OverlapReading | None) -> str:
    """The adopted line on one line — every member's rate over the SAME cells, plus what the
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


def incumbent_key(rr: RoundResult) -> str:
    """What makes two rounds' incumbents the SAME measurable individual: the configuration they
    are scored under — the prompt fields and the pipeline params, with ``lineage`` dropped.

    **NOT ``lineage.id``.** An L2/L3 transition mints a fresh ``OptSearchPoint`` from the same six
    prompt strings — it writes ``l1_layout`` / ``l1_overrides`` / ``plan``, which steer the
    OPTIMIZER, not the target prompt — so the incumbent's id changes while the thing being
    measured does not. Keyed on the id, that put ONE configuration on the trajectory twice, at
    the same rate by construction, the second time under a label naming no candidate. Keyed here,
    the two fold into one member, which is also what the measurement archive already believes:
    it is content-addressed on the node configs, so the re-measure cache-hit anyway.
    """
    fields = {k: v for k, v in rr.prompt_fields.items() if k != "lineage"}
    # The node's rendered ``prompt`` is dropped, and this is not tidiness. It is the RENDER of the
    # fields above, so it adds nothing — and on a WINNING round the round file records the render
    # the round STARTED with rather than the elected winner's (``l1_score`` takes it from
    # ``parse_population``, which carries param overrides; the target prompt is rendered later, at
    # ``to_job_search_point``). Keyed on it, one configuration splits into two members a round
    # apart. Every other param — temperature, effort — is a real axis and stays in the key.
    params = {
        node: {k: v for k, v in cfg.items() if k != "prompt"} if isinstance(cfg, dict) else cfg
        for node, cfg in (rr.pipeline_params or {}).items()
    }
    return stable_hash([fields, params])


def winner_trajectory(rounds: Sequence[RoundResult]) -> list[TrajectoryStep]:
    """The campaign's adopted line — C0, then every round that adopted a different configuration
    — each member carrying the union of every cell the cycle measured it on, in adoption order.

    ONE rule covers both round shapes: ``results`` belongs to the round's incumbent whether the
    round crowned an arm or retained one, so a HELD round's parent re-score WIDENS that member's
    coverage instead of being lost. The overlap rows an earlier round paid for join it too — they
    are that individual's own measurement, quarantined from the decisions and from nothing else.
    """
    rows: dict[str, list[dict[str, Any]]] = {}
    # key → the round that first adopted this configuration, and the candidate it arrived as.
    first: dict[str, tuple[int, str]] = {}
    labels: dict[str, str] = {}
    for rr in rounds:
        for cs in rr.candidate_scores:
            labels.setdefault(cs.candidate_id, cs.label)
        cid = rr.winner_id
        if not cid:
            continue
        key = incumbent_key(rr)
        first.setdefault(key, (rr.round, cid))
        merged = merge_known_outcomes(rows.get(key, []), list(rr.results))
        rows[key] = merge_known_outcomes(merged, list(rr.overlap_results))
    # `R{n}` only if a configuration was never a scored candidate at all — with the key above that
    # is a genuine anomaly rather than the routine L2 case, and a truncated id in its place would
    # be a hash the operator cannot join to anything on screen.
    return [
        TrajectoryStep(
            key=key, round=rnd, candidate_id=cid, label=labels.get(cid) or f"R{rnd}", rows=rows[key]
        )
        for key, (rnd, cid) in first.items()
    ]


def choose_overlap_set(
    common: Collection[int],
    *,
    already_measured: Collection[int],
    previous: Collection[int],
    size: int,
) -> list[int]:
    """Which cells the next 1-to-1 reading is taken on, in preference order: what the new member
    has already answered (free), then what the line was last read on (so the bars stay still),
    then the cheapest remaining id.

    *common* is the intersection over the members that are ALREADY on the set, so every cell here
    is one they have all answered — which is what bounds the cost to the new member alone and
    keeps C0 from ever paying again. Pure and total, so a resumed cycle re-chooses the same set
    off the round documents.
    """
    free, prior = set(already_measured), set(previous)
    ranked = sorted(sorted(common), key=lambda s: (s not in free, s not in prior))
    return sorted(ranked[:size])


# The reasons `RoundResult.l1_parse_failure` can carry. Opposite kinds of evidence, so no
# reader may treat the field as a bool:
#   MALFORMED  — schema-noncompliant output. The optimizer prompt's fault; charge it.
#   WRONG_TYPE — decoded cleanly but as another model, so the fault is the schema it asked
#                for, not the transport. Charged like MALFORMED.
#   TOOLING    — empty/truncated content. Missing data, not a verdict: charging it scores
#                provider flakiness as a bad mutation, so the round must be EXCLUDED.
L1_PARSE_FAILURE_MALFORMED = "optimizer_prompt_parse_failure"
L1_PARSE_FAILURE_WRONG_TYPE = "optimizer_prompt_unexpected_type"
L1_PARSE_FAILURE_TOOLING = "l1_provider_empty_response"


class RoundResult(StrictModel):
    """Per-round outcome — and the round document itself.
    ``model_dump()`` IS ``rounds/round_NNNN.json`` — declare a field here and it reaches disk."""

    # `extra="ignore"`: `round_id`/`scoreboard` are computed fields — `model_dump()` writes
    # them into the round file, `model_validate()` must not reject them coming back.
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="ignore")

    # --- checkpoint-critical scalars (no raw payloads) ---
    round: int
    label: str
    accuracy: float
    composite_fitness: float = 0.0
    total: int
    improved: bool
    # One-sided two-proportion p-value vs origin; drives the IMPROVED gate. None ⇒ no test ran.
    p_value: float | None = None
    # WHY this round ended the way it did, in the numbers it was decided on — the elected arm's θ,
    # the parent's, the margin and its SE, or on a held round the best arm that still failed to
    # clear. Written on EVERY round, won or held: its predecessor was one constant sentence
    # emitted only on failure, so a round that crowned somebody explained nothing and the
    # operator's "why did THIS one win?" had no surface to answer it. `None` only before the
    # election runs.
    verdict_reason: str | None = None
    degraded_samples: int = 0
    # Fatal-warning samples discarded from total/accuracy on the winner's run.
    deprecated: int = 0
    escalation_signal: EscalationSignal | None = None
    # Origin restricted to the winner's measured samples — apples-to-apples when PoBB locks a
    # candidate early. Drives `improved`, p_value, verdict Δ. ``None`` when the round matched
    # nothing, nullable for the reason stated on ``ScoredCandidate``'s pair above.
    matched_parent_accuracy: float | None = None
    matched_parent_composite: float | None = None
    # The winner's blocked lift, copied from its ``ScoredCandidate``, so a reader of the round
    # can say whether the margin it is about to print is one the round could resolve. ``None``
    # on a round that crowned nobody: the parent's lift over itself is not a measurement.
    matched_parent_lift: float | None = None
    matched_parent_lift_ci_lo: float | None = None
    matched_parent_lift_ci_hi: float | None = None
    # Subset-invariant peer of this round's own `accuracy`: ability of the cumulative frontier
    # on the cycle's fixed δ ruler, so a drifting per-round subset cannot inflate the outer
    # fitness signal. None when the ruler is cold. There is deliberately no
    # `cumulative_accuracy` beside it — pooling rows of mixed provenance is modelled against an
    # explicit per-sample δ, while a plain mean over them silently attributes one
    # configuration's score to another.
    cumulative_theta: float | None = None
    # How sharply THIS round measured the frontier — a precision, never a penalty: the spec
    # forbids it in the election rank key or as a `mean - λ·se` haircut, which discards good
    # candidates on wide posteriors. The panel's `λ·std` slot is cross-seed OUTCOME dispersion,
    # not this.
    cumulative_theta_se: float | None = None
    # None = the ruler is cold (flat δ) and θ is plain logit-accuracy — neither model, and the
    # state a hardcoded "1PL" would misreport.
    calibration_model: CalibrationModel | None = None
    # WHICH δ scale the θs above were read on (`intelligence/exploration.py::ruler_id`). Every
    # cycle refits its ruler from an archive that grows between runs, so a θ is only comparable
    # to one carrying the same id — without it, incomparability is un-derivable and cross-campaign
    # readings silently mix scales. `None` names NO scale: that θ is comparable to nothing.
    ruler_id: str | None = None
    # HOW MANY cells that ruler carried for this reading. The ruler grows by anchored extension,
    # so `ruler_id` alone cannot say how much of the scale was real when the round was read —
    # and a round scored mostly OFF the ruler renders identically to one scored on it.
    ruler_n: int = 0
    # --- raw payload ---
    prompt_fields: dict[str, Any]
    pipeline_params: dict[str, Any] | None = None
    # Prior round's accuracy (or campaign origin for round 0).
    origin_accuracy: float = 0.0
    # Per-sample rows — ``QueryMeasurement`` + stale-data markers (see ``RoundParent.results``).
    results: list[dict[str, Any]] = Field(default_factory=list)
    # Per-candidate scored results — lets resume rescore under a changed scorer + replay decisions.
    all_candidate_results: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    candidates_scored: int
    # How many candidates actually entered the election — measured, leader-eligible, not
    # answer-collapsed. `candidates_scored` counts one step earlier, so the gap is exactly the
    # candidates carrying no measurement of ability at all. Zero is a DIFFERENT round from
    # "everyone lost": nothing was compared against the incumbent, so it says l1_generate
    # produced no testable variant rather than that the search has stalled — which is what the
    # life bank reads it for.
    electable_count: int = 0
    candidate_scores: list[ScoredCandidate] = Field(default_factory=list)
    evaluators: dict[str, float] = Field(default_factory=dict)
    # STORED, not derived: an INPUT to scoring, not a summary of it — it reaches
    # `score_population` as the `l1_diversity` evaluator before any candidate has a score. The
    # collapse COUNTS below are the opposite, pure outputs, and are derived.
    l1_yield: float = 1.0
    # Why this round's L1 output was unparseable (zero candidates), or None. The round owns it:
    # a parse failure yields no candidate to charge. One of the three constants above.
    l1_parse_failure: str | None = None
    # The 1-to-1 reading of the adopted line on one shared set of cells, and the rows this round
    # bought to keep it whole. Two fields for the same reason `accuracy` and `results` are two:
    # one is what a reader is told, the other is what it was read off. The rows are HERE and not
    # in `results` / `all_candidate_results` by design — see `OverlapReading`. `None` before
    # the line has a second member, since C0 alone has nothing to be compared against.
    overlap: OverlapReading | None = None
    overlap_results: list[dict[str, Any]] = Field(default_factory=list)
    # --- computed post-scoring ---
    diagnostics: RoundDiagnostics | None = None
    critique: CritiqueReadout | None = None
    # Stamped at round close — the sole compute site; every surface renders this one.
    health: DegradationHealth | None = None
    # --- stamped as the round closes (the document's own fields) ---
    # Resume rebuilds `Cycle.opt_sp` from it and review/sibling-wounds read its lineage, so it
    # is round state, not a rendering detail. None only on a round that never closed.
    opt_sp: OptSearchPoint | None = None
    # AxisIndex's peaked set at close, persisted because AxisIndex is not reconstructable from
    # the round file alone and the review writer's `evidence_grounding_present` check needs it.
    axis_memory_peaked: list[str] = Field(default_factory=list)
    # Which OPTIMIZER produced this round, per node. The only thing that can answer "was this
    # round produced by the optimizer I am holding now?" once the process exited — everything
    # else a resume re-renders depends on live cycle state. Resume diverges at the FIRST round
    # that disagrees, which is what lets a prompt edit fork a sibling rather than condemn the
    # campaign. Empty ⇒ the round predates the stamp and cannot be asked.
    optimizer_prompt_hashes: dict[str, str] = Field(default_factory=dict)
    # "generation_only" for a sweep round (L1 variants generated, never scored — every
    # scoring scalar below is a structural zero, not a measurement); "" for a scored round.
    status: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def round_id(self) -> str:
        return f"round_{self.round}"

    @property
    def l1_collapsed(self) -> dict[str, int]:
        """DERIVED from ``candidate_scores``: a collapsed candidate rides it with ``invalid=True``,
        never dropped. One reason per candidate, or the parts would sum past the population."""
        counts: dict[str, int] = {}
        for cand in self.candidate_scores:
            if not cand.invalid:
                continue
            reason = next(
                (vf.reason for vf in cand.validation_failures if vf.reason in INVARIANT_REASONS),
                None,
            )
            if reason:
                counts[reason] = counts.get(reason, 0) + 1
        return counts

    @computed_field  # type: ignore[prop-decorator]
    @property
    def l1_n_no_op(self) -> int:
        """Variants whose mutation was empty against the parent."""
        return self.l1_collapsed.get("no_op_variant", 0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def l1_n_duplicate(self) -> int:
        """Variants sig-equal to a sibling in the SAME population."""
        return self.l1_collapsed.get("duplicate_variant", 0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def l1_n_repeat(self) -> int:
        """Variants re-proposing an idea an EARLIER round measured and lost."""
        return self.l1_collapsed.get("repeat_variant", 0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def scoreboard(self) -> list[ScoreboardRow]:
        """Rank-ordered display table — the crown first, then θ, then composite.

        Derived, never stored: it cannot drift from `candidate_scores` the way a
        hand-built twin could. On a warm round rank 1 IS the winner, by construction; on a cold
        one no row carries a θ and the order falls back to the composite it always had.
        """
        from promptpotter.domain.rendering import display_rank_key

        winner_id = self.winner_id
        ranked = sorted(
            self.candidate_scores,
            key=lambda c: display_rank_key(
                c.composite_fitness,
                c.accuracy,
                c.theta,
                is_winner=is_round_winner(c.candidate_id, winner_id),
            ),
            reverse=True,
        )
        return [
            ScoreboardRow(
                rank=i,
                is_winner=is_round_winner(c.candidate_id, winner_id),
                **c.model_dump(include=_SCOREBOARD_INCLUDE),
            )
            for i, c in enumerate(ranked, start=1)
        ]

    @property
    def winner_id(self) -> str:
        """The elected winner's lineage id, read off the id ``l1/score/winner.py`` stamped onto
        ``prompt_fields``. Empty only when no candidate was crowned — then no row is a winner."""
        lineage = self.prompt_fields.get("lineage")
        return str(lineage.get("id", "")) if isinstance(lineage, dict) else ""


class CycleResult(StrictModel):
    rounds: list[RoundResult]
    # Origin-EXCLUSIVE, unlike the persisted `index.json::n_rounds`, which counts round 0.
    n_l1_rounds: int
    best_accuracy: float
    best_round: int
    # They travel together because a consumer reading one against a composite computed on some
    # other basis is comparing two different measurements.
    origin_accuracy: float
    origin_composite_fitness: float = 0.0
    # The L4 outer proxy's inner-search signal: the origin's level and the ability of the
    # incumbent each round ADOPTED — the CROWNED frontier, never the round's proposals, which
    # turn the metric NEGATIVE for exactly the generators that explore. Both live in ONE space
    # (θ on the cycle's fixed δ ruler when warm, else the cycle is excluded), so no proxy delta
    # subtracts across scales, and levels are NOT floored at origin: a regressing optimizer
    # prompt must yield a level below origin or the outer loses the gradient away from it.
    # `origin_level` is `None`, not `0.0`, when the origin was never scored — every round's lift
    # is differenced against it, so a fabricated 0.0 reports the trajectory as an enormous
    # improvement over nothing. Absent ⇒ the cycle is excluded.
    origin_level: float | None = None
    round_adopted_levels: list[float] = Field(default_factory=list)
    # Index-aligned with the two above: a round that did not move the incumbent did not sharpen
    # the reading of it either. The WITHIN-cell precision an L4 panel needs to tell estimation
    # noise from between-cell heterogeneity. Precision only — never a penalty term.
    origin_level_se: float | None = None
    round_adopted_level_ses: list[float] = Field(default_factory=list)
    # The denominator the L4 law averages over, and it must come from the config rather than
    # ``len(round_adopted_levels)``: a cycle stopped early by ``lives`` adopted fewer levels, so
    # a mean over "rounds that happened" compares two estimands — and it points the wrong way,
    # since ``lives`` stops a STALLING cycle and the shorter series pays it for quitting once it
    # had lifted. 0 = never declared, and the law falls back to the series length.
    round_budget: int = 0
    winner_prompt_fields: dict[str, Any]
    winner_pipeline_params: dict[str, Any] | None = None
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
    source_campaign_accuracy: float
    source_campaign_composite: float
    source_campaign_n: int
    # ``noise-floor`` only: the backend's own run-to-run noise, not a comparison to history.
    noise_floor_k: int | None = None
    noise_floor_mean: float | None = None
    noise_floor_ci_lo: float | None = None
    noise_floor_ci_hi: float | None = None
    noise_floor_raw: list[float] | None = None


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
    reasons: list[str] = Field(default_factory=list)
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


class PayloadOutcome(StrictModel):
    source_file: str
    # A ``StopOutcome`` value for attempted forks — the one StopReason classification, never a
    # sweep-private vocabulary — or the batch states skipped | skipped_already_forked.
    status: str
    cycle_id: str


class SweepBatchResult(StrictModel):
    batch_id: str
    parent_cycle_id: str
    family_root: str
    started_at: str
    completed_at: str
    fork_cycle_ids: list[str]
    payload_outcomes: list[PayloadOutcome]
    interrupted: bool
