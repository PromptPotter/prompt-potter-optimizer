# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PromptPotter Optimizer is an API-first prompt optimization service that connects to Langfuse-compatible backends. It iteratively improves prompts through automated analysis and evaluation, delivered as both a FastAPI REST service and JupyterLab interactive environment.

**Core Philosophy**: Framework-agnostic (no LangChain/DSPy lock-in), Pydantic I/O with dependency injection, dual-mode delivery (notebooks + REST API).

## Commands

### Development
```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn api.main:app --reload
```

### Docker
```bash
cd docker
docker-compose up --build
```

### Testing
```bash
pytest                      # Run all tests
pytest tests/test_api.py    # Run specific test file
```

### Streamlit Apps
```bash
streamlit run apps/optimizer_client.py   # Optimization UI
streamlit run apps/secrets_manager.py    # API key configuration
```

## Architecture

```
api/
├── main.py                 # FastAPI app entry, router mounting
├── config/settings.py      # Pydantic BaseSettings (env config)
├── models/
│   ├── workflow.py         # WorkflowDefinition, StepDefinition (CWL-inspired)
│   └── request.py, response.py
├── routers/
│   ├── workflows.py        # /workflows/* endpoints
│   └── health.py, optimize.py
├── core/
│   ├── workflow_runner.py  # DAG execution engine
│   └── optimizer.py        # Legacy optimizer (placeholder)
├── nodes/                  # Composable workflow nodes
│   ├── base.py             # NodeBase[TInput, TOutput] generic
│   ├── llm_node.py         # LLMNode - LLM inference
│   ├── web_search_node.py  # WebSearchNode (mock)
│   └── ranker_node.py      # RankerNode - LLM-based ranking
├── evaluators/             # Evaluation framework
│   ├── base.py             # EvaluatorBase
│   ├── exact_match.py      # ExactMatchEvaluator
│   └── criteria.py         # CriteriaEvaluator (LLM-judge)
└── services/
    └── llm_client.py       # OpenAI/Anthropic abstraction

workflows/                  # CWL-inspired workflow definitions
├── examples/
│   ├── research_rank.yaml  # Web search + profile + rank
│   └── simple_llm.yaml     # Single LLM call
└── schemas/

apps/                       # Streamlit interactive UIs
docker/                     # Dockerfile, docker-compose.yml
tests/                      # pytest test suite
docs/                       # Design documentation
external/                   # GITIGNORED reference clones
```

## Key Endpoints

- `GET /api/v1/health` - Service status
- `GET /api/v1/ready` - Readiness check
- `POST /api/v1/optimize` - Legacy optimization endpoint
- `POST /api/v1/workflows/execute` - Execute a workflow
- `POST /api/v1/workflows/evaluate` - Evaluate workflow on dataset
- `GET /api/v1/workflows` - List registered workflows
- `GET /api/v1/nodes` - List available node types

## Configuration

Environment variables via `.env` (see `.env.example`):
- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` - LLM provider keys
- `DEFAULT_MODEL` - Fallback model (default: gpt-4)
- `MAX_ITERATIONS` - Optimization iteration limit (default: 5)
- `MAX_DATASET_SIZE` - Dataset size constraint (default: 1000)

## Design Patterns

- **NodeBase[TInput, TOutput]** - Generic base class for workflow nodes
- **CWL-inspired YAML** - Workflow definitions with typed inputs/outputs
- **Registry pattern** for optimization tracking (see `docs/registry-design.md`)
- **Parent-child run hierarchy** (MLflow/DSPy style) for campaign/trial tracking
- **JSONL format** for results (OpenAI Evals standard)

## Creating Custom Nodes

```python
from api.nodes.base import NodeBase
from pydantic import BaseModel

class MyInput(BaseModel):
    text: str

class MyOutput(BaseModel):
    result: str

class MyNode(NodeBase[MyInput, MyOutput]):
    @classmethod
    def get_input_model(cls): return MyInput

    @classmethod
    def get_output_model(cls): return MyOutput

    async def _execute(self, input_data: MyInput) -> MyOutput:
        return MyOutput(result=input_data.text.upper())

# Register: api/nodes/__init__.py
from .my_node import MyNode
register_node(MyNode)
```

## Current State

- **Workflow system**: Fully implemented with LLMNode, WebSearchNode (mock), RankerNode
- **Evaluators**: ExactMatchEvaluator and CriteriaEvaluator (LLM-judge)
- **WebSearchNode**: Mock implementation - add real providers (Brave, SearxNG) later
- **Legacy optimizer**: `api/core/optimizer.py` has placeholder implementations (TODO)

## External References

The `external/` directory is gitignored and contains reference clones (like TermNorm-excel) for documentation purposes only - no runtime dependency.
