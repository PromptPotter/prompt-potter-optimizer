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

| Step | Purpose | Current function | Module |
|------|---------|------------------|--------|
| `l1_generate` | Candidate generation (also init mode) | `generate_candidates()` / `restructure_context()` | `prompt_optimizer.py` / `search/context.py` |
| `l1_evaluate` | Eval + winner selection + critique + style sampling | `evaluate_and_select_winner()` | `prompt_optimizer.py` |
| `l2_refine_context` | Context/parameter tuning on L1 stall | `refine_context()` | `campaign/layer_transitions.py` |
| `l3_modify_plan` | Strategic replanning on L2 stall or escalation | `modify_plan()` | `campaign/layer_transitions.py` |
| `l4_meta_optimize` | Meta-optimization (optimizer self-improvement) | (future) | `campaign/meta_optimize.py` (future) |

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

| Step | Current event | Proposed trace observation |
|------|--------------|---------------------------|
| `l1_generate` | `PhaseEvent("l1_generate", ...)` | `generation` observation with full meta-prompt I/O |
| `l1_evaluate` | `PhaseEvent("l1_evaluate", ...)` | `span` with nested `generation` (critique) |
| `l2_refine_context` | `PhaseEvent("refine_context", ...)` | `generation` observation with rationale |
| `l3_modify_plan` | `PhaseEvent("modify_plan", ...)` | `generation` observation with rationale |
| escalation | `PhaseEvent("escalation", ...)` | metadata on parent span (check_name, target, context) |
| `l4_meta_optimize` | (future PhaseEvent) | `generation` observation with meta-eval I/O |

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

### Phase 0: Escalation checks + optimizer state control

- `EscalationCheck`/`EscalationSignal` mechanism in `models.py`
- `DegradationCheck` as first concrete check
- Eval loop integration (`evaluate_and_select_winner`)
- Round loop generic handler (`_execute_round`, `run_feedback_cycle`)
- `_escalate_l3` accepts `EscalationSignal` context; `modify_plan()` receives it for targeted LLM guidance
- Eval path refactoring: inline `_evaluate_candidates` into `_execute_round`, cached scores in `_select_round_winner`, enriched `candidate_scores`
- Optimizer state overrides (`initial_plan`/`initial_context`/`initial_critique` on CycleConfig)
- Preflight "OPTIMIZER STATE" section with full text + escalation config
- Critique/thinking-style steps skipped when escalation fires

### Phase 1: Artifact capture

- Capture `meta_prompt` from `_build_constrained_meta_prompt()` in l1_generate phase data
- Capture `critique_text` and `thinking_styles` in trial checkpoints
- Capture L2/L3 rationale alongside `TransitionResult`
- Capture `EscalationSignal` in trial checkpoints
- Persist to `CampaignStore` trial JSON

### Phase 2: Schema

- Define `OPTIMIZER_PIPELINE_SCHEMA` in `pipeline_discovery.py`
- `GET /optimizer/pipeline` REST endpoint
- Register in `_KNOWN_PIPELINES`

### Phase 3: Trace

- Langfuse observations per optimizer step
- Map `PhaseEvent` → observation lifecycle (enter = start, exit = end)
- Attach meta-prompt, critique, rationale, escalation signals as observation I/O

### Phase 4: L4 Implementation

- Extract optimizer prompts to configurable registry
- Meta-evaluation function (convergence speed + final accuracy)
- `OptimizerConnector` implementing `ConnectorProtocol` (M7 dependency)
- Wire sensitivity scan + feedback cycle on optimizer prompts

---

## Work Packages

| ID | Work Package | Sessions | Depends on | Description |
|----|-------------|:--------:|------------|-------------|
| 8.0 | Escalation checks + state control | 1 | M6 exit | `EscalationCheck`/`EscalationSignal`, `DegradationCheck`, eval path refactoring, optimizer state overrides, preflight visibility |
| 8.1 | Step artifact capture | 1 | 8.0 | Capture meta_prompt, critique, styles, L2/L3 rationale, escalation signals in trial JSON |
| 8.2 | Extended trial persistence | 1 | 8.1 | Per-step artifact dict in `CampaignStore` trial format |
| 8.3 | `OPTIMIZER_PIPELINE_SCHEMA` | 1 | 8.1 | Schema in `pipeline_discovery.py`, `GET /optimizer/pipeline` endpoint |
| 8.4 | Optimizer step tracing | 1 | 8.2, 8.3 | Langfuse observations per step, PhaseEvent → observation lifecycle |
| 8.5 | Prompt externalization | 1 | 8.3 | Extract optimizer prompts to configurable templates |
| 8.6 | Meta-evaluation function | 1 | 8.4 | Convergence speed + final accuracy fitness function |
| 8.7 | L4 meta-optimization | 2 | 8.5, 8.6 | L4 escalation, optimizer prompt optimization loop, l4_patience |
| 8.8 | Self-optimization integration | 1 | 8.7, M7 | `OptimizerConnector` + sensitivity scan on optimizer params |

