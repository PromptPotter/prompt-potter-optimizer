# application/optimization/ — L1 / L2 / L3 agent contracts

The optimizer is three nested generation loops, and each layer mutates with cause, never at random. This file is the **agent contract**: what each layer may do, and what it may never do.

Mechanism lives at its definition site, not here — `l1/generate.py` and `escalation/firing.py` carry the composition and transition detail in their module docstrings, and each `_r_*` docstring in `dispatch/injections/panels.py` is the definition site for what its panel is and why it exists. The orchestration shape (Cycle, dispatch, escalation rules) is [`../CLAUDE.md`](../CLAUDE.md); channels and signal routing are [`../../../docs/developer/dispatch-hub.md`](../../../docs/developer/dispatch-hub.md).

<dispatch-first>
**★ FIRST PRINCIPLE, THIS PHASE — fix it in the dispatch.** A prompt is one *information package* for a small model: short, every line unique, high-value, in the right slot. The **dispatch hub** (`application/optimization/dispatch/`) is where raw measurement is recomposed into those packages and wired in through **deterministic wires** (`DispatchHub.fill`) — so it is the FIRST place to fix a bloated / low-value / misplaced prompt, and the one place to be **creative**: new functions, processing, recomposition, panel logic, whatever forms the information *perfectly for its slot*. The wires stay deterministic; the intelligence lives in the shaping. Corollaries: a panel renders only what adds signal **for this task/state** and is otherwise silent (the `answer_distribution` / suppressed-RANK rule); duplication, paraphrase, filler and task-mismatched blocks are reshaped at source — never patched downstream (a render-time dedup, a louder optimizer prompt clause).
</dispatch-first>

## Load-bearing

- Fix a bad prompt in the dispatch, not downstream → `<dispatch-first>` above
- Every tunable starts at its FLOOR → § Origin = conservative floor
- Editing renderer prose is a measurement change → § Editing a renderer's PROSE
- A validator rejects or scores, never both → § A validator either REJECTS or SCORES
- No round-number thresholds inside the loops → § Signals come from measurement
- No `l4_*.py`, ever → § Add no 4th LayerStrategy
- Don't add a second decomposition node → § checkin

## Origin = conservative floor

**Start every tunable in the dataset's per-node overlay (`datasets/{name}/pipeline.yaml::nodes.{name}.config`) at its FLOOR, not its centre** — `reasoning_effort: "low"`, low `temperature`, minimal `thinking_budget`, no expensive system-prompt scaffolding. L1 expands upward when sibling-yield or stall evidence supports it; starting from expanded-thinking defaults burns budget on round 0 and steals the headroom L1 exists to discover. Per-dataset starting points: [`../../../docs/operations/dataset-reasoning-matrix.md`](../../../docs/operations/dataset-reasoning-matrix.md).

## Cycle stop conditions

Boundary stops are `max_rounds` and its opt-in measurement-driven twin `OptimizationConfig.lives` — "hearts", +1 per improving round, −1 per stall, banked, stop at 0 → `LIVES_EXHAUSTED`. It banks `improved` alone, where the stall counter beside it also requires `RoundResult.separable`, so a round crowning a winner no arm's interval cleared 0 spends patience but not a life. On top of those, **L2 and L3 may end the cycle themselves** — through the escalation rules (goal reached / infinite stall) or by emitting `terminate_proposal`.

## L1 — what `l1_generate` may propose

