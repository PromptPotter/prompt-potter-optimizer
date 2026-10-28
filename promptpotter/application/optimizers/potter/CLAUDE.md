# application/optimizers/potter/ — potter's L1 / L2 / L3 agent contracts

Potter is three nested generation loops; each layer mutates with cause, never at random. This file says what each layer may and may never do. Channels and signal routing: [`../../../../docs/developer/dispatch-hub.md`](../../../../docs/developer/dispatch-hub.md).

<dispatch-first>
**Fix a bad prompt in the dispatch hub** (`application/optimizers/potter/dispatch/`): it recomposes raw measurement into one short, high-value package per slot through deterministic wires (`DispatchHub.fill`), and is where shaping may be creative.
A panel renders only what adds signal for this task and state; duplication, paraphrase and mismatched blocks are reshaped at source, never patched downstream.
</dispatch-first>

## Origin = conservative floor

**Every tunable in `dataset.yaml` `pipeline.nodes.{name}.config` starts at its FLOOR** — low `temperature`, minimal `thinking_budget`, no costly scaffolding; L1 expands upward on evidence. **`reasoning_effort`'s floor is the model's, so the overlay never spells one** (`pipeline_resolve.py::_apply_model_floors`). Starting points: [`../../../../docs/operations/datasets.md`](../../../../docs/operations/datasets.md) § The model a dataset runs.

## Cycle stop conditions

**Boundary stops are the bench's** (`runner/termination.py::standing_tripped`); potter's `escalation` node stops on its own exhaustion (`STOP_L3_PATIENCE`), and a layer stops through `terminate_proposal`. **Signals come from measurement, never the calendar**: no round-number threshold inside the loops.

## The ladder's DEPTH is a knob, and it is a real suppression

**`escalation_ladder` is `full` / `l1_l2` / `l1`, the ablation arms**: `l1` composes no L2/L3 prompt, `l1_l2` stands the L3 gate down. **A patience paces the ladder, never shortens it**; L1's prompt is identical across arms.

## L1 — what `l1_generate` may propose

- **Mutate only with cause** — stall, critique / axis-memory evidence, or the frozen `task_context`. No data for a choice ⇒ no gamble; a panel speaking against a mutation vetoes it.
- **The citable set is derived** (`citable_fields`), and `*_override` slots are withdrawn the same way when their panel rendered nothing.
- **Never reorder `L1Variant` alone**: `evidence_grounding` generates before `changes_description` and the overrides, and the model, `answer_format` prose and `resolved_schemas` move together.
- **Panel rules the renderers do not state**: `answer_distribution` is `L1_MANDATORY`; `failing_samples` orders on `Cycle.difficulty`, the one δ; `mutation_memory` derives from the payload, never `changes_description`; `axis_memory` and raw cross-run panels stay off the floor until L2 adds them.
- **A cluster spans two rows** (`l1/critique.py::spanning_clusters`); a variant targets `targets_rows`, and its edit holds for every row — no per-row patch.
- **Reusable prompt material reaches L1 through the block pool**; reusing a block still needs `evidence_grounding`.
- **`repeat_variant` rejects an idea this cycle measured and lost**, inside `l1_invariants`, never at parse: only measured losses count, the reject threshold is stricter than the marking one, a round is never emptied, and every rejection names its round on the wound channel.

## L2 — what `l2_context` may write

**L2 fires on L1 stall, yield drought or evidence starvation; the rule set in `escalation/rules.py` is the policy.** A fact about one node lives on its member (`optimizers/nodes.py::LlmNode`), never in a table keyed by node name.

- **Deterministic rules route; they never diagnose or stop.** L2 judges recoverability, and `terminate_proposal` is its exit for a backend fault.
- **L2 writes exactly `l1_layout` and `l1_overrides`, as one node overlay on `l1_generate`** (`L2Memory.steer`) — never `pipeline_params`. Each steer cites an axis, sample or yield number (`l2_targets_l1_surface`).
- **No layer writes `task_context`** — no wire schema has its field; target text is L1's alone, through `prompt_fields_updates`. <!-- probe: git grep -cE "task_context:" -- promptpotter/application/optimizers/potter/dispatch/schemas.py == 0 -->

## L3 — what `l3_plan` may write

**L3 fires only on L2 stall** and writes a strategic replan (`L3Memory.plan`) every prompt reads; it heals L2 thrashing within the plan. **A fire means the plan was wrong**, so it is rarer than L2.

## The layer-control channel — `fork_proposal` + `terminate_proposal`

**Each is gated by a capability bit whose injection renders empty when off; terminate outranks fork.**

- **`fork_proposal` carries no round offset**: the layer decides whether to rewind, UCB decides where (`application/mask/backprop.py::select_rewind_round`).
- **`unlock_schema_field_rename` is a bool, never `ConfigOverrides`** — a layer must not move its own spend ceiling.
- **A blank `terminate_proposal` reason is ignored**; honored and ignored both land on the operator's warnings channel.

## The optimizer never searches the GATEWAY or the ROUTE

**`PARAM_FORBIDDEN_KEYS` (`domain/search_point.py`) is an invariant**: the operator sets a backend node's route in the dataset overlay ([`../../../assets/datasets/CLAUDE.md`](../../../assets/datasets/CLAUDE.md) § Sole route), the optimizer's in `OptimizationConfig.determinism`, applied last.
**`model` is a legitimate axis**, opened per node by `optimizer.param_keys` within `param_allowed_values["model"]`; steering outside it is cap-gated and taints the branch ([decisions](../../../../docs/decisions.md) § 0005).

## Editing a renderer's PROSE is a measurement change

**`nodes.py::source_digest` hashes the prompt-shaping set AST-normalized, so rewording a directive voids banked origins — expect the re-measure.** A pool's starting content is hashed as data (`nodes.py::Initializer.starting_content`).
**A prompt-shaping constant lives in `bundle.py` unless it is a fact about something else**, and its module is in the hashed set or carries `shared/hashing.py::shapes_optimizer_prompt`.

## A validator either REJECTS or SCORES — never both

**`*_strict` / `*_output` / `l1_invariants` reject (a `ValidationFailure` the layer heals); `*_behavior` only scores into `review.md`.**
**Never teach in prompt text a rejection the reject posture already makes** — say in one clause that it is mechanical.

## Add no 4th layer — L4 is a recursion

**There is no `l4_*.py` in potter, under any name**: L4 is PromptPotter applied to itself through the `promptpotter` connector, and a peer is a sibling manifest. Which package holds L4's law and machinery: [`../../runner/inner/CLAUDE.md`](../../runner/inner/CLAUDE.md) § Where L4 lives. Spec: [`../../../../docs/specs/l4-outer-loop.md`](../../../../docs/specs/l4-outer-loop.md).
