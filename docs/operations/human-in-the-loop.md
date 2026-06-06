# Human in the loop

**HITL is not a separate I/O kind** — it collapses into the existing fork primitive.

The operator forks via `resume --fork-on-divergence` (CLI) or the webapp **Steer & fork** flow (Scoring inspector → `SteerForkPanel`): stop the run, edit the chosen searchpoint's prompt + node config + limits, and fork a sibling cycle tagged `operator_steered` (Control-remote design in [`../adr/0001-m12-control-plane.md`](../adr/0001-m12-control-plane.md); details in [`../specs/roadmap.md`](../specs/roadmap.md)). The fork mints a sibling cycle rooted at the chosen offset via `CycleEventLog.inherit_from(parent, offset)`; the new cycle inherits the parent's typed state at the cut and L1 picks up from there.

**Stop run.** Webapp's "Stop run" button writes `{cycle_dir}/.runtime/stop.flag`. The running loop's `Session.stop_check` polls the flag at the start of each round and exits cleanly; recorded as a `PhaseRecord` with `stop_reason="hitl_stop"`. CLI equivalent: Ctrl+C (first finishes in-flight, second force-quits).

Every ledger record is already typed; combined with `inherit_from`, "human in the loop" is just "operator chooses where to fork." No dedicated record type, no watched-file ingest, no new I/O kind — the existing fork primitive (Persistence) carries the whole semantic.

## See also

- [`../architecture.md`](../architecture.md) §0 — the five I/O kinds
- [`../../promptpotter/presentation/CLAUDE.md`](../../promptpotter/presentation/CLAUDE.md) — sanctioned mutating endpoints
- [`persistence-and-state.md`](persistence-and-state.md) — fork workflows + recovery
