# Observability

PromptPotter emits structured trace events for every optimizer LLM call, every backend match, and every escalation check. Traces go two places: local files (always, under the cycle directory) and optionally Langfuse cloud.

---

## Local event log

Every observability event is appended to `campaigns/{cycle_id}/langfuse/events.jsonl`. This is a pure mirror — nothing reads it for state reconstruction; it's there for debugging and post-hoc inspection. Each line is a JSON object with phase, event type, round, and payload.

Phase events (`init`, `l1_generate`, `l1_evaluate`, `refine_strategy`, `modify_plan`, `escalation`, `zero_signal_filter`, `scoring_set`) emit `enter` / `exit` pairs. Mid-phase events carry whatever the emitter chose to include.

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

---

## Live monitoring (operator drill-in)

The cleanest live-monitoring setup for an operator running `python -m promptpotter optimize`:

1. **Open `campaigns/{root_cycle_id}/dashboard.json` in an editor that auto-reloads.** This is the live scalar state — phase, round, candidate, query, baseline / best / current accuracy, in-flight query payload, and `current_round.nodes` (the per-round node I/O snapshot, including per-candidate per-sample HIT/MISS lines under `l1_score.output.candidates[].samples`). Rewritten on every callback (per-query to per-candidate cadence). For forked cycles, telemetry binds to the **family root** (the cycle with no `parent_cycle_id`); the active fork is identified by `dashboard.json::cycle_id` so a single tail covers the whole family.

2. **Watch CLI stdout in the terminal that's running `optimize`.** [`presentation/views/live.py::LiveDisplay`](../../promptpotter/presentation/views/live.py) prints per-query HIT/MISS lines, per-candidate summaries, and round-complete banners — same data dashboard.json carries, but in narrative order with tqdm progress bars.

3. **Drill into peer files when the dashboard isn't enough.** Layout splits into three bands per cycle dir, plus three sibling-group dirs at the family root:

   - **Family telemetry** at `campaigns/{root_cycle_id}/`, shared across all forks of the family: `dashboard.json` is the live scalar state.
   - **Per-cycle operator audit** at the cycle dir's top level (root cycle and every fork has its own):
     - `index.json` — campaign metadata + trial index + the `final` block (best / baseline / stop_reason / winner) once the cycle finishes.
     - `log.md` — derived markdown digest, regenerated on every round-complete and at finalize. Status block, per-round critique / L2 directive / changes, hard-samples heatmap (when sorter enabled), final winner. Pure render over `index.json` + `trials/`; safe to delete and recompute.
     - `review.md` — per-cycle review surface (M10).
     - `trials/trial_NNNN.json` — per-round optimizer checkpoint (critique text, l2_directive, escalation state).
     - `prompts/` — prompt-version archive (per L2 mutation).
     - `langfuse/` — LLM trace mirror (debug drill-in: events.jsonl, traces, observations, scores, datasets, state.json).
   - **Per-cycle internals** at `{cycle_dir}/.runtime/` — opaque to operators, projection-owned:
     - `ledger.jsonl` — RunLedger spine (every fact for this cycle).
     - `streams/round_NNNN_p_best.jsonl` — per-query PoBB telemetry (rendered as a sparkline inside `log.md`).
     - `cache/rounds/round_NNN.json` — per-round LLM action audit.
     - `cache/candidates/round_NNNN.json` — pre-scoring candidate checkpoint (resume state); overwritten next round.
     - `archived/resumed_at_<ts>/` — `--from <round>` rewind sweepup.
   - **Sibling cycles** at the family root, split by kind:
     - `forks/{cycle_id}/` — `--fork-on-divergence` operator-divergence forks.
     - `diag/{cycle_id}/` — diagnostic-BFS auto-spawned siblings.
     - `sweeps/{batch_id}/` — sweep batches; carries `index.json` + `summary.md` for the batch and `forks/{cycle_id}/` per payload.

