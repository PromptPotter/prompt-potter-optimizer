# Schema-description axis — structured-output `description` as an optimizable parameter

> **Status:** **built at the core (target) level, unmeasured.** The axis is schema-driven and always-on: **any** pipeline node that ships an `output_schema` gets its `description` prose as a tunable. Open: the `--sweep` gate (§ The open slice) — the first step that costs money. Why the schema steers at all: [`../concepts/structured-output.md`](../concepts/structured-output.md) (name / coordinates / description).

## The representation gap

Not excluded by a decision anyone defended — **invisible**, for one reason:

- `OptSearchPoint` addresses prompt fields + `pipeline_params` (node-keyed config dicts). **Data.**
- A node's `output_schema.description` strings are read by the model but by **no parser** — they were never on the tunable surface.

But *no lift is needed*. Every target node already carries its schema (`NodeOutputSchema.field_descriptions`, `pipeline_schema.py`) and it already reaches the model — the node's `output_schema` rides the wire to the backend, which sends it as `response_schema`. The strings need only be **reachable as an override** and **folded into the wire schema** — not stored as new data.

**Two schema-declaration shapes, not one.** A node declares its output schema either INLINE (`config.output_schema` — `justlogic`'s `llm_only`) or by REGISTRY IDENTITY (`config.schema_family` — TermNorm's `entity_profiling` / `llm_ranking`, resolved into the `resolved_schemas` block of `GET /pipeline`). Only the inline shape has a schema *in the node config* for the fold to write on. The registry shape must be materialized from `PipelineNode.output_schema.json_schema` at fold time — otherwise the descriptions are popped and silently dropped, and every proposal on those nodes hashes to its parent.

## Mechanism

Three seams, each a one-nesting-contract reuse — no bespoke code path:

1. **Synthesize at parse (`pipeline_parsing.py`).** Any node with an `output_schema` gets `output_schema_descriptions` added to its `param_keys` and declared `param_types: object` — exactly as a hand-declared nested param. Schema-driven, never a per-dataset opt-in.
2. **Emit (`build_l1_response_schema`, `validators/l1_strict.py`).** The nested `output_schema_descriptions` object is emitted under `pipeline_params_override[node]`, its `properties` keyed by **that node's own schema fields** (`_nested_param_property` reads `node.output_schema.fields`) with `additionalProperties: false` — describe a field, never invent one.
3. **Fold at the wire seam (`OptSearchPoint.to_job_search_point` → `fold_schema_descriptions`).** The override accumulates across generations as a normal `object` param (`apply_node_overlay` merges one level), then at render→wire it is written onto the node's real `output_schema.properties[field].description` **for existing fields only**, and the virtual key is deleted. The backend receives a valid schema and no pseudo-param.

```
pipeline_params_override:
  llm_only:                         # ← the TARGET node, not the optimizer
    output_schema_descriptions:
      answer: "TRUE / FALSE / Uncertain only. Uncertain is not a hedge against difficulty."
```

**Pydantic / the dataset schema stays the sole default.** No default table. With no override bound, `fold_schema_descriptions` is a no-op and the wire schema is byte-identical, so a dataset's C0 is unchanged *by construction*.

- **Renaming is impossible.** The fold assigns onto existing properties only, so an invented field name is dropped before the wire; the emitted object enumerates the node's fields with `additionalProperties: false`, so the LLM cannot emit one that does not exist. Field NAMES stay locked because the rename object is never grafted onto a target node's emittable surface (`SCHEMA_RENAME_PARAM`, `domain/pipeline_schema.py`) — the prose is the only free surface. (`SCHEMA_OWNED_FIELDS` is a related but different lock: it holds the four *config keys* — `output_schema`, `schema_family`, `schema_version`, `answer_field` — that the schema owns and no axis may set. Not field names.) The separate, outer-only *rename* lever — § Unlocking the name — is L4's, not a target axis.
- **Cache-safe.** Two description sets fold to two different `output_schema`s, so identity/measurement keys separate them; the virtual key is gone before hashing the wire point, so nothing double-counts. This holds *only* because the fold resolves the registry shape too — before that it silently folded nothing on those nodes, and two opposite steers shared one `sp_hash`.
- **An echo is a no-op.** The parent's current prose lives folded inside `output_schema`, so the parent never carries the virtual key and a naive diff reads *any* description proposal as a mutation. `detect_invariants` reconstructs the parent's effective value per param, so a variant restating what the parent already holds is rejected before it burns a scoring pass — the same guard that catches a `temperature: 0.0` proposal onto a parent at `0.0`.

## Permission tiers — what the optimizer may touch

Ride the existing lock. `PARAM_FORBIDDEN_KEYS` already implements exactly this shape for `model`/`provider` (always locked): `PipelineSchema.node_param_keys()` **drops the keys from the emitted JSON Schema**, so the LLM cannot emit a key that does not exist. Structural, not policed per round. No second gate.

| Tier | Surface | Default |
|---|---|---|
| **Free** — the axis | `description` strings; field order; `enum` order + per-value gloss | **On.** The optimizer *should* propose better descriptions — that is the point. |
| **Locked** — contract | field names, dot-paths, `enum` values | **Off.** Not via `PARAM_FORBIDDEN_KEYS` — `L1Variant`'s field names are not `pipeline_params`; the lock is that the rename object is never grafted, so the key does not exist to emit. Unlock forks the cycle (§ Unlocking the name). |

The overlay exposes description strings and a field permutation, **never a raw schema.** An optimizer handed a whole schema renames `candidate` and takes the pipeline down ([`../concepts/structured-output.md`](../concepts/structured-output.md) § which levers are free).

