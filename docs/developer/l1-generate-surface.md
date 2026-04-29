# L1-Generate Surface — Internals

`L1GenerateField` is the closed registry of every variable injectable into L1's meta-prompt. `L1GenerateSurface` is the typed payload built per call. `compile_l1_surface()` walks the registry, applies `OptSearchPoint` overrides, and returns the surface.

Conceptual overview in [`../concepts/l1-generate-surface.md`](../concepts/l1-generate-surface.md).

---

## Registry — `L1GenerateField`

Closed `enum.StrEnum` in [`promptpotter/application/optimization/pipeline.py`](../../promptpotter/application/optimization/pipeline.py). Two subsets:

| Kind | Members | L2-mutable | Source |
|------|---------|------------|--------|
| Section | `pipeline_schema_text`, `failure_analysis`, `axes_l1`, `task_context`, `escalation_probe`, `escalation_alert`, `l2_directive`, `plan` | yes | each has a `_section_*` renderer |
| Scalar | `n_variants`, `accuracy_pct`, `n_queries`, `rendered_prompt` | no — factual | computed in `compile_l1_surface` |

The section-only subset is exposed as `L1_GENERATE_SECTION_FIELDS`. L2's output parser silently drops override keys that are not in this subset (with a warning log) — section-name typos in L2's output do not propagate.

**Adding a section:**
1. Add an enum member to `L1GenerateField`.
2. Implement a `_section_<name>(ctx: LayerContext) -> str` renderer.
3. Wire `<name>` into `_L1_GENERATE_SECTION_RENDERERS` and `_L1_GENERATE_FIELD_DESCRIPTIONS`.
4. Append `<name>: str = ""` to `L1GenerateSurface`.
5. Add `<name>` to `L1_GENERATE_SECTION_FIELDS` and the `to_compile_vars` mapping.
6. Add `{{<name>}}` to `prompts/l1_generate.json`'s `problem_description` body.

**Removing a section:** delete the enum member. The deletion is the deliberate code change. L2 cannot drop a section by emitting an override — it can only gate one off via `scheme_overrides[name] = False`.

---

## Surface dataclass — `L1GenerateSurface`

Frozen `@dataclass` with one `str` field per registry member. Section fields carry their own trailing `\n\n` separator when non-empty so the template body stays inert when sections gate off.

`L1GenerateSurface.to_compile_vars()` returns a `dict[str, str]` mapping each `L1GenerateField.value` to its rendered string — fed directly to `run_optimizer_node(compile_vars=...)`.

---

## Compile path — `compile_l1_surface(cycle, *, round_num, n_variants)`

Walks `L1_GENERATE_SECTION_FIELDS` and applies overrides in this order:

1. `cycle.opt_sp.l1_section_overrides.get(name) is False` → empty string.
2. `name in cycle.opt_sp.l1_section_overrides_text` → use that text verbatim.
3. Otherwise → call the registered `_section_*` renderer.

After section assembly, scalars (`n_variants`, `accuracy_pct`, `n_queries`, `rendered_prompt`) are populated from `cycle.current_accuracy`, `cycle.current_results`, `cycle.opt_sp.render()`, and the `n_variants` argument.

---

## Whole-body override — `OptSearchPoint.l1_template_override`

When L2 sets `template_override` in its output, the new template body lands on `cycle.opt_sp.l1_template_override`. `l1.l1_generate()` handles this one rung up the call stack:

```python
template = load_optimizer_prompt("l1_generate")
if cycle.opt_sp.l1_template_override:
    template = template.model_copy(
        update={"problem_description": cycle.opt_sp.l1_template_override}
    )
generated, prompt = await run_optimizer_node(
    template_name="l1_generate",
    compile_vars=surface.to_compile_vars(),
    template=template,
    ...
)
```

`run_optimizer_node` accepts a `template: PromptTemplate | None = None` override; when provided, it bypasses `load_optimizer_prompt`. The trace metadata still records `template_name="l1_generate"` for observability continuity.

Authors of `template_override` must include `{{l2_directive}}` in the body so future directives still flow through. There is no parser-level enforcement — this is a contract documented for L2's prompt and operator review of trial JSONs.

---

## Surface-aware logging

`l1_generate()` reports section sizes after compile to verify the surface was built correctly:

```
L1 R3 meta-prompt: 4823 chars | rendered_prompt=1823 | failure_analysis=412 | axes_l1=287 | ...
```

Empty sections are skipped in the report. A surprise empty section often means an override is gating it off.

---

## File-line anchors

- `L1GenerateField` + descriptions + section renderer map: `promptpotter/application/optimization/pipeline.py`
- `L1GenerateSurface` dataclass: same file
- `compile_l1_surface`: same file
- `L2RefineStrategy.build_result` (validates override keys): same file
- L1 entry point that consumes the surface: `promptpotter/application/optimization/l1.py::l1_generate`
- OSP override fields: `promptpotter/domain/opt_search_point.py` — `l1_section_overrides`, `l1_section_overrides_text`, `l1_template_override`

For the L2-side orchestration, see [l2-internals.md](l2-internals.md).
