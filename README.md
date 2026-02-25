# PromptPotter Optimizer

**Systematic prompt optimization for LLM pipelines. Two nested loops — human-guided landscape analysis and AI-driven candidate search — that build on each other.**

## How It Works

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

## Quick Start

```bash
cd prompt-potter-optimizer
pip install -r requirements.txt
```

### Notebooks

Two notebooks cover the full workflow:

- **`notebooks/termnorm_backend.ipynb`** — Exploration: register backend, sync experiments, replay queries, compare variants, diagnostics
- **`notebooks/optimization_campaign.ipynb`** — Optimization: baseline evaluation, grid search, iterative HITL optimization, suggestions, save winners

### REST API

```bash
uvicorn api.main:app --port 8001 --reload
```

```bash
# Register backend
curl -X POST http://localhost:8001/api/v1/backends \
  -H "Content-Type: application/json" \
  -d '{"name": "TermNorm", "backend_type": "termnorm", "base_url": "http://localhost:8000"}'

# Sync experiments
curl -X POST http://localhost:8001/api/v1/backends/termnorm/sync

# View synced data
curl http://localhost:8001/api/v1/backends/termnorm/experiments
```

### Docker

```bash
cd docker && docker-compose up --build
```

- **JupyterLab**: http://localhost:8888
- **FastAPI docs**: http://localhost:8001/docs

## 3-Layer PromptState

Prompts are organized into three optimization layers, each with different change frequency:

| Layer | Name | Fields | When to change |
|-------|------|--------|----------------|
| **1** | Generate | `persona`, `task_intent`, `problem_description`, `instruction`, `thinking_style`, `answer_format`, `few_shot_examples` | Every optimization pass |
| **2** | Refine Context | `context`, `parameters` | When Layer 1 improvements stall |
| **3** | Modify Plan | `plan` | Rarely — optimization strategy defaults |

Each `PromptState` is immutable. `derive()` creates children, forming a lineage chain via `parent_id`. `render()` assembles Layer 1 fields into the final prompt string.

## Project Structure

```
api/
├── services/
│   ├── prompt_eval.py           # evaluate_prompt_cached() — single gateway for all eval persistence
│   ├── prompt_optimizer.py      # LLM candidate generation, winner selection, suggestions
│   ├── feedback_cycle.py        # Iterative optimization orchestrator (the AI loop)
│   ├── search/
│   │   ├── smart_search.py      # Sensitivity scan, adaptive search, axis classification
│   │   ├── grid_core.py         # Grid search evaluation engine
│   │   └── coverage.py          # Historical index + coverage advisor (data reuse)
│   ├── project_store.py         # Facade over stores/ for .promptpotter/projects/
│   ├── stores/                  # BackendStore, DatasetRunStore, GridPlanStore, SmartSearchStore, CampaignStore
│   ├── backend_client.py        # HTTP client for backend APIs
│   ├── llm_client.py            # Groq/OpenAI abstraction
│   └── langfuse_client.py       # Langfuse v2 observability
├── nodes/
│   └── optimizer_nodes.py       # InitNode, GrowFilterNode, AnalysisEvalNode
├── models/
│   └── prompt_state.py          # PromptState (3-layer, immutable, versioned)
└── routers/                     # FastAPI routers (/backends, /workflows, /health)

notebooks/
├── optimization_campaign.ipynb  # Full HITL workflow: baseline → scan → optimize → save
└── _campaign_lib.py             # Thin wrapper: progress bars, display, callbacks

.promptpotter/projects/{backend_id}/
├── dataset_runs/                # ALL eval results (shared across scan, grid, feedback cycle)
├── smart_search_plans/          # Sensitivity scan plans + axis profiles
├── grid_plans/                  # Grid search plans
└── campaigns/                   # Campaign metadata + trials
```

## Configuration

Edit `.env` (see `.env.example`):

| Variable | Description |
|----------|-------------|
| `GROQ_API_KEY` | Groq API key (primary LLM provider) |
| `LLM_PROVIDER` | LLM provider: `groq`, `openai`, or `anthropic` (default: `groq`) |
| `LLM_MODEL` | Model identifier (default: `meta-llama/llama-4-maverick-17b-128e-instruct`) |
| `OPENAI_API_KEY` | OpenAI API key (alternative provider) |
| `ANTHROPIC_API_KEY` | Anthropic API key (alternative provider) |
| `LANGFUSE_PUBLIC_KEY` | Langfuse public key for observability |
| `LANGFUSE_SECRET_KEY` | Langfuse secret key for observability |
## Documentation

- `docs/user-guide.md` — Setup, optimization workflow, configuration
- `docs/registry-design.md` — Optimization tracking (MLflow/DSPy style)
- `docs/specs/` — Formal specs (charter, PRD, ADD, WBS, roadmap)

## Contributing

See `CLAUDE.md` for architecture and conventions.

## License

MIT License - see LICENSE file.
