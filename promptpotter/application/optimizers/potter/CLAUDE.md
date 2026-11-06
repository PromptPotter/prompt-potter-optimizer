# application/optimizers/potter/ — potter's L1 / L2 / L3 agent contracts

Routing: [`dispatch-hub.md`](../../../../docs/developer/dispatch-hub.md).

<dispatch-first>
**Fix a bad prompt in the dispatch hub** (`dispatch/`, `DispatchHub.fill`), the one place shaping may be creative; a bad block is reshaped at source, never patched downstream.
</dispatch-first>

## Origin = conservative floor

**Every tunable in `dataset.yaml` `pipeline.nodes.{name}.config` starts at its FLOOR**; L1 expands upward on evidence. `reasoning_effort`'s floor is the model's, so the overlay never spells one (`pipeline_resolve.py::ParamLayers`, its `model_floor` layer).

## Stops and the ladder

- **Boundary stops are the bench's** (`runner/termination.py::standing_tripped`); potter's `escalation` node stops on its own exhaustion (`STOP_L3_PATIENCE`), a layer through `terminate_proposal`. No round-number threshold inside the loops.
- **`escalation_ladder` (`full` / `l1_l2` / `l1`) is the ablation arm**: a patience paces the ladder, never shortens it, and L1's prompt is identical across arms.

## L1 — what `l1_generate` may propose

- **Mutate only with cause** — stall, critique / axis-memory evidence, or the frozen `task_context`; a panel speaking against a mutation vetoes it.
- **L1 never writes `answer_format`**: every potter node reads the target through `dispatch/bundle.py::l1_target_schema` — withdrawn from the wire, rejected at parse (`forbidden_axis`). <!-- probe: git grep -cE "[^(]target\.schema|Target\)\.schema" -- promptpotter/application/optimizers/potter/*.py == 0 -->
- **Never reorder `L1Variant` alone**: `evidence_grounding` generates before `changes_description` and the overrides; the model, `answer_format` prose and `resolved_schemas` move together.
- **A cluster spans the detectable move, two rows at least** (`dispatch/schemas.py::critique_response_model` over `panels.py::detectable_rows`; an unmarked id is struck and a cluster left narrower dropped at parse, before the cap, the reply's others kept); a variant's edit holds for every row it `targets_rows` — no per-row patch. A reused block still needs `evidence_grounding`.
- **`repeat_variant` rejects an idea this cycle measured and lost**, inside `l1_invariants`, never at parse; a round is never emptied, and every rejection names its round on the wound channel.

## L2 — what `l2_context` may write

**L2 fires on L1 stall, an unusable L1 reply or evidence starvation; `escalation/rules.py` is the policy** — rules route, they never diagnose or stop; L2 judges recoverability, and `terminate_proposal` is its exit for a backend fault.

- **L2 writes exactly `l1_layout` and `l1_overrides`, one node overlay on `l1_generate`** (`L2Memory.steer`) — never `pipeline_params` — and, as recombination host, `recombinations` (ids and loci only, added by the next round's `l1_generate`), plus the layer-control channel below. Each steer cites an axis, sample or yield number (`l2_targets_l1_surface`).
- **No layer writes `task_context`** — no wire schema has its field; target text is L1's alone, through `prompt_fields_updates`. <!-- probe: git grep -cE "task_context:" -- promptpotter/application/optimizers/potter/dispatch/schemas.py == 0 -->

## L3 — what `l3_plan` may write

**L3 fires only on L2 stall** and writes a strategic replan (`L3Memory.plan`) every prompt reads; it heals L2 thrashing within the plan.

## The layer-control channel — `fork_proposal` + `terminate_proposal`

**Each sits behind its capability bit; terminate outranks fork.** `terminate_proposal` is in the reply schema only where `InjectionBundle.terminate_offer` holds; a blank reason is ignored, an honored one stops the cycle `optimizer_abort`. `fork_proposal` names no round — UCB picks the node (`mask/backprop.py::select_branch_round`) — but behind `reelect_capability` it may name `stand_on`, an arm label from the ELECTIONS table (`potter/dispatch/bundle.py::InjectionBundle.reelect_offer`; any other label fails parse, `potter/dispatch/schemas.py::fork_proposal_model`): the fork stands on that arm and its round is restated electing it on the fork's ledger (`RebaseRequest.hangs_from`), the act carried as `results.py::Election.reelection`. `unlock_schema_field_rename` is a bool: a layer never moves its own spend ceiling.

## The optimizer never searches the GATEWAY or the ROUTE

**`PARAM_FORBIDDEN_KEYS` (`domain/search_point.py`) is an invariant**: the operator sets a backend node's route in the dataset overlay ([`promptpotter/assets/datasets/CLAUDE.md`](../../../assets/datasets/CLAUDE.md) § Sole route), the optimizer's in `OptimizationConfig.determinism`, applied last. `model` is an axis opened per node by `optimizer.param_keys` within `param_allowed_values["model"]`; steering outside it is [decisions](../../../../docs/decisions.md) § 0005's.

## Editing a renderer's PROSE is a measurement change

`nodes.py::source_digest` hashes the prompt-shaping set, so rewording a directive voids banked origins. A prompt-shaping constant lives in `bundle.py`; a unit reached only through an instance or a registry carries `shared/hashing.py::shapes_optimizer_prompt`.

## A validator either REJECTS or SCORES — never both

`*_strict` / `*_output` / `l1_invariants` reject (a `ValidationFailure` the layer heals); `*_behavior` only scores into `review.md`.

## Add no 4th layer — L4 is a recursion

**There is no `l4_*.py` in potter**: L4 is PromptPotter applied to itself through the `promptpotter` connector — [`runner/inner/CLAUDE.md`](../../runner/inner/CLAUDE.md) § Where L4 lives; spec [`l4-outer-loop.md`](../../../../docs/specs/l4-outer-loop.md).
