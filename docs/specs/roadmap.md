# Roadmap: PromptPotter Optimizer

**Version:** 0.14.0
**Date:** 2026-04-12
**Status:** Active

---

## Milestones

| Milestone | Focus | Status |
|-----------|-------|--------|
| M0-M5 | Specifications, Foundation, Core Optimizer, Infrastructure, Observability | Complete |
| M6 | PipelineSchema + Pipeline Composability | Complete (Wave 4 → M11) |
| M7 | Optimizer-as-Pipeline | Complete |
| Parity | Entry-Point Parity (Unified Persistence) | Complete |
| M8 | Campaign Intelligence | Complete |
| M9 | Stable Config, Hierarchy Refactor, Multi-Dataset/Pipeline, File-Directory UI v0 | **Next** |
| M10 | Publication Benchmarks, Ablation Studies, Webapp Read-Only | Future |
| M11 | Multi-Connector, Competitor Comparison, Webapp Phase 2 | Future |
| M11+ | Backlog | Future |

Archived specs (M0-M7, governance docs, old M9) live in git history.

---

## M6: PipelineSchema + Pipeline Composability -- Complete

PipelineSchema model, `GET /pipeline` self-describing config, schema derivation (6 chokepoints resolved), unified tracing, composite scoring, node_type-driven intermediate metrics, consolidated pipeline control surfaces. Wave 4 (workflow nodes) deferred to M11. Spec: see git history (pre-`c94aaa83`).

---

## M7: Optimizer-as-Pipeline -- Complete

5-node optimizer pipeline (l1_generate, l1_evaluate, l1_critique, l2_refine_strategy, l3_modify_plan) with `llm_call()` primitive, `observed_node()` tracing, OptSearchPoint consolidation, warning inventory, L2 probe rounds, l2_directive bridge. Spec: see git history (pre-`c94aaa83`).

---

## Parity: Entry-Point Parity -- Complete

Three-layer I/O architecture (persistence / display / control). `CampaignPersistenceEmitter` auto-created by `run_optimization()` — all entry points produce identical `dashboard.json`, `output.log`, `log.md` (per-cycle) plus `session.json`, `journal.md`, `notes.md`, `control.json` (per-session). `FileControlSurface` extracted for bidirectional control. Parity tests enforce both artifact sets. Spec: [`m-parity-entry-point-parity.md`](m-parity-entry-point-parity.md)

---

## M8: Campaign Intelligence -- Complete

Made campaigns smarter and faster through accumulated data. Four pillars: (1) per-node intermediate caching — prompt variants skip redundant upstream computation (~60% speedup), (2) adaptive sensitivity scan with statistical pruning (Wilson CI overlap, minimum detectable effect), (3) SearchMemory — cross-campaign materialized view over dataset_runs (parameter impact, query patterns, failure modes), (4) three-tier intelligence architecture feeding L1/L2/L3/l1_critique/scan advisor with accumulated analysis. All 17 waves complete.

Architecture: [`../concepts/search-memory.md`](../concepts/search-memory.md), [`../developer/search-memory-internals.md`](../developer/search-memory-internals.md), [`../concepts/three-layer-loop.md`](../concepts/three-layer-loop.md). Original spec preserved in git history.

---

## M9: Stable Config, Hierarchy Refactor, Multi-Dataset/Pipeline, File-Directory UI v0 -- Next

M9 is foundation work. Four parallel tracks build the scaffolding that M10 and M11 then populate with benchmark results and a full webapp. The optimization loop is functionally complete but runs on proof-of-concept meta-prompts, lives in a flat service layout unsuited to multi-tenant / webapp expansion, assumes a single dataset/pipeline per campaign, and has no shared view model between notebook, CLI, and future webapp.

### Track 1: Stable Optimizer Configuration

Systematically evaluate and tune the optimizer's own meta-prompts (L1 generate, L1 critique, L2 refine, L3 replan). Define second-order success metrics (rounds to convergence, final accuracy, escalation frequency, candidate diversity, optimizer cost). Run meta-prompt variants on small-sample campaigns and document final configs with rationale. Generic prompts — adapt via `task_context` injection, not task-specific sets.

