# Observability

Every optimizer LLM call, backend match, and escalation check emits a structured trace — to local files always (under the cycle dir), and to Langfuse cloud when configured.

## What's traced, and where

Phase events (`init`, `l1_generate`, `l1_evaluate`, `refine_strategy`, `modify_plan`, `escalation`) emit `enter`/`exit` pairs into the per-cycle ledger; the `escalation` phase emits `rule_fired` whenever a post-round rule matches. `langfuse/events.jsonl` is a pure mirror — nothing reads it for state reconstruction.

| Source | Event | Payload |
|--------|-------|---------|
| L1 Generate | LLM call | rendered meta-prompt, candidate outputs, token counts |
| L1 Critique | LLM call | critique meta-prompt, structured output |
| L2 Refine | LLM call | refinement meta-prompt (incl. the L1 field catalogue), parsed transition |
| L3 Plan | LLM call | plan template (axes_digest + L2 history + pipeline + runtime failures), new plan |
| Backend match | Span | query, params, result, `diagnostics.warnings` |
| Escalation rule firing | `escalation/rule_fired` | `{layer, rule_name, rule_priority, next_action, reason, signal_inputs}` |
| Stale-data protocol | Event | ladder step taken, resolution |

## Per-sample P(best) stream

PoBB emits a per-sample Posterior-of-Being-Best snapshot for every candidate, on four channels:

| Channel | Path | Format |
|---|---|---|
| Live dashboard | `dashboard.json::current_round.nodes.candidates[].p_best` (+ `_delta`/`_history`/`_n_samples`) + `current_round.p_best_top` | scalar floats |
| CLI / notebook | stderr | `p_best q14: *c042* 44.0%▲ c017 28.4%▼ …` |
| Append-only stream | `cycles/{cycle_id}/.runtime/streams/round_NNNN_p_best.jsonl` | `{round, sample_idx, current_id, n_samples, p_best, p_best_delta}` |
| Round digest | `log.md` § P(best) trajectory | per-candidate sparkline + final % |

The JSONL stream is canonical replay; the dashboard fields and the sparkline are derived views.

## Langfuse cloud

Set `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` in `.env`, then `pip install -e ".[observability]"`. Trace shape: cycle = trace, round = span, each L1/L2/L3/critique call = LLM observation, backend match = span. A local shadow under `cycles/{cycle_id}/langfuse/` mirrors what's sent (with an id-map for cross-reference); a backfill helper replays historical traces uploaded later.

## MLflow sink

`MLFLOW_ENABLED=true` (default false) logs each round as an MLflow run under `archive/mlruns/`, experiment `{tenant_id}/{cycle_id}`. Installs alongside the file + Langfuse sinks.

## Display convention — `⚠ … ↳`

Optimizer findings (validation failures, anomaly flags, elimination, degradation) surface as two lines:

```
⚠ <fact, in data terms>
  ↳ <action, in optimizer terms>
```

Line 1 names the observation, line 2 the repair or consequence. A finding without a `↳` is a bug.

```
⚠ llm_only.model = 'gpt-4o' ∉ [openai/gpt-oss-20b]
  ↳ scored 0; L2 brief will name this value
```

The structured finding lives in `dashboard.json::last_scoring_metadata` — one source, per-surface rendering.

**Per-sample annotation order** — one `⚠ {step}: {message}` per diagnostic warning (always), then exactly one status annotation from this exclusive set:

- `🔄 cache had warnings → reran`
- `🔬 rerun still degraded → resampled N fresh calls`
- `🔀 query degrades ≥50% historically → using cached answer`
- `⚠ stale-data ladder exhausted → still degraded`
- `↩ pipeline warning observed; X/Y toward rerun trigger` — only when no fatal warning fired

Suppressing `↩` under a fatal warning is load-bearing: a fatal warning means the candidate is dead, so "1/3 toward rerun" would falsely promise more data. (The ladder's rescue step is *samplescan rescue*, never "probe".)

## Reading what L2 wrote

In `cycles/{cycle_id}/rounds/round_NNNN.json`:

- `opt_search_point.l1_layout` — per-slot signal-name layout L2 stamped. **The** thing to read: it and `l1_overrides` are the only two surfaces L2 can move, so a fire that changed neither bought nothing (`review.md`'s `l2_targets_l1_surface`).
- `opt_search_point.l1_overrides` — L1 runtime knobs (creativity, n_variants).
- `nodes.l2_context.input.prompt` / `.output` — rendered L2 prompt (incl. the field catalogue) / raw JSON.

`opt_search_point.task_context` is operator-authored framing that L2 reads and cannot write — a change there came from the operator, not the loop. There is no `probe_round_commitment` decision: probe rounds are not wired.

Deep dive: [`../developer/l2-internals.md`](../developer/l2-internals.md).
