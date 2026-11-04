# Self-Healing Internals

## Errors heal upward

The posture is [`../architecture.md`](../architecture.md) § Errors heal upward, tolerantly. The mechanics: `application/scoring/classification.py::DegradationCheck` — a bench check, run on every optimizer's arms whatever its eliminator — stops an arm **`broken`** once its fraction of failed measurements crosses `optimization.degradation_threshold`, or on a single fatal-classified sighting (`optimization.degradation_fatal_fastpath`); `broken` is recorded apart from **`eliminated`**, which only the optimizer's eliminator says (`domain/arms.py::Cut.kind`). Errors on every arm halt the run (`StopReason.BACKEND_UNREACHABLE`). **No measurement-level retry of the same (sample, candidate) pair**: transport resends below the measurement (`infrastructure/llm/spend_book.py::SendOutcome.resendable`) and the origin's one fresh re-score after a transient abort are not second verdicts. Failures surface at round end through the dispatch hub; an L1 validation failure reaches L1's own next call, not L2.

## Two axes, and who nurses

Failures attach to the candidate that produced them (fields on `OptSearchPoint`), never to the round. Every wound is two axes:

- **Detection point** picks the record type, score effect and lifecycle: parse-time → `ValidationFailure`, synthetic-0, per-candidate; mid-eval → `RuntimeFailure`, real score + rate, accumulated and deduped; post-parse → `ValidatorOutcome`, no score effect, per-round.
- **Nurse owner** falls out of the record type. Only a `RuntimeFailure` carries a real choice, so only it carries `owner: NurseOwner` ∈ `{L1, OPERATOR}` — an L1-retunable rate degradation, or an operator-terminal break no in-loop layer can reach.

The nurse is not the producer: L1 tends its own malformed proposal because it owns `pipeline_params`; L2 produces wounds and heals none; no field keys a nurse by producer. Post-parse lands on each layer's OWN memory (`L2Memory.guard_breaches`, `L3Memory.guard_breaches`). One nurse firing is one nudge, never a hard "do NOT propose X". `l1_critique → l1_generate` fires every round regardless: performance feedback, not healing.

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

**Wound 1.** `bench/children.py::overlay_failures` runs on every optimizer's child and fires when its `pipeline_overlay` proposes a value outside the axis's resolved space (`PipelineSchema.param_options`, `model` included), mismatches `param_types`, or touches a cost lever (`PARAM_FORBIDDEN_KEYS`); its `reason` is a subset of `ValidationFailure.reason`'s vocabulary. `hallucinated_node` is non-fatal: `merge_pipeline_params` strips the phantom edit, so the candidate's real edits still score and the wound rides only as routed signal.

**Wound 2.** `DegradationCheck` fires on the fatal-code fast path (`classify_result()`; one sighting ends the candidate, bypassing `min_samples` and the threshold) or rate-based (past `DegradationCheck.min_samples`, `degraded_rate >= optimization.degradation_threshold`). `application/scoring/arm_states.py::read_breakage` synthesises the `RuntimeFailure`; `Cycle.runtime_failures` derives the stream from the banked rounds, deduplicated by `(source, dominant_warning, observed_config)`, and nothing clears them. `_r_l1_wounds()` partitions NEW vs ACCUMULATED (`first_seen_round != current_round`), tagged `[owner=l1|operator]`; ACCUMULATED is the signal — L2's prior angle didn't take. `PoBBCheck` also stops a candidate mid-eval and is not a wound: no failure record, no LLM informed ([`../methods/candidate-elimination.md`](../methods/candidate-elimination.md) § The full elimination ladder).

**Wound 3.** L3 fires when `Ladder.l2.stall_count >= opt.l2_patience`, subject to `l3_patience`; it reads `NODE_LAYOUTS["l3_plan"]` and writes `dispatch/schemas.py::L3PlanOutput` — the only wound with cross-layer authority.

## Wound 4 — immediate, never patient

A REFUSED L2 layout edit makes `escalate_l2` run `_run_transition(L3, …)` immediately, bypassing both patiences; deterministic from L2's output on the round file, so resume reproduces it without a decision record; suppressed on an `escalation_ladder` that stops short of L3. `TransitionResult.l2_guard_breaches` (`escalation/firing.py`) holds every outcome `validate_l1_layout` returned plus `l1_layout_unparseable` (`dispatch/layout.py::unplaceable_edit`: an edit naming a slot its node's `NodeLayoutSpec.slots` does not open); `L2Context.land` puts them on `L2Memory.guard_breaches`, read through the `guard_breaches` injection. **The force-trigger reads the REFUSAL, not the stream** (`TransitionResult.l1_layout_refused`: a HARD breach or an unparseable edit) — the stream also carries the inert `l1_layout_voids_prefix` and `l1_layout_unchanged_from_prior`. A heal answers a refusal, not a stall, so it leaves the reading `l3_patience` compares against untouched (`escalation/state.py::l3_fired`); every L3 fire banks an `L3_ESCALATION_TRIGGER` whose `data.heal` says which kind it was.

## Validators are Evaluator-shaped

A gen-time check returns the `ValidationFailure`s it convicts; a post-parse check returns `ValidatorOutcome{validator_id, evidence}`s — only for a failure (no `passed` flag), counted as events (no `score`), routed to L3 structurally (no owner).

## `classify_result()` — fatal classification

`domain/results_health.py::classify_result` derives **fatal** and **infra** codes from the backend's neutral advisories and raw response shape (`pipeline_data.step_tokens.{node}`): backend = facts, optimizer = policy. Read the rule table in the function. Two rules a new row keeps: every `content_empty` row is gated on the result not having answered (the advisory describes one ATTEMPT; the backend's retry beside it can succeed), and a fatal code is deterministic for the whole config, so a rule that could fire on a row that answered correctly eliminates a good one. Never a tunable. Consumed via `is_deprecated()`: `DegradationCheck` eliminates on first sighting; `search_point_scorer.py::_split_off_deprecated_samples` evicts deprecated entries from the replay cache (re-measured with `retry_of_deprecated_cache=True`); `scoring/metrics.py::fold_cells` counts them apart as `CellFold.deprecated` ([`../concepts/scoring-and-memory.md`](../concepts/scoring-and-memory.md) § Which rows a reading counts). A load-boundary filter: trace records are still archived.