`optimize_result.json` and `hard_samples.json` were folded away earlier: the final-run summary lives at `index.json::final`, and the hard-samples heatmap is rendered as a section inside `log.md`.

### Alternatives to the dashboard.json-tail workflow

- **`/potter-run` skill** ([`.claude/skills/potter-run/SKILL.md`](../../.claude/skills/potter-run/SKILL.md)) — chat-driven operator session that preps configs, runs `optimize`, reads dashboard.json + trials between rounds, and summarizes. Combines well with the dashboard.json tail.
- **Notebook** (`notebooks/optimization_campaign.ipynb`) — drives the same loop in-process; live-phase per-query rendering is currently notebook-only.
- **Webapp** — minimal read-only dashboard planned on top of the FastAPI surface (`promptpotter/main.py`); zero code today.

---

## Display conventions

Canonical visualization patterns every PromptPotter entry point (notebook, CLI, `/potter-run` skill, API, webapp) renders identically. One pattern, learned once.

### Per-query annotation order

Per-query annotations render in this order, with a **mutual-exclusion rule**:

1. `⚠ {step}: {message}` — one line per diagnostic warning (always renders).
2. One status annotation from this exclusive set:
   - `🔄 cache had pipeline warnings → reran; result: …` — retried after cached degradation
   - `🔬 cache had warnings + rerun still degraded → resampled N fresh calls …` — samplescan rescue
   - `🔀 query degrades ≥50% of the time historically → using cached answer …` — switched out
   - `⚠ entire stale-data ladder exhausted → still degraded …` — persistently degraded
   - `↩ pipeline warning observed; X/Y occurrences toward rerun trigger …` — degraded observed, **AND** no fatal warning on this query

**Do not use the bare word "probe" here.** The stale-data ladder's rescue step is called "samplescan rescue" — "probe" is reserved for the L2/L3 **probe round** mechanism (round-scoped action targeting queries with recurring pipeline warnings), which is a completely different thing.

The fatal-warning suppression of `↩ …` is load-bearing: when a fatal warning fires, the candidate is dead on that query, so a counter reading "1/3 toward rerun" would falsely suggest more data is coming.

### The `⚠ … ↳` finding-and-addressed-by convention

PromptPotter surfaces optimizer findings — validation failures, anomaly flags, elimination signals, empty-output candidates, degradation escalations — with a two-line shape:

```
⚠ <what was found, in data terms>
  ↳ <what happens next, in optimizer terms>
```

Line 1 names the observation: *who, what, where*. Line 2 names the repair or consequence: *what the system will do about it*. A finding without a `↳` line is just noise; every ⚠ must be paired with an action.

Canonical example — validation failure:

```
⚠ llm_only.model = 'gpt-4o' ∉ [openai/gpt-oss-120b]
  ↳ scored 0; L2 directive will name this value
```

The first line is a structural fact about the candidate's configuration. The second line tells the reader the signal has been absorbed by the feedback cycle — no human intervention required.

### Entry-point adoption

| Surface | Status | Location |
|---|---|---|
| Notebook | Implemented | `promptpotter/presentation/views/live.py` (`LiveDisplay`) |
| CLI | Live output during `optimize`; post-mortem reads happen by opening `campaigns/{cycle_id}/log.md` | `promptpotter/presentation/cli/` |
| API | Return the `⚠ / ↳` pair as a structured pair in JSON so frontends render identically | `promptpotter/presentation/api/` |
| Webapp | Planned | — |

When adding a new self-healing mechanism, escalation check, or any other finding the optimizer surfaces to the user, use this convention rather than inventing a new format.

### Anti-patterns

- Do not omit the `↳` line.
- Do not render raw tracebacks or backend error bodies in the ⚠ slot — digest first.
- Do not stack multiple ⚠ lines without their own `↳` partners.

### Source of truth

`dashboard.json::last_scoring_metadata` holds the structured finding. Each entry point reads from there and formats using this convention — the data lives in one place, only the rendering is per-surface.
