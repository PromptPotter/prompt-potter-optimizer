"""Observability bridge — fans events from :mod:`events` to file + Langfuse + MLflow sinks
under :func:`graceful` so observability never crashes the loop.

LangfuseSink persists id state to ``campaigns/{cycle_id}/langfuse/state.json``
after every mutation so CLI-interrupted resumes produce one continuous trace.
"""

from __future__ import annotations

import hashlib
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from promptpotter.config.settings import DATASET_NAME, settings
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
    QueryNodeSpan,
    QueryScoreEnd,
    QueryScoreStart,
    RoundEnd,
    RoundStart,
    RoundWinnerChosen,
)
from promptpotter.infrastructure.tracing.file_sink import FileSink
from promptpotter.infrastructure.tracing.langfuse_client import LangfuseLogger
from promptpotter.infrastructure.tracing.langfuse_sink import LangfuseSink
from promptpotter.infrastructure.tracing.mlflow_sink import MLflowSink
from promptpotter.shared.errors import graceful

logger = logging.getLogger(__name__)

__all__ = ["NodeTrace", "ObservabilityBridge", "observed_node"]


# Event-type → sink-method dispatch. Sinks lacking a method are silently
# skipped. The 4 mid-round write-points share ``on_write_point`` (file sink).
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
    QueryScoreStart: "on_query_score_start",
    QueryNodeSpan: "on_query_node_span",
    QueryScoreEnd: "on_query_score_end",
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
        mlflow_sink: MLflowSink | None = None,
    ) -> None:

        self._enabled: bool = settings.OBS_ENABLED
        self._file = file_sink
        self._langfuse = langfuse_sink
        self._mlflow = mlflow_sink

    @classmethod
    def from_settings(
        cls,
        store_base_dir: str | Path,
        backend_id: str,
        *,
        langfuse: LangfuseLogger | None,
    ) -> ObservabilityBridge:

        file_sink = FileSink(store_base_dir, backend_id)
        lf_sink = (
            LangfuseSink(store_base_dir, backend_id, langfuse)
            if (langfuse and langfuse.enabled)
            else None
        )
        mlflow_sink = MLflowSink(store_base_dir, backend_id) if settings.MLFLOW_ENABLED else None
        return cls(file_sink=file_sink, langfuse_sink=lf_sink, mlflow_sink=mlflow_sink)

    @classmethod
    def file_only(
        cls,
        store_base_dir: str | Path,
        backend_id: str,
    ) -> ObservabilityBridge:

        return cls(
            file_sink=FileSink(store_base_dir, backend_id),
            langfuse_sink=None,
            mlflow_sink=MLflowSink(store_base_dir, backend_id) if settings.MLFLOW_ENABLED else None,
        )

    def emit(self, event: Event) -> None:
        if not self._enabled:
            return
        method = _DISPATCH.get(type(event))
        if method is None:
            return
        for label, sink in (
            ("file", self._file),
            ("langfuse", self._langfuse),
            ("mlflow", self._mlflow),
        ):
            if sink is None:
                continue
            fn = getattr(sink, method, None)
            if fn is not None:
                with graceful(f"{label} sink {method} failed on {type(event).__name__}"):
                    fn(event)

    def emit_write_point(
        self,
        event_cls: type,
        *,
        campaign_id: str,
        round_num: int,
        **extra: Any,
    ) -> None:
        # events.jsonl is a pure mirror; resume uses trials/trial_NNNN.json.
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
        """Emit ``DatasetRegistered``, return ``{query: file_item_id}``."""
        if not self._enabled:
            return {}

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
        tracing_campaign_id: str,
        langfuse_session_id: str | None,
        langfuse: LangfuseLogger | None,
    ) -> ObservabilityBridge | None:
        """Build a bridge, emit ``CampaignStart``, register the dataset."""

        if not (project_root and backend_id):
            return None

        bridge: ObservabilityBridge | None = None
        with graceful("Failed to create ObservabilityBridge"):
            bridge = cls.from_settings(project_root, backend_id, langfuse=langfuse)
        if bridge is None:
            return None

        with graceful("CampaignStart emit failed"):
            bridge.emit(
                CampaignStart(
                    campaign_id=tracing_campaign_id,
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
        tracing_campaign_id: str,
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
                    campaign_id=tracing_campaign_id,
                    best_accuracy=best_accuracy,
                    n_rounds=n_rounds,
                    stop_reason=stop_reason,
                    best_round=best_round,
                )
            )
            self.flush()
            langfuse_trace_id = self.get_langfuse_trace_id(tracing_campaign_id)
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
