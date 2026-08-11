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
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.errors import is_error_result

# Which IRT model a cycle's δ ruler was fitted under. The absence of a member is the third,
# real state: a cold ruler is FLAT, so θ degenerates to logit-accuracy — neither 1PL nor 2PL.
# Carried as `None` on the models below; never collapse it into "1PL".
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
    "SpendBucket",
    "SpendRollup",
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

    # `extra="ignore"`: round files written before `hits`/`ci_lo`/`ci_hi` were dropped
    # still carry them, and a stale key must not make a paid measurement unreadable.
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True, extra="ignore")

    candidate_id: str
    label: str
    changes_description: str = ""
    accuracy: float
    composite_fitness: float
    total: int
    evaluators: dict[str, float] = Field(default_factory=dict)
    pipeline_params_override: dict[str, Any] | None = None
    # The fully-resolved per-node config this candidate's searchpoint executes —
    # origin floor ⊕ this candidate's delta, config-only (model/provider/
    # reasoning_effort/temperature + ``steps``; the prompt rides ``prompt_fields``).
    # Served so the OBSERVE view reads the effective config verbatim and never
    # re-merges client-side. Distinct from the sparse ``pipeline_params_override``
    # above (the fork transport) — two data classes, not a stitch.
    resolved_pipeline_params: dict[str, Any] | None = None
    # The candidate's evolved prompt (``OptSearchPoint.prompt_field_dict()`` shape).
    # Paired with ``pipeline_params_override`` this is the full searchpoint an
    # operator selects to seed an operator-steered fork (read side, Decision F).
    prompt_fields: dict[str, Any] = Field(default_factory=dict)
    escalation_aborted: bool = False
    elimination_stopped: bool = False
    scored_samples: int = 0
    expected_samples: int = 0
    # Of ``scored_samples``, how many were replayed from the MeasurementArchive rather than
    # measured. Non-zero off the origin means the searchpoint already existed — a duplicate.
    cached_samples: int = 0
    # Why a partial subset was scored (``scored_samples < expected_samples``):
    # "" (full / not partial) | "skip" (operator early-abort — marks the cycle
    # ``human_intervened``). Distinct from ``elimination_stopped``/``escalation_aborted``.
    partial_reason: str = ""
    invalid: bool = False
    validation_failures: list[ValidationFailure] = Field(default_factory=list)
    runtime_failures: list[RuntimeFailure] = Field(default_factory=list)
    elimination_context: EliminationContext = Field(default_factory=EliminationContext)
    degradation_context: DegradationContext = Field(default_factory=DegradationContext)
    # The origin as this candidate's comparison floor — ``None`` unless the candidate covered
    # the origin's whole panel. This is NOT the population ``theta`` is ``None`` for: a cut
    # candidate is usually still in the election fit and keeps its θ, because θ on the fixed δ
    # ruler is subset-invariant and an accuracy is not. The round order is stratified on the
    # incumbent's own grades, so on a prefix the origin's rate is ``⌊n/4⌋/n`` — see
    # ``scoring/metrics.py::matched_origin_stats`` for the measurement.
    # These MUST NOT default to 0.0. An unstamped 0.0 is indistinguishable from a real origin
    # that scored nothing, and it reads as "this candidate beat the origin by its whole accuracy"
    # — which is what the inner narrative told the outer optimizer, on every eliminated arm.
    matched_origin_accuracy: float | None = None
    matched_origin_composite: float | None = None
    # The BLOCKED lift over that floor and its interval: the mean per-cell ``(candidate − origin)``
    # difference across the cells both measured, Student-t bracketed
    # (``scoring/selection.py::matched_origin_lift``). Same scale as ``composite_ci_*`` below and
    # sharper than it on the same rows, because pairing removes the cell-to-cell variation in the
    # origin instead of carrying it as noise — which is the whole reason a seed-paired panel is
    # worth its cost. ``None`` below two shared cells; an interval from one pair is a fiction.
    #
    # This is the one interval that answers "did it beat the origin, or did the panel just
    # wobble". It reads at EVERY level: inner cells are samples, outer cells are whole inner
    # campaigns, and the arithmetic does not care which. An L4-only reimplementation of it in
    # composite-fitness units used to live in ``domain/l4/verdict.py``, keyed on the query text
    # rather than ``sample_id`` and grading an errored row as absent rather than 0.0 — a second
    # opinion that could disagree with the election it sat beside, and did.
    matched_origin_lift: float | None = None
    matched_origin_lift_ci_lo: float | None = None
    matched_origin_lift_ci_hi: float | None = None
    # Difficulty-adjusted Rasch ability (+ Laplace SE) on the round's joint-fit scale — the
    # subset-invariant metric the winner election ranks by (`elect_round_winner`), stamped from
    # that single fit. Distinct from subset-relative `accuracy`: it discounts for *which* samples
    # this candidate saw, so it explains a lower-accuracy winner. `None` for candidates outside
    # the election fit (eliminated / under the coverage floor).
    theta: float | None = None
    theta_se: float | None = None
    # Normal-CLT CI on the mean per-cell composite fitness (``metrics.composite_ci`` —
    # ``mean_ci`` over ``_mean_fitness_by_cell``, the same reader the θ / paired decision
    # metrics use) — always present for any candidate with ≥1 scored cell, unlike ``theta_se``
    # (fit-restricted). No composite point estimate should stand alone.
    composite_ci_lo: float | None = None
    composite_ci_hi: float | None = None


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


