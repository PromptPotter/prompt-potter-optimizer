# Self-Healing Internals

## Errors heal upward

**One error is innocent.** A single failed measurement — a validation failure on L1 output, a runtime failure mid-eval, a deprecated cache entry from a transient backend hiccup — is a technical issue, not the candidate's fault: the row is skipped, never scored, and evidence keeps accumulating on the same candidate. The default posture is "ignore and continue"; aborting requires evidence.

**Repeats are the candidate's.** Errors that keep repeating on ONE candidate are its fault: `application/scoring/classification.py::DegradationCheck` — a bench check, run on every optimizer's arms whatever its eliminator — stops it as **`broken`** once its fraction of failed measurements crosses the campaign's `optimization.degradation_threshold`, or on a single fatal-classified sighting (`optimization.degradation_fatal_fastpath`). A broken arm is charged to the candidate as a wound the next proposals read, recorded apart from **`eliminated`**, which only the optimizer's eliminator says (`domain/results.py::Cut.kind`).

**Errors on every arm are an outage**, nobody's fault: the run halts (`StopReason.BACKEND_UNREACHABLE`).

**No measurement-level retry of the same (sample, candidate) pair** — same inputs, same error, wasted budget. Transport resends below the measurement (`infrastructure/llm/spend_book.py::SendOutcome.resendable`) and the origin's one fresh re-score after a transient abort are not second verdicts. No mid-round LLM diagnostic, no per-error branching: a discarded candidate is cheap, since next round's proposal produces siblings on the same axis. Aggregated failures surface at round end and flow upward through the dispatch hub; an L1 validation failure reaches L1's own next call, not L2.

## Two axes, and who nurses

Failures attach to the **candidate that produced them** (direct fields on `OptSearchPoint`), never to the round, so a losing candidate's problem never disrupts the round winner. Every wound is two axes:

- **Detection point** picks the record type, the score effect and the lifecycle. Parse-time → `ValidationFailure`, synthetic-0, per-candidate. Mid-eval → `RuntimeFailure`, real score + rate, accumulated and deduped. Post-parse → `ValidatorOutcome`, no score effect, per-round.
- **Nurse owner** falls out of the record type. Only a `RuntimeFailure` carries a real choice, so only it carries `owner: NurseOwner` ∈ `{L1, OPERATOR}` — an L1-retunable rate degradation, or an operator-terminal break (the token blowout) no in-loop layer can reach.

The **nurse is not the producer**: L1 tends its own malformed proposal because it owns `pipeline_params`, and a break whose only fix is a locked surface escalates to the operator. L2 produces wounds and heals none. No field keys a nurse by producer, and none may be added. Post-parse lands on each layer's OWN memory (`L2Memory.guard_breaches`, `L3Memory.guard_breaches`), so neither layer writes the other's state.

**Healing is gradual.** One nurse firing is one nudge: soft pointers toward the right region beat hard one-shot briefs ("do NOT propose X"), because wounds 1 and 4 retrigger on the new evidence, wound 2's trail accumulates, and wound 3 fires only on stall but reshapes both L1 and L2. The operator sees per-sample `⚠ … ↳` annotations on round reports — an audit trail, not alerts. `l1_critique → l1_generate` fires every round regardless: performance feedback, not healing.

## The wounds, mapped to the two axes

Rendering collapses to two owner-grouped signals: `l1_wounds` = validation + runtime, `guard_breaches` = L2 + L3 post-parse.

|  | Wound 1 | Wound 2 | Wound 3 | Wound 4 |
|---|---|---|---|---|
| **Producer → Nurse** | L1 → **L1** | L1 → **L1 / OPERATOR** | L2 → L3 | L2 → L3 |
| **Detector** | `bench/children.py::overlay_failures` + `validators/l1_strict.py`, at `parse_population()` | `DegradationCheck`, mid-eval | `escalate_l2` patience (`escalation/firing.py`) | `validate_l1_layout` (post-parse) |
| **Record** | `ValidationFailure` | `RuntimeFailure` | (patience event) | `ValidatorOutcome` |
| **Storage** | the arm's `ArmState.validation_failures`; a round with no arm, `L1Readout.l1_parse_failure` | `Cycle.runtime_failures`, derived off the banked rounds | `Ladder.l2.stall_count` | `L2Memory.guard_breaches` |
| **Nurse's writeback** | L1 re-proposes a valid override | L1 retunes the node config · or operator trims schema/model | `L3Memory.plan` | `L3Memory.plan` |
| **Score effect** | synthetic 0 (`runner/measurement.py::_conclude_arm`) | real score, candidate eliminated mid-eval | none | none — fires after L2 ran |

## Wound 1 — what trips the validator

`bench/children.py::overlay_failures` runs on every optimizer's child and fires when its `pipeline_overlay` proposes a value outside the axis's resolved space (`PipelineSchema.param_options`, `model` included), mismatches `param_types`, or touches a cost lever (`PARAM_FORBIDDEN_KEYS`). Its `reason` is a subset of the vocabulary `ValidationFailure.reason` declares.

**`hallucinated_node` is non-fatal.** The override named a node absent from the active schema; `merge_pipeline_params` strips the phantom edit, so the candidate's real edits still score, and the wound rides only as routed signal — `l1_wounds` self-correction and the `validation_failure_rate` evaluator, which makes hallucination-rate an L4-visible quality axis.

