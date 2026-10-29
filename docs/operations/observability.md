# Observability

Every optimizer LLM call and escalation check emits a structured trace — to local files under the cycle dir unless `OBS_ENABLED` is off, and to Langfuse or MLflow when configured. The sinks fan out from the ledger (`infrastructure/tracing/bridge.py::TracingProjection`).

## What's traced, and where

Phase events (`domain/phases.py::CampaignPhase`, and each optimizer's own on `LlmNode.phase` — potter's `escalation/state.py::PotterPhase`) emit `enter`/`exit` pairs into the cycle ledger; each optimizer call (L1 generate/critique, L2 refine, L3 plan) carries its rendered prompt, output and token counts. `langfuse/events.jsonl` is a pure mirror — nothing reads it for state. A backend-match span is declared (`tracing/events.py::QueryScoreStart`) and nothing calls it: [`../specs/mlflow-interop.md`](../specs/mlflow-interop.md) owns that gap.

## The wall clock, and where the claim stops

`index.json::final.wall_clock` is folded from the ledger at finalize (`ledger_scan.py::scan_ledger_wall_clock`) and banked, because no round file carries a timestamp and its source records are compactable. **A resumed cycle's clock is its LAST launch's**, so a quoted clock is an unbroken run. **A campaign's clock is its line's `launch_clock` records** (`application/runner/campaign_result.py::line_launches`); `domain/campaign.py::worked_seconds` sums them less origin-gate and unworked time — the number a head-to-head prices a lift in. A launch's clock is also a budget axis (`Budget.wall_clock_s`), starting afresh each launch.

**Two denominators, not interchangeable.** `phase_s` is CLOCK by phase — the legs sum to `elapsed_s`, so quote it for a share. `worked_s` is summed CALL time per bucket (`TokenUsageRecord.duration_s`), overshooting the clock under concurrency and excluding replays — quote it for a cost.

**Every fresh call is a span on the clock** (`[timestamp − duration_s, timestamp]`), so `unbracketed_call_s` — clock held outside every bracket and gate — is decided by interval arithmetic, never by node name. `unattributed_s` is what nothing held.

**We can publish `ledger open → first improvement`, fully decomposed; never `clean machine → first improvement`.** Install, image pull, row materialization and `init_services` precede the ledger and are observed by nothing, so the `init` leg is preflight plus construction only. `ORIGIN` fires before `INIT`, so the legs are not in reading order.

`gate_s` is the one HUMAN leg and is never folded into a machine one. `unworked_s` is seconds cells were not *allowed* to spend (suspend, queued behind the limiter), `None` where no cell ran under an envelope — absent and zero stay apart.

## Per-sample race stream

An eliminator emits a `race_standing` per candidate naming its manifest `member` (potter's `pobb`, a Posterior-of-Being-Best):

| Channel | Path |
|---|---|
| Live dashboard | `dashboard.json::current_round.racing` — top-5, leader first |
| Run readout | `readout.log` (and stdout for the CLI or a `readout_sink`) — one line per decision-grade reading; a block race prints what each close decided |
| Append-only stream | `.runtime/streams/round_NNNN_{member}.jsonl` — canonical replay |
| Round digest | `log.md` § P(best) trajectory — derived sparkline |

## Langfuse

`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` in `.env`, plus `.[observability]`. Cycle = trace, round = span, each optimizer call = LLM observation. The local shadow `cycles/{cycle_id}/langfuse/` mirrors what is sent, with an id map in `state.json`.

## MLflow

`MLFLOW_TRACKING_URI` names the operator's server; unset, MLflow receives nothing. The client is `mlflow-skinny` from `.[observability]`; credentials are MLflow's own variables. One server takes both halves (`infrastructure/tracing/mlflow_server.py`):

- **Rounds as runs** — `MLflowSink` logs each closed round under experiment `{tenant_id}/{campaign_id}/{cycle_id}`.
- **The winner as a prompt version** — `register_export` (via `application/runner/output.py::publish_export`) registers each finished cycle's `export.json` render as a version of `{tenant_id}.{dataset_name}`, linked to the crowning round's run. One version per cycle and prompt text; a later bench restates its tags in place.
- **No alias is ever set or moved** — promotion is the host's swap, and a reader wanting the newest loads `prompts:/<name>@latest`.
- **Not registered:** diagnostic cycles and every cycle inside an L4 sandbox.
- **A failed write never fails the run** — one attempt, ten seconds.

What is still to build is [`../specs/mlflow-interop.md`](../specs/mlflow-interop.md). **A primary span convention is chosen before a third sink is added** — owned by [`../research/external-constraints.md`](../research/external-constraints.md) § TOOL.

## Display convention — `⚠ … ↳`

Optimizer findings surface as two lines — the observation in data terms, then the repair or consequence in optimizer terms. **A finding without a `↳` is a bug.**

```
⚠ llm_only.model = 'gpt-4o' ∉ [openai/gpt-oss-20b]
  ↳ scored 0 (no backend call); the next l1_generate reads it in l1_wounds
```

The structured finding goes to the round audit file (`AuditTrailProjection`). **Per-sample annotation order** (`application/views/render/sample.py::fmt_query_result`): every `⚠ {step}: {message}` warning, then at most one status annotation, first match wins: `🔄` cache had warnings → reran · `🔀` historically degrades → cached answer · `⚠` budget-exhausted rerun skipped, fatal · `⚠` stale-data ladder exhausted · `↩` X/Y toward rerun trigger. **`↩` is suppressed under a fatal warning** — a dead candidate gets no promise of more data.

## Reading what L2 wrote

`rounds/round_NNNN.json::optimizer_state.slices.l2_memory.steer.l1_generate.config` is everything L2 moved; a fire that changed nothing there bought nothing. The rendered L2 prompt and parsed answer are `nodes.l2_context` in the audit twin `.runtime/cache/rounds/round_NNNN.json`. `task_context` is operator-authored, so a change there came from the operator. Deep dive: [`../developer/dispatch-hub.md`](../developer/dispatch-hub.md).
