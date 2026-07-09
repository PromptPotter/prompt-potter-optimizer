# Schema-description axis — structured-output `description` as an optimizable parameter

> **Status:** **built at the core (target) level, unmeasured.** The axis is now schema-driven and always-on: **any** pipeline node that ships an `output_schema` gets its `description` prose as a tunable, so `l1_generate` describing `justlogic`'s `{reasoning, answer}` is the same code path that describes any target's output. (An earlier build wired it one level too high — against the optimizer's *own* `l1_generate` response schema, `L1Variant`-hardcoded, gated behind `promptpotter-self`'s `param_keys`. That was the wrong seam; it has been replaced.) Not yet swept (slice 4). Why the schema steers at all: [`../concepts/structured-output.md`](../concepts/structured-output.md) (name / coordinates / description).

## The representation gap

Not excluded by a decision anyone defended — **invisible**, for one reason:

- `OptSearchPoint` addresses prompt fields + `pipeline_params` (node-keyed config dicts). **Data.**
- A node's `output_schema.description` strings are read by the model but by **no parser** — they were never on the tunable surface.

But *no lift is needed*. Every target node already carries its schema (`NodeOutputSchema.field_descriptions`, `pipeline_schema.py`) and the connector already ships it to the model (`llm_only.py`). The strings need only be **reachable as an override** and **folded into the wire schema** — not stored as new data.

**Two schema-declaration shapes, not one.** A node declares its output schema either INLINE (`config.output_schema` — `justlogic`'s `llm_only`) or by REGISTRY IDENTITY (`config.schema_family` — TermNorm's `entity_profiling` / `llm_ranking`, resolved into the `resolved_schemas` block of `GET /pipeline`). Only the inline shape has a schema *in the node config* for the fold to write on. The registry shape must be materialized from `PipelineNode.output_schema.json_schema` at fold time — otherwise the descriptions are popped and silently dropped, and every proposal on those nodes hashes to its parent.

## Mechanism

Three seams, each a one-nesting-contract reuse — no bespoke code path:

1. **Synthesize at parse (`pipeline_parsing.py`).** Any node with an `output_schema` gets `output_schema_descriptions` added to its `param_keys` and declared `param_types: object` — exactly as a hand-declared nested param. Schema-driven, never a per-dataset opt-in.
2. **Emit (`build_l1_output_schema`, `validators/l1_strict.py`).** The nested `output_schema_descriptions` object is emitted under `pipeline_params_override[node]`, its `properties` keyed by **that node's own schema fields** (`_nested_param_property` reads `node.output_schema.fields`) with `additionalProperties: false` — describe a field, never invent one.
3. **Fold at the wire seam (`OptSearchPoint.to_job_search_point` → `fold_schema_descriptions`).** The override accumulates across generations as a normal `object` param (`apply_node_overlay` merges one level), then at render→wire it is written onto the node's real `output_schema.properties[field].description` **for existing fields only**, and the virtual key is deleted. The backend receives a valid schema and no pseudo-param.

```
pipeline_params_override:
  llm_only:                         # ← the TARGET node, not the optimizer
    output_schema_descriptions:
      answer: "TRUE / FALSE / Uncertain only. Uncertain is not a hedge against difficulty."
```

**Pydantic / the dataset schema stays the sole default.** No default table. With no override bound, `fold_schema_descriptions` is a no-op and the wire schema is byte-identical, so a dataset's C0 is unchanged *by construction*.

- **Renaming is impossible.** The fold assigns onto existing properties only, so an invented field name is dropped before the wire; the emitted object enumerates the node's fields with `additionalProperties: false`, so the LLM cannot emit one that does not exist. Field NAMES stay locked in `SCHEMA_OWNED_FIELDS` — the prose is the only free surface. (The separate, meta-only *rename* lever — § Unlocking the name — is L4's, not a target axis.)
- **Cache-safe.** Two description sets fold to two different `output_schema`s, so identity/measurement keys separate them; the virtual key is gone before hashing the wire point, so nothing double-counts. This holds *only* because the fold resolves the registry shape too — before that it silently folded nothing on those nodes, and two opposite steers shared one `sp_hash`.
- **An echo is a no-op.** The parent's current prose lives folded inside `output_schema`, so the parent never carries the virtual key and a naive diff reads *any* description proposal as a mutation. `detect_invariants` reconstructs the parent's effective value per param, so a variant restating what the parent already holds is rejected before it burns a scoring pass — the same guard that catches a `temperature: 0.0` proposal onto a parent at `0.0`.

## Permission tiers — what the optimizer may touch

Ride the existing lock. `OptimizationConfig.forbidden_axes_strict` + `PARAM_FORBIDDEN_KEYS` already implement exactly this shape for `model`/`provider`: when locked, `PipelineSchema.node_param_keys()` **drops the keys from the emitted JSON Schema**, so the LLM cannot emit a key that does not exist. Structural, not policed per round. No second gate.

