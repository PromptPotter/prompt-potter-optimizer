# Architecture

## System Overview

Two entry points, shared service core:

1. **Jupyter notebook** — `notebooks/optimization_campaign.ipynb` uses `notebooks/_campaign_lib/` (6 submodules) wrapping services with progress bars. No business logic in the notebook layer.
2. **FastAPI API** (`api/main.py`) — REST at `/api/v1/`. Routers: `backends`, `campaigns`, `health`.

All core logic in `api/services/`.

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

**OptSearchPoint** — optimizer-level checkpoint per round. Bundles critique_text, thinking_styles, plan, optimizer_params, `task_context` (structured domain context refinable by L2), and content_hashes linking to target evaluation data.

**EvalContext** — groups infrastructure for evaluation (defined in `api/services/prompt_eval.py`).

## Evaluation Flow

All paths converge on `evaluate_prompt_cached()` — single gateway for eval persistence with content-addressed dedup via `eval_content_hash()`.

**Prompt alias groups** link semantically equivalent prompts so historical data from either form is discoverable. See [Design Principles](design-principles.md#prompt-alias-groups).

## Caching & Crash Recovery

- **Content-hash dedup** — same configuration returns cached results instantly
- **Shared store** — grid search, sensitivity scan, and feedback cycle all write to `dataset_runs`; coverage advisor discovers all cached results
- **Write with experiment_id, read by config similarity** — all dataset_runs are tagged with `experiment_id` for provenance, but reads use alias groups + pipeline_param matching to find results by config similarity, not experiment scope. This means data is shared across experiments via content-addressed dedup.

## Pipeline Composability

**`pipeline_params`** format throughout — nested dicts keyed by step name (e.g. `{"llm_ranking": {"temperature": 0.5}}`). `BackendClient.run_match()` translates to the `node_config` wire-format key at the TermNorm boundary. See [`connectors/termnorm.md`](connectors/termnorm.md) for key mapping.

## Pipeline Discovery

`GET /backends/{id}/pipeline` returns pipeline config with resolved registry metadata (30s TTL cache). Backend owns schema/prompt artifacts; PromptPotter consumes the live response.

## The Optimizer Pipeline

The optimizer itself is a 4-step pipeline, designed to be modeled using the same `PipelineSchema`/`PipelineStep` architecture as the target backend (e.g., TermNorm).

| Step | Purpose | Current function | Trigger |
|------|---------|------------------|---------|
| `l1_generate` | Candidate generation (sole pipeline_params decider) | `l1_generate()` in `prompt_optimizer.py` | Every round (also init mode via `restructure_context()`) |
| `l1_evaluate` | Eval + winner selection + critique | `l1_evaluate()` in `prompt_optimizer.py` | Every round |
| `l2_refine_context` | Situation context + meta-settings (creativity, n_variants, sample_size) | `refine_context()` in `layer_transitions.py` | L1 patience exhausted or degradation escalation |
| `l3_modify_plan` | Strategic replanning | `modify_plan()` in `layer_transitions.py` | L2 patience exhausted OR `EscalationCheck(target="l3")`  |

```
  ┌──────────────────────────────────────────────────────────┐
  │  l1_generate ────► l1_evaluate (+ critique)              │
  │       ▲                 │                                │
  │       │  critique (5 fields)                             │
  │       │  + thinking_styles                               │
  │       └──────── ◄───────┘                                │
  │                                                          │
  │  stall?       ──► l2_refine_context ──► resume L1        │
  │  degradation? ──► l2_refine_context ──► resume L1        │
  │  l2 stall?    ──► l3_modify_plan    ──► resume L2+L1     │
  └──────────────────────────────────────────────────────────┘
```

**Key design decisions:**
- **L1 Generate is the sole pipeline_params decider.** L2 refines context and meta-settings; L3 modifies the strategic plan. Neither touches pipeline_params directly.
- **Setup** before the loop: define pipeline → load dataset → decompose `TASK_DESCRIPTION` → `task_context` → restructure prompt → sensitivity scan → baseline eval + bootstrap critique. Init is `l1_evaluate` in naked mode (eval + critique, no candidate generation).
- Critique produces 5 fields (`positive_critique`, `negative_critique`, `priority_fix`, `suggested_axes`, `summary`) — fed to both L1 Generate and L2 Refine. See [`critique-agent.md`](critique-agent.md) for details.
- Pluggable `EscalationCheck`s run after each candidate eval — can short-circuit a round and route to L2/L3/abort.
- The schema describes step capabilities; loop control stays in `feedback_cycle.py`.

This model enables optimizer-level tracing (each step as a Langfuse observation), full reproducibility (every LLM call reconstructible from trial artifacts), and self-optimization (a meta-PromptPotter optimizing its own prompts). See the [M7 spec](specs/m7-optimizer-pipeline.md) for the full design.

### EXPERIMENT_ID & Config Stability

`EXPERIMENT_ID` is the single source of truth for the notebook. When set, config MUST match the stored experiment — mismatches raise `ValueError`. When `None`, a new experiment is auto-created from the config hash.

- **Writing**: all dataset_runs, campaign data, Langfuse traces are tagged with experiment_id
- **Reading**: scan/grid results are found by **config similarity** (alias groups + pipeline_param matching), not experiment_id — data is shared across experiments via content-addressed dedup
- **Dashboard**: `show_experiment_dashboard()` loads stored config, overrides notebook variables, shows resume status

## Further Reading

- [Design Principles](design-principles.md) — Core patterns
- [Sensitivity Scan & Grid Search](sensitivity-scan.md) — Exploration tools
- [Optimization](optimization.md) — Feedback cycle, 3-layer model, config reference
- [Observability](observability.md) — Langfuse, MLflow, data exploration
