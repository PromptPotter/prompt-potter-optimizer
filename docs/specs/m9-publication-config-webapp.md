# M9: Publication, Stable Config & Webapp

**Version:** 0.1.0
**Date:** 2026-04-08
**Status:** Planned
**Depends on:** M8 Campaign Intelligence (Complete)

---

## Context

PromptPotter's core optimization loop (L1 generate → L1 evaluate → critique → L2 refine → L3 replan) is functionally complete through M8 with SearchMemory cross-campaign intelligence. Three critical gaps prevent the project from reaching publication and production use:

1. **No benchmark results** — The methodology exists (`docs/benchmarks.md`), the export pipeline is built (`cli/export_results.py`, `services/campaign/export.py`, `services/campaign/reporting.py`), but result tables are all placeholders. HotPotQA and GSM8K dataset configs exist in `configs/datasets/` but no dataset loaders or LLM-only evaluation paths are implemented.

2. **Proof-of-concept meta-prompts** — The optimizer's own prompts (L1 generate, critique, L2 refine, L3 replan) in `promptpotter/config/optimizer_prompts/` are functional but untuned. They were developed against TermNorm (a multi-node retrieval pipeline) and need systematic evaluation on benchmark tasks to find stable, high-performing configurations.

3. **No web interface** — Three entry points exist (notebook, CLI, REST API) but no browser-based UI. The FastAPI API at `/api/v1/` has read-only endpoints for backends, campaigns, and trials. CORS is configured.

Additionally, the publication needs its figures and tables designed upfront so data collection is purposeful.

---

## Competitive Landscape