| Tier | Surface | Default |
|---|---|---|
| **Free** — the axis | `description` strings; field order; `enum` order + per-value gloss | **On.** The optimizer *should* propose better descriptions — that is the point. |
| **Locked** — contract | field names, dot-paths, `enum` values | **Off.** Not via `PARAM_FORBIDDEN_KEYS` — `L1Variant`'s field names are not `pipeline_params`; the lock is that the rename object is never grafted, so the key does not exist to emit. Unlock forks the cycle (§ Unlocking the name). |

The overlay exposes description strings and a field permutation, **never a raw schema.** An optimizer handed a whole schema renames `candidate` and takes the pipeline down ([`../concepts/structured-output.md`](../concepts/structured-output.md) § which levers are free).

**L2 unlocks through the surface it already has.** Its control vocabulary stays closed at `fork_proposal` + `terminate_proposal` ([`../../promptpotter/application/optimization/CLAUDE.md`](../../promptpotter/application/optimization/CLAUDE.md)) — a *third* control output would be an architectural amendment. But `fork_proposal` is the right vehicle regardless of L2: `schema_field_rename` is classified **policy** (`config_diff.py`) and bound to `Estimand.SEARCH` (`config_coupling.py`), so unlocking an axis invalidates search comparability and **must** mint a sibling cycle rather than mutate the running one. It rides `ConfigOverrides` — the same channel `per_round_resubset` already uses for the operator's "behaviour-knob change → fork-at-offset-0" workflow.

## Unlocking the name (lever 1)

The field **name** is the strongest lever and the one that breaks things (`../concepts/structured-output.md` § 1). It is locked by default and stays that way in base mode. Making it reachable is *not* a matter of loosening a check:

**A rename is fatal today, not self-healing.** `L1GenerateOutput` is `extra="forbid"` and requires `changes_description`. A model that emits `mutation_rationale` instead fails validation, `l1_generate` returns zero candidates, and the round is charged `problem_rate = 1.0` (slice 1 — *before* slice 1 it scored perfectly clean, which is why this was unsafe to contemplate). So a rename is always punished and can never pay off. The lever only exists if the same override map that renames the property in the emitted schema **un-renames the key before validation**. Rename is then a pure presentation transform: the model's priors about the key change; every downstream reader still sees `changes_description`.

1. **Un-rename at the parse seam — SHIPPED.** `output_schema_field_names: {model_field: wire_name}` rides the same per-node override object. `build_l1_response_model` derives an `L1GenerateOutput` subclass whose fields carry a `validation_alias`, so the wire key binds back onto the real field and every downstream reader still sees `changes_description`. `populate_by_name` stays **off**: the old key stops validating, so a rename the model ignores is a parse failure, not a half-applied mutation.
2. **A policy knob, not a param — SHIPPED.** `L1Variant`'s field names are not `pipeline_params`, so `PARAM_FORBIDDEN_KEYS` was the wrong lock. `optimization.schema_field_rename` (default `false`), classified `policy` + coupled to `Estimand.SEARCH`. Locked ⇒ the object is never grafted, so the LLM cannot emit a key that does not exist.
3. **L2 reaches it via `fork_proposal` — SHIPPED.** The unlock cannot travel without the rewind, so the parent keeps its frozen config and its comparability; re-requesting an open lock is dropped, since it would change nothing and still cost a sibling cycle. Seam contract: `promptpotter/application/optimization/CLAUDE.md` § layer-control channel.
4. **It should rarely fire in base mode — SHIPPED.** The clause rides the existing `rebase_capability` directive (one more sentence about the same emitted object; a second injection would render a blank line into every prompt that lacks the lever). It appears only where the unlock is *reachable* — some node DECLARES `SCHEMA_RENAME_PARAM` — and *not already open*. Reachability reads off the declaration, never a node name. The copy names the narrow condition ("stalling on what a field is FOR, not on what it says") and closes with "describe the field, do not rename it."

**The remaining slice is measurement, and it is the only one that costs money.** Every mechanism above is inert until a campaign unlocks — by `campaign.json` at mint, by an operator steer-fork, or by an L2/L3 request.

**The knob gates the PROPOSAL, never the apply.** An inner cycle honours a rename it is handed unconditionally — as it already does for prose, `layout`, and descriptions. Gating the apply on the inner cycle's config would silently drop every rename the outer emits, because an inner campaign loads the *inner dataset's* `campaign.json`, never the outer's (`runner/inner_recursion.py`) — and the outer would score that no-op as a legitimate mutation.

Both surfaces derive the rename from one function, `effective_l1_field_names()`. A schema that renames a field the response model does not alias would fail every parse of every round; `tests/test_integrity.py` pins the round-trip, the inner-apply, and the old-key rejection.

Safety rests on slice 1, not on cleverness: a rename the model then ignores or mangles yields an unparseable shape, and that round now scores maximally dirty. The optimizer steers away without anybody policing it.

## Two blockers

**Reflexivity — cleared by slice 1.** Mutating `L1GenerateOutput`'s descriptions changes how the optimizer parses *its own children's* proposals. It used to be charged to **nobody**: `MetaPromptParseError` kills the whole `l1_generate` call (zero candidates) and appends the wound to the *parent's* `opt_sp.memory.wounds`; `mutate()` resets child wounds, so `_round_problem_rate`'s `parse_fail` sum over `rnd.candidate_scores` — empty in exactly that round — was structurally always `0`, and a candidate that made its children unreadable scored *perfectly clean*. `l1_generate` now returns the reason beside its empty candidate list; it rides `L1YieldStats` → `RoundResult.l1_parse_failure`, and `_round_problem_rate` charges the **round** `1.0`. A parse failure is never charged per-candidate — that round has no candidate to charge.

