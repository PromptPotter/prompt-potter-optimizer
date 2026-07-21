"""Tracing package — events + sinks + bridge.

Public API surface preserved across the previous monolithic
``infrastructure/tracing.py`` split. Submodule layout:

- :mod:`events` — frozen dataclasses + ``Event`` union (no I/O)
- :mod:`file_sink` — ``FileSink`` (per-cycle JSONL log)
- :mod:`langfuse_client` — ``LangfuseLogger`` (SDK wrapper)
- :mod:`langfuse_sink` — ``LangfuseSink`` (cloud-forward + id state)
- :mod:`mlflow_sink` — ``MLflowSink`` (opt-in MLflow integration)
- :mod:`bridge` — ``ObservabilityBridge`` + ``observed_node`` (fan-out)
"""