- **Mutate only with cause**: stall-driven escalation, critique / axis-memory evidence on the chosen axis, or the operator's frozen `task_context`. **No data justifying a choice ⇒ do not gamble.** Random exploration is reserved for explicit stall, and if a panel field speaks against a mutation, `l1_generate` does not propose it.
- **Every citable panel is a same-named injection.** A citable name that renders nothing invites a fabricated citation, so the citable set is **derived, never declared** — `citable_fields(layout, exploration_budget)` intersects `@signal(citable=True)` with the node's live layout, and one derivation feeds the prompt's menu, the wire schema's enum and the behavior check. L1 cannot cite a panel it was never shown.
- **The `*_override` slots derive the same way**: a slot whose panel produced nothing this call is withdrawn. L1 may not EDIT an axis it was never shown any more than it may cite one.
- **`evidence_grounding` is required on the wire** even though it is optional at the parse boundary — tolerating an omission is not the same as offering one. Variants without a real citation fail `evidence_grounding_present` (`validators/l1_behavior.py`).
- **Field order is load-bearing — never reorder `L1Variant` alone.** `evidence_grounding` generates above `changes_description` *and* the `*_override` slots, because fields generate in schema order and a citation emitted after the mutation can only rationalize it. Three surfaces state that order and move together: the Pydantic model (the SoT), `l1_generate`'s `answer_format` prose, and the regenerated `resolved_schemas`.
- **The decision frame comes first**, answering what the evidence panels cannot — whether the number under them can be trusted: `measurand` · `precision` · `detectable_move` · `sample_provenance` · `confounds` · `budget_state`. `budget_state` is deliberately **not citable**: budget says how boldly to spend a round, never that a mutation is right.
- **Four panel rules the renderers do not state.** `answer_distribution` is `L1_MANDATORY`, because without it a generator rewrites the instruction it is already being denied, louder, every round. `failing_samples` orders on `Cycle.ruler`, **never** `hard_samples.json`'s re-fitted δ — that same ruler binds sample SELECTION, so no round chooses cells on a different δ than it scores them on. `mutation_memory` derives from the payload, **never** from `changes_description`, which two candidates can share. `axis_memory` and the raw cross-run panels stay OFF the floor; L2 adds them on stall.
- **The block library is the only channel handing L1 reusable prompt MATERIAL** — every cross-run panel beside it carries statistics *about* material. Reusing a block does not excuse `evidence_grounding`; the citation still names a panel.
- **An idea this cycle already measured and lost is rejected, not re-scored** (`repeat_variant`) — the only cross-round invariant, beside round-local `no_op_variant` and `duplicate_variant`. The match is lexical over the mutated VALUES (`domain/candidate_diff.py::idea_fingerprint`), never field names, because a re-proposal arrives as the same idea rewritten into a different FIELD. Four bounds, because a wrong rejection is destructive and leaves no trace: only **measured losses** count, the reject threshold is stricter than the marking one, a repeat may cost a candidate but **never empties a round**, and every rejection names the round it repeats on the wound channel.

## L2 — what `l2_context` may write

Fires on L1 stall (default), yield drought (`l2_axis_yield_drought`), or evidence-starvation (`l1_evidence_starved`). `decide_escalation` over `DEFAULT_ESCALATION_RULES` decides transitions — **the rule set is the policy**; `EscalationFSM` holds the counters those rules read but is no longer the decider.

- **Deterministic rules route; they never diagnose or stop.** A systemic fault brings L2 in as a *weak preemptor*, bypassing `l1_patience`, and L2 judges recoverability — fixable by steering L1's attention, or unfixable by any prompt move, in which case `terminate_proposal` is the HITL exit. The diagnose-and-stop authority lives in the LLM tier.
- **L2 writes exactly two surfaces**: `l1_layout` (which panels L1 sees) and `l1_overrides` (how hard it explores), plus optional optimizer-param tweaks — **never pipeline_params**, which belong to `l1_generate`.
- **The steer is evidence-anchored**: it cites a specific axis, sample or yield number. Speculative moves ("maybe try X") are out of contract, and a fire touching no L1 surface is a wasted escalation scored as one (`l2_targets_l1_surface`).
- **The framing is frozen — L2 does not write `task_context`.** The five framing fields are operator-authored evidence, and the lock is structural rather than conventional: `TaskDecomposition.merge` refuses them and the L2 wire schema has no field for them. A round's findings reach L1 through `critique`, `axis_memory` and `mutation_memory` instead — derived from measurement rather than paraphrased from the previous prompt. Only `upstream_context` / `downstream_context` stay mutable, because those splice into the TARGET prompt and a candidate carrying them is scored.
- **Escalating to L3 is rare** — only when the failure mode is outside the framing surface. Default: keep refining the framing for L1.

