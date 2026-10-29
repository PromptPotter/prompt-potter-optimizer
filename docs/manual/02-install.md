# Install

Five steps. Takes a couple of minutes.

## 1. Get an OpenRouter API key

Sign up at [openrouter.ai/keys](https://openrouter.ai/keys) and create an API key.

OpenRouter is the default LLM provider. If you prefer Groq, OpenAI or Anthropic, see [Environment variables](#environment-variables) below.

## 2. Clone the repo

```bash
git clone https://github.com/PromptPotter/prompt-potter-optimizer.git
cd prompt-potter-optimizer
```

Requires **Python 3.13+**.

## 3. Create `.env`

Create a file called `.env` in the repo root, containing:

```
OPENROUTER_API_KEY=your_key_here
```

If no key is set, `python -m promptpotter new` offers to write it for you on first run.

## 4. Install

```bash
pip install -e ".[all]"
```

Not developing on it? `pip install "promptpotter[all]"` instead, and skip step 2.

## 5. Reload Claude Code

If Claude Code was already open, **reload your session or restart VS Code now** — otherwise it won't see the `/potter-run` skill that ships with the repo.

## Done

Next: [Your first campaign](03-first-campaign.md).

---

## Environment variables

`.env` holds keys, never a model. Every key it may hold is listed in `.env.example`; the ones a first install meets:

| Variable | When required |
|----------|---------------|
| `OPENROUTER_API_KEY` | using OpenRouter (default) |
| `GROQ_API_KEY` | using Groq |
| `OPENAI_API_KEY` | using OpenAI |
| `ANTHROPIC_API_KEY` | using Anthropic |
| `TERMNORM_URL` | a TermNorm backend elsewhere than `http://127.0.0.1:8000` |
| `TERMNORM_TOKEN` | a TermNorm backend that requires auth |
