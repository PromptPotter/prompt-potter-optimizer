# narr-2: the ledger keeps facts, every renderer reads the records, one way to the ledger (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `narr-2`, arc `arc/narr-2`.

- **Base:** `origin/land/wave-6` at `f22c87a41`: main (`fda4c2bd6`) plus the readings and xconn 1–2 landing commits, which push to main as they are. `live_dashboard` is one module, the CLI is five modules, one `Estimate`. Your `<base sha>` in common.md is this commit. Branch off it, not off `origin/main`.
- **A first run was cut off by a usage limit in link 2, with nothing pushed.** Start fresh, and push `wip/narr-2` after every commit (common.md). One finding of that run, to verify: the run's ledger already rides the scope, and `CycleEventLog` is a stateless path handle, so link 2 is mostly a sweep of signatures and call arguments. `bench/resume_and_fork/decisions.py::record_decision` threads a sink only for that reason; the run proposed a `ResumeCheckpointRecord` builder in its place.
- **The code shrinks**, judged as common.md says. This is the largest excision left in the run path: `domain/phase_views.py` (13 view classes), `views/view_models.py::ViewContext`, the ingress that builds the views, `round_buffer.py` and the threaded `ledger` parameters all go. Expect well over −300 over `promptpotter/`. Run `git diff --shortstat <base> HEAD -- promptpotter` after each link.
- **Spec:** the ledger section `### batch/26 · narr (links 1-2, on stream-rk's tip)`, every row but the one this brief holds. Re-run each Re-test first: rec typed the run-phase record as a union and la merged `live_dashboard`, so a row may be half closed. Name a closed row and cut it; build only the open half.

## Decisions (orchestrator; build them, do not reopen them)

- **The ledger keeps domain facts only.**
  - A phase act is one typed record, its text computed at render. `PhaseRecord`'s untyped event string and its view go.
  - Every view class in `domain/phase_views.py` goes. `ansi.py`, the readout and the live dashboard read the records (`RoundCompleteView` is `RoundClosedRecord`; `RunSpendView` is the spend record it copies).
  - `ViewContext` goes. The anchors fold from the launch's INIT record inside the projection that reads them, so `ArmRecord.anchors` has no default and no writer stamps them.
  - Run data is reshaped: the ledger's kinds change, so cycles are wiped. Name each kind that goes or changes.
- **One way to the run's ledger: the run scope.** Its mute is what keeps a probe render from writing.
  - The threaded `ledger` parameters go, and so do `RoundContext.ledger`, `RunObservers.ledger`, `session.state.observers.ledger` and `llm/telemetry.py::append_record`'s `ledger=` argument.
  - A writer that holds a `Cycle` but no scope is a bug you fix where the scope opens, never a parameter you keep.
  - Some ledgers are written outside a run, and they take the same door. One is the draft's ledger: `datasets/origin_resolve.py:144` bills origin resolution to it. Another is the check-in's: `jobs/mint.py:704`. Each one's entry opens a scope over its ledger, as `bench/task_context.py:236` and `initialization/scoring_scope.py:212` already do.
  - **Census every caller first:** `git grep -n 'ledger=' -- promptpotter`. List each caller with the scope it writes under. `spend_book.py`'s holds and the read-model's `ledger=` are a different parameter; name each one you keep, with the reason.
- **A round's close has one builder**, `closed_round`, beside `bench/node_context.py::restated_round`. `tests/factories.py` calls it.
- **A stop's cause is a fact every door serves.** A pause by interrupt reads "paused by interrupt" in the readout, `hierarchy` and the CLI. A `parked` word nothing reads goes.
- **The acts a cycle offers are its admission's.** They are joined where the phase is projected (`application/hierarchy.py`, its two `HierarchyState.of` calls), carried on `domain/hierarchy.py::HierarchyState`, and read by `webapp/lib/derivations/run-phase.ts`. An archived campaign's paused cycle offers no `start-run`.
- **One hold tracker.** The live dashboard reads `narration.py::Narration`'s holds, and its `_holds` goes. `RoundBuffer` goes: its readers read the narration's live round.
- **The arms gate is the typed act's narration row.** `views/readout.py`'s `[ARM GATE]` block goes.
- **A closed round's `StandingArm`s freeze at its close**, so a value at round R builds tuples for the live round only.
- **One dashboard read for every door.** The CLI reads `ReadKind.CYCLE_DASHBOARD` (a verb in la's `commands/reads.py`), and `application/embedded_run.py` exports the same read. What the fold knows (the run phase, the stop's cause) rides the disk checkout; `admission` is the read's own join and stays off disk. `potter-run/SKILL.md:29,53` names the verb.
- **Held, not yours:** "The live reads re-serve history per poll". Its closed-round read is a design fork; leave the row as it stands.

## Links (one commit each, in order)

1. The ledger keeps facts: the typed phase act, the view classes, `ViewContext` and the anchors' fold. Wipe: cycles.
2. One way to the ledger: the `ledger` parameters, `RoundContext.ledger`, `RunObservers.ledger`, `append_record`'s argument. The `closed_round` builder.
3. The served facts: the stop's cause, `parked`, the admission's acts per cycle, one hold tracker, `RoundBuffer`, the arms gate's row, the frozen `StandingArm`s, the dashboard read on every door.

## Beside you

Name every shared file in its link's commit body.

- **faults-1** runs after you, on your tip. It gives `Refusal` its `next_acts` and spells no verb or flag in a message. In `domain/phases.py` you own `parked` and the per-cycle acts. Leave `STOP_REASON_INFO`'s `next_acts` and the refusal rows as they stand, since they are faults-1's.
- **ren** (cloud, later) renames classes defined twice. It waits for you.

## Proof (beyond common.md)

- Link 1 wipes cycles and changes ledger kinds: equals-main may move only trace leaves that read a ledger kind you changed. Name every moved leaf (`tracediff.py`); a moved decision or request is a bug.
- Links 2 and 3: equals-main UNMOVED on every leg, behaviour keys unchanged.
- The landing key moves at link 1: name old → new and what it wipes.
- A probe render's records reach no file, before and after link 2.
- `python -m promptpotter <verb> --campaign <id>` reads the same dashboard body as `GET /campaigns/{id}/cycles/{cycle}/dashboard` on the offline world.
- `playwright` is green at the tip, and the chat thread and the readout render every phase act (a shot of each).
- Each row's re-test, before and after.
