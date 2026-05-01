# L2 Internals

L2 is the optimizer's strategist. It fires when L1 has stalled (per `l1_patience`), reads cycle state, and writes any subset of OSP fields to steer the next round.

This page is the canonical L2 implementation reference. Conceptual overview in [`../concepts/what-is-l2.md`](../concepts/what-is-l2.md).

---

## When L2 fires

`Cycle` tracks per-layer escalation counters in `EscalationState`. After every L1 round:

1. If the round improved best accuracy, escalation counters reset.
2. Otherwise `cycle.escalation.l1_stall_count` increments. When it hits `l1_patience`, L2 fires.

Trigger gate logic and `record_decision("l2_escalation_trigger", ...)` live in `cycle.escalate_l2`.

L2 is **not every-round**. On healthy tasks where L1 keeps improving, L2 may stay dormant for a whole campaign. The flat-dict output channel and OSP mutations are designed for the cases where L2 does fire — most of them productive on stuck tasks.

---

## What L2 sees — `L2Surface`

`compile_l2_surface(cycle, *, round_num, candidate_scores, escalation_check_result, pipeline_params)` builds a frozen `L2Surface` dataclass. Fields:

| Field | Source | Purpose |
|-------|--------|---------|
| `current_params` | `json.dumps(opt_sp.optimizer_params)` | What L2's previous tunes look like. |
| `task_context_section` | `opt_sp.task_context` (filtered) | Structured domain understanding. |
| `escalation_section` | `_section_escalation_section(ctx)` | Active escalation report. |
| `warning_inventory` | `_section_warning_inventory(ctx)` | Per-query warning inventory (when no escalation report). |
| `l2_directive` | `_section_l2_directive(ctx)` | Previous round's directive (sliding window). |
| `validation_failures` | `_section_validation_failures(ctx)` | Loop 1 evidence — L1 schema-compliance failures. |
| `runtime_failures` | `_section_runtime_failures(ctx)` | Loop 2 evidence — runtime degradation patterns. |
| `axes_l2` | `_section_axes_l2(ctx)` | AxisIndex digest for L2 (axis rankings, bottlenecks, persistent failures, volatile queries). |
| `l1_generate_field_catalogue` | `_format_l1_generate_field_catalogue(...)` | Code-derived menu of L1's surface — capabilities cannot be silently lost. |

Section strings carry their own trailing `\n\n` when non-empty; the template body stays inert when sections are empty.

`to_compile_vars()` maps the dataclass into `{hole_name: text}` for `run_optimizer_node`.

---

## L2 output — flat dict

L2's LLM emits a JSON object with these optional fields:

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

Every field is independent and optional. Fields not present (or empty) leave the corresponding OSP field unchanged.

`L2RefineStrategy.build_result` parses the dict and constructs a `TransitionResult` carrying:

- `task_context`: a refined `TaskDecomposition` if `raw["task_context"]` was non-empty AND merging produced a real change.
- `l2_directive`: the directive string (defaults to `""`).
- `action`: `OptimizerAction.NORMAL_ROUND` (default) or `OptimizerAction.PROBE_ROUND`.
- `scheme_overrides` / `text_overrides`: filtered to `L1_GENERATE_SECTION_FIELDS` only — unknown section names are dropped with a `logger.warning`.
- `template_override`: passed through verbatim.
- `opt_search_point`: a `mutate()`-derived child OSP with `optimizer_params` merged and `changes_description` set to `f"L2: {rationale[:80]}"`.
- `debug_prompt` / `debug_response`: full prompt + raw LLM output for trial-JSON archival.

---

## How L2 writes — `L2RefineStrategy.apply_side_effects`

Linear, no match-statement — every field merges if present:

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
cycle.escalation.l2.record_entry(...)

is_probe = result.action is OptimizerAction.PROBE_ROUND
record_decision("probe_round_commitment", ..., outcome=is_probe, ...)
if is_probe:
    cycle.probe_next_round = True
```

The OSP is mutable Pydantic; the writes happen in place. The next round's L1 reads from the same OSP.

The single decision recorded per L2 fire is `probe_round_commitment` — outcome `True` if L2 picked `probe_round`, else `False`. Surface mutations and directives are not recorded as separate decision kinds because they are pure OSP state — `opt_search_point` in the trial JSON already archives them.

---

## What L2 does NOT do

L2 does not mutate pipeline params (that's L3). L2 does not score candidates (that's L1). L2 does not write to disk directly — the OSP mutation is checkpointed at `cycle.checkpoint(rr, round_num)` along with the rest of the round.

---

## File-line anchors

- L2 trigger gate: `promptpotter/application/optimization/cycle.py::escalate_l2`
- `compile_l2_surface`: `promptpotter/application/optimization/pipeline.py`
- `L2Surface` dataclass: same file
- `OptimizerAction` enum: same file
- `TransitionResult` dataclass: same file
- `L2RefineStrategy` class: same file (`build_compile_vars`, `build_result`, `apply_side_effects`, `enter_payload`, `exit_payload`)
- L2 prompt template: `optimizer_pipeline.json::resolved_prompts['l2_context/1']` (referenced from `nodes.l2_context.config.prompt_family`/`prompt_version`)
- OSP mutation surface: `promptpotter/domain/opt_search_point.py` — `l2_directive`, `optimizer_params`, `task_context`, `l1_section_overrides`, `l1_section_overrides_text`, `l1_template_override`

For the structural side, see:
- [l1-generate-surface.md](l1-generate-surface.md) — what L2 mutates on L1's side.
- [self-healing-internals.md](self-healing-internals.md) — four LLM-to-LLM healing loops; L2 is the nurse for Loops 1 and 2 (validation + runtime), and the producer for Loop 4 (its own output validators).
