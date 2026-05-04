<p align="center">
  <img src="docs/assets/wizard.jpg" alt="PromptPotter wizard" width="100">
  <img src="docs/assets/promptpotter-wordmark.png" alt="PromptPotter" width="420">

</p>

# PromptPotter: LLM-Driven Evolution of Prompts and Pipelines

**PromptPotter brews better prompts.** Most prompt engineering is manual. PromptPotter automates the generate → score → critique cycle — an **LLM-driven program evolution** engine that jointly searches prompts and pipeline parameters with population-aware Bayesian early-stopping (Posterior-of-Being-Best), cross-run memory, and self-healing rails. Built for RAG pipelines, LLM agents, and multi-step LLM workflows — drop in via CLI, Python SDK, or the `/potter-run` Claude Code skill.

## Why PromptPotter?

Manual prompt tuning is slow, inconsistent, and the lessons don't carry over to the next project. PromptPotter automates the loop: it tries variations, measures what works, and remembers across runs. Whether you're an office worker iterating on the same daily report or an AI agent learning a new tool, you get a better prompt without the trial and error.

Under the hood, PromptPotter just collects a lot of datapoints. Every evaluation is stored, every parameter combination is tracked, and the optimizer uses this accumulated evidence to make better decisions each round.


## The Workflow

**Core path (what everyone runs):**

1. **Provide a labeled dataset.** Input/output pairs (plus any extra context).
2. **Drop in your `pipeline.json`.** This file lists what your pipeline does and which settings PromptPotter is allowed to change — models, temperature, prompts, thresholds, anything you put on the list. It only touches what's on the list. Nothing else. (The `/potter-run` skill can help you write it from a chat — you don't have to hand-author the JSON.)
3. **Optimize:** run the critique-guided feedback cycle — PromptPotter's flavour of **LLM-driven program evolution**. The optimization loop is self-contained. It measures the baseline, generates candidates, scores them, runs L1 critique on failures, and iterates.

> [!IMPORTANT]
> **New here?** Start with [`docs/manual/`](docs/manual/README.md) — six chapters covering install → first run → reading output → troubleshooting.
>
> **Five ways to run it:** 1) `/potter-run` Claude Code skill · 2) CLI · 3) Python / Jupyter notebook · 4) REST API · 5) WebApp *(planned)*

## 🔄 The 3-layer loop

A **critique-guided** feedback cycle: each round generates candidates, scores them, and produces a structured **L1 critique** that steers the next round; **L2** escalates on stall, **L3** escalates when L2 stalls. Full mechanics in [three-layer-loop.md](docs/concepts/three-layer-loop.md).

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
- **Statistical early-stopping:** unfit individuals are eliminated after a handful of queries via Bayesian Posterior-of-Being-Best — population-aware joint posterior, stop when `P(c is best) < ε` — instead of burning the full budget. Methods: [candidate-elimination.md](docs/methods/candidate-elimination.md).
- **Cross-run learning:** every fitness measurement flows into a shared memory store. Parameter impact, query difficulty, and failure patterns are remembered. The optimizer carries what it learned into the next run.

## How It Works

PromptPotter's inner **generate → score → critique** loop mirrors the classic **plan / implement / validate (PIV)** developer workflow, driven by an LLM at scale.

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

## Relation to Karpathy's AutoResearch

If your use case is the simple one — a single agent editing one Python file against a fixed 5-min training run, no population, no statistics — go use [`karpathy/autoresearch`](https://github.com/karpathy/autoresearch). It is purpose-built and minimal.

Otherwise: PromptPotter is the same idea generalized. The architecture maps cleanly:

- AutoResearch's `train.py` (the artifact mutated each loop) ≈ PromptPotter's `OptSearchPoint` (the structured artifact L1 mutates each round — `PromptTemplate` fields + `pipeline_params`).
- AutoResearch's `program.md` (agent meta-instructions) ≈ PromptPotter's L1 generate node prompt at [`promptpotter/application/optimization/nodes/l1/generate.py`](promptpotter/application/optimization/nodes/l1/generate.py) — the instructions telling the proposing LLM *how* to mutate. Karpathy's [issue #314](https://github.com/karpathy/autoresearch/issues/314) (evolve `program.md` itself) is the L4 / self-optimization direction PromptPotter has on its own roadmap ([`docs/specs/m12-plus-backlog.md`](docs/specs/m12-plus-backlog.md) § Self-optimization).

Structurally, **AutoResearch is the degenerate case of PromptPotter**:

- `n_variants = 1`
- L2 / L3 / critique disabled
- Elimination disabled (single noisy trial accepted as-is)
- One sample, one scoring run = the 5-min training loss
- Single PromptTemplate field, e.g. `program_md`

It is *not literally* a configuration of PromptPotter today — running AutoResearch's workload on PromptPotter would require a `CodeExecutionConnector` (M12 multi-connector work). With that connector, PromptPotter strictly subsumes AutoResearch and adds population search, Bayesian Posterior-of-Being-Best elimination across seeds, L2/L3 escalation, self-healing rails, and the hard-sample sorter on top.

| | AutoResearch | PromptPotter |
|---|---|---|
| Evolved artifact | Python source code (`train.py`) | Structured prompt fields + `pipeline_params` |
| Fitness signal | 5-min nanochat training loss | Dataset accuracy (per-sample, scorer formula) |
| Search | 1 agent, try-keep-revert | Population (`n_variants`), PoBB-eliminated rounds |
| Loop layers | Flat — one agent, one loop | L1 generate/critique + L2 refine + L3 replan |
| Recovery | None — agent reverts on regression | Self-healing rails (`ValidationFailure` / `RuntimeFailure`) per individual |
| Sample selection | Fixed nanochat run | Rasch + KG scoring-set evolution; hard-sample sorter |
| Statistical guarantees | None — single noisy trial | Bayesian Posterior-of-Being-Best (population-aware best-arm-ID) |
| Domain | ML training research | Prompt/pipeline optimization for production LLM apps |

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
- **Requires a labeled dataset.** Input/output pairs are mandatory.

## Watching a run

While `python -m promptpotter optimize` is running, the cleanest setup is **`campaigns/{cycle_id}/dashboard.json` open in an auto-reloading editor + the CLI terminal visible**. `dashboard.json` is the live scalar state (phase, round, candidate, accuracy, in-flight query, per-round node I/O); the CLI prints HIT/MISS lines + per-candidate + per-round banners as they happen. Drill-down peers in the same directory: `output.log`, `trials/`, `log.md`. Internal resume + audit state lives under `.cache/` (hidden by convention). Alternatives: `/potter-run` Claude Code skill, the notebook, or the planned webapp. Full guide in [`CLAUDE.md`](CLAUDE.md#superuser-monitoring-live-runs).

## Citation

```bibtex
@software{promptpotter,
  title  = {PromptPotter: LLM-Driven Evolution of Prompts and Pipelines},
  author = {Streuli, David},
  year   = {2026},
  url    = {https://github.com/runfish5/prompt-potter-optimizer}
}
```
