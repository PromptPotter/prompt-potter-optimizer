# L2 Internals

L2 fires when L1 has stalled (per `l1_patience`), reads cycle state, and writes any subset of OSP fields to steer the next round. Concept role: [`../concepts/the-loop.md § L2 in detail`](../concepts/the-loop.md).

L2 is one entry in the unified dispatch hub — same `LayerStrategy` shape as L3, same `fill` path (from its `NODE_LAYOUTS["l2_context"].floor`), same `Bundle` per-call state. The hub is what stops L2 from accumulating its own renderers, its own surface object, its own escape hatches. See [`dispatch-hub.md`](dispatch-hub.md) for the registry it shares.

## Trigger

`Cycle.escalation` tracks per-layer counters. After every L1 round:

1. Improved best fitness → escalation counters reset.
2. Otherwise `l1_stall_count++`. When it hits `l1_patience`, L2 fires.

Three preemptors fire L2 *before* patience (rules in `escalation/rules.py`): `l1_mandatory_breach` (a dropped mandatory placeholder), `l2_axis_yield_drought` (no axis yields above noise), and `l1_evidence_starved` (a node failed across ~all of a round's samples — `evidence_starved_node` ≥ `EVIDENCE_STARVED_RATE`). The last is the self-heal-vs-HITL fork: a starved round is routed to L2 not to chase it, but so L2 can read the `evidence_health` panel and either refine or **terminate** (see Outputs → `terminate_proposal`). Deterministic rules only route; they never diagnose or stop (R-48).

Trigger gate: `escalation.escalate_l2`; the decision is recorded as `ResumeCheckpointKind.L2_ESCALATION_TRIGGER`, gated **ARCHIVAL** — the trigger is a fold over the cycle's escalation history (counters bump once per escalation *request* and reset on each fire), not a function of one round's measurements, which is what a replayer is pure over. On resume the counters are rebuilt by `EscalationFSM.from_ledger`, not re-derived; the trigger's scorer-dependence rides `improved`, hence the round measurements, whose own decisions are `REPLAYED`.

## Inputs — via the hub

L2's injection set (`plan`, `l3_to_l2_note`, `rendered_prompt`, `diagnostics`, `evidence_health`, `guard_breaches`, `axis_memory`, `archive_top_runs`, `rare_hit_samples`, `critique`, `l1_overrides`, `task_context`, `l1_signal_catalogue`) lives in `NODE_LAYOUTS["l2_context"].floor` (`domain/l1_layout.py`), not as `{{tokens}}` in the template — its `l2_context/1` `problem_description` body is now empty. `DispatchHub.fill(template, floor, bundle)` fills them in one pass. No L2-only surface object exists — L2 is just one consumer of the global `INJECTIONS` registry. L2 does not see `l2_guard_breaches` / `l3_guard_breaches` — when those appear, Wound 4 fires L3 immediately, so by L2's next fire L3 has already replanned and L2 reads the new `plan`.

One injection is L2-only: `l1_signal_catalogue` — the menu of names L2 may put in `l1_layout`. Absent from `L1_POSSIBLE` so L2 cannot accidentally inject its own catalogue into L1.

## Outputs

```json
{
  "task_context": {"domain": "...", "key_challenges": "...", ...},
  "action": "normal_round" | "probe_round",
  "axis_targeted": "...",
  "l1_layout": {"persona": [...], "task_intent": [...], ...},
  "l1_overrides": {...},
  "rationale": "...",
  "fork_proposal": null,
  "terminate_proposal": {"reason": "..."} | null
}
```

All fields are optional. Missing fields leave the corresponding OSP state untouched. The primary lever is `task_context` — broadcast to L1, L1_CRITIQUE, L2, L3 next round. `terminate_proposal` is the HITL exit: on evidence-starvation L2 emits it with an operator-actionable reason (the dead node + what to fix) and the cycle halts (`StopReason.ABORT`); the operator fixes the backend and resumes. Both control outputs are gated by their `OptimizationConfig` capability bit — see [`../../promptpotter/application/optimization/CLAUDE.md` § L2/L3 layer-control channel](../../promptpotter/application/optimization/CLAUDE.md).

`_parse_l2` (`escalation/firing.py`) constructs a `TransitionResult`:

- `task_context`: dict of refined framing fields, merged onto `opt_sp.task_context` via `TaskDecomposition.merge`. A non-empty proposal that lands no semantic delta (no-op merge, or a ≥0.5-Jaccard paraphrase) is flagged as `l2_task_context_stale_repeat` — a soft-reject: the prior framing is kept, and a sole breach skips the L3 force-trigger.
- `action`: `"normal_round"` (default) or `"probe_round"`. Probe re-runs only the warned-query subset under the same OSP next round.
- `axis_targeted`: the axis this fire tests. Required prose when `action="probe_round"`; otherwise stamped on the cycle for the next probe-outcome render.
- `l1_layout`: coerced to `L1Layout`, validated against `validate_l1_layout(prior=opt_sp.l1_layout)`. HARD-failed layouts roll back to the prior; SOFT-flagged outcomes (e.g. unchanged from prior) ride along on `opt_sp.wounds.l2_guard_breaches`.
- `l1_overrides`: merged onto a `mutate()`-derived child OSP. Two known knobs today: `n_variants` (in-prompt directive to L1 via `{{n_variants}}` caller extra) and `creativity` (L1 LLM-call temperature, out-of-prompt).

## How L2 steers L1

Two channels, both via OSP fields the dispatch hub reads:

| Channel | OSP field | L1 effect |
|---------|-----------|-----------|
| Framing | `task_context` | The `task_context` injection renders the structured framing dict. Default layout puts it in `task_intent` — front of mind for the LLM. Persistent across L2 fires; merges accumulate. |
| Layout | `l1_layout` | `DispatchHub.fill` walks the layout and appends each named injection's rendering to its slot. Mutating the layout reshapes which injections L1 sees and where. |

L2 cannot edit L1's static template text and cannot toggle `answer_format` — those are code contracts. Anything L2 wants L1 to see must already be a registered injection (from `L1_POSSIBLE`).

## Side effects — `_apply_l2`

```python
if result.task_context:
    osp.task_context = result.task_context
if result.l1_layout is not None:
    osp.l1_layout = result.l1_layout
osp.l2_guard_breaches = list(result.l2_guard_breaches)
cycle.escalation.record_l2_fired(...)
if result.axis_targeted:
    cycle.last_l2_axis = result.axis_targeted
record_decision(ResumeCheckpointKind.PROBE_ROUND_COMMITMENT, ...)
if action == "probe_round":
    cycle.probe_next_round = True
```

The OSP is mutable Pydantic; writes happen in place. `l1_layout` lives on `OptSearchPoint.memory` (an `L2L3Memory` bundle), so L3-spawned children inherit in-flight L2 edits via `copy_memory_to`. `task_context` is on the same `memory` bundle and is forwarded by `mutate()` to L1 children (along with `l1_overrides`); the other two memory fields (`wounds`, `l1_layout`) reset to defaults in `mutate()` and only flow on L2/L3 adopt.

The single decision recorded per L2 fire is `PROBE_ROUND_COMMITMENT` — outcome `True` if probe, else `False`. Layout / framing-refinement content are not separate decisions; they ride on the round file.

## Wound 4 — L2 self-healing via L3

`run_l2_output_validators` (`validators/l2_output.py`) runs `L2_TASK_CONTEXT_STALE_REPEAT` against the proposed/applied task_context pair. Layout HARD failures from `validate_l1_layout` are appended to the same `l2_guard_breaches` list. When the list holds any non-soft-reject breach after `_apply_l2`, the escalation driver force-triggers L3 to heal — but a list that is *all* soft-reject (`_L2_SOFT_REJECT_VALIDATOR_IDS`: stale-repeat / dangling-trigger — self-correcting, already discarded) skips the force-trigger. L2's own thrashing is observable to L3 via the `l2_guard_breaches` injection on its next fire.

## File-line anchors

- L2 trigger gate: `promptpotter/application/optimization/escalation/firing.py::escalate_l2`
- `_parse_l2`, `_apply_l2`, `escalate_l2`: `escalation/firing.py` (trigger gates in `escalation/decide.py`)
- `TransitionResult`: `promptpotter/application/optimization/escalation/firing.py`
- L2 prompt template: `datasets/_optimizer/pipeline.json::resolved_prompts['l2_context/1']`
- OSP mutation surface: `promptpotter/domain/opt_search_point.py` — `task_context`, `l1_layout`, `l1_overrides`, `l2_guard_breaches`
- Layout validators: `promptpotter/domain/l1_layout.py::validate_l1_layout`
- Task-context stale-repeat: `promptpotter/application/optimization/validators/l2_output.py::L2_TASK_CONTEXT_STALE_REPEAT`

Cross-references: [`dispatch-hub.md`](dispatch-hub.md) (the layout L2 mutates + the dispatch hub both layers share); [`self-healing-internals.md`](self-healing-internals.md) (L2 is the nurse for Wounds 1 + 2; produces Wound 4).
