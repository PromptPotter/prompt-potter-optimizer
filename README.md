# PromptPotter Optimizer

**Optimize prompts for any LLM application that logs in Langfuse-compatible format.**

## How It Works

PromptPotter connects to your backend (e.g. TermNorm), syncs experiment data, replays pipelines with different configurations, and runs optimization campaigns to systematically improve prompt accuracy.

```
┌─────────────────────┐         ┌─────────────────────┐
│  Your Backend       │         │  PromptPotter        │
│  (e.g. TermNorm)    │◄───────►│  Optimizer           │
│                     │  sync   │                      │
│  - Experiments      │  replay │  - Optimization      │
│  - Pipeline API     │  compare│  - Grid search       │
│  - Evaluation data  │         │  - Notebooks + API   │
└─────────────────────┘         └──────────────────────┘
```

### Optimization Campaign Workflow

1. **Sync** experiment data from your backend
2. **Replay** the pipeline to establish baseline accuracy
3. **Explore** the prompt landscape with grid search over prompt component axes
4. **Optimize** iteratively — generate candidates, evaluate, select winners
5. **Analyze** results and apply LLM-generated improvement suggestions

Prompts are structured into three optimization layers (see [3-Layer PromptState](#3-layer-promptstate)).

**Works with:**
- Any FastAPI backend with [Langfuse-compatible](https://langfuse.com/docs) logging
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
api/                             # FastAPI application
├── main.py                      # Entry point, router mounting
├── config/settings.py           # Pydantic BaseSettings
├── models/
│   ├── backend.py               # BackendConnection, Execution, ExecutionResultItem
│   ├── prompt_state.py          # PromptState (3-layer, immutable, versioned)
│   └── workflow.py              # WorkflowDefinition, StepDefinition
├── routers/
│   ├── backends.py              # /backends/* — connect, sync, execute, compare
│   ├── workflows.py             # /workflows/* — execute, evaluate
│   └── health.py                # /health, /ready
├── services/
│   ├── prompt_eval.py           # Prompt evaluation (baseline, filter, batch eval)
│   ├── prompt_optimizer.py      # Candidate generation, selection, suggestions
│   ├── grid_search.py           # Grid search over prompt component axes
│   ├── project_store.py         # Facade over stores/ for .promptpotter/projects/
│   ├── stores/                  # Focused store modules (BackendStore, ExecutionStore, etc.)
│   ├── backend_client.py        # HTTP client for backend APIs (TermNorm)
│   ├── comparison.py            # Statistical comparison (hit@k, McNemar, Wilcoxon)
│   ├── llm_client.py            # OpenAI/Anthropic/Groq abstraction
│   ├── query_utils.py           # Shared query-parsing utilities
│   └── langfuse_client.py       # Langfuse integration
├── core/
│   └── workflow_runner.py       # DAG execution engine
├── nodes/                       # Composable workflow nodes (LLM, PipelineConfig, Ranker)
└── evaluators/                  # ExactMatch, CriteriaEvaluator (LLM-judge)

notebooks/
├── termnorm_backend.ipynb       # Exploration: register → sync → replay → compare
├── optimization_campaign.ipynb  # Optimization: eval → grid search → optimize → save
└── _campaign_lib.py             # Notebook helper (thin wrapper over api/services/)

docs/
├── specs/                       # Formal specs (project-charter, PRD, ADD, WBS, roadmap)
├── connectors/                  # Backend connector contracts (termnorm.md)
├── user-guide.md                # Setup, workflows, configuration reference
└── *.md                         # Design docs (registry-design, literature-review)

tests/                           # pytest suite
scripts/                         # Utilities (sync_termnorm_to_langfuse.py)
workflows/examples/              # CWL-inspired YAML workflow definitions
docker/                          # Dockerfile, docker-compose
├── apps/                        # Streamlit UIs (secrets_manager)
└── launcher/                    # JupyterLab launcher config
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
