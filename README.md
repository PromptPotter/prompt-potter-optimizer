# PromptPotter Optimizer

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

**The AI Loop (Potter)** — From that starting point, an automated feedback cycle generates candidate prompts via LLM, evaluates each against the backend, selects winners, and iterates. This is the 3-layer PromptState optimization: Layer 1 (prompt fields) changes every round, Layer 2 (context) adjusts when Layer 1 stalls, Layer 3 (strategy) rarely changes.

**Prompt decomposition** is the core architectural move. Backends have one monolithic prompt — PromptPotter decomposes it into independent fields (persona, task_intent, thinking style, etc.) via LLM restructure, then perturbs each using a [default variant library](api/config/prompt_variants.json). This turns one opaque prompt into a combinatorial search space where each field can be independently measured, combined, and optimized.

Every evaluation point is a **SearchPoint** — an immutable bundle of prompt + model + temperature + pipeline params. All mutations via `.derive()`. Content-hashable, so every evaluation is stored once and discoverable by any workflow.

**Prompt alias groups** link the original monolithic prompt to its decomposed form (and any future variants) so all historical evaluations are discoverable across both. Core to the data model and actively evolving.

**The key insight: every evaluation is saved.** When an optimization thread stops improving, its data isn't wasted — the next sensitivity scan discovers all stored evaluations, knows the landscape better, and a fresh optimization starts from higher ground.

```
  HUMAN LOOP                           AI LOOP (Potter)
  ──────────                           ────────────────
  Sensitivity Scan                     Feedback Cycle
  ┌──────────────────┐                 ┌──────────────────┐
  │ Measure axes     │  select best    │ Generate         │
  │ Classify by      │───starting──────►  candidates      │
  │  sensitivity     │  point          │ Evaluate via     │
  │ Show coverage    │                 │  backend         │
  └──────┬───────────┘                 │ Select winner    │
         │                             │ Iterate until    │
         │  all eval data              │  patience runs   │
         │  feeds back                 │  out             │
         │                             └────────┬─────────┘
         │                                      │
         └──────────────◄───────────────────────┘
              richer landscape
              → better starting point
              → repeat
```

```
┌─────────────────────┐         ┌─────────────────────┐
│  Your Backend       │         │  PromptPotter        │
│  (e.g. TermNorm)    │◄───────►│  Optimizer           │
│                     │  sync   │                      │
│  - Experiments      │  replay │  - Sensitivity scan  │
│  - Pipeline API     │  eval   │  - Feedback cycle    │
│  - Evaluation data  │         │  - Grid search       │
└─────────────────────┘         └──────────────────────┘
```

**Works with:**
- Any FastAPI backend with a `/matches` evaluation endpoint
- [TermNorm-excel](https://github.com/runfish5/TermNorm-excel) — AI terminology normalization

## Getting Started

```bash
pip install -r requirements.txt  # then configure .env (see .env.example)
```

- Open `notebooks/optimization_campaign.ipynb` for the full HITL optimization workflow
- Or start the API: `uvicorn api.main:app --port 8001 --reload`

See the [Setup Guide](docs/setup-guide.md) for prerequisites, configuration, and first run.

## Jupyter Notebook

The notebook uses `notebooks/_campaign_lib/` wrapping services with progress bars and IPython display:
- Pipeline config fetch, dataset loading (Excel + trace-based)
- Scan advisor → sensitivity scan → coverage advisor → grid search
- Feedback cycle with patience, campaign rounds, Langfuse sync

## Documentation

- [Setup Guide](docs/setup-guide.md) — Prerequisites, installation, configuration
- [Architecture](docs/architecture.md) — System overview, two-loop design, data model
- [Design Principles](docs/design-principles.md) — Core patterns and conventions
- [Sensitivity Scan & Grid Search](docs/sensitivity-scan.md) — Exploration tools: OAT scanning, grid search, coverage diagnostic
- [Optimization](docs/optimization.md) — Feedback cycle, 3-layer optimization model, config reference
- [Observability](docs/observability.md) — Langfuse integration, MLflow, data exploration
- [Node Development](docs/node-development.md) — Building workflow nodes
- [Connector: TermNorm](docs/connectors/termnorm.md) — TermNorm-specific pipeline details
- [Specs](docs/specs/) — Project charter, PRD, ADD, WBS, roadmap
