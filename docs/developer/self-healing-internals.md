# Self-Healing Internals

Implementation of the two self-healing rails. Concept-level overview in [../concepts/self-healing.md](../concepts/self-healing.md); this page covers the classes, memory fields, and escalation wiring.

Failures attach to the **candidate that produced them** (per-candidate `OptSearchPoint.memory`), never to the round, so a losing candidate's problem never disrupts the round winner. Two rails exist; new mechanisms must pick one — **do not invent a sidecar, do not silently drop, do not just log.**

|  | **Rail 1 — `ValidationFailure`** | **Rail 2 — `RuntimeFailure`** |
|---|---|---|
| Detected | L1 parse time (before backend) | Mid-evaluation (after backend) |
| Example | `model: gpt-4o` when allowed = `[gpt-oss-120b]` | `max_tokens=150` → 100% `reasoning_budget_exhausted` on reasoning model |
| Who made the mistake | L1 (tactical — picked a disallowed value) | Nobody tactically (L1's value was in range; the *strategic shape* of the search didn't account for the runtime constraint) |
| Score effect | Synthetic 0 (zero backend calls) | Real score stands (candidate is eliminated mid-eval) |
| Per-candidate memory | `memory.validation_failures` | `memory.runtime_failures` |
| Outer-memory mirror | None — L2 reads from `candidate_scores` only | Cumulative `state.opt_sp.memory.runtime_failures` (every round's new failures deduped and appended) |
| Healer | **L2 teaches L1** via a directive (`"use ONLY one of: …"`) | **L2 heals itself** — updates its own directive / `task_context` / `optimizer_params` to re-shape what L1 is allowed to search over |
| Escalation | None (L2 can always articulate the constraint) | **L3** `modify_plan` — when the accumulated list keeps growing despite L2's adjustments, L3 reads the trail and replans |

Both rails share the rule *"detect → trace on the candidate → surface on `candidate_scores` → feed the right teacher"* but diverge on **who the teacher is** and **what the healing action looks like**.

---

## Rail 1 — `ValidationFailure` (L1 self-healing via L2 directive)

Some L1-generated candidates are invalid before evaluation starts — the optimizer LLM hallucinated a value outside the user-declared allowed set. Canonical example: `pipeline_params_override.llm_only.model = "gpt-4o"` when the backend only has `openai/gpt-oss-120b` per `PipelineSchema.available_models`.

`validate_overrides()` in `application/optimization/nodes/l1/generate.py` attaches a `ValidationFailure(axis, value, allowed, reason)` (from `domain/analysis.py`) to `OptSearchPoint.memory.validation_failures` at L1 parse time. This is **outer-layer optimizer state** — it lives on the optimizer trace alongside `l1_critique_text`, `l2_directive`, and `escalation_journal`. The target-layer `JobSearchPoint` is untouched, which is why none of the scoring-layer machinery needs to know about validation failures: `score_candidates` shortcuts to a synthetic 0 report, the existing accuracy comparator naturally deprioritizes the candidate in `select_fittest`, and the round checkpoint persists the failure with the rest of the optimizer memory.

**L2 teaches L1.** L2 `refine_strategy` already reads L1 critique, escalation reports, and the previous directive; validation failures slot into that same context as an "L1 VALIDATION FAILURES" section. L2 produces a directive that names the disallowed value by name (e.g. *"do not propose gpt-4o for model"*), which replaces L1 critique for next round's L1 via the normal directive/l1_critique mutual exclusion. L1 next round follows the directive and heals itself.

Flow: `detect → attach to candidate memory → synthetic-0 → surface on candidate_scores → L2 directive → L1 next round heals`.

---

## Rail 2 — `RuntimeFailure` (L2 self-healing with L3 escalation)

Some candidates are valid at parse time — every parameter is in the allowed range — but degrade at runtime. Canonical example: `llm_only.max_tokens=150` with `reasoning_effort=medium` on a Groq reasoning model. The model exhausts its reasoning budget before emitting visible content; the backend returns `content=""` plus a neutral `llm_only:content_empty` advisory carrying `finish_reason=length` and the reasoning token count on `step_tokens.llm_only`; PromptPotter's `classify_result()` derives the fatal code `llm_only:reasoning_budget_exhausted` from those signals on every one of the 7/7 evaluated queries. No L1 rule was broken — the *strategic shape* of the search didn't account for the runtime constraint.

**Outer-memory mirror.** After the round, every new runtime failure is appended to the outer optimizer state — deduplicated by `(source, dominant warning, observed config)` so recurring patterns don't bloat the list. This trail follows the optimizer forward automatically. It is never cleared — it represents discovered runtime constraints, not one-round guidance.

**L2 heals itself.** L2 receives two partitions: `NEW (this round)` and `ACCUMULATED (surviving from earlier rounds despite L2 adjustment)`. The `ACCUMULATED` section is the real signal — if items survived L2's prior strategy adjustments, L2's last angle didn't work and it must try a different one. L2's job is NOT to parrot "don't use X" to L1 (that's Rail 1's pattern) — it must update its own outputs: tighten the directive to name the failing config range, refine task context with the discovered constraint, or adjust optimizer params to narrow L1's search.

**L3 replans on escalation.** When the accumulated list keeps growing across L2 rounds, L3 receives it as discovered constraints on the search space and must either change pipeline params (switch model, raise a param floor, swap a node) or change the plan to steer L1/L2 around it.

Flow: `detect → attach per-candidate → real score stands → mirror to outer memory → L2 adjusts own strategy → (if pattern persists) L3 replans`.

For the prompt-injection routing (who reads each failure list), see [information-flow.md](information-flow.md). For the `⚠ … ↳` rendering convention, see [display-conventions.md](display-conventions.md).

---

## Optimizer-memory state

The fields enumerated in `OptSearchPoint.MEMORY_FIELDS` (in `domain/opt_search_point.py`) are the cross-round optimizer state that travels with each candidate. Each is a flat field on `OptSearchPoint`, persisted with the round trial JSON; lifecycles vary:

| Field | Lifecycle | Purpose |
|---|---|---|
| `l1_critique_text` | per-round, cleared on improvement | L1 critique node's narrative for next L1 |
| `escalation_journal` | cross-round, append-only | History of degradation events with outcomes |
| `warning_inventory` | cross-round | Per-query warning aggregation |
| `l2_directive` | one-round window, cleared on improvement | L2's diagnostic + action guidance for L1 |
| `validation_failures` | per-candidate (set at L1 parse time) | Parse-time invariant violations — Rail 1 |
| `runtime_failures` | per-candidate + cumulative outer-memory mirror | Runtime-observed health failures — Rail 2. Candidate-level copies set in `score_candidates`; outer-level accumulated after the round; cleared never. |

---

## Escalation chain — degradation via the RuntimeFailure rail

When a candidate's evaluation degrades mid-round, the failure is attributed **to that candidate**, not the round, and flows through the Rail 2 pipeline:

```
degraded_rate >= threshold on candidate C_k mid-eval
    ↓
DegradationCheck fires → EscalationSignal(target=ELIMINATE_CANDIDATE)
    ↓
score_candidates absorbs the signal, synthesises a RuntimeFailure from
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

Degradation is not a round-level escalation — the round never aborts for a single candidate's runtime issues, and `max_rounds` is never skipped for it. The only round-level escalation paths left are `ABORT_CAMPAIGN` (true catastrophic degradation that L2/L3 cannot rescue) and the normal patience-exhaustion path when successive rounds don't improve.

---

## Relationship to the other per-evaluation checks

| Check | Fires | Action |
|---|---|---|
| **Validation failure** | L1 parse time, before evaluation | Synthetic 0; skip backend. L2 teaches L1 via directive next round. |
| **`EliminationCheck`** | Mid-evaluation, after `n_min` queries | Stop scoring this candidate (Wilcoxon signed-rank says it can't beat the leader); continue with the next. See [../methods/candidate-elimination.md](../methods/candidate-elimination.md). |
| **`DegradationCheck`** | Mid-evaluation, after 3 queries | Stop scoring this candidate; synthesise a runtime failure; mirror to outer memory. L2 reads NEW + ACCUMULATED next round. |

Validation is the only one that fires *before* evaluation — it needs nothing but the candidate dict and the schema, which is why it can short-circuit the backend entirely.

---

## `classify_result()` — fatal classification

`classify_result()` in `application/optimization/diagnostics.py` derives **fatal codes** from the backend's neutral advisories (`llm_only:content_empty`, `*:content_filtered`, …) and the raw response shape carried in `pipeline_data.step_tokens.{node}` (normalized `finish_reason`, `reasoning` token count). Backend = facts, optimizer = policy: TermNorm reports observations and PromptPotter classifies. The rule table:

- `content_empty` + `finish_reason=length` + `reasoning_tokens > 0` → `reasoning_budget_exhausted`
- `content_empty` + `finish_reason=length` + `reasoning_tokens = 0` → `output_truncated`
- `content_empty` + any other `finish_reason` → `empty_response`
- `*:content_filtered` → passthrough as fatal

Fatal codes are deterministic for the whole config — one sighting proves the candidate is broken for every remaining query, so spending more backend calls to "confirm" is waste. Grow the rule table (don't expose it as a tunable) when a new pattern proves equally conclusive.

Legacy archive alias: rows captured before TermNorm renamed the advisory carry `llm_only:empty_content_reasoning_fallback`; the classifier maps that directly to `reasoning_budget_exhausted` so resume on old cycles still deprecates correctly.

A fatal classification has **three** load-boundary effects (consumed via the `is_deprecated()` wrapper in `application/optimization/utils.py`):

1. **Candidate elimination** — `DegradationCheck` fast-path in `application/optimization/elimination.py::DegradationCheck.evaluate` returns `EscalationSignal(target=ELIMINATE_CANDIDATE)` on first sighting; bypasses `min_queries` and `threshold` entirely.
2. **Cache eviction** — `score_search_point` runs `_filter_deprecated_priors` on the result of `dataset_runs.load_reusable_results` and drops every entry the classifier marks fatal. The query falls through to a fresh backend call so the optimizer never replays a known-bad measurement. The dataset_run archive on disk is left intact — eviction is purely load-side. Fresh re-measurements receive `retry_of_deprecated_cache=True`.
3. **Stats exclusion** — `_compute_accuracy` partitions deprecated rows into a separate `deprecated` count and excludes them from `hits`, `total`, `errors`, and the accuracy denominator. The display layer tags them `DEPR` instead of HIT/MISS, and the round summary appends `(N deprecated)` when any are present.

### Deprecated samples — why this is not a fallback

The exclusion is a **load-boundary filter**, not a score-time fallback: it removes known-invalid measurements from the cache and from the stats denominator before the scoring layer runs. Trace records continue to be archived (forensic value), only cache reuse and primary-stat aggregation are blocked. This is sanctioned alongside the `_score_candidates()` validation-failure synthetic-0 — see [`scoring-and-traces.md`](../concepts/scoring-and-traces.md#deprecated-samples) for the operator-facing framing.

If a query consistently hits a fatal warning, it will be re-measured every round and immediately eliminate the candidate via `DegradationCheck` on each attempt. That is correct behavior — frequent fatal warnings on the same query indicate a too-tight token budget on the active model.

---

## Planned future self-healing mechanisms

New mechanisms land by following one of the two rails:

- **Backend errors naming a parameter and reason** (e.g. `temperature out of range`) — Rail 1: L1 proposed a bad value, L2 teaches L1 via directive.
- **Schema/format violations on structured outputs** — usually Rail 1 (L1 picked a bad format hint) or Rail 2 (the schema itself is over-constrained for the current model — L3 replans).
- **Monotonic per-axis degradation with fault attribution** — Rail 2: surfaced per-candidate, L2 adjusts optimizer params / task context, L3 eventually changes pipeline composition.
- **Quota / rate-limit exhaustion on a specific node** — Rail 2: L2 can't fix quota via directive, but L3 can swap nodes.
