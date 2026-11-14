# rec: a record's state is its type, every field read, one act record (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `rec`, arc `arc/rec`.

- **Base:** `origin/main` at `f589cbe9a`, main's tip with narr-1, small-1, webb-4, bkeys and faults-w landed. Your `<base sha>` in common.md is this commit.
- **The code shrinks:** target ≤ −250 over `promptpotter/`. Run `git diff --shortstat <base> HEAD -- promptpotter` after each link.
- **Spec:** the ledger section `### batch/27 · rec`, every row. Re-run each Re-test first; a row its re-test already closes is named and cut, not built.
  - narr-1 deleted `AnnouncedArms`, so "a fold beside `AnnouncedArms`" means a fold on narr-1's narration table, or on the arm reading that replaced it. Read what narr-1 left before building that row.

## Decisions (orchestrator; build them, do not reopen them)

- **Census first.** List every `model_validator(mode="after")` in `promptpotter/` as state-fitting ("field X is set exactly when Y") or other. Each state-fitting record becomes a discriminated union on its state field (`Field(discriminator=…)`). Its validator goes, and so do the readers' `None` branches. A record whose states share no field to discriminate on is named and kept.
  - Renamed or reshaped record fields wipe run data: the landing's normal path. Name every record kind whose shape moves; `landing_key` must move.
- **A field written and never read goes.** Confirm each listed field by a `case X(field=…)` search and a webapp search before deleting it.
  - The gate's check: every declared field of a served or recorded model has a reader, or sits on an allowlist of timestamp stamps. The baseline only falls and ends at 0.
  - It lives in the gate's existing census machinery (`scripts/complexity_ledger.py` or the gate's check table), not a new script.
- **`RoundDiagnostics`' reporting fields are required, or `None` where absent.** The `domain/CLAUDE.md` licence line goes.
- **One act record.** The arm's act (intervention plus left comparison) is one record. The arm's evidence and its `controlled` state both derive from it.
  - It is written to the ledger of the arm it acts on, so deleting the fork leaves it standing.
  - `record_decision` returns the offset the fork's cut takes, and `fork_siblings.py` appends no second `ResumeCheckpointRecord`.
- **The typed rows:**
  - `StatusMark` is typed off `RunPhase` and `StopOutcome`.
  - The ledger-record base declares `forks_inherit`.
  - A scoring cell's beat is its own record kind, keyed (arm, sample), with no node.
  - `SampleScoredRecord.running` goes, onto the fold above.
- **`TokenUsageRecord`: one field for "no wallet paid".** First census every writer of `cached` and `key_source`. If the two always agree, `cached` goes and readers read `key_source is None`. If they ever disagree, stop the row and file what differs.
  - A missing provider count (the groq repair) is an unknown count, never 0, and never estimated from content length.
- **`domain/export.py` carries the optimization's spend and tokens,** read off the spend book. It does not recount them.
- **`Sample.key` is computed once at validation.**
- **The cold fold snapshot** (rec 4): `store/read_model.py::LedgerIndex` persists its fold keyed by `file_sig`, so a restart folds only each ledger's tail. Its cache row goes in `store/data_root.py`'s CACHE rows. Time a cold `GET /hierarchy?depth=1` on a copy of a large data root, before and after.

## Links (one commit each, in order)

1. The act record, `record_decision`'s offset, and the deleted fork's exit.
2. The census and the discriminated unions; `RoundDiagnostics`; `StatusMark`; `forks_inherit`.
3. The unread fields and their gate check; `SampleScoredRecord.running`; the cell beat; `TokenUsageRecord`; the unknown count; `Sample.key`; the export.
4. The cold fold snapshot.

## Beside you

Name every shared file in its link's commit body.

- **rn-conn** (cloud, same base) renames `backend_type` to `connector` under a one-shot migration and builds one `LLMSpendBound` for the `_sent_spend_bound` hooks. You share two files:
  - `store/data_root.py`: your CACHE row beside its migration row;
  - `scoring/sample_measurement.py`: your cell beat in `_send_cell` beside its spend bound.

  Touch only your rows' hunks there.
- **lw** (cloud) is webapp-only. `webapp/lib/api/types.generated.ts` is regenerated through its script, never by hand.

## Proof (beyond common.md)

- Equals-main: no decision and no request moves on any leg. A record's on-disk shape moves (that is the point), so name each moved record kind, and `landing_key` old → new.
- The behaviour keys must not move. A move means an optimizer package read a record you reshaped: name it.
- Delete a fork off an arm, then read the arm's `evidence`: the exit stands.
- The cold `GET /hierarchy?depth=1`, timed before and after.
- Each row's re-test, before and after.