## L3 — what `l3_plan` may write

Fires only on L2 stall. Produces a **strategic replan** — the framing surface, escalation policy, or which axes are in scope — written to `OptSearchPoint.plan` and read by **every** prompt, so it is the frame inside which both L2 and L1 operate. It heals L2 on layout HARD-validator failures or repeated cross-field issues, which are signs L2 is thrashing within the plan rather than refining across the plan-space.

**Firing is rarer still than L2**: a fire signals the cycle's plan was wrong, not that one variant missed. If L3 fires repeatedly inside one cycle the plan-space itself is exhausted, and it should terminate rather than replan again.

## The layer-control channel — `fork_proposal` + `terminate_proposal`

**These two are the COMPLETE layer-control vocabulary; nothing else back-doors a cycle exit.** Both ride the `_run_transition` post-apply seam and each is gated by an `OptimizationConfig` capability bit whose injection renders empty when off, so an ablation run is bit-for-bit identical on prompt text. Terminate outranks fork when both are set.

- **`fork_proposal` carries no round offset, because the layer decides WHETHER to rewind and UCB decides WHERE** (`application/mask/backprop.py::select_rewind_round`, UCB1 over the lineage tree). A layer never had the evidence to name a round — no panel enumerates the ancestors and their fitness — so asking it to was a phantom citation on the loop's most expensive decision. `resume --rewind N` is the operator's equivalent gesture.
- **`unlock_schema_field_rename` is a bool, never the `ConfigOverrides` object.** Handed the whole delta, a layer could move its own spend ceiling. It is the layer's only search-policy request and can ride nothing but this rewind, because `schema_field_rename` invalidates comparability and must mint a sibling.
- **A REASON is what makes `terminate_proposal` a decision.** The field is optional, so a blank one is a volunteered field — ignored, exactly as one arriving with the capability off is — and both the honored stop and the ignored blank land on the operator's warnings channel, because a halt whose reason lives only in a log line is a halt nobody can act on.

## The optimizer never searches the GATEWAY or the ROUTE

`PARAM_FORBIDDEN_KEYS` (`domain/search_point.py` — read the set there) holds `provider` and `route_order`, and is an INVARIANT rather than a toggle: neither is emitted for the LLM to set, so the lock is structural and not policed per round. Both are cost levers the operator sets against a measured capture, and hosts of one model disagree systematically.

**`model` is NOT in that set and is a legitimate axis.** Whether it is open is the answer of a node's own `optimizer.param_keys`, per dataset — so an instrument that must not move under the arms it measures closes it there (`justlogic-d234`, `bbeh`), and a campaign free to search it leaves it open. Its permitted values are `param_allowed_values["model"]`, the ONE set: what the optimizer may pick, and what a human may steer a fork to un-tainted. Steering outside it is cap-gated and taints the branch babysat ([ADR-0005](../../../docs/adr/0005-delegated-principals-and-capability-scoping.md) §4). Either way the done C0 is inherited — `WHO_ANSWERS_KEYS` (model + the two cost levers) is what that inherit reads, and it is deliberately the wider set.

## Editing a renderer's PROSE is a measurement change

`dispatch/injections/` composes most of every optimizer prompt, so rewording a directive changes what every inner cycle is handed — and on L4 that is measurement identity. `injection_source_digest` hashes it AST-normalized: a comment, docstring or reflow costs nothing; a panel's prose, its `char_cap`, a node's discretionary allowance or a selection rule voids the banked origins. **Expect the re-measure; the bug is the reverse.**