Spec: [`m9-stable-config-and-scaffolding.md`](m9-stable-config-and-scaffolding.md)

### Track 2: Hierarchy Refactor (Hexagonal Layout)

Reshape `promptpotter/` into a top-down hexagonal layout so that (a) the three-layer I/O invariant is structurally obvious, (b) a `TenantContext` seam exists for whitelabel distribution, (c) the eventual webapp lands as one more thin presentation adapter, and (d) the largest offender files (37KB, 34KB, …) become splittable in follow-up specs. Move-only in M9 — splits deferred.

```
promptpotter/
├── domain/                 # pure models, no I/O, no logging
│   ├── search_point.py     # JobSearchPoint, PromptTemplate, OptSearchPoint
│   ├── pipeline.py         # PipelineSchema, PipelineNode, NodeOutputSchema
│   ├── scoring.py          # ScoringEnv, RoundResult, composite formulas
│   ├── campaign.py         # LoopState, RunConfig, RunListener
│   └── tenant.py           # TenantContext (new — the multi-tenant seam)
│
├── application/            # use cases / orchestration — no direct disk or network
│   ├── campaign/           # lifecycle, runner, round_execution, setup
│   ├── optimization/       # L1/L2/L3 pipeline, l1_critique, escalation, layer_transitions
│   ├── search/             # search_memory (and dormant recon producers)
│   └── scoring/            # search_point_scorer, sample_measurement, metrics
│
├── infrastructure/         # adapters — all I/O lives here
│   ├── store/              # Stores composite + build_stores() + focused leaf stores
│   ├── backend/            # BackendClient, pipeline parsing
│   ├── llm/                # _OpenAICompatibleClient, providers
│   ├── tracing/            # obs_logger, langfuse_client, langfuse_push
│   └── persistence/        # session_emitter, round_recorder, control surfaces
│
├── presentation/           # entry points — thin, one per surface
│   ├── cli/                # click/typer commands → application
│   ├── api/                # FastAPI routers → application
│   └── ui/                 # notebook + webapp display adapters → application
│       ├── campaign/       # (replaces current ui/campaign/)
│       └── formatters/     # display, phase_display, reporting helpers
│
├── shared/                 # leaf utilities — no domain or application deps
│   ├── errors.py           # graceful(), PauseForReviewError
│   ├── constants.py        # PROMPT_STRING_FIELDS
│   └── scoring.py          # compile_scorer
│
└── config/                 # settings, APP_VERSION, logging setup
```

How and when this lands inside M9 is open. The target shape is the contract; execution order and commit granularity are decided during the track.

Spec: [`m9-hierarchy-refactor.md`](m9-hierarchy-refactor.md)

### Track 3: Multi-Dataset / Multi-Pipeline Support

Same connector, multiple datasets and pipelines per project. Dataset/pipeline become first-class identifiers in campaign state and store paths. Prerequisite for benchmark campaigns (HotPotQA + GSM8K + TermNorm coexisting) in M10.

### Track 4: File-Directory UI v0 (Webapp Preparation)

Draft the first non-notebook UI as a plain file-directory "view model" under the session store. The CLI and eventual webapp both read from the same files; nothing renders UI from in-memory state. Content mirrors exactly what the Jupyter notebook already displays — vanilla, no new information surfaces. This is the seed the M10/M11 webapp slices will consume. Exact structure (temp vs permanent views, what belongs where) is intentionally left open and decided during the track.

### Track 7: Config Aggregate Redesign

Collapse the three-object config mess (`LoopConfig`, `SessionEnv`, `LoopEnv`) into a clean lifecycle split: `CampaignConfig` (Pydantic, user-authored, persisted) + `SessionEnv` (runtime context) + `LoopEnv` (loop infrastructure, unchanged). Delete `LoopConfig` and its lossy `from_campaign_config()` bridge. Move derived `pipeline_params`, `recon_brief`, `session_id`, `project_root` onto `SessionEnv` so user config stops being mutated at runtime. Existing `campaign.json` files parse unchanged via a flat-or-nested validator. Full spec in [`m9-stable-config-and-scaffolding.md § Track 7`](m9-stable-config-and-scaffolding.md).

