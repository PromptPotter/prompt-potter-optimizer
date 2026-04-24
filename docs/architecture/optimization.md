# Optimization

```
┌─ ONE ROUND ────────────────────────────────────────────────────────────┐
│                                                                        │
│  L1 GENERATE (LLM) — N candidate OptSearchPoints                       │
│         ↓                                                              │
│  L1 EVALUATE — Backend /matches per candidate × per query              │
│    ├─ Stale data protocol: rerun → samplescan → sampleswitch           │
│    ├─ DegradationCheck: degraded_rate ≥ 0.4? → ABORT + EscalationSignal│
│    └─ Winner: best accuracy ≥ baseline + threshold                     │
│         ↓                                                              │
│  L1 CRITIQUE (LLM) — every-round intelligence hub                      │
│         ↓                                                              │
│  Compact critique → next L1 Generate (or L2 Refine on escalation)      │
│                                                                        │
│  ── ESCALATION (if stall or degradation) ───────────────────────────── │
│                                                                        │
│  L2 REFINE CONTEXT (LLM) — meta-controller                             │
│    (updates task_context + meta-settings; does NOT set pipeline params) │
│                                                                        │
│  L3 MODIFY PLAN (LLM) — if L2 stalls — new strategic plan              │
└────────────────────────────────────────────────────────────────────────┘
```

What each node reads and writes (inputs, outputs, mutual-exclusion rules) lives in [information-flow.md](information-flow.md) — one page, one canonical view. This file owns the execution order; that file owns the data.

**L1 (Generate)** proposes N candidate configurations each round — prompt variations and pipeline parameters. It is the innermost loop; every round produces N candidates for scoring.

**L1 Critique** analyzes evaluation results after each round. It is the every-round intelligence hub: the only layer with access to raw per-query results. Its output feeds L1 Generate in the next round (normal flow) and L2 Refine when escalation fires.

**L2 (Refine Context)** fires when L1 stalls — when successive rounds produce no improvement. L2 doesn't touch pipeline parameters; it adjusts the *context* L1 searches in: task framing, meta-settings (creativity, candidate budget), and a directive that supersedes critique as L1's primary signal for one round.

**L3 (Modify Plan)** fires when L2 itself stalls. It owns the optimization strategy — a high-level plan that shapes how L1 generates. L3 is rare; it means the optimizer is stepping back to rethink from scratch.

**Why three layers?** Each layer operates at a different cadence: L1 changes every round (fine-grained), L2 changes on stall (context shift), L3 changes on strategic failure (rare). Keeping cadences separate prevents a fast-moving parameter search from destabilizing the slower strategic context.

**Self-healing runs on two rails.** Rail 1 catches invalid proposals before any run: if L1 proposes a parameter value the backend rejects, L2 teaches L1 not to propose it again. Rail 2 catches runtime degradation during evaluation: if a candidate's configuration consistently produces bad results, the failure is pinned to that configuration and the next L2 run adjusts strategy to steer L1 away from it.

**Escalation is additive, not preemptive.** A stall escalates upward; each layer continues to run in its own slot. L3 fires, then L2 adjusts, then L1 generates — all in the same round hierarchy.

---

## Responsibility Matrix

| Agent | Fires when | Decides | Does NOT decide |
|-------|-----------|---------|-----------------|
| **L1 Generate** | Every round | pipeline_params (query_prefix, max_sites, schema, temperature, ...) | `task_context`, meta-settings |
| **Critique** | Every round | what to focus on (suggested_axes, priority_fix) | pipeline_params values |
| **L2 Refine** | Escalation only (stall, degradation) | `task_context`, meta-settings (creativity, n_variants, sp_budget_ttest), `l2_directive` | pipeline_params |
| **L3 Plan** | L2 stalls | strategic plan | pipeline_params, `task_context` |

---

## 3-Layer Optimization Model

Parameters are organized into three layers with different optimization cadences.

### Layer 1: Generate (innermost loop)

Tunable parameters discovered from the target pipeline's active nodes. Changed every round.

| Category | Examples |
|----------|----------|
| Prompt fields | `persona`, `task_intent`, `instruction`, `thinking_style`, `answer_format` |
| Model params | `temperature`, `model` |
| Output schema | Schema field overrides |
| Pipeline params | Thresholds, weights on non-LLM nodes |

Which parameters are Layer 1 depends on the target pipeline config — not a fixed list. Prompt fields only affect nodes with a prompt template referencing them.
- How to turn your workflow into the configuration needed: [node-standard.md](node-standard.md). Describes how to create a node or setup a chain of nodes.
- The canonical partitioning of input to chat models is described here:  [prompt-scheme.md](prompt-scheme.md)

