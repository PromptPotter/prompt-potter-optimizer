# Schema-description axis — structured-output `description` as an optimizable parameter

> **Status:** **built, unmeasured.** Slices 1-3 shipped; the axis is live on `promptpotter-self` and has never been swept (slice 4). Why the schema steers at all: [`../concepts/structured-output.md`](../concepts/structured-output.md) (name / coordinates / description). This spec covers only what it takes to make the third lever searchable.

## The representation gap

Not excluded by a decision anyone defended — **invisible**, for one reason:

- `OptSearchPoint` addresses prompt fields + `pipeline_params` (node-keyed config dicts). **Data.**
- The ~45 `description=` strings (17 in `dispatch/schemas.py`) live in Python source. **Code.**

The population cannot reach them. But *no lift is needed* — the strings need only be **resolvable** where the wire schema is built, not **stored** as data.

## Mechanism

`build_l1_output_schema()` (`validators/l1_strict.py`) already derives the wire schema from Pydantic **at call time**. That is the seam: after inlining, it assigns overridden `description` strings onto properties that already exist.

The edit rides the per-node override object the L4 outer cycle already owns — the same channel `layout` uses (`set_optimizer_prompt_overrides` → `overrides[node]["output_schema_descriptions"]`), resolved back by `resolve_node_schema_descriptions()`:

```
pipeline_params_override:
  l1_generate:
    output_schema_descriptions:
      changes_description: "Name the failure pattern, then the concrete change."
```

**Pydantic stays the sole default.** No default table — a second home for the same strings is the six-copy defect (`schemas.py:11-14` already declares Pydantic the SoT). With no override bound, every normal cycle builds today's schema byte-for-byte, so C0 is unchanged *by construction* rather than by a byte-comparison ritual.

Two properties fall out of the seam rather than being policed:

- **Renaming is impossible.** The apply step assigns onto existing properties only, so an invented field name is dropped before the wire. The grafted `output_schema_descriptions` object enumerates `L1Variant`'s field names with `additionalProperties: false`, so the LLM cannot emit one that does not exist.
- **The graft is self-scoping.** On the **outer** (`promptpotter-self`) build, the pipeline has an `l1_generate` node, so the emittable key appears; the ContextVar is unbound in the outer task, so the outer's own schema is untouched. On the **inner** build, the backend pipeline has no `l1_generate` node, so nothing is grafted — and the bound override applies. Same function, opposite halves.

**Why the node-config route does not work.** `optimizer_node_config()` reads `datasets/_optimizer/pipeline.json` and nothing else (`prompts.py`, cached, no merge). A `nodes.{name}.config` key never reaches the inner optimizer; only the per-node override channel does. `datasets/_optimizer/pipeline.json` is *read* by the running optimizer — it is not the surface `promptpotter-self`'s `OptSearchPoint` mutates.

## Permission tiers — what the optimizer may touch

Ride the existing lock. `OptimizationConfig.forbidden_axes_strict` + `PARAM_FORBIDDEN_KEYS` already implement exactly this shape for `model`/`provider`: when locked, `PipelineSchema.node_param_keys()` **drops the keys from the emitted JSON Schema**, so the LLM cannot emit a key that does not exist. Structural, not policed per round. No second gate.

| Tier | Surface | Default |
|---|---|---|
| **Free** — the axis | `description` strings; field order; `enum` order + per-value gloss | **On.** The optimizer *should* propose better descriptions — that is the point. |
| **Locked** — contract | field names, dot-paths, `enum` values | **Off**, via `PARAM_FORBIDDEN_KEYS`. Unlock is an explicit operator act with a warning: renaming breaks every downstream parser and validator. |

The overlay exposes description strings and a field permutation, **never a raw schema.** An optimizer handed a whole schema renames `candidate` and takes the pipeline down ([`../concepts/structured-output.md`](../concepts/structured-output.md) § which levers are free).

**L2 does not unlock.** Its control vocabulary is closed at `fork_proposal` + `terminate_proposal` ([`../../promptpotter/application/optimization/CLAUDE.md`](../../promptpotter/application/optimization/CLAUDE.md)); a third control output is an architectural amendment, not a convenience. L2 requests through the surface it has; the operator flips the bit.

## Two blockers

**Reflexivity — cleared by slice 1.** Mutating `L1GenerateOutput`'s descriptions changes how the optimizer parses *its own children's* proposals. It used to be charged to **nobody**: `MetaPromptParseError` kills the whole `l1_generate` call (zero candidates) and appends the wound to the *parent's* `opt_sp.memory.wounds`; `mutate()` resets child wounds, so `_round_problem_rate`'s `parse_fail` sum over `rnd.candidate_scores` — empty in exactly that round — was structurally always `0`, and a candidate that made its children unreadable scored *perfectly clean*. `l1_generate` now returns the reason beside its empty candidate list; it rides `L1YieldStats` → `RoundResult.l1_parse_failure`, and `_round_problem_rate` charges the **round** `1.0`. A parse failure is never charged per-candidate — that round has no candidate to charge.

