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
    "is_round_winner",
]


def candidate_label(round_num: int, idx: int) -> str:
    """Canonical candidate label — `C0` for origin, `C{N}.{idx+1}` otherwise. Sole writer."""
    if round_num == 0:
        return "C0"
    return f"C{round_num}.{idx + 1}"


def is_round_winner(changes_description: str | None, winner_label: str) -> bool:
    """The round winner is the candidate whose diff description equals the round's
    elected label. Sole definition of the rule — the round-file scoreboard
    (`_build_scoreboard`) and the dashboard round summary (`build_round_summary`)
    both call this so the operator-visible winner flag can't diverge between the
    two surfaces."""
    return bool(changes_description) and changes_description == winner_label


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
    # The candidate's evolved prompt (``OptSearchPoint.prompt_field_dict()`` shape).
    # Paired with ``pipeline_params_override`` this is the full searchpoint an
    # operator selects to seed an operator-steered fork (read side, Decision F).
    prompt_fields: dict[str, Any] = field(default_factory=dict)
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
    # Origin restricted to this candidate's measured samples — apples-to-apples
    # when PoBB locks mid-budget; equals full-set origin when fully scored.
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
            "prompt_fields": dict(self.prompt_fields),
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
    """LLM-proposed candidate — OSP + nested pipeline-params override, persisted across generate→score for resume replay."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    osp: OptSearchPoint
    pipeline_params_override: dict[str, dict[str, Any]] = Field(default_factory=dict)
    evidence_grounding: EvidenceGrounding | None = Field(
        default=None,
        description="Mirrors osp.lineage.evidence_grounding — duplicated so audit / display sites can read it before OSP construction.",
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
    """Checkpoint-critical scalars (no raw payloads); `RoundResult` inherits flat for wire-format compat."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    round: int
    label: str
    accuracy: float
    composite_fitness: float = 0.0
    hits: int
    total: int
    improved: bool
    # One-sided two-proportion p-value vs origin; drives the IMPROVED gate. None ⇒ no test ran.
    p_value: float | None = None
    # When `improved=False` despite Δ>0: which of {delta_threshold, significance, min_samples} blocked promotion.
    improved_reason: str | None = None
    degraded_samples: int = 0
    # Fatal-warning samples discarded from hits/total/accuracy on the winner's run.
    deprecated: int = 0
    escalation_signal: EscalationSignal | None = None
    # Origin restricted to the winner's measured samples — apples-to-apples when PoBB locks
    # at q8/20 while origin has 20. Drives `improved`, p_value, verdict Δ.
    matched_origin_accuracy: float = 0.0
    matched_origin_hits: int = 0
    matched_origin_composite: float = 0.0
    # Per-sample best-so-far across rounds; dashboard renders "current best on N measured" without walking priors.
    cumulative_total: int = 0
    cumulative_accuracy: float = 0.0


class RoundPayload(BaseModel):
    """Raw round payload — per-candidate detail for replay, audit, full rendering."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    prompt_fields: dict[str, Any]
    pipeline_params: dict[str, Any] | None = None
    # Prior round's accuracy (or campaign origin for round 0).
    origin_accuracy: float = 0.0
    results: list[dict[str, Any]] = Field(default_factory=list)
    # Per-candidate scored results — lets resume rescore under a changed scorer + replay decisions.
    all_candidate_results: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    candidates_scored: int
    candidate_scores: list[dict[str, Any]] = Field(default_factory=list)
    # ResumeCheckpoint records consumed by the divergence-replay walker.
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    evaluators: dict[str, float] = Field(default_factory=dict)
    # L1 yield + failure counts; defaults assume "all valid" for replay paths bypassing the detector.
    l1_yield: float = 1.0
    l1_n_no_op: int = 0
    l1_n_duplicate: int = 0


class RoundResult(RoundMetadata, RoundPayload):
    """Flat conjunction of `RoundMetadata` + `RoundPayload`, wire-compatible with `index.json`.

    `diagnostics` + `critique` computed post-scoring; read by dispatch's `diagnostics`/`critique` signals.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    diagnostics: RoundDiagnostics | None = None
    critique: dict[str, Any] | None = None


class CycleError(BaseModel):
    """Structured error carried on :class:`CycleResult` when the runner exited
    on ``CRASHED`` / ``RENDER_ERROR`` / ``DIVERGED``.

    Mirrors the trailing ``ErrorRecord`` on the canonical ledger; carried as a
    typed field so :class:`~promptpotter.application.jobs.registry.JobRegistry`
    and the post-run teardown read crash detail from one place without
    coupling to projection state. The runner's ``except`` sites build this
    instance and call ``emit_error_record`` from the same kwargs.
    """

    model_config = ConfigDict(frozen=True)

    kind: str
    message: str
    traceback: str | None = None


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
    # Set when ``stop_reason`` ∈ ``{CRASHED, RENDER_ERROR, DIVERGED}``;
    # ``None`` on clean completions. The runner's ``except`` sites populate
    # this in lockstep with the ``emit_error_record`` ledger append.
    error: CycleError | None = None


class DiagnosticRunRecord(BaseModel):
    """One on-demand candidate verification → webapp Verify tab.

    Per-sample data lands in `archive/measurements/`; this record carries the
    workspace-scope verdict (did the source-campaign composite hold).
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
    """Display-summary row for `dashboard.json::rounds[].candidates` — chart/lineage/sparkline subset of `CandidateScore`."""

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
    """Display row for `dashboard.json::rounds[]` — webapp's completed-round source.

    Top-level `accuracy`/`composite_fitness` mirror the winner so trend/sparkline
    don't scan `candidates`. In-flight round rides `current_round`.
    """

    model_config = ConfigDict(frozen=True)

    round: int
    accuracy: float
    composite_fitness: float
    candidates: list[RoundSummaryCandidate] = Field(default_factory=list)
    # Per-round selection from the adaptive queue mechanism — sample ids
    # in measurement order (longest candidate sequence carries the full
    # series since PoBB truncates losers, not the queue mechanism itself).
    selection: list[int] = Field(default_factory=list)


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
