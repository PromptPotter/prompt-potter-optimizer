# PromptPotter Optimizer

[![CI](https://github.com/runfish5/prompt-potter-optimizer/actions/workflows/ci.yml/badge.svg)](https://github.com/runfish5/prompt-potter-optimizer/actions/workflows/ci.yml)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.6.1-orange.svg)](CHANGELOG.md)

**Automatic prompt optimization for LLMs.** Give it a labeled dataset, it finds a better prompt.

## TL;DR

Give it a labeled dataset and an LLM. PromptPotter systematically searches for a better prompt — measuring accuracy, critiquing failures, and iterating through a 3-layer optimization loop. Same setup as academic benchmarks (HotPotQA, GSM8K), same loop for complex pipelines. Exports paper-ready results with confidence intervals and significance tests. See [`docs/research/benchmarks.md`](docs/research/benchmarks.md) for the comparison protocol.

At its core, PromptPotter just collects a lot of datapoints. Every evaluation is stored, every parameter combination is tracked, and the optimizer uses this accumulated evidence to make better decisions each round.

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

L2 can also mutate the prompt structure itself — adding or removing fields to widen or narrow the search space:

```
L2 REFINE ──► add_field("domain_constraints")
              remove_field("persona")
                    │
                    ▼
┌─ PROMPT SCHEME ──────────────────────────┐
│  1. task_intent                          │
│  2. problem_description                  │
│  3. instruction                          │
│  4. thinking_style                       │
│  5. answer_format                        │
│  6. domain_constraints          ← NEW    │
│                                          │
│  +/- [???]                               │
└──────────────────────────────────────────┘
```

## The 4-Step Workflow

1. **Provide a labeled dataset** — input/output pairs (and any extra context)
2. **Provide your `pipeline.json`** — your backend serves this via `GET /pipeline`. It declares every node, its parameters, and their allowed values. The optimizer only searches parameters defined in this file — nothing else is touched.
3. **Sensitivity scan** — measure which axes matter at a given `sample_size`; statistical confidence in each axis guides how aggressively the optimizer should change it
4. **Optimize** — run the feedback cycle from the scan's best starting point

The primary UI is a Jupyter notebook, backed by a FastAPI service designed to work with any LLM pipeline backend.

## How It Works

PromptPotter is built on human-in-the-loop roundtrips. The human drives exploration decisions; the system handles evaluation and search. The concrete workflow:

1. **Scan advisor** recommends which prompt axes to explore
2. **Sensitivity scan** measures each axis independently
3. **Coverage advisor** shows what's been measured and what's missing
4. **PromptPotter optimizer** iterates from the best starting point
5. **Evaluate** — increase `sample_size` to validate winners with statistical confidence

Confidence intervals and two-proportion significance tests are built in. Non-parametric tests are planned.

**The Human Loop (Sensitivity Scan)** — You analyze the prompt landscape. A one-at-a-time perturbation scan measures which prompt axes actually matter (persona, thinking style, pipeline temperature, etc.) and how sensitive accuracy is to each. The coverage advisor shows what's already been measured and what still needs exploration. You pick the best starting point.

**The AI Loop (Potter)** — From that starting point, a **critique-guided** feedback cycle iterates: each evaluation produces a structured **critique** of failures (or successes), which feeds forward into the next round's candidate generation alongside sampled **thinking styles** as mutation guidance. This separates failure analysis from candidate generation (inspired by [PromptWizard](https://arxiv.org/abs/2405.18369)'s critique-and-refine pattern). Candidates are evaluated against the backend and winners selected by composite score. 3-layer escalation: Layer 1 generates prompt and parameter candidates every round, Layer 2 (context) adjusts when Layer 1 stalls, Layer 3 (strategy) rarely changes. The critique and thinking styles operate at the **optimizer agent** level (guiding the eval LLM that generates candidates) — they are not injected into the pipeline prompt being optimized.

**Prompt decomposition** is the core architectural move. Backends have one monolithic prompt — PromptPotter decomposes it into independent fields (persona, task_intent, thinking style, etc.) via LLM restructure, then perturbs each using a [variant library](promptpotter/config/prompt_variants.json) that includes variants from published research (e.g. PromptWizard's 40 thinking styles). Each variant carries provenance metadata (source, year) for traceability. This turns one opaque prompt into a combinatorial search space where each field can be independently measured, combined, and optimized.

Every evaluation point is a **SearchPoint** — content-hashable, so every evaluation is stored once and discoverable by any workflow.

```
SearchPoint (abstract — render())
    ├── JobSearchPoint      — target evaluation space (pipeline_params, frozen)
    └── PromptTemplate      — 8-field prompt scheme (render/compile)
            └── OptSearchPoint  — optimizer working state (+ lineage, L2/L3, memory)
```

**Prompt alias groups** link the original monolithic prompt to its decomposed form (and any future variants) so all historical evaluations are discoverable across both. Core to the data model and actively evolving.

Intermediate node outputs are cached in a [suffix-hash store](docs/architecture/suffix-cache.md) keyed at every pipeline cut point — O(1) lookup for the common case, symmetric reuse across both upstream and downstream config changes. The cache replaced an earlier prefix-chain scheme during M9 and is a deliberate pre-publication architectural improvement.

**The key insight: every evaluation is saved.** When an optimization thread stops improving, its data isn't wasted — the next sensitivity scan discovers all stored evaluations, knows the landscape better, and a fresh optimization starts from higher ground.

```
  HUMAN LOOP                           AI LOOP (Potter)
  ──────────                           ────────────────
  Sensitivity Scan                     Critique-Guided Feedback Cycle
  ┌──────────────────┐                 ┌───────────────────────────┐
  │ Measure axes     │  select best    │ Growth: generate          │
  │ Classify by      │───starting──────►  candidates using         │
  │  sensitivity     │  point          │  critique + thinking      │
  │ Show coverage    │                 │  styles                   │
  └──────┬───────────┘                 │ Eval: evaluate via        │
         │                             │  backend, select winner   │
         │  all eval data              │ Critique: analyze         │
         │  feeds back                 │  failures → next round    │
         │                             └────────┬──────────────────┘
         │                                      │
         └──────────────◄───────────────────────┘
              richer landscape → better starting point → repeat
```

```
┌─────────────────────┐         ┌─────────────────────┐
│  Your Backend       │         │  PromptPotter        │
│  (any pipeline)     │◄───────►│  Optimizer           │
│                     │  sync   │                      │
│  - Experiments      │  replay │  - Sensitivity scan  │
│  - Pipeline API     │  eval   │  - Feedback cycle    │
│  - Evaluation data  │         │  - Coverage advisor   │
└─────────────────────┘         └──────────────────────┘
```

**Requires a backend** that exposes a `/matches` evaluation endpoint (any pipeline complexity). Currently tested with [TermNorm-excel](https://github.com/runfish5/TermNorm-excel) — AI terminology normalization. See [Backend Requirements](docs/setup-guide.md#backend-requirements) for the API contract.

## Getting Started

### Local install

```bash
pip install -e ".[dev,jupyter,stats]"
cp .env.example .env   # add your LLM API keys (Groq, OpenAI, or Anthropic)
```

### Docker (one command)

```bash
cd docker && docker-compose up --build
# JupyterLab: http://localhost:8888  |  API: http://localhost:8001
```

### Three entry points

- **Notebook** (recommended): open `notebooks/optimization_campaign.ipynb`
- **CLI**: `python -m promptpotter init --backend-url http://127.0.0.1:8000`
- **API**: `uvicorn promptpotter.main:app --port 8001 --reload`

See the [Setup Guide](docs/setup-guide.md) for prerequisites, configuration, and first run.

## Jupyter Notebook

The notebook uses `promptpotter/display/campaign/` wrapping services with progress bars and IPython display:
- Pipeline config fetch, dataset loading (Excel + trace-based)
- Scan advisor → sensitivity scan → coverage advisor
- Feedback cycle with patience, campaign rounds, Langfuse sync

## Limitations

- **Parameter-based optimization only** — PromptPotter optimizes any pipeline that exposes tunable parameters (prompts, thresholds, model settings). It cannot optimize internal model weights, neural architectures, or modality-specific representations (e.g. image embeddings, DNA sequences).
- **Requires a labeled dataset** — you need input/output pairs. No labeled data, no optimization.
- **Langfuse dependency** — observability is currently coupled to Langfuse (v2). It works but adds operational complexity and is not optional for full tracing.

## Benchmarks

Head-to-head comparison on BBEH (Big-Bench Extra Hard) against DSPy optimizers (GEPA, MIPROv2, BootstrapFewShot) and CAPO. Same model (`gpt-oss-120b`), same dataset splits, same scoring — no cross-paper number mixing. See [`docs/research/benchmarks.md`](docs/research/benchmarks.md) for results and [`docs/research/bbeh-comparison/`](docs/research/bbeh-comparison/) for reproducible Colab notebooks.

## Documentation

**Architecture** (how it works):
- [Overview](docs/architecture/overview.md) — System design, two-loop diagram, data model, export layer
- [Optimization](docs/architecture/optimization.md) — Feedback cycle, 3-layer model, config reference
- [Prompt Scheme](docs/architecture/prompt-scheme.md) — 8-field decomposition, variant library, rendering
- [Information Flow](docs/architecture/information-flow.md) — Prompt injection map
- [Node Standard](docs/architecture/node-standard.md) — Node types, `llm_call()` primitive
- [Suffix-Hash Cache](docs/architecture/suffix-cache.md) — Per-query intermediate cache (M9, replaces prefix-chain scheme)

**Operations** (how to use it):
- [Setup Guide](docs/setup-guide.md) — Prerequisites, installation, configuration
- [CLI Workflow](docs/cli-workflow.md) — Full subcommand reference, worked example
- [Sensitivity Scan](docs/specs/archive/sensitivity-scan.md) — OAT scanning, coverage diagnostic
- [Observability](docs/observability.md) — Langfuse integration, MLflow, data exploration

**Research** (methodology & analysis):
- [Benchmarks](docs/research/benchmarks.md) — HotPotQA/GSM8K methodology, head-to-head protocol
- [SearchMemory Intelligence](docs/research/search-memory-intelligence.md) — Cross-campaign intelligence design
- [Candidate Comparison](docs/research/candidate-comparison.md) — Sequential elimination methodology

**Specs**: [docs/specs/](docs/specs/) — Roadmap, active milestone (M9), archived specs
