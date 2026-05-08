# L1 Layout + Dispatch Hub

L1's prompt is composed by walking a per-slot list of **signal names** and resolving each name through a single registry. L2 owns the layout; the registry is closed and code-derived. Concept role: [`the-loop.md § L2 in detail`](../concepts/the-loop.md).

## What "layout" means

```
┌─ L1's prompt composition ──────────────────────────────────┐
│  PromptTemplate (l1_generate)        per-slot static text  │
│      +                                                     │
│  L1Layout (on OptSearchPoint)        per-slot signal lists │
│      ↓                                                     │
│  DispatchHub.fill_l1                 resolves names via    │
│                                      SIGNALS registry      │
│      ↓                                                     │
│  RENDERED L1 PROMPT (what the LLM sees)                    │
└────────────────────────────────────────────────────────────┘
```

Every signal name in the layout maps to a renderer `(Bundle) → str` in `SIGNALS` (`dispatch_hub.py`). Renderers are layer-agnostic — the same `plan` renderer feeds L1, L2, and L3.

## Layout — `L1Layout`

`L1Layout` (`promptpotter/domain/l1_layout.py`) is a Pydantic model with one list per addressable slot:

| Slot | Mutable by L2 |
|------|---------------|
| `persona` | yes |
| `task_intent` | yes |
| `problem_description` | yes |
| `thinking_style` | yes |

`answer_format` is omitted on purpose — it carries L1's output JSON schema (a code contract), not L2's call. Static text in each slot stays; the layout's signal renderings are appended.

`L1_POSSIBLE` (subset of `SIGNALS`) is the menu L2 picks from. L2-internal signals (`l1_config`, `l1_signal_catalogue`, `l1gen_prompt_fields`, `l2_history`) are deliberately excluded as SIGNALs into L1's slots — `l1_config`'s contents reach L1 only via the `n_variants`/`creativity` caller extras (the in-prompt directive + the LLM-call temperature, respectively). `L1_MANDATORY` (`plan`, `task_context`, `rendered_prompt`, `tunable_params`, `critique`) must appear somewhere across the slots — without these L1 has no parent prompt, no plan, no task framing, no mutation surface, and no round-local failure digest. Dropping any of them fires `l1_layout_missing_mandatory` with `nurse_target='l3'` so L3 replans rather than letting L2 starve L1.

Default layout (`default_l1_layout`): `task_context` in `task_intent`; `rendered_prompt`, `tunable_params`, `plan`, `diagnostics`, `validation_failures`, `runtime_failures`, `critique` in `problem_description`. Most L2 fires don't touch the layout.

## Dispatch hub — `DispatchHub`

Single ingress for every optimizer prompt. Three entry points, all stateless:

| Entry | Used by | Returns |
|-------|---------|---------|
| `render(name, bundle)` | internal | one signal's text |
| `fill_l1(template, layout, bundle)` | L1 generate | `PromptTemplate` with layout-driven content appended to slots |
| `fill_fixed(template, bundle)` | L1 critique, L2, L3 | `{var: text}` kwargs for `compile_prompt` |

`Bundle` is the per-call frozen state: `(opt_sp, pipeline_schema, cycle_slice, digest)`. Built once via `build_bundle(cycle)`; consumed by the fill methods. `digest` is a `RoundDigest(diagnostics, critique)` — the post-scoring compression chain in one place; renderers read through it instead of off two parallel `latest_*` fields.

L1 generate uses `fill_l1`: walks `L1_LAYOUT_SLOTS`, calls `DispatchHub.render(name, bundle)` per signal, appends results to the slot's static text. L1 critique / L2 / L3 use `fill_fixed`: scans `{{name}}` placeholders in the template body and resolves each through the registry.

Both modes feed `compile_prompt(**hub_dict, **extras)`. Template-author scalars (`n_variants` for L1) are the *extras*; everything that depends on optimizer state is the hub's output.

## Validation — split HARD / SOFT

`validate_l1_layout(layout, prior_layout)` enforces:

* HARD — missing mandatory placeholder, name outside `L1_POSSIBLE`, duplicate within a slot. Caller rolls back to the prior layout; outcomes append to `opt_sp.l2_output_failures` for self-healing on the next L2 fire.
* SOFT — layout unchanged from prior. Apply with warning logged; flagged as `score=0.5` so L3 sees the churn signal next replan.

L2's parser (`escalation._parse_l2`) coerces `{slot: [name, ...]}` into `L1Layout`, validates, and only writes the new layout to OSP when HARD checks pass.

## Adding a signal

1. Implement `_r_<name>(b: Bundle) -> str` in `dispatch_hub.py`. Return `""` when the bundle's source field is empty — empty signals are skipped by `fill_l1` so they don't waste tokens.
2. Register in `SIGNALS`.
3. If L2 may pick it for L1's layout, add to `L1_POSSIBLE`. If it's part of L1's contract, add to `L1_MANDATORY` — the validator will then refuse layouts that drop it.
4. Reference `{{<name>}}` in any fixed template body that should resolve through `fill_fixed`.

Renderers stay layer-agnostic. Per-layer specialisation is the kind of complexity the hub exists to remove — if a signal needs to differ for L2 vs L3, that's two signals.

## File-line anchors

- `SIGNALS`, `Bundle`, `DispatchHub`, `build_bundle`: `promptpotter/application/optimization/dispatch_hub.py`
- `L1Layout`, `L1_POSSIBLE`, `L1_MANDATORY`, `L1_LAYOUT_SLOTS`, `default_l1_layout`, `validate_l1_layout`: `promptpotter/domain/l1_layout.py`
- L1 generate compose path: `promptpotter/application/optimization/l1.py::l1_generate`
- OSP layout field: `promptpotter/domain/opt_search_point.py` — `l1_layout` (in `MEMORY_FIELDS`)

L2-side orchestration: [`l2-internals.md`](l2-internals.md).
