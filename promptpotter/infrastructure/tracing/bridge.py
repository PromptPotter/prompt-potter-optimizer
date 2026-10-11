from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.config.settings import settings
from promptpotter.domain.phases import RunPhase
from promptpotter.infrastructure.projections.base import Projection
from promptpotter.infrastructure.tracing.events import (
    CampaignStart,
    DatasetRegistered,
    MeasurementEvent,
    QueryNodeSpan,
    QueryScoreStart,
    TraceSink,
)
from promptpotter.infrastructure.tracing.file_sink import FileSink
from promptpotter.infrastructure.tracing.langfuse_sink import LangfuseSink
from promptpotter.infrastructure.tracing.mlflow_sink import MLflowSink
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.run_records import (
        CandidateScoredRecord,
        LLMCallRecord,
        LLMCallStartRecord,
        PhaseRecord,
        RoundClosedRecord,
        RoundEnteredRecord,
        RunPhaseRecord,
    )
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.tracing.langfuse_client import LangfuseLogger

logger = logging.getLogger(__name__)

__all__ = ["TracingProjection"]

DATASET_NAME: str = "ground_truth"


class TracingProjection(Projection):
    def __init__(self, campaign_id: str, sinks: Sequence[TraceSink]) -> None:
        self._enabled: bool = settings.OBS_ENABLED
        self._campaign_id = campaign_id
        self._sinks = tuple(sinks)
        self._langfuse = next((s for s in self._sinks if isinstance(s, LangfuseSink)), None)
        # A close with no open (a call replayed from cache, a bracket entered before a restart) reaches no sink.
        self._open: set[tuple[int | None, str]] = set()
        self._closed: set[int] = set()

    @classmethod
    def for_cycle(
        cls, store_base_dir: str | Path, hop: CycleHop, *, langfuse: LangfuseLogger | None
    ) -> TracingProjection:
        sinks: list[TraceSink] = [FileSink(store_base_dir, hop)]
        if langfuse and langfuse.enabled:
            sinks.append(LangfuseSink(store_base_dir, hop.campaign_id, langfuse))
        if settings.MLFLOW_ENABLED:
            sinks.append(MLflowSink(store_base_dir))
        return cls(hop.cycle_id, sinks)

    def start(
        self,
        *,
        config_snapshot: dict[str, Any],
        dataset: Sequence[Sample],
        langfuse_session_id: str | None,
    ) -> None:
        opening = CampaignStart(
            campaign_id=self._campaign_id,
            config=config_snapshot,
            cycle_id=self._campaign_id,
            session_id=langfuse_session_id or self._campaign_id,
        )
        self._tell("campaign start", lambda sink: sink.on_campaign_start(opening))
        self._register_dataset(dataset)

    def _tell(self, what: str, call: Callable[[TraceSink], None]) -> None:
        if not self._enabled:
            return
        for sink in self._sinks:
            with graceful(f"{type(sink).__name__} failed on {what}"):
                call(sink)

    def _register_dataset(self, dataset: Sequence[Sample]) -> None:
        seen: set[str] = set()
        items: list[tuple[str, str]] = []
        for sample in dataset:
            if not sample.query or sample.query in seen:
                continue
            seen.add(sample.query)
            items.append((sample.query, sample.ground_truth or ""))
        registered = DatasetRegistered(dataset_name=DATASET_NAME, items=tuple(items))
        self._tell("dataset registration", lambda sink: sink.on_dataset_registered(registered))

    def emit(self, event: MeasurementEvent) -> None:
        if isinstance(event, QueryScoreStart):
            self._tell("query score start", lambda sink: sink.on_query_score_start(event))
        elif isinstance(event, QueryNodeSpan):
            self._tell("query node span", lambda sink: sink.on_query_node_span(event))
        else:
            self._tell("query score end", lambda sink: sink.on_query_score_end(event))

    def langfuse_trace_id(self) -> str | None:
        if self._langfuse is None:
            return None
        return self._langfuse.get_langfuse_trace_id(self._campaign_id)

    def drain(self) -> None:
        self._tell("flush", lambda sink: sink.flush())

    def _open_span(
        self,
        round_num: int | None,
        span_id: str,
        *,
        name: str,
        node_type: str,
        as_type: str,
        metadata: dict[str, Any],
    ) -> None:
        self._open.add((round_num, span_id))
        self._tell(
            f"span {name}",
            lambda sink: sink.on_span_open(
                self._campaign_id,
                round_num,
                span_id,
                name=name,
                node_type=node_type,
                as_type=as_type,
                metadata=metadata,
            ),
        )

    def _close_span(self, round_num: int | None, span_id: str, *, error: str | None) -> None:
        if (round_num, span_id) not in self._open:
            return
        self._open.discard((round_num, span_id))
        self._tell(
            f"span {span_id}",
            lambda sink: sink.on_span_close(self._campaign_id, round_num, span_id, error=error),
        )

    def _handle_round_entered(self, record: RoundEnteredRecord) -> None:
        self._closed.discard(record.round)
        self._tell("round entered", lambda sink: sink.on_round_entered(self._campaign_id, record))

    def _handle_phase(self, record: PhaseRecord) -> None:
        span_id = f"phase:{record.phase}"
        if record.event == "enter":
            name = record.phase if record.round is None else f"{record.phase}_r{record.round}"
            self._open_span(
                record.round, span_id, name=name, node_type="phase", as_type="span", metadata={}
            )
        elif record.event == "exit":
            self._close_span(record.round, span_id, error=None)

    def _handle_llm_call_start(self, record: LLMCallStartRecord) -> None:
        name = record.node if record.round is None else f"{record.node}_r{record.round}"
        self._open_span(
            record.round,
            record.call_id,
            name=name,
            node_type="llm",
            as_type="generation",
            metadata={
                "model": record.model,
                "candidate_idx": record.candidate_idx,
                "prompt_chars": record.prompt_chars,
            },
        )

    def _handle_llm_call(self, record: LLMCallRecord) -> None:
        error = record.payload.get("error")
        self._close_span(
            record.round, record.call_id, error=error if isinstance(error, str) else None
        )

    def _handle_candidate_scored(self, record: CandidateScoredRecord) -> None:
        self._tell(
            "candidate scored", lambda sink: sink.on_candidate_scored(self._campaign_id, record)
        )

    def _handle_round_closed(self, record: RoundClosedRecord) -> None:
        # Only a round's FIRST close ends its span: a later one restates it (round 0, once the ruler warms).
        if record.round in self._closed:
            return
        self._closed.add(record.round)
        self._tell("round closed", lambda sink: sink.on_round_closed(self._campaign_id, record))

    def _handle_run_phase(self, record: RunPhaseRecord) -> None:
        if record.run_phase not in (RunPhase.PAUSED, RunPhase.TERMINAL):
            return
        rounds_closed = max(self._closed, default=0)
        self._tell(
            "run stopped",
            lambda sink: sink.on_run_stopped(
                self._campaign_id, record, rounds_closed=rounds_closed
            ),
        )
        self._tell("flush", lambda sink: sink.flush())
