<p align="center">
  <img src="docs/assets/wizard.jpg" alt="PromptPotter wizard" width="100">
  <img src="docs/assets/promptpotter-wordmark.png" alt="PromptPotter" width="420">

</p>

**PromptPotter brews better prompts.** Drop in a labeled dataset, and it searches prompts and pipeline parameters jointly — critique-guided, statistically early-stopped, and every evaluation banked for the next run.

> [!IMPORTANT]
> **New here? Start with [`docs/manual/`](docs/manual/README.md)** — six numbered chapters, install → first run → reading output → troubleshooting.

## TL;DR

Give it a labeled dataset and an LLM. PromptPotter systematically searches for a better prompt — measuring accuracy, critiquing failures, and iterating through a 3-layer optimization loop. Same setup as academic benchmarks (HotPotQA, GSM8K), same loop for complex pipelines. Exports paper-ready results with confidence intervals and significance tests. See [`docs/research/benchmarks.md`](docs/research/benchmarks.md) for the comparison protocol.

At its core, PromptPotter just collects a lot of datapoints. Every evaluation is stored, every parameter combination is tracked, and the optimizer uses this accumulated evidence to make better decisions each round.


## The Workflow

**Core path (what everyone runs):**

1. **Provide a labeled dataset** — input/output pairs (and any extra context)
2. **Provide your `pipeline.json`** — your backend serves this via `GET /pipeline`. It declares every node, its parameters, and their allowed values. The optimizer only searches parameters defined in this file — nothing else is touched.
3. **Optimize** — run the critique-guided feedback cycle. The optimization loop is self-contained: it measures the baseline, generates candidates, scores them, runs L1 critique on failures, and iterates.


## ⭐ Features

- **🔁 Self-healing optimization** — when the optimizer hallucinates a value your backend doesn't accept (e.g. `model: gpt-4o`), PromptPotter catches it before any API call, scores the candidate 0, and tells the strategy layer what went wrong so the next round stops repeating the mistake. Full architecture: [self-healing.md](docs/concepts/self-healing.md).
- **Prompt + pipeline optimization** — searches the prompt (8-field decomposition) AND your pipeline parameters jointly. Most tools optimize one or the other. Head-to-head: [related-work.md](docs/research/related-work.md).
- **🔍 Hard-sample sorter** *(WIP)* — point it at your dataset + a handful of candidate prompts, get back a Rasch-ranked difficulty list and a candidate × sample hit/miss heatmap. Works standalone — no optimizer loop required. Spec: [`docs/specs/hard-sample-sorter.md`](docs/specs/hard-sample-sorter.md).
- **Statistical early-stopping** — sequential elimination via paired Wilcoxon signed-rank test stops inferior candidates after ~6 queries instead of running the full eval set. Real cost is well below `n_variants × eval_size`.
- **Cross-run learning** — every evaluation flows into a shared `SearchMemory` store: parameter impact, axis exhaustion, query tractability, failure-group correlations. The optimizer carries what it learned across runs.
- **Auto-injected scoring** — define your scoring formula once in `campaign.json`. It compiles into every eval path automatically. No glue code.
- **Scoring as policy, not data** — per-trace `scorer_id` ledger + rescore-on-load; resume replays recorded decisions against rescored inputs and halts on first divergence. `promptpotter fork` re-roots with `parent_cycle_id`. [`scoring-and-traces.md`](docs/concepts/scoring-and-traces.md)
- **Symmetric mid-output tail lookup** — one answer-extraction primitive (same regex on prediction and ground truth, last match wins) backs `\boxed{…}`, `**…**`, and GSM8K `#### N`. Prose-wrapped answers score cleanly without per-dataset parsers.
- **IDE-native operation** — drive a full optimization campaign from the terminal via the `/potter-run` Claude Code skill. No notebook required.

## 🔄 The 3-layer loop

A **critique-guided** feedback cycle: each round generates candidates, scores them, and produces a structured **L1 critique** that steers the next round — with **L2** escalating on stall and **L3** escalating when L2 stalls. Full mechanics in [three-layer-loop.md](docs/concepts/three-layer-loop.md).

> [!TIP]
> <details>
> <summary><b>What a round actually looks like</b> (click to expand)</summary>
> 
> ```
> round 3/10 · 5 candidates · sp_budget_ttest=40
> ├─ c0  seed                             acc=0.62  [baseline]
> ├─ c1  +thinking_style:step-by-step     acc=0.74  ✓
> ├─ c2  +thinking_style:socratic          acc=0.71
> ├─ c3  +persona:domain expert           acc=0.68  ✗ eliminated @ q18 (t-test)
> └─ c4  model:gpt-oss-120b→… ⚠ invalid   acc=0.00  ↳ validation_failure
>                                                      → L2 directive next round
> 
> winner: c1  (+12pp over baseline, p=0.003)
> L1 critique: "Step-by-step improves multi-hop reasoning. Socratic overlaps
>               but adds no marginal gain. Persona drift hurt format compliance."
> ```
> 
> </details>

