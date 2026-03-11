# Setup Guide

## Prerequisites

- Python 3.13
- A running backend (e.g. TermNorm at `http://localhost:8000`)
- An LLM API key (Groq recommended for speed/cost)

## Installation

```bash
git clone <repo-url>
cd prompt-potter-optimizer
pip install -r requirements.txt
```

Create a `.env` file (see `.env.example`):

```
GROQ_API_KEY=your_groq_api_key
LLM_PROVIDER=groq
LLM_MODEL=your-model-id
```

## Quick Start

### Notebook (primary interface)

Open `notebooks/optimization_campaign.ipynb` for the full HITL optimization workflow. For standalone test-set evaluation, use `notebooks/evaluation.ipynb`.

### API server

```bash
uvicorn api.main:app --port 8001 --reload
```

Swagger docs at `http://localhost:8001/docs`.

### Docker

```bash
cd docker && docker-compose up --build
```

## REST API Endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /api/v1/backends` | Register a new backend connection |
| `GET /api/v1/backends` | List registered backends |
| `POST /api/v1/backends/{id}/sync` | Sync experiments from backend |
| `GET /api/v1/backends/{id}/pipeline` | Dynamic pipeline view (30s cache) |
| `GET /api/v1/campaigns` | List optimization campaigns |
| `GET /api/v1/campaigns/{id}` | Campaign detail with trial summaries |
| `POST /api/v1/workflows/execute` | Execute a workflow definition |
| `GET /api/v1/health` | Service health check |

## Troubleshooting

**"No synced experiment data"** — Run the sync cell in `evaluation.ipynb` first, or call `await client.sync_experiments(store, backend_id)`.

**"No llm_ranking prompt found"** — Your backend needs to expose prompts in the experiment data. Ensure TermNorm's prompt registry is initialized before syncing.

**"No queries have entity_profile"** — Re-run replay with `entity_profiling` in the pipeline steps. The entity_profile is populated by the full pipeline.

**LLM errors / timeouts** — Check your API key in `.env`. Increase timeout in `eval_llm["max_tokens"]`. Groq has rate limits — add delays if hitting 429s.
