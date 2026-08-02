"""Round and run outcome models — pure pydantic types, no I/O."""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

from pydantic import ConfigDict, Field, computed_field

from promptpotter.domain.escalation_signals import (
    INVARIANT_REASONS,
    EscalationSignal,
    RuntimeFailure,
    ValidationFailure,
)
from promptpotter.domain.l4.verdict import OuterVerdict
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import StopReason
from promptpotter.domain.round_diagnostics import RoundDiagnostics
from promptpotter.domain.run_records import DecisionRecord, ErrorRecord
from promptpotter.domain.strict_model import StrictModel

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
    "CycleSpend",
    "DegradationContext",
    "DegradationHealth",
    "DiagnosticRunRecord",
    "EliminationContext",
    "HeadlineMetric",
    "PayloadOutcome",
    "RoundParent",
    "RoundResult",
    "RoundSummary",
    "RoundSummaryCandidate",
    "ScoreboardRow",
    "ScoredCandidate",
    "SweepBatchResult",
    "WarningDict",
    "best_round_by_measured_accuracy",
    "candidate_label",
    "is_leader_eligible",
    "is_round_winner",
]


class EliminationContext(TypedDict, total=False):
    """PoBB exit context on a ``ScoredCandidate`` — written by ``decode_signal_effect``
    when the elimination check fires. Empty ``{}`` when the candidate wasn't cut.
    ``leader_label`` is decorated post-construction (needs prior-rank lookup), so the
    whole shape is ``total=False``. Disjoint from :class:`DegradationContext`."""

    p_best: float
    epsilon: float
    leader_id: str
    queries_scored: int
    total_queries: int
    n_priors: int
    leader_locked: bool
    leader_label: str


class DegradationContext(TypedDict, total=False):
    """DegradationCheck / scoring-error-abort exit context on a ``ScoredCandidate``.
    Empty ``{}`` when the candidate wasn't degradation-cut. Disjoint from
    :class:`EliminationContext` — the renderer branches on which is non-empty."""

    degraded_rate: float
    degraded_count: int
    total_scored: int
    dominant_warning: str
    fatal: bool
    warning_types: dict[str, int]
    source: str


class CritiqueReadout(TypedDict, total=False):
    """Serialized ``L1CritiqueOutput`` as it rides ``RoundResult.critique`` and the
    dispatch ``RoundDigest``. Domain-local mirror of the three load-bearing fields so
    a round file round-trips without the optimization layer's schema in scope; the
    optimizer node's full Pydantic shape stays in ``dispatch/schemas.py``."""

    priority_fix: str
    suggested_axes: list[str]
    failure_highlights: list[str]


def candidate_label(round_num: int, idx: int) -> str:
    """Canonical candidate label — `C0` for origin, `C{N}.{idx+1}` otherwise. Sole writer."""
    if round_num == 0:
        return "C0"
    return f"C{round_num}.{idx + 1}"


def best_round_by_measured_accuracy(
    rounds: list[dict[str, Any]],
) -> tuple[float, int | None]:
    """Highest round ``accuracy`` across ``rounds`` → ``(accuracy, round)``.
    Ties keep the earliest round (``max`` returns the first). Empty ⇒ ``(0.0, None)``.

    Every round's ``accuracy`` is ONE configuration measured on the samples that round drew
    — the elected winner's, or on a held round the retained incumbent's re-score
    (``rescore_parent``). So the headline is always a number something actually scored, and
    the round's ``total`` states what it was scored over.

    This replaced an argmax over ``cumulative_accuracy``, a sample-keyed union of rows
    measured by DIFFERENT configurations. That published ``57%→78%`` for a cycle whose best
    candidate ever measured 0.679, and crowned a round whose two candidates scored 0.0 and
    0.481. See ``cycle.py::_merge_known_outcomes``.

    Sole definition of the headline-best derivation — the cycle index (`_apply_best`) and
    the resume/fork trajectory rebuild (`resolve_resume_state`) both call this so
    ``index.json::best_accuracy`` and ``dashboard.json::best`` agree by construction rather
    than by two hand-copied ``max`` folds. NOT the winner export (that argmaxes
    ``composite_fitness`` — the optimizer's objective); this is the formula-independent
    accuracy operators recognize. See ``architecture.md`` §0.5."""
    # `or 0.0` would rank a round that never recorded an accuracy alongside one that
    # genuinely scored 0% — and could crown it. A round with no number doesn't back the headline.
    scored = [r for r in rounds if r.get("accuracy") is not None]
    best = max(scored, key=lambda r: float(r["accuracy"]), default=None)
    if best is None:
        return (0.0, None)
    return (float(best["accuracy"]), best.get("round"))


