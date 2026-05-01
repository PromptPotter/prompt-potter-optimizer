# Observability

PromptPotter emits structured trace events for every optimizer LLM call, every backend match, and every escalation check. Traces go two places: local files (always, under the cycle directory) and optionally Langfuse cloud.

---

## Local event log

Every observability event is appended to `campaigns/{cycle_id}/langfuse/events.jsonl`. This is a pure mirror — nothing reads it for state reconstruction; it's there for debugging and post-hoc inspection. Each line is a JSON object with phase, event type, round, and payload.

Phase events (`init`, `l1_generate`, `l1_evaluate`, `refine_strategy`, `modify_plan`, `escalation`, `zero_signal_filter`, `scoring_set`) emit `enter` / `exit` pairs. Mid-phase events carry whatever the emitter chose to include.

For the allowlist of phase events and what each emits, see [../developer/information-flow.md](../developer/information-flow.md).

---

## Per-query P(best) stream

The Bayesian PoBB abortion mechanism (see [`../methods/candidate-elimination.md`](../methods/candidate-elimination.md)) emits a per-query Posterior-of-Being-Best snapshot for every candidate. Today the round-end leaderboards refresh once per round; PoBB updates strictly faster (per query) and is surfaced on every monitoring channel:

| Channel | Path | Cadence | Format |
|---|---|---|---|
| Live dashboard fields | `campaigns/{root_cycle_id}/dashboard.json::current_round.nodes.candidates[].p_best` (also `.p_best_delta`, `.p_best_history`, `.p_best_n_queries`) plus the round-wide `current_round.p_best_top` (top-5 sorted) | per-query overwrite | scalar floats |
| CLI / notebook live display | stderr, via `LiveDisplay` | per-query | one line per query: `p_best q14: *c042* 44.0%▲ c017 28.4%▼ ...` |
| Append-only stream | `campaigns/{cycle_id}/streams/round_NNNN_p_best.jsonl` | per-query append | one JSON record per line: `{round, query_idx, current_id, n_queries, p_best, p_best_delta}` |
| Round-end digest | `campaigns/{cycle_id}/log.md` § P(best) trajectory | once per round | per-candidate Unicode block-element sparkline + final P(best) % |

**Tail-it-yourself** while a campaign is live:

```powershell
# PowerShell — watch dashboard scalars rewrite per query
Get-Content -Path .promptpotter\projects\default\campaigns\<cycle_id>\dashboard.json -Wait
# Or watch the JSONL stream for a specific round
Get-Content -Path .promptpotter\projects\default\campaigns\<cycle_id>\streams\round_0003_p_best.jsonl -Wait
```

```bash
# POSIX — equivalent
tail -f .promptpotter/projects/default/campaigns/<cycle_id>/dashboard.json
tail -f .promptpotter/projects/default/campaigns/<cycle_id>/streams/round_0003_p_best.jsonl
```

The JSONL stream is the canonical replay surface — plotting tools and post-hoc analysis read it. The dashboard fields and log.md sparkline are derived views over the same data; nothing reads them for state reconstruction.

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
| L1 Generate | LLM call | meta-prompt (compiled from `L1GenerateSurface`), candidate outputs, token counts |
| L1 Critique | LLM call | critique-phase blob (`compile_l1_critique_blob`), structured output |
| L2 Refine | LLM call | meta-prompt (compiled from `L2Surface` including `l1_generate_field_catalogue`), parsed `TransitionResult` (directive, optimizer_params, task_context, action, scheme/text/template overrides) |
| L3 Plan | LLM call | plan template (axes_digest + L2 history + pipeline + runtime failures), new plan text |
| Backend match | Span | query, params, result, `diagnostics.warnings` |
| Escalation check | Event | check type, fired/not, reason |
| Stale-data protocol | Event | ladder step taken, resolution |

Node-level tracing follows the same envelope regardless of whether the node lives in the optimizer loop or the backend pipeline — see [../developer/node-standard.md](../developer/node-standard.md).

## Reading what L2 did from a trial

Every L2 fire serializes its writes to the trial JSON. Open `campaigns/{cycle_id}/trials/trial_NNNN.json` and inspect:

- `opt_search_point.l2_directive` — the directive L2 wrote (if any).
- `opt_search_point.l1_section_overrides` / `l1_section_overrides_text` / `l1_template_override` — surface mutations L2 has placed on the individual.
- `opt_search_point.optimizer_params` / `task_context` — strategy fields refined by L2.
- `decisions[]` — entry with `kind: "probe_round_commitment"`, outcome = `True` when L2 set `action = "probe_round"` for the next round.
- `nodes.l2_context.input.prompt` — the rendered L2 prompt; contains the `L1-GENERATE FIELD CATALOGUE` block showing every section's current state.
- `nodes.l2_context.output` — the raw LLM JSON dict L2 emitted.

For a complete reference, see [`../developer/l2-internals.md`](../developer/l2-internals.md).
