"""Tracing package — events + sinks + bridge + replay.

Public API surface preserved across the previous monolithic
``infrastructure/tracing.py`` split. Submodule layout:

- :mod:`events` — frozen dataclasses + ``Event`` union (no I/O)
- :mod:`file_sink` — ``FileSink`` (per-cycle JSONL log)
- :mod:`langfuse_client` — ``LangfuseLogger`` (SDK wrapper)
- :mod:`langfuse_sink` — ``LangfuseSink`` (cloud-forward + id state)
- :mod:`mlflow_sink` — ``MLflowSink`` (opt-in MLflow integration)
- :mod:`bridge` — ``ObservabilityBridge`` + ``observed_node`` (fan-out)
- :mod:`replay` — historical-measurement backfill into Langfuse
"""

from promptpotter.infrastructure.tracing.bridge import (
    NodeTrace,
    ObservabilityBridge,
    observed_node,
)
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
    MeasurementEvent,
    NodeEnd,
    NodeStart,
    OptimizationEvent,
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
from promptpotter.infrastructure.tracing.replay import (
    LangfuseObservation,
    classify_run_origin,
    extract_pipeline_nodes,
    push_all_runs,
    sync_langfuse_runs,
)

__all__ = [
    "CampaignEnd",
    "CampaignStart",
    "CandidateCreated",
    "CandidateScored",
    "DatasetRegistered",
    "DatasetRun",
    "Event",
    "FileSink",
    "L1CritiqueWritten",
    "LangfuseLogger",
    "LangfuseObservation",
    "LangfuseSink",
    "LayerApplied",
    "MLflowSink",
    "MeasurementEvent",
    "NodeEnd",
    "NodeStart",
    "NodeTrace",
    "ObservabilityBridge",
    "OptimizationEvent",
    "PromptVersion",
    "QueryNodeSpan",
    "QueryScoreEnd",
    "QueryScoreStart",
    "RoundEnd",
    "RoundStart",
    "RoundWinnerChosen",
    "classify_run_origin",
    "extract_pipeline_nodes",
    "observed_node",
    "push_all_runs",
    "sync_langfuse_runs",
]
