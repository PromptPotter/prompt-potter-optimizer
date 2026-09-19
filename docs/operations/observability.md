# Observability

Every optimizer LLM call, backend match, and escalation check emits a structured trace — to local files always (under the cycle dir), and to Langfuse cloud when configured.

## What's traced, and where

Phase events (`init`, `l1_generate`, `l1_score`, `refine_strategy`, `modify_plan`, `escalation`) emit `enter`/`exit` pairs into the per-cycle ledger; the `escalation` phase emits `rule_fired` whenever a post-round rule matches. `langfuse/events.jsonl` is a pure mirror — nothing reads it for state reconstruction.

| Source | Event | Payload |
|--------|-------|---------|
| L1 Generate | LLM call | rendered optimizer prompt, candidate outputs, token counts |
| L1 Critique | LLM call | critique optimizer prompt, structured output |
| L2 Refine | LLM call | refinement optimizer prompt (incl. the L1 field catalogue), parsed transition |
| L3 Plan | LLM call | plan template (axes_digest + L2 history + pipeline + runtime failures), new plan |
| Backend match | Span | query, params, result, `diagnostics.warnings` |
| Escalation rule firing | `escalation/rule_fired` | `{layer, rule_name, rule_priority, next_action, reason, signal_inputs}` |
| Stale-data protocol | Event | ladder step taken, resolution |

## The wall clock, and where the claim stops

`index.json::final.wall_clock` is the cycle's own clock, folded from the ledger at finalize (`ledger_scan.py::scan_ledger_wall_clock`) and rendered by `review.md` § Wall clock. It is banked rather than derived on read because no round document carries a timestamp and the records it is folded from are compactable. **A resumed cycle's clock is its LAST launch's** — a round an earlier launch closed carries no seconds rather than a wrong number, so a result quoting a clock quotes an unbroken run.

**Two denominators, and they are not interchangeable.** `phase_s` is CLOCK, keyed by `CampaignPhase`: the brackets do not nest, so the legs sum and each is a real share of `elapsed_s`. `worked_s` is summed CALL time per spend bucket, off `TokenUsageRecord.duration_s`: concurrent cells overshoot the clock and replayed calls are excluded, so it says what the search *worked*, never what share of the run a bucket held. Quote `phase_s` for a share; quote `worked_s` for a cost.

**What we can publish is `ledger open → first improvement`, fully decomposed. What we cannot publish is `clean machine → first improvement`.** Installing the package, pulling an image, materializing a benchmark's rows and `init_services` all run before the ledger's first record, are observed by nothing, and are unrecoverable after the fact — so `elapsed_s` starts at the ledger, and the `init` leg beside it is preflight plus cycle construction, which reads under two seconds. Anyone quoting that leg as a setup measurement is out by orders of magnitude. `ORIGIN` also fires before `INIT` on every ledger, so the legs are not in reading order.

`gate_s` is the one HUMAN leg — time held at the origin gate — and is never folded into a machine one. `unworked_s` is the opposite correction: seconds cells were not *allowed* to spend (machine suspend, or queued behind the shared limiter), summed off each cell's own envelope, and `None` where no cell ran under one. A headline counting a suspended box as work is not publishable, which is why absent and zero stay apart.

## Per-sample P(best) stream

PoBB emits a per-sample Posterior-of-Being-Best snapshot for every candidate, on four channels:

| Channel | Path | Format |
|---|---|---|
| Live dashboard | `dashboard.json::current_round.pobb` — `{current_id, n_samples, leader_prob, posterior_width, top}` | scalar floats + a top-5 list |
| CLI / notebook | stderr | `p_best q14: *c042* 44.0%▲ c017 28.4%▼ …` |
| Append-only stream | `cycles/{cycle_id}/.runtime/streams/round_NNNN_p_best.jsonl` | `{round, sample_idx, current_id, n_samples, p_best, p_best_delta}` |
| Round digest | `log.md` § P(best) trajectory | per-candidate sparkline + final % |

The JSONL stream is canonical replay; the dashboard fields and the sparkline are derived views.

## Langfuse cloud

Set `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` in `.env`, then `pip install -e ".[observability]"`. Trace shape: cycle = trace, round = span, each L1/L2/L3/critique call = LLM observation, backend match = span. A local shadow under `cycles/{cycle_id}/langfuse/` mirrors what's sent (with an id-map for cross-reference); a backfill helper replays historical traces uploaded later.

## MLflow sink

`MLFLOW_ENABLED=true` (default false) logs each round as an MLflow run under `traces/mlruns/`, experiment `{tenant_id}/{cycle_id}`. Installs from `.[observability]` alongside the file + Langfuse sinks, as **`mlflow-skinny`** — the sink calls tracking APIs only, and full `mlflow` caps `cryptography<50`. MLflow 3.15 put that local file tree in maintenance mode, so the sink sets `MLFLOW_ALLOW_FILE_STORE=true`; without it the first round raises, and the migration MLflow points at (`sqlite:///`) needs SQLAlchemy, which skinny omits.

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

The structured finding is written to the round audit file (`AuditTrailProjection`) and read back via `useRoundFile` when an operator drills in.

**Per-sample annotation order** — one `⚠ {step}: {message}` per diagnostic warning (always), then exactly one status annotation from this exclusive set:

- `🔄 cache had pipeline warnings → reran`
- `🔬 cache had warnings + rerun still degraded → re-measured fresh on pipeline defaults; result accepted`
- `🔀 query degrades ≥50% historically → using cached answer`
- `⚠ cached failure was token-budget exhaustion + rerun max_tokens ≤ cached output → skipped LLM rerun; marked fatal`
- `⚠ stale-data ladder exhausted → still degraded`
- `↩ pipeline warning observed; X/Y toward rerun trigger` — only when no fatal warning fired

Suppressing `↩` under a fatal warning is load-bearing: a fatal warning means the candidate is dead, so "1/3 toward rerun" would falsely promise more data. (The ladder's rescue step is *samplescan rescue*, never "probe".)

## Reading what L2 wrote

In `cycles/{cycle_id}/rounds/round_NNNN.json`:

- `opt_search_point.l1_layout` — per-slot signal-name layout L2 stamped. **The** thing to read: it and `l1_overrides` are the only two surfaces L2 can move, so a fire that changed neither bought nothing (`review.md`'s `l2_targets_l1_surface`).
- `opt_search_point.l1_overrides` — L1 runtime knobs (creativity, n_variants).
- `nodes.l2_context.input.prompt` / `.output` — rendered L2 prompt (incl. the field catalogue) / raw JSON.

`opt_search_point.task_context` is operator-authored framing that L2 reads and cannot write — a change there came from the operator, not the loop. There is no `probe_round_commitment` decision: probe rounds are not wired.

Deep dive: [`../developer/dispatch-hub.md`](../developer/dispatch-hub.md).
