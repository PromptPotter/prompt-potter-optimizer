# hubs: the record kinds in one registry, no thin wrappers over the ledger (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `hubs`, arc `arc/hubs`.

- **Base:** `origin/main` at `822ed8e72b175055763653daea9efad3871cf28a`, main's tip with valid (de4c0cc47, valid-3 822ed8e72) and rel (75428b66b) landed. Your `<base sha>` in common.md is this commit.
- **The code shrinks:** target ≤ −220 over `promptpotter/` (link 1 ≈ −105, link 2 ≈ −30, link 3 ≈ −110). `git diff --shortstat <base> HEAD -- promptpotter` after each link; a link that leaves the chain net positive gets one rebuild, then the chain stops there.
- **Spec:** the ledger section `### hubs` of `docs/specs/code-debt-cleanup.md`. Its premise is partly stale (read-only survey at 1c8b006ab):
  - `RoundResult` holds 13 fields, not 34.
  - Round facts are already kind-keyed (`RoundOutput` / `OptimizerFact`, the bench fact copied generically).
  - `runner/entry.py` and `runner/round.py` churn because they ARE the run's sequence; no registry fits them.
  - Cut the row to its open half, record kinds in one registry, in link 3's commit, and name the hunk.

## Decisions (orchestrator; build them, do not reopen them)

- **Telemetry writes records, it does not wrap them.** `infrastructure/llm/telemetry.py`'s `emit_*` functions go (7 functions, ~134 lines). Each caller builds its record and appends it through the ledger's one append. `send_pacing` already calls `ledger.append`.
- **No thin wrapper over a store method.** These go, and callers call the method they wrap:
  - `campaign_store/store.py`'s `grade_fork`, `record_intervention`, `record_spawned`, `bank_final`, `propose_round`, `rebank_round`, `resumed_ending`, `round_proposals`, `round_submission`, `read_resolved_run`;
  - every other store method whose body is one forwarding call. Grep for it; don't trust this list.
  - valid-3 re-shaped `mint_cycle`, `mint_fork_cycle` and the fork's records. Re-read them; a wrapper it made non-trivial stays.
- **One append discipline.** This is the row valid-3 filed, "One fact, two append loops":
  - `store/io.py::append_jsonl` lands a batch in one locked write, while `ledger.py::CycleEventLog.append` takes one record.
  - The ledger's one append takes a batch, and `append_jsonl` is its one writer.
  - Every caller that loops single appends hands it the batch.
  - This rides link 2.
- **One record registry.**
  - Generalize `optimizer_state.py::StatePart`'s kind table; never build a second one.
  - `CycleRecord` becomes a base class. The adapter and `ProjectionKind` derive from the registry: `ProjectionKind` is a derived `Literal`, with a `str` alias under `TYPE_CHECKING`.
  - One `parse(kind, raw)` replaces the three ledger-line parsers.
  - Record modules under `domain/records/` are found by walking the package at import, as injections are. `ErrorRecord` and `PartitionRecord` move in.
  - The predicate that finds record modules (`p_record`) excludes modules that declare arms, or it grows the code.
  - `p_record` (23) and `p_record_drift` (33 since valid-3, where `live_dashboard/projection.py` reads the mint's cut) both fall. Name each count, and lower each `BASELINE` row to it.
  - `CycleEventLog.iter` is read only by tests: it goes.
- **`CycleResult` and `CycleFinal` are one type.** `runner/entry.py` copies one into the other field by field. One type remains, and the L4 estimator digest re-keys: name it.
- **The family split is a stop, not a goal.** Split `domain/run_records.py` into `domain/records/{round,cycle,spend,llm,account}.py` only if the split is net ≤ 0. If it is not, move `run_records.py` whole into `domain/records/` and skip the split.

## Links (one commit each, in order)

1. Telemetry's `emit_*` wrappers go.
2. The store's one-call wrappers go.
3. One record registry and one parse; `CycleResult` ≡ `CycleFinal`; the family split or the whole move.

## Beside you

Nothing runs beside you. valid and valid-3 have landed, and their files are now yours. Re-read them at your base:
- `store.py`, `run_records.py`, `ledger_scan.py`, `live_dashboard/projection.py`;
- `entry.py`, `fork_siblings.py`, `jobs/mint.py`, `repair.py`, `draft_store.py`.

## Proof (beyond common.md)

- Digests: a treatment digest moves only where a member module's own code changes (it calls an `emit_*` or a store wrapper you delete), and link 3 moves the L4 estimator. Derive that set at your base by grepping the callers. Name each move. Any move outside the set is a failed proof.
- Equals-main: no decision moves on any leg, in any link.
- No wipe: every `record_type` string stays. If one changes, stop and name it.
- Re-test: a new record kind lands in a file of its own with no hub edited. Show it with a scratch kind in a test, then delete the scratch kind.
- Rows to file, not build:
  - `store.py`'s `write_resolved_run` carries a policy warning (`:755-780`);
  - `views/activity.py`'s 11-arm `case: pass` (`:248-261`).
