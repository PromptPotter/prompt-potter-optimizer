# PromptPotter Optimizer

**Optimize prompts for any LLM application that logs in Langfuse-compatible format.**

## How It Works

PromptPotter connects to your backend (e.g. TermNorm), syncs experiment data, replays pipelines with different configurations, and computes statistical comparisons to find what works.

```
┌─────────────────────┐         ┌─────────────────────┐
│  Your Backend       │         │  PromptPotter        │
│  (e.g. TermNorm)    │◄───────►│  Optimizer           │
│                     │  sync   │                      │
│  - Experiments      │  replay │  - Project store     │
│  - Pipeline API     │  compare│  - Statistical tests │
│  - Evaluation data  │         │  - Notebooks + API   │
└─────────────────────┘         └──────────────────────┘
```

**Works with:**
- Any FastAPI backend with [Langfuse-compatible](https://langfuse.com/docs) logging
- [TermNorm-excel](https://github.com/runfish5/TermNorm-excel) — AI terminology normalization

## Quick Start

```bash
cd prompt-potter-optimizer
pip install -r requirements.txt
```

### Notebook (recommended)

Open `notebooks/termnorm_backend.ipynb` in Jupyter — register a backend, sync experiments, replay queries, compare variants. No server needed.

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

## Project Structure

```
api/                  # FastAPI application
├── routers/          #   backends, workflows, health
├── services/         #   project_store, backend_client, comparison
├── models/           #   backend, prompt_state, workflow
├── nodes/            #   LLM, WebSearch, Ranker
└── evaluators/       #   ExactMatch, Criteria (LLM-judge)
notebooks/            # Interactive workflows (termnorm_backend.ipynb)
scripts/              # Utilities (sync_termnorm_to_langfuse.py)
workflows/            # CWL-inspired YAML definitions
docker/               # Dockerfile, docker-compose
├── apps/             #   Streamlit UIs (secrets_manager)
└── launcher/         #   JupyterLab launcher config
docs/                 # Design docs + formal specs
tests/                # pytest suite
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
| `MAX_ITERATIONS` | Max optimization iterations (default: 5) |

## Documentation

- `docs/architecture.md` — Design patterns
- `docs/registry-design.md` — Optimization tracking (MLflow/DSPy style)
- `docs/specs/` — Formal specs (charter, PRD, ADD, WBS, roadmap)

## License

MIT License - see LICENSE file.
