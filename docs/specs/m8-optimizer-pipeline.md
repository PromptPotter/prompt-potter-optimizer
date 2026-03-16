# Milestone 8: Optimizer-as-Pipeline

**Version:** 0.2.0
**Date:** 2026-03-16
**Status:** Draft
**Depends on:** [M6 PipelineSchema](m6-pipeline-composability.md), [ADD v0.10.0](add.md)

---

## Context

PromptPotter optimizes workflow pipelines (currently TermNorm's 6-step terminology normalization pipeline). The optimizer itself is a workflow pipeline with 4 LLM-driven steps (+ L4 meta-optimization as a future conceptual extension). These steps share the same structural properties as any target backend pipeline:

- Each step has defined **inputs and outputs**
- Each step has a **parameter surface** (model, temperature, max_tokens, etc.)
- Each step involves **LLM calls** with specific prompts
- Steps form a **loop topology** with conditional escalation

Modeling the optimizer using the same `PipelineSchema`/`PipelineStep` architecture solves three problems by design:

1. **Tracing** — Optimizer steps get the same Langfuse tracing infrastructure. `critique_text`, `thinking_styles`, L2/L3 transition rationale, escalation signals, and meta-prompts are captured per step.
2. **Reproducibility** — Every meta-optimizer decision traced with full I/O. Given a trial JSON, you can reconstruct every LLM call.
3. **Self-optimization** — A meta-PromptPotter instance can optimize the optimizer's own prompts via `GET /optimizer/pipeline`. L4 completes the escalation hierarchy.

---

## The Tracing Gap

Artifacts not persisted (lost after each cycle):

| Artifact | Where it lives | Persistence |
|----------|---------------|-------------|
| `critique_text` | `_LoopState.critique_text` | Memory only — overwritten each round |
| `thinking_styles` | `_LoopState.thinking_styles` | Memory only — resampled each round |
| `plan` | `PromptState.plan` | Buried in prompt_state, not indexed |
| `context` | `PromptState.context` | Buried in prompt_state, not indexed |
| `parameters` | `PromptState.parameters` | Buried in prompt_state, not indexed |
| Escalation signals | `EscalationSignal` in round result | Only in `CycleRoundResult`, not in trial JSON |
| L2 transition rationale | `refine_context()` LLM response | Only derived PromptState kept |
| L3 transition rationale | `modify_plan()` LLM response | Only derived PromptState kept |
| L2/L3 transition inputs | stalled_rounds / l2_history | Not persisted |
| Candidate generation prompt | `_build_*_meta_prompt()` | Not logged |
| Scan context enrichment | `prepare_scan_context()` output | Lost after feeding to meta-prompt |

---

## Scope Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| **4 steps, not 8** | `l1_generate`, `l1_evaluate`, `l2_refine_context`, `l3_modify_plan` | Critique and thinking style sampling are sub-tools of `l1_evaluate`. L4 (meta-optimization) is a conceptual future extension, not a counted pipeline step. |
| **Critique as tool, not node** | `CritiqueAgent.run()` is a tool of `l1_evaluate` | Its output feeds the *next* round's generation — part of eval's output contract. |
| **Init = naked l1_generate** | `InitNode` runs `restructure_context()` — same decomposition, no loop | Same node, simpler mode (no critique, no thinking styles, no scan context). |
| **Loop topology, not linear** | Cyclic graph with conditional escalation | `PipelineSchema` describes steps within the loop; the loop itself is the orchestrator. |
| **Escalation as pluggable mechanism** | `EscalationCheck` evaluated after each candidate | Degradation is one signal; error rate, latency, user judgment are others. Each check routes to a target (`l3`, `l2`, `abort`). Hardcoded conditions don't scale. |
| **Optimizer state as first-class objects** | `plan`, `context`, `critique` visible and overridable | The user must see what steers the optimizer and be able to intervene. |
| **Schema describes steps, not orchestration** | `OPTIMIZER_PIPELINE_SCHEMA` describes capabilities | Loop control (patience, max_rounds, stall detection) stays in `feedback_cycle.py`. |

---

## The 5 Optimizer Pipeline Steps

### Step Table

| Step | Purpose | Node class | Service function | Module |
|------|---------|-----------|------------------|--------|
| `l1_generate` | Candidate generation (also init mode) | `L1GenerateNode` | `generate_candidates()` | `prompt_optimizer.py` |
| `l1_evaluate` | Eval + winner selection + critique + style sampling | `L1EvaluateNode` | `evaluate_and_select_winner()` | `prompt_optimizer.py` |
| `l2_refine_context` | Context/parameter tuning on L1 stall | `L2RefineNode` | `refine_context()` | `campaign/layer_transitions.py` |
| `l3_modify_plan` | Strategic replanning on L2 stall or escalation | `L3ModifyPlanNode` | `modify_plan()` | `campaign/layer_transitions.py` |
| `init` | Context decomposition into baseline PromptState | `InitNode` | `restructure_context()` | `search/context.py` |
| `l4_meta_optimize` | Meta-optimization (optimizer self-improvement) | `L4MetaNode` (future) | (future) | `campaign/meta_optimize.py` (future) |

All node classes live in `api/nodes/optimizer_nodes.py` and extend `NodeBase[TInput, TOutput]` from `api/nodes/base.py`.

### Input/Output Schemas

```
l1_generate
├── Input
│   ├── prompt_state: PromptState          # current best prompt (incl. plan, context, parameters)
│   ├── accuracy: float                    # current accuracy
│   ├── results: list[dict]               # previous eval results (for failure analysis)
│   ├── critique_text: str                 # from previous l1_evaluate (empty on init)
│   ├── thinking_styles: list[str]         # sampled mutation guidance
│   └── scan_context: dict | None          # scan analytics (optional)
├── Output
│   ├── candidates: list[dict]             # N candidate PromptStates
│   ├── n_generated: int
│   └── meta_prompt: str                   # CAPTURED: full LLM prompt used
└── Sub-tools
    └── scan_context_enrichment            # prepare_scan_context() (optional)

l1_evaluate
├── Input
│   ├── candidates: list[dict]             # from l1_generate
│   ├── eval_data: list[dict]              # evaluation dataset
│   ├── current_best: dict                 # {accuracy, composite, prompt_state, results}
│   ├── improvement_threshold: float
│   └── escalation_checks: list[EscalationCheck]
├── Output
│   ├── winner_prompt_state: dict
│   ├── winner_accuracy: float
│   ├── winner_composite: float
│   ├── improved: bool
│   ├── next_action: str                   # routing decision (see next_action routing)
│   ├── candidate_scores: list[dict]       # enriched: errors, token_recall, degraded_queries
│   ├── escalation_signal: EscalationSignal | None
│   ├── critique_text: str                 # CAPTURED: failure/success analysis
│   ├── thinking_styles: list[str]         # CAPTURED: sampled styles for next round
│   └── winner_results: list[dict]
└── Sub-tools
    ├── EscalationCheck.evaluate()         # pluggable mid-eval checks
    ├── CritiqueAgent.run()                # failure analysis (skipped on escalation)
    └── sample_thinking_styles()           # mutation guidance (skipped on escalation)

l2_refine_context
├── Input
│   ├── prompt_state: PromptState          # current prompt at stall
│   ├── stalled_rounds: list[dict]         # recent non-improving rounds
│   ├── eval_data: list[dict]
│   ├── pipeline_params: dict | None
│   ├── pipeline_schema: PipelineSchema | None
│   └── escalation_signal: EscalationSignal | None
├── Output
│   ├── transition_result: TransitionResult
│   ├── rationale: str                     # CAPTURED: LLM reasoning
│   └── input_summary: str                 # CAPTURED: what was fed to LLM
└── Trigger
    ├── L1 patience exhausted (stall_count >= patience)
    └── EscalationCheck fires with target="l2"

l3_modify_plan
├── Input
│   ├── prompt_state: PromptState          # current prompt at L2 stall or escalation
│   ├── l2_history: list[dict]             # L2 round summaries
│   ├── eval_data: list[dict]
│   ├── pipeline_params: dict | None
│   ├── pipeline_schema: PipelineSchema | None
│   └── escalation_signal: EscalationSignal | None
├── Output
│   ├── transition_result: TransitionResult
│   ├── rationale: str                     # CAPTURED: LLM reasoning
│   └── input_summary: str                 # CAPTURED: what was fed to LLM
└── Trigger
    ├── L2 patience exhausted (l2_stall_count >= l2_patience)
    └── EscalationCheck fires with target="l3" (bypasses L2)

l4_meta_optimize
├── Input
│   ├── optimizer_pipeline_schema: PipelineSchema
│   ├── campaign_history: list[CycleResult]
│   ├── l3_stall_history: list[dict]
│   └── current_optimizer_config: CycleConfig
├── Output
│   ├── optimized_prompts: dict[str, str]  # step_name → improved prompt template
│   ├── optimized_params: dict[str, Any]   # param_name → new value
│   ├── rationale: str                     # CAPTURED: LLM reasoning
│   └── meta_eval_results: dict            # convergence speed, accuracy deltas
└── Trigger
    └── L3 patience exhausted — OR manual invocation
```

### `next_action` Routing

| Value | Trigger | Effect |
|-------|---------|--------|
| `generate` | Default | Continue L1. Patience increments if `improved=False`; patience exhaustion triggers L2 via orchestrator. |
| `stop` | Suggestion analysis | Stop cycle. |
| `l3` | `EscalationCheck(target="l3")` | Abort remaining candidates, skip critique, invoke `modify_plan()` directly (bypass L2). `EscalationSignal.context` forwarded to LLM. Falls back to `abort` if `enable_l3=False`. |
| `l2` | `EscalationCheck(target="l2")` | Same abort behavior, routes to `refine_context()`. For problems addressable by parameter tuning. Falls back to `abort` if `enable_l2=False`. |
| `abort` | `EscalationCheck(target="abort")` | Stop cycle immediately. Unrecoverable (e.g., backend down). `stop_reason="escalation_{check_name}"`. |

Escalation actions bypass patience — they are immediate signals, not gradual stalls.

### Parameter Surface per Step

| Step | Parameter | Default | Notes |
|------|-----------|---------|-------|
| **l1_generate** | `model` | from config | LLM model for meta-prompt |
| | `creativity` (temperature) | 0.7 | Meta-prompt generation temperature |
| | `n_variants` | 5 | Number of candidates to generate |
| | `max_tokens` | 8192 | Meta-prompt response limit |
| | `variant_library` | from config | Field constraint options |
| | `scan_context` | None | Scan analytics for enrichment |
| **l1_evaluate** | `model` | from config | LLM model for eval orchestration |
| | `temperature` | 0.0 | Content hash temperature |
| | `improvement_threshold` | 0.01 | Min accuracy delta to accept |
| | `escalation_checks` | `[DegradationCheck()]` | Pluggable mid-eval checks |
| | critique: `model` | from config | Critique agent LLM |
| | critique: `temperature` | 0.3 | Critique LLM temperature |
| | critique: `max_tokens` | 2048 | Critique response limit |
| | critique: `critique_positive_threshold` | 0.7 | Positive vs negative critique routing |
| | thinking_styles: `n` | 3 | Number of styles to sample |
| | thinking_styles: `seed` | from config | Deterministic sampling |
| **l2_refine_context** | `model` | from config | L2 transition LLM |
| | `temperature` | 0.3 | L2 LLM temperature |
| | `max_tokens` | 2048 | L2 response limit |
| | `pipeline_schema` | None | Enables pipeline_params adjustment |
| **l3_modify_plan** | `model` | from config | L3 transition LLM |
| | `temperature` | 0.5 | L3 LLM temperature |
| | `max_tokens` | 2048 | L3 response limit |
| | `pipeline_schema` | None | Enables pipeline_params adjustment |
| | `escalation_context` | None | `EscalationSignal.context` when triggered by check |
| **l4_meta_optimize** | `model` | from config | Meta-optimizer LLM |
| | `temperature` | 0.5 | Meta-optimization temperature |
| | `max_tokens` | 4096 | Meta-optimization response limit |
| | `meta_eval_budget` | 3 | Campaigns to run for meta-evaluation |
| | `target_metric` | "convergence_speed" | What to optimize for |

### Prompt Locations

| Step | Prompt built in | Template location |
|------|----------------|-------------------|
| `l1_generate` | `_build_constrained_meta_prompt()` | `api/services/prompt_optimizer.py` |
| `l1_evaluate` (critique) | `CritiqueAgent._negative_critique()` / `._positive_critique()` | `api/services/campaign/critique.py` |
| `l2_refine_context` | `refine_context()` inline prompt | `api/services/campaign/layer_transitions.py` |
| `l3_modify_plan` | `modify_plan()` inline prompt | `api/services/campaign/layer_transitions.py` |
| `l4_meta_optimize` | (future) | `api/services/campaign/meta_optimize.py` (future) |

### Observability Events

| Step | Node class | PhaseEvent (display) | Langfuse observation (tracing) |
|------|-----------|---------------------|-------------------------------|
| `l1_generate` | `L1GenerateNode` | `PhaseEvent("l1_generate", ...)` | `generation` — meta-prompt I/O via `NodeBase.process()` |
| `l1_evaluate` | `L1EvaluateNode` | `PhaseEvent("l1_evaluate", ...)` | `span` with nested `generation` (critique) via `NodeBase.process()` |
| `l2_refine_context` | `L2RefineNode` | `PhaseEvent("refine_context", ...)` | `generation` — rationale via `NodeBase.process()` |
| `l3_modify_plan` | `L3ModifyPlanNode` | `PhaseEvent("modify_plan", ...)` | `generation` — rationale via `NodeBase.process()` |
| escalation | (orchestrator) | `PhaseEvent("escalation", ...)` | metadata on parent span (check_name, target, context) |
| `l4_meta_optimize` | `L4MetaNode` (future) | (future) | `generation` — meta-eval I/O |

---

## Escalation Checks

General mechanism for mid-evaluation escalation. Each check runs after every candidate evaluation. When a check fires, it short-circuits the round and routes to the appropriate escalation target.

### `EscalationCheck` / `EscalationSignal`

```python
@dataclass
class EscalationSignal:
    """Emitted when a check fires during candidate evaluation."""
    check_name: str              # "degradation", "error_rate", "user_abort", ...
    target: str                  # "l3" | "l2" | "abort"
    context: dict                # check-specific data for LLM prompt + tracing
    candidate_idx: int
    candidates_evaluated: int
    candidates_skipped: int

class EscalationCheck(BaseModel):
    """Condition evaluated after each candidate in the eval loop."""
    name: str
    target: str = "l3"
    enabled: bool = True

    def evaluate(self, scores: dict, candidate_idx: int, n_total: int) -> EscalationSignal | None: ...

class DegradationCheck(EscalationCheck):
    """Fire when degraded queries dominate hits."""
    name: str = "degradation"
    threshold: float = 0.3

    def evaluate(self, scores, candidate_idx, n_total):
        degraded = scores.get("degraded_queries", 0)
        total, hits = scores["total"], scores["hits"]
        if total > 0 and degraded / total >= self.threshold and degraded > hits:
            return EscalationSignal(
                check_name=self.name, target=self.target,
                context={"degraded_queries": degraded, "hits": hits, "total": total,
                         "degradation_ratio": round(degraded / total, 3)},
                candidate_idx=candidate_idx,
                candidates_evaluated=candidate_idx + 1,
                candidates_skipped=n_total - candidate_idx - 1)
        return None
```

**Eval loop**: `evaluate_and_select_winner()` receives `escalation_checks`. After each candidate eval, iterates checks; first signal short-circuits and returns `next_action=signal.target`.

**Round loop**: Generic handler routes by `signal.target`:

```python
if round_result.escalation_signal:
    sig = round_result.escalation_signal
    if sig.target == "l3" and config.enable_l3:
        await _escalate_l3(state, config, round_num, eval_data, sig, on_phase)
        continue
    elif sig.target == "l2" and config.enable_l2:
        await _escalate_l2_direct(state, config, round_num, eval_data, sig, on_phase)
        continue
    else:
        stop_reason = f"escalation_{sig.check_name}"
        break
```

**Config**: `CycleConfig.escalation_checks` defaults to `[DegradationCheck()]`. User configures via:
```python
campaign_config["optimization"]["escalation_checks"] = [
    {"name": "degradation", "threshold": 0.25, "target": "l3"},
]
```

**Tracing**: Every escalation emits `PhaseEvent("escalation", "enter", ...)` and serializes `EscalationSignal` into the trial checkpoint.

---

## Optimizer State Control

`plan` (str), `context` (str), `parameters` (dict) on `PromptState` and `critique_text` on `_LoopState` are the optimizer's working memory. The user must be able to see and override them.

### Preflight visibility

```
  OPTIMIZER STATE
  ------------------------------------------------------------------
  Plan:       (none)
  Context:    (empty)
  Parameters: creativity=0.7, n_variants=5
  Critique:   (bootstrap from baseline results)
  Escalation: degradation ≥30% → L3
```

When overrides are set:
```
  Plan:       (override) Focus on material identification...
  Context:    (override) Domain: manufacturing BOM materials...
  Critique:   (override) Copper alloys misclassified...
  Escalation: degradation ≥25% → L3  (modified from default)
```

Full text, not truncated.

### Override mechanism

`CycleConfig` gains `initial_plan`, `initial_context`, `initial_critique`. Applied before the loop starts — `baseline_ps.derive(plan=..., context=...)`, `state.critique_text = ...`.

```python
campaign_config["optimization"]["plan"] = "Focus on material identification..."
campaign_config["optimization"]["context"] = "Domain: manufacturing BOM..."
campaign_config["optimization"]["critique"] = "Copper alloys misclassified..."
```

### Real-time candidate display

The `_on_candidate` callback shows escalation proximity:

```
  ┌─ C1/5 ──────────────────────── 13.3% [3.7%-37.9%] ─┐
  │  2/15 hits  ⚠ 6/15 degraded  vs baseline: -6.7%    │
  │  ⚡ degradation 40% ≥ 30% threshold — ESCALATING    │
  └──────────────────────────────────────────────────────┘
```

---

## OPTIMIZER_PIPELINE_SCHEMA

Formal `PipelineSchema` definition analogous to `TERMNORM_DEFAULT_SCHEMA`:

```python
OPTIMIZER_PIPELINE_SCHEMA = PipelineSchema(
    name="promptpotter_optimizer",
    version="1.0",
    description="PromptPotter 4-step optimizer pipeline",
    required_step="l1_evaluate",
    dataset_name="optimizer_trials",
    steps=[
        PipelineStep(
            name="l1_generate",
            type="generation",
            runtime="frontend",
            node_role="candidate_source",
            description=(
                "LLM-driven candidate generation. Generates N variants using "
                "critique feedback, thinking styles, and optional scan context. "
                "In init mode, performs single-pass restructure_context()."
            ),
            param_keys={
                "model", "creativity", "n_variants",
                "max_tokens", "variant_library", "scan_context",
            },
            langfuse_type="generation",
        ),
        PipelineStep(
            name="l1_evaluate",
            type="span",
            runtime="frontend",
            node_role="ranker",
            description=(
                "Evaluates candidates via backend, selects winner by composite "
                "score, runs escalation checks, critique agent, and thinking "
                "style sampling."
            ),
            param_keys={
                "model", "temperature", "improvement_threshold",
                "escalation_checks",
                "critique_model", "critique_temperature",
                "critique_max_tokens", "critique_positive_threshold",
                "thinking_styles_n", "thinking_styles_seed",
            },
            langfuse_type="span",
        ),
        PipelineStep(
            name="l2_refine_context",
            type="generation",
            runtime="frontend",
            node_role="enricher",
            description=(
                "LLM-driven L2 adjustment when L1 stalls or escalation check "
                "fires with target='l2'. Adjusts parameters, context, and "
                "optionally pipeline_params."
            ),
            param_keys={
                "model", "temperature", "max_tokens", "pipeline_schema",
            },
            langfuse_type="generation",
        ),
        PipelineStep(
            name="l3_modify_plan",
            type="generation",
            runtime="frontend",
            node_role="enricher",
            description=(
                "LLM-driven L3 strategic replanning when L2 stalls or "
                "escalation check fires with target='l3' (bypasses L2). "
                "Receives escalation context for targeted guidance."
            ),
            param_keys={
                "model", "temperature", "max_tokens",
                "pipeline_schema", "escalation_context",
            },
            langfuse_type="generation",
        ),
        PipelineStep(
            name="l4_meta_optimize",
            type="generation",
            runtime="frontend",
            node_role="enricher",
            description=(
                "Meta-optimization: optimize the optimizer's own prompts "
                "and parameters by running meta-evaluation campaigns."
            ),
            param_keys={
                "model", "temperature", "max_tokens",
                "meta_eval_budget", "target_metric",
            },
            langfuse_type="generation",
        ),
    ],
)
```

**Design notes:**

- `runtime="frontend"` — the optimizer runs locally, not on a backend server.
- `node_role`: `l1_generate` = `candidate_source`, `l1_evaluate` = `ranker`, L2/L3/L4 = `enricher`.
- `required_step` is `l1_evaluate` — every round must evaluate candidates.
- Schema describes capabilities, not orchestration. Loop control remains in `feedback_cycle.py`.

---

## Node Architecture

Each optimizer step is a `NodeBase[TInput, TOutput]` subclass in `api/nodes/optimizer_nodes.py`. Nodes are the **canonical execution boundary** — the orchestrator (`feedback_cycle.py`) calls `node.process()`, not raw service functions. This gives every step typed I/O, automatic timing, and a single hook point for tracing.

### Design Principles

1. **Node = traced execution boundary.** `NodeBase.process()` validates input, runs `_execute()`, validates output, and captures `NodeMetrics` (timing, tokens, errors). Tracing hooks attach here — one integration point, all steps covered.
2. **Orchestrator owns control flow.** Patience, stall detection, stop conditions, escalation routing — all stay in `feedback_cycle.py`. Nodes are stateless step executors.
3. **Node output = artifact contract.** The output model defines what gets traced and persisted. No separate artifact assembly — the typed output is the single source of truth.
4. **Service functions remain.** `generate_candidates()`, `evaluate_and_select_winner()`, `refine_context()`, `modify_plan()` stay as implementation. Nodes are thin wrappers that add typed I/O and tracing. The service functions are the testable core; nodes add the execution boundary.

### Node Roster

| Node class | Step | Input model | Output model | Service function |
|-----------|------|------------|-------------|-----------------|
| `InitNode` | `init` | `InitNodeInput` | `InitNodeOutput` | `restructure_context()` |
| `L1GenerateNode` | `l1_generate` | `L1GenerateInput` | `L1GenerateOutput` | `generate_candidates()` |
| `L1EvaluateNode` | `l1_evaluate` | `L1EvaluateInput` | `L1EvaluateOutput` | `evaluate_and_select_winner()` |
| `L2RefineNode` | `l2_refine_context` | `L2RefineInput` | `L2RefineOutput` | `refine_context()` |
| `L3ModifyPlanNode` | `l3_modify_plan` | `L3ModifyPlanInput` | `L3ModifyPlanOutput` | `modify_plan()` |

Naming convention: `L{layer}{Verb}Node`. Input/output models use the same prefix without "Node".

### Orchestrator Pattern

```python
# feedback_cycle.py — round loop (simplified)
for round_num in range(max_rounds):
    gen_result = await l1_generate_node.process(L1GenerateInput(
        prompt_state=state.current_sp.prompt_state.model_dump(),
        accuracy=state.current_accuracy,
        results=state.current_results,
        critique_text=state.critique_text,
        thinking_styles=state.thinking_styles,
        scan_context=config.scan_context,
    ))

    eval_result = await l1_evaluate_node.process(L1EvaluateInput(
        candidates=gen_result.candidates,
        eval_data=round_eval_data,
        current_best={...},
        improvement_threshold=config.improvement_threshold,
    ))

    # Orchestrator updates state, checks patience, routes escalation
    state.critique_text = eval_result.critique_text
    state.thinking_styles = eval_result.thinking_styles

    if stalled and config.enable_l2:
        l2_result = await l2_refine_node.process(L2RefineInput(
            prompt_state=state.current_sp.prompt_state.model_dump(),
            stalled_rounds=[...],
            eval_data=round_eval_data,
            pipeline_params=state.current_sp.pipeline_params,
            pipeline_schema=config.pipeline_schema,
        ))

    if l2_stalled and config.enable_l3:
        l3_result = await l3_modify_plan_node.process(L3ModifyPlanInput(...))
```

**Key:** The orchestrator passes critique_text/thinking_styles from one round's `L1EvaluateNode` output to the next round's `L1GenerateNode` input. These are orchestrator-level state, not node-internal state.

### Critique and Thinking Styles

The M8 spec classifies critique and thinking style sampling as **sub-tools of l1_evaluate** (see Input/Output Schemas above). Two viable placements:

**Option A — Inside L1EvaluateNode**: The node calls `CritiqueAgent.run()` and `sample_thinking_styles()` after winner selection. Output includes `critique_text` and `thinking_styles`. The node is a span containing nested generations (eval + critique).

**Option B — Orchestrator calls them separately**: `L1EvaluateNode` only does eval + winner selection. The orchestrator calls critique and style sampling between l1_evaluate exit and the next round's l1_generate enter.

**Decision: Option A.** Critique and style sampling are part of the l1_evaluate step's output contract. Keeping them inside the node means the node's output model is the complete artifact — no orchestrator assembly needed. The Langfuse observation for l1_evaluate is a span with nested generations (one for each candidate eval, one for critique).

### NodeBase → ObsLogger Integration (Future Phase 3)

When tracing is wired, `NodeBase` gains optional observability:

```python
class NodeBase(ABC, Generic[TInput, TOutput]):
    def __init__(self, node_id, config=None, *, obs=None, trace_id=None):
        self.obs = obs            # Optional ObsLogger
        self.trace_id = trace_id  # Active Langfuse trace

    async def process(self, input_data):
        obs_id = self._start_observation(input_data)  # Langfuse span/generation
        try:
            result = await self._execute(validated_input)
            self._end_observation(obs_id, result)
            return result
        except Exception as e:
            self._end_observation(obs_id, error=e)
            raise
```

- **Generation nodes** (l1_generate, l2_refine, l3_modify): Create Langfuse `generation` observations with model, prompt, response, token usage.
- **Span nodes** (l1_evaluate): Create Langfuse `span` observations containing nested `generation` children (one per candidate eval, one for critique).
- **File trace**: `NodeBase._end_observation()` writes `{step_name}.json` to `obs/langfuse/traces/{campaign_id}/round_{N}/`.
- **Opt-in**: When `obs=None`, tracing is skipped. Nodes work identically without observability.

### PhaseEvent Relationship

PhaseEvent remains the **notebook display** mechanism. The orchestrator emits PhaseEvents around `node.process()` calls:

```python
_emit_phase(on_phase, "l1_generate", "enter", round=round_num, ...)
gen_result = await l1_generate_node.process(input)
_emit_phase(on_phase, "l1_generate", "exit", round=round_num, n_candidates=gen_result.n_generated, ...)
```

PhaseEvent carries **display summaries** (accuracy, counts, previews). Node output carries **full artifacts** (meta_prompt, critique_text, rationale). Two separate concerns:
- PhaseEvent → notebook UI (real-time display during optimization)
- Node output → tracing + persistence (post-hoc analysis, reproducibility)

---

## Meta-Experiment Tracing Design

Each optimizer step gets traced through three channels:

### Channel 1: File (ObsLogger)

```
obs/langfuse/traces/{campaign_id}/
  round_000/
    l1_generate.json       # meta-prompt, response, candidates
    l1_evaluate.json       # candidate scores, winner, critique, styles, escalation
  round_001/
    l1_generate.json
    l1_evaluate.json
  round_002/
    l1_generate.json
    l1_evaluate.json
    l2_refine_context.json  # L2 escalation (rationale, input summary)
  ...
```

Each trace JSON captures:
- `input`: Full input to the step (prompt_state, accuracy, eval_data hash, critique, styles)
- `output`: Full output (candidates, winner, critique_text, rationale, escalation_signal)
- `metadata`: Model, temperature, max_tokens, timing, token usage
- `prompt_template`: The actual LLM prompt used (for reproducibility)

### Channel 2: Disk (CampaignStore trial)

```json
{
  "trial_id": "round_002",
  "round": 2,
  "accuracy": 0.85,
  "escalation_signal": {
    "check_name": "degradation",
    "target": "l3",
    "context": {"degraded_queries": 6, "hits": 2, "total": 15, "degradation_ratio": 0.4}
  },
  "optimizer_state": {
    "plan": "Focus on material identification...",
    "context": "Domain: manufacturing BOM...",
    "parameters": {"creativity": 0.7, "n_variants": 5}
  },
  "steps": {
    "l1_generate": {
      "meta_prompt_hash": "abc123",
      "n_candidates": 5,
      "model": "kimi-k2-instruct-0905",
      "temperature": 0.7,
      "token_usage": {"prompt": 1200, "completion": 3400}
    },
    "l1_evaluate": {
      "critique_text": "Failures cluster around multi-word compounds...",
      "thinking_styles": ["chain-of-thought", "analogical reasoning"],
      "winner_composite": 0.87,
      "escalation_checks_evaluated": ["degradation"]
    },
    "l2_refine_context": {
      "rationale": "Adding domain-specific compound terminology...",
      "param_changes": {"context": "...", "n_variants": 7},
      "pipeline_params_changed": true
    }
  }
}
```

### Channel 3: Cloud (Langfuse)

One Langfuse trace per optimization round, with observations per step:

```
Trace: campaign_abc/round_002
├── Generation: l1_generate
│   ├── input: meta-prompt (with critique + styles)
│   ├── output: 5 candidates (changes_description summaries)
│   ├── model: kimi-k2-instruct-0905
│   └── metadata: {temperature: 0.7, n_variants: 5}
├── Span: l1_evaluate
│   ├── Generation: critique_agent
│   │   ├── input: winner_results + accuracy
│   │   └── output: critique_text
│   ├── metadata: {winner_accuracy: 0.85, improved: true, escalation: "degradation→l3"}
│   └── output: {winner, candidate_scores, thinking_styles}
└── Generation: l3_modify_plan (escalation-triggered)
    ├── input: l2_history + eval_data + escalation_context
    ├── output: TransitionResult (rationale + changes)
    └── model: kimi-k2-instruct-0905
```

---

## Reproducibility Contract

Given a trial JSON with step-level artifacts, reconstruct each LLM call:

| To reproduce... | You need | From |
|-----------------|----------|------|
| `l1_generate` LLM call | meta-prompt (or its hash) + model + temperature | trial.steps.l1_generate |
| Critique LLM call | winner_results + accuracy + critique type | trial.steps.l1_evaluate |
| `l2_refine_context` LLM call | stalled_rounds + eval_data + pipeline_section | trial.steps.l2_refine_context |
| `l3_modify_plan` LLM call | l2_history + eval_data + escalation_signal | trial.steps.l3_modify_plan |
| `l4_meta_optimize` LLM call | campaign_history + l3_stall_history + optimizer_config | trial.steps.l4_meta_optimize |
| Exact evaluation results | SearchPoint content hash + eval_data | dataset_runs/{hash}.json |
| Thinking style selection | seed + round_num | Deterministic: `sample_thinking_styles(n=3, seed=config.seed + round_num + 1)` |

**What makes a trial fully reproducible:**

1. **Input data**: eval_data (content-hashed in dataset_runs)
2. **Prompt configuration**: SearchPoint (prompt_state + model + temperature + pipeline_params)
3. **Meta-optimizer configuration**: CycleConfig (all optimizer params + escalation_checks)
4. **Step-level artifacts**: meta-prompt, critique_text, thinking_styles, L2/L3 rationale, escalation signals
5. **Random seed**: deterministic style sampling, query subsampling

---

## L4: Meta-Optimization

L4 is the top of the escalation hierarchy: L1 stalls → L2, L2 stalls → L3, L3 stalls → L4. When L3 can no longer improve performance, L4 optimizes the optimizer itself. The optimizer is a pipeline. PromptPotter optimizes pipelines. Therefore, PromptPotter can optimize itself.

### The Meta-Loop

```
Meta-PromptPotter (L4)
├── GET /optimizer/pipeline          # discover optimizer's 4 steps (+ L4 future)
├── Sensitivity Scan                 # which optimizer params matter?
│   ├── l1_generate.creativity       # does generation temperature affect quality?
│   ├── l1_evaluate.critique_model   # does critique model affect convergence?
│   └── l2_refine_context.temperature
└── Feedback Cycle                   # optimize optimizer prompts
    ├── Candidates: l1_generate meta-prompt variants
    ├── Eval: run target optimization, measure convergence speed
    └── Iterate: critique why some meta-prompts converge faster
```

### Three Requirements for L4

**1. Pipeline discovery**: `GET /optimizer/pipeline` returns `OPTIMIZER_PIPELINE_SCHEMA`.

**2. Optimizable prompts**: Optimizer LLM prompts extracted to overridable fields (prompt registry or configurable templates via step configs).

**3. Meta-evaluation**: Fitness function for optimizer performance:
- **Convergence speed**: rounds to reach target accuracy (fewer = better)
- **Final accuracy**: best accuracy achieved within budget
- **Composite**: weighted combination
- **Eval cost**: total LLM tokens consumed

### Bootstrap Problem

1. Start with hand-tuned optimizer prompts (current state)
2. Run target optimization campaigns to establish baseline convergence
3. Run meta-optimization using those baselines
4. Validate on held-out targets

Viable because optimizer prompts are domain-general — they don't need re-optimization per target pipeline.

---

## Migration Path

### Phase 0: Boundary cleanup + terminology ✅

- Renamed phase events: `"growth"` → `"l1_generate"`, `"analysis_eval"` → `"l1_evaluate"`
- Split `_escalate_l2()` into `_do_l2_transition()` + `_do_l3_transition()` + thin dispatcher
- Stopped `_evaluate_candidates()` from mutating state — returns critique/styles as values
- Made L2 meta-param override explicit (`n_variants`/`creativity` resolved in `_execute_round()`)

### Phase 0.5: Node alignment

- ~~Rename `GrowFilterNode` → `L1GenerateNode`, `AnalysisEvalNode` → `L1EvaluateNode`~~ ✅
- ~~Rename I/O models to match (`L1GenerateInput`/`Output`, `L1EvaluateInput`/`Output`)~~ ✅
- Add `L2RefineNode` and `L3ModifyPlanNode` with typed I/O
- Wire `feedback_cycle.py` to call `node.process()` instead of raw service functions
- Orchestrator state management (patience, escalation) unchanged

### Phase 1: Escalation checks + optimizer state control

- `EscalationCheck`/`EscalationSignal` mechanism in `models.py`
- `DegradationCheck` as first concrete check
- Eval loop integration (`evaluate_and_select_winner`)
- Round loop generic handler (`_execute_round`, `run_feedback_cycle`)
- Optimizer state overrides (`initial_plan`/`initial_context`/`initial_critique` on CycleConfig)
- Preflight "OPTIMIZER STATE" section with full text + escalation config
- Critique/thinking-style steps skipped when escalation fires

### Phase 2: Schema + artifact capture

- Define `OPTIMIZER_PIPELINE_SCHEMA` in `pipeline_discovery.py`
- `GET /optimizer/pipeline` REST endpoint
- Node output models = artifact contracts — persist `node.process()` outputs to trial JSON
- No separate artifact assembly; `CycleRoundResult.steps` maps step name → node output dict

### Phase 3: Trace

- `NodeBase` gains optional `obs: ObsLogger` + `trace_id`
- `process()` auto-creates Langfuse observation (generation or span) with full I/O
- File trace per step: `obs/langfuse/traces/{campaign_id}/round_{N}/{step_name}.json`
- PhaseEvent remains for notebook display (separate concern)

### Phase 4: L4 Implementation

- Extract optimizer prompts to configurable registry
- Meta-evaluation function (convergence speed + final accuracy)
- `OptimizerConnector` implementing `ConnectorProtocol` (M7 dependency)
- Wire sensitivity scan + feedback cycle on optimizer prompts

---

## Work Packages

| ID | Work Package | Sessions | Depends on | Description |
|----|-------------|:--------:|------------|-------------|
| 8.0 | Boundary cleanup + terminology | 1 | M6 exit | Phase renames, `_do_l2/_do_l3` split, eval state returns, L2 meta-param resolution. **✅ Done.** |
| 8.0.5a | Node rename + L2/L3 nodes | 1 | 8.0 | ~~Rename nodes~~ ✅. Add `L2RefineNode`, `L3ModifyPlanNode` with typed I/O. |
| 8.0.5b | Orchestrator → nodes | 1 | 8.0.5a | Wire `feedback_cycle.py` to call `node.process()` instead of raw service functions. |
| 8.1 | Escalation checks + state control | 1 | 8.0.5b | `EscalationCheck`/`EscalationSignal`, `DegradationCheck`, optimizer state overrides, preflight visibility |
| 8.2 | Schema + artifact capture | 1 | 8.1 | `OPTIMIZER_PIPELINE_SCHEMA`, `GET /optimizer/pipeline`, node outputs persisted as trial step artifacts |
| 8.3 | Optimizer step tracing | 1 | 8.2 | `NodeBase` → ObsLogger integration, Langfuse observations per step, file traces |
| 8.4 | Prompt externalization | 1 | 8.2 | Extract optimizer prompts to configurable templates |
| 8.5 | Meta-evaluation function | 1 | 8.3 | Convergence speed + final accuracy fitness function |
| 8.6 | L4 meta-optimization | 2 | 8.4, 8.5 | L4 escalation, optimizer prompt optimization loop, l4_patience |
| 8.7 | Self-optimization integration | 1 | 8.6, M7 | `OptimizerConnector` + sensitivity scan on optimizer params |

### Reading list per work package

| WP | Read first |
|----|-----------|
| 8.0.5a | `api/nodes/optimizer_nodes.py` (current nodes), `api/nodes/base.py` (`NodeBase`), M8 spec I/O schemas |
| 8.0.5b | `api/services/campaign/feedback_cycle.py` (`_generate_or_load_candidates`, `_execute_round`, `_escalate_l2`), `api/nodes/optimizer_nodes.py` |
| 8.1 | `api/services/campaign/models.py` (`CycleConfig`, `CycleRoundResult`, `_LoopState`), `api/services/prompt_optimizer.py` (`evaluate_and_select_winner`), `notebooks/_campaign_lib/_optimize.py` (`_print_preflight_sections`) |
| 8.2 | `api/services/pipeline_discovery.py` (`TERMNORM_DEFAULT_SCHEMA`, `_KNOWN_PIPELINES`), `api/services/stores/campaign_store.py` |
| 8.3 | `api/services/obs/observability_logger.py` (`log_round_start`, `log_round_end`), `api/nodes/base.py` (`NodeBase.process`) |
| 8.4 | `api/services/prompt_optimizer.py` (`_build_constrained_meta_prompt`), `api/services/campaign/critique.py`, `api/services/campaign/layer_transitions.py` |
| 8.5 | `api/services/campaign/models.py` (`CycleResult`), `api/services/prompt_eval.py` (`compute_composite_score`) |
| 8.6 | `api/services/campaign/feedback_cycle.py` (L3 stall detection), `api/services/campaign/models.py` (`CycleResult`) |
| 8.7 | `docs/specs/m7-multi-connector.md` (ConnectorProtocol), `api/services/search/smart_search.py` (`sensitivity_scan`) |

---

## Entry Criteria

- M6 exit gate passed (PipelineSchema + composite scoring active)
- All existing tests pass (`pytest -v --tb=short`)
- Feedback cycle operational with critique-guided generation

## Exit Criteria

- **Phase 0:** ✅ Boundary cleanup committed. Phase names aligned to M8 terminology.
- **Phase 0.5:** Orchestrator calls nodes. All 5 nodes (`InitNode`, `L1GenerateNode`, `L1EvaluateNode`, `L2RefineNode`, `L3ModifyPlanNode`) have typed I/O. Existing tests pass through node layer.
- **Phase 1:** Escalation checks fire and route correctly. Optimizer state visible in preflight and overridable.
- **Phase 2:** `OPTIMIZER_PIPELINE_SCHEMA` defined and discoverable via REST. Node outputs persisted as trial step artifacts.
- **Phase 3:** Optimizer steps traced end-to-end in Langfuse via `NodeBase.process()`.
- **Phase 4 (stretch):** Meta-optimization demonstrated on at least one optimizer prompt.

## Test Strategy

| Test | Type | What it verifies |
|------|------|-----------------|
| `test_l1_generate_node` | Unit | `L1GenerateNode.process()` returns `L1GenerateOutput` with candidates |
| `test_l1_evaluate_node` | Unit | `L1EvaluateNode.process()` returns winner + critique + styles |
| `test_l2_refine_node` | Unit | `L2RefineNode.process()` returns `TransitionResult` fields |
| `test_l3_modify_plan_node` | Unit | `L3ModifyPlanNode.process()` returns plan + rationale |
| `test_feedback_cycle_uses_nodes` | Integration | `run_feedback_cycle()` calls node.process(), not raw service fns |
| `test_escalation_triggers_l3` | Unit | `DegradationCheck` fires, round aborts, L3 invoked with signal context |
| `test_escalation_disabled` | Unit | Threshold=1.0 disables check, normal patience flow |
| `test_state_overrides` | Unit | `initial_plan`/`initial_context`/`initial_critique` applied to baseline |
| `test_optimizer_schema` | Unit | `OPTIMIZER_PIPELINE_SCHEMA` has 5 steps with correct names, roles, param_keys |
| `test_trial_step_artifacts` | Unit | Trial JSON contains per-step node output dicts |
| `test_step_tracing` | Integration | Langfuse observations created per step via NodeBase |
| `test_optimizer_pipeline_endpoint` | API | `GET /optimizer/pipeline` returns valid PipelineSchema |
