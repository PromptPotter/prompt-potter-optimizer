# L1-Generate Surface

The closed catalogue of every text block injectable into L1's meta-prompt. L2 controls which sections are visible and what their text says, never the catalogue itself. Concept role: [`the-loop.md § L2 in detail`](../concepts/the-loop.md).

## What "surface" means

```
┌─ L1's prompt surface ──────────────────────────────────────┐
│  CATALOGUE (closed, in code)         8 sections + 4 scalars│
│      ↓                                                     │
│  PER-ROUND OVERRIDES (set by L2, on the OSP)               │
│   • visibility toggles  — "hide section X this round"      │
│   • text overrides      — "replace section X's text"       │
│   • whole-body override — "use this template body"         │
│      ↓                                                     │
│  RENDERED L1 PROMPT (what the LLM sees)                    │
└────────────────────────────────────────────────────────────┘
```

L2 sees a **catalogue block** in its own prompt — one line per registry entry with current state — so L2 always knows what L1 is receiving. No hidden state.

## Registry — `L1GenerateField`

Closed `enum.StrEnum` in `promptpotter/application/optimization/pipeline.py`. Two subsets:

| Kind | Members | L2-mutable | Source |
|------|---------|------------|--------|
| Section | `pipeline_schema_text`, `failure_analysis`, `axes_l1`, `task_context`, `escalation_probe`, `escalation_alert`, `l2_directive`, `plan` | yes | each has a `_section_*` renderer |
| Scalar | `n_variants`, `accuracy_pct`, `n_queries`, `rendered_prompt` | no — factual | computed in `compile_l1_surface` |

The section-only subset is exposed as `L1_GENERATE_SECTION_FIELDS`. L2's output parser drops override keys not in this subset (with a warning log) — typos don't propagate.

**Adding a section:**
1. Add an enum member to `L1GenerateField`.
2. Implement a `_section_<name>(state: DispatchState) -> str` renderer.
3. Wire `<name>` into `_L1_GENERATE_SECTION_RENDERERS` and `_L1_GENERATE_FIELD_DESCRIPTIONS`.
4. Append `<name>: str = ""` to `L1GenerateSurface`.
5. Add `<name>` to `L1_GENERATE_SECTION_FIELDS` and the `to_compile_vars` mapping.
6. Add `{{<name>}}` to `optimizer_pipeline.json::resolved_prompts['l1_generate/1'].problem_description` body.

**Removing a section:** delete the enum member. The deletion is the deliberate code change — L2 cannot drop a section by emitting an override, only gate it off via `scheme_overrides[name] = False`.

## Compile path

`compile_l1_surface(cycle, *, round_num, n_variants)` walks `L1_GENERATE_SECTION_FIELDS` and applies overrides in this order:

1. `cycle.opt_sp.l1_section_overrides.get(name) is False` → empty string.
2. `name in cycle.opt_sp.l1_section_overrides_text` → use that text verbatim.
3. Otherwise → call the registered `_section_*` renderer.

After section assembly, scalars (`n_variants`, `accuracy_pct`, `n_queries`, `rendered_prompt`) are populated from `cycle.current_accuracy`, `cycle.current_results`, `cycle.opt_sp.render()`, and the `n_variants` argument.

`L1GenerateSurface.to_compile_vars()` returns a `dict[str, str]` mapping each `L1GenerateField.value` to its rendered string — fed directly to `run_optimizer_node(compile_vars=...)`.

## Whole-body override — `OptSearchPoint.l1_template_override`

When L2 sets `template_override`, the new body lands on `cycle.opt_sp.l1_template_override`. `l1.l1_generate()` handles it:

```python
template = load_optimizer_prompt("l1_generate")
if cycle.opt_sp.l1_template_override:
    template = template.model_copy(
        update={"problem_description": cycle.opt_sp.l1_template_override}
    )
```

Authors of `template_override` must include `{{l2_directive}}` in the body so future directives flow through. No parser-level enforcement — contract documented for L2's prompt and operator review.

## File-line anchors

- `L1GenerateField` + descriptions + section renderer map: `promptpotter/application/optimization/pipeline.py`
- `L1GenerateSurface` dataclass + `compile_l1_surface`: same file
- `L2RefineStrategy.build_result` (validates override keys): same file
- L1 entry point: `promptpotter/application/optimization/l1.py::l1_generate`
- OSP override fields: `promptpotter/domain/opt_search_point.py` — `l1_section_overrides`, `l1_section_overrides_text`, `l1_template_override`

L2-side orchestration: [`l2-internals.md`](l2-internals.md).
