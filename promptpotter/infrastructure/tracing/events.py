"""Event schema for the observability bridge.

Single source of truth for every observable thing the optimizer or the
backfill replayer can emit. Every public tracing method maps to exactly
one event dataclass; the bridge fans events out to sinks (file, Langfuse)
and each sink handles them by type. No implicit "active trace" state lives
on the producer side — every event is self-contained and carries the keys
the sink needs to look up its own id mappings.

Two families:

- :class:`OptimizationEvent` — Topology A (live campaign trace). One Langfuse
  trace per campaign; rounds and nodes nest underneath. Emitted inline from
  the optimization loop.
- :class:`EvaluationEvent` — Topology B (per-query dataset trace). One
  Langfuse trace per query, linked to dataset items. Emitted by the backfill
  replayer reading ``dataset_runs/`` from disk.

The audit table at ``docs/architecture/observability-audit.md`` documents
exactly which Langfuse SDK calls each event triggers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union


@dataclass(frozen=True, slots=True)
class DatasetRegistered:
    """Dataset items registered in file store and (optionally) Langfuse.

    Used by both topologies — the cloud-side ``query → item_id`` mapping
    produced here is shared between the live forward path (which links
    DatasetRun spans) and the backfill replayer (which links per-query
    traces).
    """

    dataset_name: str
    items: tuple[tuple[str, str], ...]
    """Frozen ``(query, ground_truth)`` pairs in registration order."""


# --- Optimization (Topology A) ---


@dataclass(frozen=True, slots=True)
class CampaignStart:
    campaign_id: str
    config: dict[str, Any]
    baseline_accuracy: float
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class RoundStart:
    campaign_id: str
    round_num: int


@dataclass(frozen=True, slots=True)
class NodeStart:
    """Open a node observation under the active round.

    The sink looks up the campaign's trace id by ``campaign_id`` and the
    round's parent observation id by ``(campaign_id, round_num)``. Producers
    do not pass either directly — they pass identity, sinks own state.
    """

    campaign_id: str
    round_num: int
    node_id: str
    node_type: str
    obs_type: str
    input_data: dict[str, Any]
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class NodeEnd:
    campaign_id: str
    round_num: int
    node_id: str
    output_data: dict[str, Any] | None = None
    metrics: dict[str, float] | None = None
    error: str | None = None
    state_snapshot: dict[str, Any] | None = None
    """Optional OptSearchPoint snapshot captured at node boundary for fork support.

    When present, this event becomes a fork-addressable write point — a new
    cycle can be seeded from this exact OptSearchPoint via ``--fork-from``.
    See ``docs/architecture/optimization.md`` "Forking a campaign".
    """


# --- Fork-addressable mid-round write points ---
#
# These events carry a self-contained ``state_snapshot`` (dumped
# ``OptSearchPoint``) so any new cycle can be seeded from the exact optimizer
# state at that point in the timeline. See ``docs/architecture/optimization.md``
# "Forking a campaign" and ``application/campaign/fork_loader.py``.


@dataclass(frozen=True, slots=True)
class CandidateCreated:
    """An L1-proposed candidate was registered for this round."""

    campaign_id: str
    round_num: int
    candidate_idx: int
    candidate_id: str
    state_snapshot: dict[str, Any]
    candidate_snapshot: dict[str, Any] | None = None
    """Dump of the candidate's ``OptSearchPoint`` (distinct from ``state_snapshot``
    which is the parent state) so forks can reconstruct the precise candidate."""


@dataclass(frozen=True, slots=True)
class QueryScored:
    """One backend query finished for a specific candidate."""

    campaign_id: str
    round_num: int
    candidate_idx: int
    query_idx: int
    hit: bool
    score: float
    state_snapshot: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CandidateScored:
    """A candidate finished its full scoring loop (report built)."""

    campaign_id: str
    round_num: int
    candidate_idx: int
    report: dict[str, Any]
    state_snapshot: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RoundWinnerChosen:
    """Round winner picked from evaluated candidates."""

    campaign_id: str
    round_num: int
    winner_candidate_id: str
    winner_accuracy: float
    improved: bool
    state_snapshot: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CritiqueWritten:
    """Critique text produced after scoring (inline in ``_score_and_select``)."""

    campaign_id: str
    round_num: int
    critique_text: str
    state_snapshot: dict[str, Any]


@dataclass(frozen=True, slots=True)
class L2Applied:
    """L2 ``refine_strategy`` transition applied to the loop state."""

    campaign_id: str
    round_num: int
    changes_description: str
    state_snapshot: dict[str, Any]


@dataclass(frozen=True, slots=True)
class L3Applied:
    """L3 ``modify_plan`` transition applied to the loop state."""

    campaign_id: str
    round_num: int
    changes_description: str
    state_snapshot: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PromptVersion:
    """A new optimizer prompt was materialized for the active round."""

    campaign_id: str
    round_num: int
    prompt_fields_id: str
    rendered_prompt: str
    layer1_fields: dict[str, Any]
    parent_id: str | None = None


@dataclass(frozen=True, slots=True)
class DatasetRun:
    """Target-layer scoring report for a single SearchPoint, nested under the active round.

    This event sits at the layer boundary: the data is target-layer
    (``JobSearchPoint`` evaluation) but the Langfuse parent is the
    optimizer-layer round span. Topology A intentionally entangles them so
    a campaign trace shows which scoring run each round depended on.
    """

    campaign_id: str
    round_num: int
    run_id: str
    content_hash: str
    prompt_fields_id: str
    accuracy: float
    hits: int
    total: int


@dataclass(frozen=True, slots=True)
class RoundEnd:
    campaign_id: str
    round_num: int
    accuracy: float
    hits: int
    total: int
    improved: bool
    winner_prompt_fields_id: str
    candidate_scores: list[dict[str, Any]]
    next_action: str = ""
    model: str = ""
    temperature: float = 0.0
    n_variants: int = 0
    optimizer_templates: list[str] | None = None


@dataclass(frozen=True, slots=True)
class CampaignEnd:
    campaign_id: str
    best_accuracy: float
    n_rounds: int
    stop_reason: str
    best_round: int


OptimizationEvent = Union[
    CampaignStart,
    RoundStart,
    NodeStart,
    NodeEnd,
    PromptVersion,
    DatasetRun,
    RoundEnd,
    CampaignEnd,
    CandidateCreated,
    QueryScored,
    CandidateScored,
    RoundWinnerChosen,
    CritiqueWritten,
    L2Applied,
    L3Applied,
]


# --- Evaluation (Topology B, replayed from dataset_runs/) ---


@dataclass(frozen=True, slots=True)
class QueryNodeSpan:
    """One pipeline node's input/output captured during a target-layer eval.

    Emitted as a child of a ``QueryEvalStart``/``QueryEvalEnd`` pair. The
    node's Langfuse ``as_type`` (generation/span/tool/...) is taken from
    the pipeline schema's ``langfuse_type`` for that node.
    """

    run_id: str
    query: str
    node_name: str
    as_type: str
    input_data: dict[str, Any]
    output_data: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    usage_details: dict[str, int] | None = None


@dataclass(frozen=True, slots=True)
class QueryEvalStart:
    run_id: str
    query: str
    ground_truth: str
    origin: str
    llm_provider: str
    prompt_fields_id: str
    pipeline_params: dict[str, Any] | None
    schema_name: str
    session_id: str
    dataset_name: str


@dataclass(frozen=True, slots=True)
class QueryEvalEnd:
    run_id: str
    query: str
    predicted: str
    ground_truth: str
    hit: bool
    total_time: float | None
    node_outputs: dict[str, Any]
    """Flat map of pipeline node output keys → values (for the trace output blob)."""


EvaluationEvent = Union[
    QueryEvalStart,
    QueryNodeSpan,
    QueryEvalEnd,
]


Event = Union[DatasetRegistered, OptimizationEvent, EvaluationEvent]
