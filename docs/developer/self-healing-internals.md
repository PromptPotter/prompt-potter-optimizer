# Self-Healing Internals

Implementation of the four wounds. Concept overview: [`../concepts/self-healing.md`](../concepts/self-healing.md). This page is the wiring map.

Failures attach to the **candidate that produced them** (direct fields on `OptSearchPoint`), never to the round, so a losing candidate's problem never disrupts the round winner. New mechanisms must land as one of the four wounds below — no sidecars, no silent drops. The one sanctioned non-wound healing class is the prompt-budget unit (see [Beyond the wounds](#beyond-the-wounds--the-prompt-budget-unit-tier-2)); it still rides the dispatch-hub registry, not a sidecar.

## The four wounds at a glance

|  | Wound 1 | Wound 2 | Wound 3 | Wound 4 |
|---|---|---|---|---|
| **Producer → Nurse** | L1 → L2 | L1 → L2 | L2 → L3 | L2 → L3 |
| **Detector** | `L1_SCHEMA_COMPLIANCE` validator | `DegradationCheck` (mid-eval) | `escalate_l2` patience | `L2_*` validators (post-parse) |
| **Failure record class** | `ValidationFailure` | `RuntimeFailure` | (patience event, no record) | `ValidatorOutcome` |
| **OSP storage** | `validation_failures` | `runtime_failures` | `escalation.l2.stall_count` | `l2_guard_breaches` |
| **Outer-memory mirror** | none (L2 reads `candidate_scores`) | cumulative on `cycle.opt_sp.wounds.runtime_failures` | none | per-round on the OSP itself |
| **Nurse prompt slot** | `{{validation_failures}}` | `{{runtime_failures}}` | (whole `l3_plan` template) | `{{l2_guard_breaches_section}}` |
| **Renderer** | `_r_validation_failures` | `_r_runtime_failures` | `_r_runtime_failures` | `_r_l2_guard_breaches` |
| **Nurse's writeback** | `cycle.opt_sp.task_context` | `task_context` / scheme + text overrides | `cycle.opt_sp.plan` | `cycle.opt_sp.plan` |
| **Score effect** | synthetic 0 (Path 1 in `score_population`) | real score, candidate eliminated mid-eval | none | none — fires after L2 ran |

## Beyond the wounds — the prompt-budget unit (tier 2)

The four wounds are the **vanilla** healing class: each is one
producer→nurse channel — one detector, one failure record, one nurse
layer, one prompt slot. Uniform by design.

The **prompt-budget unit** (full spec:
[`../specs/dispatch-prompt-budget.md`](../specs/dispatch-prompt-budget.md))
is a different kind of mechanism — the project's most sophisticated
healing unit. It guards one concern, the size of a composed optimizer
meta-prompt, with **four healing modes stacked on one another**:

1. **Truncate** — per-injection `char_cap`; an LLM-authored block over
   its cap is cut + warned in `DispatchHub.render`.
2. **Shed** — the aggregate allocator (`facade._apply_budget`) drops
   whole low-tier injections when the composed prompt exceeds
   `OPTIMIZER_PROMPT_CHAR_BUDGET`.
3. **L2 self-heal** — the `prompt_budget_status` injection shows L2 every
   cap + the live size of any overrun; L2 trims the blocks it authors.