# ``ScoredCandidate``'s display subset, spelled once. Deliberately narrower than the full
# ``candidate_scores`` dump beside it in the same file (no θ): scoreboard = the display table,
# candidate_scores = the complete record. The mutation text rides ``changes_description`` (its
# real name) — the dashboard's ``label`` (the C{r}.{i} id) is a different field, so the two
# never collide.
_SCOREBOARD_INCLUDE: set[str] = {
    "candidate_id",
    "changes_description",
    "accuracy",
    "composite_fitness",
    "total",
    "composite_ci_lo",
    "composite_ci_hi",
    "escalation_aborted",
    "matched_origin_accuracy",
    "matched_origin_composite",
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
    # ``None`` for a row that did not cover the origin's panel — see ``ScoredCandidate``. The
    # round file carries the absence rather than a 0.0 that reads as a verdict the origin never
    # gave, or a prefix rate that reads as one the origin never earned.
    matched_origin_accuracy: float | None
    matched_origin_composite: float | None
    composite_ci_lo: float | None
    composite_ci_hi: float | None
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
    # Per-sample ``QueryMeasurement`` rows plus open-ended stale-data protocol
    # markers (``retry_of_degraded`` etc.) — kept ``dict`` so the markers survive
    # serialization (a closed model would strip them); readers cast at hot sites.
    results: list[dict[str, Any]] = Field(default_factory=list)


# The reasons `RoundResult.l1_parse_failure` can carry. Two are opposite kinds of evidence,
# so no reader may treat the field as a bool:
#   MALFORMED  — the optimizer prompt drove the optimizer LLM to emit schema-noncompliant output.
#                That IS the optimizer prompt's fault; charge it (L4 scores the round dirty).
#   WRONG_TYPE — a MALFORMED variety worth naming: the response decoded cleanly but as some
#                other model, so the failure is the schema the optimizer prompt asked for, not
#                the transport. Charged like MALFORMED (`proxies.py` excludes only TOOLING).
#   TOOLING    — the optimizer LLM returned empty/truncated content. Missing data, not a
#                verdict. Charging it scores provider flakiness as a bad mutation; the round
#                must be EXCLUDED, exactly as a crashed inner cycle is excluded as a sample.
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
    # Origin restricted to the winner's measured samples — apples-to-apples when PoBB locks
    # at q8/20 while origin has 20. Drives `improved`, p_value, verdict Δ.
    # ``None`` when the round matched nothing — a generation-only sweep round scored no
    # candidate, so there is no origin restricted to "the winner's samples". Nullable for the
    # reason stated on ``ScoredCandidate``'s pair above, which this one contradicted: 0.0 is a
    # measurement ("the origin got everything wrong"), and a round carrying it reads as a
    # candidate that beat the origin by its entire accuracy.
    matched_origin_accuracy: float | None = None
    matched_origin_composite: float | None = None
    # The winner's blocked lift over that floor and its interval, copied from its
    # ``ScoredCandidate`` — the round-level mirror of the pair above, so a reader of the round
    # (the CLI header, the L4 narrative) can say whether the margin it is about to print is one
    # the round could actually resolve. ``None`` on a round that crowned nobody: there is no
    # winner to have lifted, and the parent's lift over itself is not a measurement.
    matched_origin_lift: float | None = None
    matched_origin_lift_ci_lo: float | None = None
    matched_origin_lift_ci_hi: float | None = None
    # Subset-invariant peer of this round's own `accuracy`: ability of the cumulative frontier
    # on the cycle's fixed δ ruler. None when the ruler is cold. Feeds the L4 outer proxy so a
    # drifting per-round subset can't inflate the outer fitness signal.
    #
    # There is deliberately no `cumulative_accuracy` beside it. θ is an ability fitted against
    # an explicit per-sample δ, so pooling rows of mixed provenance is a modelled operation;
    # a plain mean over the same rows is not — it silently attributes one configuration's
    # score to another. See `merge_known_outcomes`.
    cumulative_theta: float | None = None
    # That θ's own standard error, dispersion-corrected, straight off the same fit. It is how
    # sharply THIS round measured the frontier — a precision, never a penalty: the spec forbids
    # it in the election rank key or as a `mean - λ·se` haircut (that discards good candidates on
    # wide posteriors), and the panel's `λ·std` slot is cross-seed OUTCOME dispersion, not this.
    # Its consumer is the L4 panel, which without it must estimate estimation noise and
    # between-cell heterogeneity from one spread of six scalars and cannot separate them.
    cumulative_theta_se: float | None = None
    # Which IRT model the δ ruler above was fitted under ("1PL" | "2PL"), so the operator
    # reads the model the engine chose. None = the ruler is cold (flat δ) and θ is plain
    # logit-accuracy — neither model, and the state a hardcoded "1PL" would misreport.
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
    # How many candidates actually entered the election — measured, leader-eligible, and not
    # answer-collapsed. `candidates_scored` counts one step earlier, so the gap between them is
    # exactly the candidates that answered a single label to everything and carry no measurement
    # of ability at all.
    #
    # Zero is a DIFFERENT round from "everyone lost": nothing was compared against the
    # incumbent, so the round says nothing about whether the search has stalled — it says
    # l1_generate failed to produce a testable variant. The life bank reads this to tell those
    # two apart (`EscalationFSM._bank_round`); the escalation rules already route the generator
    # failure to L2 on their own signals.
    electable_count: int = 0
    candidate_scores: list[ScoredCandidate] = Field(default_factory=list)
    evaluators: dict[str, float] = Field(default_factory=dict)
    # L1 yield + failure counts; defaults assume "all valid" for replay paths bypassing the detector.
    # STORED, not derived: `l1_yield` is an INPUT to scoring, not a summary of it. It reaches
    # `score_population` as the `l1_diversity` evaluator before any candidate has a score, so
    # there is nothing to derive it from at the moment it is needed. The collapse COUNTS below
    # are the opposite — pure outputs — and are derived.
    l1_yield: float = 1.0
    # Reason this round's L1 output was unparseable (zero candidates), or None. The round owns
    # it: a parse failure yields no candidate to charge, and `candidate_scores` is empty in
    # exactly that round. One of `L1_PARSE_FAILURE_MALFORMED` / `L1_PARSE_FAILURE_TOOLING` —
    # the two are opposite kinds of evidence (see their docstrings) and must never be
    # collapsed to a bool by a reader.
    l1_parse_failure: str | None = None
    # --- computed post-scoring ---
    diagnostics: RoundDiagnostics | None = None
    critique: CritiqueReadout | None = None
    # Context-aware degradation verdict, stamped at round close (sole compute
    # site); every surface (dashboard summary, CLI, round file) renders this.
    health: DegradationHealth | None = None
    # --- stamped as the round closes (the document's own fields) ---
    # The cycle's working searchpoint at close. Resume rebuilds `Cycle.opt_sp` from it,
    # and review/stats/sibling-wounds read its lineage — so it is round state, not a
    # rendering detail. None only on a round that never closed.
    opt_sp: OptSearchPoint | None = None
    # AxisIndex's peaked set at close. Persisted because the review writer's
    # `evidence_grounding_present` check needs it (`output.py::_compute_behavior_per_round`)
    # and AxisIndex is not reconstructable from the round file alone.
    axis_memory_peaked: list[str] = Field(default_factory=list)
    # Which OPTIMIZER produced this round — per node, template ⊕ layout ⊕ resolved config
    # (`compute_optimizer_prompt_hashes`). Stamped at close, and the only thing that can
    # answer "was this round produced by the optimizer I am holding now?" after the process
    # that ran it exited: everything else a resume can re-render depends on live cycle state
    # and so cannot be reproduced across processes. Resume compares it per round and diverges
    # at the FIRST one that disagrees, which is what lets a prompt edit fork a sibling instead
    # of either being ignored or condemning the whole campaign. Empty ⇒ the round predates
    # the stamp and cannot be asked; resume says so rather than assuming either answer.
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


class SpendBucket(StrictModel):
    """One spend sub-bucket (backend or optimizer-loop). Mutated only by
    ``_handle_token_usage``. ``used_usd`` is the BILL; ``incurred_usd`` prices cache hits too."""

    used_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    # How much of ``output_tokens`` bought hidden reasoning rather than an answer — a
    # SUBSET of it, so it is never added into a total. The question it answers is latency,
    # not money: on the shipped optimizer route this runs ~94%, which is why the loop owns
    # a third of an L4 cell's wall-clock while every worker-model swap left that untouched.
    reasoning_tokens: int = 0
    rate_known: bool = False
    model: str | None = None
    # Tokens billed under this bucket whose USD cost couldn't be resolved (no wire
    # cost AND no rate on file). >0 means the USD cap is blind to real spend here —
    # the dashboard surfaces a "USD cap inactive" warning; the token cap backstops.
    unpriced_tokens: int = 0

    incurred_usd: float = 0.0
    # The incurred-side twin of ``unpriced_tokens``: >0 ⇒ ``incurred_usd`` UNDERSTATES
    # what this search costs, so anything dividing by it would read cheapness that never
    # happened. The L4 no-evidence guard refuses such a cell rather than score it fitter.
    incurred_unpriced_tokens: int = 0


class SpendRollup(StrictModel):
    """A cycle's spend: the two buckets, and the totals every consumer reads off them.
    ``total_used_usd`` is the BILL a budget caps; ``total_incurred_usd`` prices cache hits too."""

    backend: SpendBucket = Field(default_factory=SpendBucket)
    loop: SpendBucket = Field(default_factory=SpendBucket)
    total_used_usd: float = 0.0
    total_incurred_usd: float = 0.0

    @property
    def input_tokens(self) -> int:
        return self.backend.input_tokens + self.loop.input_tokens

    @property
    def output_tokens(self) -> int:
        return self.backend.output_tokens + self.loop.output_tokens

    @property
    def total_tokens_used(self) -> int:
        """Cumulative BILLED tokens across both buckets — the token halt probe's source. Cache hits
        are excluded: a cap bounds what the run spends, not what it would have spent."""
        return self.input_tokens + self.output_tokens

    @property
    def unpriced_tokens(self) -> int:
        """Billed tokens with no resolvable USD rate. >0 means ``total_used_usd``
        UNDERSTATES real spend — it is a floor, not the total."""
        return self.backend.unpriced_tokens + self.loop.unpriced_tokens

    @property
    def incurred_unpriced_tokens(self) -> int:
        """Incurred-side twin of :attr:`unpriced_tokens`. >0 ⇒ the L4 efficiency proxy would divide by
        an understated cost and read cheapness that never happened, so such a cell is refused."""
        return self.backend.incurred_unpriced_tokens + self.loop.incurred_unpriced_tokens


class CycleResult(StrictModel):
    rounds: list[RoundResult]
    # Completed L1 rounds only — origin-EXCLUSIVE (the origin is round 0 but is
    # not an L1 round). The persisted index.json::n_rounds counts round 0; the
    # distinct name keeps the two counts from being conflated across sinks.
    n_l1_rounds: int
    best_accuracy: float
    best_round: int
    # The origin's own two headline scalars, on the origin's own sample basis. They
    # travel together because a consumer reading one against a composite computed on
    # some other basis is comparing two different measurements.
    origin_accuracy: float
    origin_composite_fitness: float = 0.0
    # The L4 outer proxy's single-scale inner-search signal (built by
    # `exploration.adopted_level_trajectory` at finalize). `origin_level` is the origin's level
    # and `round_adopted_levels` the ability of the incumbent each round ADOPTED — both in ONE
    # space (θ on the cycle's fixed δ ruler when warm, else the cycle is excluded), so no proxy
    # delta ever subtracts across scales. Levels are NOT floored at origin — a regressing
    # optimizer prompt yields a level below origin so the outer keeps a gradient to avoid it.
    #
    # These read the CROWNED frontier. They once read the round's proposals instead, on the
    # argument that a conservative election rarely crowns at a small inner budget and so gave
    # the outer ~zero signal. `5f6bcb44` fixed the election (a Rasch fit needs two arms; c0_ok
    # was gating rounds it never should have), and crowning is now routine — while averaging
    # proposals had turned the metric NEGATIVE for exactly the generators that explore, since
    # every arm the search correctly discarded pulled the mean down.
    #
    # `origin_level` is `None`, not `0.0`, when the origin was never scored (an init-crash, a
    # cycle that halted before round 0). A zero floor is not a low floor: every round's lift is
    # differenced against it, so a fabricated 0.0 reports the whole trajectory as an enormous
    # improvement over nothing. Absent ⇒ the cycle is excluded.
    origin_level: float | None = None
    round_adopted_levels: list[float] = Field(default_factory=list)
    # Each level's own standard error, index-aligned with the two above and written by the same
    # carry-forward (`exploration.adopted_level_trajectory`) — a round that did not move the
    # incumbent did not sharpen the reading of it either. This is the WITHIN-cell precision an
    # L4 panel needs: without it the outer verdict must infer estimation noise and between-cell
    # heterogeneity from a single spread of six scalars, and cannot tell them apart. Precision
    # only — never a penalty term (see `RoundResult.cumulative_theta_se`).
    origin_level_se: float | None = None
    round_adopted_level_ses: list[float] = Field(default_factory=list)
    # The ROUND BUDGET this cycle was given — ``optimization.max_rounds``, the ceiling every
    # arm on an L4 panel shares. It is the denominator the L4 law averages over, and it has to
    # come from the config rather than from ``len(round_adopted_levels)``: a cycle stopped early
    # by ``lives`` adopted fewer levels than one that ran the budget out, so a mean over "rounds
    # that happened" is a mean over a DIFFERENT number of slots per cell — two estimands, then
    # compared. Worse, it points the wrong way: ``lives`` stops a STALLING cycle, so dividing by
    # the shorter series pays a cell for quitting once it had lifted. 0 = never declared, and the
    # law then falls back to the series length (see ``domain/l4/proxies.py``).
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
    # Set when ``stop_reason`` ∈ ``{CRASHED, RENDER_ERROR, DIVERGED}``;
    # ``None`` on clean completions. The runner's ``except`` sites carry the
    # record returned by ``emit_error_record`` straight here — the same
    # ``ErrorRecord`` that lands on the ledger, no twin model.
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
    # ``noise-floor`` only: the spread of ``k`` ``force_fresh`` re-scores of one fixed
    # config — the backend's own run-to-run noise, not a comparison to history.
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

# Which fitness number headlines the operator's surfaces. One owner: `CampaignConfig`
# declares the campaign default and `LiveDashboardState` serves it, so the two cannot
# drift into a wide `str` on one side and a closed union on the other.
HeadlineMetric = Literal["accuracy", "composite", "ability"]

# Which key ranks the hard-sample leaderboard. Same one-owner rule as `HeadlineMetric`
# above. `info_gain` is the queue mechanism's own acquisition score (`pick_value`) — what
# the leaderboard has always shown; `difficulty` is the Rasch ruler δ_s alone. Ranks what
# the operator READS and nothing else: the order the engine actually scores in is
# `build_round_order`, which no knob here reaches.
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
    # Samples whose pipeline SUCCEEDED but emitted no extractable prediction
    # (empty terminal ranking → ``NO_RESULT``). PP-owned — the backend stamps no
    # warning, since from its side generation succeeded. A high share is a
    # structurally-unscoreable floor (answer-format / extraction mismatch),
    # distinct from a wrong-but-extractable miss. Drives the ``unscoreable`` grade.
    no_result_count: int = 0
    # HOLES — cells attempted that came back carrying no measurement at all
    # (``unscoreable_cells``: a typed ``error_category`` that is not a transport failure,
    # so ``pipeline_data`` is empty and every warning-based classifier is blind to it).
    # Counted separately from ``no_result_count`` because the remedy differs and the
    # operator banner names it: a NO_RESULT row means the pipeline RAN and emitted nothing
    # parseable (fix the answer format), while a hole means the cell never reported (re-run
    # it). Before this existed such a row joined ``samples`` and no numerator, so it made a
    # round grade HEALTHIER the more of them it had — most consequentially at round 0,
    # where the grade feeds ``origin_gate_tripped`` and there is no panel gate to catch it.
    hole_count: int = 0
    # Share of this round's predictions on its single commonest label
    # (``domain/scoring.py::modal_answer_share``); ``None`` where the answer space makes
    # collapse meaningless. REPORTED, never graded — no ``reasons`` entry, no precedence
    # arm, deliberately. A model hedging to one label is the addressable failure the loop
    # exists to correct in its first rounds, so grading it critical would halt the very
    # optimization that fixes it, and the round-over-round series is the point: falling =
    # working. It rides HERE because health is the only per-round verdict every surface
    # already renders, and until now nothing outside the L1 prompt panel could see it — an
    # origin answering one label 95% of the time graded ``healthy`` and nobody was told.
    answer_modal_share: float | None = None
    degraded_rate: float
    consecutive_degraded_rounds: int
    prior_clean_rounds: int
    dominant_node: str | None = None
    node_failure_rates: dict[str, float] = Field(default_factory=dict)
    # Verbatim upstream reasons per node ("[code] message"), harvested from the
    # connector's StepWarnings — the evidence behind the verdict, connector-agnostic.
    node_warnings: dict[str, list[str]] = Field(default_factory=dict)
    suggested_action: str | None = None


class PayloadOutcome(StrictModel):
    source_file: str
    # A ``StopOutcome`` value for attempted forks (success | paused | halted |
    # failed — the one StopReason classification, never a sweep-private
    # vocabulary), or the batch bookkeeping states skipped | skipped_already_forked.
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