**Entry criteria:** M8 exit gate passed.

**Exit gate:** Stable meta-prompts documented. Hexagonal layout in place and tests green. Multi-dataset/pipeline working on at least two datasets. File-directory UI v0 readable by a human browsing the session folder.

Full spec: [`m9-stable-config-and-scaffolding.md`](m9-stable-config-and-scaffolding.md)

---

## M10: Publication Benchmarks, Ablation Studies, Webapp Read-Only -- Future

First publication slice. **Primary benchmark: BBEH** (Big-Bench Extra Hard, 23 diverse reasoning tasks). GSM8K and AIME are effectively saturated at `gpt-oss-120b` and are deprioritized; they may still appear as secondary numbers if headroom is found. HotPotQA runs second as a multi-hop QA data point — pending a saturation check, it may also be deprioritized. Head-to-head infrastructure for BBEH already exists at [`docs/research/bbeh-comparison/`](../research/bbeh-comparison/) with CAPO, GEPA, MIPROv2, and BootstrapFewShot notebooks against the same model and split.

Execute the ablation studies that feed the paper (L1-only vs L1+L2 vs full, scan vs no-scan, SearchMemory on/off, l1_critique on/off) on BBEH. Build the first real webapp pass: read-only views (dashboard, campaign detail, trial inspector) consuming the M9 file-directory view model via the FastAPI API. Publication figures designed per `docs/publication-figures.md`.

**Entry criteria:** M9 exit gate passed.

**Exit gate:** BBEH results with statistical rigor (3 seeds, CIs) including head-to-head vs CAPO/GEPA/MIPROv2/BootstrapFewShot at identical model + split. HotPotQA saturation assessed (benchmarked if non-saturated). Ablation results complete. Webapp read-only views live. First publication figures generated.

Full spec: [`m10-publication-benchmarks.md`](m10-publication-benchmarks.md)

---

## M11: Multi-Connector, Competitor Comparison, Webapp Phase 2 -- Future

Generalize beyond the current single-backend setup. Abstract `BackendClient` into `ConnectorProtocol`, connector registry, backend-agnostic evaluation, query parser registry. Resolves remaining chokepoints (4,5,7,10,11,12,13) and workflow nodes (from M6 Wave 4). Second backend connector demonstrates the abstraction. Publication gets competitor head-to-head (MIPROv2 reproduction if reviewers demand it, cited numbers otherwise). Webapp Phase 2: campaign launcher, live monitoring (WebSocket/SSE), API extensions for control.

**Entry criteria:** M10 exit gate passed.

**Exit gate:** Second backend connector runs through the same optimization workflow. Competitor comparison published. Webapp can launch and monitor a campaign end-to-end.

Full spec: [`m11-multi-connector.md`](m11-multi-connector.md)

---

## M11+: Backlog -- Future

Polish, cost tracking, MCP server mode, multimodal, self-optimization, and everything in the Backlog table below. Ships opportunistically after M11.

Full spec: [`m11-plus-backlog.md`](m11-plus-backlog.md)

---

## Backlog (unscheduled)

| Feature | Notes |
|---------|-------|
| Multimodal / non-textual modalities | Extend beyond Q&A text to other input types (RNAseq, X-ray, image, audio). Requires modality-specific evaluation, dataset formats, and scoring functions. |
| Pipeline Variant Comparison | Needs ConnectorProtocol + pipeline comparison (post-M11) |
| Web scrape ablation | Quality vs cost/latency tradeoff |
| Public service deployment | Auth, rate limiting, multi-tenancy |
| Non-prompt targets | Scoring functions, fuzzy matchers, retrieval queries, GA settings |
| Hard-sample sorter | Standalone capability — expose δ_s leaderboard + candidate×sample heatmap as a product surface. Spec: [`hard-sample-sorter.md`](hard-sample-sorter.md). Phase 1 (data primitive + spec) shipped; phase 2 (CLI/notebook ASCII heatmap) and phase 3 (webapp heatmap under M10 track) unscheduled. |
| Evolutionary operators | GA/DE population-based search |
| MCP server mode | Expose tools to Claude Code |
| Self-optimization | PromptPotter optimizes its own meta-prompts recursively |
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
