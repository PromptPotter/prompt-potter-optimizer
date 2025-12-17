# PromptPotter Optimizer

**Optimize prompts for any LLM application that logs in Langfuse-compatible format.**

## How It Works

PromptPotter connects to your existing FastAPI backend (or Langfuse server) and:

1. **Fetches** evaluation traces, failed cases, and metrics from your application
2. **Analyzes** failure patterns using LLM-powered reasoning
3. **Generates** improved prompt variants
4. **Evaluates** new prompts against your dataset
5. **Repeats** until convergence or max iterations

```
┌─────────────────────┐         ┌─────────────────────┐
│  Your Application   │         │  PromptPotter       │
│  (FastAPI backend)  │◄───────►│  Optimizer API      │
│                     │  fetch  │                     │
│  - Langfuse logs    │  traces │  - Analyze failures │
│  - Evaluation data  │         │  - Generate prompts │
│  - Match results    │         │  - Run experiments  │
└─────────────────────┘         └─────────────────────┘
```

**Works with:**
- Any FastAPI backend logging in [Langfuse-compatible format](https://langfuse.com/docs)
- Langfuse server directly
- Custom evaluation endpoints

**Example integration:** [TermNorm-excel](https://github.com/runfish5/TermNorm-excel) - an AI-powered terminology normalization add-in with Langfuse-compatible logging.

## Quick Start

```bash
# Clone and setup
git clone https://github.com/yourusername/prompt-potter-optimizer.git
cd prompt-potter-optimizer
cp .env.example .env
# Edit .env with your API keys

# Run with Docker
cd docker
docker-compose up --build
```

**Open:**
- **JupyterLab**: http://localhost:8888 (with custom learning path tiles)
- **FastAPI**: http://localhost:8000/docs

### JupyterLab Launcher Tiles

Look for **"PromptPotter Learning Path"** in the JupyterLab launcher:

| Tile | Description |
|------|-------------|
| Introduction to Prompt Optimization | Quickstart tutorial notebook |
| Advanced Optimization | Multi-iteration techniques |
| Secrets Manager | Configure API keys (OpenAI, Anthropic) |
| Prompt Optimizer Client | Interactive optimization UI |

## Overview

PromptPotter is a **companion service** for LLM applications. Instead of manually analyzing logs and tweaking prompts, point PromptPotter at your Langfuse-compatible backend and let it optimize automatically.

**Key Features:**
- **Langfuse-compatible**: Connects to any backend using Langfuse logging format
- **API-First**: REST API works from Colab notebooks, scripts, or other services
- **Iterative Optimization**: Analyzes failures, generates variants, evaluates, repeats
- **Framework-agnostic**: No LangChain/DSPy lock-in required

## API Usage

```python
import requests

response = requests.post(
    "http://localhost:8000/api/v1/optimize",
    json={
        "initial_prompt": "Classify the sentiment:",
        "dataset": [
            {"text": "I love this!", "expected": "positive"},
            {"text": "This is terrible", "expected": "negative"}
        ],
        "target_metric": "accuracy",
        "max_iterations": 5
    }
)
result = response.json()
print(f"Optimized: {result['optimized_prompt']}")
```

## Project Structure

```
prompt-potter-optimizer/
├── api/                 # FastAPI application
├── apps/                # Streamlit apps (secrets_manager, optimizer_client)
├── docker/              # Docker configs
├── examples/            # Tutorial notebooks
├── launcher/            # JupyterLab tiles config
├── tests/               # Tests
├── prompts/             # LLM agent prompt templates
├── docs/                # Extended documentation
└── external/            # Local reference clone (gitignored, see below)
```

### External Reference

The `external/` folder is gitignored and contains a local clone of [TermNorm-excel](https://github.com/runfish5/TermNorm-excel) for reference. Clone it yourself if needed:

```bash
git clone https://github.com/runfish5/TermNorm-excel external/TermNorm-excel
```

This shows how a real FastAPI backend implements Langfuse-compatible logging that PromptPotter can consume.

## Configuration

Edit `.env` file:

| Variable | Description | Default |
|----------|-------------|---------|
| `TARGET_API_URL` | Your backend's API URL (Langfuse-compatible) | - |
| `LANGFUSE_HOST` | Or connect to Langfuse server directly | - |
| `OPENAI_API_KEY` | OpenAI API key (for optimization LLM) | - |
| `ANTHROPIC_API_KEY` | Anthropic API key (alternative) | - |
| `MAX_ITERATIONS` | Max optimization iterations | 5 |

## Local Development

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn api.main:app --reload
```

## Documentation

- `docs/architecture.md` - Design patterns and dual-mode philosophy
- `docs/registry-design.md` - Optimization tracking patterns (MLflow/DSPy style)

## NVIDIA Brev Deployment

1. Go to [brev.nvidia.com](https://brev.nvidia.com)
2. Create launchable → select this repo
3. Use `.brev/setup.sh` as setup script

## License

MIT License - see LICENSE file.
