"""Round and run outcome models — pure pydantic types, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from promptpotter.domain.escalation_signals import EscalationSignal
from promptpotter.domain.opt_search_point import EvidenceGrounding, OptSearchPoint
from promptpotter.domain.round_diagnostics import RoundDiagnostics

__all__ = [
    "CandidateProposal",
    "CandidateScore",
    "CycleResult",
    "PayloadOutcome",
    "RoundMetadata",
    "RoundOrigin",
    "RoundPayload",
    "RoundResult",
    "SweepBatchResult",
    "candidate_label",
]


def candidate_label(round_num: int, idx: int) -> str:
    """Canonical candidate display identity. Sole writer for ``CN.M`` labels.

    Round 0 is origin — collapses to ``"C0"`` (single candidate). L1 round
    N produces candidates ``"CN.1"``..``"CN.{idx+1}"``. Every display site
    (CLI, dashboard, webapp) reads the persisted ``CandidateScore.label`` /
    candidate-dict ``label`` field; this helper is the one place it gets
    composed.
    """
    if round_num == 0:
        return "C0"
    return f"C{round_num}.{idx + 1}"


@dataclass(frozen=True)
class CandidateScore:
    """One candidate's score report from L1 scoring.

    Stable shape, defaults always present. Built typed in ``score_population``
    and serialized to flat dicts via :meth:`to_dict` for ``RoundResult``,
    phase events, and archive persistence.

    ``label`` is the persisted display identity: ``"C0"`` for origin,
    ``f"C{round}.{n}"`` (n=1..N) for L1 candidates. Single source of truth
    at every render site — no display-side arithmetic.
    """

    candidate_id: str
    label: str
    changes_description: str
    accuracy: float
    composite_fitness: float
    hits: int
    total: int
    evaluators: dict[str, float]
    pipeline_params_override: dict | None = None
    escalation_aborted: bool = False
    elimination_stopped: bool = False
    scored_samples: int = 0
    expected_samples: int = 0
    invalid: bool = False
    resumed_from_cache: bool = False
    validation_failures: list[dict] = field(default_factory=list)
    runtime_failures: list[dict] = field(default_factory=list)
    elimination_context: dict = field(default_factory=dict)
    degradation_context: dict = field(default_factory=dict)
    # Origin's stats restricted to *this* candidate's measured samples — the
    # apples-to-apples comparison surface for PoBB-locked candidates that
    # only ran the hardest q8/20 before the lock fired. Equals the full-set
    # origin numbers when the candidate scored every sample. Populated by
    # ``l1_score`` after backfilling composite via ``matched_origin_stats``.
    matched_origin_accuracy: float = 0.0
    matched_origin_hits: int = 0
    matched_origin_composite: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Flat dict representation for wire format / JSON serialization."""
        from promptpotter.shared.statistics import wilson_ci

        ci_lo, ci_hi = wilson_ci(self.hits, self.total)
        return {
            "candidate_id": self.candidate_id,
            "label": self.label,
            "changes_description": self.changes_description,
            "pipeline_params_override": self.pipeline_params_override,
            "accuracy": self.accuracy,
            "composite_fitness": self.composite_fitness,
            "hits": self.hits,
            "total": self.total,
            "ci_lo": ci_lo,
            "ci_hi": ci_hi,
            "evaluators": dict(self.evaluators),
            "escalation_aborted": self.escalation_aborted,
            "elimination_stopped": self.elimination_stopped,
            "scored_samples": self.scored_samples,
            "expected_samples": self.expected_samples,
            "invalid": self.invalid,
            "resumed_from_cache": self.resumed_from_cache,
            "validation_failures": list(self.validation_failures),
            "runtime_failures": list(self.runtime_failures),
            "elimination_context": dict(self.elimination_context),
            "degradation_context": dict(self.degradation_context),
            "matched_origin_accuracy": self.matched_origin_accuracy,
            "matched_origin_hits": self.matched_origin_hits,
            "matched_origin_composite": self.matched_origin_composite,
        }


class CandidateProposal(BaseModel):
    """One LLM-proposed candidate: an OptSearchPoint plus its pipeline-params override.

    Persisted between generate and score so resume can replay a round without
    re-querying the LLM. The ``osp`` carries the prompt-field mutations and
    lineage (including ``evidence_grounding``); ``pipeline_params_override``
    is the nested LLM-proposed override (deep-merged into the base
    ``pipeline_params`` at score time).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    osp: OptSearchPoint
    pipeline_params_override: dict[str, dict] = Field(default_factory=dict)
    evidence_grounding: EvidenceGrounding | None = Field(
        default=None,
        description="L1-declared panel evidence for this proposal. Mirrors "
        "``osp.lineage.evidence_grounding`` — duplicated on the proposal so "
        "audit / display sites can read it before the OSP is constructed.",
    )


class RoundOrigin(BaseModel):
    """The challenger candidates compete against — built once per round.

    Carries the OSP directly (no dict→OSP roundtrip). On probe rounds the
    accuracy/composite_fitness/results reflect the origin's score *over the probe
    subset*, not the full prior dataset.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    accuracy: float
    composite_fitness: float
    osp: OptSearchPoint
    results: list[dict] = Field(default_factory=list)
    label: str
    evaluators: dict[str, float] = Field(default_factory=dict)


