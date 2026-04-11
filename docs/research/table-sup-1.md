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

### Experiment mode (default)

The cycle identity hashes only the **problem definition**:
- `active_steps` — which pipeline nodes are active
- `baseline_rendered` — the starting prompt
- `dataset_pairs` — the evaluation questions

Everything else is excluded (`TUNING_KEYS` in `lifecycle.py`):

### Strict mode (for publication)

Enable by adding `"strict_cycle_identity": true` to `campaign.json`:

```json
{
  "campaign_config": {
    "strict_cycle_identity": true,
    ...
  }
}
```


---

## Head-to-Head Benchmark Comparisons

> **Reading these tables:** Each matchup is from a single paper using identical eval conditions (same model, same splits, same scoring). Numbers across matchups are **NOT comparable** — different backbone models, token budgets, and eval protocols. For the full taxonomy, see the [Compound AI Systems Optimization survey](https://arxiv.org/abs/2506.08234) (EMNLP 2025).

### Matchup 1 — GEPA vs MIPROv2 (GEPA paper, ICLR 2026 Oral)

**Source:** [arXiv:2507.19457](https://arxiv.org/abs/2507.19457) | **Inference:** Qwen3-8B | **Optimizer:** undisclosed (frontier)

| Method | HotPotQA | HoVer | PUPA |
|--------|----------|-------|------|
| Baseline | 42.33 | — | — |
| GRPO | 43.33 | — | 86.66 |
| MIPROv2 | 55.33 | 47.33 | 81.55 |
| **GEPA** | **62.33** | **52.33** | **91.85** |

GEPA also reports GPT-4.1 Mini results on same tasks (same relative ranking). On AIME-2025 (GPT-4.1 Mini): GEPA 56.6% vs MIPROv2 46.6%.

### Matchup 2 — CAPO vs field (promptolution paper)

**Source:** [arXiv:2512.02840](https://arxiv.org/abs/2512.02840) (Dec 2025) | **Inference:** Gemma-3-27B | **Optimizer:** Llama-3.3-70B | **Budget:** 1M tokens, 500 dev / 300 test

| Method | GSM8K (test) | SST-5 (test) |
|--------|-------------|-------------|
| Unoptimized | 78.1% | 44.6% |
| OPRO | 69.7% | 56.0% |
| EvoPromptGA | 91.0% | 53.3% |
| **CAPO** | **93.7%** | **56.3%** |
| AdalFlow | 88.7% | 55.7% |
| DSPy (GEPA) | 84.7% | 42.0% |

### Matchup 3 — AdalFlow vs TextGrad vs DSPy (AdalFlow paper)

**Source:** [arXiv:2501.16673](https://arxiv.org/abs/2501.16673) (Jan 2025) | **Inference:** GPT-3.5-turbo-0125 | **Optimizer:** GPT-4o

| Method | ObjectCount | TREC-10 | HotPotQA (Vanilla RAG) | HotPotQA (Multi-hop) | HotPotQA (Agentic) |
|--------|------------|---------|------------------------|---------------------|-------------------|
| DSPy | 82.5% | 81.7% | 42.375% | 47.75% | 31% |
| TextGrad | 84.5% | 84.88% | — | — | — |
| **AdalFlow** | **93.75%** | **87.5%** | **43.25%** | **49.625%** | **32.25%** |

### Matchup 4 — Trace vs DSPy (Trace paper, NeurIPS 2024)

**Source:** [arXiv:2406.16218](https://arxiv.org/abs/2406.16218) (Jun 2024) | **Inference:** GPT-3.5-turbo-1106 | **Optimizer:** GPT-4

| Method | BBH All (23 tasks) | BBH Algorithmic (11 tasks) |
|--------|-------------------|---------------------------|
| DSPy+CoT | 70.4% | — |
| DSPy-PO+CoT | 71.6% | 70.0% |
| **Trace+CoT** | **78.6%** | **80.6%** |

3x faster wall-clock time than TextGrad with comparable or better accuracy.

### Matchup 5 — AFlow vs ADAS (AFlow paper, ICLR 2025 Oral)

**Source:** [arXiv:2410.10762](https://arxiv.org/abs/2410.10762) (Oct 2024) | **Optimizer:** Claude-3.5-Sonnet | **Inference:** GPT-4o-mini, DeepSeek-V2.5, Claude-3.5-Sonnet, GPT-4o (all tested)

| Method | GSM8K | HotPotQA | MATH | Avg (6 benchmarks) |
|--------|-------|----------|------|---------------------|
| ADAS | 81.3% | 78.5% | 68.7% | — |
| **AFlow** | **83.5%** | **77.9%** | **82.9%** | **80.3%** |

AFlow enables GPT-4o-mini + optimized workflow to outperform GPT-4o + manual workflow at 4.55% of inference cost.

---

## Key Papers & Venues

| Paper | Venue | Year | Category |
|-------|-------|------|----------|
| [TextGrad](https://arxiv.org/abs/2406.07496) | Nature | 2024 | Compound system optimization |
| [Trace/OPTO](https://arxiv.org/abs/2406.16218) | NeurIPS | 2024 | Workflow optimization |
| [MIPROv2](https://arxiv.org/abs/2406.11695) | EMNLP | 2024 | Multi-stage prompt optimization |
| [AFlow](https://arxiv.org/abs/2410.10762) | ICLR (Oral) | 2025 | Workflow architecture search |
| [ADAS](https://arxiv.org/abs/2408.08435) | ICLR | 2025 | Agent architecture search |
| [PromptWizard](https://arxiv.org/abs/2405.18369) | — | 2024 | Critique-guided prompt optimization |
| [metaTextGrad](https://arxiv.org/abs/2505.18524) | NeurIPS | 2025 | Meta-optimization |
| [GEPA](https://arxiv.org/abs/2507.19457) | ICLR (Oral) | 2026 | Reflective prompt evolution |
| [CAPO/promptolution](https://arxiv.org/abs/2512.02840) | — | 2025 | Evolutionary prompt optimization |
| [AdalFlow](https://arxiv.org/abs/2501.16673) | — | 2025 | LLM AutoDiff (compound systems) |
| [Optimas](https://arxiv.org/abs/2507.03041) | ICLR | 2026 | Multi-component compound systems |
| [TextResNet](https://arxiv.org/abs/2602.08306) | — | 2026 | Deep compound systems (residual) |
| [TEP](https://arxiv.org/abs/2601.21064) | ICLR | 2026 | Deep compound systems (equilibrium) |
| [Survey](https://arxiv.org/abs/2506.08234) | EMNLP | 2025 | Compound AI optimization taxonomy |

---

## Hyperparameter Reference

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
