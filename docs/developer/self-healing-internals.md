# Self-Healing Internals

Implementation of the four LLM-to-LLM healing loops. Concept-level overview in [../concepts/self-healing.md](../concepts/self-healing.md); this page is the wiring map — classes, fields, hook points, and persistence.

Every failure attaches to the **candidate that produced it** (direct fields on `OptSearchPoint`), never to the round, so a losing candidate's problem never disrupts the round winner. New mechanisms must land as one of the four loops below — **do not invent a sidecar, do not silently drop, do not just log.**

## The four loops at a glance

|  | Loop 1 | Loop 2 | Loop 3 | Loop 4 |
|---|---|---|---|---|
| **Producer → Nurse** | L1 → L2 | L1 → L2 | L2 → L3 | L2 → L3 |
| **Trigger detector** | `L1_SCHEMA_COMPLIANCE` validator | `DegradationCheck` (mid-eval) | `escalate_l2` patience | `L2_*` validators (post-parse) |
| **Failure record class** | `ValidationFailure` | `RuntimeFailure` | (patience event, no record) | `ValidatorOutcome` |
| **OSP storage** | `validation_failures` | `runtime_failures` | `escalation.l2.stall_count` | `l2_output_failures` |
| **Outer-memory mirror** | none (L2 reads `candidate_scores`) | cumulative on `cycle.opt_sp.runtime_failures` | none | per-round on the OSP itself |
| **Nurse prompt slot** | `{{validation_failures}}` | `{{runtime_failures}}` | (whole `l3_plan` template) | `{{l2_output_failures_section}}` |
| **Renderer** | `_section_validation_failures` | `_section_runtime_failures` | `format_runtime_failures_for_l3` (sub-section) | `format_l2_output_failures_for_l3` |
| **Healer's writeback** | `cycle.opt_sp.l2_directive` | `l2_directive` / `task_context` / scheme + text overrides | `cycle.opt_sp.plan` | `cycle.opt_sp.plan` |
| **Score effect** | synthetic 0 (Path 1 in `score_population`) | real score, candidate eliminated mid-eval | none | none — fires after L2 ran |

---

## Loop 1 — L2 nurses L1 on `ValidationFailure`

**Trigger.** `L1_SCHEMA_COMPLIANCE` (in `application/optimization/l1.py`) is the first concrete `LLMOutputValidator`. It wraps the pure `validate_overrides()` function and runs at L1 parse time, in `parse_population()`. When L1's `pipeline_params_override` proposes a value outside `PipelineSchema.available_models` or outside a node's `param_allowed_values`, the validator emits a `ValidatorOutcome` whose `evidence["failures"]` is a list of `ValidationFailure(axis, value, allowed, reason)`.

The failures land on `OptSearchPoint.validation_failures`. This is **outer-layer optimizer state** — it lives on the optimizer trace alongside `l1_critique_text`, `l2_directive`, and `escalation_journal`. The target-layer `JobSearchPoint` is untouched, which is why none of the scoring-layer machinery needs to know about validation failures: `score_population()` shortcuts to a synthetic 0 report (Path 1), the inline winner-selection in `l1_score()` naturally deprioritizes the zero-accuracy candidate, and the round checkpoint persists the failure with the rest of the optimizer memory.

**L2 nudges L1.** L2's `refine_strategy` template (`optimizer_pipeline.json::resolved_prompts['l2_context/1']`) renders a `{{validation_failures}}` section via `_section_validation_failures()` (in `application/optimization/pipeline.py`). L2 writes a directive that points L1 toward the allowed region. The directive lands on `OptSearchPoint.l2_directive` and L1 reads it as primary signal next round.

**Healing is gradual.** L2's directive shifts L1's distribution toward valid values; it does not guarantee a one-shot fix. If L1 still proposes invalid values next round, the validator fires again, the new failures land in `validation_failures`, and L2 gets another pass with fresh evidence. The renderer guidance reflects this — it does not require L2 to enumerate every disallowed value, only to nudge in the right direction.

