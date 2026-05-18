# Human in the loop

PromptPotter optimizes by escalating: L1 generates candidates every
round; L2 fires on L1 stall to refine task framing; L3 fires on L2 stall
to replan strategy. **HITL is not a separate I/O kind** — it collapses
into the existing fork primitive. The operator picks a ledger offset in
the webapp's lineage inspector, hits "Fork from here," and the optimizer
mints a sibling cycle rooted at that offset via
`CycleEventLog.inherit_from(parent, offset)`. The fork inherits the
parent's typed state at the cut; the operator re-runs `resume` against
the new fork id.

## Status (pre-M12)

The **endorse-and-fork** path lands with the dashboard control plane:

1. Open the webapp's View Results tab.
2. Click any node in the lineage tree → scoring inspector opens for that
   candidate.
3. Enable Edit mode → "Fork from here" appears.
4. Confirm → backend mints a fork cycle dir under
   `.promptpotter/projects/{tenant}/campaigns/{root}/forks/{fork_id}/`
   and returns the CLI command to run it.
5. Run the copied command in your terminal; the new cycle picks up from
   the endorsed candidate as origin and L1 generates fresh from there.

The **substitute-typed-edit** path (operator writes their own list of
`OptSearchPoint` candidates that ride the next L1-generate slot, going
straight to L1 score) is M12 work — it needs the form UI and a typed
override carried by `inherit_from(parent, offset, override=…)`. See
[`docs/specs/m11-publication-benchmarks.md`](../specs/m11-publication-benchmarks.md)
for the M11 sequencing, M12 spec for the form path.

## Stop run

The webapp's "Stop run" button writes
`{cycle_dir}/.runtime/stop.flag`. The running loop's `Session.stop_check`
polls the flag at the start of each round and exits cleanly when it
appears. The stop is recorded as a `PhaseRecord` in the ledger with
`stop_reason="hitl_stop"`.

## Why this shape

Every ledger record is already typed; combined with `inherit_from`,
"human in the loop" is just "operator chooses where to fork." No
dedicated record type, no watched-file ingest, no fourth I/O kind —
the existing fork primitive carries the whole semantic.

## See also

- [`promptpotter/CLAUDE.md`](../../promptpotter/CLAUDE.md) — L1/L2/L3
  agent contracts.
- [`docs/architecture.md`](../architecture.md) §0 — three I/O kinds
  (Persistence, Display, Control-local) + the planned-M12
  Control-remote.
- [`promptpotter/presentation/CLAUDE.md`](../../promptpotter/presentation/CLAUDE.md)
  — sanctioned mutating endpoints (the two HITL POST routes are listed
  here, not invented ad hoc).
