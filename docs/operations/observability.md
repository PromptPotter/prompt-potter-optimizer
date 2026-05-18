# Observability

Every optimizer LLM call, backend match, and escalation check emits a structured trace event. Traces go two places: local files (always, under the cycle directory) and optionally Langfuse cloud.

## Local event log

Events are appended to `campaigns/{cycle_id}/langfuse/events.jsonl`. Pure mirror — nothing reads it for state reconstruction; it's there for debugging and post-hoc inspection. Each line is a JSON object with phase, event type, round, and payload.

Phase events (`init`, `l1_generate`, `l1_evaluate`, `refine_strategy`, `modify_plan`, `escalation`) emit `enter` / `exit` pairs. The `escalation` phase emits a `rule_fired` event whenever the post-round rule engine matches a rule (see "Escalation rule signal stream" below).

## What gets traced

| Source | Event type | Payload |
|--------|-----------|---------|
| L1 Generate | LLM call | meta-prompt (compiled from `L1GenerateSurface`), candidate outputs, token counts |
| L1 Critique | LLM call | critique-phase blob (`compile_l1_critique_blob`), structured output |
| L2 Refine | LLM call | meta-prompt (from `L2Surface` incl. `l1_generate_field_catalogue`), parsed `TransitionResult` |
| L3 Plan | LLM call | plan template (axes_digest + L2 history + pipeline + runtime failures), new plan text |
| Backend match | Span | query, params, result, `diagnostics.warnings` |
| Escalation rule firing | `phase` event (`escalation/rule_fired`) | `{layer, rule_name, rule_priority, next_action, reason, signal_inputs}` |
| Escalation check | Event | check type, fired/not, reason |
| Stale-data protocol | Event | ladder step taken, resolution |

## Per-sample P(best) stream

PoBB emits a per-sample Posterior-of-Being-Best snapshot for every candidate. Surfaced on every monitoring channel:

| Channel | Path | Cadence | Format |
|---|---|---|---|
| Live dashboard fields | `dashboard.json::current_round.nodes.candidates[].p_best` (also `.p_best_delta`, `.p_best_history`, `.p_best_n_samples`) + `current_round.p_best_top` (top-5 sorted) | per-sample overwrite | scalar floats |
| CLI / notebook | stderr via `LiveDisplay` | per-sample | one line: `p_best q14: *c042* 44.0%▲ c017 28.4%▼ ...` |
| Append-only stream | `campaigns/{cycle_id}/.runtime/streams/round_NNNN_p_best.jsonl` | per-sample append | `{round, sample_idx, current_id, n_samples, p_best, p_best_delta}` |
| Round-end digest | `campaigns/{cycle_id}/log.md` § P(best) trajectory | once per round | per-candidate Unicode sparkline + final % |

The JSONL stream is canonical replay. Dashboard fields and `log.md` sparkline are derived views.

```bash
# Tail the live JSONL
tail -f .promptpotter/projects/default/campaigns/<cycle_id>/.runtime/streams/round_0003_p_best.jsonl
```

## Escalation rule firing

Every escalation-rule firing — `decide_escalation` over `DEFAULT_ESCALATION_RULES` — emits a `escalation/rule_fired` PhaseRecord through the writer-side ingress (`RunCallbacks.on_phase`). The ledger event lands in `events.jsonl` and is consumed by the audit projection. Operator-facing read: `events.jsonl` itself (the standalone `signals.jsonl` mirror + dashboard `recent_rules` were dropped in the M10 cleanup; the ledger is the canonical record).

## Langfuse cloud

When `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` are set in `.env`, traces also go to Langfuse cloud. Integration: `promptpotter/infrastructure/tracing.py`.

Trace shape:

- **Cycle** = Langfuse trace
- **Round** = nested span
- **L1 / L2 / L3 / critique call** = LLM observation (input, output, token counts)
- **Backend match** = span (query, params, result, diagnostics)

A mirror of what's sent persists locally under `campaigns/{cycle_id}/langfuse/` including the id-map `state.json` for cross-reference.

### Enable

```bash
pip install -e ".[observability]"
```

```dotenv
LANGFUSE_PUBLIC_KEY=pk-...
LANGFUSE_SECRET_KEY=sk-...
LANGFUSE_HOST=https://cloud.langfuse.com
```

Next `new` / `resume` starts sending traces. To push historical traces uploaded later, the backfill helper in `infrastructure/tracing.py` reads the local shadow and replays it.

## MLflow sink

Each round can be logged as an MLflow run when `settings.MLFLOW_ENABLED` is true (default false). Tracking URI: `archive/mlruns/`. Experiment name: `{tenant_id}/{cycle_id}`. Implementation: `infrastructure/tracing.py`. Installs alongside file + Langfuse sinks. Toggle via `MLFLOW_ENABLED=true` in `.env`.

## Display convention — `⚠ … ↳`

PromptPotter surfaces optimizer findings (validation failures, anomaly flags, elimination signals, degradation) with a two-line shape:

```
⚠ <fact, in data terms>
  ↳ <action, in optimizer terms>
```

Line 1 names the observation. Line 2 names the repair or consequence. A finding without a `↳` is a bug.

```
⚠ llm_only.model = 'gpt-4o' ∉ [openai/gpt-oss-120b]
  ↳ scored 0; L2 brief will name this value
```

`dashboard.json::last_scoring_metadata` holds the structured finding. Each entry point reads from there and formats using this convention — data lives in one place, only rendering is per-surface.

### Per-sample annotation order

Annotations render in this order, with mutual exclusion on the status line:

1. `⚠ {step}: {message}` — one line per diagnostic warning (always renders).
2. One status annotation, exclusive set:
   - `🔄 cache had pipeline warnings → reran; result: …`
   - `🔬 cache had warnings + rerun still degraded → resampled N fresh calls …`
   - `🔀 query degrades ≥ 50% historically → using cached answer …`
   - `⚠ entire stale-data ladder exhausted → still degraded …`
   - `↩ pipeline warning observed; X/Y occurrences toward rerun trigger …` — degraded observed, AND no fatal warning on this query

The fatal-warning suppression of `↩ …` is load-bearing: when a fatal warning fires, the candidate is dead, so a counter reading "1/3 toward rerun" would falsely suggest more data is coming.

The stale-data ladder's rescue step is **samplescan rescue** — "probe" is reserved for L2/L3 probe rounds.

## Reading what L2 wrote from a trial

`campaigns/{cycle_id}/rounds/round_NNNN.json`:

- `opt_search_point.task_context` — refined task framing (broadcast to all layers next round).
- `opt_search_point.l1_layout` — per-slot signal-name layout L2 has stamped.
- `opt_search_point.l1_overrides` — L1 runtime knobs (creativity, n_variants).
- `decisions[]` with `kind: "probe_round_commitment"` — outcome `True` when L2 set `action = "probe_round"`.
- `nodes.l2_context.input.prompt` — rendered L2 prompt (incl. the `L1-GENERATE FIELD CATALOGUE` block).
- `nodes.l2_context.output` — raw LLM JSON dict.

Full reference: [`../developer/l2-internals.md`](../developer/l2-internals.md).