def is_round_winner(candidate_id: str, winner_id: str) -> bool:
    """The round winner is the candidate the election ELECTED — matched by identity.

    Sole definition of the rule; the round-file scoreboard (`RoundResult.scoreboard`), the
    dashboard round summary (`build_round_summary`) and the terminal readout all call this,
    so the operator-visible winner flag can't diverge between surfaces.

    Prose is not an identity: `changes_description` can be EMPTY (the label falls back to
    `lineage.id[:12]`) and IDENTICAL across candidates (`detect_invariants` dedups on the
    mutation SIGNATURE, not the prose).

    The elected id is already on disk in two places — the `ROUND_WINNER` decision record and
    `RoundResult.prompt_fields["lineage"]["id"]`. Ask it; don't re-derive it from prose."""
    return bool(winner_id) and candidate_id == winner_id


class ScoredCandidate(StrictModel):
    """One candidate's L1 score report — the single shape for round-file scores.

    ``model_dump()`` *is* the wire format; ``model_validate()`` reads it back.

    There is no ``hits`` and no Wilson interval. ``accuracy`` IS mean fitness, so on a
    binary scorer ``hits/total`` was exactly that number a second time, and on a graded
    one it counted an unreachable ceiling. The interval that belongs beside a composite
    is ``composite_ci_lo``/``_hi``, which brackets the quantity actually reported.
    """

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
    # Why a partial subset was scored (``scored_samples < expected_samples``):
    # "" (full / not partial) | "skip" (operator early-abort — marks the cycle
    # ``human_intervened``). Distinct from ``elimination_stopped``/``escalation_aborted``.
    partial_reason: str = ""
    invalid: bool = False
    validation_failures: list[ValidationFailure] = Field(default_factory=list)
    runtime_failures: list[RuntimeFailure] = Field(default_factory=list)
    elimination_context: EliminationContext = Field(default_factory=EliminationContext)
    degradation_context: DegradationContext = Field(default_factory=DegradationContext)
    # Origin restricted to this candidate's measured samples — apples-to-apples when PoBB locks
    # mid-budget; equals full-set origin when fully scored. ``None`` for candidates outside the
    # election fit (eliminated / under the coverage floor) — the same population ``theta`` is
    # ``None`` for, and for the same reason: nothing matched them, so there is no comparison.
    # These MUST NOT default to 0.0. An unstamped 0.0 is indistinguishable from a real origin
    # that scored nothing, and it reads as "this candidate beat the origin by its whole accuracy"
    # — which is what the inner narrative told the outer optimizer, on every eliminated arm.
    matched_origin_accuracy: float | None = None
    matched_origin_composite: float | None = None
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
    """Eligibility for round-leader election — a **validity** predicate, never a ranking one.

    Disqualifies exactly the two shapes whose measurement cannot be trusted: (a)
    escalation-aborted-without-PoBB (mid-run failure outside measured comparison) and (b)
    degradation/scoring_error_abort (partial subset accuracy can fake-inflate above origin).

    **A stop is not a disqualification.** A PoBB stop — ε, futility, lock-in — is a BUDGET
    decision: "more samples will not change the answer". It says nothing about whether the
    candidate is the round's best, and the election is the thing that answers that, on θ over
    the fixed ruler with ``coverage_floor`` guarding thin arms. Conflating the two cost a real
    round: a candidate stopped at 19/28 carried a genuine +0.099 θ lift over origin and was the
    best thing measured, but its stop wrote ``p_best: 0.0`` (a placeholder the gate never
    computed) and this predicate read that as "PoBB says it lost". Every candidate in the round
    stopped the same way, so the round crowned nobody — silently, with `improved=False` and no
    reason recorded.

    Lives beside the model it judges, not in the L1 scoring module: it is a pure predicate
    over recorded facts, and every reader of a recorded round needs it — the live election,
    the mask read-model, the lineage backprop. Filed under the loop, it dragged the whole
    optimization package into any module that merely wanted to read a candidate's fate.
    """
    if cs.escalation_aborted and not cs.elimination_stopped:
        return False
    return not cs.degradation_context


