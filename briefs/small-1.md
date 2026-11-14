> **STOP CHECK, first:** if your task fired at 07:30 UTC on 2026-11-14 or later (your first message says when), you are a duplicate run. Stop at once: change nothing, push nothing, and end your turn. Only the run fired at 07:13 UTC works this brief.

# small-1: one shape of a budget's remainder, one name check, fixed-width stamps, three torn writes (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `small-1`, arc `arc/small-1`.

- **Base:** `origin/main` at `1e0db24787dbfffc326fc65585d407faa79b7c3a`. Your `<base sha>` in common.md is this commit.
- **The code shrinks:** target ≤ −50 over `promptpotter/`. Run `git diff --shortstat <base> HEAD -- promptpotter` after each link.
- **Spec:** rows of two ledger sections. Re-run each row's Re-test first; a row its re-test already closes is named and cut, not built.
  - `### life-2b (what life-2b left)`, both rows. Row 1's re-test already passes at your base (`git diff --shortstat 805feffd6 1e0db2478 -- promptpotter/domain promptpotter/application promptpotter/infrastructure` is −310 net). Build its one offload helper only if it deletes lines at the async doors (`admission.py::_open_pass`/`_close_pass`, `head_to_head.py::_land`/`_unmint`); otherwise cut the row and say why.
  - `### batch/31 · small`, these rows only:
    - "Four settings no operator can discover";
    - "An immutable record is written four ways" (the conventions line only; no sweep);
    - "ISO stamps sort as strings at ~12 sites";
    - "Three torn-write smells" (confirm the second before building it);
    - "Two wrong names at the doors" (verify each first; one that does not reproduce is named);
    - "Three name checks stand beside the one validator";
    - "A budget's remainder has two shapes".
  - **Not yours** (they stay filed): the `Selector` default row (bkeys rewrites the optimizer packages it touches), the coupon verb, the model-swap verb and archive hygiene.

## Decisions (orchestrator; build them, do not reopen them)

- **`_Left` goes.** `jobs/quota.py` reads the remainder through `domain/spend.py::Budget` (`run_out` and its kin). If `Budget` lacks the signed per-axis remainder a quota message needs, it gains that one reader; `quota.py` keeps no second shape.
- **`iso_z` writes fixed-width microseconds,** and the "never as strings" rule in `shared/clock.py` and its doc lines go. The ~12 sort sites stay string sorts. Whole-second stamps already on disk:
  - in run data, they are wiped (no backward compatibility);
  - in a SURVIVOR file (`store/data_root.py`'s SURVIVOR rows: the job files among them), a reader that sorts across old and new stamps gets a one-shot migration under the landing (`docs/decisions.md` § 0011). Otherwise name why no reader sorts across the change.
- **One name rule.** `shared/identity.py::safe_name` uses `fullmatch`. `cycle_files.py`'s `..`/`\`/`/` precheck goes, because the `resolve` containment owns it. `draft_campaign.py` derives its slug in the dataset-name charset, so `café.csv` is refused typed or derived, never a 500.
- **The torn writes.** Each write's commit point is the last thing it writes:
  - an empty origin removes the stale `origin.yaml`;
  - a zero-spend delete writes its tombstone;
  - PAUSED keeps `superseded_by`, if that one reproduces.
  A crash-point test per write shows the half-written state reads as before.

## Links (one commit each, in order)

1. `_Left` gone; the one name rule; the two wrong names.
2. Fixed-width `iso_z` and the rule gone (with its migration, if one is owed); the torn writes.
3. life-2b's two rows; the settings listed; the conventions line.

## Beside you (four chains run at your base; avoid their files)

- **bkeys** owns identity and hashing (`shared/hashing.py`), `data_root.py`, `optimizers/nodes.py` and every optimizer package under `promptpotter/assets/optimizers/`, `runner/inner/`, `domain/l4/`, `domain/pipeline_schema.py`, `campaign_config.py`, `scripts/offline_run.py` and `scripts/gate.py`.
  - A migration you owe registers under the landing. Keep the `data_root.py` hunk to the one row, and name it in the commit body.
- **webb-4** owns the webapp and `presentation/api/routers/reads.py`.
- **narr-1** owns `views/readout.py`, `domain/activity.py`, `run_observers.py`, `projections/event_stream.py`, `live_dashboard/round_buffer.py` and `domain/arm_reading.py`.
- **faults-w** (local) folds `LedgerUnreadableError` across `account.py`, `commands/launching.py`, `commands/workspace_edits.py`, `jobs/reaper.py`, `jobs/install_spend.py` and `store/read_model.py`. It also fixes the LLM wire client and the parser seams' depth bombs. Your `draft_campaign.py` hunk is the slug lines only.
- Touch no `scripts/` file. A row that needs one is cut into "rows to file".

## Proof (beyond common.md)

- Equals-main UNMOVED on every leg.
- `café.csv` uploads under a refused-or-derived slug, never a 500.
- `iso_z` at a whole second ends `.000000Z`.
- `git grep -c "class _Left(" -- promptpotter/application/jobs/quota.py` is 0.
- `safe_name("abc\n")` refuses.
- Each row's re-test, before and after.
