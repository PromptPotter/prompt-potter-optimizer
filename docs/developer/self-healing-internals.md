# Self-Healing Internals

Failures attach to the **candidate that produced them** (direct fields on `OptSearchPoint`), never to the round, so a losing candidate's problem never disrupts the round winner.

**Two axes describe every wound — there is no "four-wound taxonomy" to memorise.** What used to read as four parallel pipelines is two orthogonal axes:

- **Detection point** → record type + score effect + lifecycle. Parse-time → `ValidationFailure`, synthetic-0 (bar the non-fatal `hallucinated_node`), per-candidate. Mid-eval → `RuntimeFailure`, real score + rate, accumulated/deduped. Post-parse → `ValidatorOutcome`, no score effect, per-round. Three detection points, but **four** typed `WoundChannels` lists: post-parse splits `l2_guard_breaches` vs `l3_guard_breaches` because `escalate_l2` reads L2's breach-stream as its L3-fire trigger while L3 self-reads its own — distinct consumers, not redundancy (merging them would need a discriminator = over-collapse). A fifth channel, `l3_note`, is a sticky free-text L3→L2 steer, not a failure record.
- **Nurse owner** → who heals it. This axis is **mostly structural — it falls out of the record type**, so only one record carries a field for it. A `ValidationFailure` is *always* L1's own malformed output (L1 retunes). A guard-breach `ValidatorOutcome` *always* routes to L3 — via the non-empty-`l2_guard_breaches`-stream → `escalate_l2` mechanism, not a stored value. Only a `RuntimeFailure` carries a genuine choice, so it (and only it) carries an `owner: NurseOwner` field ∈ `{L1, OPERATOR}`: an L1-retunable rate-degradation vs an operator-terminal break (the token blowout) no in-loop layer can reach. L2 produces wounds (its guard breaches) but heals none.

