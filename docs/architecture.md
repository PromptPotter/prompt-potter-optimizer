# Architecture

## System Overview

Two entry points, shared service core:

1. **Jupyter notebook** — `notebooks/optimization_campaign.ipynb` uses `notebooks/_campaign_lib/` (6 submodules) wrapping services with progress bars. No business logic in the notebook layer.
2. **FastAPI API** (`api/main.py`) — REST at `/api/v1/`. Routers: `backends`, `campaigns`, `health`, `workflows`.

All core logic in `api/services/`. See [`api/services/CLAUDE.md`](../api/services/CLAUDE.md).

## Two-Loop Architecture

```
  HUMAN LOOP                           AI LOOP (Potter)
  ──────────                           ────────────────
  Sensitivity Scan                     Feedback Cycle
  ┌──────────────────┐                 ┌──────────────────┐
  │ Measure axes     │  select best    │ Generate         │
  │ Classify by      │───starting──────►  candidates      │
  │  sensitivity     │  point          │ Evaluate via     │
  │ Show coverage    │                 │  backend         │
  └──────┬───────────┘                 │ Select winner    │
         │                             │ Iterate until    │
         │  all eval data              │  patience runs   │
         │  feeds back                 │  out             │
         │                             └────────┬─────────┘
         │                                      │
         └──────────────◄───────────────────────┘
              richer landscape → better starting point → repeat
```

**Human Loop** — OAT perturbation scan measures which axes matter. You pick the best starting point.

**AI Loop** — Feedback cycle generates candidates, evaluates, selects winners. 3-layer escalation (Layer 1 → Layer 2 → Layer 3).

## Data Model

**SearchPoint** — immutable bundle of `prompt_state` + `model` + `temperature` + `pipeline_params`. All mutations via `.derive()`.

**PipelineSchema** — defines the backend pipeline. Together: `f(SearchPoint, PipelineSchema, eval_data) → scores`.

**EvalContext** — groups infrastructure for evaluation. See [`api/models/CLAUDE.md`](../api/models/CLAUDE.md).

## Evaluation Flow

All paths converge on `evaluate_prompt_cached()` — single gateway for eval persistence with content-addressed dedup via `eval_content_hash()`.

**Prompt alias groups** link semantically equivalent prompts so historical data from either form is discoverable. See [Design Principles](design-principles.md#prompt-alias-groups).

## Caching & Crash Recovery

- **Incremental writes** — `.partial.jsonl` files enable resume after crash
- **Content-hash dedup** — same configuration returns cached results instantly
- **Shared store** — grid search, sensitivity scan, and feedback cycle all write to `dataset_runs`; coverage advisor discovers all cached results

## Pipeline Composability

**`node_config`** format throughout — same nested dict shape as `pipeline.json` and `/matches` wire format. `run_match()` forwards as-is to the backend. See [`connectors/termnorm.md`](connectors/termnorm.md) for key mapping.

## Pipeline Discovery

`GET /backends/{id}/pipeline` returns pipeline config with resolved registry metadata (30s TTL cache). Backend owns schema/prompt artifacts; PromptPotter consumes the live response.

## Further Reading

- [Design Principles](design-principles.md) — Core patterns
- [Sensitivity Scan & Grid Search](sensitivity-scan.md) — Exploration tools
- [Optimization](optimization.md) — Feedback cycle, 3-layer model, config reference
- [Observability](observability.md) — Langfuse, MLflow, data exploration