The hashed set is `bundle` + `compose` + `facade` + `domain/ruler.py` + the renderers. **A prompt-shaping constant lives in `bundle.py` unless it is a fact about something else the prompt reports** — `OPTIMIZER_DISCRETIONARY_CHARS` and the wire schema's `maxLength`/descriptions belong there; `BAND_COLLAPSE_LOGITS`/`_RATIO` do not, describing the δ scale and served on `AbilityReading` as well as rendered. Either way **the module holding it MUST be in the hashed set** — one parked outside shapes every prompt for free.

## A validator either REJECTS or SCORES — never both

`*_strict` / `*_output` / `l1_invariants` **reject**, so a failure routes back up as a `ValidationFailure` and the layer heals. `*_behavior` only **scores** conformance into `review.md` and the round file, and never blocks a candidate.

Within the reject posture, `l1_strict.py` judges ONE proposal against a declared rule; `l1_invariants.py` compares proposals against each other and against history. **Emitting the wire schema is neither posture** — that is `dispatch/l1_wire_schema.py`, which composes a prompt surface. The SCORING vocabulary (`CheckResult`, `ValidatorContext`, `CheckFn`) is owned by `validators/behavior_base.py` and by neither layer that speaks it.

**A rejection the REJECT posture already makes deterministically must not also be taught in prompt text.** The model earns nothing by obeying it — the candidate dies before the backend call either way — so the words buy no behaviour and are charged twice, once as tokens and once as the quality tax every model pays on a longer input (`<simplify-the-problem>`). State that they ARE mechanical in one clause, and spend the prompt on the traps nothing polices.

## Signals come from measurement, not from the calendar

Avoid hardcoded round thresholds inside the loops. `params_unlocked` derives from stall depth + mutation history, not `round ≥ 3`; `exploration_budget` widens with `stall_rounds`, not on a fixed schedule. Hardcoded stop conditions sit at the cycle boundary; everything inside the loops reasons from measurement.

## Add no 4th LayerStrategy — L4 is a recursion

**There is no `l4_*.py` in this package and there will not be one.** The ban is derived rather than arbitrary, which is why naming a file `l4_recursion.py` breaks it just as surely: L4 is the same PromptPotter applied to itself via the `promptpotter` connector — an outer cycle whose *backend* is an inner cycle, mutating the inner's optimizer prompt template fields as `pipeline_params`. The ladder is closed at L1 / L2 / L3.

Conceptually L2 / L3 / L4 are one family, each mutating a slower-changing surface of the level below. Structurally L2 and L3 live here as escalation strategies while L4 lives at the connector seam and the dataset; which package holds which half is [`../../CLAUDE.md`](../../CLAUDE.md) § Where L4 lives. Spec: [`../../../docs/specs/l4-outer-loop.md`](../../../docs/specs/l4-outer-loop.md).

## checkin — the fifth optimizer node

`checkin` is a registered optimizer node (`OPTIMIZER_RESPONSE_MODELS`) but **not a loop layer**: it runs *around* the loop and skips the injection path. It is **not** thereby a "non-ledger" call — both modes bind the seeded campaign's cycle ledger via `task_context.py::checkin_call_context` and wrap in `observed_node`, so tokens, cost and audit record land like any other. They did not, once.

**One node, two modes, one output schema (`CheckinOutput`) — don't add a second decomposition/resolution node.** Task decomposition (CLI `new`) turns a raw `task_description` into the six Layer-1 prompt strings plus `task_context`; origin resolution (web ingest) turns a draft origin into `assessment` + `findings` + `next_action` + `recap`. Both produce the six decomposition fields and both drivers capture them, so an origin turn returns the resolved origin *and* a seeded starting prompt the operator edits before mint.

## Reviewing an L1 round trace

Walk the checklist in the `potter-self` skill (§ The round-trace checklist) before reporting findings on any operator-pasted round dump — it enumerates the checks that historically slipped past, and which are validator-enforced versus pure analysis responsibility.