A new mechanism picks its stream by **detection point**; its owner is implied by the record type, except a `RuntimeFailure` which stamps `owner` where the choice is real. No nurse wired by hand, no sidecar. The producer-keyed `nurse_target` field is **retired** — gone from the code entirely (don't reintroduce it; no test guards this, see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)). The prompt-budget unit (below) is a **separate** mechanism, not a wound.

## Producer / detector / nurse

- **Producer** — the LLM that left the wound (L1 or L2).
- **Detector** — the deterministic check that caught it (validator, mid-eval check, patience timer).
- **Nurse** — the layer that **owns the fix**, **not** the producer. So L1 tends its own malformed proposal (it owns pipeline_params), and a deterministic-for-config break whose only fix is a locked surface escalates to the **operator** instead of churning at a layer that can't reach the lever. The owner is structural (record type) except for a `RuntimeFailure`, whose `owner` field decides L1-vs-OPERATOR.

**Healing is gradual.** A nurse firing once produces *one* nudge, not a guaranteed fix. The producer's distribution shifts; whether the next proposal lands depends on how clear the evidence was and how strongly the nurse encoded it. Hard one-shot briefs ("do NOT propose X") aren't required — softer pointers toward the right region are enough, because the nurse is built to retry. If the wound recurs:

- Wounds 1 and 4 retrigger same/next round with the new evidence.
- Wound 2's failure trail accumulates across rounds; L2 sees NEW vs ACCUMULATED and must change angle if the latter survives.
- Wound 3 fires only on stall, but each L3 plan shapes both subsequent L1 and L2.

**User-visible surface:** per-sample `⚠ … ↳` annotations on round reports. Audit trail, not alerts.

**Round-over-round feedback (separate).** `l1_critique → l1_generate` fires every round, regardless of failure. Performance-driven feedback, not failure-driven healing.

## The wounds, mapped to the two axes

Four historical "wounds" — but read them as (detection point × nurse owner), not as four
pipelines. Storage stays four typed lists (+ `l3_note`); **rendering collapses to two
owner-grouped signals** (`l1_wounds` = validation + runtime; `guard_breaches` = L2 + L3 post-parse).

|  | Wound 1 | Wound 2 | Wound 3 | Wound 4 |
|---|---|---|---|---|
| **Producer → Nurse** (owner-keyed, not producer-keyed) | L1 → **L1** | L1 → **L1 / OPERATOR** | L2 → L3 | L2 → L3 |
| **Owner source** | structural (L1's own output) | `RuntimeFailure.owner`: `L1` (rate) · `OPERATOR` (fatal) | (patience event) | structural (guard stream → L3) |
| **Detector** | `L1_SCHEMA_COMPLIANCE` validator | `DegradationCheck` (mid-eval) | `escalate_l2` patience | `validate_l1_layout` (post-parse) |
| **Failure record class** | `ValidationFailure` | `RuntimeFailure` | (patience event, no record) | `ValidatorOutcome` |
| **OSP storage** | `validation_failures` | `runtime_failures` | `escalation.l2.stall_count` | `l2_guard_breaches` |
| **Outer-memory mirror** | none (L2 reads `candidate_scores`) | cumulative on `cycle.opt_sp.wounds.runtime_failures` | none | per-round on the OSP itself |
| **Nurse prompt slot** | `{{l1_wounds}}` | `{{l1_wounds}}` | (whole `l3_plan` template) | `{{guard_breaches}}` |
| **Renderer** | `_r_l1_wounds` | `_r_l1_wounds` | `_r_l1_wounds` | `_r_guard_breaches` |
| **Nurse's writeback** | L1 re-proposes a valid override | L1 retunes the node config · or operator trims schema/model | `cycle.opt_sp.plan` | `cycle.opt_sp.plan` |
| **Score effect** | synthetic 0 (Path 1 in `score_population`) | real score, candidate eliminated mid-eval | none | none — fires after L2 ran |

## The prompt-budget unit (a separate mechanism)

The wounds heal **candidate** failures. The prompt-budget unit is a
**different mechanism** — not a tier or escape-hatch of the wound model —
that guards one unrelated concern: the size of a composed optimizer
optimizer prompt. It earns its own section because it isn't a wound, not
because the taxonomy needed an exception. It has exactly two healing modes:

1. **Truncate** — per-injection `char_cap`; an over-cap block is
   section-aware truncated in the hub (`facade.py`), with an
   `injection_budget_overrun` warning naming the overrun + dropped
   sections.
2. **Halt** — `RENDER_ERROR`: an injection renderer *raised* (usually
   code drift); operator-recoverable stop.

Why it is not a wound: it has no producer→nurse pair — mode 1 is
mechanical (no LLM), mode 2 escalates to a human. It obeys the
no-sidecar rule: both modes ride the `INJECTIONS` registry,
`DispatchHub`, and the existing `StopLoop` / round-loop teardown.

## Wound 1 — L1 tends its own `ValidationFailure`

`L1_SCHEMA_COMPLIANCE` (`application/optimization/validators/l1_strict.py`) wraps `validate_overrides()` and runs at L1 parse time in `parse_population()`. When L1's `pipeline_params_override` proposes a value outside `PipelineSchema.available_models`, outside a node's `param_allowed_values`, mismatched against the declared `param_types`, or touches an operator-locked axis (`PARAM_FORBIDDEN_KEYS = {model, provider}`, always locked), the validator emits a `ValidatorOutcome` whose `evidence["failures"]` is `list[ValidationFailure(axis, value, allowed, reason)]`. `reason` is one of `not_in_available_models`, `not_in_param_allowed_values`, `type_mismatch`, `forbidden_axis`, or `hallucinated_node` (the override named a node absent from the active schema — the node-name twin of `validate_l1_layout`'s unknown-placeholder wound; `build_l1_response_schema`'s node-name enum is advisory under `strict=False`).

Failures land on `OptSearchPoint.wounds.validation_failures` — outer-layer optimizer state, not target-layer. Effect chain: `score_population()` shortcuts to a synthetic-0 report (Path 1) → inline winner-selection deprioritises the candidate → round checkpoint persists the failure. **Exception — `hallucinated_node` is non-fatal:** the phantom edit is stripped from the wire (`merge_pipeline_params` drops nodes outside `active_steps`), so the candidate's real edits still score; the reason-aware Path-1 gate skips synthetic-0 and the wound rides along only as routed signal (`l1_wounds` self-correction + the `validation_failure_rate` evaluator, i.e. hallucination-rate as an L4-visible quality axis).

L1's own layout (`l1_layout`) renders the validation block inside the `{{l1_wounds}}` signal via `_r_l1_wounds()` (validation + runtime in one owner-grouped block) — L1 reads its own wounds and re-proposes toward the allowed region (the L2-briefs-L1 hop is gone). Healing is gradual — if L1 still proposes invalid values next round, the validator fires again and L1 gets fresh evidence.

## Wound 2 — L1 (or the operator) tends the `RuntimeFailure`

`DegradationCheck` (`application/optimization/pobb/checks.py`) fires mid-evaluation. Two paths:

1. **Fatal-code fast path.** `classify_result()` derives a fatal code from raw response shape (`finish_reason=length` + `reasoning_tokens > 0` → `reasoning_budget_exhausted`). One sighting ends the candidate; bypasses `min_samples`/`threshold`.
2. **Rate-based.** After `min_samples=3`, if `degraded_rate >= 0.4`, eliminate.

Both produce `EscalationSignal(target=ELIMINATE_CANDIDATE)`. `score_population` synthesises `RuntimeFailure(source, dominant_warning, warning_types, degraded_rate, …)` from the check + observed pipeline_params, attaches to `cp.osp.wounds.runtime_failures`, and continues with the next candidate — round winner unaffected.

End-of-round, `execute_round` mirrors new `RuntimeFailure`s onto `cycle.opt_sp.wounds.runtime_failures`, deduplicated by `(source, dominant_warning, observed_config)`. Never cleared — represents discovered runtime constraints.

The runtime block inside `_r_l1_wounds()` partitions into NEW (this round) vs ACCUMULATED (`first_seen_round != current_round`) and tags each entry `[owner=l1|operator]` from `RuntimeFailure.owner`. ACCUMULATED is the real signal — surviving items mean L2's prior angle didn't take. L2 updates its outputs; if ACCUMULATED keeps growing, Wound 3 takes over.

## Wound 3 — L3 replans on L2 stall

`escalate_l2` (`application/optimization/escalation/firing.py`) checks `esc.l2.stall_count >= opt.l2_patience`. When stalled, L3 fires (subject to its own `l3_patience`).

L3's prompt (`promptpotter/assets/optimizer/pipeline.yaml::resolved_prompts['l3_plan/1']`) reads:

- `{{l1_wounds}}` — accumulated `RuntimeFailure` trail (+ validation block) rendered by `_r_l1_wounds()`.
- `{{guard_breaches}}` — Wound 4 evidence (L2 + L3 post-parse breaches) rendered by `_r_guard_breaches()`.
- `{{plan}}`, `{{task_context}}`, `{{diagnostics}}`, `{{critique}}`.

L3 writes a new `plan` (and optionally `pipeline_params`). Lands on `OptSearchPoint.plan`; feeds both L1's `{{plan}}` slot and the next L2 invocation. Only wound with cross-layer authority — L3 changes pipeline composition or strategy framing.

## Wound 4 — L3 tends L2's parsed-output failure

`cycle.opt_sp.wounds.l2_guard_breaches` holds HARD `validate_l1_layout` failures — mandatory placeholder missing, unknown name, duplicate within a slot — written by `apply_side_effects` off `TransitionResult.l2_guard_breaches`. **Any** breach after L2 runs makes `escalate_l2` invoke `L3ModifyPlan` *immediately*, bypassing `l2_patience` and `l3_patience`: broken L2 output is not "wait and see". The trigger is deterministic from L2's output (already on the round file), so resume reproduces it without a separate decision record.

**Every breach is hard** — owned by [`l2-internals.md`](l2-internals.md) § Wound 4. This layer must route every breach straight to L3; it has no soft-reject tier to fall back to.

## Validators are Evaluator-shaped

```python
@dataclass(frozen=True)
class ValidatorOutcome:
    validator_id: str
    evidence: dict[str, Any]


@dataclass(frozen=True)
class LLMOutputValidator:
    id: str
    check: Callable[..., ValidatorOutcome | None]
```

An outcome only exists for a failure — so no `passed` flag — and the self-healers count
**events**, not graded scores, so there is no `score` field (the old "score flows into
`campaign.yaml::scoring`" symmetry was aspirational and never wired; it's gone). No owner field
either: a guard-breach outcome always routes to L3 structurally (see above), so there is no
per-outcome choice to store.

## Optimizer-memory state

Fields on `OptSearchPoint.memory` (the `L2L3Memory` sub-model in `domain/opt_search_point.py`) travel with each candidate cross-round:

| Field | Lifecycle | Wound |
|---|---|---|
| `task_context` | persistent, accumulative; merged on each L2 fire; inherits through `mutate()` | Wound 1, Wound 2 — L2 writeback |
| `wounds.validation_failures` | per-candidate (set at L1 parse) | Wound 1 — L1 reads (via `l1_wounds`) |
| `wounds.runtime_failures` | per-candidate + cumulative outer-memory mirror | Wound 2 + 3 |
| `wounds.l2_guard_breaches` | per-round, set by L2 post-parse | Wound 4 — L3 reads |
| `wounds.l3_guard_breaches` | per-round, set by L3 post-parse | L3 self-heal |
| `wounds.l3_note` | sticky free-text; set by L3, survives every incumbent swap (L1 win + L2/L3 transition) via the `Cycle.adopt` seam's `copy_memory_to` | L3→L2 steer (not a failure record) |

The L1 critique itself lives on `RoundResult.critique` (a dict, not on `L2L3Memory`); the dispatch hub's `critique` injection reads it from `cycle.latest_round.critique`. Per-round trajectory lives on `Cycle` (`Cycle.rounds`), not OSP.

## Mid-eval termination — what is and isn't healing

| Check | Fires | Self-healing? |
|---|---|---|
| **Validation** (`L1_SCHEMA_COMPLIANCE`) | L1 parse, before evaluation | Wound 1 |
| **`PoBBCheck`** | Mid-eval, after `n_min` queries (Bayesian Posterior-of-Being-Best) | No — pure statistical termination, no failure record |
| **`DegradationCheck`** | Mid-eval, after 3 queries (or first fatal) | Wound 2 |

`PoBBCheck` stops scoring this candidate (posterior probability of being round's best fell below ε); no failure record, no LLM informed. See [`../methods/candidate-elimination.md`](../methods/candidate-elimination.md).

## `classify_result()` — fatal classification

`classify_result()` (`domain/rendering.py`) derives **fatal** and **infra** codes from the backend's neutral advisories (`llm_only:content_empty`, `*:content_filtered`, …) and raw response shape (`pipeline_data.step_tokens.{node}`: normalised `finish_reason`, `reasoning` token count). Backend = facts, optimizer = policy.

Rule table. Every `content_empty` row is gated on **the result not having answered** — the advisory describes one ATTEMPT, the backend retries beside it, and that retry can succeed; a row carrying a real `predicted` is not an empty response whatever the advisory says. Among the unanswered, `reasoning_tokens > 0` is proof the model **worked** (a refusal carries content, or `content_filter`), so emitting nothing after thinking is route shape whatever ended the call — `stop` and `length` are one fault at two budgets.

- `content_empty`, unanswered, `reasoning_tokens > 0`, `finish_reason=length` → `reasoning_budget_exhausted` *(infra)*
- `content_empty`, unanswered, `reasoning_tokens > 0`, any other `finish_reason` → `reasoning_only_response` *(infra)*
- `content_empty`, unanswered, `reasoning_tokens = 0`, `finish_reason=length` → `output_truncated` *(infra)*
- `content_empty`, unanswered, `reasoning_tokens = 0`, any other `finish_reason` → `empty_response` *(fatal)*
- `*:content_filtered` → passthrough as fatal

Fatal codes are deterministic for the whole config — one sighting proves the candidate is broken for every remaining query, which is exactly why a rule allowed to fire on a row that answered *correctly* eliminates a good candidate rather than reading it strictly. Grow the rule table (don't expose it as a tunable) when a new pattern proves equally conclusive.

Three load-boundary effects (consumed via `is_deprecated()`):

1. **Candidate elimination** — `DegradationCheck` fast-path returns `EscalationSignal(target=ELIMINATE_CANDIDATE)` on first sighting; bypasses `min_samples` and `threshold`.
2. **Cache eviction** — `score_search_point` runs `_filter_deprecated_priors` on `archive.load_reusable_results` and drops every entry the classifier marks fatal. Fresh re-measurements receive `retry_of_deprecated_cache=True`.
3. **Stats exclusion** — `_compute_accuracy` partitions deprecated rows into a separate `deprecated` count and excludes them from `hits`, `total`, `errors`, accuracy denominator.

This is a load-boundary filter, not a score-time fallback. Trace records continue to be archived (forensic value); only cache reuse and primary-stat aggregation are blocked. Sanctioned alongside the `score_population()` validation-failure synthetic-0 — see [`../concepts/scoring-and-memory.md`](../concepts/scoring-and-memory.md#deprecated-samples).

## Adding a new mechanism

Pick the storage stream by detector + score-effect (the four wounds); the owner falls out of the
record type — you never wire a nurse by hand:

- New gen-time check on L1's output → **Wound 1**. Add a validator next to `L1_SCHEMA_COMPLIANCE`; owner is L1 structurally (its own malformed output).
- New runtime measurement pointing at a candidate config region → **Wound 2**. Add a check that emits `RuntimeFailure` from `score_population`; stamp `owner=NurseOwner.L1` when L1 can retune it, `owner=NurseOwner.OPERATOR` when only the operator can (a locked schema/model the in-loop layer can't reach).
- New strategic-stall trigger → **Wound 3** isn't a registry; it's the patience timer.
- New post-parse check on L2/L3's output → **Wound 4**. L3's side has a registry (`L3_OUTPUT_VALIDATORS`, `validators/l3_output.py`); L2's is the layout check itself (`domain/l1_layout.py::validate_l1_layout`) — there is no `L2_OUTPUT_VALIDATORS` to append to, and a new L2 check means either extending that validator or standing a registry up. Owner is L3 structurally either way (guard-breach stream → `escalate_l2`).

For each: declare `LLMOutputValidator` with a stable id, write the `check` callable, append to the appropriate registry. Prompt-section render and persistence path are already wired — they iterate the registry, not a hard-coded list. Only add a `NurseOwner` member when a producer actually stamps it (today only `RuntimeFailure` does, with `L1`/`OPERATOR`).
