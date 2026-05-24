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
    "DiagnosticRunRecord",
    "OriginSummary",
    "PayloadOutcome",
    "RoundMetadata",
    "RoundOrigin",
    "RoundPayload",
    "RoundResult",
    "RoundSummary",
    "RoundSummaryCandidate",
    "SweepBatchResult",
    "candidate_label",
]


def candidate_label(round_num: int, idx: int) -> str:
    """Canonical candidate label — `C0` for origin, `C{N}.{idx+1}` otherwise. Sole writer."""
    if round_num == 0:
        return "C0"
    return f"C{round_num}.{idx + 1}"


@dataclass(frozen=True)
class CandidateScore:
    """One candidate's L1 score report. Typed in `score_population`, flattened via `to_dict`."""

    candidate_id: str
    label: str
    changes_description: str
    accuracy: float
    composite_fitness: float
    hits: int
    total: int
    evaluators: dict[str, float]
    pipeline_params_override: dict[str, Any] | None = None
    escalation_aborted: bool = False
    elimination_stopped: bool = False
    scored_samples: int = 0
    expected_samples: int = 0
    invalid: bool = False
    resumed_from_cache: bool = False
    validation_failures: list[dict[str, Any]] = field(default_factory=list)
    runtime_failures: list[dict[str, Any]] = field(default_factory=list)
    elimination_context: dict[str, Any] = field(default_factory=dict)
    degradation_context: dict[str, Any] = field(default_factory=dict)
    # Origin stats restricted to this candidate's measured samples — apples-to-apples for
    # PoBB-locked candidates that stopped mid-budget. Equals full-set origin when fully scored.
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
    """LLM-proposed candidate — OSP + nested pipeline-params override, persisted across generate→score
    so resume can replay without re-querying the LLM. Override deep-merges onto base at score time.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    osp: OptSearchPoint
    pipeline_params_override: dict[str, dict[str, Any]] = Field(default_factory=dict)
    evidence_grounding: EvidenceGrounding | None = Field(
        default=None,
        description="L1-declared panel evidence for this proposal. Mirrors "
        "``osp.lineage.evidence_grounding`` — duplicated on the proposal so "
        "audit / display sites can read it before the OSP is constructed.",
    )


class RoundOrigin(BaseModel):
    """The round's challenger anchor. On probe rounds, scalars reflect the probe subset, not the full set."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    accuracy: float
    composite_fitness: float
    osp: OptSearchPoint
    results: list[dict[str, Any]] = Field(default_factory=list)
    label: str
    evaluators: dict[str, float] = Field(default_factory=dict)


class RoundMetadata(BaseModel):
    """Checkpoint-critical scalars (no raw payloads) — what a termination/escalation/dashboard
    reader needs. `RoundResult` inherits these flat for wire-format compatibility.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    round: int
    label: str
    accuracy: float
    composite_fitness: float = 0.0
    hits: int
    total: int
    improved: bool
    # One-sided two-proportion p-value vs origin; consumed by the IMPROVED gate (not just display).
    # None means no test could run.
    p_value: float | None = None
    # When `improved=False` despite Δ>0: names which of {delta_threshold, significance, min_samples}
    # blocked promotion. Persisted so log.md/dashboard render the verdict reason without re-deriving.
    improved_reason: str | None = None
    degraded_samples: int = 0
    # Fatal-warning samples discarded from hits/total/accuracy on the winner's run.
    deprecated: int = 0
    escalation_signal: EscalationSignal | None = None
    # Origin stats restricted to the winner's measured samples — keeps the comparison fair when
    # PoBB leader-locks at q8/20 while origin has 20. Drives `improved`, p_value, and verdict Δ.
    matched_origin_accuracy: float = 0.0
    matched_origin_hits: int = 0
    matched_origin_composite: float = 0.0
    # Per-sample best-so-far across all rounds, snapshotted after absorbing this round —
    # so dashboard/log.md render "current best on N measured" without walking priors.
    cumulative_total: int = 0
    cumulative_accuracy: float = 0.0


class RoundPayload(BaseModel):
    """Raw round payload — per-candidate detail for replay (resume rescoring), audit, full rendering."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    prompt_fields: dict[str, Any]
    pipeline_params: dict[str, Any] | None = None
    # Comparison anchor: prior round's accuracy (or campaign origin for round 0).
    origin_accuracy: float = 0.0
    results: list[dict[str, Any]] = Field(default_factory=list)
    # Per-candidate scored results, keyed by candidate id — so resume can rescore under a changed
    # scorer and replay decisions without re-running the pipeline.
    all_candidate_results: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    candidates_scored: int
    candidate_scores: list[dict[str, Any]] = Field(default_factory=list)
    # ResumeCheckpoint records produced this round (round_winner, elimination_cut, …) —
    # consumed by the divergence replay walker in `optimization/cycle.py`.
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    evaluators: dict[str, float] = Field(default_factory=dict)
    # L1 generation quality — yield fraction + per-class failure counts. Defaults assume "all valid"
    # for replay paths that bypass the detector. Surfaced as the `l1_diversity` evaluator.
    l1_yield: float = 1.0
    l1_n_no_op: int = 0
    l1_n_duplicate: int = 0


class RoundResult(RoundMetadata, RoundPayload):
    """Round outcome — flat conjunction of `RoundMetadata` + `RoundPayload`, wire-compatible with `index.json`.

    `diagnostics` (rank dist / trajectory / near-misses) + `critique` are computed post-scoring and
    read by the dispatch hub's `diagnostics` / `critique` signals.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    diagnostics: RoundDiagnostics | None = None
    critique: dict[str, Any] | None = None


class CycleResult(BaseModel):
    """Final result of the feedback cycling process."""

    rounds: list[RoundResult]
    n_rounds: int
    best_accuracy: float
    best_round: int
    origin_accuracy: float
    winner_prompt_fields: dict[str, Any]
    winner_pipeline_params: dict[str, Any] | None = None
    stop_reason: str
    started_at: str
    finished_at: str
    langfuse_trace_id: str | None = None
    cycle_id: str | None = None
    session_id: str | None = None
    resumed_from_round: int = 1


class DiagnosticRunRecord(BaseModel):
    """One on-demand verification of a campaign candidate vs more samples — written by `cmd_verify`,
    consumed by the webapp's Verify tab. Per-sample data lands in `archive/measurements/`; this
    record carries the workspace-scope verdict (did the source-campaign composite hold).
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


class RoundSummaryCandidate(BaseModel):
    """Display-summary row for `dashboard.json::rounds[].candidates` — strict subset of `CandidateScore`,
    only what the chart/lineage/sparkline render. Deep audit stays in `round_NNNN.json`.
    """

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


class RoundSummary(BaseModel):
    """Display row for `dashboard.json::rounds[]` — webapp's sole source for completed-round bars
    (in-flight round rides `current_round`). Top-level `accuracy`/`composite_fitness` mirror the
    winner so trend/sparkline don't have to scan `candidates`.
    """

    model_config = ConfigDict(frozen=True)

    round: int
    accuracy: float
    composite_fitness: float
    candidates: list[RoundSummaryCandidate] = Field(default_factory=list)


class OriginSummary(BaseModel):
    """Origin row for `dashboard.json::origin` — separate from `rounds[]` since origin has no candidates/fitness."""

    model_config = ConfigDict(frozen=True)

    accuracy: float
    samples: int


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
