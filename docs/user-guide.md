# User Guide

## Prerequisites

- Python 3.11+
- A running backend (e.g. TermNorm at `http://localhost:8000`)
- An LLM API key (Groq recommended for speed/cost)

## Setup

```bash
git clone <repo-url>
cd prompt-potter-optimizer
pip install -r requirements.txt
```

Create a `.env` file (see `.env.example`):

```
GROQ_API_KEY=your_groq_api_key
LLM_PROVIDER=groq
LLM_MODEL=meta-llama/llama-4-maverick-17b-128e-instruct
```

## Workflow Overview

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────┐
│  Exploration  │ ──► │  Grid Search      │ ──► │  Optimization │
│  Notebook     │     │  (landscape)      │     │  Campaign     │
└──────────────┘     └──────────────────┘     └──────────────┘
termnorm_backend      optimization_campaign     optimization_campaign
.ipynb                .ipynb (Section 6)        .ipynb (Sections 7-9)
```

### 1. Exploration Notebook

Open `notebooks/termnorm_backend.ipynb`:

1. **Register** your backend connection
2. **Sync** experiment data (queries, ground truth, pipeline traces)
3. **Replay** queries through the pipeline to establish baseline
4. **Analyze** candidate coverage and diagnostic metrics
5. **Compare** pipeline variants statistically

### 2. Optimization Campaign

Open `notebooks/optimization_campaign.ipynb`:

1. **Load baseline** prompt from synced experiment data
2. **Filter** evaluation data (queries with entity_profile)
3. **Evaluate baseline** accuracy
4. **Grid search** over prompt component axes (persona, thinking_style, etc.)
5. **Iterative optimization** — generate candidates via LLM, evaluate, select winners
6. **Get suggestions** — LLM-generated improvement advice
7. **Save** the best prompt to the project store

## 3-Layer PromptState

Prompts are structured into three layers with different optimization cadences:

### Layer 1: Generate

Prompt components that vary every optimization pass:

| Field | Purpose | Example |
|-------|---------|---------|
| `persona` | Who the LLM acts as | "You are a domain expert..." |
| `task_intent` | What the prompt accomplishes | "Identify the best match..." |
| `problem_description` | Problem domain context | "Terminology normalization..." |
| `instruction` | Core instruction (may have template vars) | "Rank {{matches}} for {{core_concept}}..." |
| `thinking_style` | Reasoning approach | "Think step by step" |
| `answer_format` | Expected output format | "Return JSON with ranked_candidates" |
| `few_shot_examples` | Input/output demonstration pairs | (list of FewShotExample) |

### Layer 2: Refine Context

Adjusted when Layer 1 improvements stall:

| Field | Purpose |
|-------|---------|
| `context` | Additional optimization context |
| `parameters` | Hypervariables (family, version, template_variables) |

### Layer 3: Modify Plan

Optimization strategy — rarely changed:

| Field | Purpose |
|-------|---------|
| `plan` | High-level optimization strategy |

`render()` assembles Layer 1 fields into the final prompt string. `derive()` creates child states, forming a lineage chain.

## Grid Search

Grid search explores the prompt landscape by evaluating a cartesian product of field variants.

### Default Grid Axes

```python
DEFAULT_GRID_AXES = {
    "persona": ["", "You are a domain expert...", "You are a precise, analytical system...", ...],
    "task_intent": ["", "Your task is to identify the single best match...", ...],
    "thinking_style": ["", "Think step by step.", "Focus on semantic meaning...", ...],
    "answer_format": ["", "Rank all candidates from most to least relevant."],
}
```

### Custom Grid

Override any axis or add new ones from `GRID_SEARCHABLE_FIELDS`:

```python
grid_config = {
    "persona": ["", "You are a medical terminology expert."],
    "thinking_style": ["", "Think step by step.", "Consider semantic similarity."],
}
```

## Configuration Reference

### campaign_config

```python
campaign_config = {
    "n_variants": 3,           # Candidates per round
    "creativity": 0.7,         # Meta-prompt temperature (0.0-1.0)
    "improvement_threshold": 0.02,  # Min accuracy improvement to accept
    "max_rounds": 5,           # Optimization iterations
}
```

### eval_llm

```python
eval_llm = {
    "model": "meta-llama/llama-4-maverick-17b-128e-instruct",
    "provider_url": "https://api.groq.com/openai/v1/chat/completions",
    "temperature": 0.1,
    "max_tokens": 4096,
}
```

## Troubleshooting

**"No synced experiment data"** — Run the sync cell in `termnorm_backend.ipynb` first, or call `await client.sync_experiments(store, backend_id)`.

**"No llm_ranking prompt found"** — Your backend needs to expose prompts in the experiment data. Ensure TermNorm's prompt registry is initialized before syncing.

**"No queries have entity_profile"** — Re-run replay with `skip_llm_ranking=False`. The entity_profile is populated by the full pipeline.

**Grid search takes too long** — Reduce combinations with `max_combinations` parameter in `build_grid_combinations()`, or reduce `query_limit` in the evaluation dataset.

**LLM errors / timeouts** — Check your API key in `.env`. Increase timeout in `eval_llm["max_tokens"]`. Groq has rate limits — add delays if hitting 429s.
