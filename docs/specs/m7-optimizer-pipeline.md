# Milestone 7: Optimizer-as-Pipeline

**Version:** 2.0.0
**Date:** 2026-03-24
**Status:** Complete. All optimizer nodes operational, tracing active, pipeline declaration stable.
**Depends on:** [M6 PipelineSchema](m6-pipeline-composability.md), [ADD v0.10.0](architecture-design.md)

---

## 1. Context & Motivation

PromptPotter optimizes workflow pipelines (currently TermNorm's 6-step terminology normalization pipeline). The optimizer itself is a pipeline with 5 nodes organized into 4 named sequences. These nodes share the same structural properties as any target backend pipeline:

- Each node has defined **inputs and outputs**
- Each node has a **parameter surface** (model, temperature, max_tokens, etc.)
- Each node involves **LLM calls** with specific prompts (or backend calls for evaluation)
- Nodes form a **loop topology** with conditional escalation

Modeling the optimizer as a pipeline solves three problems:

1. **Tracing** — Optimizer nodes get the same Langfuse tracing infrastructure as target pipeline steps. `critique_text`, `thinking_styles`, L2/L3 transition rationale, escalation signals, and meta-prompts are captured per node.
2. **Reproducibility** — Every meta-optimizer decision is traced with full I/O. Given a trial JSON, you can reconstruct every LLM call.
3. **Self-optimization** — A meta-PromptPotter instance can optimize the optimizer's own prompts. L4 completes the escalation hierarchy.

### The Tracing Gap (pre-M7)

Artifacts not persisted (lost after each cycle):

| Artifact | Where it lived | Persistence |
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

**M7 solved all of these** via `OptSearchPoint` checkpointing in trial JSON + node-level Langfuse tracing via `observed_step()`.

---

## 2. Architectural Decisions Log

Eight key design choices made during M7 development. Some reference intermediate implementations (Pydantic node wrapper classes) that were built then deleted — the decisions themselves remain valid.

### ADR-1: Critique inside l1_evaluate, not orchestrator

**Decision:** `CritiqueAgent.run()` and `sample_thinking_styles()` execute inside the l1_evaluate phase of `_evaluate_candidates()`, producing `critique_text` and `thinking_styles` as output fields.

**Rationale:** Critique output feeds the *next* round's generation — it is part of l1_evaluate's output contract. Keeping it in the evaluate phase means Langfuse traces capture the critique LLM call as a child of the evaluate span, and the output documents the full data contract.

**Alternative rejected:** Running critique in the orchestrator after evaluation. This splits the observation across two trace locations and requires the orchestrator to know about critique internals.

### ADR-2: `baseline_rendered` INCLUDED in `cycle_config_identity()`

**Decision:** `cycle_config_identity()` includes `baseline_rendered` alongside optimization-relevant config fields + sorted eval_data pairs.

**Rationale:** Removing `baseline_rendered` from the hash orphans existing campaign data — the cycle_id changes, breaking campaign continuity (resume, trial lookup, dashboard display). The non-determinism concern from `restructure_context()` is acceptable because the baseline is typically set once per experiment and reused across kernel restarts via `baseline_prompt_state` passthrough.

**Fields included:** `max_rounds`, `patience`, `n_variants`, `creativity`, `improvement_threshold`, `model`, `provider`, `temperature`, `sample_size`, `seed`, `baseline_rendered`, sorted `eval_data_pairs`.

### ADR-3: `scan_context` as runtime input, not node config

**Decision:** `scan_context` is passed as a runtime argument to `l1_generate()`, not a config key in `optimizer_pipeline.json`.

**Rationale:** Scan context can change between rounds (e.g., after L2 transition changes the pipeline). Node config is set at declaration time; input data varies per invocation. The orchestrator resolves scan context and passes it as input.

**Implementation:** `CycleConfig.scan_context` carries the initial value. The orchestrator forwards it to `l1_generate()` via function arguments.

### ADR-4: Suggestion generation stays in orchestrator

**Decision:** `generate_suggestions()` is NOT a pipeline node. It runs in `_evaluate_candidates()` after l1_evaluate returns.

**Rationale:** Suggestions require accumulated round history (`state.rounds`) and the campaign config — both orchestrator-level state. Making this a node would require threading the entire history through the node interface, which adds complexity without tracing benefit (suggestions are a secondary output).

### ADR-5: Observation type per node

**Decision:** Each node declares its Langfuse observation type. Most nodes are `"generation"` (single LLM call). l1_evaluate is `"span"` (composite operation).

**Rationale:** Most nodes (`l1_generate`, `l2_refine_context`, `l3_modify_plan`) make a single LLM call — a Langfuse `generation` observation. l1_evaluate is a composite operation with nested children (N candidate evaluations + optional critique) — a Langfuse `span` that contains child observations.

| Node | obs_type | Why |
|------|----------|-----|
| `l1_generate` | `"generation"` | Single `l1_generate()` LLM call |
| `l1_evaluate` | `"span"` | N eval calls + optional critique agent |
| `critique` | `"generation"` | Single `CritiqueAgent.run()` LLM call |
| `l2_refine_context` | `"generation"` | Single `refine_context()` LLM call |
| `l3_modify_plan` | `"generation"` | Single `modify_plan()` LLM call |

### ADR-6: OptSearchPoint as cross-reference, not container

**Decision:** `OptSearchPoint` holds optimizer config state + `content_hashes` linking to target-layer `dataset_runs`. It does NOT embed or contain target evaluation data.

**Rationale:** Target data (dataset_runs, SearchPoints) stays clean in the shared `dataset_runs/` store with content-addressed dedup. All optimizer provenance lives in the optimizer layer (trial JSON). L4 meta-optimization correlates `OptSearchPoint.parameters` with `dataset_run.accuracy` via `content_hashes` join — no data duplication.

### ADR-7: PromptState.compile() double-brace syntax

**Decision:** Optimizer prompt templates use `{{variable}}` syntax (double braces), rendered via `PromptState.compile(**kwargs)`.

**Rationale:** Single `{braces}` conflict with JSON examples in prompt text. Double braces are Python `.format()`-escaped for literal braces, then `.compile()` does a second pass replacing `{{var}}` with values. This keeps templates readable as both raw text and PromptState JSON.

### ADR-8: OptSearchPoint is mutable, not frozen

**Decision:** `OptSearchPoint` is a mutable `BaseModel` (not frozen). Unlike `SearchPoint` which is content-addressed (identity = hash), `OptSearchPoint` is a checkpoint snapshot written once per round.

**Rationale:** Freezing forces unnecessary full reconstruction each round. The mutable design enables in-place updates during the feedback cycle (e.g., `state.opt_sp.critique_text = ...`), then a single `model_dump()` at checkpoint time. All optimizer-state fields that were previously scattered across `_LoopState` are consolidated on `OptSearchPoint`.

---

## 3. Pipeline Architecture

### 3.1 Pipeline Declaration

The optimizer pipeline is declared in `api/config/optimizer_pipeline.json`. Five nodes, four named sequences:

```json
{
  "name": "PromptPotter Optimizer",
  "version": "v1.0",
  "nodes": {
    "l1_generate":       { "type": "llm/meta",   "config": { ... } },
    "l1_evaluate":       { "type": "evaluation",  "config": { ... } },
    "critique":          { "type": "agent",       "config": { ... } },
    "l2_refine_context": { "type": "llm/meta",   "config": { ... } },
    "l3_modify_plan":    { "type": "llm/meta",   "config": { ... } }
  },
  "pipelines": {
    "l1_round":                ["l1_generate", "l1_evaluate"],
    "l1_round_with_critique":  ["l1_generate", "l1_evaluate", "critique"],
    "l2_escalation":           ["l2_refine_context", "l1_generate", "l1_evaluate", "critique"],
    "l3_escalation":           ["l3_modify_plan", "l2_refine_context", "l1_generate", "l1_evaluate", "critique"]
  }
}
```

### 3.2 Node Catalog

#### l1_generate (type: `llm/meta`)

Generates N candidate PromptState variants via LLM meta-prompt.

**Config defaults:** `temperature: 0.7`, `max_tokens: 8192`, `output_format: "json"`, `prompt_family: "meta_scan_aware"`, `context_sources: ["scan_context", "critique", "task_context", "escalation_journal"]`, `response_parser: "candidate_list"`

**Implementation:** `l1_generate()` in `api/services/l1_optimizer.py`. Assembles a meta-prompt from scan analytics, critique, task context, L2 directive, thinking styles, escalation journal, and warning inventory. Returns a list of candidate dicts (PromptState dumps with optional `__pipeline_params_override__`).

**Key inputs:** `current_ps`, `current_accuracy`, `current_results`, `n_variants`, `creativity`, `scan_context`, `critique_text`, `thinking_styles`, `escalation_journal`, `task_context`, `warning_inventory`, `l2_directive`, `is_probe_round`

**Key output:** `list[dict]` — candidate PromptState dicts

#### l1_evaluate (type: `evaluation`)

Evaluates candidates via backend `/matches` endpoint, selects winner, runs critique.

**Config defaults:** `improvement_threshold: 0.01`

**Implementation:** `l1_evaluate()` in `api/services/l1_optimizer.py` (winner selection) + `_evaluate_candidates()` in `api/services/campaign/feedback_cycle.py` (orchestration wrapper that adds critique + thinking style sampling).

**Key inputs:** `candidates`, `round_eval_data`, `current_best` (accuracy + prompt_state + results), `eval_ctx`, `improvement_threshold`, `escalation_checks`

**Key output:** Dict with `winner`, `winner_accuracy`, `winner_prompt_state`, `improved`, `next_action`, `candidate_scores`, `winner_results`, `critique_text`, `critique`, `thinking_styles`, `winner_composite`, `winner_pipeline_params`, `escalation_signal`

**Observation type:** `"span"` (composite operation containing per-candidate evals + critique)

#### critique (type: `agent`)

CritiqueAgent produces a 5-field analysis dict fed to both l1_generate (next round) and l2_refine_context (on escalation).

**Config defaults:** `temperature: 0.3`, `max_tokens: 4096`, `agent_class: "CritiqueAgent"`, `positive_threshold: 0.7`

**Implementation:** `CritiqueAgent` class in `api/services/campaign/critique.py`. Routes to positive path (accuracy >= threshold: extend what works) or negative path (accuracy < threshold: diagnose root causes). Uses pre-computed stats from `critique_stats.py` (pipeline health, rank analysis, round evolution, query categories, anomaly flags).

**Output fields:**

| Field | Description |
|-------|-------------|
| `positive_critique` | What's working — patterns to extend |
| `negative_critique` | What's failing — root causes and blockers |
| `priority_fix` | Single most impactful change to make |
| `suggested_axes` | Parameter axes to explore (e.g., `["query_prefix", "max_sites"]`) |
| `summary` | 2-3 sentence actionable critique |

**Note:** In the current implementation, critique runs *inside* `_evaluate_candidates()` (the l1_evaluate orchestration wrapper), not as a separately-invoked pipeline node. This is per ADR-1. The `critique` node in `optimizer_pipeline.json` declares its config; the orchestrator reads the config via `get_node_config("critique")` but invokes `CritiqueAgent` directly.

#### l2_refine_context (type: `llm/meta`)

Refines task_context + meta-settings (creativity, n_variants, sample_size) when L1 stalls.

**Config defaults:** `temperature: 0.3`, `max_tokens: 2048`, `output_format: "json"`, `prompt_family: "l2_refine_context"`, `context_sources: ["critique", "task_context", "l2_directive"]`, `response_parser: "transition_result"`

**Implementation:** `refine_context()` in `api/services/campaign/layer_transitions.py`. Receives stalled round summaries, failure examples, pipeline params, escalation context, escalation journal, warning inventory, critique text, and previous L2 directive.

**Returns:** `TransitionResult` with:
- `prompt_state` — new PromptState with updated optimizer_params
- `task_context` — refined domain context dict (or None if unchanged)
- `l2_directive` — 2-3 sentence instruction for l1_generate (sliding window of 1)
- `action` — `"continue"` (normal L1 cycle) or `"probe"` (probe round)
- `debug_prompt` / `debug_response` — for observability

**Responsibility boundary:** L2 does NOT set `pipeline_params` — that is l1_generate's job. L2 refines the *situation context* and *meta-settings* so l1_generate makes better choices.

#### l3_modify_plan (type: `llm/meta`)

Modifies the strategic optimization plan when L2 stalls.

**Config defaults:** `temperature: 0.5`, `max_tokens: 2048`, `output_format: "json"`, `prompt_family: "l3_modify_plan"`, `context_sources: ["critique", "task_context", "plan", "escalation_journal"]`, `response_parser: "transition_result"`

**Implementation:** `modify_plan()` in `api/services/campaign/layer_transitions.py`. Receives L2 adjustment history, current plan, pipeline params + schema.

**Returns:** `TransitionResult` with:
- `prompt_state` — new PromptState with updated `plan`
- `pipeline_params` — optional new pipeline params (L3 can suggest broad strategy shifts)

### 3.3 Node Type Hierarchy

Shared vocabulary between TermNorm and PromptPotter pipelines:

```
llm                  <- raw prompt -> response
+-- llm/structured   <- + prompt template + output schema (TermNorm nodes)
|   +-- llm/meta     <- + multi-source assembly + context parsing (optimizer nodes)
+-- agent            <- + multi-step loop (CritiqueAgent)
web_search           <- external HTTP service
deterministic        <- pure function
evaluation           <- backend call + comparison
```

**Key insight:** `llm/meta` inherits from `llm/structured` which inherits from `llm`. The LLM call is always the same internally — subtypes add prompt assembly and response parsing around it. A new node = configure which subtype + `prompt_family` + `response_parser`.

### 3.4 Shared Primitive: `llm_call()`

`llm_call()` in `api/core/llm_call.py` is the shared LLM interaction primitive. Config-driven from `optimizer_pipeline.json` with runtime overrides.

```python
async def llm_call(
    llm_client: LLMClientBase,
    messages: list[dict[str, str]],
    config: dict,          # from optimizer_pipeline.json node config
    **overrides,           # runtime: model, temperature, max_tokens
) -> LLMResponse
```

`get_node_config(node_name)` loads a node's config dict from `optimizer_pipeline.json` (cached after first call). All optimizer nodes use this instead of calling `chat()` directly.

**Callers:**
- `l1_generate()` in `l1_optimizer.py` — `llm_call(..., config=get_node_config("l1_generate"), temperature=creativity)`
- `CritiqueAgent.run()` in `critique.py` — `llm_call(..., config=get_node_config("critique"))`
- `refine_context()` in `layer_transitions.py` — `llm_call(..., config=get_node_config("l2_refine_context"))`
- `modify_plan()` in `layer_transitions.py` — `llm_call(..., config=get_node_config("l3_modify_plan"))`

### 3.5 Tracing: `observed_step()`

`observed_step()` in `api/services/obs/step_tracer.py` is an async context manager wrapping each node execution with timing + Langfuse observations.

```python
async with observed_step("l1_generate_r3", "llm/meta", obs=obs, trace_id=tid) as step:
    result = await l1_generate(...)
    step.output = {"n_candidates": len(result)}
```

Yields a `StepTrace` with `.output` (set by caller), `.duration_ms`, `.error`. Tracing is opt-in: only active when `obs` + `trace_id` are provided. Non-fatal: observability failures are logged as warnings and never crash the caller.

**Usage in feedback_cycle.py:**

| Call site | step_id pattern | step_type | obs_type |
|-----------|----------------|-----------|----------|
| `_generate_or_load_candidates()` | `l1_generate_r{N}` | `"llm/meta"` | `"generation"` (default) |
| `_evaluate_candidates()` | `l1_evaluate_r{N}` | `"evaluation"` | `"span"` |
| `_do_l2_transition()` | `l2_refine_r{N}` | `"llm/meta"` | `"generation"` (default) |
| `_do_l3_transition()` | `l3_modify_plan_r{N}` | `"llm/meta"` | `"generation"` (default) |

### 3.6 Responsibility Matrix

| Node | Decides | Does NOT decide |
|------|---------|-----------------|
| **l1_generate** | `pipeline_params` (query_prefix, max_sites, schema, temperature, ...) | context, meta-settings |
| **critique** | what to focus on (suggested_axes, priority_fix) | pipeline_params values |
| **l2_refine_context** | context, meta-settings (creativity, n_variants, sample_size), task_context, l2_directive | pipeline_params |
| **l3_modify_plan** | strategic plan, broad pipeline_params shifts | context, meta-settings |

---

## 4. Orchestrator Flow

### 4.1 State Machine

```
                    +------------------------------+
                    |  Campaign Init               |  (decompose_task_context + restructure)
                    +--------------+---------------+
                                   | baseline PromptState + task_context
                                   v
             +---- Resume Detection ----+
             |  CampaignStore lookup    |
             |  Obs setup               |
             |  EvalContext build        |
             |  Bootstrap critique      |
             +----------+---------------+
                        |
    +-------------------v--------------------+
    |            Round Loop                  |
    |  +-----------------------------------+ |
    |  | l1_generate                       | |
    |  |  (or load from disk if resume)    | |
    |  +----------------+------------------+ |
    |                   | candidates          |
    |  +----------------v------------------+ |
    |  | l1_evaluate                       | |
    |  |  eval -> winner -> critique       | |
    |  |  -> thinking_styles               | |
    |  +----------------+------------------+ |
    |                   | round_result        |
    |  +----------------v------------------+ |
    |  | State Update                      | |
    |  |  - Update current_sp, accuracy    | |
    |  |  - Track best_sp                  | |
    |  |  - Increment stall_count          | |
    |  |  - Checkpoint OptSearchPoint      | |
    |  +----------------+------------------+ |
    |                   |                    |
    |  +----------------v------------------+ |
    |  | Stopping Conditions               | |
    |  |  * perfect_score (acc >= 1.0)     | |
    |  |  * next_action_stop               | |
    |  |  * patience_exhausted             | |
    |  |  * max_rounds                     | |
    |  |  * l2/l3_patience_exhausted       | |
    |  +----------------+------------------+ |
    |                   | stall_count >= patience
    |  +----------------v------------------+ |
    |  | Escalation (if enable_l2)         | |
    |  |  l2_refine_context -> reset stall | |
    |  |    +- if L2 stalls:               | |
    |  |       l3_modify_plan              | |
    |  |       -> reset L2 + L1 stall      | |
    |  +-----------------------------------+ |
    +----------------------------------------+
                        |
                        v
                   Finalize
                   (CampaignStore, Obs, CycleResult)
```

### 4.2 `_LoopState` structure

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

    # Optimizer state — single OptSearchPoint (mutable, checkpointed per round)
    opt_sp: OptSearchPoint            # critique, thinking_styles, task_context,
                                      # escalation_journal, warning_inventory, etc.

    # Probe round flag (set by L2 action="probe", reset after probe round)
    probe_next_round: bool

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

**Key design:** All optimizer-level state lives on `opt_sp: OptSearchPoint`. Loop-mechanics fields (`current_sp`, `stall_count`, `l2_stall_count`, etc.) stay on `_LoopState`. On resume, `opt_sp` is hydrated from the latest trial JSON in one shot: `state.opt_sp = OptSearchPoint(**stored_osp)`.

### 4.3 Round execution flow

Each round in `_execute_round()`:

1. Resolve L2 meta-param overrides from `PromptState.optimizer_params` (n_variants, creativity)
2. `_generate_or_load_candidates()` — check disk cache first, then call `l1_generate()` wrapped in `observed_step()`
3. `_evaluate_candidates()` — call `l1_evaluate()` wrapped in `observed_step()` + run CritiqueAgent + sample thinking styles
4. Pop critique_text/thinking_styles/critique from eval output into `state.opt_sp`
5. Build `CycleRoundResult`
6. Update per-query warning inventory from all candidate results
7. Log round to ObsLogger (round_end + prompt_version)

### 4.4 Escalation routing

```
stall_count >= patience
    +- enable_l2?
        +- YES -> _escalate_l2()
        |        +- l2_improved?
        |            +- YES -> l2_stall_count = 0, continue L1
        |            +- NO  -> l2_stall_count++
        |                     +- l2_stall_count >= l2_patience?
        |                         +- enable_l3?
        |                         |   +- YES -> l3_modify_plan
        |                         |   |        l3_stall_count check -> continue or stop
        |                         |   +- NO  -> stop_reason="l2_patience_exhausted"
        |                         +- continue L1
        +- NO  -> stop_reason="patience_exhausted"
```

L2 transition: resets L1 stall_count, increments l2_round, snapshots best_accuracy_at_l2_entry.
L3 transition: resets L2 stall_count + l2_round + L1 stall_count, increments l3_round.

### 4.5 Degradation escalation path

When `DegradationCheck` fires mid-evaluation (degraded query fraction exceeds threshold):

1. Evaluation aborted, `EscalationSignal` bubbled up via `escalation_signal` on round result
2. Orchestrator dispatches to L2 with `from_degradation=True` and `escalation_context`
3. L2/L3 patience exhaustion during degradation *resets counters* instead of stopping — investigation continues
4. Escalation journal entry recorded (tried config, degradation rate, outcome)
5. Degradation rounds do not count toward `max_rounds` (hard cap: 100)

---

## 5. Prompt Template Inventory

All templates are PromptState JSON with 5 Layer 1 fields (`persona`, `task_intent`, `instruction`, `thinking_style`, `answer_format`). Placeholders use `{{variable}}` syntax.

| Template | Variables | Consumer |
|----------|-----------|----------|
| `meta_scan_aware.json` | `n_variants`, `accuracy_pct`, `n_queries`, `rendered_prompt`, `failure_examples`, `scan_analytics`, `focus_note`, `context_sections`, `instruction_spec` | `l1_generate()` — primary generation mode |
| `critique_negative.json` | `accuracy_pct`, `n_failures`, `max_examples`, `failure_lines` | `CritiqueAgent` — below threshold |
| `critique_positive.json` | `accuracy_pct`, `n_successes`, `max_examples`, `success_lines`, `n_failures`, `failure_lines` | `CritiqueAgent` — above threshold |
| `l2_refine_context.json` | `round_summary`, `rendered_prompt`, `failure_lines`, `current_params`, `task_context_section`, `pipeline_section`, `escalation_section`, `critique_section`, `prev_directive_section`, `response_schema_suffix` | `refine_context()` |
| `l3_modify_plan.json` | `current_plan`, `l2_summary`, `rendered_prompt`, `pipeline_section`, `response_schema_suffix` | `modify_plan()` |
| `suggestions.json` | `history_lines`, `accuracy_pct`, `rendered_prompt`, `campaign_config`, `n_failures`, `n_queries`, `failure_detail` | `generate_suggestions()` |
| `restructure.json` | `consultation_instruction` | `restructure_context()` — campaign init decomposition |

**Loading:** `load_optimizer_prompt(name)` in `api/config/optimizer_prompt_loader.py`. Resolution order: Langfuse prompt registry (opt-in, by `production` label, SDK-cached) -> local JSON in `api/config/optimizer_prompts/`. LRU-cached for local files. Push via `push_all_to_langfuse()`.

---

## 6. OptSearchPoint & L4 Path

### 6.1 Model

```python
class OptSearchPoint(BaseModel):
    """Optimizer-level search point — the optimizer's configuration at a moment."""

    critique_text: str = ""
    critique: dict[str, Any] = Field(
        default_factory=dict,
        description="Full 5-field critique dict (positive_critique, negative_critique, "
        "priority_fix, suggested_axes, summary).",
    )
    thinking_styles: list[str] = Field(default_factory=list)
    plan: str = ""
    optimizer_params: dict[str, Any] = Field(default_factory=dict)
    task_context: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured domain context (domain, pipeline_purpose, "
        "data_characteristics, optimization_goals, key_challenges). "
        "Set from TASK_DESCRIPTION decomposition, refinable by L2.",
    )
    escalation_journal: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Cross-round degradation investigation memory.",
    )
    warning_inventory: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Per-query warning inventory across rounds.",
    )
    l2_directive: str = Field(
        default="",
        description="L2's diagnostic reasoning + action guidance for L1. "
        "Sliding window of 1.",
    )
    content_hashes: list[str] = Field(
        default_factory=list,
        description="Content hashes of dataset_runs produced under this config",
    )
    degradation_reset_count: int = Field(0)
    backend_warning_emitted: bool = Field(False)
```

### 6.2 Persistence

Checkpointed in trial JSON after each round:

```json
{
  "trial_id": "round_0",
  "round": 0,
  "accuracy": 0.67,
  "stall_count": 0,
  "l2_round": 0,
  "l3_round": 0,
  "opt_search_point": {
    "critique_text": "Strengths: ...\nWeaknesses: ...",
    "critique": {
      "positive_critique": "...",
      "negative_critique": "...",
      "priority_fix": "...",
      "suggested_axes": ["query_prefix"],
      "summary": "..."
    },
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
    "escalation_journal": [],
    "warning_inventory": {},
    "l2_directive": "",
    "content_hashes": [],
    "degradation_reset_count": 0,
    "backend_warning_emitted": false
  }
}
```

### 6.3 Resume simplification

On resume, the entire optimizer state is hydrated in one shot:

```python
state.opt_sp = OptSearchPoint(**{
    k: v for k, v in stored_osp.items()
    if k in OptSearchPoint.model_fields
})
```

`escalation_journal`, `warning_inventory`, `l2_directive`, and `degradation_reset_count` all survive kernel restarts for free.

### 6.4 L4 path

L4 meta-optimization searches over `OptSearchPoint`s the same way L1-L3 search over `SearchPoint`s. The join key is `content_hashes` — linking optimizer config to target-layer evaluation outcomes without duplicating data.

### 6.5 Task context decomposition

`decompose_task_context()` (in `notebooks/_campaign_lib/_setup.py`) runs at campaign init, before the feedback cycle. It calls `restructure_context_cached()` with `TASK_DESCRIPTION` to produce a structured domain context dict.

**Fields:**

| Field | Description |
|-------|-------------|
| `domain` | Domain of the backend pipeline (e.g., "Life Cycle Assessment") |
| `pipeline_purpose` | What the pipeline does |
| `data_characteristics` | Nature of the input data |
| `optimization_goals` | What success looks like |
| `key_challenges` | Known difficulties |
| `raw_description` | Original `TASK_DESCRIPTION` text |

**Flow:**

1. Campaign init -> `decompose_task_context()` -> structured dict
2. Stored on `OptSearchPoint.task_context`
3. Flows to l1_generate meta-prompt (via `context_sections`)
4. L2 `refine_context()` can update `task_context` fields when escalation fires
5. `PromptState.context` auto-synced from `task_context`

---

## 7. Tracing Architecture

### 7.1 Nesting hierarchy

```
Campaign trace (feedback_cycle)
+-- round_0 (span)
|   +-- l1_generate_r0 (generation)       <- observed_step wrapping l1_generate()
|   +-- l1_evaluate_r0 (span)             <- observed_step wrapping _evaluate_candidates()
|   |   +-- eval_abc123 (tool)            <- per-candidate dataset_run
|   |   +-- eval_def456 (tool)
|   |   +-- [critique] (generation)       <- CritiqueAgent (if enabled)
|   +-- prompt_version (tool)             <- winner prompt registration
+-- round_1 (span)
|   +-- l1_generate_r1 (generation)
|   +-- l1_evaluate_r1 (span)
|   +-- prompt_version (tool)
+-- [l2_refine_r2] (generation)           <- if escalation triggered
+-- [l3_modify_plan_r2] (generation)      <- if L2 stalls
```

### 7.2 Dual-write (file + cloud)

`ObsLogger` writes to disk first, then delegates to `CloudObsBackend`:

- **File:** `obs/langfuse/observations/{trace_id}/{obs_id}.json` — created at start, updated with output/metrics at end
- **Cloud:** `CloudObsBackend.on_node_step_start()` -> `lf.start_span()`, `on_node_step_end()` -> `lf.end_observation()`
- **Events:** `events.jsonl` gets `node_step_start` and `node_step_end` entries

Cloud failures never crash the main flow — circuit breaker trips on first error.

### 7.3 Tracing gap resolution

| Artifact | Pre-M7 | After M7 |
|----------|--------|----------|
| `critique_text` | Lost | OptSearchPoint in trial JSON |
| `critique` (full 5-field dict) | Lost | OptSearchPoint in trial JSON |
| `thinking_styles` | Lost | OptSearchPoint in trial JSON |
| `plan` | Buried in prompt_state | OptSearchPoint in trial JSON |
| `task_context` | Not tracked | OptSearchPoint in trial JSON |
| `optimizer_params` | Not tracked | OptSearchPoint in trial JSON |
| `escalation_journal` | Lost on restart | OptSearchPoint in trial JSON |
| `warning_inventory` | N/A | OptSearchPoint in trial JSON |
| `l2_directive` | Lost | OptSearchPoint in trial JSON |
| L2 transition rationale | Lost | `observed_step()` Langfuse observation |
| L3 transition rationale | Lost | `observed_step()` Langfuse observation |
| L2/L3 transition inputs | Lost | `observed_step()` Langfuse observation |
| Candidate generation prompt | Lost | `observed_step()` Langfuse observation |
| Scan context enrichment | Lost | l1_generate input via `observed_step()` |
| Escalation signals | Not indexed | `EscalationSignal` in round result + journal |
| Per-round Langfuse scores | Partial | `log_round_end()` with full metrics |
| Phase events (display) | N/A | `_emit_phase()` callbacks |

---

## 8. CycleConfig Reference

### Core optimization

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_rounds` | int \| None | 10 | Maximum optimization rounds (None = unlimited) |
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
| `task_context` | dict \| None | None | Structured domain context for L1 gen and L2 refinement |

### L2/L3 escalation

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enable_l2` | bool | True | Enable L2 refine_context loop |
| `enable_l3` | bool | True | Enable L3 modify_plan loop |
| `l2_patience` | int \| None | 2 | L2 stalls before escalating to L3 (None = unlimited) |
| `l3_patience` | int \| None | 1 | L3 stalls before stopping (None = unlimited) |
| `l2_temperature` | float | 0.3 | Temperature for L2 LLM call |
| `l3_temperature` | float | 0.5 | Temperature for L3 LLM call |
| `degradation_threshold` | float | 0.4 | Fraction of degraded queries to trigger escalation (0 = disabled) |
| `backend_warning_threshold` | int | 2 | Degradation resets before emitting backend warning (0 = disabled) |

---

## 9. Jupyter Kernel Hang: Root Cause Analysis

The original Pydantic node wrapper implementation (v1, now deleted) surfaced a Windows kernel hang bug. This section documents the root causes and permanent design rules that emerged.

### Root causes

**RC-1: httpx async client `aclose()`** — When `KeyboardInterrupt` fires during an active httpx request, the async client's `__aexit__` calls `aclose()` which tries to gracefully close HTTP/2 connections. On Windows, this blocks indefinitely because the event loop is already corrupted by the interrupt.

**RC-2: Langfuse SDK thread join** — `langfuse.shutdown()` calls `thread.join()` on its background flush thread. If the thread is mid-HTTP-request when interrupt fires, the join blocks indefinitely.

**RC-3: asyncio corruption on Windows** — `KeyboardInterrupt` during `await` corrupts the `ProactorEventLoop` state. Subsequent async operations (including cleanup) hang. This is a known CPython issue.

**RC-4: Langfuse SDK lazy init in hot path** — `LangfuseLogger.get_instance()` called repeatedly. If Langfuse SDK is initializing its background threads when interrupt fires, thread state becomes inconsistent.

**RC-5 (root cause found via bisect): `self.config.copy()` in metrics collection.** When the v1 `L1EvaluateNode` was interrupted mid-httpx-request, Python ran the `finally` block which did `metadata=self.config.copy()`. For l1_evaluate, `self.config` contained `eval_ctx` — an `EvalContext` holding a `BackendClient` with a live httpx `AsyncClient`. Copying this object on a corrupted event loop triggered httpx internals that hang indefinitely. l1_generate never hung because its config was plain primitives.

### Design rules (permanent)

**R-1: No live objects in metrics metadata** — Metrics metadata must never reference live infrastructure objects. The current `observed_step()` implementation avoids this entirely by yielding a plain `StepTrace` dataclass.

**R-2: Daemon threads for Langfuse SDK** — Ensure all Langfuse-spawned threads are daemon threads so they don't prevent interpreter exit on interrupt.

**R-3: No `asyncio.shield` in eval path** — Shield creates more problems than it solves in interrupt scenarios. Let `KeyboardInterrupt` propagate naturally and rely on checkpointing for resume.

**R-4: Lazy Langfuse init** — Initialize `LangfuseLogger` once at campaign start, not on-demand.

---

## 10. Warning Inventory & L2 Probe Rounds

### 10.1 Problem

The optimizer treats every query failure identically — whether the query failed because the prompt was bad or because `web_search` returned no content. Consequences:

1. **Wasted eval budget**: Same queries fail every round for the same pipeline reason.
2. **False escalation loops**: `DegradationCheck` fires every round because the same queries always degrade.
3. **No cross-round memory**: Nobody tracks "Query X has had `web_search:partial_scrape` warnings for 3 consecutive rounds."

### 10.2 Per-Query Warning Inventory

A cross-round per-query warning inventory on `OptSearchPoint.warning_inventory`. Updated after each round's eval results via `update_query_tracker()`.

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

- **`update_query_tracker(tracker, results)`** — merges current round's per-query results into the inventory
- **`summarize_warning_inventory(tracker)`** — produces text summary grouped by warning type for prompt injection

### 10.3 Context Injection

The warning inventory summary is injected into three consumers:

| Consumer | Injection point | Purpose |
|----------|----------------|---------|
| **Critique** | `## RECURRING PIPELINE WARNINGS` section in `assemble_critique_prompt()` | Distinguish prompt failures from pipeline failures |
| **L2 refine_context** | Alongside escalation section in the L2 prompt | See which queries have recurring warnings |
| **L1 generate** | Warning annotations on `failure_examples` + probe round context | Know which failures have recurring pipeline issues |

Failure examples in `l1_optimizer.py` are annotated with warning history:

```
Query: PA 66 25%... | Predicted: Glass fibre... | GT: Polyamide...  [web_search:partial_scrape 3/3 rounds]
```

### 10.4 L2 Action Classification & Probe Rounds

L2's structured output includes an `"action"` classification field:

| Action | Meaning |
|--------|---------|
| `"continue"` | Default — normal L1 cycle continues with random subsample |
| `"probe"` | Next L1 round is a probe round — specialized eval batch + no degradation abort |

**Probe round = specialized L1 round**, not a separate mini-eval:

- **Eval batch**: All queries with warnings from the tracker, not the random subsample
- **No degradation abort**: `DegradationCheck` disabled — we expect degradation
- **Normal L1 flow**: Generate -> evaluate -> winner selection proceeds as usual
- **Counts toward `max_rounds`**: Probe rounds are regular rounds with a different eval batch
- **L2 follows**: After a probe round completes, L2 fires to assess results and decide next action

**Flow:**

```
Round N: 8/20 queries degraded (recurring web_search warnings)
  -> Escalation fires, L2 runs
  -> L2 sees warning inventory: "PA 66 25%... (3/4 rounds, 0 hits)"
  -> L2 returns {"action": "probe", ...}
  -> state.probe_next_round = True

Round N+1 (probe round):
  -> round_eval_data = all warned queries from tracker
  -> escalation_checks disabled for this round
  -> L1 generates candidates (with warning inventory context)
  -> Evaluate candidates against warned queries only
  -> Winner selection, tracker updated
  -> L2 fires to assess probe results
    -> action="continue": resume normal L1 with random subsample
    -> action="probe": another probe round
```

### 10.5 L2->L1 Information Bridge (l2_directive)

L2 produces a 2-3 sentence directive (diagnostic reasoning + action guidance) injected into l1_generate's meta-prompt as primary signal. Sliding window of 1 — set after L2 runs, cleared when the round improves (L2 doesn't fire).

