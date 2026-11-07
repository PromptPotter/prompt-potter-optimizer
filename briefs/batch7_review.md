# Batch 7 review (`origin/batch/6..origin/batch/7`, W Y AA AB AE AD)

**Verdict: land after one fix pass.** W, Y, AA and AD are clean. AB has one introduced finding, AE one low.

## INTRODUCED

1. **`run_job.py:91` with `cycle_files.py:120`.** AB moves the detached child's stderr out of the install tree into the tenant's cycle. `list_cycle_files` hides `.runtime/` (`_shown`, `:95`), but `read_cycle_file` never asks `_shown`; `_resolve_safe_file` refuses only `..`, `\` and escapes.
   - Scenario: a tenant reads `GET /campaigns/{c}/cycles/{y}/file?scope=cycle&path=.runtime/job.log` (REST or MCP) and gets the run process's INFO log and tracebacks: host paths, the backend base URL (`wiring.py:254`), machine back-pressure (`capacity.py:24`). On batch/6 it sat in `jobs/logs/`, outside every scope root.
   - Fix (root): `read_cycle_file` answers NotFound for any path `_shown` hides, one rule for list and read. The webapp opens only listed files (`FileTree` → `reads.ts:202`). The fix also closes `.runtime/ledger.jsonl`. Hold it in `test_security.py`'s leaks journey.
2. **Low: `main.py:83`.** On a server that never admitted a run, `this_producer` creates and locks a `jobs/producers/*.lock`. It is the first read that mints: `_orphaned` asks only once a task exists, so the producer is already minted.
   - A clean exit is safe: atexit retires the file after the lifespan.
   - A hard kill during `notify_operator`'s HTTP call leaves a producer file with no job. `reclaim_producer` never reaches it, since it runs only for a released job.
   - Fix: read `_tokens` without minting. `None` means nothing of its own.
   - The identity is right. With `KillMode=process`, `_DETACHED` and `adopt` re-stamping `producer_id`, only a spawn not yet adopted dies with the server.

## PRE-EXISTING (row proposals)

- **`persistence-and-state.md:82-86`**: the `.runtime/` layout omits `pass.lock`, which `data_root.py:371` rows; twin of census 3's `jobs/ended/` smell. **Rides with:** M17.

## Stale lines

- `deploy-linux/README.md:297-299`: "the server stopping with runs in flight" rings and "a stop with nothing running" is silent. Now: the runs it stops; detached runs continue, unannounced.
- Pre-existing: `roadmap.md:72` (U3) cites "§ Seen removing the migrations", which the ledger has not held since main's `0b2cab850`.
- Pre-existing: `potter-run/SKILL.md:95` and `potter-self/SKILL.md:37` name `.runtime/streams/round_NNNN_pobb.jsonl`, which no code in `promptpotter/` writes.

## Checked clean

- **W:** nothing holds `backend_id`. `connector.name` was never empty and every entry point builds through `init_services`, so filing and replay are unchanged. `_round` strips only the archive address, and its node-less walks open no feed. No test door remains.
- **Y:** `_at` loaded that same manifest from that same leaf.
- **AA:** option 3, as decided. No other holder of `start_refusal`; PAUSED and TERMINAL still answer their parked word.
- **AB:** every delete takes the log: the campaign rmtree, the keepsake strip (not a keepsake row), stub `delete-cycle`, sandbox and draft rmtrees, `reset` (`Fate.RUN`). `land-data` classifies it at campaign, draft and sandbox scope. Nothing reads `jobs/logs` any more. Storage counts it under Trace.
- **AD:** byte-equal except two comments, both correct. Every moved selector names `wf-`, `pipeline-` or `connector`; nothing in barrel slots 16–27 matches a renderer element; no keyframes moved.
