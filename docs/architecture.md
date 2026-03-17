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
  Sensitivity Scan                     Critique-Guided Feedback Cycle
  ┌──────────────────┐                 ┌───────────────────────────┐
  │ Measure axes     │  select best    │ Growth: generate          │
  │ Classify by      │───starting──────►  candidates using         │
  │  sensitivity     │  point          │  critique + thinking      │
  │ Show leaderboard │                 │  styles + scan analytics  │
  │ Query difficulty  │  scan context  │ Eval: evaluate via        │
  │ Show coverage    │─────────────────►  backend, select winner   │
  └──────┬───────────┘                 │ Critique: analyze         │
         │                             │  failures → next round    │
         │  all eval data              │ L1→L2→L3 escalation      │
         │  feeds back                 └────────┬──────────────────┘
         │                                      │
         └──────────────◄───────────────────────┘
              richer landscape → better starting point → repeat
```

**Human Loop** — OAT perturbation scan measures which axes matter. Variant leaderboard and query difficulty analytics provide visibility. You pick the best starting point.

**AI Loop** — Critique-guided feedback cycle. Each evaluation produces a **critique** (structured failure/success analysis) that feeds forward into the next round's candidate generation alongside sampled **thinking styles** as mutation guidance (PromptWizard-inspired). Critique and styles operate at the **optimizer agent level** — they guide the eval LLM, not the pipeline prompt. When scan data is available, each round also uses scan analytics (leaderboard, sensitivity, difficulty, tested values) to generate informed pipeline_param combinations with per-candidate overrides. 3-layer escalation (L1 generate → L2 refine_context → L3 modify_plan) on diminishing returns.

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

## The Optimizer Pipeline

The optimizer itself is a 4-step pipeline, designed to be modeled using the same `PipelineSchema`/`PipelineStep` architecture as the target backend (e.g., TermNorm).

| Step | Purpose | Current function | Trigger |
|------|---------|------------------|---------|
| `l1_generate` | Candidate generation | `generate_candidates()` in `prompt_optimizer.py` | Every round (also init mode via `restructure_context()`) |
| `l1_evaluate` | Eval + winner selection + critique | `evaluate_and_select_winner()` in `prompt_optimizer.py` | Every round |
| `l2_refine_context` | Context/parameter tuning | `refine_context()` in `layer_transitions.py` | L1 patience exhausted |
| `l3_modify_plan` | Strategic replanning | `modify_plan()` in `layer_transitions.py` | L2 patience exhausted OR `EscalationCheck(target="l3")` |

```
  ┌────────────────────────────────────────────────────┐
  │  l1_generate ──► l1_evaluate                       │
  │       ▲               │                            │
  │       │    critique +  │                            │
  │       └── styles ◄────┘                            │
  │                                                    │
  │  stall? ──────► l2_refine_context ──► resume L1      │
  │  stall? ──────► l3_modify_plan    ──► resume L2+L1  │
  │  escalation? ─► l3_modify_plan    ──► resume L1     │
  └────────────────────────────────────────────────────┘
```

**Key design decisions:**
- Init is `l1_generate` in naked mode (single decomposition pass, no critique/styles)
- Critique and thinking style sampling are sub-tools of `l1_evaluate`, not separate steps
- Pluggable `EscalationCheck`s run after each candidate eval — can short-circuit a round and route to L2/L3/abort
- The schema describes step capabilities; loop control stays in `feedback_cycle.py`

This model enables optimizer-level tracing (each step as a Langfuse observation), full reproducibility (every LLM call reconstructible from trial artifacts), and self-optimization (a meta-PromptPotter optimizing its own prompts). See the [M7 spec](specs/m7-optimizer-pipeline.md) for the full design.

## Further Reading

- [Design Principles](design-principles.md) — Core patterns
- [Sensitivity Scan & Grid Search](sensitivity-scan.md) — Exploration tools
- [Optimization](optimization.md) — Feedback cycle, 3-layer model, config reference
- [Observability](observability.md) — Langfuse, MLflow, data exploration
