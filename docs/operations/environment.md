# Environment

Env variables, optional extras bundles, and Docker setup. For the minimal install path, see [../manual/02-install.md](../manual/02-install.md).

---

## Prerequisites

- Python 3.13+
- A running backend with a `/matches` evaluation endpoint — see [backend-integration.md](backend-integration.md)
- An LLM API key for the optimizer agent (Groq recommended for speed/cost)

---

## Installation

```bash
git clone https://github.com/runfish5/prompt-potter-optimizer.git
cd prompt-potter-optimizer
pip install -e .
```

The core install is intentionally minimal. Every optional feature is lazy-imported with a clear error message telling you which extras to install, so a missing dep never silently disables a feature.

### Optional dependency bundles

Install extras based on your use case:

```bash
pip install -e ".[stats]"          # Statistical analysis: Wilson CI, significance tests (scipy)
pip install -e ".[jupyter]"        # JupyterLab notebook interface + IPython display helpers
pip install -e ".[excel]"          # Excel dataset loading (pandas, openpyxl)
pip install -e ".[benchmarks]"     # HuggingFace benchmarks — GSM8K, AIME 2025, BBEH (datasets)
pip install -e ".[observability]"  # Langfuse cloud tracing
pip install -e ".[anthropic]"      # Anthropic Claude as optimizer LLM
pip install -e ".[dev]"            # Development: pytest, ruff, mypy, deptry, pre-commit
pip install -e ".[all]"            # Every optional feature bundled (excluding [dev])
pip install -e ".[all,dev]"        # Everything — recommended for contributors
```

---

## Environment variables

Create a `.env` file (see `.env.example`):

```
GROQ_API_KEY=your_groq_api_key
LLM_PROVIDER=groq
LLM_MODEL=openai/gpt-oss-120b
```

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_API_KEY` | Yes (if using Groq) | Groq API key for optimizer LLM calls |
| `OPENAI_API_KEY` | Yes (if using OpenAI) | OpenAI-compatible API key |
| `LLM_PROVIDER` | Yes | `groq` or `openai` |
| `LLM_MODEL` | Yes | Model identifier (e.g. `openai/gpt-oss-120b`) |
| `LANGFUSE_PUBLIC_KEY` | No | Langfuse cloud tracing |
| `LANGFUSE_SECRET_KEY` | No | Langfuse cloud tracing |
| `LANGFUSE_HOST` | No | Langfuse host URL |

---

## Entry-point quickstart

### Notebook

```bash
pip install -e ".[jupyter,stats]"
jupyter lab notebooks/optimization_campaign.ipynb
```

### API server

```bash
uvicorn promptpotter.main:app --port 8001 --reload
```

Swagger docs at `http://localhost:8001/docs`.

### Docker (one command)

```bash
cd docker && docker-compose up --build
# JupyterLab: http://localhost:8888  |  API: http://localhost:8001
```

### CLI

See [cli-reference.md](cli-reference.md).
