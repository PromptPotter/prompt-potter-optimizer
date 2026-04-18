# Optimization

```
┌─ ONE ROUND ────────────────────────────────────────────────────────────┐
│                                                                        │
│  L1 GENERATE (LLM)                                                     │
│    in:  critique OR l2_directive (mutual exclusion),                   │
│         task_context, thinking_styles, plan,                          │
│         escalation_journal + warning_inventory (probe rounds only),   │
│         failure_analysis (clustered failure patterns + signals),      │
│         search_memory (failure clusters, top axes, dead queries)      │
│    out: N candidate OptSearchPoints (prompt + pipeline_params)         │
│         ↓                                                              │
│  L1 EVALUATE                                                           │
│    ┌─ Backend /matches ──── per candidate × per query ──────────────┐  │
│    │  in:  query + pipeline_params (per-node overrides)             │  │
│    │  out: ranked_candidates + diagnostics.warnings                 │  │
│    │                                                                │  │
│    │  Stale data protocol (cached degraded queries):                │  │
│    │    rerun → samplescan → sampleswitch (3-step ladder)           │  │
│    │                                                                │  │
│    │  DegradationCheck (per-query):                                 │  │
│    │    degraded_rate >= 0.4? → ABORT + EscalationSignal            │  │
│    └────────────────────────────────────────────────────────────────┘  │
│    Winner selection: best accuracy >= baseline + threshold             │
│         ↓                                                              │
│    ┌─ CRITIQUE (LLM) — every-round intelligence hub ───────────────┐  │
│    │  in:  pipeline_health, rank_analysis, round_evolution,        │  │
│    │       query_categories, failure_details, successes,           │  │
│    │       search_memory (failure clusters, discriminating queries, │  │
│    │         tractability profiles, axis exhaustion, value trends)  │  │
│    │                                                                │  │
│    │  out: { summary, priority_fix, suggested_axes,                │  │
│    │         failure_highlights, positive/negative_critique }       │  │
│    │  (compact form via format_critique_for_prompt: summary,       │  │
│    │   priority_fix, axes, highlights — internal fields omitted)   │  │
│    └────────────────────────────────────────────────────────────────┘  │
│         ↓                                                              │
│  Compact critique → next L1 Generate (or L2 Refine on escalation)     │
│                                                                        │
│  ── ESCALATION (if degradation detected) ──────────────────────────── │
│                                                                        │
│  L2 REFINE CONTEXT (LLM) — escalation-only meta-controller             │
│    in:  critique, prev l2_directive, escalation report                 │
│         (OR warning_inventory when no report), task_context,           │
│         pipeline schema param keys,                                    │
│         round trajectory, failure group × axis insights,              │
│         candidate comparison summary                                   │
│    out: updated task_context + meta-settings (creativity,              │
│         n_variants, sp_budget_ttest)                                  │
│    L2 does NOT set pipeline_params — that's L1's job.                  │
│                                                                        │
│  L3 MODIFY PLAN (LLM) — if L2 stalls                                  │
│    in:  current plan, L2 history, rendered prompt, pipeline section,   │
│         search_memory (axis rankings, bottleneck dist, failure         │
│         clusters, persistent failures)                                 │
│    out: new strategic plan                                             │
└────────────────────────────────────────────────────────────────────────┘
```

## Responsibility Matrix

| Agent | Fires when | Decides | Does NOT decide |
|-------|-----------|---------|-----------------|
| **L1 Generate** | Every round | pipeline_params (query_prefix, max_sites, schema, temperature, ...) | `task_context`, meta-settings |
| **Critique** | Every round | what to focus on (suggested_axes, priority_fix) | pipeline_params values |
| **L2 Refine** | Escalation only (stall, degradation) | `task_context`, meta-settings (creativity, n_variants, sp_budget_ttest), `l2_directive` | pipeline_params |
| **L3 Plan** | L2 stalls | strategic plan | pipeline_params, `task_context` |

---

## 3-Layer Optimization Model

Parameters are organized into three layers with different optimization cadences. The target pipeline snapshot (`show_pipeline_snapshot(svc)`) determines which parameters are available.

### Layer 1: Generate (innermost loop)

Tunable parameters discovered from the target pipeline's active nodes. Changed every round.

