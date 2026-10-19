# Observability

Every optimizer LLM call, backend match, and escalation check emits a structured trace — to local files under the cycle dir unless `OBS_ENABLED` is off, and to Langfuse cloud or MLflow when configured. The sinks are fanned out from the ledger (`infrastructure/tracing/bridge.py::TracingProjection`).

## What's traced, and where

Phase events (the bench's `domain/phases.py::CampaignPhase`, and each optimizer's own, declared on the llm-node member that runs in it (`LlmNode.phase`) — potter's are `escalation/state.py::PotterPhase`) emit `enter`/`exit` pairs into the per-cycle ledger. `langfuse/events.jsonl` is a pure mirror — nothing reads it for state reconstruction.

| Source | Event | Payload |
|--------|-------|---------|
| L1 Generate | LLM call | rendered optimizer prompt, candidate outputs, token counts |
| L1 Critique | LLM call | critique optimizer prompt, structured output |
| L2 Refine | LLM call | refinement optimizer prompt (incl. the L1 field catalogue), parsed transition |
| L3 Plan | LLM call | plan template (axes_digest + L2 history + pipeline + runtime failures), new plan |
| Backend match | Span | query, params, result, `diagnostics.warnings` — declared (`TracingProjection.emit`, `tracing/events.py::QueryScoreStart` / `QueryNodeSpan` / `QueryScoreEnd`); nothing in the loop calls it |

## The wall clock, and where the claim stops

`index.json::final.wall_clock` is the cycle's own clock, folded from the ledger at finalize (`ledger_scan.py::scan_ledger_wall_clock`) and rendered by `review.md` § Wall clock. It is banked rather than derived on read because no round file carries a timestamp and the records it is folded from are compactable. **A resumed cycle's clock is its LAST launch's** — a round an earlier launch closed carries no seconds rather than a wrong number, so a result quoting a clock quotes an unbroken run. **A campaign's clock is its result's** (`campaigns/{id}/result.json::cost.launches`): one clock per launch, folded across every cycle that launch ran on the campaign's line, and `ArmCost.worked_s` sums them less each launch's origin gate and unworked time — the number a head-to-head prices a lift in.

**Two denominators, and they are not interchangeable.** `phase_s` is CLOCK, keyed by phase: the brackets do not nest, so the legs sum and each is a real share of `elapsed_s`. `worked_s` is summed CALL time per spend bucket and billing node, off `TokenUsageRecord.duration_s`: concurrent cells overshoot the clock and replayed calls are excluded, so it says what the search *worked*, never what share of the run a node held. Quote `phase_s` for a share; quote `worked_s` for a cost.

**Every fresh call is a span on the clock, so no call needs a bracket to be counted.** A bill is stamped when it lands, so a call spans `[timestamp − duration_s, timestamp]`, and `unbracketed_call_s` is the CLOCK those spans held outside every phase bracket and gate, keyed like `worked_s`. It is decided by interval arithmetic and never by node name, so a node that runs between brackets — the round's critique, a non-escalating L2 — is attributed without joining the phase vocabulary, and a call inside its own bracket is not counted twice. `unattributed_s` is what no bracket, gate or fresh call held.

**What we can publish is `ledger open → first improvement`, fully decomposed. What we cannot publish is `clean machine → first improvement`.** Installing the package, pulling an image, materializing a benchmark's rows and `init_services` all run before the ledger's first record, are observed by nothing, and are unrecoverable after the fact — so `elapsed_s` starts at the ledger, and the `init` leg beside it is preflight plus cycle construction, which reads under two seconds. Anyone quoting that leg as a setup measurement is out by orders of magnitude. `ORIGIN` also fires before `INIT` on every ledger, so the legs are not in reading order.

`gate_s` is the one HUMAN leg — time held at a gate, the origin's or a round's arm gate — and is never folded into a machine one. `unworked_s` is the opposite correction: seconds cells were not *allowed* to spend (machine suspend, or queued behind the shared limiter), summed off each cell's own envelope, and `None` where no cell ran under one. A headline counting a suspended box as work is not publishable, which is why absent and zero stay apart.

## Per-sample race stream

An optimizer's eliminator emits a per-sample standing for every candidate as a `race_standing` record naming its manifest `member` — potter's PoBB (`pobb`) as a Posterior-of-Being-Best — on four channels:

| Channel | Path | Format |
|---|---|---|
| Live dashboard | `dashboard.json::current_round.racing` — `{member, current_id, n_samples, top}`, null before a reading | a top-5 list, leader first |
| Run readout | `cycles/{cycle_id}/readout.log` at every entry point; the CLI and a notebook passing `readout_sink` also see it on stdout | one line per decision-grade reading as the race runs — `pobb: P(best)=44.0% @ q14  vs hardest=…`; a block race prints what each close decided instead — `block 1/4: 6 raced · cut C1.3 · 5 survive`. No cross-arm P(best) line at round close: each reading is one arm against its own priors |
| Append-only stream | `cycles/{cycle_id}/.runtime/streams/round_NNNN_{member}.jsonl` | `{round, sample_idx, current_id, n_samples, p_best, p_best_delta}` |
| Round digest | `log.md` § P(best) trajectory | per-candidate sparkline + final %, rows named by label |

The JSONL stream is canonical replay; the dashboard fields and the sparkline are derived views.

## Langfuse cloud

Set `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` in `.env`, then `pip install -e ".[observability]"`. Trace shape: cycle = trace, round = span, each L1/L2/L3/critique call = LLM observation, backend match = span. A local shadow under `cycles/{cycle_id}/langfuse/` mirrors what's sent (`events.jsonl`), with an id-map for cross-reference (`state.json`).

## MLflow

`MLFLOW_TRACKING_URI` names the operator's MLflow server, and unset — the default — MLflow receives nothing. One server takes both halves (`infrastructure/tracing/mlflow_server.py`), which is what lets a prompt version name the run that won it. It installs from `.[observability]` as **`mlflow-skinny`**, the client alone: the server is the operator's own process — one running beside the box is `http://127.0.0.1:5000` — and its credentials are MLflow's own environment variables.

**How the run went — rounds as runs.** `MLflowSink` opens the experiment `{tenant_id}/{cycle_id}` and logs each closed round as a run in it: accuracy, the total scored, whether it improved, and the winner's lineage id.

**What it found — the winner as a prompt version.** `register_export` (reached through `application/runner/output.py::publish_export`) registers each finished cycle's winner as a version of the prompt `{tenant_id}.{dataset_name}`. The version's text is `export.json` rendered (`PromptExport.render()`), read back off disk, so the registry never holds a prompt the file does not; its tags are that file's identity, the node config the winner ran under and the bench headline on one line; and it is linked to the run of the round that crowned it.

- **One version per cycle and prompt text.** A bench graded after the run restates that version's tags in place; a cycle that ends on a different prompt takes the next version.
- **No alias is ever set or moved.** Promotion is the host's swap ([`../specs/roadmap.md`](../specs/roadmap.md) § Application radius), and a reader that wants the newest loads `prompts:/<name>@latest`.
- **Not registered:** a diagnostic cycle's winner, and that of every cycle inside an L4 inner sandbox — those are the recursion's measurements, not an answer anyone asked for.
- **A failed write is logged and never fails the run, and an unreachable server cannot hold one**: the client is bound to one attempt and ten seconds, where MLflow's own defaults retry for minutes. The offline run blanks the setting.

What is still to build between the two tools, in order, is [`../specs/mlflow-interop.md`](../specs/mlflow-interop.md).

**A primary span convention is chosen before a third sink is added** — owned by [`../research/external-constraints.md`](../research/external-constraints.md) § TOOL; both sinks here sit downstream of the two it names.

## Display convention — `⚠ … ↳`

Optimizer findings (validation failures, anomaly flags, elimination, degradation) surface as two lines:

```
⚠ <fact, in data terms>
  ↳ <action, in optimizer terms>
```

Line 1 names the observation, line 2 the repair or consequence. A finding without a `↳` is a bug.

```
⚠ llm_only.model = 'gpt-4o' ∉ [openai/gpt-oss-20b]
  ↳ scored 0 (no backend call); the next l1_generate reads it in l1_wounds
```

The structured finding is written to the round audit file (`AuditTrailProjection`) and read back via `useRoundNodes` when an operator drills in.

**Per-sample annotation order** (`application/views/render/sample.py::fmt_query_result`) — one `⚠ {step}: {message}` per diagnostic warning (always), then at most one status annotation from this exclusive set, first match wins:

- `🔄 cache had pipeline warnings → reran`
- `🔀 query degrades ≥50% of the time historically → using cached answer`
- `⚠ cached failure was token-budget exhaustion + rerun max_tokens ≤ cached output → skipped LLM rerun; marked fatal`
- `⚠ entire stale-data ladder exhausted → still degraded`
- `↩ pipeline warning observed; X/Y occurrences toward rerun trigger` — only when the sample is not deprecated by a fatal warning

Suppressing `↩` under a fatal warning is load-bearing: a fatal warning means the candidate is dead, so "1/3 toward rerun" would falsely promise more data.

## Reading what L2 wrote

In `cycles/{cycle_id}/rounds/round_NNNN.json`:

- `optimizer_state.payload.memory.steer.l1_generate.config` — everything L2 moved, as one node overlay: `layout` (the per-slot signal names, whole) and the call settings (`temperature`, `n_variants`). **The** thing to read: a fire that changed nothing here bought nothing (`review.md`'s `l2_targets_l1_surface`).

In its audit twin `cycles/{cycle_id}/.runtime/cache/rounds/round_NNNN.json` — the round file carries no `nodes`:

- `nodes.l2_context.input.template_fields` + `.variables` / `.output.response` — what the L2 prompt was rendered from (incl. the field catalogue) / the parsed answer.

The campaign's `task_context` (`Cycle.framing`, from the front matter of the dataset's `task.md`) is operator-authored framing that L2 reads and cannot write — a change there came from the operator, not the loop. There is no `probe_round_commitment` decision: probe rounds are not wired.

Deep dive: [`../developer/dispatch-hub.md`](../developer/dispatch-hub.md).
