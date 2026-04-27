<p align="center">
  <img src="docs/assets/wizard.jpg" alt="PromptPotter wizard" width="100">
  <img src="docs/assets/promptpotter-wordmark.png" alt="PromptPotter" width="420">

</p>

# PromptPotter: Automated Prompt Optimization for LLMs and Pipelines

**PromptPotter brews better prompts.** Point it at your task and it tries thousands of prompt and parameter combinations for you, keeping what works. No more guessing which wording the model likes.

## Why PromptPotter?

Manual prompt tuning is slow, inconsistent, and the lessons don't carry over to the next project. PromptPotter automates the loop: it tries variations, measures what works, and remembers across runs. Whether you're an office worker iterating on the same daily report or an AI agent learning a new tool, you get a better prompt without the trial and error.

Under the hood, PromptPotter just collects a lot of datapoints. Every evaluation is stored, every parameter combination is tracked, and the optimizer uses this accumulated evidence to make better decisions each round.


## The Workflow

**Core path (what everyone runs):**

1. **Provide a labeled dataset:** input/output pairs (and any extra context)
2. **Provide your `pipeline.json`:** your backend serves this via `GET /pipeline`. It declares every node, its parameters, and their allowed values. The optimizer only searches parameters defined in this file. Nothing else is touched.
3. **Optimize:** run the critique-guided feedback cycle — PromptPotter's flavour of **LLM-driven program evolution**. The optimization loop is self-contained. It measures the baseline, generates candidates, scores them, runs L1 critique on failures, and iterates.

> [!IMPORTANT]
> **New here? Start with [`docs/manual/`](docs/manual/README.md):** six numbered chapters, install → first run → reading output → troubleshooting.
>
> **Choose your personal experiance:** 5 was to operate the software:
> 1. `/potter-run` skill via Claude Code - 2. CLI- 3. Python / Jupyter notebook - 4. REST API - 5. WebApp *(planned)*

## 🔄 The 3-layer loop

A **critique-guided** feedback cycle: each round generates candidates, scores them, and produces a structured **L1 critique** that steers the next round, with **L2** escalating on stall and **L3** escalating when L2 stalls. Full mechanics in [three-layer-loop.md](docs/concepts/three-layer-loop.md).

```
  ┌──────────────────────────────────────────────────────────┐
  │  l1_generate ────► l1_evaluate (+ l1_critique)            │
  │       ▲                 │                                │
  │       │  l1_critique OR l2_directive                      │
  │       └──────── ◄───────┘                                │
  │                                                          │
  │  stall?       ──► l2_refine_strategy ──► resume L1        │
  │  degradation? ──► l2_refine_strategy ──► resume L1        │
  │  l2 stall?    ──► l3_modify_plan    ──► resume L2+L1     │
  └──────────────────────────────────────────────────────────┘
```

## ⭐ Features

- **Prompt + pipeline optimization:** **LLM-driven program evolution** over your prompt AND your pipeline parameters jointly. Most tools optimize one or the other. Head-to-head: [related-work.md](docs/research/related-work.md).
- **Auto-injected scoring:** define your scoring formula once in `campaign.json`. It's wired into every evaluation path automatically. No glue code.
- **IDE-native operation:** drive a full optimization campaign from your terminal via the `/potter-run` Claude Code skill. No notebook required.
- **🔁 Self-healing optimization:** when a proposed setting isn't valid for your task workflow, the verification harness catches it (deterministic) and tells the strategy layer (L2 or L3) what went wrong, which in turn updates the prompt of the model that proposed the invalid setting. Full architecture: [self-healing.md](docs/concepts/self-healing.md).
- **Statistical early-stopping:** unfit individuals are eliminated after a handful of queries via paired Wilcoxon signed-rank tests, instead of burning the full budget. Methods: [candidate-elimination.md](docs/methods/candidate-elimination.md).
- **Cross-run learning:** every fitness measurement flows into a shared memory store. Parameter impact, query difficulty, and failure patterns are remembered. The optimizer carries what it learned into the next run.

## How It Works

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

Head-to-head comparison on BBEH (Big-Bench Extra Hard) against DSPy optimizers (GEPA, MIPROv2, BootstrapFewShot) and CAPO. Same model (`gpt-oss-120b`), same dataset splits, same scoring, no cross-paper number mixing. See [`docs/research/benchmarks.md`](docs/research/benchmarks.md) for results and [`docs/research/bbeh-comparison/`](docs/research/bbeh-comparison/) for reproducible Colab notebooks.

Compared head-to-head with DSPy (GEPA, MIPROv2, BootstrapFewShot), CAPO, and PromptWizard. See [related work](docs/research/related-work.md).



## Documentation

| 🧠 Concepts | ⚙ Operations | 🔬 Research |
|---|---|---|
| [Campaign lifecycle](docs/concepts/campaign-lifecycle.md) | [CLI reference](docs/operations/cli-reference.md) | [Benchmarks](docs/research/benchmarks.md) |
| [Three-layer loop](docs/concepts/three-layer-loop.md) | [Environment](docs/operations/environment.md) | [Metrics (HC, SE, R₉₀)](docs/research/metrics.md) |
| [Self-healing](docs/concepts/self-healing.md) | [🔌Backend integration](docs/operations/backend-integration.md) | [Related work](docs/research/related-work.md) |
| [Scoring and traces](docs/concepts/scoring-and-traces.md) | [Persistence and state](docs/operations/persistence-and-state.md) | |
| [Search memory](docs/concepts/search-memory.md) | [Rewind and fork](docs/operations/rewind-and-fork.md) | |
| [Prompts and individuals](docs/concepts/prompts-and-individuals.md) | [Observability](docs/operations/observability.md) | |
| [Nodes and pipelines](docs/concepts/nodes-and-pipelines.md) | | |

Developer internals (Python symbols, data contracts, wiring) live under [`docs/developer/`](docs/developer/README.md). Statistical foundations under [`docs/methods/`](docs/methods/README.md).

## Limitations

- **Parameter-based optimization only.** PromptPotter optimizes any pipeline that exposes tunable parameters (prompts, thresholds, model settings). It cannot optimize internal model weights, neural architectures, or modality-specific representations (e.g. image embeddings, DNA sequences).
- **Requires a labeled dataset.** You need input/output pairs. No labeled data, no optimization.
- **Langfuse dependency.** Observability is currently coupled to Langfuse (v2). It works but adds operational complexity and is not optional for full tracing.

## Watching a Run

While `python -m promptpotter optimize` is running, the cleanest setup is **`campaigns/{cycle_id}/dashboard.json` open in an auto-reloading editor + the CLI terminal visible**. `dashboard.json` is the live scalar state (phase, round, candidate, accuracy, in-flight query, per-round node I/O); the CLI prints HIT/MISS lines + per-candidate + per-round banners as they happen. Drill-down peers in the same directory: `output.log`, `phase_events.jsonl`, `trials/`, `candidates/`. Alternatives: `/potter-run` Claude Code skill, the notebook, or the planned webapp. Full guide in [`CLAUDE.md`](CLAUDE.md#superuser-monitoring-live-runs).

## Citation

```bibtex
@software{promptpotter,
  title  = {PromptPotter: Automated Prompt and Pipeline Optimization for LLMs},
  author = {Streuli, David},
  year   = {2026},
  url    = {https://github.com/uniqued4ve/prompt-potter-optimizer}
}
```
