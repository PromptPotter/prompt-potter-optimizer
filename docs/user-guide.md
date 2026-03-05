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

PromptPotter uses two nested loops:

- **Human Loop** — you explore the landscape (sensitivity scan, grid search), run an optimization, harvest the results, and start again from a better position.
- **AI Loop** — the feedback cycle automatically generates candidate prompts, evaluates them against the backend, selects winners, and iterates until patience runs out.

Every evaluation is saved. When one optimization thread stops improving, the next sensitivity scan automatically discovers all stored data and computes a better starting point.

### 1. Evaluation Notebook

Open `notebooks/evaluation.ipynb`:

1. **Load** ground-truth dataset and connect to backend
2. **Evaluate** test-set accuracy against the current pipeline
3. **Compare** results across prompt variants

### 2. Optimization Campaign

Open `notebooks/optimization_campaign.ipynb`:

1. **Load baseline** and evaluate accuracy (Section 4)
2. **Sensitivity scan** — classify which prompt axes matter most (Section 4.5)
3. **Grid search** — explore combinations of high-impact axes (Section 4.6)
4. **Feedback cycle** — automated candidate generation and evaluation (Section 5)
5. **Review suggestions** and save the best prompt (Sections 6-7)

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

## Sensitivity Scan

Before grid search, a sensitivity scan identifies which prompt axes actually matter. It perturbs one axis at a time (OAT) and measures the accuracy delta against your baseline.

**When to use:** Before grid search, to avoid wasting budget on axes that don't affect accuracy. Also useful after an optimization round — re-scanning with all accumulated data reveals which axes still have room for improvement.

**How it works:**
1. A **diagnostic set** is built from your eval data (~75% baseline hits for regression guard, ~25% misses for improvement signal)
2. Each axis is perturbed independently, and the accuracy change is measured
3. Axes are classified as high / medium / low sensitivity
4. The **coverage advisor** checks what's already cached — if prior runs already evaluated a variant, those results are reused automatically

**In the notebook:** Section 4.5. Key cells: build diagnostic set, run historical audit, check coverage, run scan, select winner.

```python
# Coverage advisor shows what's already measured
coverage = assess_scan_coverage(plan, store, backend_id)
# Scan only evaluates what's missing — cached results are reused
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

## Feedback Cycle (Optimization)

The feedback cycle automates prompt optimization with 3-layer escalation:

```python
campaign_rounds = await run_feedback_cycle_notebook(
    campaign_rounds, eval_data, campaign_config, GROQ_API_KEY,
    store=svc["store"], backend_id=svc["backend_id"],
)
```

**How it works:**
1. Each round generates N candidate prompts via LLM, evaluates each against the backend, and selects the winner
2. If Layer 1 changes (persona, instruction, etc.) stop improving, the system escalates to Layer 2 (context refinement), then Layer 3 (strategy modification)
3. Stops when: `patience` consecutive non-improving rounds, `max_rounds` reached, or perfect accuracy

**In the notebook:** Section 5. Progress bars show per-query and per-candidate evaluation status.

Configure via `optimization` section:
```python
"optimization": {
    "patience": 3,               # rounds without improvement before auto-stop
    "improvement_threshold": 0.01,
    "n_variants": 5,
    "creativity": 0.7,
    "max_rounds": 10,
}
```

## Caching & Crash Recovery

**Safe to interrupt.** Long evaluations write results incrementally to `.partial.jsonl` files. If a run crashes or you restart the kernel, it resumes from where it stopped — no work is lost.

**Automatic deduplication.** Every evaluation is cached by a content hash (prompt text + queries + model + temperature). Re-running the same prompt against the same data returns cached results instantly, regardless of which optimization path produced them originally.

**Shared across all paths.** Grid search, sensitivity scan, and feedback cycle all write to the same `dataset_runs` store. The coverage advisor discovers all cached results and skips backend calls for variants that have already been evaluated. This means every optimization run enriches the next one.

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

## Langfuse Cloud Observability

All evaluation data is stored locally first (file-based traces in `.promptpotter/projects/{backend_id}/obs/`). Cloud Langfuse is optional — you can run optimization campaigns without it and push data later.

### Setup

Add to `.env`:

```
LANGFUSE_ENABLED=true
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com
```

### Live tracing (during optimization)

When credentials are set **before** running a feedback cycle, traces are pushed to Langfuse in real-time. Each campaign creates a trace with:
- A root `chain` observation (triggers the pipeline graph visualization)
- Per-round `span` observations with real start/end times
- Per-evaluation `tool` observations nested under their round
- Accuracy scores attached to each round

### Retroactive push (forgot to configure Langfuse)

If you ran an optimization campaign without Langfuse credentials, all evaluation data is still on disk. Push it after the fact:

```python
from _campaign_lib import configure_langfuse, push_langfuse

# 1. Enable Langfuse (if not already in .env)
configure_langfuse(
    enabled=True,
    public_key="pk-lf-...",
    secret_key="sk-lf-...",
)

# 2. Push all accumulated data
stats = push_langfuse(svc["store"], svc["backend_id"])
```

This creates one trace per dataset run, registers a ground-truth dataset with all queries, and links each evaluation to its dataset item. Re-running is safe — already-pushed runs are skipped.

### Re-pushing after clearing Langfuse

If you delete traces/datasets in the Langfuse UI and want to re-push everything, delete the local state file first:

```python
import os
state_path = os.path.join(
    svc["store"].base_dir, svc["backend_id"],
    "obs", "langfuse", "backfill_state.json",
)
os.remove(state_path)
stats = push_langfuse(svc["store"], svc["backend_id"])
```

## REST API

Start the API server: `uvicorn api.main:app --port 8001 --reload`

Response models and endpoints are auto-documented at `http://localhost:8001/docs` (Swagger UI).

### Key endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /api/v1/backends` | Register a new backend connection |
| `GET /api/v1/backends` | List registered backends |
| `POST /api/v1/backends/{id}/sync` | Sync experiments from backend |
| `GET /api/v1/backends/{id}/pipeline` | Dynamic pipeline view: backend pipeline config + local workflow nodes (30s cache) |
| `GET /api/v1/campaigns` | List optimization campaigns |
| `GET /api/v1/campaigns/{id}` | Campaign detail with trial summaries |
| `POST /api/v1/workflows/execute` | Execute a workflow definition |
| `GET /api/v1/health` | Service health check |

## Troubleshooting

**"No synced experiment data"** — Run the sync cell in `evaluation.ipynb` first, or call `await client.sync_experiments(store, backend_id)`.

**"No llm_ranking prompt found"** — Your backend needs to expose prompts in the experiment data. Ensure TermNorm's prompt registry is initialized before syncing.

**"No queries have entity_profile"** — Re-run replay with `entity_profiling` in the pipeline `steps`. The entity_profile is populated by the full pipeline.

**Grid search takes too long** — Reduce `grid_budget` in `grid_search` config, or reduce `eval_queries_per_point` for fewer queries per grid point.

**LLM errors / timeouts** — Check your API key in `.env`. Increase timeout in `eval_llm["max_tokens"]`. Groq has rate limits — add delays if hitting 429s.
