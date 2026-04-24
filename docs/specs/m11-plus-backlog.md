# M11+: Backlog

**Version:** 0.1.0
**Date:** 2026-04-12
**Status:** Unscheduled
**Depends on:** M11 (Multi-Connector, Competitor Comparison, Webapp Phase 2)

---

## Context

M11+ is the opportunistic bucket. Items here ship after M11 as user demand, time, and research interest dictate. Nothing here is blocking. Each item is a candidate for its own spec when picked up.

## Items

### Polish & Operations

| Item | Notes |
|------|-------|
| Cost tracking | Token usage and $ cost per campaign / round / variant. Feeds cost-aware optimizer strategies and reproducibility manifests |
| API tests | Deferred from entry-point parity work |
| Health endpoint | `GET /api/v1/health` with dependency status (backend, store, LLM provider) |
| CONTRIBUTING.md | External contributor onboarding |
| Docker hardening | Non-root user, minimal base image, secrets handling |
| Notebook tests | Catch notebook import drift |
| Metrics | Prometheus exporter for campaign progress, queue depth, LLM latency |
| Fat-file splits | Each offender from M9 hierarchy refactor gets its own splitting spec. See `m9-hierarchy-refactor.md` § "Fat Files" |

### New Capabilities

| Item | Notes |
|------|-------|
| Multimodal / non-text modalities | RNAseq, X-ray, image, audio. Requires modality-specific evaluation, dataset formats, scoring functions |
| Pipeline variant comparison | Compare pipelines (not just searchpoints within a pipeline). Needs ConnectorProtocol from M11 |
| Non-prompt targets | Optimize scoring functions, fuzzy matchers, retrieval queries, GA settings — not just prompt strings |
| Evolutionary operators | Population-based search (GA / DE) as an alternative to critique-guided generation |
| MCP server mode | Expose PromptPotter as MCP tools to Claude Code and other MCP clients |
| Self-optimization | PromptPotter optimizes its own meta-prompts recursively. The final proof-of-method. **Mechanical groundwork landed (2026-04-12):** all 5 meta-prompts (`meta_scan_aware`, `l1_critique`, `l2_refine_strategy`, `l3_modify_plan`, `restructure`) are fully-decomposed `PromptTemplate` instances under `application/optimization/prompts/`, and the `potter_traces` dataset loader (`application/datasets/trace_dataset.py`) emits `{round_context → next_directive → score_delta}` rows from archived campaigns. **Remaining blockers:** (1) credit assignment / outer-loop cost — no cheap proxy reward for a meta-prompt, so every outer trial is otherwise an entire inner campaign; need a trusted replay surrogate. (2) "PromptPotter-as-backend" adapter — a thin shim exposing `POST /match` that internally invokes L1/l1_critique/L2/L3 against a fixed trace-replay fixture, plus a `pipeline.json` describing those nodes. **Suggested wedge:** start with the l1_critique node alone — smallest I/O surface, offline-scorable via "did the directive predict the axis the next round moved on." See [`../architecture/prompt-scheme.md § Optimizer Meta-Prompts`](../architecture/prompt-scheme.md#optimizer-meta-prompts) and [§ Potter Trace Dataset](../architecture/prompt-scheme.md#potter-trace-dataset) |
| Model comparison matrix | Same benchmark across multiple target LLMs |
| Web scrape ablation | Quality vs cost/latency tradeoff for backends that do web retrieval |
| Public service deployment | Auth, rate limiting, multi-tenancy hardening, billing. Builds on M9's `TenantContext` + M11's enforcement |

### Research Extensions

| Item | Notes |
|------|-------|
| Further OptSearchPoint refinement | Advanced L1/L2/L3 strategies surfaced during M10 ablations |
| Diminishing returns detector | Critique (anomaly flag) + L2 (strategic context) — signal when optimization is plateauing |
| Candidate diversity monitor | L2 — detect mode collapse in candidate generation |
| Query improvement attribution | Critique (this-round) + L2 (cross-round patterns) — track which prompt changes drove which query flips |
| Cross-candidate failure diff | Critique — missed opportunities from non-winner candidates |
| Failure group refresh in loop | L2 — periodic recomputation of failure group × axis correlations during optimization |
| Additional benchmarks | Beyond HotPotQA + GSM8K — MMLU, BBH, LiveCodeBench, domain-specific |
| Longer-horizon ablations | SearchMemory value over N campaigns, learning curves |
| Human evaluation | Qualitative prompt quality assessment beyond task accuracy |

## Prioritization

None. M11+ items are pulled based on user demand, research direction, and who's asking. When an item becomes hot, promote it to its own spec in `docs/specs/`.