| Category | Source | Examples |
|----------|--------|----------|
| Prompt fields | LLM nodes (`llm_ranking`, `entity_profiling`) | `prompt`, `persona`, `task_intent`, `instruction`, `thinking_style`, `answer_format` |
| Model params | Any LLM node | `temperature`, `model`, `max_tokens` |
| Output schema | LLM nodes with structured output | `output_schema` field overrides |
| Pipeline params | Non-LLM nodes (`fuzzy_matching`, `token_matching`) | thresholds, weights, `sp_budget_ttest` |

Which parameters are Layer 1 depends on the target pipeline config — not a fixed list. Prompt fields only affect nodes with a prompt template referencing them (see CLAUDE.md Known Issues for backend-specific constraints). The scan advisor reads the full pipeline snapshot to recommend which axes to optimize.

### Layer 2: Refine Context

Adjusted when Layer 1 improvements stall:

| Field | Purpose |
|-------|---------|
| `optimizer_params` | Meta-settings (creativity, n_variants, sp_budget_ttest, variant_strategy) |
| `task_context` | Structured domain context (domain, pipeline_purpose, data_characteristics, optimization_goals, key_challenges, raw_description). Decomposed from `TASK_DESCRIPTION` at init. L2 can refine individual fields. |

### Layer 3: Modify Plan

Optimization strategy -- rarely changed:

| Field | Purpose |
|-------|---------|
| `plan` | High-level optimization strategy |

`render()` assembles prompt fields into the final rendered prompt. `derive_candidate()` creates child points forming a lineage chain.

### Dynamic Field Set (Design Vision)

Currently fixed (8 fields in `PROMPT_STRING_FIELDS` + `few_shot_examples` + `plan`). Vision: make it open — L2 adds/removes fields to widen or narrow the search space.

```
L2 REFINE ──► add_field("domain_constraints")
              remove_field("persona")
                    │
                    ▼
┌─ PROMPT SCHEME ──────────────────────────┐
│  1. task_intent                          │
│  2. problem_description                  │
│  3. instruction                          │
│  4. thinking_style                       │
│  5. answer_format                        │
│  6. domain_constraints          ← NEW    │
│                                          │
│  +/- [???]                               │
└──────────────────────────────────────────┘
```

Architecturally feasible: `render()` already skips empty fields, `derive_candidate()` iterates a field list. An overflow `dict[str, str]` handles additions without new Pydantic attributes.

---

## Feedback Cycle

