"""Observability bridge — single entry point over file + Langfuse sinks.

Call sites build event dataclasses from :mod:`events` and call
``bridge.emit(event)``. The bridge looks the event type up in
:data:`_DISPATCH`, then calls ``on_<name>`` on each sink directly under
:func:`graceful` so observability can never crash the optimization loop.

The dual-state ``ObsLogger`` it replaces held both a file logger and a
shadowed Langfuse client with six in-memory id maps that were lost on
resume. :class:`LangfuseSink` now persists all id state to
``campaigns/{cycle_id}/langfuse_state.json`` after every mutation, so a
campaign interrupted on the CLI and resumed from the notebook produces one
continuous trace.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.infrastructure.tracing.events import (
    CampaignEnd,
    CampaignStart,
    CandidateCreated,
    CandidateScored,
    DatasetRegistered,
    DatasetRun,
    Event,
    L1CritiqueWritten,
    LayerApplied,
    NodeEnd,
    NodeStart,
    PromptVersion,
    QueryEvalEnd,
    QueryEvalStart,
    QueryNodeSpan,
    RoundEnd,
    RoundStart,
    RoundWinnerChosen,
)
from promptpotter.infrastructure.tracing.sinks.file_sink import FileSink
from promptpotter.infrastructure.tracing.sinks.langfuse_sink import LangfuseSink
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.infrastructure.tracing.langfuse_client import LangfuseLogger

logger = logging.getLogger(__name__)

__all__ = ["NodeTrace", "ObservabilityBridge", "observed_node"]


# Single source of truth for event-type → sink-method-name dispatch.
# Sinks expose ``on_<name>(event)`` (or skip a key, in which case the bridge
# silently drops the event for that sink — see ``getattr(.., None)`` below).
# The four mid-round write-points all map to ``on_write_point`` because the
# file sink writes them through one shared formatter.
_DISPATCH: dict[type[Event], str] = {
    DatasetRegistered: "on_dataset_registered",
    CampaignStart: "on_campaign_start",
    DatasetRun: "on_dataset_run",
    RoundStart: "on_round_start",
    NodeStart: "on_node_start",
    NodeEnd: "on_node_end",
    RoundEnd: "on_round_end",
    PromptVersion: "on_prompt_version",
    CampaignEnd: "on_campaign_end",
    CandidateCreated: "on_write_point",
    CandidateScored: "on_write_point",
    RoundWinnerChosen: "on_write_point",
    L1CritiqueWritten: "on_write_point",
    LayerApplied: "on_layer_applied",
    QueryEvalStart: "on_query_eval_start",
    QueryNodeSpan: "on_query_node_span",
    QueryEvalEnd: "on_query_eval_end",
}


@dataclass
class NodeTrace:
    output: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    error: str | None = None


class ObservabilityBridge:
    """Fans events out to file + Langfuse sinks under :func:`graceful`."""

    def __init__(
        self,
        *,
        file_sink: FileSink,
        langfuse_sink: LangfuseSink | None,
    ) -> None:
        from promptpotter.config.settings import settings

        self._enabled: bool = settings.OBS_ENABLED
        self._file = file_sink
        self._langfuse = langfuse_sink

    @classmethod
    def from_settings(
        cls,
        store_base_dir: str | Path,
        backend_id: str,
        *,
        langfuse: LangfuseLogger | None = None,
    ) -> ObservabilityBridge:
        file_sink = FileSink(store_base_dir, backend_id)
        lf_sink: LangfuseSink | None = None
        if langfuse is None:
            with graceful("Cloud backend init failed; file-only mode", level=logging.DEBUG):
                from promptpotter.infrastructure.tracing.langfuse_client import LangfuseLogger

                lf = LangfuseLogger.get_instance()
                if lf.enabled:
                    lf_sink = LangfuseSink(store_base_dir, backend_id, lf)
        elif langfuse.enabled:
            lf_sink = LangfuseSink(store_base_dir, backend_id, langfuse)
        return cls(file_sink=file_sink, langfuse_sink=lf_sink)

    @classmethod
    def file_only(
        cls,
        store_base_dir: str | Path,
        backend_id: str,
    ) -> ObservabilityBridge:
        return cls(
            file_sink=FileSink(store_base_dir, backend_id),
            langfuse_sink=None,
        )

    def emit(self, event: Event) -> None:
        """Dispatch *event* to file + (optionally) Langfuse sinks directly.

        A sink that doesn't subscribe to the event simply lacks the method;
        ``getattr(...,  None)`` makes that a no-op without an explicit table
        per sink.
        """
        if not self._enabled:
            return
        method = _DISPATCH.get(type(event))
        if method is None:
            return
        fn = getattr(self._file, method, None)
        if fn is not None:
            with graceful(f"file sink {method} failed on {type(event).__name__}"):
                fn(event)
        if self._langfuse is not None:
            fn = getattr(self._langfuse, method, None)
            if fn is not None:
                with graceful(f"langfuse {method} failed on {type(event).__name__}"):
                    fn(event)

    def emit_write_point(
        self,
        event_cls: type,
        *,
        campaign_id: str,
        round_num: int,
        **extra: Any,
    ) -> None:
        """Emit a mid-round write-point observability event.

        events.jsonl is a pure observability mirror — nothing reads these
        events back for state reconstruction. Resume uses
        ``campaigns/{cycle_id}/trials/trial_NNNN.json`` via ``CampaignStore``.
        """
        if not self._enabled:
            return
        with graceful(f"{event_cls.__name__} emit failed"):
            self.emit(event_cls(campaign_id=campaign_id, round_num=round_num, **extra))

    def flush(self) -> None:
        with graceful("Langfuse sink flush failed"):
            if self._langfuse is not None:
                self._langfuse.flush()

    def get_langfuse_trace_id(self, campaign_id: str) -> str | None:
        return self._langfuse.get_langfuse_trace_id(campaign_id) if self._langfuse else None

    @property
    def langfuse_sink(self) -> LangfuseSink | None:
        return self._langfuse

    def register_dataset(self, dataset_name: str, dataset: list) -> dict[str, str]:
        """Emit ``DatasetRegistered`` and return ``{query: file_item_id}``.

        Accepts ``list[Sample]`` or legacy ``list[dict]``.
        """
        if not self._enabled:
            return {}
        import hashlib

        query_to_item_id: dict[str, str] = {}
        seen: set[str] = set()
        items: list[tuple[str, str]] = []
        for entry in dataset:
            if hasattr(entry, "query"):
                query = entry.query
                ground_truth = entry.ground_truth
            else:
                query = entry.get("query", "")
                ground_truth = entry.get("ground_truth", "")
            if not query or query in seen:
                continue
            seen.add(query)
            items.append((query, ground_truth))
            query_to_item_id[query] = hashlib.sha256(
                f"{dataset_name}:{query}".encode(),
            ).hexdigest()[:16]

        self.emit(DatasetRegistered(dataset_name=dataset_name, items=tuple(items)))
        return query_to_item_id

    @classmethod
    def start_campaign(
        cls,
        project_root: str | Path | None,
        backend_id: str | None,
        *,
        config_snapshot: dict[str, Any],
        baseline_accuracy: float,
        dataset: list,
        obs_campaign_id: str,
        langfuse_session_id: str | None,
    ) -> ObservabilityBridge | None:
        """Build a bridge, emit ``CampaignStart``, register the dataset."""
        from promptpotter.shared.constants import DATASET_NAME

        if not (project_root and backend_id):
            return None

        bridge: ObservabilityBridge | None = None
        with graceful("Failed to create ObservabilityBridge"):
            bridge = cls.from_settings(project_root, backend_id)
        if bridge is None:
            return None

        with graceful("CampaignStart emit failed"):
            bridge.emit(
                CampaignStart(
                    campaign_id=obs_campaign_id,
                    config=config_snapshot,
                    baseline_accuracy=baseline_accuracy,
                    session_id=langfuse_session_id,
                )
            )
        with graceful("Dataset registration failed"):
            dataset_item_map = bridge.register_dataset(DATASET_NAME, dataset)
            if dataset_item_map:
                logger.debug(
                    "Registered %d dataset items for '%s'",
                    len(dataset_item_map),
                    DATASET_NAME,
                )
        return bridge

    def end_campaign(
        self,
        obs_campaign_id: str,
        *,
        best_accuracy: float,
        n_rounds: int,
        stop_reason: str,
        best_round: int,
    ) -> str | None:
        """Emit ``CampaignEnd``, flush, return the Langfuse trace id."""
        langfuse_trace_id: str | None = None
        with graceful("Bridge campaign end failed"):
            self.emit(
                CampaignEnd(
                    campaign_id=obs_campaign_id,
                    best_accuracy=best_accuracy,
                    n_rounds=n_rounds,
                    stop_reason=stop_reason,
                    best_round=best_round,
                )
            )
            self.flush()
            langfuse_trace_id = self.get_langfuse_trace_id(obs_campaign_id)
        return langfuse_trace_id


@asynccontextmanager
async def observed_node(
    node_id: str,
    node_type: str,
    *,
    obs: ObservabilityBridge | None,
    campaign_id: str,
    round_num: int,
    obs_type: str = "generation",
):
    """Time a node and emit ``NodeStart`` / ``NodeEnd`` around the body."""
    trace = NodeTrace()
    opened = False

    if obs is not None:
        with graceful(f"observed_node start failed for {node_id}"):
            obs.emit(
                NodeStart(
                    campaign_id=campaign_id,
                    round_num=round_num,
                    node_id=node_id,
                    node_type=node_type,
                    obs_type=obs_type,
                    input_data={},
                )
            )
            opened = True

    t0 = time.perf_counter()
    try:
        yield trace
    except Exception as exc:
        trace.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        trace.duration_ms = (time.perf_counter() - t0) * 1000
        if obs is not None and opened:
            with graceful(f"observed_node end failed for {node_id}"):
                obs.emit(
                    NodeEnd(
                        campaign_id=campaign_id,
                        round_num=round_num,
                        node_id=node_id,
                        output_data=trace.output,
                        metrics={"duration_ms": trace.duration_ms},
                        error=trace.error,
                    )
                )