### Layer 2: Refine Context

Adjusted when Layer 1 improvements stall:

| Field | Purpose |
|-------|---------|
| `optimizer_params` | Meta-settings (creativity, n_variants, sp_budget_ttest, variant_strategy) |
| `task_context` | Structured domain context (domain, pipeline_purpose, data_characteristics, optimization_goals, key_challenges, raw_description). Decomposed from `TASK_DESCRIPTION` at init. L2 can refine individual fields. |

### Layer 3: Modify Plan

Optimization strategy — rarely changed:

| Field | Purpose |
|-------|---------|
| `plan` | High-level optimization strategy |


### Dynamic Field Set

8 fields in `PROMPT_STRING_FIELDS` + `few_shot_examples` + `plan`, but L2 can add fields to widen or narrow the search space.

```
L2 REFINE
┌─ PROMPT SCHEME ──────────────────────────┐
│  1. task_intent                          │
│  2. problem_description                  │
│  3. instruction                          │
│  4. thinking_style                       │
│  5. answer_format                        │
│  6. domain_constraints          ← NEW    │
│                                          │
│  +/- [???] (managed by L2)               │
└──────────────────────────────────────────┘
```

## Feedback Cycle

Critique-guided optimization with 3-layer escalation, inspired by [PromptWizard](https://arxiv.org/abs/2405.18369)'s critique-and-refine pattern.


Each round:

1. **Generate** — Produce N candidates. The meta-prompt includes **L1 critique from the previous round** + freshly sampled **thinking styles** as mutation guidance.
2. **Evaluate + Critique** — Score candidates via the backend, compare by composite score against the current best. Generate a **L1 critique** of remaining failures/successes, fed forward to next round.
3. **Loop control** — Stall counter on no improvement. Patience exhausted: escalate to L2 (task context) then L3 (strategy). Escalation checks can also trigger L2/L3/abort mid-round (e.g., degradation check on target pipeline regression). Stop on `max_rounds` or perfect accuracy.


---

## L1 Critique Agent

Failure analysis is **separated from candidate generation** (PromptWizard pattern). L1 critique is the **every-round intelligence hub** — it runs after evaluation and winner selection. It is the **sole reader** of raw evaluation results AND receives SearchMemory intelligence (failure clusters, discriminating queries, tractability profiles, axis exhaustion, value trends) to frame its analysis. Its output feeds forward to **L1 Generate** (next round) and **L2 Refine** (on escalation). L2 only fires on escalation — L1 critique is the normal-flow intelligence bridge.

### L1 Critique Output

Both positive and negative paths produce the same 6-field result:

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

Injected into:
- **L1 Generate** as `l1_critique_text` — only when no `l2_directive` (mutual exclusion)
- **L2 Refine** as `l1_critique_text` in intelligence sections (always, so L2 can build on it)

### Sections Assembled

| Section | Source |
|---------|--------|
| **Evaluation summary** | `CritiqueContext` + `Cycle` |
| **Anomaly flags** | Computed inline from health/rank/evolution |
| **Pipeline health** | `winner_results.pipeline_data` |
| **Rank analysis** | `winner_results` + `candidate_keys` from schema |
| **Round evolution** | `state.rounds` (`RoundResult` history) |
| **Query categories** | `winner_results.terminated_at` |
| **Failure details** | `winner_results` (8 max, deduped) |
| **Successes** | `winner_results` (2 examples) |
| **Search memory** *(M8)* | `SearchMemory` atomic accessors: failure clusters, discriminating queries, tractability profiles, axis exhaustion, value trends |

### Anomaly Flags

| Flag | Fires when | Severity |
|------|-----------|----------|
| `high_degradation` | Degraded query count exceeds threshold | HIGH |
| `near_miss_pattern` | Ground truth in candidates for >30% of misses but not rank 1 | MEDIUM |
| `plateau_signal` | 2+ consecutive rounds with <1% improvement | MEDIUM |

### Pipeline Data Flow

Backend `/matches` returns `diagnostics.warnings[]` per query. A query is **"degraded"** if it has any non-empty warnings list. Each warning carries `{step, code, message}` — classified as `{step}:{code}` (e.g., `web_search:partial_scrape`).

---

## Escalation Chain — degradation via the RuntimeFailure rail

When a candidate's evaluation degrades mid-round, the failure is attributed **to that candidate**, not the round, and flows through the Rail 2 self-healing pipeline (see "Self-healing optimization — two rails" below):

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
| `l1_critique_text` | per-round, cleared on improvement | L1 critique node's narrative for next L1 |
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

`validate_overrides()` in `application/optimization/nodes/generate.py` attaches a `ValidationFailure(axis, value, allowed, reason)` (from `domain/analysis.py`) to `OptSearchPoint.memory.validation_failures` at L1 parse time. This is **outer-layer optimizer state** — it lives on the optimizer trace alongside `l1_critique_text`, `l2_directive`, and `escalation_journal`. The target-layer `JobSearchPoint` is untouched, which is why none of the scoring-layer machinery needs to know about validation failures: `_score_candidates` shortcuts to a synthetic 0 report, the existing accuracy comparator naturally deprioritizes the candidate in `_select_round_winner`, and the round checkpoint persists the failure with the rest of the optimizer memory.

**L2 teaches L1.** L2 `refine_strategy` already reads L1 critique, escalation reports, and the previous directive; validation failures slot into that same context as an "L1 VALIDATION FAILURES" section. L2 produces a directive that names the disallowed value by name (e.g. *"do not propose gpt-4o for model"*), which replaces L1 critique for next round's L1 via the normal directive/l1_critique mutual exclusion. L1 next round follows the directive and heals itself.

Flow: `detect → attach to candidate memory → synthetic-0 → surface on candidate_scores → L2 directive → L1 next round heals`.

### Rail 2 — `RuntimeFailure` (L2 self-healing with L3 escalation)

Some candidates are valid at parse time — every parameter is in the allowed range — but degrade at runtime. Canonical example: `llm_only.max_tokens=150` with `reasoning_effort=medium` on a Groq reasoning model. The model exhausts its reasoning budget before emitting visible content; the backend returns the raw reasoning trace as the answer; 7/7 evaluated queries produce an `llm_only:empty_content_reasoning_fallback` warning. No L1 rule was broken — the *strategic shape* of the search didn't account for the runtime constraint.

**Outer-memory mirror.** After the round, every new runtime failure is appended to the outer optimizer state — deduplicated by `(source, dominant warning, observed config)` so recurring patterns don't bloat the list. This trail follows the optimizer forward automatically. It is never cleared — it represents discovered runtime constraints, not one-round guidance.

**L2 heals itself.** L2 receives two partitions: `NEW (this round)` and `ACCUMULATED (surviving from earlier rounds despite L2 adjustment)`. The `ACCUMULATED` section is the real signal — if items survived L2's prior strategy adjustments, L2's last angle didn't work and it must try a different one. L2's job is NOT to parrot "don't use X" to L1 (that's Rail 1's pattern) — it must update its own outputs: tighten the directive to name the failing config range, refine task context with the discovered constraint, or adjust optimizer params to narrow L1's search.

**L3 replans on escalation.** When the accumulated list keeps growing across L2 rounds, L3 receives it as discovered constraints on the search space and must either change pipeline params (switch model, raise a param floor, swap a node) or change the plan to steer L1/L2 around it.

Flow: `detect → attach per-candidate → real score stands → mirror to outer memory → L2 adjusts own strategy → (if pattern persists) L3 replans`.

For the prompt-injection routing (who reads each failure list), see [information-flow.md](information-flow.md). For the `⚠ … ↳` rendering convention, see [display-conventions.md](display-conventions.md).

### Relationship to the other per-evaluation checks

| Check | Fires | Action |
|---|---|---|
| **Validation failure** | L1 parse time, before evaluation | Synthetic 0; skip backend. L2 teaches L1 via directive next round. |
| **Elimination check** | Mid-evaluation, after `n_min` queries | Stop scoring this candidate (Wilcoxon signed-rank says it can't beat the leader); continue with the next. |
| **Empty output check** | Mid-evaluation, after 3 queries | Stop scoring this candidate; continue with the next. |
| **Degradation check** | Mid-evaluation, after 3 queries | Stop scoring this candidate; synthesise a runtime failure; mirror to outer memory. L2 reads NEW + ACCUMULATED next round and adjusts its own strategy; L3 replans when the pattern persists. |

Validation is the only one that fires *before* evaluation — it needs nothing but the candidate dict and the schema, which is why it can short-circuit the backend entirely.

### Planned future self-healing mechanisms

New mechanisms land by following one of the two rails:

- **Backend errors naming a parameter and reason** (e.g. `temperature out of range`) — Rail 1: L1 proposed a bad value, L2 teaches L1 via directive.
- **Schema/format violations on structured outputs** — usually Rail 1 (L1 picked a bad format hint) or Rail 2 (the schema itself is over-constrained for the current model — L3 replans).
- **Monotonic per-axis degradation with fault attribution** — Rail 2: surfaced per-candidate, L2 adjusts optimizer params / task context, L3 eventually changes pipeline composition.
- **Quota / rate-limit exhaustion on a specific node** — Rail 2: L2 can't fix quota via directive, but L3 can swap nodes.

---

## Candidate elimination pathways — full ladder and display contract

Six independent mechanisms can end a candidate's evaluation early or annotate a query. They run in a fixed order and each owns its own memory field and display annotation. Maintainers tracing "why did this candidate die at n=1?" should walk this ladder from top to bottom.

| # | Mechanism | Fires | `n_min` | Candidate fate | Memory | Source |
|---|---|---|---|---|---|---|
| 1 | **Validation skip** — `OptSearchPoint.memory.validation_failures` non-empty | pre-score | — | synthetic `{accuracy: 0.0, invalid: True}` (no backend calls) | `memory.validation_failures` | `application/optimization/nodes/score.py::_score_candidates` (path 1) |
| 2 | **Stale-data protocol** — cached *or* fresh result carries `diagnostics.warnings` | every degraded query | — | same candidate, annotated + possibly re-measured / swapped | — | `application/scoring/stale_data.py::execute_stale_data_protocol` |
| 3 | **DegradationCheck — fatal fast-path** — latest query carries a `FATAL_WARNINGS` code | every query | **1** | eliminated; synthesises `RuntimeFailure` | `memory.runtime_failures` | `application/optimization/nodes/escalation.py:98-121` |
| 4 | **DegradationCheck — rate-based** — `degraded_rate >= threshold` | every query | **3** | eliminated; synthesises `RuntimeFailure` | `memory.runtime_failures` | `application/optimization/nodes/escalation.py:123-149` |
| 5 | **EmptyOutputCheck** — `empty_predicted_rate >= threshold` | every query | **3** | eliminated | — | `application/optimization/nodes/escalation.py::EmptyOutputCheck` |
| 6 | **EliminationCheck** (Wilcoxon signed-rank vs completed priors) | every query | **4** | eliminated; records `elimination_cut` decision | — | `application/optimization/elimination.py` |

### Ordering inside `_run_query_loop`

For each query, `search_point_scorer._run_query_loop` runs:

1. Prior-result cache lookup (may replay a cached result).
2. If result is degraded → `execute_stale_data_protocol` (may decorate with `degraded_observed`, trigger rerun/samplescan/sampleswitch, or return unchanged).
3. `on_result` fires → display renders the query line with annotations.
4. Iterate every enabled check in the shared `degradation_checks` list; first one to return a signal ends the candidate.

Mechanisms 3–6 all co-exist in that final list, so the *first-to-fire-wins* ordering inside the list matters. `_score_candidates` currently wires degradation checks first (from `build_degradation_checks`), then appends `EliminationCheck`. Fatal warnings therefore beat any rate check; rate checks beat the Wilcoxon signed-rank gate.

### `FATAL_WARNINGS` is a hardcoded invariant, not a tunable

`FATAL_WARNINGS = frozenset({"llm_only:empty_content_reasoning_fallback"})` in `escalation.py:51`. These codes are deterministic for the whole config — one sighting proves the candidate is broken for every remaining query, so spending more backend calls to "confirm" is waste. The fast-path bypasses `min_queries` and `threshold` entirely. Grow this set (don't expose it as a tunable) when a new warning proves equally conclusive.


## SearchMemory Intelligence Feed



1. **Zero-signal filter** — runs immediately after the round completes. When enabled, queries with variance 0 across enough samples are moved into the dataset's excluded list. See [candidate-comparison.md](../methods/candidate-comparison.md).
2. **Adaptive sample prefix** — runs immediately after the zero-signal filter. When enabled, Rasch + Knowledge Gradient swaps low-info samples in the active scoring slice for high-information ones. Mutates only the in-memory slice; never the dataset on disk. See § Adaptive sample prefix below. See [candidate-comparison.md](../methods/candidate-comparison.md).



### Persistence

Each evolved round appends a compact event: `{round, swapped_in, swapped_out, reason, rasch: {n_candidates, n_samples, iterations, converged}, hardness_top: [...]}`. Persisted in every trial and restored on resume. The shared renderer consumes this list for both CLI and notebook.


## Stale Data Load Protocol

When a cached evaluation result is degraded (non-empty `diagnostics.warnings`), the protocol walks a 3-step ladder:

| Step | Action | Resolves when |
|------|--------|---------------|
| **rerun** | Re-evaluate after enough observations | Fresh result not degraded |
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
| `l1_evaluate` | Evaluation, winner selection & L1 critique |
| `refine_strategy` | L2 escalation |
| `modify_plan` | L3 escalation |
| `escalation` | `EscalationCheck` fires mid-eval |
| `zero_signal_filter` | Dataset sweep removed always-hit/always-miss queries |
| `adaptive_prefix` | Round-end Rasch+KG swap of low-info ↔ high-info samples |

Each event: `phase`, `event` ("enter"/"exit"), `round`, `data` (dict), `timestamp` (ISO 8601).

---

## Configuration

```json
{
  "sp_budget_ttest": 35,             # queries per eval (must be > 0)
  "exclude_nodes": ["node1"],
  "optimization": {
    "n_variants": 5,
    "creativity": 0.7,
    "improvement_threshold": 0.01,
    "l1_patience": 3,
    "max_rounds": 10,
    "hard_cap": 100,                 # absolute round limit (safety)
    "max_consecutive_errors": 3,     # abort eval after N backend errors
    "enable_l1_critique": true,
    "degradation_threshold": 0.4,    # 0 = disabled
    "l1_critique_degradation_threshold": 0.4,
    "l1_critique_near_miss_ratio": 0.3,
    "enable_l2": true,
    "l2_patience": 2,                # None = unlimited during degradation
    "enable_l3": true,
    "l3_patience": 1,                # None = unlimited during degradation
    "stale_data_load_protocol": ["rerun", "samplescan", "sampleswitch"],
    "plan": None,                    # override optimizer strategy (str)
    "context": None,                 # override domain task_context (str)
    "l1_critique": None,             # override bootstrap L1 critique (str)
}
```
---

## Resuming mid-cycle

`optimize --from <round>` rewinds the active cycle to a specific round boundary and continues from there. It is **not** a new campaign: the same `cycle_id`, the same `campaigns/{cycle_id}/` directory, trial files appended past the rewind point after the next run. Use it when you want to edit the optimizer state by hand (edit `trials/trial_NNNN.json` between runs), discard a bad trajectory, or re-enter HITL review.

### The only snapshot source is `trials/trial_NNNN.json`

Each completed round writes `campaigns/{cycle_id}/trials/trial_{round:04d}.json` via `CampaignStore.add_trial`. That file already carries the full serialized `OptSearchPoint` (`opt_search_point` key), so resume rehydrates the exact optimizer state via `Cycle.restore_from_trial` — no separate write-ahead log. `events.jsonl` is a pure observability mirror parallel to Langfuse; nothing reads it for state reconstruction.

### What `--from N` does

1. `optimize --from N` requires an active cycle on the session (run `optimize` at least once first).
2. `CampaignStore.rewind_to_round(backend_id, cycle_id, N)` moves `trials/trial_{M:04d}.json` and `candidates/round_{M:04d}.json` for every `M > N` into `campaigns/{cycle_id}/archived/resumed_at_<ts>/{trials,candidates}/`, then rebuilds the in-memory trial index (`trials`, `n_trials`, `best_accuracy`, `best_trial_id`) from the surviving files.
3. `resume_or_create` opens the same cycle and returns `resumed_from_round = N + 1`. The runner loads `trial_N` via `Cycle.restore_from_trial` and begins round `N + 1` with that state as the baseline.
4. The dashboard's `rounds_completed` / `best_round` are clamped so the UI does not show phantom rounds from the archived trajectory.
5. `dataset_runs/` is unchanged — content-addressed per-query results replay automatically for any unchanged `(prompt_hash, query)` pair.

### Editing the snapshot by hand

To alter optimizer state before resuming, edit `campaigns/{cycle_id}/trials/trial_{N:04d}.json` between runs. Keep the file valid JSON and leave the `opt_search_point` block round-trippable through `OptSearchPoint.model_validate`. On the next `optimize --from N`, the edited trial is what `restore_from_trial` sees.

### Examples

```bash
# Resume from the latest completed round (default behavior — no --from).
python -m promptpotter optimize

# Rewind to "after round 2 completed" and continue from round 3.
# Trials 3..M are moved to archived/resumed_at_<ts>/.
python -m promptpotter optimize --from 2
```

## Data vs. scoring policy

Traces are facts; scores are policy. Traces are written once and never edited — scores are a view, produced by applying the active scoring formula on demand. Editing the formula mid-campaign doesn't corrupt the trace archive; on resume, all prior decisions are replayed against rescored inputs and the first divergence halts the run with a fork hint. Full mechanics: [`scoring-policy.md`](scoring-policy.md).

