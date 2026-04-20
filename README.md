<p align="center">
  <img src="docs/assets/wizard.jpg" alt="PromptPotter wizard" width="100">
  <img src="docs/assets/promptpotter-wordmark.png" alt="PromptPotter" width="420">

</p>

**PromptPotter brews better prompts.** Drop in a labeled dataset, and it searches prompts and pipeline parameters jointly — critique-guided, statistically early-stopped, and every evaluation banked for the next run.

## TL;DR

Give it a labeled dataset and an LLM. PromptPotter systematically searches for a better prompt — measuring accuracy, critiquing failures, and iterating through a 3-layer optimization loop. Same setup as academic benchmarks (HotPotQA, GSM8K), same loop for complex pipelines. Exports paper-ready results with confidence intervals and significance tests. See [`docs/research/benchmarks.md`](docs/research/benchmarks.md) for the comparison protocol.

At its core, PromptPotter just collects a lot of datapoints. Every evaluation is stored, every parameter combination is tracked, and the optimizer uses this accumulated evidence to make better decisions each round.

## ⭐ Features

- **🔁 Self-healing optimization** — when the optimizer hallucinates a value your backend doesn't accept (e.g. `model: gpt-4o`), PromptPotter catches it before any API call, scores the candidate 0, and tells the strategy layer what went wrong so the next round stops repeating the mistake.
- **Prompt + pipeline optimization** — searches the prompt (8-field decomposition) AND your pipeline parameters jointly. Most tools optimize one or the other.
- **Statistical early-stopping** — sequential elimination via paired Wilcoxon signed-rank test stops inferior candidates after ~6 queries instead of running the full eval set. Real cost is well below `n_variants × eval_size`.
- **Cross-run learning** — every evaluation flows into a shared `SearchMemory` store: parameter impact, axis exhaustion, query tractability, failure-group correlations. The optimizer carries what it learned across runs.
- **Auto-injected scoring** — define your scoring formula once in `campaign.json`. It compiles into every eval path automatically. No glue code.
- **IDE-native operation** — drive a full optimization campaign from the terminal via the `/potter-run` Claude Code skill. No notebook required.

For a head-to-head comparison with other prompt-optimization frameworks, see [`docs/research/README.md`](docs/research/README.md). For the self-healing architecture in detail, see [`docs/architecture/optimization.md`](docs/architecture/optimization.md).

Your backend's monolithic prompt gets decomposed into independent, optimizable fields:

```
┌─ PROMPT SCHEME ──────────────────────────┐
│  1. task_intent                          │
│  2. problem_description                  │
│  3. instruction                          │
│  4. thinking_style                       │
│  5. answer_format                        │
│  6. domain_constraints                   │
│  +/- [???] auto configuring              │
└──────────────────────────────────────────┘
```

## The Workflow

**Core path (what everyone runs):**

1. **Provide a labeled dataset** — input/output pairs (and any extra context)
2. **Provide your `pipeline.json`** — your backend serves this via `GET /pipeline`. It declares every node, its parameters, and their allowed values. The optimizer only searches parameters defined in this file — nothing else is touched.
3. **Optimize** — run the critique-guided feedback cycle. The optimization loop is self-contained: it measures the baseline, generates candidates, scores them, critiques failures, and iterates.

The primary UI is a Jupyter notebook, backed by a FastAPI service designed to work with any LLM pipeline backend.

## How It Works