Critique-guided optimization with 3-layer escalation, inspired by [PromptWizard](https://arxiv.org/abs/2405.18369)'s critique-and-refine pattern.

```
INIT: configure pipeline (baseline deferred to first optimize round) → bootstrap critique + sample thinking styles
  ↓
ROUND 0: Growth (bootstrap critique + styles) → Eval → critique₀ → winner vs current best
  ↓
ROUND 1: Growth (critique₀ + new styles)      → Eval → critique₁ → winner vs current best
  ...
```

Each round:

1. **Growth** -- Generate N candidates. The meta-prompt includes **critique from previous round** + freshly sampled **thinking styles** as mutation guidance.
2. **Eval + Critique** -- Evaluate candidates via the backend, compare by composite score against the current best (previous winner). Generate a **critique** of remaining failures/successes, fed forward to next round.
3. **Loop control** -- Stall counter on no improvement. Patience exhausted: escalate L2 (`task_context`) then L3 (strategy). Pluggable `EscalationCheck`s can also trigger L2/L3/abort mid-round (e.g., `DegradationCheck` on target pipeline regression). Stop on `max_rounds` or perfect accuracy.

**Breadth over depth.** Failure analysis surfaces 2-3 distinct improvement directions ranked by failure count, not a single dominant fix. L1 receives all directions and generates candidates across them rather than N variations on one theme — this is the primary guard against mode collapse. The single `priority_fix` in critique output is one signal among several; `failure_highlights` and `suggested_axes` carry the breadth.

**Init** configures the pipeline; baseline evaluation is deferred and runs automatically when `optimize` starts. The first critique is bootstrapped from baseline results at that point. When scan data is available (leaderboard, axis sensitivity, query difficulty), it feeds into both the bootstrap critique and subsequent rounds via `prepare_recon_brief()`.

---

## Critique Agent

Failure analysis is **separated from candidate generation** (PromptWizard pattern). Critique is the **every-round intelligence hub** — it runs every round inside L1 Evaluate after backend evaluation and winner selection. It is the **sole reader** of raw eval results AND receives SearchMemory intelligence (failure clusters, discriminating queries, tractability profiles, axis exhaustion, value trends) to frame its analysis. Its output feeds forward to **L1 Generate** (next round) and **L2 Refine** (on escalation). L2 only fires on escalation — critique is the normal-flow intelligence bridge.

### Critique Output

Both positive and negative paths produce the same 6-field JSON:

```json
{
  "positive_critique": "what's working — patterns to extend",
  "negative_critique": "what's failing — root causes and blockers",
  "priority_fix": "single most impactful change to make",
  "suggested_axes": ["query_prefix", "max_sites"],
  "failure_highlights": ["Q→Pred→GT lines (3-5 most actionable)"],
  "summary": "2-3 sentence actionable critique"
}
```

Formatted by `format_critique_for_prompt()` (emits only actionable fields: summary, priority_fix, suggested_axes, failure_highlights — positive/negative_critique stays internal). Injected into:
- **L1 Generate** as `critique_text` — only when no `l2_directive` (mutual exclusion)
- **L2 Refine** as `critique_text` in intelligence sections (always, so L2 can build on it)

### Stat-Rich Analysis

`_assemble_critique_sections()` in `critique.py` builds the stat sections from section helper functions. The critique template (`critique.json`) wraps these sections with persona, task_intent, and answer_format. Critique is the **sole reader** of raw eval results — all other nodes receive its digested output (see [`information-flow.md`](information-flow.md) consumer matrix).

Per-query diagnostics are derived from `PipelineSchema.nodes` via `NODE_TYPE_METRICS` (`pipeline_schema.py`) — a registry mapping node type → metrics → `pipeline_data_key`. Schema-driven, not hardcoded: when the pipeline gains a new node or node type, diagnostics extend automatically without code changes.

| Section | Source |
|---------|--------|
| **Evaluation summary** | `CritiqueContext` + `LoopState` |
| **Anomaly flags** | Computed inline from health/rank/evolution |
| **Pipeline health** | `winner_results.pipeline_data` |
| **Rank analysis** | `winner_results` + `candidate_keys` from schema |
| **Round evolution** | `state.rounds` (`RoundResult` history) |
| **Query categories** | `winner_results.terminated_at` |
| **Failure details** | `winner_results` (8 max, deduped) |
| **Successes** | `winner_results` (2 examples) |
| **Search memory** *(M8)* | `SearchMemory` atomic accessors: failure clusters, discriminating queries, tractability profiles, axis exhaustion, value trends |

### Anomaly Flags

Computed inline from the health, rank, and evolution sections:

| Flag | Fires when | Severity |
|------|-----------|----------|
| `high_degradation` | Degraded query count exceeds threshold | HIGH |
| `near_miss_pattern` | GT in candidates for >30% of misses but not rank 1 | MEDIUM |
| `plateau_signal` | 2+ consecutive rounds with <1% improvement | MEDIUM |

### Pipeline Data Flow

Backend `/matches` returns `diagnostics.warnings[]` per query. A query is **"degraded"** if it has any non-empty warnings list. Each warning carries `{step, code, message}` — classified as **`{step}:{code}`** (e.g., `web_search:partial_scrape`). Flow: `measure_sample()` projects `diagnostics` into `pipeline_data` → `_pipeline_health_section()` → anomaly flags in critique meta-prompt.

---

## Escalation Chain — degradation via the RuntimeFailure rail

When a candidate's evaluation degrades mid-round, the failure is attributed **to that candidate**, not the round, and flows through the rail-2 self-healing pipeline (see "Self-healing optimization — two rails" below for the full two-rail discipline):

```
degraded_rate >= threshold on candidate C_k mid-eval
    ↓
DegradationCheck fires → EscalationSignal(target=ELIMINATE_CANDIDATE)
    ↓
_score_candidates absorbs the signal, synthesises a RuntimeFailure from
check_result + C_k's observed pipeline_params, attaches to
C_k.memory.runtime_failures, includes in C_k's score report, CONTINUES
with the next candidate (round winner is unaffected)
    ↓
End of round: execute_round mirrors every new RuntimeFailure onto
state.opt_sp.memory.runtime_failures (deduped across rounds)
    ↓
L2 Refine next round receives: NEW (this round's candidate_scores) +
ACCUMULATED (outer-memory trail)
    → L2 updates its OWN outputs — directive / task_context /
      optimizer_params — to re-shape L1's search around the safe region
    ↓
Round N+1: pattern reduced? L2 self-healing worked.
           pattern persists? ACCUMULATED list keeps growing.
    ↓
L3 modify_plan (triggered by l2_stall or l3_patience) reads the
cumulative runtime_failures_section from opt_sp.memory and replans —
changes pipeline_params / swaps nodes / rewrites plan text — to escape
the failing region entirely.
```

Degradation is no longer a round-level escalation — the round never aborts for a single candidate's runtime issues, and `max_rounds` is never skipped for it. The only round-level escalation paths left are `ABORT_CAMPAIGN` (true catastrophic degradation that L2/L3 cannot rescue) and the normal patience-exhaustion path when successive rounds don't improve.

---

## OptimizationMemory state

`OptSearchPoint.memory` (an `OptimizationMemory` Pydantic submodel in `domain/opt_search_point.py`) bundles the cross-round optimizer state that travels with each candidate. Every field is persisted with the round trial JSON; lifecycles vary:

| Field | Lifecycle | Purpose |
|---|---|---|
| `critique_text` | per-round, cleared on improvement | Critique node's narrative for next L1 |
| `thinking_styles` | per-round | 2-3 strategy hints sampled into the meta-prompt |
| `escalation_journal` | cross-round, append-only | History of degradation events with outcomes |
| `warning_inventory` | cross-round | Per-query warning aggregation |
| `l2_directive` | one-round window, cleared on improvement | L2's diagnostic + action guidance for L1 |
| `degradation_reset_count` | cross-round | How many times L2/L3 patience exhausted |
| `backend_warning_emitted` | one-shot | Backend-warning emission flag |
| `validation_failures` | per-candidate (set at L1 parse time) | Parse-time invariant violations — rail 1 (L1 self-healing via L2 directive). See below |
| `runtime_failures` | per-candidate + cumulative outer-memory mirror | Runtime-observed health failures — rail 2 (L2 self-healing, L3 escalation). Candidate-level copies set in `_score_candidates`; outer-level accumulated in `execute_round` after the round; cleared never (they represent discovered runtime constraints, not one-round guidance). |

---

## Self-healing optimization — two rails

Failures attach to the **candidate that produced them** (per-candidate `OptSearchPoint.memory`), never to the round, so a losing candidate's problem never disrupts the round winner. Two rails exist; new mechanisms must pick one — **do not invent a sidecar, do not silently drop, do not just log.**

|  | **Rail 1 — `ValidationFailure`** | **Rail 2 — `RuntimeFailure`** |
|---|---|---|
| Detected | L1 parse time (before backend) | Mid-evaluation (after backend) |
| Example | `model: gpt-4o` when allowed = `[gpt-oss-120b]` | `max_tokens=150` → 100% `empty_content_reasoning_fallback` on reasoning model |
| Who made the mistake | L1 (tactical — picked a disallowed value) | Nobody tactically (L1's value was in range; the *strategic shape* of the search didn't account for the runtime constraint) |
| Score effect | Synthetic 0 (zero backend calls) | Real score stands (candidate is eliminated mid-eval) |
| Per-candidate memory | `memory.validation_failures` | `memory.runtime_failures` |
| Outer-memory mirror | None — L2 reads from `candidate_scores` only | Cumulative `state.opt_sp.memory.runtime_failures` (every round's new failures deduped and appended) |
| Healer | **L2 teaches L1** via a directive (`"use ONLY one of: …"`) | **L2 heals itself** — updates its own directive / `task_context` / `optimizer_params` to re-shape what L1 is allowed to search over |
| Escalation | None (L2 can always articulate the constraint) | **L3** `modify_plan` — when the `ACCUMULATED` list keeps growing despite L2's adjustments, L3 reads the trail and replans (change `pipeline_params`, swap nodes, rewrite `plan` text) |

Both rails share the rule *"detect → trace on the candidate → surface on `candidate_scores` → feed the right teacher"* but diverge on **who the teacher is** and **what the healing action looks like**.

### Rail 1 — `ValidationFailure` (L1 self-healing via L2 directive)

Some L1-generated candidates are invalid before evaluation starts — the optimizer LLM hallucinated a value outside the user-declared allowed set. Canonical example: `pipeline_params_override.llm_only.model = "gpt-4o"` when the backend only has `openai/gpt-oss-120b` per `PipelineSchema.available_models`.

`validate_overrides()` in `application/optimization/nodes/generate.py` attaches a `ValidationFailure(axis, value, allowed, reason)` (from `domain/analysis.py`) to `OptSearchPoint.memory.validation_failures` at L1 parse time. This is **outer-layer optimizer state** — it lives on the optimizer trace alongside `critique_text`, `l2_directive`, and `escalation_journal`. The target-layer `JobSearchPoint` is untouched, which is why none of the scoring-layer machinery needs to know about validation failures: `_score_candidates` shortcuts to a synthetic 0 report, the existing accuracy comparator naturally deprioritizes the candidate in `_select_round_winner`, and the round checkpoint persists the failure with the rest of the optimizer memory.

**L2 teaches L1.** L2 `refine_strategy` already reads critique, escalation reports, and the previous directive; validation failures slot into that same context as an "L1 VALIDATION FAILURES" section. L2 produces a directive that names the disallowed value by name (e.g. *"do not propose gpt-4o for model"*), which replaces critique for next round's L1 via the normal directive/critique mutual exclusion. L1 next round follows the directive and heals itself.

Flow: `detect → attach to candidate memory → synthetic-0 → surface on candidate_scores → L2 directive → L1 next round heals`.

### Rail 2 — `RuntimeFailure` (L2 self-healing with L3 escalation)

Some candidates are valid at parse time — every parameter is in the allowed range — but degrade at runtime. Canonical example: `llm_only.max_tokens=150` with `reasoning_effort=medium` on a Groq reasoning model. The model exhausts its reasoning budget before emitting visible content; the backend returns the raw reasoning trace as the answer; 7/7 evaluated queries produce an `llm_only:empty_content_reasoning_fallback` warning. No L1 rule was broken — the *strategic shape* of the search didn't account for the runtime constraint.

`DegradationCheck` in `application/optimization/nodes/escalation.py` fires mid-evaluation when `degraded_rate >= threshold`. Its target is `EscalationTarget.ELIMINATE_CANDIDATE` — the failure is attributed to the **single candidate that produced it**, not the round. `_score_candidates` in `application/optimization/nodes/score.py` synthesises a `RuntimeFailure` (source, dominant_warning, warning_types histogram, degraded_rate/count, observed_config snapshot of the offending node) from the check result and attaches it to that candidate's `OptSearchPoint.memory.runtime_failures`, includes it in the candidate's score report, and continues with the next candidate. The round winner is unaffected by a losing candidate's runtime issues.

**Outer-memory mirror.** After the round completes, `round_execution.execute_round` walks `candidate_scores` and appends every new `RuntimeFailure` to `state.opt_sp.memory.runtime_failures` on the **outer** OptSearchPoint — deduped by `(source, dominant_warning, observed_config)` so recurring patterns don't bloat the list. `LoopState.apply_transition` deep-copies `memory` across L2/L3 transitions, so the cumulative trail follows the optimizer forward automatically. `clear_volatile()` does **not** clear this list — it represents discovered runtime constraints, not one-round guidance.

**L2 heals itself.** L2 `refine_strategy` receives two partitions of the runtime failures for next round's L2 prompt: `NEW (this round)` pulled from `candidate_scores[*].runtime_failures`, and `ACCUMULATED (surviving from earlier rounds despite L2 adjustment)` pulled from `opt_sp.memory.runtime_failures`. The `ACCUMULATED` section is the real signal — if items there survived L2's prior strategy adjustments, L2's last angle didn't work and it must try a different one. L2's job is **not** to parrot "don't use X" to L1 (that's rail 1's pattern) — it must update its own outputs: tighten the directive to name the failing config range, refine `task_context` with the discovered constraint, or adjust `optimizer_params` (`creativity`, `n_variants`) to narrow L1's search around the safe region.

**L3 replans on escalation.** When the `ACCUMULATED` list keeps growing across L2 rounds — i.e. L2's self-healing is running out of runway — `modify_plan` receives the cumulative `runtime_failures_section` and treats those patterns as discovered constraints on the search space. L3's replan must either change `pipeline_params` to escape the failing region (switch model, raise a param floor, swap a node) or change the `plan` text to steer L1/L2 around it. The instruction in `l3_modify_plan.json` is explicit: *"Do not propose a plan that re-enters the same failure mode."*

Flow: `detect → attach per-candidate → real score stands → mirror to outer memory → L2 adjusts own strategy (directive, task_context, optimizer_params) → (if pattern persists across L2 rounds) L3 replans pipeline / plan`.

For the prompt-injection routing (who reads each failure list), see [`information-flow.md`](information-flow.md). For the `⚠ … ↳` rendering convention, see [`display-conventions.md`](display-conventions.md).

### Relationship to the other per-evaluation checks

| Check | Fires | Action | Target enum |
|---|---|---|---|
| **Validation failure** | L1 parse time, before evaluation | Synthetic 0; skip backend. Surface on `OptSearchPoint.memory.validation_failures`; L2 reads via `candidate_scores` next round and teaches L1 via directive. | (no signal — handled in `_score_candidates`) |
| `EliminationCheck` | Mid-evaluation, after `n_min` queries | Stop scoring this candidate (Welch t-test says it can't beat the leader); continue with the next | `EscalationTarget.ELIMINATE_CANDIDATE` |
| `EmptyOutputCheck` | Mid-evaluation, after 3 queries | Stop scoring this candidate; continue with the next | `EscalationTarget.ELIMINATE_CANDIDATE` |
| `DegradationCheck` | Mid-evaluation, after 3 queries | Stop scoring this candidate; synthesise a `RuntimeFailure` and attach to its `OptSearchPoint.memory.runtime_failures`; mirror to outer memory after the round. L2 reads *NEW + ACCUMULATED* next round and adjusts its own strategy; L3 replans when the pattern persists. | `EscalationTarget.ELIMINATE_CANDIDATE` |

Validation is the only one that fires *before* evaluation — it needs nothing but the candidate dict and the schema, which is why it can short-circuit the backend entirely.

### Planned future self-healing mechanisms

New mechanisms land by following one of the two rails, not by inventing parallel machinery:

- **Backend errors naming a parameter and reason** (e.g. `temperature out of range` returned by the backend as a structured error) — rail 1 (`ValidationFailure`): L1 proposed a bad value, L2 teaches L1 via directive. Needs a structured-error parser at the backend client that emits the axis + allowed range.
- **Schema/format violations on structured outputs** — valid JSON, wrong shape. Usually rail 1 (L1 picked a bad format hint) or rail 2 (the schema itself is over-constrained for the current model — L3 replans).
- **Monotonic per-axis degradation with fault attribution** — rail 2 (`RuntimeFailure`): surfaced per-candidate, L2 adjusts `optimizer_params` / `task_context`, L3 eventually changes pipeline composition.
- **Quota / rate-limit exhaustion on a specific node** — rail 2: L2 can't fix quota via directive, but L3 can swap nodes.

`validate_overrides()` today only checks `model` against `PipelineSchema.available_models`; future enum params plug in by extending the validator — `ValidationFailure` is intentionally axis-agnostic. `DegradationCheck` today only looks at warning rate; future health signals plug in by extending `RuntimeFailure` fields or adding sibling checks that emit the same dataclass.

---

## Wiring a New Node

Reference: `web_search`. Default chain works for **any** target pipeline node that emits warnings.

| Step | What | Where | Required? |
|------|------|-------|-----------|
| **1** | Emit `diagnostics.warnings[]` with `{step, code, message}` | Backend | **Yes** |
| **2** | Add routing strategy for `{step}:{code}` | `escalation.py` | No (defaults to L2) |
| **3** | Add anomaly detector | `critique.py` | No |
| **4** | Set `degradation_threshold` | campaign config | **Yes** (0 = disabled) |

Example -- adding `entity_profiling` error detection:

```json
{"step": "entity_profiling", "code": "schema_error", "message": "Failed to parse JSON"}
```

That's it. `DegradationCheck` counts the warning, synthesises a `RuntimeFailure` on the offending candidate, and the round completes normally. L2 reads the failure next round and adjusts its own strategy (directive, task_context, optimizer_params) to steer L1 away from the failing config region. If the pattern persists across L2 rounds, L3 replans. Critique still shows `ANOMALY FLAGS` for the winner's warnings — but the round-level escalation path is gone for per-candidate runtime issues.

---

## Thinking Styles

Each round samples 2-3 styles from the variant library (`promptpotter/config/prompt_variants.json`, 35+ from published research) into the meta-prompt as mutation guidance. Structured diversity beyond temperature randomness.

## SearchMemory Intelligence Feed

Cross-campaign intelligence loaded at cycle init, refreshed before each round. Each consumer receives a tailored subset via builder functions. See [`search-memory-intelligence.md`](search-memory-intelligence.md) for the full design, consumer matrix, and two-tier intelligence architecture.

**Round-boundary dataset mutation — zero-signal filter.** Immediately after `SearchMemory.on_round_complete()`, `campaign/runner.py::_maybe_apply_zero_signal_filter` runs. When enabled (`LoopConfig.zero_signal_filter_enabled`), queries with variance 0 across ≥ `zero_signal_filter_min_observations` samples are physically moved into the dataset's `excluded` sidelist and dropped from the in-memory active list. This is the **only** sanctioned round-boundary mutation of the active dataset; all other intelligence tiers are read-only signals into LLM prompts. See [`search-memory-intelligence.md § Zero-Signal Sample Filtering`](search-memory-intelligence.md).

## Stale Data Load Protocol

When a cached eval result is degraded (non-empty `diagnostics.warnings`), the protocol walks a 3-step ladder:

| Step | Action | Resolves when |
|------|--------|---------------|
| **rerun** | Re-evaluate after `rerun_trigger_count` observations | Fresh result not degraded |
| **samplescan** | Re-evaluate with default params | Default-config not degraded |
| **sampleswitch** | Check SearchMemory degradation rate | Rate exceeds threshold → exclude |

If all steps fail, result is marked `persistently_degraded` and passed through. Observation counts come from `SearchMemory.query_degradation_count`, which is populated at round boundaries by ingesting `dataset_runs/`.

---

## Phase Events

The feedback cycle emits `PhaseEvent` objects at phase boundaries via `on_phase`. The notebook renders these as ANSI-colored banners.

| Phase | Trigger |
|-------|---------|
| `init` | Cycle start |
| `l1_generate` | Candidate generation |
| `l1_evaluate` | Evaluation, winner selection & critique |
| `refine_strategy` | L2 escalation |
| `modify_plan` | L3 escalation |
| `escalation` | `EscalationCheck` fires mid-eval |
| `zero_signal_filter` | Dataset sweep removed always-hit/always-miss queries |

Each event: `phase`, `event` ("enter"/"exit"), `round`, `data` (dict), `timestamp` (ISO 8601). See `RunCallbacks` for the callback interface.

---

## Configuration

```python
campaign_config = {
    "sp_budget_ttest": 35,                    # queries per eval (must be > 0)
    "exclude_nodes": ["llm_ranking"],          # target pipeline nodes to skip
    "optimization": {
        "n_variants": 5,
        "creativity": 0.7,
        "improvement_threshold": 0.01,
        "l1_patience": 3,
        "max_rounds": 10,
        "hard_cap": 100,                       # absolute round limit (safety)
        "max_consecutive_errors": 3,           # abort eval after N backend errors
        "enable_critique": True,               # critique-guided generation
        "degradation_threshold": 0.4,          # 0 = disabled
        "critique_degradation_threshold": 0.4, # critique anomaly flag threshold
        "critique_near_miss_ratio": 0.3,       # GT-in-candidates ratio for near-miss flag
        "enable_l2": True,                     # refine task_context on L1 stall
        "l2_patience": 2,                      # None = unlimited during degradation
        "enable_l3": True,                     # modify plan on L2 stall
        "l3_patience": 1,                      # None = unlimited during degradation
        "stale_data_load_protocol": ["rerun", "samplescan", "sampleswitch"],
        "plan": None,                          # override optimizer strategy (str)
        "context": None,                       # override domain task_context (str)
        "critique": None,                      # override bootstrap critique (str)
    },
    "eval_llm": { ... },
}
```

---

## Troubleshooting

- **Stalls at low accuracy** — Lower `improvement_threshold`, increase `n_variants`, or manually escalate to L2/L3.
- **Generic critique** — Try a more capable `eval_llm.model`, or `enable_critique: False` for direct generation.
- **Low diversity** — Increase `creativity` or `n_variants`.
- **Scan aborted early** — Circuit breaker. See [sensitivity-scan.md](../specs/archive/sensitivity-scan.md#circuit-breaker).

---

## Resuming mid-cycle

`optimize --from <round>` rewinds the active cycle to a specific round boundary and continues from there. It is **not** a new campaign: the same `cycle_id`, the same `campaigns/{cycle_id}/` directory, trial files appended past the rewind point after the next run. Use it when you want to edit the optimizer state by hand (edit `trial_NNNN.json` between runs), discard a bad trajectory, or re-enter HITL review.

### The only snapshot source is `trial_NNNN.json`

Each completed round writes `campaigns/{cycle_id}/trial_{round:04d}.json` via `CampaignStore.add_trial`. That file already carries the full serialized `OptSearchPoint` (`opt_search_point` key), so resume rehydrates the exact optimizer state via `LoopState.restore_from_trial` — no separate write-ahead log. `events.jsonl` is a pure observability mirror parallel to Langfuse; nothing reads it for state reconstruction.

### What `--from N` does

1. `optimize --from N` requires an active cycle on the session (run `optimize` at least once first).
2. `CampaignStore.rewind_to_round(backend_id, cycle_id, N)` moves `trial_{M:04d}.json` and `round_{M:04d}_candidates.json` for every `M > N` into `campaigns/{cycle_id}/archived/resumed_at_<ts>/`, then rebuilds the in-memory trial index (`trials`, `n_trials`, `best_accuracy`, `best_trial_id`) from the surviving files.
3. `resume_or_create` opens the same cycle and returns `resumed_from_round = N + 1`. The runner loads `trial_N` via `LoopState.restore_from_trial` and begins round `N + 1` with that state as the baseline.
4. The dashboard's `rounds_completed` / `best_round` are clamped so the UI does not show phantom rounds from the archived trajectory.
5. `dataset_runs/` is unchanged — content-addressed per-query results replay automatically for any unchanged `(prompt_hash, query)` pair.

### Editing the snapshot by hand

To alter optimizer state before resuming, edit `campaigns/{cycle_id}/trial_{N:04d}.json` between runs. Keep the file valid JSON and leave the `opt_search_point` block round-trippable through `OptSearchPoint.model_validate`. On the next `optimize --from N`, the edited trial is what `restore_from_trial` sees.

### Examples

```bash
# Resume from the latest completed round (default behavior — no --from).
python -m promptpotter optimize

# Rewind the active cycle to "after round 2 completed" and continue from
# round 3. Trials 3..M are moved to archived/resumed_at_<ts>/.
python -m promptpotter optimize --from 2
```

Forking across cycles (new `cycle_id`, parent pointer, independent trajectory) is not a supported primitive — if you need that, copy the campaign directory to a new name before running. The complexity was not worth the WAL it required.

## Key Files

| File | Role |
|------|------|
| `campaign/nodes/critique.py` | `CritiqueAgent`, `format_critique_for_prompt()`, pos/neg routing, stat computation |
| `campaign/nodes/escalation.py` | `DegradationCheck`, `DEFAULT_STRATEGIES`, `classify_warnings` |
| `campaign/runner.py` | Orchestration, escalation journal, critique threading |
| `campaign/nodes/layer_transitions.py` | L2 (`task_context` + meta-settings), L3 (plan) |
| `campaign/nodes/generate.py` | L1 generation (sole pipeline_params decider) |
| `campaign/nodes/score.py` | L1 scoring, winner selection, composite score |
| `scoring/search_point_scorer.py` | `score_search_point()` gateway, batch orchestration |
| `scoring/sample_measurement.py` | Per-query measurement, backend response parsing |
| `search/search_memory.py` | Cross-campaign intelligence (M8 Wave 3): parameter impact, query patterns, failure modes |
