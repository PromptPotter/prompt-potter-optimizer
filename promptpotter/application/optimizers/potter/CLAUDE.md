# application/optimizers/potter/ — potter's L1 / L2 / L3 agent contracts

<dispatch-first>
**Fix a bad prompt in the dispatch hub** (`dispatch/`, `DispatchHub.fill`, routed per [`dispatch-hub.md`](../../../../docs/concepts/dispatch-hub.md)), the one place shaping may be creative; a bad block is reshaped at source, never patched downstream.
</dispatch-first>

## Origin = conservative floor

**Every tunable in `dataset.yaml` `pipeline.nodes.{name}.config` starts at its FLOOR**; L1 expands upward on evidence. `reasoning_effort`'s floor is the model's, so the overlay never spells one.

## Stops and the ladder

- **Boundary stops are the bench's** (`runner/termination.py::standing_tripped`); potter's `escalation` node stops on its own exhaustion, a layer through `terminate_proposal`. No round-number threshold inside the loops.
- **`escalation_ladder` (`full` / `l1_l2` / `l1`) is the ablation arm**: a patience paces the ladder, never shortens it, and L1's prompt is identical across arms.

## L1 — what `l1_generate` may propose

- **Mutate only with cause** (stall, critique / axis-memory evidence, the frozen `task_context`); a panel speaking against a mutation vetoes it.
- **L1 never writes `answer_format`**: every node reads the target through `dispatch/bundle.py::l1_target_schema`, rejected at parse (`forbidden_axis`). <!-- probe: git grep -cE "[^(]target\.schema|Target\)\.schema" -- promptpotter/application/optimizers/potter/*.py == 0 -->
- **Never reorder `L1Variant` alone**: `evidence_grounding` generates before `changes_description` and the overrides.
- **A cluster spans the detectable move, two rows at least**; a variant's edit holds for every row it `targets_rows`, no per-row patch.
- **`repeat_variant` rejects an idea this cycle measured and lost** inside `validators.py::detect_invariants`, never at parse; a round is never emptied.

## L2 — what `l2_context` (and L3's `l3_plan`) may write

**L2 fires on L1 stall, an unusable L1 reply or evidence starvation; `escalation.py` routes, never diagnoses; its one stop is `NextAction.STOP_L3_PATIENCE` → `OPTIMIZER_EXHAUSTED`.** L2 judges recoverability; `terminate_proposal` is its exit for a backend fault.

- **L2 writes exactly `l1_layout` and `l1_overrides`, one node overlay on `l1_generate`** (`L2Memory.steer`), never `pipeline_params`; as recombination host, `recombinations` (ids and loci only); plus the layer-control channel. Each steer cites an axis, sample or yield number.
- **No layer writes `task_context`**: no wire schema has its field; target text is L1's alone, through `prompt_fields_updates`. <!-- probe: git grep -cE "task_context:" -- promptpotter/application/optimizers/potter/dispatch/schemas.py == 0 -->
- **L3 fires on L2 stall, or on `l1_layout_refused` (`escalation.py::decide_heal`)**, and writes a strategic replan (`L3Memory.plan`) every prompt reads.

## The layer-control channel — `fork_proposal` + `terminate_proposal`

**Each sits behind its capability bit; terminate outranks fork.** A blank terminate reason is ignored; an honored one stops the cycle `optimizer_abort`. `fork_proposal` names no round (UCB picks the node, `mask/backprop.py::select_branch_round`), but behind `reelect_capability` it may name `stand_on`, an arm label from the ELECTIONS table. A layer never moves its own spend ceiling.

## The optimizer never searches the GATEWAY or the ROUTE

**`PARAM_FORBIDDEN_KEYS` (`domain/pipeline_params.py`) is an invariant**: the operator sets a node's route in the dataset overlay ([`datasets/CLAUDE.md`](../../../assets/datasets/CLAUDE.md) § Sole route), the optimizer's in `OptimizationConfig.determinism`, applied last. `model` is an axis opened per node by `optimizer.param_keys` within `param_allowed_values["model"]` ([decisions](../../../../docs/decisions.md) § 0005).

## Editing a renderer's PROSE is a measurement change

`nodes.py::source_digest` hashes the prompt-shaping set, so rewording a directive voids banked origins. A prompt-shaping constant lives in `bundle.py`; a unit reached only through an instance or registry carries `shared/hashing.py::shapes_optimizer_prompt`. A validator either REJECTS (`validators.py`: a `ValidationFailure` the layer heals) or SCORES (`behavior_checks.py`, into `review.md`), never both.

## Add no 4th layer — L4 is a recursion

**There is no `l4_*.py` in potter**: L4 is PromptPotter applied to itself through the `promptpotter` connector ([`application/CLAUDE.md`](../../CLAUDE.md) § L4's machinery; [`l4.md`](../../../../docs/concepts/l4.md)).
