# User Guide

## Prerequisites

- Python 3.13
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

### Two Primary Knobs

The optimization system is controlled by two top-level parameters:

| Knob | Range | Purpose |
|------|-------|---------|
| `queries_per_eval` | integer | Queries per optimization evaluation step |
| `exploration_rate` | 0.0–1.0 | Controls exploration aggressiveness — biases grid sampling toward conservative (low distance) or aggressive (high distance) grid points |

Set in the notebook's campaign config:
```python
campaign_config = {
    "queries_per_eval": 35,    # queries per optimization evaluation step
    "exploration_rate": 0.5,   # 0.0=conservative, 1.0=aggressive
    ...
}
```

### How `exploration_rate` Works

Each grid point has a **distance** = number of non-empty field values. The `exploration_rate` biases sampling toward different distance bands:

- **0.0 (conservative)**: Favors grid points with few changes from baseline (low distance)
- **0.5 (balanced)**: Even distribution across distance bands
- **1.0 (aggressive)**: Favors grid points with many changes (high distance)

The weight function is `w(d) = exp(-alpha * |d - target| / max_d)` where `target = exploration_rate * max_distance`.

### Grid Budget (`grid_budget`)

Set `grid_search.grid_budget` to control exactly how many grid points to evaluate:

```python
"grid_search": {
    "grid_budget": 35,   # exact budget (0=full grid)
    ...
}
```

When `grid_budget` exceeds the full cartesian product size, the full grid is used (capped).

### Per-Point Query Sampling (`eval_queries_per_point`)

Controls how many queries each grid point is evaluated on:

```python
"grid_search": {
    "eval_queries_per_point": 1,  # queries per grid point (0=use all eval_data)
    "shared_queries": False,      # False=different random queries per point
    ...
}
```

- **`eval_queries_per_point=1`** (default): Each grid point gets 1 randomly chosen query — fast landscape scanning
- **`shared_queries=False`** (default): Each point gets different random queries (seeded by `seed + point_index`)
- **`shared_queries=True`**: All grid points use the same query set — for rigorous comparison

### Default Grid Axes

```python
DEFAULT_GRID_AXES = {
    "persona": ["", "You are a domain expert...", "You are a precise, analytical system...", ...],
    "task_intent": ["", "Your task is to identify the single best match...", ...],
    "thinking_style": ["", "Think step by step.", "Focus on semantic meaning...", ...],
    "answer_format": ["", "Rank all candidates from most to least relevant."],
}
```

### Improvement Areas

Domain expert guidance injected into the LLM consultant during context restructuring (cell 4.5a). Describe where you believe improvement is most likely:

```python
improvement_areas = "profile schema quality, web search relevance"
```

When set, `restructure_context()` returns a `consultation` key with natural-language strategic advice tailored to your observations.

### Custom Grid

Override any axis or add new ones from `GRID_SEARCHABLE_FIELDS`:

```python
grid_config = {
    "persona": ["", "You are a medical terminology expert."],
    "thinking_style": ["", "Think step by step.", "Consider semantic similarity."],
}
```

## Progress Tracking

After each round (grid search winner, optimization rounds), a training-style progress table is displayed:

```
Round  Accuracy  Rolling Avg (8)  Trend
  0    62.9%     62.9%            -
  G    71.4%     67.1%            +8.6%
  1    74.3%     69.5%            +2.9%
  2    74.3%     70.7%            +0.0%  <-- plateau
```

- **Rolling Avg**: Smoothed accuracy over the last 8 rounds
- **Trend**: Per-round improvement indicator; plateau detection shows when accuracy stalls

## Semi-Automatic Optimization

The `run_optimization_loop()` function automates the generate → evaluate → select cycle:

```python
campaign_rounds = await run_optimization_loop(
    campaign_rounds, eval_data, campaign_config, GROQ_API_KEY,
    store=svc["store"], backend_id=svc["backend_id"],
)
```

Behavior:
- Subsamples `eval_data` to `n_samples` queries per round
- Auto-continues while improvement exceeds `improvement_threshold`
- Stops after `patience` consecutive rounds without improvement
- Displays progress after each round

Configure via `optimization` section:
```python
"optimization": {
    "patience": 3,               # rounds without improvement before auto-stop
    "improvement_threshold": 0.01,
    "n_variants": 5,
    "creativity": 0.7,
}
```

## Configuration Reference

### campaign_config

```python
campaign_config = {
    "queries_per_eval": 35,         # Queries per optimization evaluation step
    "exploration_rate": 0.5,        # PRIMARY: 0.0=conservative, 1.0=aggressive
    "optimization": {
        "n_variants": 5,            # Candidates per round
        "creativity": 0.7,          # Meta-prompt temperature (0.0-1.0)
        "improvement_threshold": 0.01,  # Min accuracy improvement to accept
        "patience": 3,              # Rounds without improvement before auto-stop
        "max_rounds": 10,
    },
    "eval_llm": { ... },
    "grid_search": {
        "grid_budget": 35,          # Exact budget (0=full grid)
        "eval_queries_per_point": 1,  # Queries per grid point
        "shared_queries": False,    # Different random queries per point
        "seed": 42,
        "top_k": 5,
        "use_defaults": True,
    },
}
```

### eval_llm

```python
eval_llm = {
    "model": "meta-llama/llama-4-maverick-17b-128e-instruct",
    "provider_url": "https://api.groq.com/openai/v1/chat/completions",
    "temperature": 0,
    "max_tokens": 4000,
}
```

## Troubleshooting

**"No synced experiment data"** — Run the sync cell in `termnorm_backend.ipynb` first, or call `await client.sync_experiments(store, backend_id)`.

**"No llm_ranking prompt found"** — Your backend needs to expose prompts in the experiment data. Ensure TermNorm's prompt registry is initialized before syncing.

**"No queries have entity_profile"** — Re-run replay with `skip_llm_ranking=False`. The entity_profile is populated by the full pipeline.

**Grid search takes too long** — Reduce `grid_budget` in `grid_search` config, or reduce `eval_queries_per_point` for fewer queries per grid point.

**LLM errors / timeouts** — Check your API key in `.env`. Increase timeout in `eval_llm["max_tokens"]`. Groq has rate limits — add delays if hitting 429s.
