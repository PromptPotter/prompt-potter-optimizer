# L4 outer loop + the shared in-process execution seam

> **Status:** design — resolves the open Lane-C3 decisions (`roadmap.md` § Connectors + L4). Forward-looking; the code lands in C3. Once shipped, the past-tense facts move to `docs/concepts/optimizer-of-the-optimizer.md`.
>
> **Scope:** CLI / headless only — no webapp surface yet (that's a later lane). The outer loop is a normal `python -m promptpotter new promptpotter-self` invocation.
>
> **Prerequisite:** [`fitness-comparability.md`](fitness-comparability.md) — the outer fitness here reads inner-campaign improvement over samples; that signal must be subset-invariant (θ-based) first, or the outer loop inherits the per-candidate drift distortion. Build comparability before this.

## Why

L4 — PromptPotter optimizing its own meta-prompts — has been a deliberate skeleton: the `promptpotter` connector, `datasets/promptpotter-self/`, the `Connector.execution` enum, and `concepts/optimizer-of-the-optimizer.md` all exist, but the inner-cycle run raises `NotImplementedError` (`infrastructure/backend.py::BackendClient.run_query`, the `_execution != "remote_http"` arm). C3 left three things open: **how** `in_process` executes (localhost vs in-process), **what** the outer optimizer prompts are, and **which** value the outer composite fitness uses. This spec decides all three, and folds in a second feature that rides the same seam.

The decisions:

1. **In-process recursion, one process, zero networking.** The outer loop runs inner campaigns as direct `run_optimization(...)` calls — never a second PromptPotter server, never an HTTP self-call.
2. **One seam, two features.** The same `in_process` arm powers both the `promptpotter` connector (inner-cycle recursion) **and** a new in-process `llm_only` connector, so the basic LLM-only case no longer needs the TermNorm server downloaded/running.
3. **Specialized outer prompts.** The outer optimizer gets its own meta-aware prompt set that emits `PromptTemplate` edits, not the standard task-tuned loop.
4. **A cleverer outer fitness** than "delta within budget."

## 1. The shared in-process execution seam — one seam, two features

`Connector.execution` (`connectors/protocol.py`, `Literal["remote_http", "in_process"]`) already dispatches in `run_query`; today the non-HTTP arm raises. Replace the raise with a dispatch to a **connector-supplied in-process runner** — `Connector.in_process_run(query, payload) -> dict[str, Any]` returning the result shape the scorer (`application/scoring/sample_measurement.py`) already consumes (`predicted` + `pipeline_data`). The HTTP arm is unchanged. This keeps the rule the boundary already follows: **dispatch on the declared mode, never the connector name** (`connectors/CLAUDE.md`).

Two connectors implement that runner:

### Feature A — in-process `llm_only` connector (drop the TermNorm dependency)
A new connector (`connectors/llm_only.py`) whose runner calls the optimizer LLM client directly with the rendered prompt and returns `{predicted, pipeline_data: {terminated_at: "llm_only"}}`. No `/matches`, no session, no second server. This **deliberately re-introduces** the in-process LLM-only execution that `LLMOnlyAdapter` (deleted) once did — but as a first-class connector on the canonical `execution` seam, not a sidecar adapter routed around the backend. The reversal is justified by distribution comfort: the whitelabeled app ships runnable for the basic case with nothing to download. It also directly serves the prompt-iteration exit gate, which already requires `rounds_to_95 ≤ 5` **on `llm_only`** AND TermNorm under the same `l1_generate_hash` (`roadmap.md` § Prompt-iteration framework) — that gate presumes a runnable `llm_only`.

### Feature B — `promptpotter` connector inner-cycle runner (L4)
The same seam, but the runner runs a full inner campaign (§2) and returns the three proxy metrics (`first_round_delta`, `after_N_rounds_delta`, `rounds_to_N`) in the result `pipeline_data`. The wire shapes already exist: `promptpotter_wire_adapter` produces `{query, meta_prompt_overrides}`; `PromptPotterSession` no-ops the session; `_extract_experiment` reads the `tasks` list. Only the runner is new.

**Reuse:** `connectors/protocol.py::Connector`, the `CONNECTORS` data-row registry (`connectors/__init__.py`, no `register()`), the existing `promptpotter.py` hooks. Adding `llm_only` is one new file + one registry row + an `in_process_run` field on the protocol.

## 2. In-process recursion isolation (the hazards the skeleton doesn't address)

`run_optimization` (`application/runner/entry.py`) is a plain async function already called from many sites; calling it for an inner campaign in one process is feasible but **not free**. Two isolations are mandatory:

- **Each inner cycle runs in its own `asyncio.Task`.** Three ContextVars isolate **per task, not per call** — `_CYCLE_LEDGER` and `_CURRENT_ROUND` (`infrastructure/llm/models.py`) and `_ABORT_CHECK` (`infrastructure/llm/rate_limit.py`). A naïvely-nested `await run_optimization(...)` in the outer's own task would `set`/`reset` these and clobber the outer's ledger binding, round stamp, and abort predicate. The runner already binds them — `build_run_observers` (set) / `drain_all` (reset) in `run_observers.py`, and `set_abort_check` (defined in `rate_limit.py`, called from `entry.py`); the connector runner MUST spawn the inner cycle in a fresh task so each gets its own ContextVar copy.
- **Isolated stores under `.runtime/inner/`.** Inner mints must not write the outer's `active_session.json` pointer or trip the capacity-1 `JobRegistry`. The inner cycle gets a sandboxed `build_stores(...)` rooted at the outer cycle's `.runtime/inner/`, so its `cycles/` tree, ledger, and dashboards are self-contained and don't pollute the outer campaign's listing or the SSE stream.

The process-global rate limiter is shared — acceptable, but inner spend competes with outer for TPM/RPM (flagged, not blocked; `RateLimiter` per-cycle isolation is a known forward item).

**Execution home.** The outer loop is a CLI/headless `new`/`resume` invocation. It is **not** hosted in the read-only uvicorn app (`promptpotter.main:app`), which is capacity-1 by design. The existing uvicorn on `:8001` *observes* both the outer cycle and the sandboxed inner cycles via the file tree (`dashboard.json`) — no second optimizer process, no HTTP self-call. This is the resolution of C3's "localhost endpoint vs in-process dispatch" fork: **in-process dispatch under `.runtime/inner/`.** (The localhost-endpoint option is retained only as the future hosted/multi-tenant worker mode — a new `execution` value, no core-loop edit.)

## 3. Specialized outer meta-prompt set

The outer cycle is a PromptPotter cycle, but its optimizer reasons about a *different* object than the inner: it mutates whole meta-prompt templates, judged by meta-evidence (mode-collapse, parse-fail rate, candidate stratification, proxy-lift), not task answers. So it gets its **own** prompt set rather than the standard task-tuned loop.

- **New optimizer prompts** (meta-aware): `l1_generate`, `l1_critique`, `l2_context` first; `l3_plan` and `checkin` lag (same evidence-gated scoping the inner loop uses). The outer `l1_generate` emits a **`PromptTemplate` edit** — the six prose fields keyed by inner node (`l1_generate`/`l1_critique`/`l2_context`/`l3_plan`, the four nodes in `datasets/promptpotter-self/pipeline.json`) — with its own output schema registered in `dispatch/schemas.py::OPTIMIZER_RESPONSE_MODELS`.
- **Mechanism — per-campaign optimizer-pipeline selection.** Today the optimizer prompt set is global: `OPTIMIZER_PIPELINE_PATH` is a module constant in `dispatch/llm_call/prompts.py` whose manifest loader (`_load_optimizer_manifest`) is `lru_cache`-d. The outer campaign instead resolves a **new `datasets/_optimizer_meta/pipeline.json`** (the inner keeps `datasets/_optimizer/`). This mirrors how datasets already own their overlays — the optimizer pipeline becomes a per-campaign resolution (carried on the campaign/session, defaulting to `_optimizer/`), not a global constant. Cache-invalidation: the lru-cache keys on the resolved path, so two pipelines coexist without cross-talk.
- The meta-evidence panels the outer `l1_generate`/`l1_critique` read are the existing round-trace signals (parse-fail rate, candidate stratification/entropy, per-candidate deltas) already computed by the sweep/round machinery — surfaced as outer injections, not re-derived.

## 4. Outer composite fitness — the "much cleverer" score

The three landed proxies stay the raw signals (`datasets/promptpotter-self/campaign.json::scoring` already composes them: `0.4·first_round_delta + 0.4·after_N_rounds_delta + 0.2·(1/max(rounds_to_N,1))`). The naïve reading — "improvement within a fixed budget" — is too lossy: it is origin-strength biased, endpoint-blind, and trusts one noisy inner campaign. Enrich, don't replace:

- **Normalized headroom lift** — `(best − origin) / (target − origin)`, capped at 1.0, with per-task `target` (`0.80` in `inner_tasks.json`). Removes origin-strength bias so a meta-prompt config compares across inner tasks of different difficulty.
- **Area-under-lift-vs-budget** — rewards a config that climbs fast then plateaus over one that crawls; captures the trajectory finesse a two-endpoint delta discards. **Data-shape gap (must resolve):** per-round spend is *not* materialized — `index.json` carries no spend; `dashboard.json::spend` is a single cycle-total. Per-round (hence cumulative-by-round) spend IS reconstructible from the cycle ledger: `TokenUsageRecord` carries a `round` field in `.runtime/ledger.jsonl`. **Decision:** the fitness reads the ledger to build cumulative-spend-by-round for the AUC; a per-round spend rollup on the dashboard projection is the durable follow-up (not required for C3).
- **Panel aggregation** — `mean lift − λ·std` across the inner-task panel (the four `gsm8k-small/seed-*`). Rewards generalization, penalizes a config that wins one seed and loses another — the same anti-mono-bias principle as the measurement provenance grade.
- **PoBB-decisive promotion** — reuse `pobb/elevation.py::elevate_to_decisive` at the **outer** level: keep topping up inner campaigns (one more inner run) until the *ranking* of meta-prompt configs is statistically decisive, not just the point score. This is the finesse the budget cap otherwise throws away — confidence, not a single noisy number.
- **Subset-invariant, grade-A inner signal** — the proxies measure the inner loop's **θ-based** improvement (per [`fitness-comparability.md`](fitness-comparability.md)), not raw accuracy on a drifting subset, so `best − origin` is comparable across inner tasks; and they read deliberate, grade-A measurements (the provenance grade, `domain/measurement_provenance.py`), never connector noise. The outer loop inherits the inner's comparable, clean-measurement guarantee — which is why comparability is a prerequisite.

Implementation maps to the roadmap composite-fitness phases: **P2** (per-candidate rollup + scatter) and **P3** (`compile_post_aggregate_fitness(formula)` + `campaign.json::scoring_post_aggregate`). The normalized-AUC / panel-aggregate / variance-penalty terms are the post-aggregate formula; the three proxies remain the per-sample primitives. A pure `outer_fitness` module computes them from a finished inner cycle's `index.json` (`origin_accuracy`, `best_accuracy`, `rounds[].accuracy`, `n_rounds`, `final`) plus the ledger-reconstructed spend.

## 5. Non-goals + validation

**Non-goals (this spec):** any webapp surface (CLI/headless only); competitor/publication numbers; mutating the inner `checkin` (off the operator surface — deferred). The full inner-cycle code lands in C3; this spec defines it.

**Validation gate** (C3 exit, `roadmap.md`): `proxy_lift_corr ≥ 0.6` over ≥4 paired branches — the empirical proof the cheap proxy predicts real lift. The `outer_fitness` module is the artifact that computes it. The `llm_only` connector additionally unblocks the prompt-iteration exit gate's `llm_only`-side `rounds_to_95 ≤ 5`.

## Implementation order

Sequenced by dependency and standalone value — each slice ships something usable before the next starts.

1. **Shared `in_process` seam + `llm_only` connector (independent feature, ship first).** Add `Connector.in_process_run`; replace the raise in `run_query` with a dispatch to it; write `connectors/llm_only.py` whose runner calls the existing `get_llm_client(provider).chat(...)` with the rendered prompt and returns the `{"data": …}` shape `measure_sample` already parses; register it (`CONNECTORS` row); add an `llm_only` in-process dataset. **Reuses unchanged:** `measure_sample` → `run_query` dispatch → `terminal_ranking`/`extract_item_label`, the LLM client + provider registry, the node overlay (model/provider). **Done when:** an `llm_only` campaign scores end-to-end with no TermNorm server, and the exit gate's `llm_only`-side `rounds_to_95` is runnable. This slice carries zero L4 risk and delivers the distribution-comfort win on its own.
2. **`promptpotter` inner-cycle runner + isolation.** Same seam; `in_process_run` runs an inner campaign via `run_optimization` in its **own asyncio task** under a `.runtime/inner/` store sandbox (§2), returning the three proxy metrics. **Done when:** `new promptpotter-self` runs end-to-end (no `NotImplementedError`); inner cycles appear under `.runtime/inner/` without touching the outer's active-pointer.
3. **Specialized outer prompt set + per-campaign optimizer pipeline (§3).** Make the optimizer pipeline per-campaign-resolved; add `datasets/_optimizer_meta/` with meta-aware prompts + output schema(s). **Done when:** the outer `l1_generate` emits `PromptTemplate` edits and the outer campaign resolves the meta set, the inner the standard set.
4. **Enriched outer fitness (§4).** The `outer_fitness` module (normalized AUC-lift, panel mean−λ·std, ledger-reconstructed per-round spend), PoBB-decisive via `elevate_to_decisive`, wired as the P2/P3 post-aggregate formula. **Done when:** `proxy_lift_corr ≥ 0.6` over ≥4 paired branches — the C3 exit gate.

Slice 1 stands alone (no L4 needed); 2 depends on 1's seam; 3 and 4 depend on 2 producing inner campaigns to optimize and score.

## Named seams (for the implementing PR; not edited by this spec)

| Concern | File |
|---|---|
| `in_process` dispatch (replace the raise) | `promptpotter/infrastructure/backend.py::BackendClient.run_query` |
| `in_process_run` on the protocol | `promptpotter/connectors/protocol.py` |
| New `llm_only` connector + registry row | `promptpotter/connectors/llm_only.py`, `connectors/__init__.py` |
| `promptpotter` inner-cycle runner | `promptpotter/connectors/promptpotter.py` |
| Recursion entry + task isolation | `application/runner/entry.py::run_optimization`, ContextVars in `infrastructure/llm/models.py` + `rate_limit.py` |
| Sandboxed inner stores | `infrastructure/store/stores.py::build_stores` → `.runtime/inner/` |
| Per-campaign optimizer pipeline | `application/optimization/dispatch/llm_call/prompts.py` (`OPTIMIZER_PIPELINE_PATH` → resolved), new `datasets/_optimizer_meta/pipeline.json` |
| Meta output schema(s) | `application/optimization/dispatch/schemas.py::OPTIMIZER_RESPONSE_MODELS` |
| Outer fitness | new `outer_fitness` module; reuses `pobb/elevation.py::elevate_to_decisive`, reads `index.json` + `.runtime/ledger.jsonl` |
