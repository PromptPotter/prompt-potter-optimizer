# L2 Internals

L2 fires when L1 has stalled (per `l1_patience`), reads cycle state, and writes any subset of OSP fields to steer the next round. Concept role: [`../concepts/the-loop.md`](../concepts/the-loop.md).

L2 is one entry in the unified dispatch hub — same `LayerStrategy` shape as L3, same `fill` path (from its `NODE_LAYOUTS["l2_context"].floor`), same `Bundle` per-call state. The hub is what stops L2 from accumulating its own renderers, its own surface object, its own escape hatches. See [`dispatch-hub.md`](dispatch-hub.md) for the registry it shares.

## Trigger

`Cycle.escalation` tracks per-layer counters. After every L1 round:

1. Improved best fitness → escalation counters reset.
2. Otherwise `l1_stall_count++`. When it hits `l1_patience`, L2 fires.

Three preemptors fire L2 *before* patience (rules in `escalation/rules.py`): `l1_mandatory_breach` (a dropped mandatory placeholder), `l2_axis_yield_drought` (no axis yields above noise), and `l1_evidence_starved` (a node failed across ~all of a round's samples — `evidence_starved_node` ≥ `EVIDENCE_STARVED_RATE`). The last is the self-heal-vs-HITL fork: a starved round is routed to L2 not to chase it, but so L2 can read the `evidence_health` panel and either refine or **terminate** (see Outputs → `terminate_proposal`). Deterministic rules only route; they never diagnose or stop — termination authority belongs to the most-general reader, and a backend-coupled deterministic check only WARNS.

Trigger gate: `escalation.escalate_l2`; the decision is recorded as `ResumeCheckpointKind.L2_ESCALATION_TRIGGER`, gated **ARCHIVAL** — the trigger is a fold over the cycle's escalation history (counters bump once per escalation *request* and reset on each fire), not a function of one round's measurements, which is what a replayer is pure over. On resume the counters are rebuilt by `EscalationFSM.from_ledger`, not re-derived; the trigger's scorer-dependence rides `improved`, hence the round measurements, whose own decisions are `REPLAYED`.

## Inputs — via the hub

L2's injection set **is** `NODE_LAYOUTS["l2_context"].floor` (`domain/l1_layout.py`) — read the membership there, never from a copy on this page, because the copy is what went stale when the capability directives were wired in. It lives in that layout rather than as `{{tokens}}` in the template — its `l2_context/1` `problem_description` body is now empty. `DispatchHub.fill(template, floor, bundle)` fills them in one pass. No L2-only surface object exists — L2 is just one consumer of the global `INJECTIONS` registry. L2 does not see `l2_guard_breaches` / `l3_guard_breaches` — when those appear, Wound 4 fires L3 immediately, so by L2's next fire L3 has already replanned and L2 reads the new `plan`.

One injection is L2-only: `l1_signal_catalogue` — the cross-slot mandatory rule, which `l1_layout`'s schema cannot express. The vocabulary itself (legal slots, signal enum) is on that schema, not here: while it was prose-only, L2 answered the gap by inventing a shape and the edit rolled back. Absent from `L1_POSSIBLE` so L2 cannot accidentally inject its own catalogue into L1.

## Outputs

```json
{
  "axis_targeted": "...",
  "l1_layout": {"persona": [...], "task_intent": [...], ...},
  "l1_overrides": {...},
  "rationale": "...",
  "fork_proposal": null,
  "terminate_proposal": {"reason": "..."} | null
}
```

All fields are optional. Missing fields leave the corresponding OSP state untouched. The levers are `l1_layout` (what L1 looks at) and `l1_overrides` (how hard it explores) — there are only two, and an L2 fire touching neither is a wasted escalation, scored as one by `l2_targets_l1_surface`. `terminate_proposal` is the HITL exit: on evidence-starvation L2 emits it with an operator-actionable reason (the dead node + what to fix) and the cycle halts (`StopReason.ABORT`); the operator fixes the backend and resumes. Both control outputs are gated by their `OptimizationConfig` capability bit — see [`../../promptpotter/application/optimization/CLAUDE.md` § L2/L3 layer-control channel](../../promptpotter/application/optimization/CLAUDE.md).

**Two fields this schema deliberately does not have.** `task_context` — the operator's framing is frozen for the run; L2 steers what L1 *looks at*, never rewrites what the operator wrote about the task. `action` (`normal_round` / `probe_round`) — probe rounds are not wired; the lever was removed rather than guarded because it selected samples by a warned-query set that is empty on every healthy run, so choosing it measured nothing. Both are stated on `L2ContextOutput` itself (`dispatch/schemas.py`), with the full history in [`../../promptpotter/application/optimization/CLAUDE.md`](../../promptpotter/application/optimization/CLAUDE.md) § *The framing is frozen* and § *Probe rounds*.

`_parse_l2` (`escalation/firing.py`) constructs a `TransitionResult`:

- `axis_targeted`: prose naming the axis this fire routed the failure cluster to — its evidence anchor, read by `l2_evidence_anchored`. Deliberately **not** a steering surface: L1 reads its axes from `axis_memory`, which is derived from measurement.
- `l1_layout`: coerced to `L1Layout`, validated against `validate_l1_layout(prior=opt_sp.memory.l1_layout)`. HARD-failed layouts roll back to the prior; SOFT-flagged outcomes (e.g. unchanged from prior) ride along on `opt_sp.memory.wounds.l2_guard_breaches`.
- `l1_overrides`: merged onto a `mutate()`-derived child OSP. Two known knobs today: `n_variants` (in-prompt directive to L1 via `{{n_variants}}` caller extra) and `creativity` (L1 LLM-call temperature, out-of-prompt).

## How L2 steers L1

Two channels, both via OSP fields the dispatch hub reads:

| Channel | OSP field | L1 effect |
|---------|-----------|-----------|
| Attention | `memory.l1_layout` | `DispatchHub.fill` walks the layout and appends each named injection's rendering to its slot. Mutating the layout reshapes which injections L1 sees and where. |
| Exploration | `memory.l1_overrides` | Optimizer params for L1's next call — `n_variants` (in-prompt) and `creativity` (call temperature). |

`task_context` is **not** a channel: it is operator-authored framing that L2 reads as evidence and cannot write.

L2 cannot edit L1's static template text and cannot toggle `answer_format` — those are code contracts. Anything L2 wants L1 to see must already be a registered injection (from `L1_POSSIBLE`).

## Side effects — `_apply_l2`

```python
if result.l1_layout is not None:
    osp.memory.l1_layout = result.l1_layout
osp.memory.wounds.l2_guard_breaches = list(result.l2_guard_breaches)
cycle.escalation.record_l2_fired(...)
```

That is the whole of `_apply_l2`. The OSP is mutable Pydantic; writes happen in place. `l1_layout` lives on `OptSearchPoint.memory` (an `L2L3Memory` bundle), so L3-spawned children inherit in-flight L2 edits via `copy_memory_to`. `task_context` is on the same `memory` bundle and is forwarded by `mutate()` to L1 children (along with `l1_overrides`); the other two memory fields (`wounds`, `l1_layout`) reset to defaults in `mutate()` and instead carry forward when a child is **adopted** as the cycle's incumbent — the one `Cycle.adopt` seam (an L1 win and an L2/L3 transition alike) runs `copy_memory_to` from the outgoing incumbent, then overlays only the surface the adoption owns.

**No decision is recorded per L2 fire.** There was one — `PROBE_ROUND_COMMITMENT`, outcome `True` if probe — and it left with the probe lever; `ResumeCheckpointKind` no longer declares it. The L2 fire itself is on the ledger as `L2_ESCALATION_TRIGGER`; layout and exploration content are not separate decisions and ride on the round file.

## Wound 4 — L2 self-healing via L3

`l2_guard_breaches` holds L2's HARD layout breaches — `validate_l1_layout`'s three (mandatory placeholder missing, unknown name, duplicate within a slot) plus `l1_layout_unparseable`, which `_parse_l2` emits when a non-empty edit coerces to no slot at all and the validator therefore never runs — and **any** breach after `_apply_l2` force-triggers L3 to heal. L2's own thrashing is observable to L3 via the `l2_guard_breaches` injection on its next fire.

**Every breach is hard — there is no soft-reject tier, and no `task_context` validator.** `task_context` framing is frozen for the run (`TaskDecomposition.merge` refuses a rewrite), so a stale-repeat breach is not representable and there is nothing inert to except: `escalation/firing.py` is an unconditional `if breaches:`. Do not add a tier to re-admit one.

## File-line anchors

- L2 trigger gate: `promptpotter/application/optimization/escalation/firing.py::escalate_l2`
- `_parse_l2`, `_apply_l2`, `escalate_l2`: `escalation/firing.py` (trigger gates in `escalation/decide.py`)
- `TransitionResult`: `promptpotter/application/optimization/escalation/firing.py`
- L2 prompt template: `promptpotter/assets/optimizer/pipeline.yaml::resolved_prompts['l2_context/1']`
- OSP mutation surface: `promptpotter/domain/opt_search_point.py` — `task_context`, `l1_layout`, `l1_overrides`, `l2_guard_breaches`
- Layout validators: `promptpotter/domain/l1_layout.py::validate_l1_layout`

Cross-references: [`dispatch-hub.md`](dispatch-hub.md) (the layout L2 mutates + the dispatch hub both layers share); [`self-healing-internals.md`](self-healing-internals.md) (L2 produces wounds — its guard breaches — and heals none).