Flow: `validate → ValidationFailure attached → synthetic-0 → surface on candidate_scores → L2 reads validation_failures section → writes l2_directive → L1 next round attempts within shifted distribution → loop retriggers if failures persist`.

---

## Loop 2 — L2 nurses L1 on `RuntimeFailure`

**Trigger.** `DegradationCheck` (in `application/optimization/elimination.py`) fires mid-evaluation. Two paths:

1. **Fatal-code fast path.** `classify_result()` derives a fatal code from raw response shape (`finish_reason=length` + `reasoning_tokens > 0` → `reasoning_budget_exhausted`). One sighting ends the candidate; bypasses `min_queries`/`threshold`.
2. **Rate-based path.** After `min_queries=3`, if `degraded_rate >= 0.4`, eliminate.

Both paths produce an `EscalationSignal(target=ELIMINATE_CANDIDATE)`. The candidate's caller in `score_population` synthesizes a `RuntimeFailure(source, dominant_warning, warning_types, degraded_rate, …)` from the check result + the candidate's observed pipeline_params, attaches it to `cp.osp.runtime_failures`, includes it in the candidate's score report, and **continues with the next candidate** — the round winner is unaffected.

**Outer-memory mirror.** End of round, `execute_round` mirrors every new `RuntimeFailure` onto `cycle.opt_sp.runtime_failures`, deduplicated by `(source, dominant_warning, observed_config)` so recurring patterns don't bloat the list. Never cleared — it represents discovered runtime constraints.

**L2 heals itself, gradually.** `_section_runtime_failures()` partitions the list into NEW (this round) vs ACCUMULATED (`first_seen_round != current_round`). The ACCUMULATED partition is the real signal — surviving items mean L2's prior angle didn't take. L2 updates its own outputs (shifted directive, refined task_context, scheme/text/template overrides) and tries again. No one-shot fix is expected; if ACCUMULATED still grows, the angle keeps changing until L3 takes over.

If ACCUMULATED keeps growing across rounds, **Loop 3** takes over.

Flow: `DegradationCheck → RuntimeFailure attached per candidate → real score stands → end-of-round mirror to outer memory → L2 reads runtime_failures section (NEW + ACCUMULATED) → adjusts own strategy → (if pattern persists) L3 replans`.

---

## Loop 3 — L3 replans on L2 stall

**Trigger.** `escalate_l2` (in `application/optimization/cycle.py`) checks `esc.l2.stall_count >= opt.l2_patience`. When stalled, L3 fires (subject to its own `l3_patience`).

**Inputs.** L3's prompt (`optimizer_pipeline.json::resolved_prompts['l3_plan/1']`) reads:

- `{{l2_summary}}` — recent L2 adjustments + accuracy delta from `cycle.escalation.l2`.
- `{{runtime_failures_section}}` — accumulated `RuntimeFailure` trail rendered by `format_runtime_failures_for_l3()`.
- `{{l2_output_failures_section}}` — L2-output validator outcomes rendered by `format_l2_output_failures_for_l3()` (Loop 4 evidence).
- `{{rendered_prompt}}`, `{{pipeline_section}}`, `{{axes_digest}}`.

**Output.** L3 writes a new `plan` (and optionally `pipeline_params`). The plan lands on `OptSearchPoint.plan` and feeds both L1's `{{plan}}` slot and the next L2 invocation.

This is the only loop with cross-layer authority — L3 changes pipeline composition or strategy framing, not just per-round directives.

---

## Loop 4 — L3 nurses L2 on output validators

**Trigger.** `L2_OUTPUT_VALIDATORS` registry (in `application/optimization/l2_validators.py`) — three starter validators, all `nurse_target="l3"`:

