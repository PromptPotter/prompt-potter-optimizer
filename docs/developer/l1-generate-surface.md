# L1 Layout + Dispatch Hub

L1's prompt is composed by walking a per-slot list of **injection names** and resolving each name through a single registry. L2 owns the layout; the registry is closed and code-derived. Concept role: [`the-loop.md § L2 in detail`](../concepts/the-loop.md).

## What "layout" means

```
┌─ L1's prompt composition ──────────────────────────────────┐
│  PromptTemplate (l1_generate)        per-slot static text  │
│      +                                                     │
│  L1Layout (on OptSearchPoint)    per-slot injection lists  │
│      ↓                                                     │
│  DispatchHub.fill_l1                 resolves names via    │
│                                      INJECTIONS registry   │
│      ↓                                                     │
│  RENDERED L1 PROMPT (what the LLM sees)                    │
└────────────────────────────────────────────────────────────┘
```

Every injection name in the layout maps to a renderer `(InjectionBundle) → str` in `INJECTIONS` (`dispatch/hub/injections/`). Renderers are layer-agnostic — the same `plan` renderer feeds L1, L2, and L3.

## Layout — `L1Layout`

`L1Layout` (`promptpotter/domain/l1_layout.py`) is a Pydantic model with one list per addressable slot:

| Slot | Mutable by L2 |
|------|---------------|
| `persona` | yes |
| `task_intent` | yes |
| `problem_description` | yes |
| `thinking_style` | yes |

`answer_format` is omitted on purpose — it carries L1's output JSON schema (a code contract), not L2's call. Static text in each slot stays; the layout's injection renderings are appended.

`L1_POSSIBLE` (subset of `INJECTIONS`) is the menu L2 picks from. L2-internal injections (`l1_overrides`, `l1_signal_catalogue`) are deliberately excluded from L1's slots — `l1_overrides`'s contents reach L1 only via the `n_variants`/`creativity` caller extras (the in-prompt directive + the LLM-call temperature, respectively). `L1_MANDATORY` (`plan`, `task_context`, `rendered_prompt`, `pipeline_param_catalogue`, `critique`) must appear somewhere across the slots — without these L1 has no parent prompt, no plan, no task framing, no mutation surface, and no round-local failure digest. Dropping any of them fires `l1_layout_missing_mandatory` — a guard breach that routes to L3 (replan) rather than letting L2 starve L1.

Default layout (`default_l1_layout`): `task_context` in `task_intent`; `rendered_prompt`, `pipeline_param_catalogue`, `plan`, `diagnostics`, `l1_wounds`, `critique` in `problem_description`. Most L2 fires don't touch the layout.

## Dispatch hub — `DispatchHub`

Single ingress for every optimizer prompt. Three entry points, all stateless:

| Entry | Used by | Returns |
|-------|---------|---------|
| `render(name, bundle)` | internal | one injection's text |
| `fill_l1(template, layout, bundle)` | L1 generate | `PromptTemplate` with layout-driven content appended to slots |
| `fill_fixed(template, bundle)` | L1 critique, L2, L3 | `{var: text}` kwargs for `compile_prompt` |

`InjectionBundle` is the per-call frozen state: `(opt_sp, pipeline_schema, cycle_slice, digest)`. Built once via `build_bundle(cycle)`; consumed by the fill methods. `digest` is a `RoundDigest(diagnostics, critique)` — the post-scoring compression chain in one place; renderers read through it instead of off two parallel `latest_*` fields.

L1 generate uses `fill_l1`: walks `L1_LAYOUT_SLOTS`, calls `DispatchHub.render(name, bundle)` per injection, appends results to the slot's static text. L1 critique / L2 / L3 use `fill_fixed`: scans `{{name}}` placeholders in the template body and resolves each through the registry.

Both modes feed `compile_prompt(**hub_dict, **extras)`. Template-author scalars (`n_variants` for L1) are the *extras*; everything that depends on optimizer state is the hub's output.

## Validation — split HARD / SOFT

`validate_l1_layout(layout, prior_layout)` enforces:

* HARD — missing mandatory placeholder, name outside `L1_POSSIBLE`, duplicate within a slot. Caller rolls back to the prior layout; outcomes append to `opt_sp.wounds.l2_guard_breaches` for self-healing on the next L2 fire.
* SOFT — layout unchanged from prior. Apply with warning logged; flagged as `score=0.5` so L3 sees the churn signal next replan.

L2's parser (`escalation._parse_l2`) coerces `{slot: [name, ...]}` into `L1Layout`, validates, and only writes the new layout to OSP when HARD checks pass.

## Adding an injection

1. Implement `_r_<name>(b: InjectionBundle) -> str` in the matching `dispatch/hub/injections/` module. Return `""` when the bundle's source field is empty — empty injections are skipped by `fill_l1` so they don't waste tokens.
2. Register in `INJECTIONS`.
3. If L2 may pick it for L1's layout, add to `L1_POSSIBLE`. If it's part of L1's contract, add to `L1_MANDATORY` — the validator will then refuse layouts that drop it.
4. Reference `{{<name>}}` in any fixed template body that should resolve through `fill_fixed`.

Renderers stay layer-agnostic. Per-layer specialisation is the kind of complexity the hub exists to remove — if an injection needs to differ for L2 vs L3, that's two injections.

## File-line anchors

- `INJECTIONS`: `promptpotter/application/optimization/dispatch/hub/injections/registry.py`; `InjectionBundle`: `dispatch/hub/bundle.py`; `DispatchHub` + `build_bundle`: `dispatch/hub/facade.py`
- `L1Layout`, `L1_POSSIBLE`, `L1_MANDATORY`, `L1_LAYOUT_SLOTS`, `default_l1_layout`, `validate_l1_layout`: `promptpotter/domain/l1_layout.py`
- L1 generate compose path: `promptpotter/application/optimization/l1/generate.py::l1_generate`
- OSP layout field: `promptpotter/domain/opt_search_point.py` — `OptSearchPoint.memory.l1_layout` on the `L2L3Memory` sub-model

L2-side orchestration: [`l2-internals.md`](l2-internals.md).
