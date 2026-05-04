# L2 Internals

L2 fires when L1 has stalled (per `l1_patience`), reads cycle state, and writes any subset of OSP fields to steer the next round. Concept role: [`../concepts/the-loop.md § L2 in detail`](../concepts/the-loop.md).

## Trigger

`Cycle.escalation` tracks per-layer counters. After every L1 round:

1. Improved best accuracy → escalation counters reset.
2. Otherwise `cycle.escalation.l1_stall_count++`. When it hits `l1_patience`, L2 fires.

Trigger gate logic + `record_decision("l2_escalation_trigger", ...)` in `cycle.escalate_l2`.

## Surface — `L2Surface`

`compile_l2_surface(cycle, *, round_num, candidate_scores, escalation_check_result, pipeline_params)` builds a frozen `L2Surface`. Sections carry their own trailing `\n\n` when non-empty.

| Field | Source | Purpose |
|-------|--------|---------|
| `current_params` | `json.dumps(opt_sp.optimizer_params)` | Previous tunes |
| `task_context_section` | `opt_sp.task_context` (filtered) | Domain understanding |
| `escalation_section` | `_section_escalation_section(ctx)` | Active escalation report |
| `warning_inventory` | `_section_warning_inventory(ctx)` | Per-query warnings (when no escalation report) |
| `l2_directive` | `_section_l2_directive(ctx)` | Previous round's directive |
| `validation_failures` | `_section_validation_failures(ctx)` | Loop 1 evidence |
| `runtime_failures` | `_section_runtime_failures(ctx)` | Loop 2 evidence |
| `axes_l2` | `_section_axes_l2(ctx)` | AxisIndex digest |
| `l1_generate_field_catalogue` | `_format_l1_generate_field_catalogue(...)` | L1 surface menu — capabilities can't disappear silently |

`to_compile_vars()` maps the dataclass into `{hole_name: text}` for `run_optimizer_node`.

## Output — flat dict

```json
{
  "action": "normal_round" | "probe_round",
  "directive": "...",
  "optimizer_params": {...},
  "task_context": {...},
  "scheme_overrides": {"<section>": false, ...},
  "text_overrides": {"<section>": "...", ...},
  "template_override": "...",
  "rationale": "..."
}
```

Every field optional. Missing/empty fields leave the corresponding OSP field unchanged.

`L2RefineStrategy.build_result` parses the dict and constructs a `TransitionResult`:

- `task_context`: refined `TaskDecomposition` if `raw["task_context"]` non-empty AND merging produces a real change.
- `l2_directive`: directive string (default `""`).
- `action`: `OptimizerAction.NORMAL_ROUND` (default) or `PROBE_ROUND`.
- `scheme_overrides` / `text_overrides`: filtered to `L1_GENERATE_SECTION_FIELDS` only — unknown names dropped with `logger.warning`.
- `template_override`: passthrough.
- `opt_search_point`: `mutate()`-derived child OSP with `optimizer_params` merged + `changes_description` set.
- `debug_prompt` / `debug_response`: trial-JSON archival.

## Side effects — `L2RefineStrategy.apply_side_effects`

```python
if result.task_context:
    cycle.opt_sp.task_context = result.task_context
cycle.opt_sp.l2_directive = result.l2_directive
if result.scheme_overrides:
    cycle.opt_sp.l1_section_overrides |= result.scheme_overrides
if result.text_overrides:
    cycle.opt_sp.l1_section_overrides_text |= result.text_overrides
if result.template_override:
    cycle.opt_sp.l1_template_override = result.template_override
cycle.escalation.record_l2_fired(best_accuracy=..., best_composite=...)

is_probe = result.action is OptimizerAction.PROBE_ROUND
record_decision("probe_round_commitment", ..., outcome=is_probe, ...)
if is_probe:
    cycle.probe_next_round = True
```

The OSP is mutable Pydantic; writes happen in place. Next round's L1 reads from the same OSP.

The single decision recorded per L2 fire is `probe_round_commitment` — outcome `True` if `probe_round`, else `False`. Surface mutations and directives are not recorded as separate decision kinds — `opt_search_point` in the trial JSON archives them.

## File-line anchors

- L2 trigger gate: `promptpotter/application/optimization/cycle.py::escalate_l2`
- `compile_l2_surface`, `L2Surface`, `OptimizerAction`, `TransitionResult`, `L2RefineStrategy`: `promptpotter/application/optimization/pipeline.py`
- L2 prompt template: `optimizer_pipeline.json::resolved_prompts['l2_context/1']`
- OSP mutation surface: `promptpotter/domain/opt_search_point.py` — `l2_directive`, `optimizer_params`, `task_context`, `l1_section_overrides`, `l1_section_overrides_text`, `l1_template_override`

Cross-references: [`l1-generate-surface.md`](l1-generate-surface.md) (what L2 mutates on L1's side); [`self-healing-internals.md`](self-healing-internals.md) (L2 nurses Loops 1 + 2; produces Loop 4).
