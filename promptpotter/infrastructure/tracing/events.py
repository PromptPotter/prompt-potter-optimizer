"""Two families: ``OptimizationEvent`` (one trace per campaign, emitted inline by the loop) and ``MeasurementEvent`` (one trace per query,
emitted by the backfill replayer). Each event is self-contained; sinks own the id mappings."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Union


def generate_observation_id() -> str:
    prefix = datetime.now(UTC).strftime("%y%m%d%H%M%S")
    suffix = uuid.uuid4().hex[: 32 - len(prefix)]
    return f"{prefix}{suffix}"


def dataset_item_id(dataset_name: str, query: str) -> str:
    """Content-addressed Langfuse item id for a ``(dataset, query)`` pair. It MUST be byte-identical across the file sink
    and the cloud bridge, or the two register mismatched ids for one pair — hence it lives here, not at each sink."""
    return hashlib.sha256(f"{dataset_name}:{query}".encode()).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class DatasetRegistered:
    """Dataset items registered in the file store and, optionally, Langfuse. The cloud-side query → item id mapping is
    shared between both topologies."""

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
    """Open a node observation under the active round. Producers pass identity; sinks resolve trace + parent by id."""

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


# An event declared here must have a REMOTE sink. Five mid-round ones (candidate created /
# scored, round winner, L1 critique, layer applied) reached only the file mirror, restating a
# fact the ledger and ``rounds/round_NNNN.json`` already carried — so the writer paid the
# tracing tax for a second copy nothing read. The ledger is where a mid-round fact lands.


@dataclass(frozen=True, slots=True)
class PromptVersion:
    """``lineage_id`` is ``OptSearchPoint.lineage.id`` — a per-individual ``uuid4``, and NOT the
    ``prompt_fields_id`` the archive stores under that name (which is ``sp_hash``). Two events on
    this page carry each; joining a trace to an archive row on the wrong one matches nothing and
    raises nothing. The ladder is `docs/developer/README.md` § Cross-run memory."""

    campaign_id: str
    round_num: int
    lineage_id: str
    rendered_prompt: str
    layer1_fields: dict[str, Any]
    parent_id: str | None = None


@dataclass(frozen=True, slots=True)
class DatasetRun:
    """Target-layer scoring report, nested under the optimizer round span — a layer-boundary event with target data and an
    optimizer-layer parent, so the campaign trace shows which scoring run each round used."""

    campaign_id: str
    round_num: int
    run_id: str
    content_hash: str
    prompt_fields_id: str
    """``sp_hash`` — the archive's own spelling, and the one id that joins a trace to a row."""
    accuracy: float
    total: int


@dataclass(frozen=True, slots=True)
class RoundEnd:
    campaign_id: str
    round_num: int
    accuracy: float
    total: int
    improved: bool
    winner_lineage_id: str
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
]


# --- Measurement (Topology B, replayed from measurements/) ---


@dataclass(frozen=True, slots=True)
class QueryNodeSpan:
    """One pipeline node's I/O during a measurement. ``as_type`` is the pipeline schema's declared type for that node."""

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
    "DatasetRegistered",
    "DatasetRun",
    "Event",
    "NodeEnd",
    "NodeStart",
    "PromptVersion",
    "QueryNodeSpan",
    "QueryScoreEnd",
    "QueryScoreStart",
    "RoundEnd",
    "RoundStart",
    "dataset_item_id",
    "generate_observation_id",
]