4. **Halt** — two distinct operator-recoverable stops: `RENDER_ERROR`
   (an injection renderer *raised* — code drift) and `PROMPT_BUDGET`
   (the prompt won't fit even after shedding everything).

Why it is not a wound: it has no single producer→nurse pair. Modes 1–2
are mechanical (no LLM), mode 3 is LLM-routed but only for blocks L2
itself authored, mode 4 escalates to a human. It is the one place
mechanical healing, LLM-routed healing, and a graceful halt meet on one
concern — hence "tier 2". It still obeys the no-sidecar rule: every mode
rides the `INJECTIONS` registry, `DispatchHub`, and the existing
`StopLoop` / round-loop teardown.

## Wound 1 — L2 tends L1's `ValidationFailure`

`L1_SCHEMA_COMPLIANCE` (`application/optimization/l1.py`) wraps `validate_overrides()` and runs at L1 parse time in `parse_population()`. When L1's `pipeline_params_override` proposes a value outside `PipelineSchema.available_models`, outside a node's `param_allowed_values`, mismatched against the declared `param_types`, or touches an operator-locked axis (`PARAM_FORBIDDEN_KEYS = {model, provider}`, gated by `OptimizationConfig.forbidden_axes_strict` — default on), the validator emits a `ValidatorOutcome` whose `evidence["failures"]` is `list[ValidationFailure(axis, value, allowed, reason)]`. `reason` is one of `not_in_available_models`, `not_in_param_allowed_values`, `type_mismatch`, or `forbidden_axis`.

Failures land on `OptSearchPoint.wounds.validation_failures` — outer-layer optimizer state, not target-layer. Effect chain: `score_population()` shortcuts to a synthetic-0 report (Path 1) → inline winner-selection deprioritises the candidate → round checkpoint persists the failure.

L2's template (`optimizer_pipeline.json::resolved_prompts['l2_context/1']`) renders `{{validation_failures}}` via `_r_validation_failures()`. L2 writes a brief pointing L1 toward the allowed region. Healing is gradual — if L1 still proposes invalid values next round, the validator fires again and L2 gets fresh evidence.

## Wound 2 — L2 tends L1's `RuntimeFailure`

`DegradationCheck` (`application/optimization/pobb/elimination/checks.py`) fires mid-evaluation. Two paths:

1. **Fatal-code fast path.** `classify_result()` derives a fatal code from raw response shape (`finish_reason=length` + `reasoning_tokens > 0` → `reasoning_budget_exhausted`). One sighting ends the candidate; bypasses `min_queries`/`threshold`.
2. **Rate-based.** After `min_queries=3`, if `degraded_rate >= 0.4`, eliminate.

Both produce `EscalationSignal(target=ELIMINATE_CANDIDATE)`. `score_population` synthesises `RuntimeFailure(source, dominant_warning, warning_types, degraded_rate, …)` from the check + observed pipeline_params, attaches to `cp.osp.wounds.runtime_failures`, and continues with the next candidate — round winner unaffected.

End-of-round, `execute_round` mirrors new `RuntimeFailure`s onto `cycle.opt_sp.wounds.runtime_failures`, deduplicated by `(source, dominant_warning, observed_config)`. Never cleared — represents discovered runtime constraints.

`_r_runtime_failures()` partitions into NEW (this round) vs ACCUMULATED (`first_seen_round != current_round`). ACCUMULATED is the real signal — surviving items mean L2's prior angle didn't take. L2 updates its outputs; if ACCUMULATED keeps growing, Wound 3 takes over.

## Wound 3 — L3 replans on L2 stall

`escalate_l2` (`application/optimization/cycle.py`) checks `esc.l2.stall_count >= opt.l2_patience`. When stalled, L3 fires (subject to its own `l3_patience`).

L3's prompt (`optimizer_pipeline.json::resolved_prompts['l3_plan/1']`) reads:

- `{{runtime_failures}}` — accumulated `RuntimeFailure` trail rendered by `_r_runtime_failures()`.
- `{{l2_guard_breaches}}` — Wound 4 evidence rendered by `_r_l2_guard_breaches()`.
- `{{l3_guard_breaches}}` — L3's own past validator failures rendered by `_r_l3_guard_breaches()`.
- `{{plan}}`, `{{task_context}}`, `{{diagnostics}}`, `{{validation_failures}}`, `{{critique}}`.

L3 writes a new `plan` (and optionally `pipeline_params`). Lands on `OptSearchPoint.plan`; feeds both L1's `{{plan}}` slot and the next L2 invocation. Only wound with cross-layer authority — L3 changes pipeline composition or strategy framing.

## Wound 4 — L3 tends L2's parsed-output failure

`L2_OUTPUT_VALIDATORS` registry (`application/optimization/l2_validators.py`) — three starter validators, all `nurse_target="l3"`:

| Validator id | Detects |
|---|---|
| `l2_cross_field_duplication` | Same N+ line block in ≥2 of `{brief, template_override, text_overrides[*]}` |
| `l2_verbatim_self_repeat` | L2's `brief` this round equals previous round's brief on OSP |
| `l2_catalogue_redundancy` | `text_overrides[section]` equals existing override on OSP for that section |

Validators run inside `L2RefineStrategy.build_result()` between LLM-output parse and `TransitionResult` construction. Outcomes ride on `TransitionResult.l2_guard_breaches` and are written by `apply_side_effects` to `cycle.opt_sp.wounds.l2_guard_breaches`.

When `cycle.opt_sp.wounds.l2_guard_breaches` is non-empty after L2 runs, `escalate_l2` invokes `L3ModifyPlan` *immediately* — bypassing `l2_patience` and `l3_patience`. Broken L2 output is not "wait and see". Trigger is deterministic from L2's output (already on the round file), so resume reproduces it without a separate decision record.

## Validators are Evaluator-shaped

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

Mirrors `Evaluator` (`name, description, scope, compute, …`) so future composite scoring can read validator outcomes through the same channel. `score` is the seam — currently for display only, but flows into a `campaign.json::scoring` formula the same way `accuracy` does.

## Optimizer-memory state

Fields enumerated in `OptSearchPoint.MEMORY_FIELDS` (`domain/opt_search_point.py`) travel with each candidate cross-round:

| Field | Lifecycle | Wound |
|---|---|---|
| `task_context` | persistent, accumulative; merged on each L2 fire | Wound 1, Wound 2 — L2 writeback |
| `validation_failures` | per-candidate (set at L1 parse) | Wound 1 — L2 reads |
| `runtime_failures` | per-candidate + cumulative outer-memory mirror | Wound 2 + 3 |
| `l2_guard_breaches` | per-round, set by L2 post-parse | Wound 4 — L3 reads |
| `l3_guard_breaches` | per-round, set by L3 post-parse | L3 self-heal |

The L1 critique itself lives on `RoundResult.critique` (a dict, not in `MEMORY_FIELDS`); the dispatch hub's `critique` injection reads it from `cycle.latest_round.critique`. Per-round trajectory + cumulative warned-query subset (probe-round source) live on `Cycle` (`Cycle.rounds`, `Cycle.warned_queries`), not OSP.

## Mid-eval termination — what is and isn't healing

| Check | Fires | Self-healing? |
|---|---|---|
| **Validation** (`L1_SCHEMA_COMPLIANCE`) | L1 parse, before evaluation | Wound 1 |
| **`PoBBCheck`** | Mid-eval, after `n_min` queries (Bayesian Posterior-of-Being-Best) | No — pure statistical termination, no failure record |
| **`DegradationCheck`** | Mid-eval, after 3 queries (or first fatal) | Wound 2 |

`PoBBCheck` stops scoring this candidate (posterior probability of being round's best fell below ε); no failure record, no LLM informed. See [`../methods/candidate-elimination.md`](../methods/candidate-elimination.md).

## `classify_result()` — fatal classification

`classify_result()` (`application/optimization/pobb/elimination/classification.py`) derives **fatal codes** from the backend's neutral advisories (`llm_only:content_empty`, `*:content_filtered`, …) and raw response shape (`pipeline_data.step_tokens.{node}`: normalised `finish_reason`, `reasoning` token count). Backend = facts, optimizer = policy.

Rule table:

- `content_empty` + `finish_reason=length` + `reasoning_tokens > 0` → `reasoning_budget_exhausted`
- `content_empty` + `finish_reason=length` + `reasoning_tokens = 0` → `output_truncated`
- `content_empty` + any other `finish_reason` → `empty_response`
- `*:content_filtered` → passthrough as fatal

Fatal codes are deterministic for the whole config — one sighting proves the candidate is broken for every remaining query. Grow the rule table (don't expose it as a tunable) when a new pattern proves equally conclusive.

Three load-boundary effects (consumed via `is_deprecated()`):

1. **Candidate elimination** — `DegradationCheck` fast-path returns `EscalationSignal(target=ELIMINATE_CANDIDATE)` on first sighting; bypasses `min_queries` and `threshold`.
2. **Cache eviction** — `score_search_point` runs `_filter_deprecated_priors` on `archive.load_reusable_results` and drops every entry the classifier marks fatal. Fresh re-measurements receive `retry_of_deprecated_cache=True`.
3. **Stats exclusion** — `_compute_accuracy` partitions deprecated rows into a separate `deprecated` count and excludes them from `hits`, `total`, `errors`, accuracy denominator.

This is a load-boundary filter, not a score-time fallback. Trace records continue to be archived (forensic value); only cache reuse and primary-stat aggregation are blocked. Sanctioned alongside the `score_population()` validation-failure synthetic-0 — see [`../concepts/scoring-and-memory.md`](../concepts/scoring-and-memory.md#deprecated-samples).

## Adding a new mechanism

Pick one of the four wounds based on producer/nurse pair:

- New gen-time check on L1's output → **Wound 1**. Add a validator next to `L1_SCHEMA_COMPLIANCE`.
- New runtime measurement pointing at a candidate config region → **Wound 2**. Add a check that emits `RuntimeFailure` from `score_population`.
- New strategic-stall trigger → **Wound 3** isn't a registry; it's the patience timer.
- New post-parse check on L2's output → **Wound 4**. Add a validator to `L2_OUTPUT_VALIDATORS` in `l2_validators.py`.

For each: declare `LLMOutputValidator` with stable id, write the `check` callable, append to the appropriate registry. Prompt-section render and persistence path are already wired — they iterate the registry, not a hard-coded list.
