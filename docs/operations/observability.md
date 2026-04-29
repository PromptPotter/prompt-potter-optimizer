# Observability

PromptPotter emits structured trace events for every optimizer LLM call, every backend match, and every escalation check. Traces go two places: local files (always, under the cycle directory) and optionally Langfuse cloud.

---

## Local event log

Every observability event is appended to `campaigns/{cycle_id}/langfuse/events.jsonl`. This is a pure mirror — nothing reads it for state reconstruction; it's there for debugging and post-hoc inspection. Each line is a JSON object with phase, event type, round, and payload.

Phase events (`init`, `l1_generate`, `l1_evaluate`, `refine_strategy`, `modify_plan`, `escalation`, `zero_signal_filter`, `scoring_set`) emit `enter` / `exit` pairs. Mid-phase events carry whatever the emitter chose to include.

For the allowlist of phase events and what each emits, see [../developer/information-flow.md](../developer/information-flow.md).

---

## Langfuse integration

When `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` are set in `.env`, traces also go to Langfuse cloud. The integration and sink both live in `promptpotter/infrastructure/tracing.py`.

Trace shape:

- **Cycle** = Langfuse trace
- **Round** = nested span
- **Each L1 / L2 / L3 / critique call** = LLM observation with input, output, and token counts
- **Each backend match** = span with query, params, result, and diagnostics

A mirror of what's sent to Langfuse is persisted locally under `campaigns/{cycle_id}/langfuse/` including the id-map `state.json` so you can cross-reference a local round with its Langfuse trace.

### Backfill

If Langfuse was off when a campaign ran and you want to push historical traces up later, the backfill helper in `promptpotter/infrastructure/tracing.py` reads the local shadow and replays it. Useful for resurrecting runs when cloud observability was added mid-project.

---

## MLflow sink

Each round can optionally be logged as an MLflow run, gated by `settings.MLFLOW_ENABLED` (default `False`). Tracking URI points at `library/mlruns/` under the tenant root, and the experiment name is `{tenant_id}/{cycle_id}` so every cycle owns its own experiment. Implementation: `promptpotter/infrastructure/tracing.py`. The sink installs alongside the file and Langfuse sinks — no other observability surface changes when it's toggled. Turn it on by setting `MLFLOW_ENABLED=true` in `.env`.

---

## Enabling Langfuse

Install the extra:

```bash
pip install -e ".[observability]"
```

Add to `.env`:

```
LANGFUSE_PUBLIC_KEY=pk-...
LANGFUSE_SECRET_KEY=sk-...
LANGFUSE_HOST=https://cloud.langfuse.com
```

Next `optimize` will start sending traces.

---

## What gets traced

| Source | Event type | Payload |
|--------|-----------|---------|
| L1 Generate | LLM call | meta-prompt, candidate outputs, token counts |
| L1 Critique | LLM call | critique-phase dispatch_msg, structured output |
| L2 Refine | LLM call | refine dispatch_msg, new directive / task_context |
| L3 Plan | LLM call | plan template (axes_digest + L2 history + pipeline + runtime failures), new plan text |
| Backend match | Span | query, params, result, `diagnostics.warnings` |
| Escalation check | Event | check type, fired/not, reason |
| Stale-data protocol | Event | ladder step taken, resolution |

Node-level tracing follows the same envelope regardless of whether the node lives in the optimizer loop or the backend pipeline — see [../developer/node-standard.md](../developer/node-standard.md).
