# Batch 5 review (`c8f9800f1..origin/batch/5`, M N P Q R)

**Verdict: land after one fix pass.** There are three introduced findings: one webapp parity miss in Q, one ledger duplicate in R, and one low-severity label coupling in N. M and P are clean. R's store logic is clean. The batch changes no served, openapi or TS file; `p_served` +1 is M's known row.

## INTRODUCED

1. **`webapp/components/dashboard/pipeline/RaceCatchUp.tsx:17`** still renders `#{e.position}` unpadded. It shows the same `RaceCatchUpRecord.position` that Q padded in `readout.py::on_race_catch_up`.
   - Scenario: in a race, `readout.log` prints `↻ pobb catch-up #007`, while the dashboard's Race catch-up pane shows `↻ #7` for the same event. The CLI and the webapp now disagree (entry-point parity).
   - Fix: `{fmtSampleTag(e.position)}`. The census grep misses JSX `#{`. A wider re-test, `git grep -nE '#\{|#\$\{' -- webapp/components`, names only `MeasurementsPane`'s rank and `VendorLogo`'s href.
2. **`docs/specs/code-debt-cleanup.md:250-255`**: R's new row restates `:1668` ("`jobs/logs/{job_id}.log` … outlives its job: nothing deletes it"), which was on main before this batch. Two rows now hold one fact. The new row also has no **Rides with:**, which the ledger's entry format requires.
   - Fix: keep one row carrying the decision, delete the `:1668` sub-bullet, and add **Rides with:** M6 (roadmap `:167` already lists "the job logs").
3. **`promptpotter/connectors/__init__.py:57`**: the guard decides its rule by rebuilding the registry's error-message label `f"{BUILT_IN}: {m}"`, a second spelling of `plugin_registry.py:77`. Low severity.
   - Scenario: if someone rewords the origin label, `promptpotter` is refused at every boot. Rebuilding the table by member name (what `tests/test_scoring.py:823,913` did) now refuses too. That is why N edited a test outside its Owns and rewired it onto the private `Registry._builtins`.
   - Fix: one `plugin_registry` helper that builds the built-in label, read by both sites. The test could then read `connectors._builtins`, next to its `connectors._validate`.

## PRE-EXISTING (row proposals)

- **A campaign's connector has two sources.** `wiring.py:239` picks the session's connector from the definition's `backend_type`. `mask/load.py:216`, `hierarchy.py:373`, `admission.py:707` and `embedded_run.py:201` read `campaign.connector` instead. A resume after the definition is retargeted reads both. M's trim, made as the brief asked, deleted the ledger's only record of this. **Rides with:** M4b.
- **The optimizer prompt spells slots its own way.** It writes `[#N]` / `#N` (`injections/panels.py:441,606,780,884,1022,1043`, `catalogues.py:154,165`, `layer_state.py:234`), while every surface prints `sample_tag`'s `#007`. Changing it is a prompt-shaping edit. **Rides with:** M8.
- **Operator step, not a row:** `open_append` sets the mode only on create. If the box's `app_visit_digests.jsonl`, named in the deleted row, is still 0644, it stays that way until the operator runs `chmod 600` on it.

## Stale lines

- `roadmap.md:178`: M17 still lists "the file mode", which R closed.

## Checked clean

- **M:** `session.backend_id` is now read only by `search_point_scorer.py`. Every entry point reaches `lock_criterion` and `_outer_cells` through `application/`, and `backend_reach_line` is CLI-only.
- **N:** no name test is left. `hierarchy.py`, `pipeline_resolve.py` and `spawn_context.py` all read the one declaration, and an unregistered name still answers `False`.
- **P:** `poll.tsx:74` is the only `roundOf`.
- **R:** every append in the store goes through `append_line` → `open_append`: the ledger, the readout, `read_model`, the wallet, the identity jsonl tables, site records, and the job log.
  - The atomic writer is `mkstemp`, so 0600. Lock files are empty and are excluded.
  - Windows bytes are kept: `O_BINARY` plus a binary `fdopen`. The `.env` still gets CRLF, because both writers open it in text mode.
  - The test fails if the fix is reverted, under a forced 022 umask.
