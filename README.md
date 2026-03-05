# PromptPotter Optimizer

**Automatic prompt optimization for any LLM pipeline.**

## The 4-Step Workflow

1. **Provide a dataset** — input/output pairs (and any extra context)
2. **Describe your pipeline** — a schema of your LLM application's steps
3. **Set your budget** — how many rounds, how many evaluations
4. **Get optimized parameters** — the best prompt configuration for your pipeline

PromptPotter treats your LLM pipeline as a black box, systematically explores
the prompt space, and returns the configuration that maximizes accuracy on
your dataset.

## How It Works Under the Hood

PromptPotter has two loops that work together:

**The Human Loop (Sensitivity Scan)** — You analyze the prompt landscape. A one-at-a-time perturbation scan measures which prompt axes actually matter (persona, thinking style, pipeline temperature, etc.) and how sensitive accuracy is to each. The coverage advisor shows what's already been measured and what still needs exploration. You pick the best starting point.

**The AI Loop (Potter)** — From that starting point, an automated feedback cycle generates candidate prompts via LLM, evaluates each against the backend, selects winners, and iterates. This is the 3-layer PromptState optimization: Layer 1 (prompt fields) changes every round, Layer 2 (context) adjusts when Layer 1 stalls, Layer 3 (strategy) rarely changes.

**The key insight: every evaluation is saved.** When an optimization thread stops improving, its data isn't wasted — it's harvested. The next sensitivity scan automatically discovers all stored evaluations and knows the landscape better. A new starting point is computed, and a fresh optimization thread begins from higher ground.

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

See the [User Guide](docs/user-guide.md) for setup, configuration, and the complete workflow.

## Documentation

- [User Guide](docs/user-guide.md) — Setup, optimization workflow, configuration reference
- [Observability Guide](docs/obs-guide.md) — Langfuse integration, data exploration
- [Connector: TermNorm](docs/connectors/termnorm.md) — TermNorm-specific pipeline details
- [Specs](docs/specs/) — Project charter, PRD, ADD, WBS, roadmap