| System | Origin | Approach | Key Strength |
|--------|--------|----------|-------------|
| DSPy / MIPROv2 | Stanford, 2024 | Bayesian optimization over instructions + few-shot demos | Largest community, full programming framework, ecosystem maturity |
| GEPA | 2025 (now in DSPy) | Reflective prompt evolution, tree of candidates, text feedback | High-quality prompts in few rollouts; +12% over MIPROv2 on AIME-2025 |
| Promptomatix | Salesforce, 2025 | Meta-prompt + DSPy compiler, cost-aware objectives | Competitive performance with reduced prompt length and compute |
| adv-CoT | 2025 | Adversarial generator-discriminator for reasoning tasks | +4.44% on GPT-3.5-turbo across 12 reasoning datasets |
| PromptWizard | Microsoft | Critique-guided generation (PromptPotter's inspiration) | Cost-efficient, simple, strong on single-LLM tasks |

**PromptPotter's differentiators:** Three-layer escalation (L1→L2→L3), SearchMemory cross-campaign learning, pipeline-aware optimization (not just single LLM calls), per-node caching for evaluation efficiency.

---

## Track 1: Publication Readiness

### Problem

The benchmark methodology is fully designed and the export pipeline is production-ready, but zero benchmark results exist. The system has only been tested against TermNorm (terminology normalization via multi-node retrieval pipeline). Academic benchmarks require LLM-only evaluation paths that don't exist yet.

### Deliverables

#### 1a. Dataset Loaders

A `DatasetLoader` protocol with implementations for HotPotQA and GSM8K.

```python
class DatasetLoader(Protocol):
    def load(self) -> list[dict[str, str]]:
        """Return list of {"query": str, "ground_truth": str} dicts."""
        ...
```

- **HotPotQA loader** — Load from HuggingFace `datasets` or local JSON. Validation (distractor) split. 7,405 questions.
- **GSM8K loader** — Load from local JSON (OpenAI format). Test split. 1,319 questions. Extract `#### N` answer format.
- Integration via `campaign.json["dataset_source"]` field — loader name + optional config.

**Key existing code:** `promptpotter/services/dataset_builder.py` (Excel-only), `promptpotter/services/stores/dataset_store.py`.

#### 1b. Evaluation Scorers

- **Token F1 scorer** — Token-level precision/recall/F1 for HotPotQA. Normalize whitespace, lowercase, remove articles/punctuation (standard SQuAD preprocessing).
- **Numeric exact match scorer** — Extract `#### N` from model output, compare numeric value for GSM8K.
- Integration with `shared/scoring.py`'s `compile_scorer()` system via `campaign.json["scoring"]`.

#### 1c. Local LLM Eval Adapter

`eval_query_local()` — calls the target LLM directly via `llm_client.py` instead of routing through `BackendClient.run_query()`. For benchmark tasks where the "pipeline" is a single LLM call.

- Renders the prompt (system + user message) from `JobSearchPoint.pipeline_params`
- Calls the LLM via existing `llm_client.py`
- Returns result in the same format as `eval_query_via_backend()` for downstream compatibility
- **NOT** the full `ConnectorProtocol` from old M9 — minimal, shaped compatibly for future M11 wrapping

**Key existing code:** `promptpotter/services/eval_query.py`, `promptpotter/config/llm_client.py`.

#### 1d. Benchmark Campaigns

- Finalize `campaign.json` configs for HotPotQA and GSM8K in `configs/datasets/`
- Run campaigns with full optimization loop (L1+L2+L3)
- Fill result tables in `docs/benchmarks.md`
- 3 seeds per configuration, 95% Wilson CIs, McNemar's test for significance

#### 1e. Competitor Baselines

Cite published numbers from papers. All competitor results marked as "cited" in results tables with paper reference.

| Method | Source | Datasets |
|--------|--------|----------|
| DSPy Bootstrap | DSPy library | HotPotQA, GSM8K |
| MIPROv2 | Opsahl-Ong et al., 2024 | HotPotQA, GSM8K |
| GEPA | GEPA paper, 2025 | HotPotQA |
| Promptomatix | Salesforce, 2025 | GSM8K |
| adv-CoT | 2025 | GSM8K |
| PromptWizard | Microsoft | GSM8K |

**Note:** Different models and hardware across papers weaken comparison. Use same datasets and metrics for apples-to-apples where possible. If reviewer objects, MIPROv2 is the easiest to reproduce locally (well-packaged library).

#### 1f. Figures

Paper-quality visualizations using matplotlib/plotly:
- Convergence plots (accuracy vs. round)
- Comparison bar charts (PromptPotter vs. competitors)
- Ablation tables
- Cost/efficiency scatter plots

Generation integrated into `cli/export_results.py` or standalone script.

### Key Existing Infrastructure

- `docs/benchmarks.md` — Full methodology, placeholder result tables
- `promptpotter/cli/export_results.py` — Supplemental markdown + JSON export
- `promptpotter/services/campaign/reporting.py` — CI formatting, significance markers, convergence tables
- `promptpotter/services/campaign/export.py` — Pairwise significance, query difficulty, failure clusters
- `configs/datasets/hotpotqa/`, `configs/datasets/gsm8k/` — Pipeline configs, campaign configs, task descriptions

---

## Track 2: Stable Optimizer Configuration

### Problem

The meta-prompts in `promptpotter/config/optimizer_prompts/` are functional but proof-of-concept:

| Prompt | File | Temperature | Max Tokens | State |
|--------|------|-------------|------------|-------|
| L1 Generate | `meta_scan_aware.json` | 0.7 | 8192 | Working, tuned for TermNorm pipeline references |
| Critique | `critique.json` | 0.3 | 4096 | Working, extensive stat assembly |
| Critique (negative) | `critique_negative.json` | 0.3 | 4096 | Fallback for low accuracy |
| L2 Refine | `l2_refine_context.json` | 0.3 | 2048 | Working, clean layer transition |
| L3 Replan | `l3_modify_plan.json` | 0.5 | 2048 | Working, strategic pivots |

Current model: `openai/gpt-oss-120b` via Groq for all optimizer calls.

**Generic approach (decided):** Meta-prompts adapt via `task_context` injection — the `problem_description` and `instruction` fields use template variables. Task-specific details flow through `task_context` without changing base prompts. No task-specific prompt sets.

### Deliverables

#### 2a. Meta-Prompt Evaluation Protocol

Define second-order success metrics measured at the campaign level:

| Metric | What It Measures | Better = |
|--------|-----------------|----------|
| Rounds to convergence | How quickly the optimizer finds a good prompt | Lower |
| Final accuracy | Best accuracy achieved | Higher |
| L2/L3 escalation frequency | How often L1 stalls and needs meta-intervention | Lower |
| Candidate diversity | Variety of generated candidates per round | Higher (avoids mode collapse) |
| Cost (optimizer LLM calls) | Total tokens spent on optimizer calls | Lower |

Run the same benchmark campaign (e.g., HotPotQA with sample_size=100) with different meta-prompt variants. Compare across variants.

#### 2b. Systematic Improvements

- Prompt language refinement (clarity, instruction quality)
- Temperature/max_tokens tuning per node
- `thinking_style` instruction experiments
- `answer_format` schema variations
- Model selection (if alternatives to gpt-oss-120b are available)

#### 2c. Final Config + Documentation

- Document final configs with rationale for each choice
- Feeds paper's "method" section: "Our optimizer uses the following meta-prompt structure..."
- Commit final configs to `promptpotter/config/optimizer_prompts/`

### Dependencies

Track 2 depends on Track 1b/1c — needs benchmark datasets and local LLM eval to evaluate meta-prompt quality. Cannot evaluate on TermNorm alone (retrieval pipeline ≠ LLM-only benchmark task).

### Risk: Bootstrap Cost

Evaluating meta-prompts requires full campaign runs. A single HotPotQA campaign with 100 eval samples, 5 variants, 10 rounds ≈ 5,000 eval LLM calls + optimizer calls. Testing 3 meta-prompt variants = 15,000+ calls. **Mitigation:** Use smaller eval samples (50-100) for meta-prompt evaluation; reserve full 200+ sample runs for final benchmark numbers.

---

## Track 3: Web Application

### Problem

No web UI exists. The FastAPI API at `/api/v1/` has:
- `GET /api/v1/health` — Service status
- `GET /api/v1/backends` — List backends
- `POST /api/v1/backends` — Register backend
- `GET /api/v1/backends/{id}/campaigns` — List campaigns
- `GET /api/v1/backends/{id}/campaigns/{id}` — Campaign detail
- `GET /api/v1/backends/{id}/campaigns/{id}/trials/{n}` — Trial detail

CORS enabled. No frontend consumes these endpoints.

### Technology

**Next.js + React + Tailwind CSS** in `webapp/` directory.

- Next.js: React framework with SSR/SSG, file-based routing, API routes
- Tailwind CSS: Utility-first styling, fast prototyping, consistent design
- Consumes existing FastAPI REST API via HTTP

### Deliverables

#### 3a. Scaffolding

- `webapp/` directory with Next.js project
- API client module for FastAPI endpoints
- Layout shell (nav, sidebar, content area)
- Development proxy to FastAPI backend

#### 3b. Read-Only Views (MVP)

- **Dashboard** — Backend list, campaign summary cards, overall stats
- **Campaign detail** — Convergence chart (accuracy vs. round), trial timeline, best vs baseline comparison
- **Trial inspector** — Prompt diff view (before/after), per-query results table, failure analysis display

#### 3c. Benchmark Results Display

- Comparison tables from `docs/benchmarks.md` data
- Interactive convergence plots
- Ablation results visualization
- Competitor comparison charts

#### 3d. Campaign Launcher + Live Monitoring (Phase 2)

API extensions needed:
- `POST /api/v1/backends/{id}/campaigns` — Start new campaign
- `POST /api/v1/backends/{id}/campaigns/{id}/control` — Pause/resume/stop
- `GET /api/v1/backends/{id}/campaigns/{id}/state` — Live state polling
- WebSocket or SSE endpoint for real-time round progress

Webapp views:
- Campaign configuration form
- Real-time progress dashboard (current round, candidates, accuracy)
- Log viewer

#### 3e. Polish + Deployment

- Production build configuration
- Docker Compose (FastAPI + webapp)
- Environment configuration

### Risk: Scope Creep

"Nice web application" is unbounded. **Mitigation:** 3a-3c are MVP (read-only browser + benchmarks). 3d-3e are Phase 2. Ship MVP before starting Phase 2.

---

## Track 4: Results Design (Publication Figures & Tables)

### Problem

Before running benchmarks, define WHAT to show. What tables, figures, and comparisons make the publication compelling? This drives Track 1's data collection priorities.

### Deliverables

#### 4a. Main Results Table

Design the primary comparison table:

| Column candidates | Include? |
|-------------------|----------|
| Method name | Yes |
| HotPotQA Token F1 | Yes |
| HotPotQA Exact Match | Yes |
| GSM8K Exact Match | Yes |
| Optimizer cost (LLM calls) | TBD — relevant for Promptomatix comparison |
| Wall time | TBD |
| Source (ours/cited) | Yes — transparency |

Methods to include: Zero-shot, Few-shot (manual), DSPy Bootstrap (cited), MIPROv2 (cited), GEPA (cited), Promptomatix (cited), adv-CoT (cited), PromptWizard (cited), PromptPotter (L1 only), PromptPotter (L1+L2), PromptPotter (full).

#### 4b. Convergence Figure

Accuracy vs. round number:
- PromptPotter L1-only, L1+L2, full as separate curves
- Competitor final numbers as horizontal reference lines
- Shaded regions for ±1 std across 3 seeds
- One figure per dataset

#### 4c. Ablation Table

| Ablation | What it isolates |
|----------|-----------------|
| L1 only vs L1+L2 vs full | Value of each optimization layer |
| Scan vs no scan | Value of sensitivity scan seeding |
| SearchMemory on vs off | Value of cross-campaign learning |
| Critique on vs off | Value of separated failure analysis |

#### 4d. Analysis Figures (paper vs supplemental)

| Figure | Paper? | Supplemental? |
|--------|--------|--------------|
| SearchMemory parameter impact heatmap | Maybe | Yes |
| Failure cluster visualization | Maybe | Yes |
| Query difficulty distribution | No | Yes |
| Per-node cache hit rate over rounds | No | Yes |
| L2/L3 escalation timeline | Maybe | Yes |

#### 4e. Cost/Efficiency Comparison

Optimizer LLM calls vs. accuracy gain scatter plot. Positions PromptPotter against Promptomatix (cost-aware) and PromptWizard (cost-efficient).

#### 4f. Documentation

Document all figure and table designs in `docs/publication-figures.md` with mock layouts. This becomes the data collection checklist for Track 1.

### Dependency

Track 4 should be done FIRST or in parallel with Track 1a — it defines what data Track 1 needs to collect.

---

## Wave Sequencing

```
Wave 1: Track 4a-4f + Track 1a + Track 3a
        (results design, dataset loaders, webapp scaffold — parallel, no deps)

Wave 2: Track 1b + Track 1c
        (scorers + local LLM eval adapter)

Wave 3: Track 1d + Track 3b
        (run benchmarks + build read-only webapp views — parallel)

Wave 4: Track 2a
        (meta-prompt evaluation protocol — needs 1b/1c)

Wave 5: Track 1e + Track 1f + Track 2b + Track 3c
        (competitor baselines, figures per 4f spec, prompt improvements, benchmark display)

Wave 6: Track 2c + Track 3d + Track 3e
        (final optimizer config, campaign launcher, polish)
```

---

## Entry Criteria

- M8 exit gate passed (all waves complete) ✅
- All existing tests pass

## Exit Criteria

- [ ] Publication figures and tables designed and documented (`docs/publication-figures.md`)
- [ ] HotPotQA + GSM8K results with statistical rigor (3 seeds, CIs, significance tests)
- [ ] At least one head-to-head comparison against MIPROv2 (cited numbers, same metrics)
- [ ] `docs/benchmarks.md` result tables filled with real numbers
- [ ] Meta-prompts evaluated on ≥2 benchmark datasets with documented rationale for final configs
- [ ] Webapp showing campaign list, campaign detail, trial inspector, benchmark results
- [ ] Campaign can be launched and monitored through the webapp (Phase 2)

---

## Key Existing Code

| Area | Files |
|------|-------|
| Meta-prompts | `promptpotter/config/optimizer_prompts/*.json` |
| Optimizer pipeline config | `promptpotter/config/optimizer_pipeline.py`, `optimizer_pipeline.json` |
| LLM client | `promptpotter/config/llm_client.py` |
| Eval gateway | `promptpotter/services/eval_gateway.py` |
| Eval query | `promptpotter/services/eval_query.py` |
| Scoring | `promptpotter/shared/scoring.py` |
| Dataset builder | `promptpotter/services/dataset_builder.py` |
| Dataset store | `promptpotter/services/stores/dataset_store.py` |
| Export pipeline | `promptpotter/cli/export_results.py`, `services/campaign/export.py`, `services/campaign/reporting.py` |
| FastAPI API | `promptpotter/main.py` |
| Benchmark methodology | `docs/benchmarks.md` |
| Dataset configs | `configs/datasets/hotpotqa/`, `configs/datasets/gsm8k/`, `configs/datasets/lca-termnorm/` |

---

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Bootstrap cost for meta-prompt eval | High compute cost (15K+ LLM calls per variant) | Use 50-100 eval samples for tuning; full samples for final numbers |
| Local LLM eval = mini-connector | Technical debt, future refactor for M11 | Shape interface compatibly but don't build full ConnectorProtocol |
| Webapp scope creep | Unbounded frontend work | MVP scope (3a-3c) before Phase 2 (3d-3e) |
| Cited competitor numbers | Weak comparison (different models/hardware) | Label "cited" clearly; MIPROv2 is easiest to reproduce if challenged |
| TermNorm meta-prompts on LLM-only tasks | Pipeline references irrelevant for benchmarks | Generic prompts via task_context injection; no pipeline-specific language in base prompts |
| Model deprecation | Results unreproducible | Document exact model version in reproducibility manifest |
