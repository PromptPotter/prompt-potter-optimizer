# infrastructure/tracing/ — trace sinks

## Tracing — fan-out only, and DORMANT ON PURPOSE

`tracing/` exposes no read API. State reaches the optimizer via the
ledger; tracing is fan-out only — and it is fanned out FROM the ledger:
`tracing/bridge.py::TracingProjection` is a subscriber like the projections in [`../CLAUDE.md`](../CLAUDE.md) § Persistence, each sink a
`tracing/events.py::TraceSink` taking ledger records. Nothing in the loop emits to a sink, so a
span the bench's records imply exists for every optimizer.

**The winner's write-back rides here too**: `tracing/mlflow_server.py` is everything written to the operator's MLflow server — the sink, and `register_export`, which registers a cycle's `export.json` as a prompt version. That function is called from the runner and never from a sink — the export is a file, not a ledger record — and what it writes, and what it never does, is [`docs/operations/observability.md`](../../../docs/operations/observability.md) § MLflow's.

**It has no in-repo reader by design, and that is not evidence it is dead — do not propose deleting it.** The Langfuse and MLflow sinks are held for a live integration the operator is bringing up; `LANGFUSE_*` and `MLFLOW_TRACKING_URI` defaulting to `""` are an integration not switched on, not a feature nobody wanted. If it is *badly written*, refactor it; absence of a reader is not the reason.
