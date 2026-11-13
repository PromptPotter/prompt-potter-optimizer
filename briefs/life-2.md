# life-2: what life left at landing 51 (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `life-2`, arc `arc/life-2`.

- **Base:** `origin/main` at `e24dfda95` (v0.8.20). What you build on:
  - Landing 51 (life): the launch lifecycle as data (`domain/job.py`'s `LAUNCH_MOVES`, one `JobRegistry.move`), `infrastructure/run_state.py`, and the opener owning the backend client.
  - Landings 48, 51 and 54 (holders): `holders.py::holder_of` is the one holder fold, the producer token the death rule, and a pause stands for its hold.

  Build on that, never beside it.
- **Spec:** the ledger section `### life-2 (what life left at landing 51)` of `docs/specs/code-debt-cleanup.md`, every row. It also includes every row elsewhere whose **Rides with:** names life-2:
  - the host-door row, "A host may file any kind its identity's capability allows";
  - the passes-6 section's stale-pause row.

  `git grep -n "Rides with:\*\* life-2" -- docs/specs/code-debt-cleanup.md` lists them all.

## Decisions (orchestrator; build them, do not reopen them)

- **A blocking lock refuses on the event-loop thread.** `take_lock` with wait > 0 and the admission gate raise when called there, and each async door offloads once (`asyncio.to_thread` at the door, never inside the store). The per-request reads are offloaded the same way or read once at startup (build id, grants, `claim_default`, the dispatcher's ack, the kept-upload re-read).
- **The server composes, the CLI prints.**
  - The drift pivot is `ConfigDriftError` carrying `NextAct("mint-campaign")`.
  - The second Ctrl+C's verb comes off the served next act.
  - Readiness is checked once, with one `draft_served` fetch.
  - `machine-status` serves `account_bound`.
- **The live-jobs index is kept by the writer.** It is one file under the jobs dir that `JobRegistry.move` / `release` update under the lock they already hold. `list_running` / `list_queued` / `pass_on` read it, and reaping moves to the sweep. A job file that does not load is named in `machine-status`'s served read.
- **Every client is closed by the opener that opened it.** An async withdraw closes the head-to-head source stack. Tests open clients through the opener. The runner asks the dashboard projection for spend, and the `dashboard.live.state` walks go.
- **The host door:** a `CommandCall` names its door, and `open` refuses a kind whose `press` excludes that door.

## Links (one commit each, in order)

1. No blocking lock on the event loop, with a test that a loop-thread `take_lock(wait>0)` raises.
2. The writer-kept live-jobs index; the unloadable job named in a served read.
3. The server composing what the CLI prints; the host door.
4. No client left open, the spend projection and the stale pause.

## Beside you (cloud, running now)

- **cfg-2** shares two files, each by hunk:
  - `presentation/cli/commands/new.py`: cfg-2's are the `--set` typing and the `--config` budget pop; yours is the readiness check.
  - `application/commands/launching.py`: cfg-2's is the steer typing; yours are `admit` and the readiness check.

  Edit only your hunks and name each in the commit body.
- **Main also holds an unpushed commit** that touches `application/health.py`, `deploy-linux/update.sh`, `scripts/gate.py::_install_digest`, the end of `tests/test_security.py` and the ledger's `batch/31 · box` row. Leave those hunks alone.

## Proof (beyond common.md)

- Equals-main: every leg UNMOVED (no decision moves); name any digest move.
- Each row's Re-test, before and after. A five-door line in the report: each door's launch through the one offload.