**L2 unlocks through the surface it already has.** Its control vocabulary stays closed at `fork_proposal` + `terminate_proposal` ([`../../promptpotter/application/optimization/CLAUDE.md`](../../promptpotter/application/optimization/CLAUDE.md)) — a *third* control output would be an architectural amendment. But `fork_proposal` is the right vehicle regardless of L2: `schema_field_rename` declares itself `Knob(Scope.POLICY, Estimand.SEARCH)` on its own `CampaignConfig` field, so unlocking an axis invalidates search comparability and **must** mint a sibling cycle rather than mutate the running one. It rides `ConfigOverrides` — the same channel `per_round_resubset` already uses for the operator's "behaviour-knob change → fork-at-offset-0" workflow.

## Unlocking the name (lever 1) — SHIPPED contract, inert until unlocked

The field **name** is the strongest lever and the one that breaks things (`../concepts/structured-output.md` § 1); it stays locked in base mode. The shipped contract:

- **Rename is a pure presentation transform.** `output_schema_field_names: {model_field: wire_name}` rides the same per-node override object; `build_l1_response_model` binds the wire key back onto the real field via `validation_alias` (`populate_by_name` off — the old key stops validating), so every downstream reader still sees `changes_description`. Both surfaces derive from one function, `effective_l1_field_names()`; `tests/test_integrity.py` pins the round-trip, the inner-apply, and the old-key rejection.
- **A policy knob, not a param.** `optimization.schema_field_rename` (default `false`, `policy` + `Estimand.SEARCH`). Locked ⇒ the rename object is never grafted, so the LLM cannot emit a key that does not exist. Unlocking invalidates search comparability, so it **must** mint a sibling cycle — L2 reaches it via `fork_proposal`, never a mutation of the running cycle.
- **The knob gates the PROPOSAL, never the apply.** An inner cycle honours a rename it is handed unconditionally (it loads the *inner* dataset's `campaign.json`, never the outer's) — gating the apply would silently no-op every outer-emitted rename and score it as a legitimate mutation.
- **Safety is parse-failure attribution, not policing:** a rename the model ignores or mangles is an unparseable round charged `problem_rate = 1.0` (`RoundResult.l1_parse_failure`), so the optimizer steers away on its own.

**Unmeasured — still open.** "Huge lever" is an empirical claim and this repo adjudicates exactly that claim. The **description** axis deliberately has **no toggle**: it is always on, and a negative sweep closes the spec by reverting the commit rather than leaving dead config behind. Keep it that way — a disabled-but-present axis is a fallback chain wearing a flag. The **rename** axis is the one exception, and it earns the lock: a field name is the wire contract, so `schema_field_rename` is `policy`-classified and can only be opened by the fork that isolates it. A default-on rename lever would widen every campaign's search space with nothing in the ledger to say when, or why.

## The open slice — the sweep gate

`--sweep` on `justlogic`; promote only on `proxy_lift_corr ≥ 0.6`. **A negative result closes this spec** — record the finding and revert. That is a successful outcome. **Not yet run — this is the next action, and the first that costs money.**

**One comparability caveat.** Turning the axis on adds `output_schema_descriptions` to the emittable params of any target node that ships a schema, which changes that dataset's L1 in-context tokens. A dataset's C0 therefore shifts, and its runs from before this commit are not comparable to runs after it. With no override bound the wire schema is byte-identical, so only the *emittable-surface* text moves, not the origin's actual schema.

## Scope — two surfaces, not one

**Optimizer-owned** (`dispatch/schemas.py`, Pydantic): the representation gap above applies.

**TermNorm**:

- Schemas are **already versioned JSON data** — `backend-api/logs/schemas/{family}/{version}/schema.json`, families `entity_profile` + `llm_ranking_output`, behind `utils/schema_registry.py`. No lift needed.
- **`output_schema` is live but unset — and the registry schemas never reach the model.** `api/pipeline.py::_resolve_registries` publishes them under a top-level `resolved_schemas` key and *does not modify node configs* (line 33). At runtime `lr_cfg.get("output_schema")` is `None`, so `output_format` falls to `"json"` — free-form. The real second prompt is a hand-written JSON example inlined in the prompt string (`research_and_rank/call_llm_for_ranking.py:113-126`, *"matching this exact structure"*), and it repeats the same defect: `candidate → core_concept_score → spec_score → evaluation_reasoning`. **Fixing `schema.json` is a no-op; the hoist belongs in the prompt example.**
- **Hazard.** `output_schema` is an override slot only PP's overlay can fill. Set it and the whole schema — field names included — comes under optimizer control, and the ranking code reads `ranked_candidates`/`candidate` by key. Narrow to descriptions + order before it is ever unlocked.
- **Two sources of truth.** `initialize_default_schemas()` skips families that already exist on disk, so editing the Python literals does *not* update an installed schema. On-disk wins silently.
- **Live lever-2 defect.** `llm_ranking_output.ranked_candidates.items` orders `candidate → core_concept_score → spec_score → evaluation_reasoning → …`. The scores that drive match ranking are emitted **before** any reasoning exists in context. Fix is a reorder, not an optimization.

Backends stay read-only *at runtime* ([`../../promptpotter/application/CLAUDE.md`](../../promptpotter/application/CLAUDE.md)) — per-dataset tunables ride the overlay. But TermNorm is co-owned, and a structural defect in its schema is a TermNorm root-fix, coordinated. The highway is a shape contract: both sides land together or neither does.
