# Milestone 7: Optimizer-as-Pipeline

**Version:** 1.1.0
**Date:** 2026-03-19
**Status:** Reset for re-implementation (v2 branch). v1 implementation (`feat/m8-optimizer-pipeline`) retained as code reference.
**Depends on:** [M6 PipelineSchema](m6-pipeline-composability.md), [ADD v0.10.0](add.md)

---

## 1. Context & Motivation

PromptPotter optimizes workflow pipelines (currently TermNorm's 6-step terminology normalization pipeline). The optimizer itself is a workflow pipeline with 4 LLM-driven steps (+ L4 meta-optimization as a future conceptual extension). These steps share the same structural properties as any target backend pipeline:

- Each step has defined **inputs and outputs**
- Each step has a **parameter surface** (model, temperature, max_tokens, etc.)
- Each step involves **LLM calls** with specific prompts
- Steps form a **loop topology** with conditional escalation

Modeling the optimizer using the same `PipelineSchema`/`PipelineStep` architecture solves three problems by design:

1. **Tracing** — Optimizer steps get the same Langfuse tracing infrastructure. `critique_text`, `thinking_styles`, L2/L3 transition rationale, escalation signals, and meta-prompts are captured per step.
2. **Reproducibility** — Every meta-optimizer decision traced with full I/O. Given a trial JSON, you can reconstruct every LLM call.
3. **Self-optimization** — A meta-PromptPotter instance can optimize the optimizer's own prompts via `GET /optimizer/pipeline`. L4 completes the escalation hierarchy.

### The Tracing Gap (pre-M7)

Artifacts not persisted (lost after each cycle):

| Artifact | Where it lives | Persistence |
|----------|---------------|-------------|
| `critique_text` | `_LoopState.critique_text` | Memory only — overwritten each round |
| `thinking_styles` | `_LoopState.thinking_styles` | Memory only — resampled each round |
| `plan` | `PromptState.plan` | Buried in prompt_state, not indexed |
| `task_context` | `OptSearchPoint.task_context` | Structured domain context (set at campaign init, refined by L2) |
| `optimizer_params` | `OptSearchPoint.optimizer_params` | Optimizer meta-settings (creativity, n_variants, etc.) |
| L2 transition rationale | `refine_context()` LLM response | Only derived PromptState kept |
| L3 transition rationale | `modify_plan()` LLM response | Only derived PromptState kept |
| L2/L3 transition inputs | stalled_rounds / l2_history | Not persisted |
| Candidate generation prompt | `_build_*_meta_prompt()` | Not logged |
| Scan context enrichment | `prepare_scan_context()` output | Lost after feeding to meta-prompt |

**Phase 0.5 of v1 solved:** critique_text, thinking_styles, plan, task_context, optimizer_params — via `OptSearchPoint` checkpointing in trial JSON + node-level Langfuse tracing via `NodeBase`.

---

## 2. Architectural Decisions Log

Seven key design choices from the v1 implementation with rationale. These MUST be preserved in v2.

### ADR-1: Critique inside L1EvaluateNode, not orchestrator

**Decision:** `CritiqueAgent.run()` and `sample_thinking_styles()` execute inside `L1EvaluateNode._execute()`, producing `critique_text` and `thinking_styles` as output fields.

**Rationale:** Critique output feeds the *next* round's generation — it's part of L1Evaluate's output contract. Keeping it in the node means Langfuse traces capture the critique LLM call as a child of the evaluate span, and the node's output model documents the full data contract.

**Alternative rejected:** Running critique in the orchestrator after `L1EvaluateNode.process()`. This splits the observation across two trace locations and requires the orchestrator to know about critique internals.

### ADR-2: `baseline_rendered` INCLUDED in `cycle_config_identity()`

**Decision:** `cycle_config_identity()` includes `baseline_rendered` alongside optimization-relevant config fields + sorted eval_data pairs.

**Rationale:** Removing `baseline_rendered` from the hash orphans existing campaign data — the cycle_id changes, breaking campaign continuity (resume, trial lookup, dashboard display). The non-determinism concern from `restructure_context()` is acceptable because the baseline is typically set once per experiment and reused across kernel restarts via `baseline_prompt_state` passthrough.

**Fields included:** `max_rounds`, `patience`, `n_variants`, `creativity`, `improvement_threshold`, `model`, `provider`, `temperature`, `sample_size`, `seed`, `baseline_rendered`, sorted `eval_data_pairs`.

### ADR-3: `scan_context` as node input, not config

**Decision:** `scan_context` is a field on `L1GenerateInput`, not a config key on the node.

**Rationale:** Scan context can change between rounds (e.g., after L2 transition changes the pipeline). Node config is set at construction time; input data varies per invocation. The orchestrator resolves scan context and passes it as input.

**Implementation note:** `CycleConfig.scan_context` carries the initial value. The orchestrator forwards it to `L1GenerateNode` via input, not via `node.config`.

### ADR-4: Suggestion generation stays in orchestrator

**Decision:** `generate_suggestions()` is NOT wrapped in a node. It runs in `_evaluate_candidates()` after the L1EvaluateNode returns.

**Rationale:** Suggestions require accumulated round history (`state.rounds`) and the campaign config — both orchestrator-level state. Making this a node would require threading the entire history through the node interface, which adds complexity without tracing benefit (suggestions are a secondary output).

### ADR-5: `_node_obs_type()` override pattern

**Decision:** `NodeBase._node_obs_type()` returns `"generation"` by default. `L1EvaluateNode` overrides to return `"span"`.

**Rationale:** Most nodes (L1GenerateNode, L2RefineNode, L3ModifyPlanNode) make a single LLM call — a Langfuse `generation` observation. L1EvaluateNode is a composite operation with nested children (N candidate evaluations + optional critique) — a Langfuse `span` that contains child observations.

| Node | `_node_obs_type()` | Why |
|------|--------------------|-----|
| `L1GenerateNode` | `"generation"` | Single `generate_candidates()` LLM call |
| `L1EvaluateNode` | `"span"` | N eval calls + optional critique agent |
| `L2RefineNode` | `"generation"` | Single `refine_context()` LLM call |
| `L3ModifyPlanNode` | `"generation"` | Single `modify_plan()` LLM call |

### ADR-6: OptSearchPoint as cross-reference, not container

**Decision:** `OptSearchPoint` holds optimizer config state + `content_hashes` linking to target-layer `dataset_runs`. It does NOT embed or contain target evaluation data.

**Rationale:** Target data (dataset_runs, SearchPoints) stays clean in the shared `dataset_runs/` store with content-addressed dedup. All optimizer provenance lives in the optimizer layer (trial JSON). L4 meta-optimization correlates `OptSearchPoint.parameters` with `dataset_run.accuracy` via `content_hashes` join — no data duplication.

### ADR-7: PromptState.compile() double-brace syntax

**Decision:** Optimizer prompt templates use `{{variable}}` syntax (double braces), rendered via `PromptState.compile(**kwargs)`.

**Rationale:** Single `{braces}` conflict with JSON examples in prompt text. Double braces are Python `.format()`-escaped for literal braces, then `.compile()` does a second pass replacing `{{var}}` → value. This keeps templates readable as both raw text and PromptState JSON.

---

## 3. Node I/O Contracts

All node classes live in `api/nodes/optimizer_nodes.py` and extend `NodeBase[TInput, TOutput]` from `api/nodes/base.py`.

### NodeBase

Template Method pattern with Pydantic generics. Key interface:

```python
class NodeBase(ABC, Generic[TInput, TOutput]):
    def __init__(self, node_id: str, config: dict | None = None,
                 *, obs: ObsLogger | None = None, trace_id: str | None = None)

    async def process(self, input_data: TInput | dict) -> TOutput  # public entry
    async def _execute(self, input_data: TInput) -> TOutput         # override this

    def _node_obs_type(self) -> str          # "generation" (default) or "span"
    def _start_observation(self, ...) -> str  # auto-called by process()
    def _end_observation(self, ...)           # auto-called by process()
```

`process()` handles: input validation → obs start → `_execute()` → output validation → metrics → obs end. Observability is opt-in (pass `obs` + `trace_id`).

### NodeMetrics

```python
class NodeMetrics(BaseModel):
    node_id: str
    node_type: str          # class name
    start_time: str         # ISO timestamp
    end_time: str
    duration_ms: float
    input_tokens: int | None
    output_tokens: int | None
    model: str | None
    error: str | None
    metadata: dict[str, Any]
```

### InitNode (removed)

InitNode was removed in the M7 v2 audit. Baseline decomposition is now handled by `decompose_task_context()` at campaign init time, before the feedback cycle starts. The `task_context` dict produced by decomposition flows to `OptSearchPoint` and is used by L1 Generate and L2 Refine. See §6.1 for details.

### L1GenerateNode

Generates N candidate PromptState variants via LLM meta-prompt.

**Config:** `model`, `provider`, `n_variants` (default: 5), `creativity` (default: 0.7)

```
L1GenerateInput:
    prompt_state: dict        # Current best PromptState (serialized)
    accuracy: float           # Current accuracy (0.0-1.0)
    results: list             # Previous eval results for failure analysis (default: [])
    critique_text: str        # Critique from previous round (default: "")
    thinking_styles: list[str]  # Sampled mutation guidance (default: [])
    scan_context: dict | None # Pipeline-aware context from scan analytics (default: None)

L1GenerateOutput:
    candidates: list[dict]    # Candidate PromptStates (serialized)
    n_generated: int          # Number of candidates generated
```

### L1EvaluateNode

Evaluates candidates via backend, selects winner, runs critique.

**Config:** `eval_ctx` (EvalContext, required), `improvement_threshold` (default: 0.01), `on_candidate_eval` (callback), `on_query_eval` (callback), `model`, `provider`, `enable_critique` (default: false), `critique_positive_threshold` (default: 0.7), `thinking_styles_seed` (default: 42)

**`_node_obs_type()` → `"span"`** (composite operation)

```
L1EvaluateInput:
    candidates: list[dict]    # Candidate PromptStates (serialized)
    eval_data: list           # Evaluation query dicts
    current_best: dict        # {accuracy, prompt_state, results, label}

L1EvaluateOutput:
    winner: dict              # Winner round entry dict
    winner_prompt_state: dict # Winner PromptState (serialized)
    winner_accuracy: float
    improved: bool
    next_action: str          # "generate" or "stop" (default: "generate")
    candidate_scores: list[dict]
    winner_results: list[dict]
    critique_text: str        # Critique for next round (default: "")
    thinking_styles: list[str]  # Sampled styles for next round (default: [])
    winner_composite: float   # Composite score (default: 0.0)
    winner_pipeline_params: dict | None  # Pipeline params from winner (default: None)
```

### L2RefineNode

L2 refine_context transition: adjust parameters and context.

**Config:** `model`, `provider`, `temperature` (default: 0.3), `pipeline_schema`

```
L2RefineInput:
    prompt_state: dict        # Current PromptState (serialized)
    stalled_rounds: list[dict]  # Recent stalled round summaries
    eval_data: list[dict]     # Evaluation query dicts
    pipeline_params: dict | None  # Current pipeline parameters (default: None)

L2RefineOutput:
    prompt_state: dict        # New PromptState (serialized)
    pipeline_params: dict | None  # Updated pipeline params (default: None)
    changes_description: str  # Description of changes (default: "")
```

### L3ModifyPlanNode

L3 modify_plan transition: change strategic optimization plan.

**Config:** `model`, `provider`, `temperature` (default: 0.5), `pipeline_schema`

```
L3ModifyPlanInput:
    prompt_state: dict        # Current PromptState (serialized)
    l2_history: list[dict]    # L2 adjustment history summaries
    eval_data: list[dict]     # Evaluation query dicts
    pipeline_params: dict | None  # Current pipeline parameters (default: None)

L3ModifyPlanOutput:
    prompt_state: dict        # New PromptState (serialized)
    pipeline_params: dict | None  # Updated pipeline params (default: None)
    changes_description: str  # Description of changes (default: "")
```

---

## 4. Orchestrator Flow

### State Machine

```
                    ┌──────────────────────────┐
                    │  Campaign Init           │  (decompose_task_context + restructure)
                    └──────────┬───────────────┘
                               │ baseline PromptState + task_context
                               ▼
             ┌──── Resume Detection ────┐
             │  CampaignStore lookup    │
             │  Obs setup               │
             │  EvalContext build        │
             │  Bootstrap critique      │
             └──────────┬───────────────┘
                        │
    ┌───────────────────▼────────────────────┐
    │            Round Loop                  │
    │  ┌─────────────────────────────────┐   │
    │  │ L1GenerateNode                  │   │
    │  │  (or load from disk if resume)  │   │
    │  └──────────────┬──────────────────┘   │
    │                 │ candidates            │
    │  ┌──────────────▼──────────────────┐   │
    │  │ L1EvaluateNode                  │   │
    │  │  eval → winner → critique       │   │
    │  │  → thinking_styles              │   │
    │  └──────────────┬──────────────────┘   │
    │                 │ round_result          │
    │  ┌──────────────▼──────────────────┐   │
    │  │ State Update                    │   │
    │  │  - Update current_sp, accuracy  │   │
    │  │  - Track best_sp               │   │
    │  │  - Increment stall_count       │   │
    │  │  - Checkpoint OptSearchPoint    │   │
    │  └──────────────┬──────────────────┘   │
    │                 │                      │
    │  ┌──────────────▼──────────────────┐   │
    │  │ Stopping Conditions             │   │
    │  │  ✓ perfect_score (acc >= 1.0)   │   │
    │  │  ✓ next_action_stop             │   │
    │  │  ✓ patience_exhausted           │   │
    │  │  ✓ max_rounds                   │   │
    │  │  ✓ l2/l3_patience_exhausted     │   │
    │  └──────────────┬──────────────────┘   │
    │                 │ stall_count >= patience│
    │  ┌──────────────▼──────────────────┐   │
    │  │ Escalation (if enable_l2)       │   │
    │  │  L2RefineNode → reset stall     │   │
    │  │    └─ if L2 stalls:             │   │
    │  │       L3ModifyPlanNode          │   │
    │  │       → reset L2 + L1 stall     │   │
    │  └─────────────────────────────────┘   │
    └────────────────────────────────────────┘
                        │
                        ▼
                   Finalize
                   (CampaignStore, Obs, CycleResult)
```

### `_LoopState` threading

```python
@dataclass
class _LoopState:
    rounds: list[CycleRoundResult]    # accumulated round results
    current_sp: SearchPoint | None    # current prompt + pipeline_params bundle
    current_accuracy: float
    current_composite: float
    current_results: list[dict]       # winner results for failure analysis
    best_accuracy: float
    best_composite: float
    best_round: int
    best_sp: SearchPoint | None       # overall best
    stall_count: int                  # consecutive non-improving rounds

    # Critique + thinking styles (fed forward between rounds)
    critique_text: str
    thinking_styles: list[str]

    # L2/L3 escalation tracking
    l2_stall_count: int
    l3_stall_count: int
    l2_round: int
    l3_round: int
    best_accuracy_at_l2_entry: float
    best_accuracy_at_l3_entry: float
    best_composite_at_l2_entry: float
    best_composite_at_l3_entry: float

    eval_ctx: EvalContext | None      # shared across all rounds
```

### Round execution flow

Each round in `_execute_round()`:

1. Resolve L2 meta-param overrides from `OptSearchPoint.optimizer_params` (n_variants, creativity)
2. `_generate_or_load_candidates()` — check disk first, then `L1GenerateNode.process()`
3. `_evaluate_candidates()` — `L1EvaluateNode.process()` + optional suggestions
4. Pop critique_text/thinking_styles from eval output → store in `_LoopState`
5. Build `CycleRoundResult`
6. Log round to ObsLogger (round_end + prompt_version)

### Escalation routing

```
stall_count >= patience
    └─ enable_l2?
        ├─ YES → _escalate_l2()
        │        └─ l2_improved?
        │            ├─ YES → l2_stall_count = 0, continue L1
        │            └─ NO  → l2_stall_count++
        │                     └─ l2_stall_count >= l2_patience?
        │                         ├─ enable_l3?
        │                         │   ├─ YES → L3ModifyPlanNode
        │                         │   │        l3_stall_count check → continue or stop
        │                         │   └─ NO  → stop_reason="l2_patience_exhausted"
        │                         └─ continue L1
        └─ NO  → stop_reason="patience_exhausted"
```

L2 transition: resets L1 stall_count, increments l2_round, snapshots best_accuracy_at_l2_entry.
L3 transition: resets L2 stall_count + l2_round + L1 stall_count, increments l3_round.

---

## 5. Prompt Template Inventory

All templates are `PromptState` JSON with 5 Layer 1 fields (`persona`, `task_intent`, `instruction`, `thinking_style`, `answer_format`). Placeholders use `{{variable}}` syntax.

| Template | Variables | Consumer |
|----------|-----------|----------|
| `meta_freeform.json` | `n_variants`, `accuracy_pct`, `n_queries`, `rendered_prompt`, `failure_examples` | `generate_candidates()` — default generation mode |
| `meta_constrained.json` | `n_variants`, `accuracy_pct`, `n_queries`, `rendered_prompt`, `failure_examples`, `library_desc`, `response_schema` | `generate_candidates()` — constrained by variant library |
| `meta_scan_aware.json` | `n_variants`, `accuracy_pct`, `n_queries`, `rendered_prompt`, `failure_examples`, `leaderboard_text`, `sensitivity_text`, `difficulty_text`, `tested_values`, `focus_note`, `instruction_spec` | `generate_candidates()` — uses scan analytics |
| `critique_negative.json` | `accuracy_pct`, `n_failures`, `max_examples`, `failure_lines` | `CritiqueAgent._negative_critique()` — below threshold |
| `critique_positive.json` | `accuracy_pct`, `n_successes`, `max_examples`, `success_lines`, `n_failures`, `failure_lines` | `CritiqueAgent._positive_critique()` — above threshold |
| `l2_refine_context.json` | `round_summary`, `rendered_prompt`, `failure_lines`, `current_params`, `current_context`, `pipeline_section`, `response_schema_suffix` | `refine_context()` in `layer_transitions.py` |
| `l3_modify_plan.json` | `current_plan`, `l2_summary`, `rendered_prompt`, `pipeline_section`, `response_schema_suffix` | `modify_plan()` in `layer_transitions.py` |
| `suggestions.json` | `history_lines`, `accuracy_pct`, `rendered_prompt`, `campaign_config`, `n_failures`, `n_queries`, `failure_detail` | `generate_suggestions()` — post-round analysis |
| `restructure.json` | `consultation_instruction` | `restructure_context()` — campaign init decomposition |

**Loading:** `load_optimizer_prompt(name)` in `api/config/optimizer_prompt_loader.py`. Resolution order: Langfuse prompt registry (opt-in, by `production` label, SDK-cached) → local JSON in `api/config/optimizer_prompts/`. LRU-cached for local files. Push via `push_all_to_langfuse()`.

---

## 6. OptSearchPoint & L4 Path

### Model

```python
class OptSearchPoint(BaseModel):
    """Optimizer-level search point — the optimizer's configuration at a moment."""
    critique_text: str = ""
    thinking_styles: list[str] = Field(default_factory=list)
    plan: str = ""
    optimizer_params: dict = Field(default_factory=dict)
    task_context: dict = Field(
        default_factory=dict,
        description="Structured domain context (domain, pipeline_purpose, "
        "data_characteristics, optimization_goals, key_challenges). "
        "Set from TASK_DESCRIPTION decomposition, refinable by L2.",
    )
    content_hashes: list[str] = Field(
        default_factory=list,
        description="Content hashes of dataset_runs produced under this config",
    )
```

### Persistence

Checkpointed in trial JSON after each round:

```json
{
  "trial_id": "round_0",
  "round": 0,
  "accuracy": 0.67,
  "opt_search_point": {
    "critique_text": "...",
    "thinking_styles": ["analytical", "comparative", "systematic"],
    "plan": "",
    "optimizer_params": {},
    "task_context": {
      "domain": "Life Cycle Assessment",
      "pipeline_purpose": "Normalize terminology...",
      "data_characteristics": "...",
      "optimization_goals": "...",
      "key_challenges": "..."
    },
    "content_hashes": []
  }
}
```

### L4 path

L4 meta-optimization searches over `OptSearchPoint`s the same way L1-L3 search over `SearchPoint`s. The join key is `content_hashes` — linking optimizer config to target-layer evaluation outcomes without duplicating data. *Design note: the recursive closure (L4 — optimizing the optimizer's own prompts) was recognized from inception as an inherent property of the architecture.*

### Task context decomposition

`decompose_task_context()` (in `notebooks/_campaign_lib/_setup.py`) runs at campaign init, before the feedback cycle. It calls `restructure_context_cached()` with `TASK_DESCRIPTION` to produce a structured domain context dict.

**Fields:**

| Field | Description |
|-------|-------------|
| `domain` | Domain of the backend pipeline (e.g. "Life Cycle Assessment") |
| `pipeline_purpose` | What the pipeline does (e.g. "Normalize terminology...") |
| `data_characteristics` | Nature of the input data |
| `optimization_goals` | What success looks like |
| `key_challenges` | Known difficulties |
| `raw_description` | Original `TASK_DESCRIPTION` text |

**Flow:**

1. Campaign init → `decompose_task_context()` → structured dict
2. Stored on `OptSearchPoint.task_context`
3. Flows to L1 candidate generation meta-prompt (via `scan_context` or direct injection)
4. L2 `refine_context()` can update `task_context` fields when escalation fires
5. `PromptState.context` auto-synced from `task_context` — one source of truth

---

## 7. Tracing Architecture

### NodeBase hooks

Every node's `process()` call:

1. `_start_observation(input_data)` → calls `obs.log_node_step_start()` → returns `obs_id`
2. `_execute(input_data)` → node logic
3. `_end_observation(obs_id, output_data, error)` → calls `obs.log_node_step_end()`

Tracing is opt-in: only active when `obs` + `trace_id` are passed at construction. When omitted, steps 1 and 3 are silently skipped.

### Nesting hierarchy

```
Campaign trace (feedback_cycle)
├── round_0 (span)
│   ├── l1_generate_r0 (generation)    ← L1GenerateNode
│   ├── l1_evaluate_r0 (span)          ← L1EvaluateNode
│   │   ├── eval_abc123 (tool)         ← per-candidate dataset_run
│   │   ├── eval_def456 (tool)
│   │   └── [critique] (generation)    ← CritiqueAgent (if enabled)
│   └── prompt_version (tool)          ← winner prompt registration
├── round_1 (span)
│   ├── l1_generate_r1 (generation)
│   ├── l1_evaluate_r1 (span)
│   └── prompt_version (tool)
└── [l2_refine_r2] (generation)        ← if escalation triggered
```

### Dual-write (file + cloud)

`ObsLogger` writes to disk first, then delegates to `CloudObsBackend`:

- **File:** `obs/langfuse/observations/{trace_id}/{obs_id}.json` — created at start, updated with output/metrics at end
- **Cloud:** `CloudObsBackend.on_node_step_start()` → `lf.start_span()`, `on_node_step_end()` → `lf.end_observation()`
- **Events:** `events.jsonl` gets `node_step_start` and `node_step_end` entries

Cloud failures never crash the main flow — circuit breaker trips on first error.

---

## 8. CycleConfig Reference

### Core optimization

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_rounds` | int | 10 | Maximum optimization rounds |
| `patience` | int | 3 | Stop after N consecutive non-improvements |
| `n_variants` | int | 5 | Candidates per round |
| `creativity` | float | 0.7 | Temperature for candidate generation |
| `improvement_threshold` | float | 0.01 | Min accuracy delta to accept |

### LLM

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | str \| None | None | LLM model identifier |
| `provider` | str \| None | None | "groq", "openai", "anthropic" |

### Backend & infrastructure

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `backend_url` | str | (required) | Backend URL for evaluation |
| `backend_id` | str | "" | Backend identifier for caching |
| `project_root` | str | "" | Project root for store |
| `session_terms` | list[str] \| None | None | Backend session terms |
| `temperature` | float | 0.0 | Temperature for content hash |

### Evaluation

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `sample_size` | int | 0 | Subsample size (0 = use all) |
| `seed` | int | 42 | Random seed for subsampling |
| `pipeline_schema` | PipelineSchema \| None | None | Pipeline schema for eval |
| `pipeline_params` | dict \| None | None | Pipeline parameter overrides |

### Critique

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enable_critique` | bool | True | Enable critique agent between rounds |
| `critique_positive_threshold` | float | 0.7 | Accuracy threshold for positive vs negative critique |

### Scan-aware optimization

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `scan_context` | dict \| None | None | Scan analytics context for candidate gen |

### L2/L3 escalation

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enable_l2` | bool | False | Enable L2 refine_context loop |
| `enable_l3` | bool | False | Enable L3 modify_plan loop |
| `l2_patience` | int | 2 | L2 stalls before escalating to L3 |
| `l3_patience` | int | 1 | L3 stalls before stopping |
| `l2_temperature` | float | 0.3 | Temperature for L2 LLM call |
| `l3_temperature` | float | 0.5 | Temperature for L3 LLM call |
| `suggestion_temperature` | float | 0.0 | Temperature for suggestion generation |

### Misc

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `generate_suggestions` | bool | False | LLM suggestions each round |

---

## 9. Jupyter Kernel Hang: Root Cause & Recommendations

The v1 implementation has an unresolvable Jupyter kernel hang bug: cell interrupt (Ctrl+C) hangs the kernel on Windows. This section documents root causes and recommendations for v2.

### Root causes

**RC-1: httpx async client `aclose()`** — When `KeyboardInterrupt` fires during an active httpx request, the async client's `__aexit__` calls `aclose()` which tries to gracefully close HTTP/2 connections. On Windows, this blocks indefinitely because the event loop is already corrupted by the interrupt.

**RC-2: Langfuse SDK thread join** — `langfuse.shutdown()` calls `thread.join()` on its background flush thread. If the thread is mid-HTTP-request when interrupt fires, the join blocks indefinitely. The `CloudObsBackend.shutdown()` workaround (ThreadPoolExecutor with 5s timeout) partially mitigates this but doesn't prevent the root hang.

**RC-3: asyncio corruption on Windows** — `KeyboardInterrupt` during `await` corrupts the `ProactorEventLoop` state. Subsequent async operations (including cleanup) hang. This is a known CPython issue on Windows — the event loop's internal `_poll()` method doesn't handle interrupts cleanly.

**RC-4: Langfuse SDK lazy Langfuse init in hot path** — `LangfuseLogger.get_instance()` is called repeatedly. If Langfuse SDK is initializing its background threads when interrupt fires, thread state becomes inconsistent.

### Root cause found (v2 bisect)

**RC-5: `self.config.copy()` in `NodeBase.process()` finally block.**

When `L1EvaluateNode` is interrupted mid-httpx-request, Python runs the `finally` block in `process()`. The original code did `metadata=self.config.copy()` to capture config in `NodeMetrics`. For L1EvaluateNode, `self.config` contains `eval_ctx` — an `EvalContext` holding a `BackendClient` with a live httpx `AsyncClient`. Copying this object on a corrupted event loop triggers httpx internals (connection pool inspection, `__repr__`, or `__deepcopy__`) that hang indefinitely.

**Why L1GenerateNode didn't hang:** Its config is plain primitives (`{"model": "...", "n_variants": 5}`) — no live I/O objects.

**Fix (applied):** `NodeMetrics.metadata` filters to JSON-safe primitives only:
```python
safe_meta = {
    k: v for k, v in self.config.items()
    if isinstance(v, (str, int, float, bool, list, type(None)))
}
```

This is a permanent design rule, not a workaround: metrics metadata must never reference live infrastructure objects. The full config remains on `self.config` for runtime use.

### Failed fixes (v1)

| Fix attempted | Why it failed |
|---------------|---------------|
| `asyncio.shield()` around eval calls | Shield doesn't protect against `CancelledError` propagation in httpx internals |
| `signal.signal(SIGINT, ...)` handler | Jupyter captures SIGINT before user handlers; doesn't reach our code |
| `concurrent.futures.ThreadPoolExecutor` timeout on flush/shutdown | Mitigates Langfuse hang but not httpx hang |
| `obs.shutdown()` in `finally` block | The shutdown itself hangs because Langfuse thread is stuck |
| `asyncio.get_event_loop().stop()` | Can't call from within a hung event loop |
| Skip `_end_observation()` on `KeyboardInterrupt` flag | Wrong target — the hang was in `self.config.copy()`, not obs cleanup |

### Additional recommendations

**R-1: No live objects in node config metadata** — ENFORCED. `NodeMetrics.metadata` uses primitive filter. This prevents future regressions if new nodes pass complex objects via config.

**R-2: Daemon threads for Langfuse SDK** — Ensure all Langfuse-spawned threads are daemon threads so they don't prevent interpreter exit on interrupt.

**R-3: No `asyncio.shield` in eval path** — Shield creates more problems than it solves in interrupt scenarios. Let `KeyboardInterrupt` propagate naturally and rely on checkpointing for resume.

**R-4: Lazy Langfuse init** — Initialize `LangfuseLogger` once at campaign start, not on-demand. Store the initialized instance in `EvalContext` or `_LoopState`. This avoids thread initialization races during interrupt.

---

## 10. Phased Execution Plan — Strangler Fig Migration

Each wave swaps ONE piece while the system stays working. `feedback_cycle.py` (highest risk, most dependencies) is touched LAST. Tests pass after every wave.

### Wave A — Leaf modules (zero risk to feedback_cycle.py)

#### A1: Prompt templates + `optimizer_prompt_loader.py`

**Files added:**
- `api/config/optimizer_prompts/*.json` (9 template files)
- `api/config/optimizer_prompt_loader.py`

**What it does:** Creates the 9 optimizer prompt templates as PromptState JSON files. Implements `load_optimizer_prompt()`, `push_all_to_langfuse()`, `list_optimizer_prompts()` with LRU cache and Langfuse fallback.

**Verify:** `from api.config.optimizer_prompt_loader import load_optimizer_prompt; ps = load_optimizer_prompt("critique_negative"); assert ps.render()`

**Rollback:** Delete the 2 new paths. No existing code references them.

#### A2: `OptSearchPoint` model

**Files added:**
- `api/models/opt_search_point.py`

**What it does:** Defines `OptSearchPoint` Pydantic model with 6 fields: `critique_text`, `thinking_styles`, `plan`, `optimizer_params`, `task_context`, `content_hashes`.

**Verify:** `from api.models.opt_search_point import OptSearchPoint; osp = OptSearchPoint(critique_text="test"); assert osp.content_hashes == []`

**Rollback:** Delete file. No existing code references it.

#### A3: `NodeBase` in `base.py`

**Files added:**
- `api/nodes/base.py`

**What it does:** Abstract base class with Template Method pattern. Typed I/O via Pydantic generics. Opt-in observability hooks (`_start_observation`, `_end_observation`). `NodeMetrics` model.

**Verify:** Import succeeds. Create a trivial subclass, call `process()`, check `get_last_metrics()`.

⚠️ **WARNING:** `api/nodes/__init__.py` may need updating for node registration. Check existing `__init__.py` structure before adding.

**Rollback:** Delete `base.py`. No existing code depends on it.

#### A4: Node I/O Pydantic models only (no logic)

**Files added:**
- `api/nodes/optimizer_nodes.py` (with all 10 Input/Output models, but node classes can be stubs)

**What it does:** Defines `InitNodeInput`, `InitNodeOutput`, `L1GenerateInput`, `L1GenerateOutput`, `L1EvaluateInput`, `L1EvaluateOutput`, `L2RefineInput`, `L2RefineOutput`, `L3ModifyPlanInput`, `L3ModifyPlanOutput`.

**Verify:** All 10 models importable and constructible with valid data.

**Rollback:** Delete file.

### Wave B — Node implementations (still no feedback_cycle.py changes)

#### B1: `InitNode` implementation — REMOVED

InitNode was removed in the M7 v2 audit. Decomposition now happens at campaign init via `decompose_task_context()`.

#### B2: `L1GenerateNode` implementation

**What it does:** Wraps `generate_candidates()`. Config: `model`, `provider`, `n_variants`, `creativity`.

**Verify:** `test_grow_filter_node`, `test_l1_generate_forwards_critique` pass.

#### B3: `L1EvaluateNode` implementation

**What it does:** Wraps `evaluate_and_select_winner()` + optional `CritiqueAgent.run()` + `sample_thinking_styles()`. Override: `_node_obs_type()` → `"span"`.

**Verify:** `test_l1_evaluate_node`, `test_node_obs_types` pass.

#### B4: `L2RefineNode` + `L3ModifyPlanNode`

**What it does:** Wrap `refine_context()` and `modify_plan()` respectively.

**Verify:** `test_l2_refine_node`, `test_l3_modify_plan_node` pass.

#### B5: Node tests

**Files added:**
- `tests/test_optimizer_nodes.py`

**What it does:** Tests for all 4 nodes: registration, I/O contracts, critique forwarding, obs type, step tracing (opt-in), error tracing.

⚠️ **WARNING:** Node tests require mocking `restructure_context`, `generate_candidates`, `evaluate_and_select_winner`, `refine_context`, `modify_plan`. Use `monkeypatch` on the import paths inside the node modules (e.g., `"api.services.search.context.restructure_context"`).

**Verify:** `pytest tests/test_optimizer_nodes.py -v` — all pass.

**Rollback:** Delete test file + `optimizer_nodes.py` node classes. Wave A models remain valid.

### Wave C — Observability plumbing (still no feedback_cycle.py changes)

#### C1: `ObsLogger` node step methods

**Files modified:**
- `api/services/obs/observability_logger.py` — add `log_node_step_start()`, `log_node_step_end()`

**What it does:**
- `log_node_step_start(trace_id, node_id, node_type, obs_type, input_data, metadata)` → returns `obs_id`
- `log_node_step_end(obs_id, trace_id, node_id, output_data, metrics, error)` → updates file observation

⚠️ **WARNING:** These are NEW methods on an existing class. Ensure no signature conflicts with existing methods. The methods follow the same pattern as `log_round_start`/`log_round_end`.

**Verify:** Unit test with mock ObsLogger — call start/end, verify file written.

**Rollback:** Remove the two new methods. No existing code calls them yet.

#### C2: `CloudObsBackend` node step support

**Files modified:**
- `api/services/obs/cloud_backend.py` — add `on_node_step_start()`, `on_node_step_end()`

**What it does:**
- `on_node_step_start()` → `lf.start_span()` nested under active round, stores obs_id in `_active_step_obs_ids[node_id]`
- `on_node_step_end()` → pops obs_id, calls `lf.end_observation()`

**Verify:** Unit test with mock Langfuse client.

**Rollback:** Remove the two new methods.

#### C3: Wire NodeBase tracing hooks to ObsLogger

**What it does:** NodeBase's `_start_observation()` calls `self.obs.log_node_step_start(...)` and `_end_observation()` calls `self.obs.log_node_step_end(...)`. This is already in the NodeBase code from Wave A3, but now ObsLogger actually has the methods.

**Verify:** `test_node_tracing_opt_in`, `test_node_tracing_on_error` pass (from B5 tests, now with real ObsLogger mock).

**Rollback:** Tracing gracefully degrades — if obs methods are missing, the warning is logged and nodes work identically.

### Wave D — Orchestrator migration (feedback_cycle.py — controlled swap)

This is the highest-risk wave. Each sub-wave modifies ONE call site in `feedback_cycle.py`. Run the full test suite after each.

#### D1: Sync httpx client for notebook path

**Files modified:**
- `api/services/backend_client.py` — add sync `match_sync()` method alongside async `match()`

**What it does:** Fixes the kernel hang root cause (RC-1) by providing a sync HTTP path. Uses `httpx.Client` (sync) instead of `httpx.AsyncClient`. `BackendClient` gains a `_sync_client` property.

⚠️ **WARNING:** This changes `BackendClient` which is used by ALL eval paths (grid search, smart search, feedback cycle). The sync method is additive — existing async `match()` is untouched.

**Verify:** `pytest -v --tb=short` — all existing tests pass. Manual test in notebook: interrupt during eval no longer hangs kernel.

**Rollback:** Remove `match_sync()` and `_sync_client`. Existing code unchanged.

#### D2: Swap L1Generate call site → `L1GenerateNode.process()`

**Files modified:**
- `api/services/campaign/feedback_cycle.py` — replace direct `generate_candidates()` call in `_generate_or_load_candidates()` with `L1GenerateNode` instantiation + `.process()`

**What changes:**
```python
# Before:
candidates = await generate_candidates(current_ps, ...)

# After:
l1_gen_node = L1GenerateNode(
    node_id=f"l1_generate_r{round_num}",
    config={"model": config.model, "provider": config.provider,
            "n_variants": _n_variants, "creativity": _creativity},
    obs=obs, trace_id=trace_id,
)
gen_result = await l1_gen_node.process({...})
candidates = gen_result.candidates
```

⚠️ **WARNING:** The `generate_candidates()` import can be removed from feedback_cycle.py. The node handles it internally via lazy import. Verify that `critique_text`, `thinking_styles`, and `scan_context` are correctly forwarded.

**Verify:** `pytest tests/test_feedback_cycle.py -v` — all pass. Check `test_results_tracked_across_rounds` specifically (results flow).

**Rollback:** Revert `_generate_or_load_candidates()` to direct `generate_candidates()` call.

#### D3: Swap L1Evaluate call site → `L1EvaluateNode.process()`

**Files modified:**
- `api/services/campaign/feedback_cycle.py` — replace direct `evaluate_and_select_winner()` call in `_evaluate_candidates()` with `L1EvaluateNode` instantiation + `.process()`

**What changes:**
- `L1EvaluateNode` receives `eval_ctx` via config, not positional arg
- Output includes `critique_text` and `thinking_styles` — orchestrator pops them into `_LoopState`
- `enable_critique` flag forwarded from `CycleConfig`

⚠️ **WARNING:** The `evaluate_and_select_winner()` import remains needed by `L1EvaluateNode._execute()` internally. Don't remove it from the module yet. Verify that `thinking_styles_seed` is correctly computed as `config.seed + round_num + 1`.

**Verify:** `pytest tests/test_feedback_cycle.py -v` — all pass. Check `test_on_phase_callback` (phase events must still fire).

**Rollback:** Revert `_evaluate_candidates()` to direct `evaluate_and_select_winner()` call.

#### D4: Swap L2/L3 escalation → `L2RefineNode`/`L3ModifyPlanNode`

**Files modified:**
- `api/services/campaign/feedback_cycle.py` — replace direct `refine_context()`/`modify_plan()` calls in `_do_l2_transition()` and `_do_l3_transition()` with node instantiation

**What changes:**
```python
# Before (in _do_l2_transition):
tr = await refine_context(ps, stalled_rounds, eval_data, client, ...)

# After:
l2_node = L2RefineNode(
    node_id=f"l2_refine_r{round_num}",
    config={"model": config.model, "provider": config.provider,
            "temperature": config.l2_temperature,
            "pipeline_schema": config.pipeline_schema},
    obs=obs, trace_id=trace_id,
)
l2_result = await l2_node.process({...})
tr = TransitionResult(prompt_state=PromptState(**l2_result.prompt_state), ...)
```

⚠️ **WARNING:** L2/L3 nodes receive `pipeline_schema` via config (not input), because it's session-stable. The `TransitionResult` reconstruction from node output adds a conversion step.

**Verify:** L2/L3 escalation tests (if any). Run full suite: `pytest -v --tb=short`.

**Rollback:** Revert `_do_l2_transition()` and `_do_l3_transition()`.

#### D5: Add OptSearchPoint checkpointing to round loop

**Files modified:**
- `api/services/campaign/feedback_cycle.py` — after each round, build `OptSearchPoint` from `_LoopState` and include in trial checkpoint

**What changes:**
```python
opt_sp = OptSearchPoint(
    critique_text=state.critique_text,
    thinking_styles=state.thinking_styles,
    plan=state.current_sp.prompt_state.plan,
    optimizer_params={},  # populated from CycleConfig meta-settings
    task_context=state.task_context,
)
campaign_store.add_trial(config.backend_id, cycle_id, {
    ...,
    "opt_search_point": opt_sp.model_dump(),
})
```

**Verify:** Run a cycle with `project_root` set, inspect trial JSON for `opt_search_point` key.

**Rollback:** Remove the `OptSearchPoint` construction + `opt_search_point` key from trial dict.

#### D6: Verify `cycle_config_identity()` (baseline_rendered stays in hash)

**Files modified:**
- `api/services/campaign/feedback_cycle.py` — verified `cycle_config_identity()` includes `baseline_rendered`

**What it does:** Verified: `baseline_rendered` stays in hash (campaign continuity). Removing it would orphan existing campaign data by changing cycle_id. See ADR-2.

**Verify:** `TestCycleConfigIdentity` — all 3 tests pass.

### Wave E — New capabilities (after full migration)

#### E1: EscalationCheck framework — IMPLEMENTED

**Files:**
- `api/services/campaign/escalation.py` — `EscalationCheck`, `EscalationSignal`, `DegradationCheck`

**Status:** Fully implemented with 7 passing tests. Pluggable mid-evaluation escalation. `EscalationCheck.evaluate()` runs after each candidate eval. `DegradationCheck` fires when degraded queries exceed threshold.

```python
@dataclass
class EscalationSignal:
    check_name: str         # "degradation", "error_rate", etc.
    target: str             # "l3" | "l2" | "abort"
    context: dict           # check-specific data
    candidate_idx: int
    candidates_evaluated: int
    candidates_skipped: int

class EscalationCheck(BaseModel):
    name: str
    target: str = "l3"
    enabled: bool = True
    def evaluate(self, scores, candidate_idx, n_total) -> EscalationSignal | None: ...

class DegradationCheck(EscalationCheck):
    name: str = "degradation"
    threshold: float = 0.3
```

#### E2: OPTIMIZER_PIPELINE_SCHEMA — REPLACED BY BUILDING BLOCK APPROACH

The original plan to model the optimizer pipeline as a `PipelineSchema`/`PipelineStep` instance has been replaced by the building block standard. Instead of reusing `PipelineSchema` (which is tightly coupled to target backend pipeline semantics), the optimizer declares its nodes in `api/config/optimizer_pipeline.json` using the same JSON format as TermNorm's `GET /pipeline` but with building block type annotations (`llm/meta`, `agent`, `evaluation`). See §14 and [`docs/building-blocks.md`](../building-blocks.md).

#### E3: End-to-end Langfuse tracing

**What it does:** Wire all nodes through the tracing hooks. Campaign trace → round spans → node observations → dataset_run tools. The nesting hierarchy from Section 7 is fully realized.

**Verify:** Run a cycle with Langfuse credentials, check trace in Langfuse UI.

---

## 11. Testing Strategy

### Mock patterns

- **No pytest-mock plugin** — use `monkeypatch` for async service mocking, `unittest.mock.MagicMock` when needed
- **Test helpers** in `tests/_helpers.py`: `apply_init_mock`, `apply_llm_mock`, `apply_grow_mock`, `apply_eval_mock`, `apply_critique_mock`
- **`apply_eval_mock(monkeypatch, round_hits=[1, 2, 3])`** — cycles through hit counts per round

### Key fixtures

| Fixture | Description |
|---------|-------------|
| `eval_data` | Standard 3-query dataset: aspirin, ibuprofen, acetaminophen |
| `cycle_config` | Standard CycleConfig: max_rounds=5, patience=2, n_variants=3 |
| `baseline_ps` | PromptState with instruction + persona + thinking_style |
| `baseline_results` | 3-element results (2 hits, 1 miss) |

### Test ordering constraint

**`test_interrupt_writes_interrupted_status` MUST be last in `test_feedback_cycle.py`** — `KeyboardInterrupt` inside asyncio corrupts the event loop on Windows, causing subsequent async tests to hang.

### Node tests

- **Registration:** All 4 nodes discoverable via `get_node_class()`
- **I/O contracts:** Process with valid input → correct output types
- **Critique forwarding:** `L1GenerateNode` forwards `critique_text`, `thinking_styles`, `scan_context`
- **Obs type:** Each node returns correct `_node_obs_type()`
- **Step tracing:** When `obs` + `trace_id` set, `log_node_step_start/end` called
- **Error tracing:** When `_execute` raises, `log_node_step_end` called with error

### Feedback cycle tests

- **Multi-round improvement:** Hits improve 1→2→3, stops at perfect_score
- **Patience exhaustion:** All 0% hits, stops after patience rounds
- **Max rounds:** Slow improvement, stops at max_rounds
- **next_action stop:** Analysis signals stop after 1 round
- **Baseline acceptance:** Skip InitNode when baseline provided
- **Results tracking:** Winner results flow between rounds
- **on_round_complete callback:** Fires with correct stall_count
- **on_phase callback:** Phase events for init, l1_generate, l1_evaluate
- **Resume:** Completed cycle replays from cache; interrupted cycle resumes
- **Mid-round resume:** Persisted candidates reused, L1GenerateNode skipped
- **Interrupt:** KeyboardInterrupt writes status="interrupted"
- **cycle_config_identity:** Stable across restarts, differs on config change, order-invariant

---

## 12. Tracing Gap Table

Status after v1 Phase 0.5 (to be re-achieved by Wave D5):

| Artifact | Pre-M7 | After Phase 0.5 | After Wave |
|----------|--------|-----------------|------------|
| `critique_text` | Lost | ✅ OptSearchPoint in trial | D5 |
| `thinking_styles` | Lost | ✅ OptSearchPoint in trial | D5 |
| `plan` | Buried | ✅ OptSearchPoint in trial | D5 |
| `task_context` | Not tracked | ✅ OptSearchPoint in trial | D5 |
| `optimizer_params` | Not tracked | ✅ OptSearchPoint in trial | D5 |
| L2 transition rationale | Lost | ✅ Node I/O in Langfuse | C3 + D4 |
| L3 transition rationale | Lost | ✅ Node I/O in Langfuse | C3 + D4 |
| L2/L3 transition inputs | Lost | ✅ Node input in Langfuse | C3 + D4 |
| Candidate generation prompt | Lost | ✅ Node I/O in Langfuse | C3 + D2 |
| Scan context enrichment | Lost | ✅ L1Generate input | D2 |
| Escalation signals | Not indexed | ❌ Phase 1 (Wave E1) | E1 |
| `OPTIMIZER_PIPELINE_SCHEMA` | N/A | ✅ Replaced by `optimizer_pipeline.json` + building block standard | G |
| Per-round Langfuse scores | Partial | ✅ Obs round_end | Already present |
| Phase events (display) | N/A | ✅ `_emit_phase()` callbacks | Already present |
| `escalation_journal` | Lost on restart | ❌ Move to OptSearchPoint | F1 |
| `critique` (full dict) | Lost (only text) | ❌ Move to OptSearchPoint | F1 |
| `query_failure_tracker` | N/A | ❌ New in Wave F | F1 |

---

## 13. Warning Inventory & L2 Probe Rounds

### 13.1 Problem

The optimizer treats every query failure identically — whether the query failed because the prompt was bad or because `web_search` returned no content (e.g., "3 of 14 fetched URLs returned content"). Consequences:

1. **Wasted eval budget**: Same queries fail every round for the same pipeline reason. L1 generates prompt variants trying to fix unfixable-by-prompt queries.
2. **False escalation loops**: `DegradationCheck` fires every round because the same queries always degrade, even when the prompt is improving on other queries.
3. **No cross-round memory**: Critique sees per-query `pipeline_data` (warnings, `terminated_at`) within a single round, but nobody tracks "Query X has had `web_search:partial_scrape` warnings for 3 consecutive rounds."

### 13.2 OptSearchPoint Consolidation

**ADR-8: OptSearchPoint is mutable, not frozen.**

`OptSearchPoint` was `frozen=True`, modeled after `SearchPoint`'s content-addressed design. But `OptSearchPoint` serves a different role — it's a **checkpoint snapshot** written once per round, not a content-addressed identity used for dedup. Freezing forces unnecessary full reconstruction each round.

**Decision:** Remove `model_config = {"frozen": True}`. Add missing optimizer-state fields that were scattered on `_LoopState`:

```python
class OptSearchPoint(BaseModel):
    # Existing fields
    critique_text: str = ""
    thinking_styles: list[str] = Field(default_factory=list)
    plan: str = ""
    optimizer_params: dict[str, Any] = Field(default_factory=dict)
    task_context: dict[str, Any] = Field(default_factory=dict)
    content_hashes: list[str] = Field(default_factory=list)

    # New fields (Wave F1)
    critique: dict[str, Any] = Field(
        default_factory=dict,
        description="Full 5-field critique dict (positive_critique, negative_critique, "
        "priority_fix, suggested_axes, summary). Currently only critique_text persists.",
    )
    escalation_journal: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Cross-round degradation investigation memory. "
        "Currently on _LoopState only — lost on kernel restart.",
    )
    query_failure_tracker: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Per-query warning inventory across rounds. "
        "Keyed by query text, values are warning counters.",
    )
```

**`_LoopState` consolidation:** Replace 5 scattered optimizer-state fields (`critique_text`, `critique`, `thinking_styles`, `task_context`, `escalation_journal`) with single `opt_sp: OptSearchPoint`. Loop-mechanics fields (`current_sp`, `stall_count`, `l2_stall_count`, etc.) stay on `_LoopState`.

| Before (`_LoopState`) | After (`_LoopState`) |
|---|---|
| `critique_text: str` | `opt_sp: OptSearchPoint` |
| `critique: dict` | *(26 references in feedback_cycle.py migrate to `state.opt_sp.X`)* |
| `thinking_styles: list[str]` | |
| `task_context: dict` | |
| `escalation_journal: list[dict]` | |

**Checkpoint simplification:**

```python
# Before: construct new frozen OSP from scattered fields
opt_sp = OptSearchPoint(critique_text=state.critique_text, ...)

# After: direct serialization (opt_sp already up-to-date)
state.opt_sp.plan = state.current_sp.prompt_state.plan
state.opt_sp.optimizer_params = state.current_sp.prompt_state.optimizer_params
campaign_store.add_trial(..., "opt_search_point": state.opt_sp.model_dump())
```

**Resume simplification:** `state.opt_sp = OptSearchPoint(**_osp)` — one-shot hydration. `escalation_journal` and `query_failure_tracker` now survive kernel restarts for free.

**Scope boundary:** Node I/O models (`L1GenerateInput.critique_text`, etc.) are wire-format fields — unchanged. The orchestrator passes `state.opt_sp.critique_text` into node input dicts.

### 13.3 Per-Query Warning Inventory

A cross-round per-query warning inventory on `OptSearchPoint.query_failure_tracker`. Updated after each round's eval results. Simple counters, no complex classification.

**Data shape:**

```python
{
    "PA 66 25% GF V0 RAL 7012/0": {
        "rounds_seen": 3,
        "hits": 0,
        "misses": 3,
        "warnings": {
            "web_search:partial_scrape": 3,
            "web_search:low_content": 2,
        },
        "last_terminated_at": "web_search",
    },
    "Kingfa NPG25": {
        "rounds_seen": 3,
        "hits": 1,
        "misses": 2,
        "warnings": {"web_search:partial_scrape": 2},
        "last_terminated_at": "llm_ranking",
    },
    "Aspirin powder": {
        "rounds_seen": 3,
        "hits": 3,
        "misses": 0,
        "warnings": {},
        "last_terminated_at": "llm_ranking",
    },
}
```

**Pure functions** in `critique_stats.py`:

- **`update_query_tracker(tracker, results)`** — merges current round's per-query results into the inventory. Increments `rounds_seen`, `hits`/`misses`, warning type counts, updates `last_terminated_at`.
- **`summarize_warning_inventory(tracker)`** — produces a text summary grouped by warning type for prompt injection. Returns empty string when no warnings tracked.

Output example:
```
## RECURRING PIPELINE WARNINGS (across 5 rounds)
  web_search:partial_scrape — 3 queries affected:
    PA 66 25% GF V0 RAL 7012/0  (3/3 rounds, 0 hits)
    Kingfa NPG25  (2/3 rounds, 1 hit)
    BASF Ultramid  (2/3 rounds, 0 hits)
  entity_profiling:timeout — 1 query affected:
    Specialty resin XR-200  (2/5 rounds, 2 hits)
```

### 13.4 Context Injection

The warning inventory summary is injected into three consumers (only when tracker has data, typically round 2+):

| Consumer | Injection point | Purpose |
|----------|----------------|---------|
| **Critique** | New `## RECURRING PIPELINE WARNINGS` section in `assemble_critique_prompt()` | Critique can distinguish prompt failures from pipeline failures |
| **L2 refine_context** | Alongside existing escalation section in the L2 prompt | L2 sees which queries have recurring warnings, can reason about targeted fixes |
| **L1 generate** | `focus_note` in scan-aware meta-prompt | L1 knows which failures have recurring pipeline issues |

Additionally, `failure_examples` in `prompt_optimizer.py` annotated with warning history:

```
Query: PA 66 25%... | Predicted: Glass fibre... | GT: Polyamide...  [⚠ web_search:partial_scrape 3/3 rounds]
```

### 13.5 DegradationCheck — Unchanged

`DegradationCheck` in `escalation.py` stays exactly as-is. It fires when degradation rate exceeds threshold, triggering L2. The improvement is that **L2 now has the warning inventory context** to make better decisions instead of blindly trying query prefix changes.

### 13.6 L2 Action Classification & Probe Rounds

L2's structured output includes an `"action"` classification field:

| Action | Meaning |
|--------|---------|
| `"continue"` | Default — normal L1 cycle continues with random subsample |
| `"probe"` | Next L1 round is a **probe round** — specialized eval batch + no degradation abort |

Extensible — future actions (e.g., `"skip_warned"`, `"retry"`) can be added without schema changes.

**Design principle:** L2 decides WHETHER to probe based on the warning inventory data (per-query warning counts across rounds). Low counts in early rounds → L2 naturally ignores them. As counts grow, L2 classifies the situation as needing a probe.

**Probe round = specialized L1 round**, not a separate mini-eval:

- **Eval batch**: All queries with warnings from the tracker (looked up in full `eval_data`), not the random subsample
- **No degradation abort**: `DegradationCheck` disabled — we expect degradation, that's the point of probing
- **Normal L1 flow**: Generate → evaluate → winner selection proceeds as usual
- **Counts toward `max_rounds`**: Probe rounds are regular rounds with a different eval batch, not exempt from accounting
- **L2 follows**: After a probe round completes, L2 fires to assess probe results and decide next action (continue, probe again, etc.)

**Flow:**

```
Round N: 8/20 queries degraded (recurring web_search warnings)
  → Escalation fires, L2 runs
  → L2 sees warning inventory: "PA 66 25%... (3/4 rounds, 0 hits)"
  → L2 returns {"action": "probe", "optimizer_params": {...}, ...}
  → state.probe_next_round = True

Round N+1 (probe round):
  → round_eval_data = all warned queries from tracker (via full eval_data)
  → escalation_checks disabled for this round
  → L1 generates candidates (with warning inventory context)
  → Evaluate candidates against warned queries only
  → Winner selection, tracker updated
  → L2 fires to assess probe results
    → action="continue": resume normal L1 with random subsample
    → action="probe": another probe round
```

**Implementation:**

1. `TransitionResult.action: str = "continue"` — parsed from L2 JSON response
2. `_LoopState.probe_next_round: bool = False` — set by orchestrator when `action == "probe"`
3. Main loop: if `probe_next_round`, override `round_eval_data` with warned queries, pass `escalation_checks=None`
4. After probe round: reset flag, force L2 to fire

### 13.7 Wave F — Execution Plan

#### F1: OptSearchPoint consolidation

- `api/models/opt_search_point.py` — remove frozen, add 3 new fields
- `api/services/campaign/models.py` — replace 5 scattered fields with `opt_sp: OptSearchPoint`
- `api/services/campaign/feedback_cycle.py` — migrate 26 `state.X` → `state.opt_sp.X`; simplify checkpoint/resume

#### F2: Warning inventory

- `api/services/campaign/critique_stats.py` — `update_query_tracker()`, `summarize_warning_inventory()`
- `api/services/campaign/feedback_cycle.py` — call `update_query_tracker()` after each round
- `api/services/campaign/critique_stats.py` — add inventory section to `assemble_critique_prompt()`

#### F3: Context injection

- `api/services/prompt_optimizer.py` — annotate failure_examples with `[⚠ warning_type N/M rounds]`
- `api/services/campaign/feedback_cycle.py` — wire inventory into L2 and L1-gen prompts

#### F4: L2 probe rounds

- `api/services/campaign/layer_transitions.py` — `TransitionResult.probe_queries`; parse from L2 response
- `api/config/optimizer_prompts/l2_refine_context.json` — probe instruction in L2 prompt
- `api/services/campaign/feedback_cycle.py` — probe round orchestration logic

---

## 14. Building Block Standard

**Wave G** replaced the Pydantic node I/O wrappers (Waves A-D) with direct service calls + `observed_step` tracing. This simplification revealed a more fundamental pattern: both TermNorm and PromptPotter use the same primitives (LLM calls, web search, deterministic functions) and should share a common vocabulary for declaring and composing them.

### Type hierarchy

Every node is self-contained: prompt assembly + execution + response parsing in one unit.

```
llm                  ← raw prompt → response
├── llm/structured   ← + prompt template + output schema (TermNorm nodes)
│   └── llm/meta     ← + multi-source assembly + context parsing (optimizer nodes)
└── agent            ← + multi-step loop (CritiqueAgent)
web_search           ← external HTTP service
deterministic        ← pure function
evaluation           ← backend call + comparison
```

### Composability pattern

Every node has one signature: `async def run(ctx: Ctx) -> None`. Reads from ctx, writes to ctx. Pipelines are lists of nodes; the runner just loops.

### `optimizer_pipeline.json`

Declares the optimizer's nodes using the same JSON format as TermNorm's `GET /pipeline`. Located at `api/config/optimizer_pipeline.json`. Defines 5 nodes (`l1_generate`, `l1_evaluate`, `critique`, `l2_refine_context`, `l3_modify_plan`) and 4 pipeline sequences (`l1_round`, `l1_round_with_critique`, `l2_escalation`, `l3_escalation`).

### Current scope

This wave delivers the standard (documentation + config declaration). The actual `llm_call()` primitive extraction and shared library are future work, dependent on ConnectorProtocol readiness.

**Full reference:** [`docs/building-blocks.md`](../building-blocks.md)
