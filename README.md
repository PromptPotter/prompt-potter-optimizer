# PromptPotter Optimizer

[![CI](https://github.com/runfish5/prompt-potter-optimizer/actions/workflows/ci.yml/badge.svg)](https://github.com/runfish5/prompt-potter-optimizer/actions/workflows/ci.yml)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.6.1-orange.svg)](CHANGELOG.md)

**Automatic prompt optimization for any LLM pipeline.**

## The 4-Step Workflow

1. **Provide a dataset** — input/output pairs (and any extra context)
2. **Describe your pipeline** — a schema of your LLM application's steps
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

Future: non-parametric significance tests and confidence intervals on accuracy deltas.

**The Human Loop (Sensitivity Scan)** — You analyze the prompt landscape. A one-at-a-time perturbation scan measures which prompt axes actually matter (persona, thinking style, pipeline temperature, etc.) and how sensitive accuracy is to each. The coverage advisor shows what's already been measured and what still needs exploration. You pick the best starting point.

**The AI Loop (Potter)** — From that starting point, a **critique-guided** feedback cycle iterates: each evaluation produces a structured **critique** of failures (or successes), which feeds forward into the next round's candidate generation alongside sampled **thinking styles** as mutation guidance. This separates failure analysis from candidate generation (inspired by [PromptWizard](https://arxiv.org/abs/2405.18369)'s critique-and-refine pattern). Candidates are evaluated against the backend and winners selected by composite score. 3-layer escalation: Layer 1 (prompt fields) changes every round, Layer 2 (context) adjusts when Layer 1 stalls, Layer 3 (strategy) rarely changes. The critique and thinking styles operate at the **optimizer agent** level (guiding the eval LLM that generates candidates) — they are not injected into the pipeline prompt being optimized.

**Prompt decomposition** is the core architectural move. Backends have one monolithic prompt — PromptPotter decomposes it into independent fields (persona, task_intent, thinking style, etc.) via LLM restructure, then perturbs each using a [variant library](promptpotter/config/prompt_variants.json) that includes variants from published research (e.g. PromptWizard's 40 thinking styles). Each variant carries provenance metadata (source, year) for traceability. This turns one opaque prompt into a combinatorial search space where each field can be independently measured, combined, and optimized.

Every evaluation point is a **SearchPoint** — content-hashable, so every evaluation is stored once and discoverable by any workflow.

```
SearchPoint (abstract — render())
    ├── JobSearchPoint      — target evaluation space (pipeline_params, frozen)
    └── PromptTemplate      — 8-field prompt scheme (render/compile)
            └── OptSearchPoint  — optimizer working state (+ lineage, L2/L3, memory)
```

**Prompt alias groups** link the original monolithic prompt to its decomposed form (and any future variants) so all historical evaluations are discoverable across both. Core to the data model and actively evolving.

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
│  (e.g. TermNorm)    │◄───────►│  Optimizer           │
│                     │  sync   │                      │
│  - Experiments      │  replay │  - Sensitivity scan  │
│  - Pipeline API     │  eval   │  - Feedback cycle    │
│  - Evaluation data  │         │  - Coverage advisor   │
└─────────────────────┘         └──────────────────────┘
```

**Works with:**
- Any FastAPI backend with a `/matches` evaluation endpoint
- [TermNorm-excel](https://github.com/runfish5/TermNorm-excel) — AI terminology normalization

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
- **CLI**: `python -m promptpotter.cli.campaign_runner init --backend-url http://127.0.0.1:8000`
- **API**: `uvicorn promptpotter.main:app --port 8001 --reload`

See the [Setup Guide](docs/setup-guide.md) for prerequisites, configuration, and first run.

## Jupyter Notebook

The notebook uses `notebooks/campaign_lib/` wrapping services with progress bars and IPython display:
- Pipeline config fetch, dataset loading (Excel + trace-based)
- Scan advisor → sensitivity scan → coverage advisor
- Feedback cycle with patience, campaign rounds, Langfuse sync

## Documentation

- [Setup Guide](docs/setup-guide.md) — Prerequisites, installation, configuration
- [Architecture](docs/architecture.md) — System overview, two-loop design, data model
- [Sensitivity Scan](docs/sensitivity-scan.md) — OAT scanning, coverage diagnostic
- [Optimization](docs/optimization.md) — Feedback cycle, 3-layer optimization model, config reference
- [Observability](docs/observability.md) — Langfuse integration, MLflow, data exploration
- TermNorm connector — see the TermNorm repo's own `CLAUDE.md`
- [Specs](docs/specs/) — Project charter, PRD, ADD, WBS, roadmap
