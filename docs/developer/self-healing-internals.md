# Self-Healing Internals

Failures attach to the **candidate that produced them** (direct fields on `OptSearchPoint`), never to the round, so a losing candidate's problem never disrupts the round winner.

Every wound is two axes, not a four-item taxonomy:

- **Detection point** picks the record type, the score effect and the lifecycle. Parse-time → `ValidationFailure`, synthetic-0, per-candidate. Mid-eval → `RuntimeFailure`, real score + rate, accumulated and deduped. Post-parse → `ValidatorOutcome`, no score effect, per-round.
- **Nurse owner** picks who heals it, and falls out of the record type. Only a `RuntimeFailure` carries a real choice, so only it carries `owner: NurseOwner` ∈ `{L1, OPERATOR}` — an L1-retunable rate degradation, or an operator-terminal break (the token blowout) no in-loop layer can reach.

The **nurse is not the producer**: L1 tends its own malformed proposal because it owns `pipeline_params`, and a break whose only fix is a locked surface escalates to the operator rather than churning at a layer that cannot reach the lever. L2 produces wounds and heals none. The producer-keyed `nurse_target` field is **retired** — gone from the code entirely, and no test guards this ([`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)), so do not reintroduce it.

Three detection points but **four** typed `WoundChannels` lists: post-parse splits `l2_guard_breaches` from `l3_guard_breaches` because `escalate_l2` reads L2's stream as its L3-fire trigger while L3 self-reads its own — distinct consumers, so merging them would need a discriminator. A fifth channel, `l3_note`, is a sticky free-text L3→L2 steer, not a failure record.

**Healing is gradual.** One nurse firing is one nudge, not a guaranteed fix. Soft pointers toward the right region beat hard one-shot briefs ("do NOT propose X"), because the nurse is built to retry — wounds 1 and 4 retrigger on the new evidence, wound 2's trail accumulates so L2 must change angle if ACCUMULATED survives, and wound 3 fires only on stall but reshapes both L1 and L2.

**User-visible surface:** per-sample `⚠ … ↳` annotations on round reports. An audit trail, not alerts. Separately, `l1_critique → l1_generate` fires every round regardless of failure — performance-driven feedback, not failure-driven healing.

## The wounds, mapped to the two axes

Storage stays four typed lists (+ `l3_note`); **rendering collapses to two owner-grouped signals** — `l1_wounds` = validation + runtime, `guard_breaches` = L2 + L3 post-parse. This table is the roster; each § below adds only what it cannot hold.

|  | Wound 1 | Wound 2 | Wound 3 | Wound 4 |
|---|---|---|---|---|
| **Producer → Nurse** (owner-keyed, not producer-keyed) | L1 → **L1** | L1 → **L1 / OPERATOR** | L2 → L3 | L2 → L3 |
| **Owner source** | structural (L1's own output) | `RuntimeFailure.owner`: `L1` (rate) · `OPERATOR` (fatal) | (patience event) | structural (guard stream → L3) |
| **Detector** | `L1_SCHEMA_COMPLIANCE` (`validators/l1_strict.py`), at `parse_population()` | `DegradationCheck` (`pobb/checks.py`), mid-eval | `escalate_l2` patience (`escalation/firing.py`) | `validate_l1_layout` (post-parse) |
| **Failure record class** | `ValidationFailure` | `RuntimeFailure` | (patience event, no record) | `ValidatorOutcome` |
| **OSP storage** | `validation_failures` | `runtime_failures` | `escalation.l2.stall_count` | `l2_guard_breaches` |
| **Outer-memory mirror** | none (L2 reads `candidate_scores`) | cumulative on `cycle.opt_sp.wounds.runtime_failures` | none | per-round on the OSP itself |
| **Nurse prompt slot** | `{{l1_wounds}}` | `{{l1_wounds}}` | (whole `l3_plan` template) | `{{guard_breaches}}` |
| **Renderer** | `_r_l1_wounds` | `_r_l1_wounds` | `_r_l1_wounds` | `_r_guard_breaches` |
| **Nurse's writeback** | L1 re-proposes a valid override | L1 retunes the node config · or operator trims schema/model | `cycle.opt_sp.plan` | `cycle.opt_sp.plan` |
| **Score effect** | synthetic 0 (Path 1 in `score_one_candidate`) | real score, candidate eliminated mid-eval | none | none — fires after L2 ran |

## Wound 1 — what trips the validator

`L1_SCHEMA_COMPLIANCE` wraps `validate_overrides()` and fires when L1's `pipeline_overlay` proposes a value outside the axis's resolved space (`PipelineSchema.param_options`, which answers for every axis including `model` — so a rung the node declared but the chosen model refuses is caught here too), mismatched against the declared `param_types`, or touching a cost lever (`PARAM_FORBIDDEN_KEYS` — `provider`/`route_order`, always locked; `model` is an ordinary axis and validates against the node's permitted set). `evidence["failures"]` is `list[ValidationFailure(axis, value, allowed, reason)]`, and `reason` is this validator's subset of the vocabulary `ValidationFailure.reason` declares: `not_in_available_models`, `not_in_param_allowed_values`, `not_accepted_by_model`, `type_mismatch`, `unknown_param`, `forbidden_axis`, `hallucinated_node`.

**Exception — `hallucinated_node` is non-fatal.** The override named a node absent from the active schema, the node-name twin of `validate_l1_layout`'s unknown-placeholder wound (`build_l1_response_schema`'s node-name enum is advisory under `strict=False`). The phantom edit is stripped from the wire — `merge_pipeline_params` drops nodes outside `active_steps` — so the candidate's real edits still score; the reason-aware Path-1 gate skips synthetic-0 and the wound rides along only as routed signal, feeding `l1_wounds` self-correction and the `validation_failure_rate` evaluator, which makes hallucination-rate an L4-visible quality axis.

## Wound 2 — two paths, and why ACCUMULATED is the signal

`DegradationCheck` fires on either path, both producing `EscalationSignal(target=ELIMINATE_CANDIDATE)`:

1. **Fatal-code fast path.** `classify_result()` derives a fatal code from raw response shape. One sighting ends the candidate; bypasses `min_samples`/`threshold`.
2. **Rate-based.** After `min_samples=3`, if `degraded_rate >= 0.4`, eliminate.

`score_population` synthesises `RuntimeFailure(source, dominant_warning, warning_types, degraded_rate, …)` from the check plus the observed pipeline_params, then continues with the next candidate. End-of-round, `execute_round` mirrors new records onto the cycle's list, deduplicated by `(source, dominant_warning, observed_config)`, and never clears them — they represent discovered runtime constraints.

`_r_l1_wounds()` partitions the runtime block into NEW (this round) vs ACCUMULATED (`first_seen_round != current_round`) and tags each entry `[owner=l1|operator]`. **ACCUMULATED is the real signal** — a surviving item means L2's prior angle didn't take. If it keeps growing, Wound 3 takes over.

## Wound 3 — what L3 reads and writes

L3 fires when `esc.l2.stall_count >= opt.l2_patience`, subject to its own `l3_patience`. Its prompt (`promptpotter/assets/optimizer/pipeline.yaml::resolved_prompts['l3_plan/1']`) reads `{{l1_wounds}}`, `{{guard_breaches}}`, `{{plan}}`, `{{task_context}}`, `{{diagnostics}}` and `{{critique}}`, and writes a new `plan` (optionally `pipeline_params`) that feeds both L1's `{{plan}}` slot and the next L2 invocation. The only wound with cross-layer authority — L3 changes pipeline composition or strategy framing.

## Wound 4 — immediate, never patient

**Any** breach after L2 runs makes `escalate_l2` invoke `L3ModifyPlan` *immediately*, bypassing `l2_patience` and `l3_patience`: broken L2 output is not "wait and see". The trigger is deterministic from L2's output, already on the round file, so resume reproduces it without a separate decision record. Breaches are written by `apply_side_effects` off `TransitionResult.l2_guard_breaches`.

**Every breach is hard** — owned by [`dispatch-hub.md`](dispatch-hub.md) § Wound 4, which also holds the breach set. This layer routes every breach straight to L3 and has no soft-reject tier.

## Validators are Evaluator-shaped

`LLMOutputValidator` is `{id, check}` and its `check` returns `ValidatorOutcome{validator_id, evidence}` or `None`. An outcome exists only for a failure, so there is no `passed` flag; the self-healers count **events** rather than graded scores, so there is no `score` field; and a guard-breach outcome routes to L3 structurally, so there is no owner to store.

## Optimizer-memory state

The fields that travel with each candidate cross-round are `domain/opt_search_point.py::L2L3Memory` — read the roster and each field's lifecycle off the model, which is frozen and cannot drift from itself.

Two that the model cannot tell you. **`wounds.l3_note` is sticky free-text and not a failure record** — L3 sets it to steer L2, and it survives every parent swap (an L1 win as well as an L2/L3 transition) through the `Cycle.adopt` seam's `copy_memory_to`, the only field there with that lifetime. And **the L1 critique is not on `L2L3Memory` at all**: it lives on `RoundResult.critique`, which the dispatch hub's `critique` injection reads from `cycle.latest_round.critique`, the same way per-round trajectory lives on `Cycle.rounds` rather than the OSP.

## The prompt-budget unit (a separate mechanism)

Not a wound: it guards the size of a composed optimizer prompt, has no producer→nurse pair, and rides the `injection_table()` registry, `DispatchHub` and the existing `StopLoop` / round-loop teardown rather than a sidecar. Two healing modes:

1. **Truncate** — per-injection `char_cap`; an over-cap block is section-aware truncated in the hub (`facade.py`), with an `injection_budget_overrun` warning naming the overrun + dropped sections.
2. **Halt** — `RENDER_ERROR`: an injection renderer *raised* (usually code drift); operator-recoverable stop.

## Mid-eval termination — what is and isn't healing

Two mid-eval checks stop a candidate and only one is healing. `DegradationCheck` is Wound 2. **`PoBBCheck` is not a wound** — it stops scoring because the candidate's posterior probability of being the round's best fell below ε after `n_min` queries, writes no failure record and informs no LLM. See [`../methods/candidate-elimination.md`](../methods/candidate-elimination.md).

## `classify_result()` — fatal classification

`classify_result()` (`domain/rendering.py`) derives **fatal** and **infra** codes from the backend's neutral advisories (`llm_only:content_empty`, `*:content_filtered`, …) and raw response shape (`pipeline_data.step_tokens.{node}`: normalised `finish_reason`, `reasoning` token count). Backend = facts, optimizer = policy.

Every `content_empty` row is gated on **the result not having answered** — the advisory describes one ATTEMPT, the backend retries beside it, and that retry can succeed, so a row carrying a real `predicted` is not an empty response whatever the advisory says. Among the unanswered, `reasoning_tokens > 0` proves the model **worked** (a refusal carries content, or `content_filter`), so emitting nothing after thinking is route shape whatever ended the call — `stop` and `length` are one fault at two budgets.

- `content_empty`, unanswered, `reasoning_tokens > 0`, `finish_reason=length` → `reasoning_budget_exhausted` *(infra)*
- `content_empty`, unanswered, `reasoning_tokens > 0`, any other `finish_reason` → `reasoning_only_response` *(infra)*
- `content_empty`, unanswered, `reasoning_tokens = 0`, `finish_reason=length` → `output_truncated` *(infra)*
- `content_empty`, unanswered, `reasoning_tokens = 0`, any other `finish_reason` → `empty_response` *(fatal)*
- `*:content_filtered` → passthrough as fatal

A fatal code is deterministic for the whole config — one sighting proves the candidate is broken for every remaining query, which is why a rule allowed to fire on a row that answered *correctly* eliminates a good candidate. Grow the rule table (don't expose it as a tunable) when a new pattern proves equally conclusive.

Three load-boundary effects, consumed via `is_deprecated()`: `DegradationCheck` eliminates the candidate on first sighting; `score_search_point` runs `_filter_deprecated_priors` over `archive.load_reusable_results` so fatal entries are evicted from cache and re-measured with `retry_of_deprecated_cache=True`; and `_compute_accuracy` partitions deprecated rows into their own count, out of `hits`, `total`, `errors` and the accuracy denominator.

This is a load-boundary filter, not a score-time fallback: trace records are still archived for forensic value, and only cache reuse and primary-stat aggregation are blocked. Sanctioned alongside the `score_population()` validation-failure synthetic-0 — see [`../concepts/scoring-and-memory.md`](../concepts/scoring-and-memory.md#deprecated-samples).

## Adding a new mechanism

Pick the storage stream by detector + score-effect; the owner falls out of the record type, so you never wire a nurse by hand.

- New gen-time check on L1's output → **Wound 1**. Add a validator next to `L1_SCHEMA_COMPLIANCE`.
- New runtime measurement pointing at a candidate config region → **Wound 2**. Add a check that emits `RuntimeFailure` from `l1/score/signal_effect.py`; stamp `owner=NurseOwner.L1` when L1 can retune it, `owner=NurseOwner.OPERATOR` when only the operator can.
- New strategic-stall trigger → **Wound 3** isn't a registry; it's the patience timer.
- New post-parse check on L2/L3's output → **Wound 4**. L3's side has a registry (`L3_OUTPUT_VALIDATORS`, `validators/l3_output.py`); L2's is the layout check itself (`domain/l1_layout.py::validate_l1_layout`) — there is no `L2_OUTPUT_VALIDATORS` to append to, so a new L2 check means extending that validator or standing a registry up.

For each: declare `LLMOutputValidator` with a stable id, write the `check` callable, append to the appropriate registry. Prompt-section render and persistence path are already wired — they iterate the registry, not a hard-coded list. Only add a `NurseOwner` member when a producer actually stamps it (today only `RuntimeFailure` does).
