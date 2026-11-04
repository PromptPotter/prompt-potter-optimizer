# Observability

Every optimizer LLM call and escalation check is a trace folded from the ledger (`infrastructure/tracing/bridge.py::TracingProjection`, off when `OBS_ENABLED` is false) and fanned out to sinks: the local mirror `cycles/{cycle_id}/langfuse/events.jsonl` (`tracing/file_sink.py`; nothing reads it for state), Langfuse (`tracing/langfuse_sink.py`) and MLflow (`tracing/mlflow_server.py`) when configured. Phase events (`domain/phases.py::CampaignPhase`, and each optimizer's own on `LlmNode.phase` — potter's `escalation/state.py::PotterPhase`) emit `enter`/`exit` pairs into the cycle ledger; each optimizer call carries its rendered prompt, output and token counts. A backend-match span is declared (`tracing/events.py::QueryScoreStart`) and nothing calls it: [`../specs/mlflow-interop.md`](../specs/mlflow-interop.md) owns that gap.

## The wall clock, and where the claim stops

`index.json::final.wall_clock` is folded from the ledger at finalize (`ledger_scan.py::scan_ledger_wall_clock`) and banked, since its source records are compactable. **A resumed cycle's clock is its LAST launch's**, so a quoted clock is an unbroken run. **A campaign's clock is its line's `launch_clock` records** (`application/runner/campaign_result.py::line_launches`); `domain/campaign.py::worked_seconds` sums them less origin-gate and unworked time — the number a head-to-head prices a lift in. A launch's clock is also a budget axis (`Budget.wall_clock_s`), afresh each launch.

**Two denominators, not interchangeable.** `phase_s` is CLOCK by phase — the legs sum to `elapsed_s`, so quote it for a share. `worked_s` is summed CALL time per bucket (`TokenUsageRecord.duration_s`), overshooting the clock under concurrency and excluding replays — quote it for a cost. `unbracketed_call_s` is clock a fresh call held outside every bracket and gate; `unattributed_s` what nothing held; `gate_s` the one HUMAN leg; `unworked_s` seconds cells were not *allowed* to spend, `None` where no cell ran under an envelope. **Publish `ledger open → first improvement`, never `clean machine → first improvement`**: install, image pull and `init_services` precede the ledger.

## Per-sample race stream

An eliminator emits a `race_standing` per candidate naming its manifest `member` (potter's `pobb`):

| Channel | Path |
|---|---|
| Live dashboard | `dashboard.json::current_round.racing` — top-5, leader first |
| Run readout | `readout.log` (and stdout for the CLI or a `readout_sink`) — one line per decision-grade reading |
| Canonical replay | the ledger's `RaceStandingRecord`s |
| Round digest | `log.md` § P(best) trajectory — derived sparkline |

## Langfuse

`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` in `.env`, plus `.[observability]`. Cycle = trace, round = span, each optimizer call = LLM observation. The local shadow `cycles/{cycle_id}/langfuse/` mirrors what is sent, with an id map in `state.json`.

## MLflow

`MLFLOW_TRACKING_URI` names the operator's server; unset, MLflow receives nothing. The client is `mlflow-skinny` from `.[observability]`; credentials are MLflow's own variables. One server takes both halves (`infrastructure/tracing/mlflow_server.py`): **rounds as runs** — `MLflowSink` logs each closed round under experiment `{tenant_id}/{campaign_id}/{cycle_id}`; **the winner as a prompt version** — `register_export` (via `application/runner/output.py::publish_export`) registers each finished cycle's `export.json` render as a version of `{tenant_id}.{dataset_name}`, one per cycle and prompt text. No alias is ever set or moved — promotion is the host's swap; the newest is `prompts:/<name>@latest`. Not registered: diagnostic cycles and every cycle inside an L4 sandbox. A failed write never fails the run. Still to build: [`../specs/mlflow-interop.md`](../specs/mlflow-interop.md); a primary span convention precedes a third sink ([`../research/external-constraints.md`](../research/external-constraints.md) § TOOL).

## Display convention — `⚠ … ↳`

Optimizer findings surface as two lines — the observation in data terms, then the repair or consequence in optimizer terms; a finding without a `↳` is a bug.

```
⚠ llm_only.model = 'gpt-4o' ∉ [openai/gpt-oss-20b]
  ↳ scored 0 (no backend call); the next l1_generate reads it in l1_wounds
```

The structured finding goes to the round audit file (`AuditTrailProjection`); per-sample annotation order is `application/views/render/sample.py::fmt_query_result`'s; `↩` (toward a rerun trigger) is suppressed under a fatal warning.

## Reading what L2 wrote

`rounds/round_NNNN.json::optimizer_state.slices.l2_memory.steer.l1_generate.config` is everything L2 moved; a fire that changed nothing there bought nothing. The rendered L2 prompt and parsed answer are `nodes.l2_context` in the audit twin `.runtime/cache/rounds/round_NNNN.json`. `task_context` is operator-authored. Deep dive: [`../developer/dispatch-hub.md`](../developer/dispatch-hub.md).
