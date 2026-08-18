from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, NotRequired, TypedDict

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
from promptpotter.domain.run_records import ErrorRecord
from promptpotter.domain.scoring import is_answer_collapsed
from promptpotter.domain.spend import SpendRollup
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.errors import is_error_result

# Which IRT model a cycle's δ ruler was fitted under. The ABSENCE of a member is the third,
# real state — a cold ruler is flat, so θ degenerates to logit-accuracy. Never collapse it
# into "1PL".
CalibrationModel = Literal["1PL", "2PL"]

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
    "PayloadOutcome",
    "RoundParent",
    "RoundResult",
    "ScoreboardRow",
    "ScoredCandidate",
    "SweepBatchResult",
    "WarningDict",
    "best_round_by_measured_accuracy",
    "candidate_label",
    "is_electable",
    "is_leader_eligible",
    "is_round_winner",
    "merge_known_outcomes",
    "unscoreable_cells",
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
    # When `improved=False` despite Δ>0: which of {delta_threshold, significance, min_samples} blocked promotion.
    improved_reason: str | None = None
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
        """Rank-ordered display table — composite-first, accuracy-tiebreak; winner tagged.

        Derived, never stored: it cannot drift from `candidate_scores` the way a
        hand-built twin could.
        """
        from promptpotter.domain.rendering import display_rank_key

        ranked = sorted(
            self.candidate_scores,
            key=lambda c: display_rank_key(c.composite_fitness, c.accuracy),
            reverse=True,
        )
        return [
            ScoreboardRow(
                rank=i,
                is_winner=is_round_winner(c.candidate_id, self.winner_id),
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