class RoundMetadata(BaseModel):
    """Checkpoint-critical scalars describing a round's outcome.

    These are the fields a termination/escalation/dashboard reader needs:
    what round, how good, did it improve, did it escalate. No raw payloads.
    `RoundResult` inherits these flat for wire-format compatibility; functions
    that only need the outcome can be typed against `RoundMetadata` directly.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    round: int
    label: str
    accuracy: float
    composite_fitness: float = 0.0
    hits: int
    total: int
    improved: bool
    # One-sided two-proportion p-value vs the round's origin. Now computed
    # unconditionally (whenever ``total > 0``) so the IMPROVED gate can
    # consult it — not just for display. ``None`` means no test could be
    # run (zero samples scored).
    p_value: float | None = None
    # Diagnostic string set when ``improved=False`` and the challenger had
    # a positive Δ vs origin — names which of {delta_threshold, significance,
    # min_samples} blocked promotion. ``None`` when the round was promoted
    # or had no positive Δ to begin with. Persisted so log.md / dashboard
    # can render the verdict's reason without re-deriving it.
    improved_reason: str | None = None
    degraded_samples: int = 0
    # Count of samples discarded as deprecated (fatal warnings) on the
    # round-winner's scoring run; excluded from hits/total/accuracy and
    # surfaced in round summary for operator transparency.
    deprecated: int = 0
    escalation_signal: EscalationSignal | None = None
    # Round-winner's apples-to-apples comparison anchor: origin's stats
    # restricted to the winner's measured samples. Drives the ``improved``
    # gate, the p-value, and the verdict-line delta — keeps the comparison
    # fair when PoBB leader-locks at q8/20 while origin has all 20. Equals
    # ``origin_accuracy`` / origin's full hits / ``origin_composite_fitness``
    # when the winner scored every sample.
    matched_origin_accuracy: float = 0.0
    matched_origin_hits: int = 0
    matched_origin_composite: float = 0.0
    # Cumulative pool snapshot after this round absorbed: per-sample best-so-
    # far across every round to date. Surfaces what ``tr.current_*`` now
    # tracks so dashboard / log.md don't have to re-walk priors to render
    # "Current best on N measured samples." Equals the round's own accuracy
    # / total when the round measured every sample; richer otherwise.
    cumulative_total: int = 0
    cumulative_accuracy: float = 0.0


class RoundPayload(BaseModel):
    """Raw round payload — what was tested and the per-candidate detail.

    These fields exist for replay (resume rescoring), audit (decisions log),
    and full-fidelity rendering. Persistence and the divergence replay walker
    consume them; lean readers do not.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    prompt_fields: dict
    pipeline_params: dict | None = None
    # Comparison anchor for this round: prior round's accuracy (or campaign
    # origin for round 0). Persisted so the scoreboard can render
    # delta-vs-origin without resolving the prior round_data.
    origin_accuracy: float = 0.0
    results: list[dict] = Field(default_factory=list)
    # Per-candidate scored results — persisted so resume can rescore
    # them under a changed scorer and replay decisions without needing
    # to re-run the pipeline. Keyed by candidate id.
    all_candidate_results: dict[str, list[dict]] = Field(default_factory=dict)
    candidates_scored: int
    candidate_scores: list[dict] = Field(default_factory=list)
    # ResumeCheckpointRecord records produced this round (round_winner, elimination_cut,
    # escalate_l2, …). Consumed by the divergence replay walker in
    # ``application/optimization/cycle.py``.
    decisions: list[dict] = Field(default_factory=list)
    evaluators: dict[str, float] = Field(default_factory=dict)
    # L1 generation quality — fraction of variants that proposed a non-empty
    # unique mutation, plus the per-failure-class counts. Defaults preserve
    # the "all valid" state for rounds where the detector is bypassed
    # (resume / cache replay). Surfaced as the ``l1_diversity`` evaluator
    # on each candidate so ``compile_scorer(formula)`` can weight it.
    l1_yield: float = 1.0
    l1_n_no_op: int = 0
    l1_n_duplicate: int = 0


class RoundResult(RoundMetadata, RoundPayload):
    """Result of a single feedback cycle round.

    Conjunction of `RoundMetadata` + `RoundPayload`. Fields are flat at
    the access level (`result.accuracy`, `result.results`) and serialize to a
    flat dict for wire-format compatibility with `index.json` / `trial_NNNN.json`.

    The post-scoring deterministics (rank distribution, trajectory, near
    misses, sample diagnostics) live on the optional ``diagnostics`` field
    as :class:`promptpotter.domain.round_diagnostics.RoundDiagnostics` —
    computed once after scoring + critique, read everywhere via the
    dispatch hub's ``diagnostics`` signal.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Filled in by ``execute_round`` after ``l1_score``; read by the
    # dispatch hub's ``diagnostics`` signal renderer (layer-agnostic).
    diagnostics: RoundDiagnostics | None = None
    # Filled in by ``execute_round`` after ``run_l1_critique`` returns;
    # read by the dispatch hub's ``critique`` signal renderer.
    critique: dict | None = None


class CycleResult(BaseModel):
    """Final result of the feedback cycling process."""

    rounds: list[RoundResult]
    n_rounds: int
    best_accuracy: float
    best_round: int
    origin_accuracy: float
    winner_prompt_fields: dict
    winner_pipeline_params: dict | None = None
    stop_reason: str
    started_at: str
    finished_at: str
    langfuse_trace_id: str | None = None
    cycle_id: str | None = None
    session_id: str | None = None
    resumed_from_round: int = 1


class PayloadOutcome(BaseModel):
    """Per-payload row inside a ``SweepBatchResult``."""

    source_file: str
    status: str  # completed | interrupted | skipped | skipped_already_forked
    cycle_id: str


class SweepBatchResult(BaseModel):
    """Final outcome of a sweep batch — all forks attempted, persistence finalized."""

    batch_id: str
    parent_cycle_id: str
    family_root: str
    started_at: str
    completed_at: str
    fork_cycle_ids: list[str]
    payload_outcomes: list[PayloadOutcome]
    interrupted: bool
