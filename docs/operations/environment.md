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
LLM_MODEL=openai/gpt-oss-120b
```

Provider selection lives on `CampaignConfig.optimizer_llm.provider` (in each dataset's `campaign.json`) — there is no env-var default. Set the API key for whichever providers you'll use.

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_API_KEY` | Yes (if using Groq) | Groq API key for optimizer LLM calls |
| `OPENAI_API_KEY` | Yes (if using OpenAI) | OpenAI API key |
| `ANTHROPIC_API_KEY` | Yes (if using Anthropic) | Anthropic API key |
| `OPENROUTER_API_KEY` | Yes (if using OpenRouter) | OpenRouter API key (`sk-or-…`) |
| `LLM_MODEL` | Yes | Default model identifier when `optimizer_llm.model` is null (e.g. `openai/gpt-oss-120b`) |
| `LANGFUSE_PUBLIC_KEY` | No | Langfuse cloud tracing |
| `LANGFUSE_SECRET_KEY` | No | Langfuse cloud tracing |
| `LANGFUSE_HOST` | No | Langfuse host URL |

### Groq daily-volume model swap

`openai/gpt-oss-120b` is the canonical default for all reasoning datasets. During development, when daily volume on `120b` is exhausted, swap the `model` field in the relevant `datasets/<name>/pipeline.json` to `openai/gpt-oss-20b` and keep iterating; flip back to `120b` for benchmarking / publication runs. Each dataset's `reasoning_effort` default is tuned to keep both models clear of Groq's per-model output ceiling — see [`.claude/skills/potter-run/reference/dataset-reasoning-matrix.md`](../../.claude/skills/potter-run/reference/dataset-reasoning-matrix.md) for the full table. Most importantly, `bbeh` ships `reasoning_effort: low` so the smaller `20b` model doesn't burn its reasoning budget before emitting a visible answer.

`max_tokens` is **never** set as a numeric default in any dataset's `pipeline.json` node config — provider ceiling applies. Operators raise the cap per-cycle via `campaign.json::pipeline_overrides`, not by editing the dataset default.

### Provider swap notes (dev log)

Operator dev notes — only useful when picking a model to iterate against. Benchmark / publication runs always go back to Groq `openai/gpt-oss-120b`.

- **2026-04-27** — Groq developer plan suspended on this account, so iteration moved to OpenRouter. Quick latency probe (single-query feel test, not a benchmark): `mistralai/mistral-small-3.2-24b-instruct` was clearly the fastest of the cheap models tried; `google/gemini-2.5-flash` and the Qwen variants felt comparably slow. Switched both `datasets/bbeh/pipeline.json` (`llm_only.config.model` + `provider: openrouter`) and `datasets/bbeh/campaign.json` (`optimizer_llm`) to mistral-small for the next experimentation stage.
- **Keep in mind when swapping again:** the target-layer model lives in `datasets/<name>/pipeline.json::llm_only.config` (and needs `provider` if it's not Groq); the optimizer-layer model lives in `datasets/<name>/campaign.json::optimizer_llm`. They're independent — flip them together when you're just changing inference vendor, separately when you're A/B-ing one layer.

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
