"""Event schema for the observability bridge.

Two families:
- :class:`OptimizationEvent` — Topology A: one Langfuse trace per campaign,
  rounds + nodes nest underneath. Emitted inline by the loop.
- :class:`MeasurementEvent` — Topology B: one trace per query, linked to
  dataset items. Emitted by the backfill replayer over ``measurements/``.

Each event is self-contained; sinks own the id mappings.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Union


def generate_observation_id() -> str:
    prefix = datetime.now(UTC).strftime("%y%m%d%H%M%S")
    suffix = uuid.uuid4().hex[: 32 - len(prefix)]
    return f"{prefix}{suffix}"


def dataset_item_id(dataset_name: str, query: str) -> str:
    """Content-addressed Langfuse item id for a ``(dataset, query)`` pair.

    The file sink and the cloud bridge both key items by this id; it MUST be
    byte-identical across them or the two register mismatched ids for the same
    pair — so it lives here, not duplicated at each sink.
    """
    return hashlib.sha256(f"{dataset_name}:{query}".encode()).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class DatasetRegistered:
    """Dataset items registered in file store and (optionally) Langfuse.

    The cloud-side ``query → item_id`` mapping is shared between both topologies.
    """

    dataset_name: str
    items: tuple[tuple[str, str], ...]
    """Frozen ``(query, ground_truth)`` pairs in registration order."""


# --- Optimization (Topology A) ---


@dataclass(frozen=True, slots=True)
class CampaignStart:
    campaign_id: str
    config: dict[str, Any]
    origin_accuracy: float
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class RoundStart:
    campaign_id: str
    round_num: int


@dataclass(frozen=True, slots=True)
class NodeStart:
    """Open a node observation under the active round.

    Producers pass identity; sinks resolve trace + parent observation by id.
    """

    campaign_id: str
    round_num: int
    node_id: str
    node_type: str
    as_type: str
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


# --- Mid-round write-point events ---
# events.jsonl is an observability mirror; resume + fork read
# ``campaigns/{cycle_id}/rounds/round_NNNN.json`` via ``CampaignStore``, not this.


@dataclass(frozen=True, slots=True)
class CandidateCreated:
    """An L1-proposed candidate was registered for this round."""

    campaign_id: str
    round_num: int
    candidate_idx: int
    candidate_id: str


@dataclass(frozen=True, slots=True)
class CandidateScored:
    """A candidate finished its full scoring loop (report built)."""

    campaign_id: str
    round_num: int
    candidate_idx: int
    report: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RoundWinnerChosen:
    """Round winner picked from scored candidates."""

    campaign_id: str
    round_num: int
    winner_candidate_id: str
    winner_accuracy: float
    improved: bool


@dataclass(frozen=True, slots=True)
class L1CritiqueWritten:
    """L1 critique text produced after scoring (inline in ``_score_and_select``)."""

    campaign_id: str
    round_num: int
    l1_critique_text: str


@dataclass(frozen=True, slots=True)
class LayerApplied:
    """L2 (``refine_strategy``) or L3 (``modify_plan``) transition applied to the loop state."""

    layer: Literal["L2", "L3"]
    campaign_id: str
    round_num: int
    changes_description: str


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
    """Target-layer scoring report (one SearchPoint), nested under the optimizer round span.

    Layer-boundary event: target data, optimizer-layer parent — Topology A
    entangles them so the campaign trace shows which scoring run each round used.
    """

    campaign_id: str
    round_num: int
    run_id: str
    content_hash: str
    prompt_fields_id: str
    accuracy: float
    total: int


@dataclass(frozen=True, slots=True)
class RoundEnd:
    campaign_id: str
    round_num: int
    accuracy: float
    total: int
    improved: bool
    winner_prompt_fields_id: str
    candidate_scores: list[dict[str, Any]]
    next_action: str = ""
    model: str = ""
    n_variants: int = 0
    optimizer_templates: list[str] | None = None
    evaluators: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CampaignEnd:
    campaign_id: str
    best_accuracy: float
    # Completed L1 rounds, origin-EXCLUSIVE (mirrors CycleResult.n_l1_rounds).
    n_l1_rounds: int
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
    CandidateScored,
    RoundWinnerChosen,
    L1CritiqueWritten,
    LayerApplied,
]


# --- Measurement (Topology B, replayed from measurements/) ---


@dataclass(frozen=True, slots=True)
class QueryNodeSpan:
    """One pipeline node's I/O during a measurement; child of a ``QueryScore*`` pair.

    ``as_type`` is the pipeline schema's ``langfuse_type`` for the node.
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
class QueryScoreStart:
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
class QueryScoreEnd:
    run_id: str
    query: str
    predicted: str
    ground_truth: str
    hit: bool
    total_time: float | None
    node_outputs: dict[str, Any]
    """Flat map of pipeline node output keys → values (for the trace output blob)."""


MeasurementEvent = Union[
    QueryScoreStart,
    QueryNodeSpan,
    QueryScoreEnd,
]


Event = Union[DatasetRegistered, OptimizationEvent, MeasurementEvent]
"""Per-cycle Langfuse shadow + events.jsonl + prompts under campaigns/{cycle_id}/langfuse/."""


__all__ = [
    "CampaignEnd",
    "CampaignStart",
    "CandidateCreated",
    "CandidateScored",
    "DatasetRegistered",
    "DatasetRun",
    "Event",
    "L1CritiqueWritten",
    "LayerApplied",
    "MeasurementEvent",
    "NodeEnd",
    "NodeStart",
    "OptimizationEvent",
    "PromptVersion",
    "QueryNodeSpan",
    "QueryScoreEnd",
    "QueryScoreStart",
    "RoundEnd",
    "RoundStart",
    "RoundWinnerChosen",
    "dataset_item_id",
    "generate_observation_id",
]
