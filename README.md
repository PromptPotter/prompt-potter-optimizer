# PromptPotter Optimizer

[![CI](https://github.com/runfish5/prompt-potter-optimizer/actions/workflows/ci.yml/badge.svg)](https://github.com/runfish5/prompt-potter-optimizer/actions/workflows/ci.yml)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.6.1-orange.svg)](CHANGELOG.md)

**Automatic prompt optimization for LLMs.** Give it a labeled dataset, it finds a better prompt.

## TL;DR

Give it a labeled dataset and an LLM. PromptPotter systematically searches for a better prompt — measuring accuracy, critiquing failures, and iterating through a 3-layer optimization loop. Same setup as academic benchmarks (HotPotQA, GSM8K), same loop for complex pipelines. Exports paper-ready results with confidence intervals and significance tests. See [`docs/research/benchmarks.md`](docs/research/benchmarks.md) for the comparison protocol.

At its core, PromptPotter just collects a lot of datapoints. Every evaluation is stored, every parameter combination is tracked, and the optimizer uses this accumulated evidence to make better decisions each round.

## ⭐ Features

- **🔁 Self-healing optimization** — when the optimizer hallucinates a value your backend doesn't accept (e.g. `model: gpt-4o`), PromptPotter catches it before any API call, scores the candidate 0, and tells the strategy layer what went wrong so the next round stops repeating the mistake.
- **Prompt + pipeline optimization** — searches the prompt (8-field decomposition) AND your pipeline parameters jointly. Most tools optimize one or the other.
- **Statistical early-stopping** — sequential elimination via paired Welch's t-test stops inferior candidates after ~6 queries instead of running the full eval set. Real cost is well below `n_variants × eval_size`.
- **Cross-run learning** — every evaluation flows into a shared `SearchMemory` store: parameter impact, axis exhaustion, query tractability, failure-group correlations. The optimizer carries what it learned across runs.
- **Auto-injected scoring** — define your scoring formula once in `campaign.json`. It compiles into every eval path automatically. No glue code.
- **IDE-native operation** — drive a full optimization campaign from the terminal via the `/potter-run` Claude Code skill. No notebook required.

For a head-to-head comparison with other prompt-optimization frameworks, see [`docs/research/README.md`](docs/research/README.md). For the self-healing architecture in detail, see [`docs/architecture/optimization.md`](docs/architecture/optimization.md).

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

## The Workflow

**Core path (what everyone runs):**

1. **Provide a labeled dataset** — input/output pairs (and any extra context)
2. **Provide your `pipeline.json`** — your backend serves this via `GET /pipeline`. It declares every node, its parameters, and their allowed values. The optimizer only searches parameters defined in this file — nothing else is touched.
3. **Optimize** — run the critique-guided feedback cycle. The optimization loop is self-contained: it measures the baseline, generates candidates, scores them, critiques failures, and iterates.

**Optional pre-step — Sensitivity scan.** Before optimizing, you can run a one-at-a-time perturbation scan to measure which prompt axes actually matter. The scan is a **separate, optional feature** — a human-driven exploration tool that lives in its own `recon/` package. Skip it and `optimize` still runs end-to-end. Run it and its results (`ReconBrief`) are passed into the optimizer as a hint about where to look first. That's the single, sanctioned bridge between the two features.

The primary UI is a Jupyter notebook, backed by a FastAPI service designed to work with any LLM pipeline backend.

## How It Works

**The Optimization Loop (always runs).** A **critique-guided** feedback cycle: each round generates candidates, scores them against your dataset, and produces a structured **critique** of failures (or successes) that feeds forward into the next round's candidate generation — alongside sampled **thinking styles** as mutation guidance. This separates failure analysis from candidate generation (inspired by [PromptWizard](https://arxiv.org/abs/2405.18369)'s critique-and-refine pattern). Candidates are evaluated against the backend and winners selected by composite score. 3-layer escalation: Layer 1 generates prompt and parameter candidates every round, Layer 2 (context) adjusts when Layer 1 stalls, Layer 3 (strategy) rarely changes. Five LLM call sites: `restructure` (one-time setup — parses your task into 8 canonical prompt fields), `l1_generate`, `critique`, `l2_context`, `l3_plan`. The critique and thinking styles operate at the **optimizer agent** level (guiding the LLM that generates candidates) — they are not injected into the pipeline prompt being optimized.

**The Sensitivity Scan (optional, human-driven).** Completely independent. One LLM call site (`recon_advisor`) plus a one-at-a-time perturbation runner. You analyze the prompt landscape: the scan measures which axes actually matter (persona, thinking style, pipeline temperature, etc.) and how sensitive accuracy is to each, and the coverage advisor shows what's already been measured and what still needs exploration. If you run it, you get a `ReconBrief` to hand to the optimizer as a starting-point hint. If you don't, the optimizer starts from the baseline and discovers axes as it goes.

**What happens if you skip the scan?** `optimize` runs end-to-end. The baseline is measured automatically. The optimizer has no prior knowledge of which axes are sensitive — it discovers this through the critique loop. You trade a faster start (no scan) for a slightly slower convergence. For most users, skipping is the right default.

Confidence intervals and two-proportion significance tests are built into both the optimization loop's candidate comparison and the scan's axis sensitivity reporting. Non-parametric tests are planned.

**Prompt decomposition** is the core architectural move. Backends have one monolithic prompt — PromptPotter decomposes it into independent fields (persona, task_intent, thinking style, etc.) via LLM restructure, then perturbs each using a [variant library](promptpotter/config/prompt_variants.json) that includes variants from published research (e.g. PromptWizard's 40 thinking styles). Each variant carries provenance metadata (source, year) for traceability. This turns one opaque prompt into a combinatorial search space where each field can be independently measured, combined, and optimized.

Every evaluation point is a **SearchPoint** — content-hashable, so every evaluation is stored once and discoverable by any workflow.

```
SearchPoint (abstract — render())
    ├── JobSearchPoint      — target evaluation space (pipeline_params, frozen)
    └── PromptTemplate      — 8-field prompt scheme (render/compile)
            └── OptSearchPoint  — optimizer working state (+ lineage, L2/L3, memory)
```

**Prompt alias groups** link the original monolithic prompt to its decomposed form (and any future variants) so all historical evaluations are discoverable across both. Core to the data model and actively evolving.

Intermediate node outputs are cached per query — see [suffix-hash cache](docs/architecture/suffix-cache.md). This enables mid-task cache pooling if the backend streams intermediate inputs back (outputs must already be converted to inputs).

```
  OPTIONAL                             CORE — always runs
  ────────                             ─────────────────
  Sensitivity Scan                     Critique-Guided Optimization Loop
  ┌──────────────────┐                 ┌───────────────────────────┐
  │ recon_advisor     │  ReconBrief      │ restructure → l1_generate │
  │ Measure axes     │───(optional)────►  → critique                │
  │ Classify by      │  starting hint  │     ↓ stall?               │
  │  sensitivity     │                 │  → l2_context              │
  │ Show coverage    │                 │     ↓ stall?               │
  └──────────────────┘                 │  → l3_plan                 │
                                       └───────────────────────────┘
        (skip entirely and `optimize` still runs end-to-end)
```

Every evaluation — from the optional scan AND the optimization loop — flows into the same shared `intelligence/` store (`SearchMemory`). That shared store feeds both features on subsequent runs, but the two features never import each other's code.

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
- [SearchMemory Intelligence](docs/architecture/search-memory-intelligence.md) — Cross-campaign intelligence design

**Specs**: [docs/specs/](docs/specs/) — Roadmap, active milestone (M9), archived specs
