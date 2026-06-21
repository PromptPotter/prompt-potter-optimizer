# Install

Five steps. Takes a couple of minutes.

## 1. Get an OpenRouter API key

Sign up at [openrouter.ai/keys](https://openrouter.ai/keys) and create an API key.

OpenRouter is the default optimizer LLM provider — the optimizer meta-prompt is too large for Groq's free tier. If you prefer Groq, OpenAI, or Anthropic, see [§ Environment variables](#environment-variables) below for the alternative variables.

## 2. Clone the repo

```bash
git clone https://github.com/runfish5/prompt-potter-optimizer.git
cd prompt-potter-optimizer
```

Requires **Python 3.13+**.

## 3. Create `.env`

Create a file called `.env` in the repo root, containing exactly:

```
OPENROUTER_API_KEY=your_key_here
```

The optimizer model defaults to `openai/gpt-oss-120b` on OpenRouter. It's install-global, configured once in `datasets/_optimizer/pipeline.json` (per optimizer node's `config.model` / `config.provider`) — the same optimizer runs every campaign. To use a different model or provider, edit that file; set the corresponding `*_API_KEY` for Groq/Anthropic/OpenAI. There is no per-campaign or env-var override.

Connecting to a remote / auth-gated backend? See [`operations/backend-integration.md § Connection security`](../operations/backend-integration.md#connection-security).

## 4. Install

```bash
pip install -e ".[all]"
```

`[all]` bundles every optional feature (Jupyter, benchmarks, observability, Excel loaders, etc.). For a minimal install or a specific extra, see [§ Optional dependency bundles](#optional-dependency-bundles) below.

## 5. Reload Claude Code

If Claude Code (CLI, desktop app, or IDE extension) was already open, **reload your session or restart VS Code now** — otherwise it won't see the `/potter-run` skill that ships with the repo.

## Done

Next: [Your first campaign](03-first-campaign.md).

---

## Environment variables

The `.env` file (see `.env.example`) carries API keys. The optimizer's provider + model are install-global in `datasets/_optimizer/pipeline.json` (per optimizer node) — no per-campaign or env-var default. (Target/scoring model is per-dataset in the pipeline overlay.)

| Variable | When required | Purpose |
|----------|---------------|---------|
| `OPENROUTER_API_KEY` | using OpenRouter (default) | OpenRouter (`sk-or-…`) |
| `GROQ_API_KEY` | using Groq | Groq API key |
| `OPENAI_API_KEY` | using OpenAI | OpenAI API key |
| `ANTHROPIC_API_KEY` | using Anthropic | Anthropic API key |
| `LANGFUSE_PUBLIC_KEY` | optional | Langfuse cloud tracing |
| `LANGFUSE_SECRET_KEY` | optional | Langfuse cloud tracing |
| `LANGFUSE_HOST` | optional | Langfuse host URL |

Groq daily-volume model swap (when `120b` exhausts): [`05-troubleshooting.md § Groq daily token limit exhausted on 120b`](05-troubleshooting.md).

## Optional dependency bundles

```bash
pip install -e ".[stats]"          # Wilson CI, significance tests (scipy)
pip install -e ".[jupyter]"        # JupyterLab + IPython display
pip install -e ".[benchmarks]"     # GSM8K, AIME 2025, BBEH (HuggingFace datasets)
pip install -e ".[observability]"  # Langfuse cloud tracing
pip install -e ".[anthropic]"      # Anthropic Claude as optimizer LLM
pip install -e ".[dev]"            # pytest, ruff, mypy, deptry
pip install -e ".[all]"            # Everything except [dev]
pip install -e ".[all,dev]"        # Everything — recommended for contributors
```
