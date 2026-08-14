"""Observability bridge — fans events to file + Langfuse + MLflow under :func:`graceful`, so observability never crashes
the loop. Langfuse id state persists after every mutation, so an interrupted resume produces one continuous trace."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, get_args

from promptpotter.config.settings import DATASET_NAME, settings
from promptpotter.infrastructure.tracing.events import (
    CampaignEnd,
    CampaignStart,
    DatasetRegistered,
    DatasetRun,
    Event,
    NodeEnd,
    NodeStart,
    PromptVersion,
    QueryNodeSpan,
    QueryScoreEnd,
    QueryScoreStart,
    RoundEnd,
    RoundStart,
    dataset_item_id,
)
from promptpotter.infrastructure.tracing.file_sink import FileSink
from promptpotter.infrastructure.tracing.langfuse_client import LangfuseLogger

if TYPE_CHECKING:
    from promptpotter.domain.sample import Sample
from promptpotter.infrastructure.tracing.langfuse_sink import LangfuseSink
from promptpotter.infrastructure.tracing.mlflow_sink import MLflowSink
from promptpotter.shared.errors import graceful

logger = logging.getLogger(__name__)

__all__ = ["ObservabilityBridge", "observed_node"]

_Route = tuple[str, Callable[[Any], None] | None]


@dataclass
class NodeTrace:
    output: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    error: str | None = None


class ObservabilityBridge:
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

        f, lf, ml = file_sink, langfuse_sink, mlflow_sink
        # The routing table IS the fan-out: event type → the sink handlers that receive it,
        # as literal bound methods, so `grep on_node_start` still lands on the one call site
        # and a sink's ABSENCE from a row means it genuinely isn't called. Adding an event is
        # this row plus its handlers — never a separate dispatch arm to keep in step.
        self._routes: dict[type, tuple[_Route, ...]] = {
            DatasetRegistered: (
                ("file", f.on_dataset_registered),
                ("langfuse", lf.on_dataset_registered if lf else None),
            ),
            CampaignStart: (
                ("file", f.on_campaign_start),
                ("langfuse", lf.on_campaign_start if lf else None),
                ("mlflow", ml.on_campaign_start if ml else None),
            ),
            DatasetRun: (
                ("file", f.on_dataset_run),
                ("langfuse", lf.on_dataset_run if lf else None),
            ),
            RoundStart: (
                ("file", f.on_round_start),
                ("langfuse", lf.on_round_start if lf else None),
            ),
            NodeStart: (
                ("file", f.on_node_start),
                ("langfuse", lf.on_node_start if lf else None),
            ),
            NodeEnd: (
                ("file", f.on_node_end),
                ("langfuse", lf.on_node_end if lf else None),
            ),
            RoundEnd: (
                ("file", f.on_round_end),
                ("langfuse", lf.on_round_end if lf else None),
                ("mlflow", ml.on_round_end if ml else None),
            ),
            PromptVersion: (
                ("file", f.on_prompt_version),
                ("langfuse", lf.on_prompt_version if lf else None),
            ),
            CampaignEnd: (
                ("file", f.on_campaign_end),
                ("langfuse", lf.on_campaign_end if lf else None),
            ),
            QueryScoreStart: (("langfuse", lf.on_query_score_start if lf else None),),
            QueryNodeSpan: (("langfuse", lf.on_query_node_span if lf else None),),
            QueryScoreEnd: (("langfuse", lf.on_query_score_end if lf else None),),
        }
        # The table is the registry, so it must be WHOLE: an `Event` member with no row is
        # dropped by `emit` in silence, which is the one failure this fan-out cannot report.
        unrouted = [e.__name__ for e in get_args(Event) if e not in self._routes]
        if unrouted:
            raise RuntimeError(
                f"ObservabilityBridge routes no sink for {', '.join(unrouted)} — every Event "
                "member needs a row, or delete it from the union (it has no sink to reach)"
            )

    @classmethod
    def from_settings(
        cls,
        store_base_dir: str | Path,
        campaign_id: str = "",
        *,
        langfuse: LangfuseLogger | None,
    ) -> ObservabilityBridge:

        file_sink = FileSink(store_base_dir, campaign_id)
        lf_sink = (
            LangfuseSink(store_base_dir, campaign_id, langfuse)
            if (langfuse and langfuse.enabled)
            else None
        )
        mlflow_sink = MLflowSink(store_base_dir) if settings.MLFLOW_ENABLED else None
        return cls(file_sink=file_sink, langfuse_sink=lf_sink, mlflow_sink=mlflow_sink)

    @classmethod
    def file_only(
        cls,
        store_base_dir: str | Path,
        campaign_id: str = "",
    ) -> ObservabilityBridge:

        return cls(
            file_sink=FileSink(store_base_dir, campaign_id),
            langfuse_sink=None,
            mlflow_sink=MLflowSink(store_base_dir) if settings.MLFLOW_ENABLED else None,
        )

    def emit(self, event: Event) -> None:
        """Call each sink handler routed to ``event``'s type under :func:`graceful`. An event type
        absent from ``_routes`` has no sink and is dropped — declaring one without a row is the bug."""
        if not self._enabled:
            return
        for label, fn in self._routes.get(type(event), ()):
            if fn is None:
                continue
            with graceful(f"{label} sink failed on {type(event).__name__}"):
                fn(event)

    def flush(self) -> None:
        with graceful("Langfuse sink flush failed"):
            if self._langfuse is not None:
                self._langfuse.flush()

    def get_langfuse_trace_id(self, campaign_id: str) -> str | None:
        return self._langfuse.get_langfuse_trace_id(campaign_id) if self._langfuse else None

    @property
    def langfuse_sink(self) -> LangfuseSink | None:
        return self._langfuse

    def register_dataset(
        self, dataset_name: str, dataset: Sequence[Sample | dict[str, Any]]
    ) -> dict[str, str]:
        if not self._enabled:
            return {}

        query_to_item_id: dict[str, str] = {}
        seen: set[str] = set()
        items: list[tuple[str, str]] = []
        for entry in dataset:
            if isinstance(entry, dict):
                query = entry.get("query", "")
                ground_truth = entry.get("ground_truth", "")
            else:
                query = entry.query
                ground_truth = entry.ground_truth
            if not query or query in seen:
                continue
            seen.add(query)
            items.append((query, ground_truth))
            query_to_item_id[query] = dataset_item_id(dataset_name, query)

        self.emit(DatasetRegistered(dataset_name=dataset_name, items=tuple(items)))
        return query_to_item_id

    @classmethod
    def start_campaign(
        cls,
        tenant_root: str | Path | None,
        backend_id: str | None,
        *,
        config_snapshot: dict[str, Any],
        origin_accuracy: float,
        dataset: Sequence[Sample | dict[str, Any]],
        tracing_campaign_id: str,
        campaign_id: str,
        langfuse_session_id: str | None,
        langfuse: LangfuseLogger | None,
    ) -> ObservabilityBridge | None:
        if not (tenant_root and backend_id):
            return None

        bridge: ObservabilityBridge | None = None
        with graceful("Failed to create ObservabilityBridge"):
            bridge = cls.from_settings(tenant_root, campaign_id, langfuse=langfuse)
        if bridge is None:
            return None

        with graceful("CampaignStart emit failed"):
            bridge.emit(
                CampaignStart(
                    campaign_id=tracing_campaign_id,
                    config=config_snapshot,
                    origin_accuracy=origin_accuracy,
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
        n_l1_rounds: int,
        stop_reason: str,
        best_round: int,
    ) -> str | None:
        langfuse_trace_id: str | None = None
        with graceful("Bridge campaign end failed"):
            self.emit(
                CampaignEnd(
                    campaign_id=tracing_campaign_id,
                    best_accuracy=best_accuracy,
                    n_l1_rounds=n_l1_rounds,
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
    as_type: str = "generation",
) -> AsyncIterator[NodeTrace]:
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
                    as_type=as_type,
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