### Reading list per work package

| WP | Read first |
|----|-----------|
| 8.0 | `api/services/campaign/models.py` (`CycleConfig`, `CycleRoundResult`, `_LoopState`), `api/services/prompt_optimizer.py` (`evaluate_and_select_winner`, `_select_round_winner`), `api/services/campaign/feedback_cycle.py` (`_execute_round`, `_escalate_l2`, `run_feedback_cycle`), `notebooks/_campaign_lib/_optimize.py` (`_print_preflight_sections`) |
| 8.1 | `api/services/campaign/feedback_cycle.py` (`_generate_or_load_candidates`, `_execute_round`, `_escalate_l2`), `api/services/campaign/layer_transitions.py` |
| 8.2 | `api/services/stores/campaign_store.py`, `api/services/campaign/models.py` (`CycleRoundResult`) |
| 8.3 | `api/services/pipeline_discovery.py` (`TERMNORM_DEFAULT_SCHEMA`, `_KNOWN_PIPELINES`), `api/models/pipeline_schema.py` |
| 8.4 | `api/services/obs/observability_logger.py` (`log_round_start`, `log_round_end`), `api/models/phase_event.py` |
| 8.5 | `api/services/prompt_optimizer.py` (`_build_constrained_meta_prompt`), `api/services/campaign/critique.py`, `api/services/campaign/layer_transitions.py` |
| 8.6 | `api/services/campaign/models.py` (`CycleResult`), `api/services/prompt_eval.py` (`compute_composite_score`) |
| 8.7 | `api/services/campaign/feedback_cycle.py` (L3 stall detection), `api/services/campaign/models.py` (`CycleResult`) |
| 8.8 | `docs/specs/m7-multi-connector.md` (ConnectorProtocol), `api/services/search/smart_search.py` (`sensitivity_scan`) |

---

## Entry Criteria

- M6 exit gate passed (PipelineSchema + composite scoring active)
- All existing tests pass (`pytest -v --tb=short`)
- Feedback cycle operational with critique-guided generation

## Exit Criteria

- **Phase 0:** Escalation checks fire and route correctly. Optimizer state visible in preflight and overridable.
- **Phase 1:** All step-level artifacts captured in trial JSON.
- **Phase 2:** `OPTIMIZER_PIPELINE_SCHEMA` defined and discoverable via REST endpoint.
- **Phase 3:** Optimizer steps traced end-to-end in Langfuse with full I/O.
- **Phase 4 (stretch):** Meta-optimization demonstrated on at least one optimizer prompt.

## Test Strategy

| Test | Type | What it verifies |
|------|------|-----------------|
| `test_escalation_triggers_l3` | Unit | `DegradationCheck` fires, round aborts, L3 invoked with signal context |
| `test_escalation_disabled` | Unit | Threshold=1.0 disables check, normal patience flow |
| `test_escalation_no_target` | Unit | Escalation with `enable_l3=False` stops cycle with `escalation_*` reason |
| `test_state_overrides` | Unit | `initial_plan`/`initial_context`/`initial_critique` applied to baseline |
| `test_optimizer_schema` | Unit | `OPTIMIZER_PIPELINE_SCHEMA` has 4 steps with correct names, roles, param_keys |
| `test_trial_step_artifacts` | Unit | Trial JSON contains per-step artifact dicts |
| `test_step_tracing` | Integration | Langfuse observations created per step |
| `test_reproducibility` | Integration | Given trial JSON + eval_data, reconstruct exact LLM calls |
| `test_optimizer_pipeline_endpoint` | API | `GET /optimizer/pipeline` returns valid PipelineSchema |
