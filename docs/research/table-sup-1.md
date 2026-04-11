# Supplementary Table 1: Related Tools Comparison

For academic optimization methods (DSPy/MIPROv2, GEPA, PromptWizard, adv-CoT, Promptomatix) and benchmark accuracy numbers, see [`benchmarks.md`](benchmarks.md).

---

## Highlights

| Capability | PP | po | pf | How in PromptPotter |
|------------|:--:|:--:|:--:|---------------------|
| **Auto-injected scoring** | 🟢 | 🔴 | 🔴 | `compile_scorer()` — per-dataset formula from `campaign.json`, compiled once, injected into all eval paths |
| **IDE-native operation** | 🟢 | 🔴 | 🔴 | `/potter-run` Claude Code skill — full campaign lifecycle from the terminal |
| **Prompt + pipeline optimization** | 🟢 | 🔴 | 🔴 | 8-field prompt decomposition + per-node `pipeline_params` — optimizes prompts AND pipeline config jointly |
| **Statistical early-stopping** | 🟢 | 🟡 | 🔴 | Sequential elimination via paired Welch's t-test + Holm-Bonferroni (α=0.05) after 20 queries |
| **Cross-run learning** | 🟢 | 🔴 | 🔴 | SearchMemory — parameter impact, axis exhaustion, value trends, query tractability, failure-group × axis correlation |

---

## Feature Matrix

| Dimension | PP | po | pf | PromptPotter | promptolution | promptfoo |
|-----------|:--:|:--:|:--:|-------------|---------------|-----------|
| **Language** | — | — | — | Python 3.13+ | Python 3.10–3.12 | TypeScript |
| **Adoption** | 🟡 | 🔴 | 🟢 | Research/production tool | 126 stars (academic, AutoML group) | 19.9k stars, 300K+ users, acquired by OpenAI |
| **Core approach** | 🟢 | 🟢 | 🔴 | Critique-guided L1→L2→L3 loop | Evolutionary (GA, DE) + LLM-as-optimizer (OPRO) + hybrid (CAPO) | Manual A/B testing (human writes all variants) |
| **Multi-step pipeline** | 🟢 | 🔴 | 🔴 | Per-node params, PipelineSchema from backend | No — single LLM call only | Single LLM call (custom script for multi-step) |
| **Budget control** | 🟢 | 🟡 | 🟡 | `sp_budget_ttest` (adaptive), early-stopping | Token budget callback (`max_tokens_for_termination`) | `maxConcurrency`, `repeat`, `timeoutMs` |
| **Scoring** | 🟡 | 🟡 | 🟢 | Composite formula (`compile_scorer()`), custom per-dataset | `accuracy_score` (classification), reward function, LLM-as-judge | 40+ assertion types (deterministic + model-graded) |
| **Candidate selection** | 🟢 | 🟡 | 🔴 | Sequential elimination, Welch's t-test early-stop | CAPO: paired t-test racing (α=0.2). Others: full eval or subsampling | Pass/fail assertions, weighted aggregation |
| **Cross-run learning** | 🟢 | 🔴 | 🔴 | SearchMemory (parameter impact, axis exhaustion, failure groups) | None (in-memory only, lost on exit) | None (each eval independent) |
| **Few-shot optimization** | 🟡 | 🟢 | 🔴 | Pipeline-level (backend handles examples) | CAPO: joint instruction + few-shot optimization | Not applicable (manual) |
| **Prompt representation** | 🟢 | 🔴 | 🔴 | 8-field decomposition (persona, task_intent, etc.) | Opaque string (monolithic instruction) | Opaque string templates (Nunjucks) |
| **Red teaming** | 🔴 | 🔴 | 🟢 | — | — | 50+ vulnerability types, dedicated pipeline |
| **RAG / agent metrics** | 🔴 | 🔴 | 🟢 | — | — | context-faithfulness/-recall/-relevance, trajectory assertions |
| **Persistence** | 🟢 | 🔴 | 🟡 | Two-tier (session + campaign store), content-addressed archival | None (in-memory; FileOutputCallback writes parquet/csv post-hoc) | Disk cache for LLM responses only |
| **Provider ecosystem** | 🟡 | 🟡 | 🟢 | Backend-agnostic (single BackendClient endpoint) | OpenAI-compatible API, HuggingFace local, vLLM | 50+ built-in (OpenAI, Anthropic, Groq, Bedrock, etc.) |
| **CI/CD** | 🔴 | 🔴 | 🟢 | — | — | GitHub Actions, GitLab, Jenkins, Azure Pipelines, etc. |

---

## Hyperparameter Reference

### promptolution

**Benchmark results** (Gemma-3-27B, 1M token budget, 500 dev / 300 test):

| Method | GSM8K (test) | SST-5 (test) |
|--------|-------------|-------------|
| Unoptimized | 78.1% | 44.6% |
| OPRO | 69.7% | 56.0% |
| EvoPromptGA | 91.0% | 53.3% |
| CAPO | **93.7%** | **56.3%** |
| AdalFlow | 88.7% | 55.7% |
| DSPy (GEPA) | 84.7% | 42.0% |

**Paper:** [arXiv:2512.02840](https://arxiv.org/abs/2512.02840) (Dec 2025) | **Authors:** LMU Munich / MCML / ELLIS / Uni Freiburg / TUM


### promptfoo

**URL:** <https://www.promptfoo.dev/> | **Backing:** Acquired by OpenAI | **SOC2 + ISO 27001** (enterprise)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `maxConcurrency` | 4 | Concurrent API requests |
| `repeat` | 1 | Run each test N times |
| `max_tokens` | — | Max generation length |
| `top_p` | — | Nucleus sampling |
| `threshold` (assertion) | varies | Pass/fail boundary (0.75 rouge-n, 0.5 BLEU/METEOR) |
| `--num-tests` (redteam) | varies | Test cases per attack plugin |
| `--plugins` (redteam) | "default" (11) | Attack plugin list |