## Wound 2 — two paths, and why ACCUMULATED is the signal

`DegradationCheck` fires on either path, both producing `StopSignal(outcome=BROKEN)`:

1. **Fatal-code fast path.** `classify_result()` derives a fatal code from raw response shape; one sighting ends the candidate, bypassing `min_samples` and the threshold.
2. **Rate-based.** Past `DegradationCheck.min_samples`, eliminate once `degraded_rate >= optimization.degradation_threshold`.

`application/scoring/arm_states.py::read_breakage` synthesises `RuntimeFailure(source, dominant_warning, degraded_rate, …)` from the check plus the observed pipeline_params. Each record rides its `Cut` arm's `Broken` reason, and `Cycle.runtime_failures` derives the stream from the banked rounds — concluded siblings' first — deduplicated by `(source, dominant_warning, observed_config)`; nothing clears them, since they are discovered runtime constraints. `_r_l1_wounds()` partitions it into NEW vs ACCUMULATED (`first_seen_round != current_round`), tagged `[owner=l1|operator]`. **ACCUMULATED is the real signal** — a surviving item means L2's prior angle didn't take; if it keeps growing, Wound 3 takes over.

`PoBBCheck` also stops a candidate mid-eval and is **not** a wound: it writes no failure record and informs no LLM ([`../methods/candidate-elimination.md`](../methods/candidate-elimination.md) § The full elimination ladder).

## Wound 3 — what L3 reads and writes

L3 fires when `Ladder.l2.stall_count >= opt.l2_patience`, subject to its own `l3_patience`. It reads its layout, `NODE_LAYOUTS["l3_plan"]`, and writes `dispatch/schemas.py::L3PlanOutput`; the new `plan` feeds every node whose layout places it. The only wound with cross-layer authority — L3 changes the strategy L2 and L1 work within.

## Wound 4 — immediate, never patient

A REFUSED L2 layout edit makes `escalate_l2` run `_run_transition(L3, …)` *immediately*, bypassing `l2_patience` and `l3_patience`: broken L2 output is not "wait and see". The trigger is deterministic from L2's output on the round file, so resume reproduces it without a separate decision record; it is suppressed on an `escalation_ladder` that stops short of L3.

`TransitionResult.l2_guard_breaches` (`escalation/firing.py`) holds every outcome `validate_l1_layout` returned, plus `l1_layout_unparseable`, which `dispatch/layout.py::unplaceable_edit` returns for an edit naming a slot no layout has. `L2Context.land` puts them on `L2Memory.guard_breaches`, which L2 and L3 read through the `guard_breaches` injection on their next fire.

**The force-trigger reads the REFUSAL, not the stream** (`TransitionResult.l1_layout_refused`: a HARD breach or an unparseable edit). The stream also carries two inert members — `l1_layout_voids_prefix` (a cache-cost report on an ACCEPTED layout) and `l1_layout_unchanged_from_prior` (a no-op) — and reading it replanned the cycle on both. A heal answers a refusal, not a stall, so it leaves the reading `l3_patience` compares against untouched (`escalation/state.py::l3_fired`). Every L3 fire banks an `L3_ESCALATION_TRIGGER` whose `data.heal` says which kind it was. There is no `task_context` validator: no L1/L2/L3 wire schema declares a field of the frozen framing, so a stale-repeat breach is not representable.

## Validators are Evaluator-shaped

A gen-time check is a plain function returning the `ValidationFailure`s it convicts; a post-parse check returns `ValidatorOutcome{validator_id, evidence}`s. An outcome exists only for a failure, so there is no `passed` flag; the self-healers count **events**, not graded scores, so there is no `score` field; and a guard-breach outcome routes to L3 structurally, so there is no owner to store.

## `classify_result()` — fatal classification

`classify_result()` (`domain/results_health.py`) derives **fatal** and **infra** codes from the backend's neutral advisories and raw response shape (`pipeline_data.step_tokens.{node}`). Backend = facts, optimizer = policy. **Read the rule table in the function, never from a copy here.**

Two rules a new row must keep. Every `content_empty` row is gated on **the result not having answered** — the advisory describes one ATTEMPT, and the backend's retry beside it can succeed. And a fatal code is deterministic for the whole config — one sighting eliminates the candidate, so a rule allowed to fire on a row that answered *correctly* eliminates a good one. Grow the rule table (never expose it as a tunable) when a new pattern proves equally conclusive.

Load-boundary effects, consumed via `is_deprecated()`: `DegradationCheck` eliminates on first sighting; `search_point_scorer.py::_split_off_deprecated_samples` evicts deprecated entries from the replay cache so they are re-measured with `retry_of_deprecated_cache=True`; and `scoring/metrics.py::fold_cells` counts them apart as `CellFold.deprecated`. Which rows a reading counts is [`../concepts/scoring-and-memory.md`](../concepts/scoring-and-memory.md#which-rows-a-reading-counts)'s. This is a load-boundary filter, not a score-time fallback: trace records are still archived, and only cache reuse is blocked — [`../concepts/scoring-and-memory.md`](../concepts/scoring-and-memory.md#deprecated-samples).