L2 also receives:
- `critique_text` — builds on the critique rather than re-analyzing
- `prev_l2_directive` — evolves or supersedes its own previous directive

---

## 11. Testing Strategy

### Mock patterns

- **No pytest-mock plugin** — use `monkeypatch` for async service mocking, `unittest.mock.MagicMock` when needed
- **Test helpers** in `tests/_helpers.py`: `apply_llm_mock`, `apply_grow_mock`, `apply_eval_mock`, `run_simple_cycle`
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

### Feedback cycle tests

- **Multi-round improvement:** Hits improve 1->2->3, stops at perfect_score
- **Patience exhaustion:** All 0% hits, stops after patience rounds
- **Max rounds:** Slow improvement, stops at max_rounds
- **next_action stop:** Analysis signals stop after 1 round
- **Results tracking:** Winner results flow between rounds
- **on_round_complete callback:** Fires with correct stall_count
- **on_phase callback:** Phase events for init, l1_generate, l1_evaluate
- **Resume:** Completed cycle replays from cache; interrupted cycle resumes
- **Mid-round resume:** Persisted candidates reused, l1_generate skipped
- **Interrupt:** KeyboardInterrupt writes status="interrupted"
- **cycle_config_identity:** Stable across restarts, differs on config change, order-invariant

---