| Validator id | Detects |
|---|---|
| `l2_cross_field_duplication` | Same N+ line block in ≥2 of `{directive, template_override, text_overrides[*]}` |
| `l2_verbatim_self_repeat` | L2's `directive` this round equals previous round's directive on OSP |
| `l2_catalogue_redundancy` | `text_overrides[section]` value equals existing override on OSP for that section |

The validators run inside `L2RefineStrategy.build_result()` (in `pipeline.py`), between LLM-output parse and `TransitionResult` construction. Outcomes ride on `TransitionResult.l2_output_failures` and are written onto `cycle.opt_sp.l2_output_failures` by `apply_side_effects`.

**L3 force-fires.** When `cycle.opt_sp.l2_output_failures` is non-empty after L2 runs, `escalate_l2` invokes `L3ModifyPlan` *immediately* — bypassing `l2_patience` and `l3_patience`. Broken L2 output is not a "wait and see" signal. The trigger is deterministic from L2's output (which itself rides on the trial JSON), so resume reproduces it without needing a separate decision record.

L3 reads the failures via `format_l2_output_failures_for_l3()`, which names each fired validator with its evidence and asks L3 to refine the plan text so L2's next pass comes out cleaner. The renderer guidance is gradual — L3 nudges, doesn't have to perfectly diagnose. If the same validator keeps firing across L3 passes, the loop keeps retriggering until structural change (pipeline composition, task framing) lands.

Flow: `L2 emits parsed output → L2_OUTPUT_VALIDATORS run → outcomes attached → apply_side_effects writes to OSP → escalate_l2 sees non-empty list → forces L3 → L3 refines plan → L2 next pass attempts within shifted distribution → loop retriggers if failures persist`.

---

## Validators are Evaluator-shaped

`promptpotter/domain/validators.py` defines:

```python
@dataclass(frozen=True)
class ValidatorOutcome:
    validator_id: str
    passed: bool
    score: float                # 1.0 = clean, 0.0 = full failure
    evidence: dict[str, Any]
    nurse_target: Literal["l2", "l3"]


@dataclass(frozen=True)
class LLMOutputValidator:
    id: str
    description: str
    nurse_target: Literal["l2", "l3"]
    check: Callable[..., ValidatorOutcome | None]
```

The shape mirrors `Evaluator` (`name, description, scope, compute, …`) so future L4 composite scoring can read validator outcomes through the same channel as evaluators. The `score` field is the seam — currently only consumed for display, but it can flow into a `campaign.json::scoring` formula the same way `accuracy` and `prompt_compactness` do today.

---

## Optimizer-memory state

The fields enumerated in `OptSearchPoint.MEMORY_FIELDS` (in `domain/opt_search_point.py`) are the cross-round optimizer state that travels with each candidate. Each is a flat field; lifecycles vary:

| Field | Lifecycle | Used by which loop |
|---|---|---|
| `l1_critique_text` | per-round, cleared on improvement | (round-over-round feedback, not a loop) |
| `escalation_journal` | cross-round, append-only | (general history) |
| `warning_inventory` | cross-round | (per-query warning aggregation) |
| `l2_directive` | one-round window, cleared on improvement | Loop 1, Loop 2 — L2's writeback |
| `validation_failures` | per-candidate (set at L1 parse time) | Loop 1 — L2 reads |
| `runtime_failures` | per-candidate + cumulative outer-memory mirror | Loop 2 — L2 reads NEW + ACCUMULATED; Loop 3 reads cumulative |
| `l2_output_failures` | per-round, set by L2 post-parse | Loop 4 — L3 reads |
| `failure_analysis` | per-round | (round-over-round feedback) |
| `round_history` | per-round, append-only | (trajectory display) |

---

## Relationship to mid-eval termination

| Check | Fires | Self-healing loop? |
|---|---|---|
| **Validation** (`L1_SCHEMA_COMPLIANCE`) | L1 parse time, before evaluation | Loop 1 |
| **`EliminationCheck`** | Mid-evaluation, after `n_min` queries (Wilcoxon) | No — pure statistical termination, no failure record |
| **`DegradationCheck`** | Mid-evaluation, after 3 queries (or first fatal) | Loop 2 |