# ``ScoredCandidate``'s display subset, spelled once. Deliberately narrower than the full
# ``candidate_scores`` dump beside it in the same file (no θ / composite_ci): scoreboard =
# the display table, candidate_scores = the complete record. The mutation text rides
# ``changes_description`` (its real name) — the dashboard's ``label`` (the C{r}.{i} id) is a
# different field, so the two never collide.
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
    # ``None`` for a row nothing was matched to (eliminated / under the coverage floor) — see
    # ``ScoredCandidate``. The round file carries the absence rather than a 0.0 that reads as a
    # verdict the origin never gave.
    matched_origin_accuracy: float | None
    matched_origin_composite: float | None
    composite_ci_lo: float | None
    composite_ci_hi: float | None
    is_winner: bool


class CandidateProposal(StrictModel):
    """LLM-proposed candidate — OSP + the two override deltas, persisted across generate→score for resume replay.

    Both deltas ride here, not just the merged result: the OSP carries the *resulting*
    prompt fields (parent's, plus whatever L1 changed), which cannot answer "what did L1
    actually propose this round" — the question `prompt_block_catalogue="restrict"` asks.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    opt_sp: OptSearchPoint
    pipeline_params_override: dict[str, dict[str, Any]] = Field(default_factory=dict)
    prompt_fields_override: dict[str, str] = Field(default_factory=dict)


class RoundParent(StrictModel):
    """The individual this round's candidates were mutated from, scored over the samples they
    touched so every paired diff is matched. It is the origin only at round 0; after that it is
    the prior winner. On probe rounds it reflects the probe subset, not the full set.

    Its measurement is a `ScoredCandidate` like any other individual's — same shape, same
    single builder, straight from the scoring gateway. It carried loose `accuracy` /
    `composite_fitness` / `evaluators` / `label` copies instead, which is what let a
    re-score drop the evaluator namespace with nothing to notice.
    """

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

    ``model_dump()`` IS ``rounds/round_NNNN.json``; ``model_validate()`` reads it back.
    There is no second, hand-mirrored payload dict: a new field here reaches disk, the
    `GET /rounds/{n}` route, and every reader by declaring it, not by being copied into
    a builder that can forget it.

    Three field groups: checkpoint-critical scalars (the resume/diff surface), the raw
    payload (per-candidate detail for replay, audit, full rendering), and the fields
    stamped as the round closes. `diagnostics`/`critique`/`health` are computed
    post-scoring; read by dispatch's `diagnostics`/`critique` signals and rendered by
    every health surface.

    Three fields ARE derivable yet deliberately stored, each for a different reason:

    * `candidates_scored` == the `candidate_scores` rows that were measured
      (`candidate_id in all_candidate_results`) and stayed leader-eligible — exact at all
      three construction sites. `model_config` here is `extra="ignore"`, so every caller
      still passing `candidates_scored=` would keep working **silently** while the value
      changed underneath: on a lax model, promoting a field to `@computed_field` is a
      quiet semantic change, not a mechanical one. One int does not buy that.
    * `degraded_samples` == `sum(has_pipeline_warnings(r) for r in self.results)`, and
      `shared/` is importable from here, so the derivation is cheap. It is kept because
      it would not be a pure refactor: round 0 never passes the field, so deriving it
      would start reporting a nonzero origin degradation count where the document has
      always carried 0. That is probably a FIX, and it belongs in a change that says so
      and is measured — not bundled into a simplification pass.
    * `deprecated` is derivable only via `is_deprecated`, which lives in
      `application/optimization/pobb/` — domain cannot import it. Structurally blocked.

    And one genuine duplication that MUST NOT be collapsed — the reason is worth keeping
    because the change looks obviously correct until you follow it. `results` is the
    winner's rows, which also ride `all_candidate_results[winner_id]`, so a winning round
    stores that payload twice; the tempting fix is to derive `results` from
    `all_candidate_results[winner_id]` and, for the no-winner case (where `results`
    carries the retained *incumbent's* rows and the incumbent is not a candidate), write
    the incumbent in under its own lineage id.

    That would silently corrupt the difficulty ruler. `exploration.build_observations`
    — the Rasch fit's input — flattens `all_candidate_results` across EVERY round into
    `(candidate_id, sample_id, response)` triples with no round filter and no dedup at
    that seam. A retained incumbent keeps its lineage id across consecutive no-winner
    rounds, so a lineage retained for k rounds would contribute each of its observations
    k+1 times to a fit whose entire purpose is subset-invariance. The rows in the
    no-winner case were also measured in a DIFFERENT round, so the key would assert a
    measurement that did not happen here. `results` stays: it is a round-local headline
    payload, and `all_candidate_results` means "measured in this round", which is a
    property several cross-round readers depend on without restating it.
    """

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
    # Subset-invariant peer of this round's own `accuracy`: ability of the cumulative frontier
    # on the cycle's fixed δ ruler. None when the ruler is cold. Feeds the L4 outer proxy so a
    # drifting per-round subset can't inflate the outer fitness signal.
    #
    # There is deliberately no `cumulative_accuracy` beside it. θ is an ability fitted against
    # an explicit per-sample δ, so pooling rows of mixed provenance is a modelled operation;
    # a plain mean over the same rows is not — it silently attributes one configuration's
    # score to another. See `cycle.py::_merge_known_outcomes`.
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
    # logit-accuracy — neither model, and the state a hardcoded "1PL" used to misreport.
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
    # ResumeCheckpoint records consumed by the divergence-replay walker.
    decisions: list[DecisionRecord] = Field(default_factory=list)
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
    # "generation_only" for a sweep round (L1 variants generated, never scored — every
    # scoring scalar below is a structural zero, not a measurement); "" for a scored round.
    status: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def round_id(self) -> str:
        return f"round_{self.round}"

    @property
    def l1_collapsed(self) -> dict[str, int]:
        """Candidates rejected before they could cost a backend call, keyed by reason.

        DERIVED from ``candidate_scores``, never stored: a collapsed candidate is not
        dropped, it rides ``candidate_scores`` with ``invalid=True`` and its
        ``validation_failures``, which is where ``invalid_reason`` and the display filter
        already read it from.

        One reason per candidate — a variant that trips several invariants collapsed once and
        must be counted once, or the parts would sum past the population.
        """
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
        from promptpotter.domain.rendering import round_winner_key

        ranked = sorted(
            self.candidate_scores,
            key=lambda c: round_winner_key(c.composite_fitness, c.accuracy),
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
        """The elected winner's lineage id, read off the id `l1/score/winner.py` stamped
        onto ``prompt_fields`` when it built this round (round 0 stamps the origin's own).
        Empty only when no candidate was crowned — and then no row is a winner, which is
        the honest answer rather than a prose match that happens to hit."""
        lineage = self.prompt_fields.get("lineage")
        return str(lineage.get("id", "")) if isinstance(lineage, dict) else ""


class CycleSpend(StrictModel):
    """Terminal token/USD totals for a finished cycle — the summed cost across
    the backend + optimizer-loop buckets.

    Read from the run's **in-memory** dashboard state at finalize, so a consumer
    (notably the L4 outer loop rolling an inner campaign's spend up onto its own
    backend-cost channel) gets the true total without racing the debounced
    ``dashboard.json`` projection file.

    Two costs, and picking the wrong one is a live bug class:

    - ``cost_usd`` / ``input_tokens`` / ``output_tokens`` — the BILL. Money that left
      the account. What rolls up onto an outer campaign's spend and what a budget caps.
    - ``incurred_usd`` — what this cycle would cost to run against a cold cache. The
      only honest divisor for a *measurement of the search*, because the caches are
      tenant-global: replay a cycle we already ran and the bill is $0 while the search
      did exactly the same work. On a cold cache the two are equal."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    # Tokens billed with no resolvable USD rate, summed across both buckets. >0 means
    # ``cost_usd`` UNDERSTATES real spend — it is a floor, not the total.
    unpriced_tokens: int = 0

    incurred_usd: float = 0.0
    # Incurred-side twin of ``unpriced_tokens``. >0 ⇒ ``incurred_usd`` understates what the
    # search costs, so a consumer that divides by it (the L4 efficiency proxy) would read
    # cheapness that never happened and score the run fitter for it. Such a cell is refused,
    # not scored.
    incurred_unpriced_tokens: int = 0


class CycleResult(StrictModel):
    """Final result of the feedback cycling process."""

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
    spend: CycleSpend | None = None
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


class RoundSummaryCandidate(StrictModel):
    """Display-summary row for `dashboard.json::rounds[].candidates` — chart/lineage/sparkline subset of `ScoredCandidate`."""

    model_config = ConfigDict(frozen=True)

    candidate_id: str
    label: str
    accuracy: float
    composite_fitness: float
    scored_samples: int
    expected_samples: int
    is_winner: bool
    evaluators: dict[str, float] = Field(default_factory=dict)
    changes_description: str = ""
    partial_reason: str = ""  # "" | "skip" — see ScoredCandidate.partial_reason
    # Difficulty-adjusted Rasch ability + SE (`ScoredCandidate.theta`) — the metric the winner
    # was elected on, so the chart can explain a lower-accuracy winner. `None` outside the fit.
    theta: float | None = None
    theta_se: float | None = None
    # Composite-fitness CI (`ScoredCandidate.composite_ci_lo/hi`) — always present for any
    # candidate with ≥1 scored sample; the whisker the chart draws around `composite_fitness`.
    composite_ci_lo: float | None = None
    composite_ci_hi: float | None = None
    # The floor this candidate was JUDGED against (`ScoredCandidate.matched_origin_*`): the
    # origin restricted to the samples this candidate actually measured. Served because
    # `accuracy` alone is unreadable under elimination — a PoBB-locked candidate ran 8 of 20,
    # so what it beat is NOT the origin's full-set rate, and the terminal has always printed
    # "was 42%" beside the verdict while the webapp printed the verdict alone. `None` outside
    # the election fit (eliminated / under the coverage floor) — nothing matched it.
    matched_origin_accuracy: float | None = None
    matched_origin_composite: float | None = None


class WarningDict(TypedDict):
    """One backend diagnostics warning as it rides ``round_file::results[].pipeline_data.diagnostics.warnings``.

    ``kind`` is **source-stamped by the backend** (TermNorm's ``WarningKind`` enum —
    ``structural`` = config/schema fault the operator must fix, ``transient`` =
    recoverable noise). PromptPotter reads it directly and keeps NO shadow code→kind
    taxonomy: an absent or unrecognized ``kind`` is SKIPPED, never guessed. A backend
    site that forgets to stamp under-counts (and surfaces in the live smoke), instead
    of silently grading a transient blip as an abort-worthy ``structural`` break."""

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


class DegradationHealth(StrictModel):
    """Context-aware degradation verdict for a round (origin included), computed
    PP-side at round close (``domain/results_health.py``) from the backend's
    per-sample warning stamps — the single graded signal every surface renders (R-36).

    ``grade`` distinguishes a structurally-broken pipeline (``critical``,
    abort-worthy) from transient backend noise (``degraded``, keep going) from a
    sound round (``healthy``). The SAME degradation grades differently by track
    record: structural failures at an untested config (``prior_clean_rounds == 0``)
    are abort-suspect, while the same isolated failure deep in a proven campaign is
    noise. The verdict NEVER stops the run — ``suggested_action`` (set only at
    ``critical``) is an operator-facing recommendation, surfaced read-only."""

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
    degraded_rate: float
    consecutive_degraded_rounds: int
    prior_clean_rounds: int
    dominant_node: str | None = None
    node_failure_rates: dict[str, float] = Field(default_factory=dict)
    # Verbatim upstream reasons per node ("[code] message"), harvested from the
    # connector's StepWarnings — the evidence behind the verdict, connector-agnostic.
    node_warnings: dict[str, list[str]] = Field(default_factory=dict)
    suggested_action: str | None = None


class RoundSummary(StrictModel):
    """Display row for `dashboard.json::rounds[]` — webapp's completed-round source.

    Top-level `accuracy`/`composite_fitness` are what the round MEASURED (the winner on
    its subset, or a held round's incumbent re-score); `cumulative_theta` is the
    subset-invariant cross-round series the trend/sparkline plot. In-flight round rides
    `current_round`.
    """

    model_config = ConfigDict(frozen=True)

    round: int
    accuracy: float
    composite_fitness: float
    # The cross-round-comparable series: ability on the cycle's fixed δ ruler, which is
    # subset-invariant. `accuracy`/`composite_fitness` above are subset-relative — under
    # `per_round_resubset` they swing round-to-round on a fresh 6–16 sample draw, which reads
    # as a false "great start → decay". The trend/sparkline plot THIS series so progress is
    # honest; the per-round measured number stays on `candidates[]` (badged with its count).
    #
    # It used to be a `cumulative_accuracy` beside it — a mean over a sample-keyed union of
    # rows from DIFFERENT configurations, which fixed the swing by fabricating a number no
    # individual scored. θ solves the same problem legitimately, by modelling per-sample
    # difficulty instead of averaging across provenance. Mirrors `RoundResult.cumulative_theta`.
    cumulative_theta: float | None = None
    # Mirrors `RoundResult.calibration_model` — the model the webapp's ability popover reads.
    calibration_model: CalibrationModel | None = None
    # The round's verdict and the evidence it rests on — the two bits that decide how long the
    # cycle lives, so they are the two an operator most needs to see. `improved` moves the stall
    # counter and the life bank; `electable_count` decides whether the bank moves AT ALL, since a
    # round no candidate reached measured nothing about the search (`EscalationFSM._bank_round`).
    # Both were engine-only, which is why a campaign could visibly climb while its bank drained
    # and the dashboard showed neither number. Round 0 holds no election, so both stay unset there.
    improved: bool | None = None
    electable_count: int | None = None
    candidates: list[RoundSummaryCandidate] = Field(default_factory=list)
    # Per-round selection from the adaptive queue mechanism — sample ids
    # in measurement order (longest candidate sequence carries the full
    # series since PoBB truncates losers, not the queue mechanism itself).
    selection: list[int] = Field(default_factory=list)
    # PP-computed (round-close) degradation verdict for this round (origin included).
    # ``None`` only when the round measured zero samples. Webapp/CLI render it;
    # never recompute (R-36).
    health: DegradationHealth | None = None
    # Blocked, paired L4 outer verdict: the target variant's pooled (variant − noop)
    # effect across the panel's cells + a 3-way decision. ``None`` on any non-L4 round
    # (no no-op probe to pair against). Webapp/CLI render it; never recompute.
    outer_verdict: OuterVerdict | None = None


class PayloadOutcome(StrictModel):
    """Per-payload row inside a ``SweepBatchResult``."""

    source_file: str
    # A ``StopOutcome`` value for attempted forks (success | paused | halted |
    # failed — the one StopReason classification, never a sweep-private
    # vocabulary), or the batch bookkeeping states skipped | skipped_already_forked.
    status: str
    cycle_id: str


class SweepBatchResult(StrictModel):
    """Final outcome of a sweep batch — all forks attempted, persistence finalized."""

    batch_id: str
    parent_cycle_id: str
    family_root: str
    started_at: str
    completed_at: str
    fork_cycle_ids: list[str]
    payload_outcomes: list[PayloadOutcome]
    interrupted: bool