## 12. Escalation Framework

### EscalationCheck

Pluggable mid-evaluation checks that can abort evaluation and route to L2/L3.

```python
class EscalationCheck(ABC):
    name: str = ""
    enabled: bool = True

    @abstractmethod
    def evaluate(
        self,
        results_so_far: list[dict],
        candidate_idx: int,
        n_total_candidates: int,
    ) -> EscalationSignal | None: ...

@dataclass
class EscalationSignal:
    check_name: str           # "degradation", "error_rate", etc.
    target: str               # "retry" | "l2" | "l3" | "abort"
    context: dict[str, Any]
    candidate_idx: int
    candidates_evaluated: int
    candidates_skipped: int
```

### DegradationCheck

Triggers when degraded query fraction exceeds `degradation_threshold`. Default routing via `DEFAULT_STRATEGIES` (e.g., `"web_search:partial_scrape"` -> L2).

### Backend Warning

After repeated degradation resets (configurable via `backend_warning_threshold`), a one-shot backend warning is emitted via phase event, advising the user that the issue is likely a backend server problem rather than a prompt problem.

---

## 13. Key Files Reference

| File | Role |
|------|------|
| `api/config/optimizer_pipeline.json` | Pipeline declaration: 5 nodes, 4 sequences |
| `api/core/llm_call.py` | `llm_call()` primitive + `get_node_config()` |
| `api/services/obs/step_tracer.py` | `observed_step()` async context manager |
| `api/services/campaign/feedback_cycle.py` | Orchestrator: round loop, escalation, checkpointing |
| `api/services/campaign/models.py` | `CycleConfig`, `CycleRoundResult`, `CycleResult`, `_LoopState` |
| `api/services/l1_optimizer.py` | `l1_generate()`, `l1_evaluate()`, `generate_suggestions()` |
| `api/services/campaign/layer_transitions.py` | `refine_context()` (L2), `modify_plan()` (L3), `TransitionResult` |
| `api/services/campaign/critique.py` | `CritiqueAgent`, `format_critique_for_prompt()`, `sample_thinking_styles()` |
| `api/services/campaign/critique_stats.py` | Pre-computed stats, anomaly detection, warning inventory, prompt assembly |
| `api/services/campaign/escalation.py` | `EscalationCheck`, `DegradationCheck`, `EscalationSignal` |
| `api/models/opt_search_point.py` | `OptSearchPoint` model |
| `api/models/search_point.py` | `SearchPoint` model (target layer) |
| `api/config/optimizer_prompts/*.json` | Prompt templates (7 files) |
| `api/config/optimizer_prompt_loader.py` | `load_optimizer_prompt()` with Langfuse fallback |
| `docs/optimization.md` | Critique architecture reference (merged) |
