# Setup Guide — PromptPotter Optimizer

You have a prompt and a dataset of input/output pairs. PromptPotter finds a better prompt — automatically. It tries variations, measures accuracy, and iterates using a critique-guided 3-layer optimization loop (generate candidates, refine context, replan strategy).

The simplest backend is a single LLM call with a question-answer dataset (like HotPotQA or GSM8K). The same tool scales to multi-step pipelines with retrieval, web search, and ranking nodes. PromptPotter evaluates your pipeline via the backend's `/matches` HTTP endpoint. Separately, the optimizer uses its own LLM (configured via `LLM_PROVIDER`/`LLM_MODEL`) to generate candidates and critique results.

---

## Prerequisites

- Python 3.13+
- A running backend with a `/matches` evaluation endpoint (see [Backend Requirements](#backend-requirements) below)
- An LLM API key for the optimizer agent (Groq recommended for speed/cost)

## Installation

```bash
git clone https://github.com/runfish5/prompt-potter-optimizer.git
cd prompt-potter-optimizer
pip install -e .
```

### Optional dependencies

Install extras based on your use case:

```bash
pip install -e ".[stats]"        # Statistical analysis: Wilson CI, significance tests (scipy)
pip install -e ".[jupyter]"      # JupyterLab notebook interface
pip install -e ".[dev]"          # Development: pytest, ruff, mypy, pre-commit
pip install -e ".[dev,jupyter,stats]"  # All of the above
```

### Environment variables

Create a `.env` file (see `.env.example`):

```
GROQ_API_KEY=your_groq_api_key
LLM_PROVIDER=groq
LLM_MODEL=your-model-id
```

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_API_KEY` | Yes (if using Groq) | Groq API key for optimizer LLM calls |
| `OPENAI_API_KEY` | Yes (if using OpenAI) | OpenAI-compatible API key |
| `LLM_PROVIDER` | Yes | `groq` or `openai` |
| `LLM_MODEL` | Yes | Model identifier (e.g. `openai/gpt-oss-120b`) |
| `LANGFUSE_PUBLIC_KEY` | No | Langfuse cloud tracing (optional observability) |
| `LANGFUSE_SECRET_KEY` | No | Langfuse cloud tracing |
| `LANGFUSE_HOST` | No | Langfuse host URL |

---

## Quick Start

### 1. Notebook (recommended for exploration)

The Jupyter notebook provides the full human-in-the-loop workflow: pipeline inspection, sensitivity scan, optimization, and results visualization.

```bash
pip install -e ".[jupyter,stats]"
jupyter lab notebooks/optimization_campaign.ipynb
```

### 2. CLI (recommended for automation)

The CLI campaign runner supports both interactive (round-by-round) and autonomous optimization:

```bash
# Initialize against your backend
python -m promptpotter.cli.campaign_runner init \
    --backend-url http://127.0.0.1:8000 \
    --config configs/datasets/lca-termnorm/campaign.json \
    --run-baseline

# Run optimization (autonomous — L1/L2/L3 until convergence)
python -m promptpotter.cli.campaign_runner optimize --auto

# View results
python -m promptpotter.cli.campaign_runner results
```

See [`cli-workflow.md`](cli-workflow.md) for the full subcommand reference and worked examples.

### 3. API server

```bash
uvicorn promptpotter.main:app --port 8001 --reload
```

Swagger docs at `http://localhost:8001/docs`.

### 4. Docker (one command)

```bash
cd docker && docker-compose up --build
# JupyterLab: http://localhost:8888  |  API: http://localhost:8001
```

---

## Export Results

After optimization campaigns complete, generate paper-ready supplemental materials:

```bash
# Markdown: comparison tables, convergence, significance tests, reproducibility manifest
python -m promptpotter.cli.export_results supplemental \
    --backend-id local --output supplemental.md

# JSON: structured data for paper repositories or further analysis
python -m promptpotter.cli.export_results json \
    --backend-id local --output paper_results.json
```

Or from the notebook:

```python
from promptpotter.display.campaign import generate_supplemental
md = generate_supplemental(session.store, session.backend_id)
```

See [`benchmarks.md`](benchmarks.md) for the full benchmark methodology, head-to-head comparison protocol, and result table format.

---

## Backend Requirements

PromptPotter works with any backend that exposes these endpoints:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/matches` | POST | Evaluate a query with pipeline parameters |
| `/pipeline` | GET | Pipeline schema (nodes, parameters, node types) |
| `/status` | GET | Health check |

The pipeline can be anything from a single LLM call to a multi-step pipeline with retrieval, enrichment, and ranking nodes. PromptPotter discovers the pipeline structure via `GET /pipeline` and optimizes the parameters it finds there.

**Currently tested with:** [TermNorm-excel](https://github.com/runfish5/TermNorm-excel) (AI terminology normalization — 5-node active pipeline: cache, fuzzy matching, web search, entity profiling, token matching. LLM ranking exists but is excluded due to bugs).

## REST API Endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /promptpotter/v1/backends` | Register a new backend connection |
| `GET /promptpotter/v1/backends` | List registered backends |
| `POST /promptpotter/v1/backends/{id}/sync` | Sync experiments from backend |
| `GET /promptpotter/v1/backends/{id}/pipeline` | Dynamic pipeline view (30s cache) |
| `GET /promptpotter/v1/campaigns` | List optimization campaigns |
| `GET /promptpotter/v1/campaigns/{id}` | Campaign detail with trial summaries |
| `GET /promptpotter/v1/health` | Service health check |

---

## Troubleshooting

**"No synced experiment data"** — Run `await client.sync_experiments(store, backend_id)` to sync from the backend.

**"No llm_ranking prompt found"** — The backend needs to expose prompts in experiment data. Ensure the prompt registry is initialized before syncing.

**"No queries have entity_profile"** — Re-run replay with `entity_profiling` in the pipeline nodes.

**LLM errors / timeouts** — Check your API key in `.env`. Groq has rate limits — back off if hitting 429s.

**scipy not found** — Install the stats extra: `pip install -e ".[stats]"`. Required for Wilson CI and significance tests used by scan analysis and export.

**Backend connection refused** — Ensure your backend is running and accessible at the URL passed to `--backend-url`. Check with `curl http://127.0.0.1:8000/status`.
