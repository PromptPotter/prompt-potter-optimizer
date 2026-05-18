# Roadmap: PromptPotter Optimizer

**Version:** 0.15.0
**Date:** 2026-04-28
**Status:** Active — leading toward M12

---

## Where we're heading

**M12 is the headline.** Multi-connector architecture, competitor comparison, and the webapp Phase 2 are the pieces that turn PromptPotter from a single-backend research artifact into a product surface — and the publication that goes alongside. Everything in front of M12 is backbone work that exists to make M12 land cleanly.

M9 (hexagonal layout + multi-dataset + file-directory view model + config aggregate) shipped — the structural prep is done. **M10 (prompt-iteration framework + L1-generate tuning) is the next active milestone**; it doubles as the L4 partial implementation (most of self-optimization's credit-assignment infrastructure, operated manually). Without it, every M11 benchmark number is sampled from an under-tuned loop. M11 (BBEH benchmarks + ablation + webapp read-only) is the publication backbone — the numbers paragraph and the first webapp slice. Both feed M12; neither is the destination.

## Milestones

| Milestone | Focus | Status |
|-----------|-------|--------|
| M12 | **Multi-Connector, Competitor Comparison, Webapp Phase 2** | **Headline — Future** |
| M12+ | Backlog | Future |
| M11 | Publication Benchmarks, Ablation Studies, Webapp Read-Only | Backbone (Future) |
| M10 | Prompt-Iteration Framework + L1-generate Tuning (also L4 partial) | Backbone (Future) |
| M9 | Hierarchy Refactor, Multi-Dataset/Pipeline, File-Directory UI v0, Config Aggregate | Complete |
| M8 | Campaign Intelligence | Complete |
| Parity | Entry-Point Parity (Unified Persistence) | Complete |
| M7 | Optimizer-as-Pipeline | Complete |
| M6 | PipelineSchema + Pipeline Composability | Complete (Wave 4 → M12) |
| M0-M5 | Specifications, Foundation, Core Optimizer, Infrastructure, Observability | Complete |

Archived specs (M0-M7, governance docs, old M9, M9 hierarchy refactor) live in `docs/specs/archive/` or git history.

---

## M12: Multi-Connector, Competitor Comparison, Webapp Phase 2 -- Headline

The destination. Three deliverables, one milestone:

1. **Multi-connector architecture.** Foundation shipped (`ed95509`): `Connector` shape + registry at `promptpotter/connectors/`, `BackendClient` connector-agnostic, TermNorm migrated. Outstanding: register a second connector to prove the boundary end-to-end, drive lookup from `pipeline.json::backend_type`, hoist the query parser per-connector, and land the workflow nodes deferred from M6 Wave 4.
2. **Competitor comparison.** Publication picks up its head-to-head numbers — MIPROv2 reproduction if reviewers demand it, cited numbers otherwise. The BBEH backbone from M11 is the substrate; M12 is the pass that turns it into "vs. competitors" rather than "ours alone."
3. **Webapp Phase 2.** Campaign launcher, live monitoring over WebSocket / SSE, API extensions for control. The M11 read-only views become a full operator surface.

**Why headline now:** the loop is functionally complete, the backbone is most of the way landed, and the next thing that meaningfully changes what PromptPotter *is* — not just how it's tuned — is connector generalization. M9, M10, and M11 are valuable because they make M12 cheaper, not because they're terminal goals.

**Entry criteria:** M11 backbone landed (publication numbers + read-only webapp slice).

**Exit gate:** Second backend connector runs through the same optimization workflow with parity tests. Competitor head-to-head published. Webapp can launch and monitor a campaign end-to-end.

Full spec: [`m12-multi-connector.md`](m12-multi-connector.md)

---

## M12+: Backlog -- Future

Polish, cost tracking, MCP server mode, multimodal, and everything in the [Backlog table](#backlog-unscheduled) below. Ships opportunistically after M12. (L4 self-optimization completion was promoted to M12 — M10 partial → M11 connector → M12 closure run; see `m12-multi-connector.md` Track 4.)

Full spec: [`m12-plus-backlog.md`](m12-plus-backlog.md)

---

## Backbone work (in front of M12)

M9 shipped. The two remaining backbone milestones below exist to make M12 land cleanly. They are not the destination — they're the prep that turns M12 from a rewrite into a series of seam swaps.

### M10: Prompt-Iteration Framework + L1-generate Tuning -- Backbone (optimizer-prompts)

Manual refinement of the four optimizer prompts (`l1_generate`, `l1_critique`, `l2_context`, `l3_plan`), gated on a per-cycle `review.md` artifact, an extensible behavior-check registry, a `rounds_to_95` headline metric, and a cross-cycle leaderboard keyed by prompt-hash. Headline goal: ≥95% training-set accuracy in ≤5 rounds on at least two pipelines (`llm_only` and TermNorm) under the same prompt revision. Lifted out of the original M9 Track 1. Without M10, every M11 benchmark number is sampled from an under-tuned loop.

**Doubles as L4 partial implementation.** `proxy_lift_corr`, `new --sweep-batch`, the behavior-check registry, and the `review.md` feature extraction are the manually-operated form of the self-optimization infrastructure. M10 also pins the `optimizer_pipeline.json` contract (parity test against backend `pipeline.json`) and ships a self-optimization fixture under `datasets/promptpotter/` (per `m10-cleanup.md` §3.5 + §1). M11 ships the PromptPotter-as-backend connector ([`m11-publication-benchmarks.md`](m11-publication-benchmarks.md) Track 5) — the residual adapter that was originally parked in M12+. M12 swaps the human for the outer-loop LLM in the actual closure run ([`m12-multi-connector.md`](m12-multi-connector.md) Track 4).

Full spec: [`m10-prompt-iteration-framework.md`](m10-prompt-iteration-framework.md).

### M11: Publication Benchmarks, Ablation Studies, Webapp Read-Only -- Backbone (publication)

The numbers paragraph. Primary benchmark is BBEH (23 diverse reasoning tasks); GSM8K and AIME are saturated at `gpt-oss-120b` and stay secondary. Ablation studies (L1-only vs L1+L2 vs full, scan vs no-scan, SearchMemory on/off, l1_critique on/off) on BBEH feed the paper's "method" section. The webapp gets its first real slice — read-only views consuming M9's file-directory view model via the FastAPI API. M12 then takes the same webapp shell and adds launching + live monitoring.

Full spec: [`m11-publication-benchmarks.md`](m11-publication-benchmarks.md)

---

## Completed milestones

The work below is done; it's listed for narrative continuity and as a pointer back to specs. M0–M5 ship dates, original specs, and decision rationale live in git history (with archived spec files at `docs/specs/archive/` for the ones that earned a permanent home).

## M6: PipelineSchema + Pipeline Composability -- Complete

PipelineSchema model, `GET /pipeline` self-describing config, schema derivation (6 chokepoints resolved), unified tracing, composite scoring, node_type-driven intermediate metrics, consolidated pipeline control surfaces. Wave 4 (workflow nodes) deferred to M12. Spec: see git history (pre-`c94aaa83`).

---

## M7: Optimizer-as-Pipeline -- Complete

5-node optimizer pipeline (now `l1_generate`, `l1_critique`, `l2_context`, `l3_plan`, `restructure` after subsequent renames) with `llm_call()` primitive, `observed_node()` tracing, `OptSearchPoint` consolidation, warning inventory, L2 probe rounds, broadcast `task_context` channel. Spec: see git history (pre-`c94aaa83`).

---

## Parity: Entry-Point Parity -- Complete

Three-layer I/O architecture (persistence / display / control). `LiveDashboardView` and `AuditTrailView` (under `infrastructure/projections/`) auto-created by `run_optimization()` — all entry points produce identical `dashboard.json`, `output.log` (per-cycle family) plus `session.json`, `journal.md`, `notes.md` (per-session). Parity tests enforce both artifact sets. Stop control is via Ctrl+C (CLI) or kernel interrupt (notebook); the file-based `control.json` mechanism was retired alongside the `control` / `show-status` / `show-results` CLI commands. Spec: [`m-parity-entry-point-parity.md`](m-parity-entry-point-parity.md)

---

## M8: Campaign Intelligence -- Complete

Made campaigns smarter and faster through accumulated data. Four pillars: (1) per-node intermediate caching — prompt variants skip redundant upstream computation (~60% speedup), (2) adaptive sensitivity scan with statistical pruning (Wilson CI overlap, minimum detectable effect), (3) SearchMemory — cross-campaign materialized view over dataset_runs (parameter impact, query patterns, failure modes), (4) three-tier intelligence architecture feeding L1/L2/L3/l1_critique/scan advisor with accumulated analysis. All 17 waves complete.

Architecture: [`../concepts/scoring-and-memory.md`](../concepts/scoring-and-memory.md), [`../concepts/the-loop.md`](../concepts/the-loop.md). Original spec preserved in git history.

---

## M9: Hierarchy Refactor, Multi-Dataset/Pipeline, File-Directory UI v0, Config Aggregate -- Complete

Structural prep for the publication and product surfaces. Tracks 2, 3, 6, 7 shipped; Tracks 4 and 5 superseded by cleaner outcomes. Hexagonal layout (`domain / application / infrastructure / presentation / shared / config`) with a `TenantContext` seam for whitelabel. Five datasets coexist as peers under `datasets/` (lca-termnorm, bbeh, gsm8k, hotpotqa, aime_2025); pipeline identity carried by `pipeline_schema.name` + content hash rather than a separate field. `CampaignConfig` (Pydantic) + `Session` (runtime) + `LoopEnv` (loop infra) replaced the three-object config mess (`LoopConfig` deleted). Two-tree directory layout (`sessions/{session_id}/` + `campaigns/{cycle_id}/`) under `.promptpotter/projects/{tenant_id}/`; MLflow via SDK at `archive/mlruns/`. Renderer unified: the `campaigns/{cycle_id}/` artifact tree IS the view model — `LiveDisplay` (`presentation/views/live/display.py`) is the single renderer that CLI + notebook both call. CLI seed-source unification dropped (recon archival left only two real sources, typed vocabulary buys nothing). Track 1 (optimizer-prompt tuning) lifted out into M10.

Spec: [`archive/m9-stable-config-and-scaffolding.md`](archive/m9-stable-config-and-scaffolding.md). Hierarchy refactor archived as done at [`archive/m9-hierarchy-refactor.md`](archive/m9-hierarchy-refactor.md).

---

## M10 detail: Prompt-iteration backbone

**Goal:** ≥95% training-set accuracy in ≤5 rounds on at least two pipelines (`llm_only` and TermNorm) under the same prompt revision. When this holds, optimizer prompts are "configured" and M11 picks up to validate on the test set + run the benchmark numbers. Timeline target: ~1 week of manual iteration once the framework lands.

**Routed Dispatch infrastructure shipped on `feat/routed-dispatch-v2`** — typed `dispatch_hub.INJECTIONS` with load-time `validate_template`, `axis_memory` cross-round signal, escalation rules engine (`application/optimization/escalation/`) with opt-in `l2_axis_yield_drought`. M10's prompt-iteration goal now runs on top of these. The five tracks below remain the spec; the loop that executes them is now signal-driven rather than patience-only.

The 3-layer LLM-driven program-evolution loop is plumbed end-to-end through M8 + M9 backbone work. The loop runs; it does not yet **converge well**. `l1_generate` is the principal bottleneck since the whole loop only descends gradient when L1 produces useful variants. Auto-tuning the prompts is too expensive in the small-N regime, so M10 builds the framework for a manual ping-pong between running and prompt-editing — with auto-checks that flag known-bad L1 behaviors so each iteration produces a clear "did the fix land" verdict.

Five tracks:

1. **L1 Behavior Ledger + Auto-Checks.** Extensible registry of `(round_dict) -> CheckResult` rules in `application/l1_behavior_checks.py` (planned). Seeded with `context_object_honored`, `param_scope_discipline`, `not_only_param_variants`. Adding a new check is a one-function diff — that's where new "unknown unknowns" land as the operator iterates.
2. **Per-cycle `review.md` renderer.** Pure renderer (peer of `log_md.py`) emitting per-round inputs → behavior-checks → variants-vs-fitness table → critique. Wired into `runner/entry.py::_finalize_run`. Parity test updated.
3. **L1Stats with `rounds_to_95`.** Headline metric is `rounds_to_95` (first round where best accuracy ≥ 0.95; `None` if never reached). Diagnostics: `yield_rate`, `top_lift_mean`, `behavior_pass_rate`, `stagnation_max`, `l2_fires`.
4. **Cross-cycle leaderboard.** `application/leaderboard.py` (planned) + read-only `scripts/ppot_review.py --leaderboard`. Rows cluster by `l1_generate_hash`, sorted by `rounds_to_95` ascending. Visual readout: when a prompt revision works, `rounds_to_95` drops and `behavior_pass_rate` climbs.
5. **Methodology document.** `docs/methods/manual-prompt-tuning.md` — daily cadence, diagnosis decision tree, "one knob per iteration" rule, "general fix not specific" rule, generalization gate, procedure for adding a new behavior check.

**Entry criteria:** M9 exit gate passed.

**Exit gate:** All five deliverables implemented; tests green; `rounds_to_95 ≤ 5` achieved on `llm_only` AND TermNorm for at least one cycle each under the same prompt revision; `behavior_pass_rate = 1.0` for both seeded checks across the qualifying cycles.

Full spec: [`m10-prompt-iteration-framework.md`](m10-prompt-iteration-framework.md)

---

## M11 detail: Publication backbone

**Primary benchmark: BBEH** (Big-Bench Extra Hard, 23 diverse reasoning tasks). GSM8K and AIME are effectively saturated at `gpt-oss-120b` and are deprioritized; they may still appear as secondary numbers if headroom is found. HotPotQA runs second as a multi-hop QA data point — pending a saturation check, it may also be deprioritized. Head-to-head infrastructure for BBEH already exists at [`docs/research/bbeh-comparison/`](../research/bbeh-comparison/) with CAPO, GEPA, MIPROv2, and BootstrapFewShot notebooks against the same model and split.

Execute the ablation studies that feed the paper (L1-only vs L1+L2 vs full, scan vs no-scan, SearchMemory on/off, l1_critique on/off) on BBEH. Build the first real webapp pass: read-only views (dashboard, campaign detail, trial inspector) consuming the M9 file-directory view model via the FastAPI API. Publication figures designed per `docs/publication-figures.md`.

The Routed Dispatch arc shipped `SignalsPanel` (rolling list of recent escalation-rule firings) and `StuckDiagnosis` (per-layer verdict from the latest `signal_inputs` snapshot) — read from `dashboard.json::recent_rules` + `dashboard.json::current_signals`. These are the precondition for M12 streaming (live signal stream via SSE/WebSocket).

**Entry criteria:** M10 exit gate passed (optimizer prompts configured).

**Exit gate:** BBEH results with statistical rigor (3 seeds, CIs) including head-to-head vs CAPO/GEPA/MIPROv2/BootstrapFewShot at identical model + split. HotPotQA saturation assessed (benchmarked if non-saturated). Ablation results complete. Webapp read-only views live. First publication figures generated.

Full spec: [`m11-publication-benchmarks.md`](m11-publication-benchmarks.md)

---

## Backlog (unscheduled)

| Feature | Notes |
|---------|-------|
| Multimodal / non-textual modalities | Extend beyond Q&A text to other input types (RNAseq, X-ray, image, audio). Requires modality-specific evaluation, dataset formats, and scoring functions. |
| Pipeline Variant Comparison | Needs second connector + pipeline comparison (post-M12) |
| Web scrape ablation | Quality vs cost/latency tradeoff |
| Public service deployment | Auth, rate limiting, multi-tenancy |
| Non-prompt targets | Scoring functions, fuzzy matchers, retrieval queries, GA settings |
| Hard-sample sorter | Standalone capability — expose δ_s leaderboard + candidate×sample heatmap as a product surface. Spec: [`hard-sample-sorter.md`](hard-sample-sorter.md). Phase 1 (data primitive + spec) shipped; phase 2 (CLI/notebook ASCII heatmap) and phase 3 (webapp heatmap under M11 track) unscheduled. |
| Evolutionary operators | GA/DE population-based search |
| MCP server mode | Expose tools to Claude Code |
| ~~Self-optimization (L4)~~ | **Promoted to M12** (no longer M12+). M10 ships partial (proxy reward, cheap-trial mechanism, conformance checks) + `optimizer_pipeline.json` contract pin + self-optimization fixture under `datasets/promptpotter/` (`m10-cleanup.md` §3.5 + §1). M11 ships the PromptPotter-as-backend connector ([`m11-publication-benchmarks.md`](m11-publication-benchmarks.md) Track 5). M12 ships the outer-loop closure run ([`m12-multi-connector.md`](m12-multi-connector.md) Track 4) — including findings doc on whether meta-optimization improved target-task accuracy. |
| Cost tracking | Token usage and cost per campaign/round/variant |
| Model comparison matrix | Same benchmark across multiple target LLMs |

---

## Non-Functional Requirements

| Requirement | Target |
|-------------|--------|
| Single evaluation (500-item dataset) | < 10 minutes |
| Full optimization run (5 iterations, 500 items) | < 60 minutes |
| Project store per campaign | < 10 MB |
| LLM providers | Groq and OpenAI (any OpenAI-compatible) |
| Python | 3.13 |
| Evaluation mode | All datasets route through TermNorm `/matches`. Retrieval pipelines (e.g. `lca-termnorm`) use the default step list; generation-only benchmarks (BBEH/GSM8K/AIME/HotPotQA) use `steps: ["llm_only"]`. No local evaluation path. |
| Crash recovery | Incremental `.partial.jsonl` with partial-run resume |

---

## Progression Rules

- Complete current milestone before starting the next
- Each milestone ends with a decision gate
- Update CLAUDE.md at each milestone boundary
