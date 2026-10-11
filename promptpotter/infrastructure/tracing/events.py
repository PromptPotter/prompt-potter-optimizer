from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Union

from promptpotter.shared.hashing import stable_hash

if TYPE_CHECKING:
    from promptpotter.domain.run_records import (
        CandidateScoredRecord,
        RoundClosedRecord,
        RoundEnteredRecord,
        RunPhaseRecord,
    )


def generate_observation_id() -> str:
    prefix = datetime.now(UTC).strftime("%y%m%d%H%M%S")
    suffix = uuid.uuid4().hex[: 32 - len(prefix)]
    return f"{prefix}{suffix}"


def dataset_item_id(dataset_name: str, query: str) -> str:
    """MUST be byte-identical across the file sink and the cloud bridge, hence it lives here."""
    return stable_hash([dataset_name, query])


@dataclass(frozen=True, slots=True)
class DatasetRegistered:
    dataset_name: str
    items: tuple[tuple[str, str], ...]
    """Queries non-empty and distinct: ``TracingProjection._register_dataset`` settles it once."""


@dataclass(frozen=True, slots=True)
class CampaignStart:
    campaign_id: str
    config: dict[str, Any]
    cycle_id: str
    session_id: str | None


@dataclass(frozen=True, slots=True)
class QueryNodeSpan:
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


MeasurementEvent = Union[
    QueryScoreStart,
    QueryNodeSpan,
    QueryScoreEnd,
]


class TraceSink:
    def on_dataset_registered(self, event: DatasetRegistered) -> None: ...
    def on_campaign_start(self, event: CampaignStart) -> None: ...
    def on_round_entered(self, campaign_id: str, record: RoundEnteredRecord) -> None: ...

    def on_span_open(
        self,
        campaign_id: str,
        round_num: int | None,
        span_id: str,
        *,
        name: str,
        node_type: str,
        as_type: str,
        metadata: dict[str, Any],
    ) -> None: ...

    def on_span_close(
        self, campaign_id: str, round_num: int | None, span_id: str, *, error: str | None
    ) -> None: ...

    def on_candidate_scored(self, campaign_id: str, record: CandidateScoredRecord) -> None: ...
    def on_round_closed(self, campaign_id: str, record: RoundClosedRecord) -> None: ...

    def on_run_stopped(
        self, campaign_id: str, record: RunPhaseRecord, *, rounds_closed: int
    ) -> None: ...

    def on_query_score_start(self, event: QueryScoreStart) -> None: ...
    def on_query_node_span(self, event: QueryNodeSpan) -> None: ...
    def on_query_score_end(self, event: QueryScoreEnd) -> None: ...
    def flush(self) -> None: ...


__all__ = [
    "CampaignStart",
    "DatasetRegistered",
    "MeasurementEvent",
    "QueryNodeSpan",
    "QueryScoreEnd",
    "QueryScoreStart",
    "TraceSink",
    "dataset_item_id",
    "generate_observation_id",
]
