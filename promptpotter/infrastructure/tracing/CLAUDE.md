# infrastructure/tracing/ — trace sinks

## Tracing — fan-out only, and DORMANT ON PURPOSE

**Tracing exposes no read API and is fanned out FROM the ledger**: `tracing/bridge.py::TracingProjection` is a subscriber, each sink a `tracing/events.py::TraceSink`; nothing in the loop emits to a sink.

**`tracing/mlflow_server.py::register_export` is called from the runner, never a sink** <!-- probe: git grep -cE "register_export\(" -- promptpotter/infrastructure/tracing/*.py <= 1 --> — the export is a file, not a ledger record ([`observability.md`](../../../docs/operations/observability.md) § MLflow's).
