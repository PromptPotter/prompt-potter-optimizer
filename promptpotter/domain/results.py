"""Round and run outcome models — pure pydantic types, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from promptpotter.domain.analysis import EscalationSignal
from promptpotter.domain.opt_search_point import OptSearchPoint

__all__ = [
    "CandidateProposal",
    "CandidateScore",
    "RoundBaseline",
    "RoundDiagnostics",
    "RoundMetadata",
    "RoundResult",
    "RunResult",
]


@dataclass(frozen=True)
class CandidateScore:
    """One candidate's score report from L1 scoring.

    Stable shape, defaults always present. Lives in ``L1ScoringResult.candidate_scores``
    (typed) and on phase events / RoundResult / library archives as a flat
    dict via :meth:`to_dict` (wire format).
    """

    candidate_id: str
    changes_description: str
    accuracy: float
    composite: float
    hits: int
    total: int
    evaluators: dict[str, float]
    pipeline_params_override: dict | None = None
    escalation_aborted: bool = False
    elimination_stopped: bool = False
    scored_queries: int = 0
    expected_queries: int = 0
    invalid: bool = False
    resumed_from_cache: bool = False
    validation_failures: list[dict] = field(default_factory=list)
    runtime_failures: list[dict] = field(default_factory=list)
    elimination_context: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Flat dict representation for wire format / JSON serialization."""
        return {
            "candidate_id": self.candidate_id,
            "changes_description": self.changes_description,
            "pipeline_params_override": self.pipeline_params_override,
            "accuracy": self.accuracy,
            "composite": self.composite,
            "hits": self.hits,
            "total": self.total,
            "evaluators": dict(self.evaluators),
            "escalation_aborted": self.escalation_aborted,
            "elimination_stopped": self.elimination_stopped,
            "scored_queries": self.scored_queries,
            "expected_queries": self.expected_queries,
            "invalid": self.invalid,
            "resumed_from_cache": self.resumed_from_cache,
            "validation_failures": list(self.validation_failures),
            "runtime_failures": list(self.runtime_failures),
            "elimination_context": dict(self.elimination_context),
        }


class CandidateProposal(BaseModel):
    """One LLM-proposed individual: an OptSearchPoint plus its node-param overrides.

    Persisted between generate and score so resume can replay a round without
    re-querying the LLM. The ``osp`` carries the prompt-field mutations and
    lineage; ``node_overrides`` carries the LLM-proposed changes to non-prompt
    pipeline params (deep-merged into the base ``pipeline_params`` at score time).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    osp: OptSearchPoint
    node_overrides: dict[str, dict] = Field(default_factory=dict)


class RoundBaseline(BaseModel):
    """The challenger candidates compete against — built once per round.

    Carries the OSP directly (no dict→OSP roundtrip). On probe rounds the
    accuracy/composite/results reflect the baseline's score *over the probe
    subset*, not the full prior dataset.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    accuracy: float
    composite: float
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
    composite: float = 0.0
    hits: int
    total: int
    improved: bool
    degraded_queries: int = 0
    # Count of samples discarded as deprecated (fatal warnings) on the
    # round-winner's scoring run; excluded from hits/total/accuracy and
    # surfaced in round summary for operator transparency.
    deprecated: int = 0
    escalation_signal: EscalationSignal | None = None


class RoundDiagnostics(BaseModel):
    """Raw round payload — what was tested and the per-candidate detail.

    These fields exist for replay (resume rescoring), audit (decisions log),
    and full-fidelity rendering. Persistence and the divergence replay walker
    consume them; lean readers do not.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    prompt_fields: dict
    pipeline_params: dict | None = None
    results: list[dict] = Field(default_factory=list)
    # Per-candidate scored results — persisted so resume can rescore
    # them under a changed scorer and replay decisions without needing
    # to re-run the pipeline. Keyed by candidate id.
    all_candidate_results: dict[str, list[dict]] = Field(default_factory=dict)
    candidates_scored: int
    candidate_scores: list[dict] = Field(default_factory=list)
    # Decision records produced this round (round_winner, elimination_cut,
    # escalate_l2, …). Consumed by the divergence replay walker in
    # ``application/campaign/decisions.py``.
    decisions: list[dict] = Field(default_factory=list)
    evaluators: dict[str, float] = Field(default_factory=dict)
    # Scoring-set evolution events emitted during this round (only populated
    # when ``scoring_set.enabled``; empty otherwise). Persisted to the
    # trial JSON so post-hoc renderers can walk the trial list and
    # reconstruct the full event log.
    scoring_set_events: list[dict] = Field(default_factory=list)
    # L1 generation quality — fraction of variants that proposed a non-empty
    # unique mutation, plus the per-failure-class counts. Defaults preserve
    # the "all valid" state for rounds where the detector is bypassed
    # (resume / cache replay). Surfaced as the ``l1_diversity`` evaluator
    # on each candidate so ``compile_scorer(formula)`` can weight it.
    l1_yield: float = 1.0
    l1_n_no_op: int = 0
    l1_n_duplicate: int = 0


class RoundResult(RoundMetadata, RoundDiagnostics):
    """Result of a single feedback cycle round.

    Conjunction of `RoundMetadata` + `RoundDiagnostics`. Fields are flat at
    the access level (`result.accuracy`, `result.results`) and serialize to a
    flat dict for wire-format compatibility with `index.json` / `trial_NNNN.json`.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)


class RunResult(BaseModel):
    """Final result of the feedback cycling process."""

    rounds: list[RoundResult]
    n_rounds: int
    best_accuracy: float
    best_round: int
    baseline_accuracy: float
    winner_prompt_fields: dict
    winner_pipeline_params: dict | None = None
    stop_reason: str
    started_at: str
    finished_at: str
    langfuse_trace_id: str | None = None
    cycle_id: str | None = None
    session_id: str | None = None
    resumed_from_round: int = 0
