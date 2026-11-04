# infrastructure/tracing/ — trace sinks

**Fan-out only, and dormant by default**: `bridge.py::TracingProjection` is a ledger subscriber, each sink an `events.py::TraceSink`, and there is no read API; nothing in the loop emits to a sink. `mlflow_server.py::register_export` is called from the runner, never a sink <!-- probe: git grep -cE "register_export\(" -- promptpotter/infrastructure/tracing/*.py <= 1 --> — the export is a file, not a ledger record ([`observability.md`](../../../docs/operations/observability.md) § MLflow's).
