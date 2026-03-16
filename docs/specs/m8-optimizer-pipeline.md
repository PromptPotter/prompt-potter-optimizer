# Milestone 8: Optimizer-as-Pipeline

**Version:** 0.1.0
**Date:** 2026-03-16
**Status:** Draft
**Depends on:** [M6 PipelineSchema](m6-pipeline-composability.md), [ADD v0.10.0](add.md)

---

## Context

PromptPotter optimizes workflow pipelines (currently TermNorm's 6-step terminology normalization pipeline). The optimizer itself is a workflow pipeline with 5 LLM-driven steps. Today these steps are implemented as Python functions orchestrated by `feedback_cycle.py`, but they share the same structural properties as the target backend pipeline:

- Each step has defined **inputs and outputs**
- Each step has a **parameter surface** (model, temperature, max_tokens, etc.)
- Each step involves **LLM calls** with specific prompts
- Steps form a **loop topology** with conditional escalation

If the optimizer pipeline is modeled using the same `PipelineSchema`/`PipelineStep` architecture as the target backend, three problems are solved by design:

1. **Tracing** — Optimizer steps get the same Langfuse tracing infrastructure (one trace per optimization round, each step as an observation). Today `critique_text`, `thinking_styles`, L2/L3 transition rationale, and transition inputs are lost after each cycle.
2. **Reproducibility** — Every meta-optimizer decision is traced with full input/output. Given a trial JSON, you can reconstruct every LLM call that produced a given candidate set.
3. **Self-optimization** — A meta-PromptPotter instance can optimize the optimizer's own prompts by treating it as just another pipeline (`GET /optimizer/pipeline`). L4 (meta-optimization) completes the escalation hierarchy by optimizing the optimizer's own prompts, parameters, and scoring functions when L3 stalls.

---

## The Tracing Gap

What is NOT persisted today (lost after each cycle):

| Artifact | Where it lives | Persistence |
|----------|---------------|-------------|
| `critique_text` | `_LoopState.critique_text` | Memory only — overwritten each round |
| `thinking_styles` | `_LoopState.thinking_styles` | Memory only — resampled each round |
| `plan` | `PromptState.plan` dict | Buried in prompt_state, not indexed |
| `context` | `PromptState.context` str | Buried in prompt_state, not indexed |
| `parameters` | `PromptState.parameters` dict | Buried in prompt_state, not indexed |
| L2 transition rationale | `refine_context()` LLM response | LOST — only derived PromptState is kept |
| L2 transition inputs | stalled_rounds fed to LLM | LOST — not persisted anywhere |
| L3 transition rationale | `modify_plan()` LLM response | LOST — only derived PromptState is kept |
| L3 transition inputs | l2_history fed to LLM | LOST — not persisted anywhere |
| Candidate generation prompt | Built in `_build_constrained_meta_prompt()` | LOST — not logged |
| Scan context enrichment | `prepare_scan_context()` output | LOST after feeding to meta-prompt |

These artifacts are essential for understanding *why* the optimizer made specific decisions and for reproducing any optimization run.

---

## Scope Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| **5 steps, not 8** | Model the optimizer as 5 pipeline steps, not one per function call | Critique and thinking style sampling are sub-tools of `l1_evaluate`, not independent pipeline steps. They don't have independent parameter surfaces or routing decisions. L4 (meta-optimization) is a distinct step with its own parameter surface and trigger. |
| **Critique as tool, not node** | `CritiqueAgent.run()` is a tool of `l1_evaluate` | The critique agent runs *within* the evaluation step — its output feeds the *next* round's generation, making it part of eval's output contract. |
| **Init = naked l1_generate** | `InitNode` is `l1_generate` in simplified mode | Init runs `restructure_context()` — the same decomposition used by `generate_candidates()` but without the iterative loop. Same node, simpler mode (no critique, no thinking styles, no scan context). |
| **Loop topology, not linear chain** | Optimizer is a loop with conditional escalation | Unlike TermNorm's linear pipeline, the optimizer is a cyclic graph: L1 loops until stall, escalates to L2, then L3. `PipelineSchema` needs no loop construct — the loop is the orchestrator (`feedback_cycle.py`), and the schema describes the steps within it. |
| **`suggestion_generation` excluded** | Not a pipeline step | Legacy optional feature (`generate_suggestions=False` by default), superseded by critique-guided generation. If re-enabled, it would be a tool of `l1_evaluate`, not a separate step. |
| **Schema describes steps, not orchestration** | `OPTIMIZER_PIPELINE_SCHEMA` describes step capabilities | Loop control (patience, max_rounds, stall detection) stays in `feedback_cycle.py`. The schema describes what each step *does* and what it *needs*, not when it runs. |

---

## The 5 Optimizer Pipeline Steps

### Step Table

| Step | Purpose | Current function | Module |
|------|---------|------------------|--------|
| `l1_generate` | Candidate generation (also init mode) | `generate_candidates()` / `restructure_context()` | `prompt_optimizer.py` / `search/context.py` |
| `l1_evaluate` | Eval + winner selection + critique + style sampling | `evaluate_and_select_winner()` | `prompt_optimizer.py` |
| `l2_refine_context` | Context/parameter tuning on L1 stall | `refine_context()` | `campaign/layer_transitions.py` |
| `l3_modify_plan` | Strategic replanning on L2 stall | `modify_plan()` | `campaign/layer_transitions.py` |
| `l4_meta_optimize` | Meta-optimization (optimizer self-improvement) | (future -- M8 Phase 4) | `campaign/meta_optimize.py` (future) |

### Input/Output Schemas

```
l1_generate
├── Input
│   ├── prompt_state: PromptState          # current best prompt
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
│   └── improvement_threshold: float
├── Output
│   ├── winner_prompt_state: dict
│   ├── winner_accuracy: float
│   ├── winner_composite: float
│   ├── improved: bool
│   ├── next_action: str                   # generate | stop
│   ├── candidate_scores: list[dict]
│   ├── critique_text: str                 # CAPTURED: failure/success analysis
│   ├── thinking_styles: list[str]         # CAPTURED: sampled styles for next round
│   └── winner_results: list[dict]
└── Sub-tools
    ├── CritiqueAgent.run()                # failure analysis
    └── sample_thinking_styles()           # mutation guidance

l2_refine_context
├── Input
│   ├── prompt_state: PromptState          # current prompt at stall
│   ├── stalled_rounds: list[dict]         # recent non-improving rounds
│   ├── eval_data: list[dict]
│   ├── pipeline_params: dict | None       # current pipeline config
│   └── pipeline_schema: PipelineSchema | None
├── Output
│   ├── transition_result: TransitionResult  # new prompt_state + optional pipeline_params
│   ├── rationale: str                     # CAPTURED: LLM reasoning
│   └── input_summary: str                 # CAPTURED: what was fed to LLM
└── Trigger
    └── L1 patience exhausted (stall_count >= patience)

l3_modify_plan
├── Input
│   ├── prompt_state: PromptState          # current prompt at L2 stall
│   ├── l2_history: list[dict]             # L2 round summaries
│   ├── eval_data: list[dict]
│   ├── pipeline_params: dict | None
│   ├── pipeline_schema: PipelineSchema | None
│   └── degradation_context: dict | None   # performance degradation info
├── Output
│   ├── transition_result: TransitionResult  # new plan + optional pipeline_params
│   ├── rationale: str                     # CAPTURED: LLM reasoning
│   └── input_summary: str                 # CAPTURED: what was fed to LLM
└── Trigger
    └── L2 patience exhausted (l2_stall_count >= l2_patience)

l4_meta_optimize
├── Input
│   ├── optimizer_pipeline_schema: PipelineSchema  # the optimizer's own schema
│   ├── campaign_history: list[CycleResult]        # past optimization campaigns
│   ├── l3_stall_history: list[dict]               # L3 rounds that didn't improve
│   └── current_optimizer_config: CycleConfig
├── Output
│   ├── optimized_prompts: dict[str, str]          # step_name → improved prompt template
│   ├── optimized_params: dict[str, Any]           # param_name → new value
│   ├── rationale: str                             # CAPTURED: LLM reasoning
│   └── meta_eval_results: dict                    # convergence speed, accuracy deltas
└── Trigger
    └── L3 patience exhausted (l3_stall_count >= l3_patience) — OR manual invocation
```

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
| | `degradation_abort_threshold` | 0.3 | Stop on severe regression |
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
| | `degradation_context` | None | Performance degradation info |
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
| `l1_generate` | `PhaseEvent("growth", ...)` | `generation` observation with full meta-prompt I/O |
| `l1_evaluate` | `PhaseEvent("analysis_eval", ...)` | `span` with nested `generation` (critique) |
| `l2_refine_context` | `PhaseEvent("refine_context", ...)` | `generation` observation with rationale |
| `l3_modify_plan` | `PhaseEvent("modify_plan", ...)` | `generation` observation with rationale |
| `l4_meta_optimize` | (future PhaseEvent) | `generation` observation with meta-eval I/O |

---

## OPTIMIZER_PIPELINE_SCHEMA

Formal `PipelineSchema` definition analogous to `TERMNORM_DEFAULT_SCHEMA`:

```python
OPTIMIZER_PIPELINE_SCHEMA = PipelineSchema(
    name="promptpotter_optimizer",
    version="1.0",
    description="PromptPotter 5-step optimizer pipeline",
    required_step="l1_evaluate",
    dataset_name="optimizer_trials",
    steps=[
        PipelineStep(
            name="l1_generate",
            type="generation",
            runtime="frontend",       # runs locally, not on a backend
            node_role="candidate_source",
            description=(
                "LLM-driven candidate generation. Decomposes current prompt "
                "into Layer 1 fields and generates N variants using critique "
                "feedback, thinking styles, and optional scan context. "
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
                "Evaluates all candidates via backend, selects winner by "
                "composite score, runs critique agent for failure/success "
                "analysis, and samples thinking styles for next round."
            ),
            param_keys={
                "model", "temperature", "improvement_threshold",
                "degradation_abort_threshold",
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
                "LLM-driven L2 adjustment when L1 stalls. Analyzes failure "
                "patterns and adjusts PromptState parameters, context, and "
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
                "LLM-driven L3 strategic replanning when L2 stalls. Analyzes "
                "why L2 adjustments didn't help, proposes new optimization "
                "strategy, and optionally adjusts pipeline_params."
            ),
            param_keys={
                "model", "temperature", "max_tokens",
                "pipeline_schema", "degradation_context",
            },
            langfuse_type="generation",
        ),
        PipelineStep(
            name="l4_meta_optimize",
            type="generation",
            runtime="frontend",
            node_role="enricher",
            description=(
                "Meta-optimization: when L3 stalls, optimize the optimizer's own "
                "prompts (candidate generation meta-prompt, critique template, "
                "L2/L3 transition prompts) and parameters (creativity, n_variants, "
                "patience) by running meta-evaluation campaigns."
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

- `runtime="frontend"` for all steps — the optimizer runs locally, not on a backend server.
- `node_role` maps to semantic roles: `l1_generate` is `candidate_source` (produces candidates), `l1_evaluate` is `ranker` (selects winner), L2/L3 are `enricher` (add context, no candidates produced).
- `required_step` is `l1_evaluate` — every optimization round must evaluate candidates.
- The schema describes capabilities, not orchestration. Loop control remains in `feedback_cycle.py`.
- `l4_meta_optimize` is `enricher` — it improves the optimizer's own configuration, not the target pipeline directly. It's the recursive closure of the optimization hierarchy.

---

## Meta-Experiment Tracing Design

Each optimizer step gets traced through three channels:

### Channel 1: File (ObsLogger)

```
obs/langfuse/traces/{campaign_id}/
  round_000/
    l1_generate.json       # meta-prompt, response, candidates
    l1_evaluate.json       # candidate scores, winner, critique, styles
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
- `output`: Full output (candidates, winner, critique_text, rationale)
- `metadata`: Model, temperature, max_tokens, timing, token usage
- `prompt_template`: The actual LLM prompt used (for reproducibility)

### Channel 2: Disk (CampaignStore trial)

Extend the existing trial JSON with per-step artifacts:

```json
{
  "trial_id": "round_002",
  "round": 2,
  "accuracy": 0.85,
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
      "winner_composite": 0.87
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
│   ├── metadata: {winner_accuracy: 0.85, improved: true}
│   └── output: {winner, candidate_scores, thinking_styles}
└── Generation: l2_refine_context (conditional)
    ├── input: stalled_rounds + eval_data summary
    ├── output: TransitionResult (rationale + changes)
    └── model: kimi-k2-instruct-0905
```

---

## Reproducibility Contract

Given a campaign trial JSON with step-level artifacts, you can reconstruct each LLM call:

| To reproduce... | You need | From |
|-----------------|----------|------|
| `l1_generate` LLM call | meta-prompt (or its hash) + model + temperature | trial.steps.l1_generate |
| Critique LLM call | winner_results + accuracy + critique type | trial.steps.l1_evaluate |
| `l2_refine_context` LLM call | stalled_rounds + eval_data + pipeline_section | trial.steps.l2_refine_context |
| `l3_modify_plan` LLM call | l2_history + eval_data + degradation_context | trial.steps.l3_modify_plan |
| `l4_meta_optimize` LLM call | campaign_history + l3_stall_history + optimizer_config | trial.steps.l4_meta_optimize |
| Exact evaluation results | SearchPoint content hash + eval_data | dataset_runs/{hash}.json |
| Thinking style selection | seed + round_num | Deterministic: `sample_thinking_styles(n=3, seed=config.seed + round_num + 1)` |

**What makes a trial fully reproducible:**

1. **Input data**: eval_data (content-hashed in dataset_runs)
2. **Prompt configuration**: SearchPoint (prompt_state + model + temperature + pipeline_params)
3. **Meta-optimizer configuration**: CycleConfig (all optimizer params)
4. **Step-level artifacts**: meta-prompt, critique_text, thinking_styles, L2/L3 rationale
5. **Random seed**: deterministic style sampling, query subsampling

---

## L4: Meta-Optimization

L4 is the top of the escalation hierarchy: L1 stalls -> L2, L2 stalls -> L3, L3 stalls -> L4. When L3 can no longer improve performance, L4 optimizes the optimizer itself — its meta-prompts, parameters, and scoring functions. The optimizer is a pipeline. PromptPotter optimizes pipelines. Therefore, PromptPotter can optimize itself.

### The Meta-Loop

```
Meta-PromptPotter (L4)
├── GET /optimizer/pipeline          # discover optimizer's 5 steps
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

**1. Pipeline discovery**: `GET /optimizer/pipeline` returns `OPTIMIZER_PIPELINE_SCHEMA`, exposing the 5 steps, their parameter surfaces, and their prompt templates. This is analogous to `GET /backends/{id}/pipeline` for target backends.

**2. Optimizable prompts**: The optimizer's LLM prompts (meta-prompt for candidate generation, critique prompt, L2/L3 transition prompts) must be accessible as overridable fields, not buried in Python string literals. This requires extracting them into a prompt registry (similar to TermNorm's `logs/prompts/`) or at minimum making them configurable via `OPTIMIZER_PIPELINE_SCHEMA` step configs.

**3. Meta-evaluation**: The meta-optimizer needs a fitness function for optimizer performance. Candidates:
- **Convergence speed**: rounds to reach target accuracy (fewer = better)
- **Final accuracy**: best accuracy achieved within budget
- **Composite**: `convergence_speed_weight * speed + final_accuracy_weight * accuracy`
- **Eval cost**: total LLM tokens consumed (lower = better at same accuracy)

### Bootstrap Problem

L4 requires a working optimizer to optimize the optimizer. This is bootstrapped by:

1. Start with hand-tuned optimizer prompts (current state)
2. Run target optimization campaigns to establish baseline convergence metrics
3. Run meta-optimization on the optimizer prompts using those baselines
4. Validate that meta-optimized prompts improve convergence on held-out targets

This is viable because the optimizer prompts are domain-general (prompt engineering advice, failure analysis, strategic planning) — they don't need to be re-optimized per target pipeline.

---

## Migration Path

### Phase 1: Capture (no schema changes)

Add step-level artifact capture to the existing feedback cycle without introducing `OPTIMIZER_PIPELINE_SCHEMA`.

- Capture `meta_prompt` from `_build_constrained_meta_prompt()` in growth phase data
- Capture `critique_text` and `thinking_styles` in trial checkpoints
- Capture L2/L3 rationale (LLM response text) alongside `TransitionResult`
- Persist all captured artifacts to `CampaignStore` trial JSON

**Files affected:**
- `api/services/campaign/feedback_cycle.py` — add captures to `_generate_or_load_candidates()`, `_evaluate_candidates()`, `_escalate_l2()`
- `api/services/campaign/layer_transitions.py` — return rationale alongside `TransitionResult`
- `api/services/stores/campaign_store.py` — extend trial schema

### Phase 2: Schema

Define `OPTIMIZER_PIPELINE_SCHEMA` and wire it into `pipeline_discovery.py`.

- Add `OPTIMIZER_PIPELINE_SCHEMA` to `pipeline_discovery.py` (alongside `TERMNORM_DEFAULT_SCHEMA`)
- Add `GET /optimizer/pipeline` REST endpoint
- Register optimizer steps in `_KNOWN_PIPELINES`

**Files affected:**
- `api/services/pipeline_discovery.py` — add schema
- `api/routers/` — new endpoint

### Phase 3: Trace

Wire optimizer steps into the Langfuse tracing infrastructure.

- Create Langfuse observations per optimizer step (using the same `ObsLogger` patterns)
- Map `PhaseEvent` emissions to Langfuse observation lifecycle (enter = start, exit = end)
- Attach meta-prompt, critique, rationale as observation I/O

**Files affected:**
- `api/services/obs/observability_logger.py` — add optimizer step tracing methods
- `api/services/campaign/feedback_cycle.py` — wire obs calls at step boundaries

### Phase 4: L4 Implementation

Build the L4 meta-optimization path.

- Extract optimizer prompts from inline strings to a configurable registry
- Implement meta-evaluation function (convergence speed + final accuracy)
- Create `OptimizerConnector` implementing `ConnectorProtocol` (M7 dependency)
- Wire sensitivity scan + feedback cycle to operate on optimizer prompts

**Files affected:**
- `api/services/prompt_optimizer.py` — externalize meta-prompt templates
- `api/services/campaign/critique.py` — externalize critique prompts
- `api/services/campaign/layer_transitions.py` — externalize L2/L3 prompts
- New: meta-evaluation service

---

## Work Packages

| ID | Work Package | Sessions | Depends on | Description |
|----|-------------|:--------:|------------|-------------|
| 8.1 | Step artifact capture | 1 | M6 exit | Capture meta_prompt, critique, styles, L2/L3 rationale in trial JSON. No schema changes. |
| 8.2 | Extended trial persistence | 1 | 8.1 | Extend `CampaignStore` trial format with per-step artifact dict. Update trial checkpoint in `_execute_round()`. |
| 8.3 | `OPTIMIZER_PIPELINE_SCHEMA` | 1 | 8.1 | Define schema in `pipeline_discovery.py`. Add to `_KNOWN_PIPELINES`. Add `GET /optimizer/pipeline` endpoint. |
| 8.4 | Optimizer step tracing | 1 | 8.2, 8.3 | Wire Langfuse observations per step. Map PhaseEvent → observation lifecycle. |
| 8.5 | Prompt externalization | 1 | 8.3 | Extract optimizer prompts from inline strings to configurable templates (prep for self-optimization). |
| 8.6 | Meta-evaluation function | 1 | 8.4 | Implement convergence speed + final accuracy fitness function for optimizer performance. |
| 8.7 | L4 meta-optimization | 2 | 8.5, 8.6 | Implement L4 escalation: meta-evaluation function, optimizer prompt optimization loop, l4_patience config. |
| 8.8 | Self-optimization integration | 1 | 8.7, M7 | `OptimizerConnector` + sensitivity scan on optimizer params + feedback cycle on optimizer prompts. |

### Reading list per work package

| WP | Read first |
|----|-----------|
| 8.1 | `api/services/campaign/feedback_cycle.py` (`_generate_or_load_candidates`, `_evaluate_candidates`, `_escalate_l2`), `api/services/campaign/layer_transitions.py` |
| 8.2 | `api/services/stores/campaign_store.py`, `api/services/campaign/models.py` (`CycleRoundResult`) |
| 8.3 | `api/services/pipeline_discovery.py` (`TERMNORM_DEFAULT_SCHEMA`, `_KNOWN_PIPELINES`), `api/models/pipeline_schema.py` |
| 8.4 | `api/services/obs/observability_logger.py` (`log_round_start`, `log_round_end`), `api/models/phase_event.py` |
| 8.5 | `api/services/prompt_optimizer.py` (`_build_constrained_meta_prompt`), `api/services/campaign/critique.py`, `api/services/campaign/layer_transitions.py` |
| 8.6 | `api/services/campaign/models.py` (`CycleResult`), `api/services/prompt_eval.py` (`compute_composite_score`) |
| 8.7 | `api/services/campaign/feedback_cycle.py` (L3 stall detection), `api/services/campaign/models.py` (`CycleResult`), `api/services/prompt_eval.py` (`compute_composite_score`) |
| 8.8 | `docs/specs/m7-multi-connector.md` (ConnectorProtocol), `api/services/search/smart_search.py` (`sensitivity_scan`) |

---

## Entry Criteria

- M6 exit gate passed (PipelineSchema + composite scoring active)
- All existing tests pass (`pytest -v --tb=short`)
- Feedback cycle operational with critique-guided generation

## Exit Criteria

- **Phase 1 (minimum):** All step-level artifacts captured in trial JSON
- **Phase 2:** `OPTIMIZER_PIPELINE_SCHEMA` defined and discoverable via REST endpoint
- **Phase 3:** Optimizer steps traced end-to-end in Langfuse with full I/O
- **Phase 4 (stretch):** Meta-optimization demonstrated on at least one optimizer prompt

## Test Strategy

| Test | Type | What it verifies |
|------|------|-----------------|
| `test_optimizer_schema` | Unit | `OPTIMIZER_PIPELINE_SCHEMA` has 4 steps with correct names, roles, and param_keys |
| `test_trial_step_artifacts` | Unit | Trial JSON contains per-step artifact dicts (meta_prompt, critique, rationale) |
| `test_step_tracing` | Integration | Langfuse observations created for each optimizer step in a round |
| `test_reproducibility` | Integration | Given trial JSON + eval_data, can reconstruct the exact LLM calls |
| `test_optimizer_pipeline_endpoint` | API | `GET /optimizer/pipeline` returns valid PipelineSchema |