## Benchmarks

[![PromptWizard](https://img.shields.io/badge/inspired_by-PromptWizard-blue)](https://arxiv.org/abs/2405.18369)
[![BBEH](https://img.shields.io/badge/benchmark-BBEH-purple)](https://github.com/google-deepmind/bbeh)
[![DSPy](https://img.shields.io/badge/compared_against-DSPy-green)](https://github.com/stanfordnlp/dspy)
[![CAPO](https://img.shields.io/badge/compared_against-CAPO-orange)](https://arxiv.org/abs/2504.16005)

Head-to-head comparison on BBEH (Big-Bench Extra Hard) against DSPy optimizers (GEPA, MIPROv2, BootstrapFewShot) and CAPO. Same model (`gpt-oss-120b`), same dataset splits, same scoring — no cross-paper number mixing. See [`docs/research/benchmarks.md`](docs/research/benchmarks.md) for results and [`docs/research/bbeh-comparison/`](docs/research/bbeh-comparison/) for reproducible Colab notebooks.

## How It Works

```
  ┌──────────────────────────────────────────────────────────┐
  │  l1_generate ────► l1_evaluate (+ l1_critique)            │
  │       ▲                 │                                │
  │       │  l1_critique OR l2_directive                      │
  │       │  + thinking_styles                               │
  │       └──────── ◄───────┘                                │
  │                                                          │
  │  stall?       ──► l2_refine_strategy ──► resume L1        │
  │  degradation? ──► l2_refine_strategy ──► resume L1        │
  │  l2 stall?    ──► l3_modify_plan    ──► resume L2+L1     │
  └──────────────────────────────────────────────────────────┘
```

> [!IMPORTANT]
> **Five ways to drive a campaign** — pick whichever fits your workflow:
> 1. `/potter-run` skill via Claude Code
> 2. REST API (`uvicorn promptpotter.main:app`)
> 3. CLI (`python -m promptpotter optimize`)
> 4. Python / Jupyter notebook
> 5. WebApp *(planned)*

## Documentation

| 🧠 Concepts | ⚙ Operations | 🔬 Research |
|---|---|---|
| [Campaign lifecycle](docs/concepts/campaign-lifecycle.md) | [CLI reference](docs/operations/cli-reference.md) | [Benchmarks](docs/research/benchmarks.md) |
| [Three-layer loop](docs/concepts/three-layer-loop.md) | [Environment](docs/operations/environment.md) | [Metrics (HC, SE, R₉₀)](docs/research/metrics.md) |
| [Self-healing](docs/concepts/self-healing.md) | [🔌Backend integration](docs/operations/backend-integration.md) | [Related work](docs/research/related-work.md) |
| [Scoring and traces](docs/concepts/scoring-and-traces.md) | [Persistence and state](docs/operations/persistence-and-state.md) | |
| [Search memory](docs/concepts/search-memory.md) | [Rewind and fork](docs/operations/rewind-and-fork.md) | |
| [Prompts and candidates](docs/concepts/prompts-and-candidates.md) | [Observability](docs/operations/observability.md) | |
| [Nodes and pipelines](docs/concepts/nodes-and-pipelines.md) | | |

Developer internals (Python symbols, data contracts, wiring) live under [`docs/developer/`](docs/developer/README.md). Statistical foundations under [`docs/methods/`](docs/methods/README.md).

## Limitations

- **Parameter-based optimization only** — PromptPotter optimizes any pipeline that exposes tunable parameters (prompts, thresholds, model settings). It cannot optimize internal model weights, neural architectures, or modality-specific representations (e.g. image embeddings, DNA sequences).
- **Requires a labeled dataset** — you need input/output pairs. No labeled data, no optimization.
- **Langfuse dependency** — observability is currently coupled to Langfuse (v2). It works but adds operational complexity and is not optional for full tracing.

## Getting Started

### Local install

```bash
pip install -e ".[all,dev]"
cp .env.example .env   # add your LLM API keys (Groq, OpenAI, or Anthropic)
```

The `[all]` extras bundle enables every optional feature (Excel datasets, HuggingFace benchmarks, Langfuse tracing, Anthropic client, JupyterLab). For a minimal install pick only the extras you need — see [Environment](docs/operations/environment.md).

### Docker (one command)

```bash
cd docker && docker-compose up --build
# JupyterLab: http://localhost:8888  |  API: http://localhost:8001
```

See the [Manual](docs/manual/README.md) for prerequisites and first run, and the [CLI Reference](docs/operations/cli-reference.md) for the full command reference.




