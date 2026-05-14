# M12: Multi-Connector, Competitor Comparison, Webapp Phase 2

**Status:** Track 1 (foundation) shipped in `ed95509`; Tracks 2 + 3 in flight; Tracks 4 + 5 + 3.5 specced.
**Depends on:** M11 (Publication Benchmarks, Ablation Studies, Webapp Read-Only).

## Why

M11 delivered first benchmark numbers, ablations, and a read-only webapp on M9's hexagonal foundation + M10's tuned optimizer-prompts. M12 generalizes the connector, closes the publication with a competitor head-to-head, upgrades the webapp into a live control surface, runs the L4 self-optimization closure, and lands multi-objective fitness.

Three gaps M12 closes:

1. **Single-backend assumption.** `BackendClient` was concrete; the boundary now lives at `promptpotter/connectors/`. A second backend is one new file — but no second connector is registered, so "pluggable" is unfalsified.
2. **Publication lacks competitor comparison.** M11 produces our numbers; M12 adds cited competitors (or MIPROv2 reproduction if reviewers object).
3. **Webapp is read-only.** No launch, no live progress, no control.

## Tracks

### Track 1 — Multi-connector architecture *(foundation shipped; second connector pending)*

`Connector` lives at `promptpotter/connectors/protocol.py`, registry at `connectors/__init__.py`, TermNorm at `connectors/termnorm.py`. The four hooks bundled per `Connector` (`wire_adapter`, `session_factory`, `extract_experiment`, `resolve_ground_truth`) fold the previously-scattered `EXPERIMENT_EXTRACTORS` / `TRACE_GT_RESOLVERS` registries.

**Outstanding deliverables:**

