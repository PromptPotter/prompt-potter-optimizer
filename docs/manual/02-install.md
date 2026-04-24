# Install

Four steps. Takes a couple of minutes.

## 1. Get a Groq API key

Sign up at [console.groq.com](https://console.groq.com) and create an API key. The free tier works.

Groq is the default optimizer LLM provider. If you prefer OpenAI, see [`operations/environment.md`](../operations/environment.md) for the alternative variables.

## 2. Clone the repo

```bash
git clone https://github.com/runfish5/prompt-potter-optimizer.git
cd prompt-potter-optimizer
```

Requires **Python 3.13+**.

## 3. Create `.env`

Create a file called `.env` in the repo root, containing exactly:

```
GROQ_API_KEY=your_key_here
LLM_PROVIDER=groq
LLM_MODEL=openai/gpt-oss-120b
```

Any Groq-hosted model ID works; `openai/gpt-oss-120b` is the recommended default for optimizer calls. If you hit free-tier rate limits, try `meta-llama/llama-4-scout-17b-16e-instruct`.

## 4. Install

```bash
pip install -e ".[all]"
```

`[all]` bundles every optional feature (Jupyter, benchmarks, observability, Excel loaders, etc.). If you want a minimal install or a specific extra, see [`operations/environment.md`](../operations/environment.md).

## Done

Next: [Your first campaign](03-first-campaign.md).
