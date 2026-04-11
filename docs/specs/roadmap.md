# Roadmap: PromptPotter Optimizer

**Version:** 0.13.0
**Date:** 2026-04-08
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
| M9 | Publication, Stable Config & Webapp | **Next** |
| M10 | OptSearchPoint Refinement & Ablation Studies | Future |
| M11 | Multi-Connector Architecture | Future |

Archived specs (M0-M7, governance docs, old M9) are in `archive/` or git history.

---

## M6: PipelineSchema + Pipeline Composability -- Complete

PipelineSchema model, `GET /pipeline` self-describing config, schema derivation (6 chokepoints resolved), unified tracing, composite scoring, node_type-driven intermediate metrics, consolidated pipeline control surfaces. Wave 4 (workflow nodes) deferred to M11. Spec: [`archive/m6-pipeline-composability.md`](archive/m6-pipeline-composability.md)

---

## M7: Optimizer-as-Pipeline -- Complete

5-node optimizer pipeline (l1_generate, l1_evaluate, critique, l2_refine_strategy, l3_modify_plan) with `llm_call()` primitive, `observed_node()` tracing, OptSearchPoint consolidation, warning inventory, L2 probe rounds, l2_directive bridge. Spec: [`archive/m7-optimizer-pipeline.md`](archive/m7-optimizer-pipeline.md)

---

## Parity: Entry-Point Parity -- Complete

Three-layer I/O architecture (persistence / display / control). `CampaignPersistenceEmitter` auto-created by `run_optimization()` — all entry points produce identical `campaign_state.json`, `campaign_output.log`, `campaign_log.md`. `FileControlSurface` extracted for bidirectional control. Parity test enforces artifact manifest. Spec: [`m-parity-entry-point-parity.md`](m-parity-entry-point-parity.md)

---

## M8: Campaign Intelligence -- Complete

Made campaigns smarter and faster through accumulated data. Four pillars: (1) per-node intermediate caching — prompt variants skip redundant upstream computation (~60% speedup), (2) adaptive sensitivity scan with statistical pruning (Wilson CI overlap, minimum detectable effect), (3) SearchMemory — cross-campaign materialized view over dataset_runs (parameter impact, query patterns, failure modes), (4) three-tier intelligence architecture feeding L1/L2/L3/critique/scan advisor with accumulated analysis. All 17 waves complete.

Full spec: [`m8-campaign-intelligence.md`](m8-campaign-intelligence.md)

---

## M9: Publication, Stable Config & Webapp -- Next

Four tracks delivering publication readiness, optimizer tuning, a web application, and publication figure design. The optimization loop (L1/L2/L3) is functionally complete but unvalidated against academic benchmarks, running on proof-of-concept meta-prompts, and accessible only via notebook/CLI.

### Track 1: Publication Readiness (Benchmarks + Competitor Comparison)

Build dataset loaders (HotPotQA, GSM8K), evaluation scorers (Token F1, numeric exact match), and run benchmark campaigns via the backend's `llm_only` step. Compare against published results from DSPy/MIPROv2, GEPA, Promptomatix, adv-CoT, and PromptWizard (cited numbers). Fill `docs/research/benchmarks.md` result tables. Generate paper-quality convergence plots and comparison figures.

### Track 2: Stable Optimizer Configuration

Systematically evaluate and tune the optimizer's own meta-prompts (L1 generate, critique, L2 refine, L3 replan). Define success metrics (rounds to convergence, final accuracy, escalation frequency, candidate diversity). Run benchmark campaigns with different meta-prompt variants. Document final configs with rationale for paper's "method" section. Generic prompts — adapt via `task_context` injection, not task-specific sets.

### Track 3: Web Application

Next.js + React + Tailwind CSS webapp in `webapp/` directory, consuming the existing FastAPI REST API. MVP: campaign list, campaign detail (convergence chart, trial timeline), trial inspector (prompt diff, per-query results), benchmark results display. Phase 2: campaign launcher, live monitoring (WebSocket/SSE), API extensions.

### Track 4: Results Design (Publication Figures & Tables)

Define WHAT to show before collecting data. Design main results table, convergence figures, ablation tables, analysis visualizations, cost/efficiency comparisons. Document in `docs/publication-figures.md` as the data collection checklist.

**Entry criteria:** M8 exit gate passed.

**Exit gate:** HotPotQA + GSM8K results with statistical rigor (3 seeds, CIs, significance tests). Meta-prompts evaluated on ≥2 benchmarks with documented rationale. Webapp showing campaign browser and benchmark results. Publication figures designed and documented.

Full spec: [`m9-publication-config-webapp.md`](m9-publication-config-webapp.md)

---

## M10: OptSearchPoint Refinement & Ablation Studies -- Future

Slim milestone focused on optimization loop refinement and ablation studies that feed the publication. Multi-pipeline/project/dataset support (same connector, already half-built). OptSearchPoint bells and whistles — advanced L1/L2/L3 strategies, detailed analysis tooling, ablation experiments (L1-only vs L1+L2 vs full, scan vs no-scan, SearchMemory on/off, critique on/off). Can contribute to paper if completed quickly enough.

**Entry criteria:** M9 stable config validated.

**Exit gate:** Ablation results complete. Multi-pipeline/dataset support working.

---

## M11: Multi-Connector Architecture -- Future

Generalize beyond the current single-backend setup to support arbitrary LLM application backends. Abstract `BackendClient` into `ConnectorProtocol`, connector registry, backend-agnostic evaluation, query parser registry. Resolves remaining chokepoints (4,5,7,10,11,12,13). Workflow nodes (from M6 Wave 4).

**Entry criteria:** M10 exit gate passed.

**Exit gate:** A second backend connector exists and runs through the same optimization workflow.

Preserved spec: [`archive/m9-multi-connector.md`](archive/m9-multi-connector.md)

---

## Backlog (unscheduled)

| Feature | Notes |
|---------|-------|
| Multimodal / non-textual modalities | Extend beyond Q&A text to other input types (RNAseq, X-ray, image, audio). Requires modality-specific evaluation, dataset formats, and scoring functions. |
| Pipeline Variant Comparison | Needs ConnectorProtocol + pipeline comparison (post-M11) |
| Web scrape ablation | Quality vs cost/latency tradeoff |
| Public service deployment | Auth, rate limiting, multi-tenancy |
| Non-prompt targets | Scoring functions, fuzzy matchers, retrieval queries, GA settings |
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
| Evaluation mode | Backend via `/matches` (default, `llm_only` step). Optional local eval via `LLMOnlyAdapter` (opt-in). |
| Crash recovery | Incremental `.partial.jsonl` with partial-run resume |

---

## Progression Rules

- Complete current milestone before starting the next
- Each milestone ends with a decision gate
- Update CLAUDE.md at each milestone boundary