[![PromptWizard](https://img.shields.io/badge/inspired_by-PromptWizard-blue)](https://arxiv.org/abs/2405.18369)
[![BBEH](https://img.shields.io/badge/benchmark-BBEH-purple)](https://github.com/google-deepmind/bbeh)
[![DSPy](https://img.shields.io/badge/compared_against-DSPy-green)](https://github.com/stanfordnlp/dspy)
[![CAPO](https://img.shields.io/badge/compared_against-CAPO-orange)](https://arxiv.org/abs/2504.16005)

```
  ┌──────────────────────────────────────────────────────────┐
  │  l1_generate ────► l1_evaluate (+ critique)              │
  │       ▲                 │                                │
  │       │  critique OR l2_directive                         │
  │       │  + thinking_styles                               │
  │       └──────── ◄───────┘                                │
  │                                                          │
  │  stall?       ──► l2_refine_strategy ──► resume L1        │
  │  degradation? ──► l2_refine_strategy ──► resume L1        │
  │  l2 stall?    ──► l3_modify_plan    ──► resume L2+L1     │
  └──────────────────────────────────────────────────────────┘
```

### 🔄 The 3-layer loop

A **critique-guided** feedback cycle: each round generates candidates, scores them against your dataset, and produces a structured **critique** of failures that feeds forward into the next round — alongside sampled **thinking styles** as mutation guidance. This separates failure analysis from candidate generation (inspired by [PromptWizard](https://arxiv.org/abs/2405.18369)'s critique-and-refine pattern). 3-layer escalation: **L1** generates prompt + parameter candidates every round, **L2** (context) adjusts when L1 stalls, **L3** (strategy) rarely changes. Five LLM call sites: `restructure`, `l1_generate`, `critique`, `l2_context`, `l3_plan`.

Confidence intervals and two-proportion significance tests are built into the candidate comparison. Non-parametric tests are planned.

> [!TIP]
> **Five ways to drive a campaign** — pick whichever fits your workflow:
> 1. `/potter-run` skill via Claude Code
> 2. REST API (`uvicorn promptpotter.main:app`)
> 3. CLI (`python -m promptpotter optimize`)
> 4. Python / Jupyter notebook
> 5. WebApp *(planned)*

> [!IMPORTANT]
> PromptPotter requires a backend that exposes a `/matches` evaluation endpoint and a `GET /pipeline` endpoint that declares every node, its parameters, and their allowed values. The optimizer searches **only** parameters declared in `pipeline.json` — nothing else is touched. Currently tested with [TermNorm-excel](https://github.com/runfish5/TermNorm-excel). See [Backend Requirements](docs/setup-guide.md#backend-requirements) for the full contract.

### 🧬 SearchPoint — the unit of evaluation

Every evaluation is a **SearchPoint**: content-hashable, stored once, discoverable by any workflow. The decomposed prompt fields and pipeline parameters live together inside it, which is what makes joint search possible in the first place. See [prompt scheme](docs/architecture/prompt-scheme.md) for the SearchPoint hierarchy and alias groups.

## Documentation

| 🏗 Architecture | ⚙ Operations | 🔬 Research |
|---|---|---|
| [Overview](docs/architecture/overview.md) | [Setup guide](docs/setup-guide.md) | [Benchmarks](docs/research/benchmarks.md) |
| [Optimization loop](docs/architecture/optimization.md) | [CLI workflow](docs/cli-workflow.md) | [SearchMemory intelligence](docs/architecture/search-memory-intelligence.md) |
| [Prompt scheme](docs/architecture/prompt-scheme.md) | [Observability](docs/observability.md) | |
| [Information flow](docs/architecture/information-flow.md) | | |
| [Node standard](docs/architecture/node-standard.md) | | |

### 🧠 Cross-run memory

Every evaluation flows into a shared `intelligence/` store (`SearchMemory`) — parameter impact, axis exhaustion, query tractability, failure-group correlations. The optimizer carries what it learned forward into the next run instead of starting cold.

<details>
<summary><b>What a round actually looks like</b> (click to expand)</summary>

```
round 3/10 · 5 candidates · sp_budget_ttest=40
├─ c0  seed                             acc=0.62  [baseline]
├─ c1  +thinking_style:step-by-step     acc=0.74  ✓
├─ c2  +thinking_style:socratic          acc=0.71
├─ c3  +persona:domain expert           acc=0.68  ✗ eliminated @ q18 (t-test)
└─ c4  model:gpt-oss-120b→… ⚠ invalid   acc=0.00  ↳ validation_failure
                                                     → L2 directive next round

winner: c1  (+12pp over baseline, p=0.003)
critique: "Step-by-step improves multi-hop reasoning. Socratic overlaps
           but adds no marginal gain. Persona drift hurt format compliance."
```

</details>


## Limitations

- **Parameter-based optimization only** — PromptPotter optimizes any pipeline that exposes tunable parameters (prompts, thresholds, model settings). It cannot optimize internal model weights, neural architectures, or modality-specific representations (e.g. image embeddings, DNA sequences).
- **Requires a labeled dataset** — you need input/output pairs. No labeled data, no optimization.
- **Langfuse dependency** — observability is currently coupled to Langfuse (v2). It works but adds operational complexity and is not optional for full tracing.

## Benchmarks

Head-to-head comparison on BBEH (Big-Bench Extra Hard) against DSPy optimizers (GEPA, MIPROv2, BootstrapFewShot) and CAPO. Same model (`gpt-oss-120b`), same dataset splits, same scoring — no cross-paper number mixing. See [`docs/research/benchmarks.md`](docs/research/benchmarks.md) for results and [`docs/research/bbeh-comparison/`](docs/research/bbeh-comparison/) for reproducible Colab notebooks.

### 🔌 Backend contract

PromptPotter requires a backend that exposes a `/matches` evaluation endpoint and a `GET /pipeline` endpoint that declares every node, its parameters, and their allowed values. The optimizer searches **only** parameters declared in `pipeline.json` — nothing else is touched. Currently tested with [TermNorm-excel](https://github.com/runfish5/TermNorm-excel). See [Backend Requirements](docs/setup-guide.md#backend-requirements) for the full contract.

```
┌──────────────────────┐                       ┌──────────────────────┐
│  Your Backend        │  GET  /pipeline   ──► │  PromptPotter        │
│  (any pipeline)      │                       │  Optimizer           │
│                      │  POST /matches    ◄── │                      │
│  runs the task       │   {prompt, params}    │  generates candidates│
│                      │                       │  scores + critiques  │
│                      │  → predictions    ──► │  iterates            │
└──────────────────────┘                       └──────────────────────┘
```

## Getting Started

### Local install

```bash
pip install -e ".[all,dev]"
cp .env.example .env   # add your LLM API keys (Groq, OpenAI, or Anthropic)
```

The `[all]` extras bundle enables every optional feature (Excel datasets, HuggingFace benchmarks, Langfuse tracing, Anthropic client, JupyterLab). For a minimal install pick only the extras you need — see [Setup Guide](docs/setup-guide.md).

### Docker (one command)

```bash
cd docker && docker-compose up --build
# JupyterLab: http://localhost:8888  |  API: http://localhost:8001
```

See the [Setup Guide](docs/setup-guide.md) for prerequisites and first run, and the [CLI Workflow](docs/cli-workflow.md) for the full command reference.