`EliminationCheck` is *not* self-healing — it stops scoring this candidate (Wilcoxon signed-rank says it can't beat the leader), but no failure record is created and no LLM is informed. See [../methods/candidate-elimination.md](../methods/candidate-elimination.md).

---

## `classify_result()` — fatal classification

`classify_result()` in `application/optimization/elimination.py` derives **fatal codes** from the backend's neutral advisories (`llm_only:content_empty`, `*:content_filtered`, …) and the raw response shape carried in `pipeline_data.step_tokens.{node}` (normalized `finish_reason`, `reasoning` token count). Backend = facts, optimizer = policy. The rule table:

- `content_empty` + `finish_reason=length` + `reasoning_tokens > 0` → `reasoning_budget_exhausted`
- `content_empty` + `finish_reason=length` + `reasoning_tokens = 0` → `output_truncated`
- `content_empty` + any other `finish_reason` → `empty_response`
- `*:content_filtered` → passthrough as fatal

Fatal codes are deterministic for the whole config — one sighting proves the candidate is broken for every remaining query, so spending more backend calls to "confirm" is waste. Grow the rule table (don't expose it as a tunable) when a new pattern proves equally conclusive.

A fatal classification has **three** load-boundary effects (consumed via the `is_deprecated()` wrapper):

1. **Candidate elimination** — `DegradationCheck` fast-path returns `EscalationSignal(target=ELIMINATE_CANDIDATE)` on first sighting; bypasses `min_queries` and `threshold`.
2. **Cache eviction** — `score_search_point` runs `_filter_deprecated_priors` on the result of `archive.load_reusable_results` and drops every entry the classifier marks fatal. The query falls through to a fresh backend call so the optimizer never replays a known-bad measurement. Fresh re-measurements receive `retry_of_deprecated_cache=True`.
3. **Stats exclusion** — `_compute_accuracy` partitions deprecated rows into a separate `deprecated` count and excludes them from `hits`, `total`, `errors`, and the accuracy denominator.

### Deprecated samples — why this is not a fallback

The exclusion is a **load-boundary filter**, not a score-time fallback: it removes known-invalid measurements from the cache and from the stats denominator before the scoring layer runs. Trace records continue to be archived (forensic value), only cache reuse and primary-stat aggregation are blocked. Sanctioned alongside the `score_population()` validation-failure synthetic-0 — see [`scoring-and-traces.md`](../concepts/scoring-and-traces.md#deprecated-samples).

---

## Mistakes-as-signals: the future seam

Every loop's failure record persists via `OptSearchPoint` → trial JSON → `campaigns/{cycle_id}/trials/trial_NNNN.json`. That makes the trial JSON corpus the **mistakes archive** — no separate on-disk surface needed. Cross-cycle queries are deferred (future L4 work); today, validator outcomes and runtime failures are first-class signals already serialized alongside everything else, ready for a future composite-scoring formula to consume them.

The `ValidatorOutcome.score: float` field is the consumer seam — emitted day one even though no formula reads it yet. Adding score consumption later means writing a formula; it does not require a serialization change.

---

## Adding a new self-healing mechanism

Pick one of the four loops based on producer/nurse pair:

- New gen-time check on L1's output → Loop 1. Add a validator next to `L1_SCHEMA_COMPLIANCE`.
- New runtime measurement that points at a candidate config region → Loop 2. Add a check that emits `RuntimeFailure` from `score_population`.
- New strategic-stall trigger → Loop 3 isn't a registry; it's the patience timer.
- New post-parse check on L2's output → Loop 4. Add a validator to `L2_OUTPUT_VALIDATORS` in `l2_validators.py`.

For each new validator: declare an `LLMOutputValidator` with a stable id, write the `check` callable, append to the appropriate registry. Both the prompt-section render and the persistence path are already wired — they iterate the registry, not a hard-coded list.