**Unmeasured — still open.** "Huge lever" is an empirical claim and this repo adjudicates exactly that claim. The axis is *reachable* (slice 3) but unproven. The **description** axis deliberately has **no toggle**: it is always on, and a negative sweep closes the spec by reverting the commit rather than leaving dead config behind. Keep it that way — a disabled-but-present axis is a fallback chain wearing a flag. The **rename** axis is the one exception, and it earns the lock: a field name is the wire contract, so `schema_field_rename` is `policy`-classified and can only be opened by the fork that isolates it (below). A default-on rename lever would widen every campaign's search space with nothing in the ledger to say when, or why.

## Slices

1. ~~**Parse-failure attribution.**~~ **Shipped.** A schema-induced parse failure is charged to the round it occurred in (`RoundResult.l1_parse_failure` → `_round_problem_rate` = `1.0`); before, a zero-candidate round scored `problem_rate = 0.0`, the cleanest possible. *(A live measurement bug independent of this spec.)*
2. ~~**Descriptions become data.**~~ **Dropped — the premise was wrong.** Nothing has to become data: `build_l1_output_schema` resolves the overrides at call time and Pydantic keeps the defaults. A default table would have given the strings two homes.
3. ~~**One axis, narrowest scope (`promptpotter-self` / `l1_generate` / `L1Variant`).**~~ **Superseded.** That was the wrong level — the optimizer describing its own response schema. Replaced by the core, schema-driven build above: the lever is synthesized onto **every** `output_schema`-bearing target node, keyed by that node's own fields. Pinned by `tests/test_integrity.py::test_schema_description_axis_reaches_the_target_and_cannot_rename_a_field`, which fails if the emitted key drifts from the folded one (a silent no-op axis) or if an invented field could reach the wire.
4. **Sweep gate.** `--sweep` on `justlogic`; promote only on `proxy_lift_corr ≥ 0.6`. **A negative result closes this spec** — record the finding and revert. That is a successful outcome. **Not yet run — this is the next action, and the first that costs money.**
5. ~~**Widen** to remaining optimizer-owned schemas.~~ **Folded into the core build** — there is no widening step; it is on for all targets at once.

**One comparability caveat.** Turning the axis on adds `output_schema_descriptions` to the emittable params of any target node that ships a schema, which changes that dataset's L1 in-context tokens. A dataset's C0 therefore shifts, and its runs from before this commit are not comparable to runs after it. With no override bound the wire schema is byte-identical, so only the *emittable-surface* text moves, not the origin's actual schema.

## Scope — two surfaces, not one

**Optimizer-owned** (`dispatch/schemas.py`, Pydantic): the representation gap above applies.

**TermNorm** (2026-07-08 — *corrects an earlier claim in this spec that no surface existed; that claim came from grepping Python for `description=`, which finds only `register_schema(description=…)` metadata, not field descriptions*):

- Schemas are **already versioned JSON data** — `backend-api/logs/schemas/{family}/{version}/schema.json`, families `entity_profile` + `llm_ranking_output`, behind `utils/schema_registry.py`. No lift needed.
- **`output_schema` is live but unset — and the registry schemas never reach the model.** `api/pipeline.py::_resolve_registries` publishes them under a top-level `resolved_schemas` key and *does not modify node configs* (line 33). At runtime `lr_cfg.get("output_schema")` is `None`, so `output_format` falls to `"json"` — free-form. The real second prompt is a hand-written JSON example inlined in the prompt string (`research_and_rank/call_llm_for_ranking.py:113-126`, *"matching this exact structure"*), and it repeats the same defect: `candidate → core_concept_score → spec_score → evaluation_reasoning`. **Fixing `schema.json` is a no-op; the hoist belongs in the prompt example.**
- **Hazard.** `output_schema` is an override slot only PP's overlay can fill. Set it and the whole schema — field names included — comes under optimizer control, and the ranking code reads `ranked_candidates`/`candidate` by key. Narrow to descriptions + order before it is ever unlocked.
- **Two sources of truth.** `initialize_default_schemas()` skips families that already exist on disk, so editing the Python literals does *not* update an installed schema. On-disk wins silently.
- **Live lever-2 defect.** `llm_ranking_output.ranked_candidates.items` orders `candidate → core_concept_score → spec_score → evaluation_reasoning → …`. The scores that drive match ranking are emitted **before** any reasoning exists in context. Fix is a reorder, not an optimization.

Backends stay read-only *at runtime* ([`../../promptpotter/application/CLAUDE.md`](../../promptpotter/application/CLAUDE.md)) — per-dataset tunables ride the overlay. But TermNorm is co-owned, and a structural defect in its schema is a TermNorm root-fix, coordinated. The highway is a shape contract: both sides land together or neither does.
