# Schema-description axis — structured-output `description` as an optimizable parameter

> **Status:** design. **Nothing below is implemented.** Why the schema steers at all: [`../concepts/structured-output.md`](../concepts/structured-output.md) (name / coordinates / description). This spec covers only what it takes to make the third lever searchable.

## The representation gap

Not excluded by a decision anyone defended — **invisible**, for one reason:

- `OptSearchPoint` addresses prompt fields + `pipeline_params` (node-keyed config dicts) in `datasets/{name}/pipeline.json::nodes.{name}.config`. **Data.**
- The ~45 `description=` strings (17 in `dispatch/schemas.py`) live in Python source. **Code.**

The population cannot reach them. Lift them into the overlay and the axis exists — `OptSearchPoint` already carries node config. **No new searchpoint machinery, no sidecar.** That is the whole change.

## Mechanism

`schemas.py` stops holding literals. One default table; schema construction resolves each `description` from the overlay, keyed `{Model}.{field}`:

```
nodes.l1_generate.config.output_schema_descriptions:
  L1Variant:
    changes_description: "Why this variant differs from its parent, in one line."
```

Defaults reproduce today's strings byte-for-byte, so C0 is unchanged and existing measurements stay comparable. Because `promptpotter-self` optimizes `datasets/_optimizer/pipeline.json`, **the axis is an L4 axis the moment the strings are data.**

## Permission tiers — what the optimizer may touch

Ride the existing lock. `OptimizationConfig.forbidden_axes_strict` + `PARAM_FORBIDDEN_KEYS` already implement exactly this shape for `model`/`provider`: when locked, `PipelineSchema.node_param_keys()` **drops the keys from the emitted JSON Schema**, so the LLM cannot emit a key that does not exist. Structural, not policed per round. No second gate.

| Tier | Surface | Default |
|---|---|---|
| **Free** — the axis | `description` strings; field order; `enum` order + per-value gloss | **On.** The optimizer *should* propose better descriptions — that is the point. |
| **Locked** — contract | field names, dot-paths, `enum` values | **Off**, via `PARAM_FORBIDDEN_KEYS`. Unlock is an explicit operator act with a warning: renaming breaks every downstream parser and validator. |

The overlay exposes description strings and a field permutation, **never a raw schema.** An optimizer handed a whole schema renames `candidate` and takes the pipeline down ([`../concepts/structured-output.md`](../concepts/structured-output.md) § which levers are free).

**L2 does not unlock.** Its control vocabulary is closed at `fork_proposal` + `terminate_proposal` ([`../../promptpotter/application/optimization/CLAUDE.md`](../../promptpotter/application/optimization/CLAUDE.md)); a third control output is an architectural amendment, not a convenience. L2 requests through the surface it has; the operator flips the bit.

## Two blockers

**Reflexivity.** Mutating `L1GenerateOutput`'s descriptions changes how the optimizer parses *its own children's* proposals. Not self-penalizing at all: the failure is charged to **nobody**. `MetaPromptParseError` kills the whole `l1_generate` call (zero candidates) and appends the wound to the *parent's* `opt_sp.memory.wounds`; `mutate()` resets child wounds, so `_round_problem_rate`'s `parse_fail` sum over `rnd.candidate_scores` — empty in exactly that round — is structurally always `0`. A candidate that makes its children unreadable scores *perfectly clean*. Slice 1 fixes attribution; the axis stays off until it does.

**Unmeasured.** "Huge lever" is an empirical claim and this repo adjudicates exactly that claim. Turned on when `--sweep` says so, not because it sounds right.

## Slices

1. **Parse-failure attribution.** Charge a schema-induced parse failure to the round it occurred in — today a zero-candidate round scores `problem_rate = 0.0`, the cleanest possible. Prerequisite; ships alone, measurable on existing cycles. *(A live measurement bug independent of this spec.)*
2. **Descriptions become data.** Default table + overlay resolution. Byte-identical defaults; C0 must reproduce exactly. Pure refactor, no axis.
3. **One axis, narrowest scope.** `datasets/promptpotter-self/` only, node `l1_generate`, model `L1Variant`.
4. **Sweep gate.** `--sweep` on `justlogic`; promote only on `proxy_lift_corr ≥ 0.6`. **A negative result closes this spec** — record the finding and stop. That is a successful outcome.
5. **Widen** to remaining optimizer-owned schemas, iff 4 passes.

## Scope — two surfaces, not one

**Optimizer-owned** (`dispatch/schemas.py`, Pydantic): the representation gap above applies.

**TermNorm** (2026-07-08 — *corrects an earlier claim in this spec that no surface existed; that claim came from grepping Python for `description=`, which finds only `register_schema(description=…)` metadata, not field descriptions*):

- Schemas are **already versioned JSON data** — `backend-api/logs/schemas/{family}/{version}/schema.json`, families `entity_profile` + `llm_ranking_output`, behind `utils/schema_registry.py`. No lift needed.
- **`output_schema` is live but unset — and the registry schemas never reach the model.** `api/pipeline.py::_resolve_registries` publishes them under a top-level `resolved_schemas` key and *does not modify node configs* (line 33). At runtime `lr_cfg.get("output_schema")` is `None`, so `output_format` falls to `"json"` — free-form. The real second prompt is a hand-written JSON example inlined in the prompt string (`research_and_rank/call_llm_for_ranking.py:113-126`, *"matching this exact structure"*), and it repeats the same defect: `candidate → core_concept_score → spec_score → evaluation_reasoning`. **Fixing `schema.json` is a no-op; the hoist belongs in the prompt example.**
- **Hazard.** `output_schema` is an override slot only PP's overlay can fill. Set it and the whole schema — field names included — comes under optimizer control, and the ranking code reads `ranked_candidates`/`candidate` by key. Narrow to descriptions + order before it is ever unlocked.
- **Two sources of truth.** `initialize_default_schemas()` skips families that already exist on disk, so editing the Python literals does *not* update an installed schema. On-disk wins silently.
- **Live lever-2 defect.** `llm_ranking_output.ranked_candidates.items` orders `candidate → core_concept_score → spec_score → evaluation_reasoning → …`. The scores that drive match ranking are emitted **before** any reasoning exists in context. Fix is a reorder, not an optimization.

Backends stay read-only *at runtime* ([`../../promptpotter/application/CLAUDE.md`](../../promptpotter/application/CLAUDE.md)) — per-dataset tunables ride the overlay. But TermNorm is co-owned, and a structural defect in its schema is a TermNorm root-fix, coordinated. The highway is a shape contract: both sides land together or neither does.