1. **Second connector — `promptpotter/connectors/promptpotter.py`.** PromptPotter-as-backend. Detailed below. **Most urgent strategic item:** validates the connector boundary every other M12 deliverable rests on, unlocks Track 5 (cross-connector composite fitness), and produces the headline self-referential demo (PromptPotter optimizes PromptPotter's L1/L2/L3/critique meta-prompts).
2. **Config-driven connector lookup.** `bootstrap.py:514` currently hardcodes `connectors.get("termnorm")`. Read `pipeline.json::backend_type` (already in dataset configs). Same for `presentation/api.py` sites consuming `BackendConnection.backend_type`.
3. **Query parser registry.** `split_query_parts()` (in `services/backend_client.py`) is still TermNorm-shaped. With the second connector, hoist into a per-connector hook (or fold into the wire adapter — decide when the second connector lands).
4. **Workflow nodes** (M6 Wave 4 holdover) — unblocked by the connector boundary.
5. **Multi-tenant `TenantId` newtype.** See [`security-audit.md`](security-audit.md) § SafeName / TenantId. Lite path-validation landed; structural newtype migration belongs with multi-tenant rollout (touches every store).
6. **Prompt-injection Phase 2.** See [`security-audit.md`](security-audit.md) § Prompt-injection Phase 2. Starter fence on untrusted SIGNAL renderers landed; structural lint + output validators + cross-call repeat detection belong with multi-tenant work.

#### Track 1.5 — PromptPotter-as-Connector *(deliverable 1 detail)*

`promptpotter/connectors/promptpotter.py` provides the five-hook `Connector`:

| Hook | Behavior |
|---|---|
| `name` | `"promptpotter"` |
| `wire_adapter(query, pipeline_params) → payload` | `query` = inner-benchmark task identifier (dataset name + cycle params). `pipeline_params` carries the meta-prompt overrides being explored (outer L1's mutation surface). Adapter constructs the inner-cycle config. |
| `session_factory()` | Builds an isolated inner `Session` rooted under `.runtime/inner/<outer_round>/<sample_idx>/`. In-process, no external service. |
| `extract_experiment(experiment_data) → (queries, index_terms)` | Maps "queries" to inner-benchmark tasks. `index_terms` empty (no retrieval index). |
| `resolve_ground_truth(experiment_data, query) → str \| None` | Returns the inner cycle's success criterion — typically a target accuracy threshold. |

Self-registers via `register(Connector(...))` at import.

**Meta-prompt pipeline schema** — `datasets/promptpotter-self/pipeline.json` describes the four meta-prompt nodes as `pipeline_params` keys:

| Node | Mutable fields |
|---|---|
| `l1_generate` | template fields (instruction, decomposition, output schema), dispatch-hub injection slot list |
| `l1_critique` | template fields (`negative_critique`, `suggested_axes` framing, score-narration prompt), injection slots |
| `l2_context` | template fields (refinement instruction, `task_context` merge policy), injection slots |
| `l3_plan` | template fields (replan trigger framing, plan-space description), injection slots |

Hand-built once; afterwards it's just config. Schema validates against `optimizer_pipeline.json`'s pinned shape.

**Three composable inner-cycle proxies** — operator composes inner-cycle scoring from three independently meaningful metrics. **All three exposed simultaneously** so the operator scales weights through campaigns and accumulates evidence about which correlate with publication-quality outcomes.

| Metric | Definition | Cost | Signal |
|---|---|---|---|
| `first_round_delta` | inner-cycle score after round 1 minus origin | cheapest — one round per outer sample | weak alone; fast iteration |
| `after_N_rounds_delta` | inner-cycle score after `N` rounds (default 3) minus origin | bounded — `N` rounds per outer sample | the workhorse |
| `rounds_to_N` | rounds to reach target score (e.g. `0.80`); times out at `max_rounds` | variable — cheap on easy mutations, expensive on hard | most truthful; closest to "did this meta-prompt actually help" |

Composed via `compile_scorer` (`promptpotter/shared/scoring.py`); example: `0.4 * first_round_delta + 0.4 * after_N_rounds_delta + 0.2 * (1.0 / max(rounds_to_N, 1))`. Names enter flat (not `pipeline_data.X`) — `compile_scorer`'s AST validator rejects `Attribute` access. Names are scoped to the PromptPotter connector — formulas referencing them against a TermNorm result fail at compile time (intentional).

**Operator workflow:** start with `first_round_delta` for fast iteration; add `after_N_rounds_delta` when outer dynamics stabilize; switch to `rounds_to_N`-weighted for publication runs.

**Inner-cycle isolation.** Per-outer-sample sub-tree at `.runtime/inner/<outer_round>/<sample_idx>/`. Contains the inner cycle's full `.promptpotter/` tree. Inner cycles share no state across outer samples by default — each starts from inner-origin. Retained for the outer cycle's lifetime (debugging), pruned at outer finalize. Opt out via `campaign.json::optimization.retain_inner_cycles: true`.

**Cost realism.** `outer_cost ≈ outer_n_samples × outer_n_candidates × inner_n_samples × inner_n_rounds × per_call_cost`. Typical `(4, 3, 10, 3)` config ⇒ ~360 inner candidate-evaluations per outer round. Sizing tiers: **dev** — `inner_n_rounds=1`, `first_round_delta` only (minutes/round) · **calibration** — `inner_n_rounds=3`, all proxies (tens of minutes) · **publication** — `inner_n_rounds=5`, `rounds_to_N`-weighted on target benchmark (hours). Outer dashboard's `dashboard.json::spend` surfaces it; operators read before extending.

**Demo target — `datasets/promptpotter-self/`:** minimal cheap-proxy benchmark wired for dev iteration. `pipeline.json` (inner-cycle meta-prompt schema) · `campaign.json` (composite scoring formula combining all three proxies; small `inner_n_samples`; `inner_n_rounds=2`) · `task_description.md` (outer task framing: "improve PromptPotter's L1-generate meta-prompt for the GSM8K-small benchmark") · small inner-benchmark dataset. End-to-end demo: `python -m promptpotter optimize --config datasets/promptpotter-self/campaign.json`; outer dashboard at `:8001/ui/` shows PromptPotter optimizing PromptPotter.

**Non-goals.** Distributed inner cycles (Track 3.5 work). Fixture-based trace replay (M11 Track 5's original design — superseded by real inner cycles). Pareto-aware composite scoring (Track 5 Phase 4). Outer-loop convergence proofs (Track 4 + findings doc).

### Track 2 — Competitor comparison (publication closure)

M11 filled PromptPotter's rows; competitor rows are still empty.

| System | Origin | Approach | Strength |
|---|---|---|---|
| DSPy / MIPROv2 | Stanford 2024 | Bayesian over instructions + few-shot demos | largest community, full framework |
| GEPA | 2025 (now in DSPy) | Reflective prompt evolution, tree of candidates | +12% over MIPROv2 on AIME-2025 |
| Promptomatix | Salesforce 2025 | Meta-prompt + DSPy compiler, cost-aware | competitive at lower cost |
| adv-CoT | 2025 | Adversarial generator-discriminator | +4.44% on GPT-3.5-turbo across 12 reasoning datasets |
| PromptWizard | Microsoft | Critique-guided generation (our inspiration) | cost-efficient, strong single-LLM |

**Deliverables.** Cited numbers (all competitors filled; labeled "cited" vs "ours") · MIPROv2 reproduction (optional, defensive — if reviewers object) on HotPotQA with same model + split as M11 Track 1 · cost/efficiency scatter (optimizer LLM calls vs accuracy gain) · final paper draft. Different models and hardware across papers weaken direct comparison; lock dataset + metric where possible.

### Track 3 — Webapp Phase 2 (launcher + live monitoring)

**API extensions.** `POST /api/v1/backends/{id}/campaigns` (start) · `POST /campaigns/{id}/control` (pause/resume/stop) · `GET /campaigns/{id}/state` (live polling) · WebSocket or SSE for real-time round progress.

**Open M11–M12 design question.** Two launcher shapes are both in play; M12 Wave 3 lands *one* that satisfies both. Neither is yet sufficient alone:

1. **Campaign configuration form** — dataset + pipeline + connector selection, `campaign.json` builder, scan variant editor. Every knob editable, no ambiguity, reproducible JSON. Strength: completeness, auditability — power users + reproducibility.
2. **Chat panel** (operator-staged in M11's "New Job" tab) — conversation-shaped entry. Drop dataset → see preview → wand toggle on → quiet evolution starts. Wires to the `restructure` optimizer node (downstream of `l3_plan`) as user-facing surface. Strength: low-friction onboarding; matches "fix a broken LLM pipeline in half a day, then it just works." Yet to fulfill what the form covers.

**Control surface — same dual-design tension.** Discrete pause/resume/stop buttons (explicit, model-the-state) vs wand "always-on background optimization" toggle (live mode vs offline campaign). `POST /control` endpoints stay; only the user-facing surface is open.

**Other views.** Dataset preview on drop (pairs with both launcher shapes) · real-time progress dashboard (current round, candidates, live accuracy, L1/L2/L3 status, log tail; SSE/WebSocket replaces 2 s polling) · pipeline visualization is connector-driven (renders whatever the active connector's `pipeline.json` declares, re-renders on dataset/session switch).

**Multi-tenant hook-up.** M12 activates the `TenantContext` seam shaped in M9: auth middleware populates it; `infrastructure/store/` enforces `{tenant_id}/...` path prefixes. Whitelabel becomes viable. Sidebar `Log out` lights up.

**New Job status bar** (v1 shipped 2026-05-08 at `webapp/components/dashboard/ChatPane.tsx::chat-job-bar`). Thin bar above the chat panel: collapsed shows `dataset · Best · Round · Spend · Budget · Δ/$`; chevron expands inline into a four-section read-only panel. Interactive piece (live wand toggle, spend chips wired to control) lands with this track.

### Track 3.5 — Orchestrator daemon (control plane structural shape)

Track 3's launcher/control work describes REST endpoints — that's the half-step. The structural shape is an **orchestrator daemon**: `optimize` runs as a long-lived process owning `Session` and `LoopState`; CLI / notebook / webapp become HTTP clients of the same orchestrator. **Adds a fourth I/O kind** beyond §0's three (Persistence / Display / Control-local) — call it **Control-remote**.

Named explicitly so Track 3 doesn't accidentally ship a halfway-daemon (in-process-per-request orchestrator with no state coherence between requests). Decide: land as a sibling sub-spec `m12-orchestrator-daemon.md` or keep inline if the section stays small.

**Must address:**

- **The fourth I/O kind ("Control-remote") and what it can and can't do** — parallel to §0's three-kind invariant. **Allowed:** receive control commands, expose `Session`/`LoopState` snapshots, route campaign output. **Not allowed:** writing campaign artifacts directly (still through `CycleEventLog.append`); reading tracing data (still fan-out only).
- **How `Session` becomes daemon-owned.** Either in-process-mutable (cheap; daemon owns lifetime) or projection-over-the-ledger (expensive; enables multi-process + trivial restart). Decide on whether multi-process state coherence is required.
- **How CLI / notebook / webapp become clients.** Concrete: what does `python -m promptpotter optimize` do when the daemon is running? Spawn one if none, or hard-fail with "start the daemon first"?
- **State-authority decision.** If multi-process / restart-survival needed, pull §3.8's reconstructable-state invariant forward into a partial event-sourcing migration (Session-as-projection over ledger).
- **Coupling with Tracks 1 + 4.** Daemon owns the connector instance (not per-request); L4 closure runs as a daemon-managed long-lived job.

**Why M12 not later.** Multi-tenant work in Track 3 already touches every store boundary; adding daemon shape on top of in-process orchestration later means revisiting twice. Better to design Track 3 + 3.5 together so the launcher is daemon-shaped from the start.

### Track 4 — L4 self-optimization closure

§0 claims PromptPotter optimizes its own meta-prompts via `optimizer_pipeline.json`. M10 pins the contract; M11 ships the connector; M12 closes the loop by actually running the outer-loop optimization that improves L1/L2/L3 prompts.

**Deliverables.**

1. **Outer-loop campaign on PromptPotter dataset.** Point `python -m promptpotter optimize` at `datasets/promptpotter/` using the Track 1.5 connector. Run 5–10 rounds. The campaign optimizes PromptPotter's own meta-prompts.
2. **`proxy_lift_corr` validation on the meta-loop.** M10 ships `proxy_lift_corr ≥ 0.6` as the L1-tuning gate. M12 confirms the same gate holds when the optimizer is optimizing itself. If correlation breaks, that's a finding worth publishing — meta-stability as positive or negative result.
3. **Cross-cycle digest of meta-prompt evolution.** Same `archive/measurements/` mechanism as target-task campaigns. Operator reads meta-prompt history the same way they read TermNorm campaign history — no parallel infrastructure.
4. **Findings doc — `docs/research/l4-self-optimization-results.md`.** Did meta-optimization improve target-task accuracy on a held-out benchmark? Cost? What changed in the meta-prompts? Pairs with Track 2 publication closure.

**Why M12 not M12+.** M11 connector + M10 fixture/contract eliminate the residual blocker. What's left is "run the loop" — same orchestration code as any other campaign, just with the PromptPotter connector. Publication value (closing the L4 story) is significant enough to belong in the headline milestone.

### Track 5 — Composite fitness function *(multi-objective scoring)*

Today's fitness is one-dimensional: `compile_scorer` returns a clamped float per sample; PoBB ranks on this single number. The optimizer drifts toward verbose hedge-everything prompts that marginally improve recall.

Three axes the user wants weighted: **accuracy** (existing composite) · **money** (`cost_usd` per candidate) · **time** (wall-clock latency per candidate).

**Why post-Track-1.5.** Composite fitness is cross-connector — TermNorm + PromptPotter-self both feed it. Designing on a single connector bakes in TermNorm-shaped cost/latency assumptions. PromptPotter-as-connector's inner cycles produce per-candidate cost + time at the outer level, exactly the shape needed to validate end-to-end.

**What's already in place.** `TokenUsageRecord` (`domain/run_records.py:115-141`) — one record per LLM call with `cost_usd`, `input_tokens`, `output_tokens`, `duration_s`, `kind`, `node`, `model`, `round`. `dashboard.json::spend` — two-bucket rollup. `shared/spend.py` — token → USD with multi-source fallback. **Missing:** per-candidate cost rollup, per-candidate latency rollup, scoring-formula access to those aggregates.

**Per-candidate aggregates** — new projection field on `LiveStateCore` (or sibling), one row per `(candidate_id, cycle_id)`:

```
cost_usd_total      sum of TokenUsageRecord.cost_usd for this candidate
input_tokens_total  sum of input_tokens
output_tokens_total sum of output_tokens
duration_s_total    sum of duration_s
n_calls             count of LLM calls scoped to this candidate
```

Verify `TokenUsageRecord` carries candidate identity (or derivable from ledger position).

**Two scoring scopes.** Per-sample (existing) — `compile_scorer` consumes `result` dict, stays as-is for accuracy. Per-candidate post-aggregate (new) — runs after a candidate's PoBB evaluation completes, has access to `composite_fitness` (mean over the candidate's samples) + aggregates above. Returns one multi-objective fitness float.

**Example operator formulas:**

```
fitness = composite_fitness                                           # accuracy-only (today)
fitness = composite_fitness - 0.01 * cost_usd_total                   # cost-aware
fitness = composite_fitness * (1.0 if duration_s_total < 60 else 0.5) # time-aware
fitness = 0.7 * composite_fitness - 0.2 * (cost_usd_total / cost_budget) - 0.1 * (duration_s_total / time_budget)
```

`cost_budget` + `time_budget` from `campaign.json::optimization` (`spend_budget_usd` exists; `time_budget_s` new).

**Pareto-aware PoBB** *(M12+ stretch — designed not committed).* Replace scalar `score` in `posterior_best_probabilities` with vector `(accuracy, -cost, -time)`; compute Pareto rank per posterior sample (1 = non-dominated); eliminate when posterior probability of Pareto rank 1 falls below ε. Substantially harder than linear combination — linear delivers most value with no PoBB changes.

**Visualization.** Dashboard score-vs-cost-vs-time scatter: one point per candidate; x = `cost_usd_total`, y = `composite_fitness`, color/size = `duration_s_total`. Pareto frontier highlighted; lineage colors group children of the same parent. `webapp/lib/poll.ts::DashboardSnapshot` extends with `current_round.candidates[].rollup`.

**Phases.** P1 surface data (M11 wrap — done by `m11-spend-tracking.md`). P2 per-candidate rollup (projection field + dashboard shape; no scoring change yet — visualization first). P3 multi-objective formula (`compile_post_aggregate_fitness(formula)` + `campaign.json::scoring_post_aggregate` field). P4 Pareto-aware PoBB (M12+).

## Wave sequencing

```
Wave 1: ✅ Track 1 foundation — Connector + registry + TermNorm migration (ed95509)

Wave 2: Track 1.5 (second connector) + Track 3 (API extensions)
        — parallel; second connector exercises the boundary, API extensions unblock webapp

Wave 3: Track 2 (cited competitor numbers + figures) + Track 3 (launcher + live monitoring)
        — parallel; publication closes, webapp gains control

Wave 4: Track 3 (multi-tenant + polish + deployment) + Track 2 (MIPROv2 reproduction if needed)
        + Track 4 (L4 outer-loop run) + Track 5 P2 (per-candidate rollup)
        — ship; L4 closure runs against the new connector + M10 fixture

Wave 5: Track 3.5 (orchestrator daemon) + Track 5 P3 (multi-objective formula)
        — Track 3.5 reshapes control plane around daemon ownership of Session;
          Track 5 P3 lands once the rollup data is stable
```

## Entry / exit

**Entry:** M11 exit gate passed · stable benchmark numbers in `docs/research/benchmarks.md` · webapp read-only views live.

**Exit:**

- [x] `Connector` shape + registry shipped; TermNorm migrated; `BackendClient` connector-agnostic.
- [ ] Second backend connector (PromptPotter-as-connector) exists and runs a full optimization campaign end-to-end.
- [ ] Bootstrap + API connector lookup driven by `pipeline.json::backend_type`.
- [ ] Workflow nodes (M6 Wave 4) implemented.
- [ ] Main results table complete with all competitors (cited or reproduced).
- [ ] Webapp can launch, monitor, and control a campaign end-to-end.
- [ ] `TenantContext` enforced at `infrastructure/store/` boundary; whitelabel viable.
- [ ] Publication final draft complete.
- [ ] L4 self-optimization closure: outer-loop campaign on `datasets/promptpotter/` ran end-to-end; findings doc at `docs/research/l4-self-optimization-results.md`.
- [ ] Track 5 P2 (per-candidate cost/time rollup on `dashboard.json::current_round.candidates[].rollup`) + P3 (`compile_post_aggregate_fitness`) shipped; one operator-written cost-aware campaign on record.
- [ ] Orchestrator daemon (Track 3.5): control-plane shape decided (in-process-mutable Session vs Session-as-projection); daemon spec landed (sub-spec or inline); CLI / notebook / webapp client behavior defined when daemon is running.

## Key existing code

| Area | Files |
|---|---|
| Connector boundary | `connectors/protocol.py` (Connector dataclass), `connectors/__init__.py` (registry) |
| TermNorm connector | `connectors/termnorm.py` |
| Backend client | `infrastructure/backend.py` (connector-agnostic; `wire_adapter` + `session` required) |
| Query parsing | `services/backend_client.py::split_query_parts` (still TermNorm-shaped; per-connector hoist pending) |
| Pipeline discovery | `infrastructure/backend.py::fetch_pipeline` |
| Tenant seam | `domain/tenant.py` + `Session.tenant` (M9 shaped, M12 enforced) |
| Token-usage record | `domain/run_records.py:115-141` (`TokenUsageRecord`) |
| Spend rollup | `infrastructure/projections/live_state.py`; `shared/spend.py` |
| Per-sample scorer | `application/scoring/formula/compiler.py::compile_scorer` |
| FastAPI surface | `presentation/api.py` |
| Webapp | `webapp/` (M11 MVP) · `webapp-react/` (source) |

## Risks

| Risk | Mitigation |
|---|---|
| Second connector is a toy | PromptPotter-as-connector is the deliberate choice — non-trivial, exercises the boundary, headlines the demo. |
| MIPROv2 reproduction cost | Only if reviewers object; reuse M11 infrastructure. |
| Webapp control surface races | Reuse `FileControlSurface` + graceful interrupt from Parity milestone. |
| Multi-tenant activation breaks existing data | Migration plan before activation; default tenant for legacy. |
| Publication stuck on model version | Document exact model version in reproducibility manifest. |
| L4 outer loop diverges (`proxy_lift_corr` < 0.6 on meta-task) | Findings doc reports it as a result; framework adapts per M10's proxy validation procedure. |
| Composite-fitness formula collapses to single axis | Operator can author multiple formulas across campaigns; the post-aggregate scope is per-campaign, not global. |