**Unmeasured — still open.** "Huge lever" is an empirical claim and this repo adjudicates exactly that claim. The axis is *reachable* (slice 3) but unproven. There is deliberately **no toggle**: a negative sweep closes the spec by reverting the commit, not by leaving dead config behind. Keep it that way — a disabled-but-present axis is a fallback chain wearing a flag.

## Slices

1. ~~**Parse-failure attribution.**~~ **Shipped.** A schema-induced parse failure is charged to the round it occurred in (`RoundResult.l1_parse_failure` → `_round_problem_rate` = `1.0`); before, a zero-candidate round scored `problem_rate = 0.0`, the cleanest possible. *(A live measurement bug independent of this spec.)*
2. ~~**Descriptions become data.**~~ **Dropped — the premise was wrong.** Nothing has to become data: `build_l1_output_schema` resolves the overrides at call time and Pydantic keeps the defaults. A default table would have given the strings two homes.
3. ~~**One axis, narrowest scope.**~~ **Shipped.** `promptpotter-self` only, node `l1_generate`, model `L1Variant` — the graft is self-scoping (see Mechanism), so nothing gates it by dataset name. Pinned by `tests/test_integrity.py::test_schema_description_axis_reaches_the_model_and_cannot_rename_a_field`, which fails if the grafted key ever drifts from the resolved one (a silent no-op axis) or if a field could be renamed.
4. **Sweep gate.** `--sweep` on `justlogic`; promote only on `proxy_lift_corr ≥ 0.6`. **A negative result closes this spec** — record the finding and stop. That is a successful outcome. **Not yet run — this is the next action, and the first that costs money.**
5. **Widen** to remaining optimizer-owned schemas, iff 4 passes.

**One comparability caveat.** Turning the axis on adds `output_schema_descriptions` to `promptpotter-self`'s emittable `l1_generate` params, which changes the **outer** meta-prompt's in-context tokens. Outer C0 therefore shifts, and pp-self runs from before this commit are not comparable to runs after it. Inner cycles and every normal campaign are untouched — their backend pipelines carry no `l1_generate` node, and with no override bound the schema is Pydantic's byte-for-byte.

## Scope — two surfaces, not one

**Optimizer-owned** (`dispatch/schemas.py`, Pydantic): the representation gap above applies.

**TermNorm** (2026-07-08 — *corrects an earlier claim in this spec that no surface existed; that claim came from grepping Python for `description=`, which finds only `register_schema(description=…)` metadata, not field descriptions*):

- Schemas are **already versioned JSON data** — `backend-api/logs/schemas/{family}/{version}/schema.json`, families `entity_profile` + `llm_ranking_output`, behind `utils/schema_registry.py`. No lift needed.
- **`output_schema` is live but unset — and the registry schemas never reach the model.** `api/pipeline.py::_resolve_registries` publishes them under a top-level `resolved_schemas` key and *does not modify node configs* (line 33). At runtime `lr_cfg.get("output_schema")` is `None`, so `output_format` falls to `"json"` — free-form. The real second prompt is a hand-written JSON example inlined in the prompt string (`research_and_rank/call_llm_for_ranking.py:113-126`, *"matching this exact structure"*), and it repeats the same defect: `candidate → core_concept_score → spec_score → evaluation_reasoning`. **Fixing `schema.json` is a no-op; the hoist belongs in the prompt example.**
- **Hazard.** `output_schema` is an override slot only PP's overlay can fill. Set it and the whole schema — field names included — comes under optimizer control, and the ranking code reads `ranked_candidates`/`candidate` by key. Narrow to descriptions + order before it is ever unlocked.
- **Two sources of truth.** `initialize_default_schemas()` skips families that already exist on disk, so editing the Python literals does *not* update an installed schema. On-disk wins silently.
- **Live lever-2 defect.** `llm_ranking_output.ranked_candidates.items` orders `candidate → core_concept_score → spec_score → evaluation_reasoning → …`. The scores that drive match ranking are emitted **before** any reasoning exists in context. Fix is a reorder, not an optimization.

Backends stay read-only *at runtime* ([`../../promptpotter/application/CLAUDE.md`](../../promptpotter/application/CLAUDE.md)) — per-dataset tunables ride the overlay. But TermNorm is co-owned, and a structural defect in its schema is a TermNorm root-fix, coordinated. The highway is a shape contract: both sides land together or neither does.
