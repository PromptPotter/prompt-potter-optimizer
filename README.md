# PromptPotter Optimizer

**Chain it your way: workflows • algorithms • LLMs • web researchers • custom evaluators**

API-first prompt optimization service that iteratively improves prompts based on dataset performance.

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

PromptPotter Optimizer helps you optimize prompts for LLM applications. Upload a dataset with examples, specify your target metric, and get back an optimized prompt.

**Key Features:**
- API-First Design (REST API for any client)
- Iterative Optimization with LLM feedback
- JupyterLab environment with custom launcher tiles
- Works with OpenAI, Anthropic, and other providers

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
├── research/            # Research artifacts (being compiled into docs)
└── external/            # Git submodule: TermNorm-excel (read-only reference)
```

## Configuration

Edit `.env` file:

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY` | OpenAI API key | - |
| `ANTHROPIC_API_KEY` | Anthropic API key | - |
| `MAX_ITERATIONS` | Max optimization iterations | 5 |
| `DEFAULT_MODEL` | Default LLM model | gpt-4 |

## Local Development

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn api.main:app --reload
```

## Documentation

- `docs/architecture.md` - Design patterns
- `docs/registry-design.md` - Optimization tracking patterns
- `docs/submodule-strategy.md` - External reference policy

## NVIDIA Brev Deployment

1. Go to [brev.nvidia.com](https://brev.nvidia.com)
2. Create launchable → select this repo
3. Use `.brev/setup.sh` as setup script

## License

MIT License - see LICENSE file.
