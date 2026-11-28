# bb-folds: N7's premise, measured (read-only)

Base `89e4cd808` (`origin/release/next`, Sat Nov 28 12:02:56 2026 +0100); `origin/main` last commit Fri Nov 27 22:47:53 2026 +0100. No code landed, no paid call. Env: uv 0.11.21 pinned, `uv sync --frozen --all-extras`. I skipped `npm ci` because no gate ran. Digests at base: estimator `146ec211`, capo `feef57af`, capo-pobb `aeeb6e73`, gepa `dd5adf40`, levi `8e998b8f`, potter `8308ae60`.

**Commands.** `PROMPTPOTTER_HOME=/tmp/h1 python scripts/offline_run.py`, then the same with `/tmp/h2`. Both exited 0 (108 s). Then `probes/fold_rounds.py <home>` (82 cycles per home, 0 errors), `probes/derived_purity.py <home>`, `probes/hash_derived.py /tmp/h1 /tmp/h2` and `probes/ledger_pair.py /tmp/h1 /tmp/h2`.

**Reference:** `STANDING_ROUNDS`, which is `_StandingFold` composed over the fork chain. `closed_round`, the round file, resume and fork all read it.

| fold | index | members | winner | pair | ending | status | cells |
|---|---|---|---|---|---|---|---|
| `_StandingFold` (own ledger) | own rounds¹ | same | same | same | same | same | same |
| `LiveDashboardProjection` | same | same² | same | not held | same | same | not held |
| `ReadoutProjection`³ | same | same | same | same | same | same | same |
| `RoundFileProjection` | own rounds¹, lazy⁴ | same | same | same | same | same | same |
| `RoundAudits` | own rounds¹, no round 0⁵ | not held | not held | not held | not held | **14 cycles differ** (capo/interrupted r1) | not held |
| `ActivityFeed` | own rounds¹ | **1 cycle differs** (potter/rebased fork r2) | not held | not held | not held | same | not held |
| `_RayTail` | as `ActivityFeed` | **1 cycle** | not held | not held | not held | same | not held |
| `CycleFactsFold` | no round notion | | | | | | |

No fold holds cost. It is held per round by `ledger_scan.py:1103` (`_by_round_then`) in the money line. Both homes give the same table.

¹ Each fork reads its own ledger by design. ² The dashboard banks proposals only (`rounds.py:97`): the parent's re-read is excluded on purpose. ³ The readout needs a live run, so its round state was re-derived from its own rule (`readout.py:269-272`, `:394-397`). ⁴ Round files are written only at a close (`closed_rounds.py:151-162`). ⁵ An audit exists only for rounds that made an optimizer call.

**Derived files.** Re-derived from their own ledger, `index.json` matched 84/84 byte for byte and `dashboard.json` 84/84 at its own `at_offset`. Four dashboards lag the head by `at_offset` alone: a `cycle_superseded` or `decision` record lands after the last write. Across the two runs, 0 of 745 derived files are byte-equal, and 0 of 109 even after masking ids, timestamps and durations, except 38 dashboards. The ledgers themselves differ: only 24 of 63 paired ledgers share a record sequence.

**Verdict.** The premise holds for the four folds that rebuild rounds (standing, dashboard, readout, round file). **The premise is false for `RoundAudits`**: a close with no fresh call is dropped. **It is false for `ActivityFeed` and `_RayTail` on one fact**: a restated branch close seats no members. Neither difference reaches one of the four files, so neither has to ride W, and **N7b can collapse the folds on its own landing**. Two conditions: `CycleState` keeps both the own and the line scope, and its byte gate re-derives from one ledger, because no derived file is byte-stable across runs (§10.1's "before and after" hash cannot work across two runs).

## Symptoms

- `audit_trail.py:121-122`: `_settle` returns early when nothing is buffered, so the close at `:98` never banks `finished_at`. A resumed round whose calls all landed before the pause reads as interrupted ("Null until the round closes", `round_audit.py:89`), and `cycle_reads.py:97` serves that. Suspected root: the audit's round end is "the next settle with content", not the close.
- `narration.py:202-205` against `ledger_scan.py:484`: a restated close is narrated but its arms are never seated (`Narration.close`), so the SSE tail and the ray hold no members for a fork's branch round.
- `run_phase_control.py:70`: `FlightRecord` is published on a `call_later` timer, so the ledger's length and order depend on timing (in 20 ledgers its count moved between runs).
- Not pure functions of the ledger: `readout.log` (`readout.py:207` strftime; `:246`/`:615` monotonic; `render/phase.py:316` orders phases by measured seconds) and round files (`closed_rounds.py:109` joins the archive's rows; the answer addresses differ between runs).

## Claims tested

- §5 V9, "each rebuilds the SAME rounds": **held** for 4 of 7 folds, **refuted in part** for `RoundAudits` (round end) and for the Narration folds (restated arms) (`probes/fold_rounds.py`).
- §10.1, "derived files can stay byte-identical": **held** under re-derivation for index and dashboard (`probes/derived_purity.py`); **not testable across runs** (`probes/hash_derived.py`, `probes/ledger_pair.py`).

## Rows to file

- **A round's audit closes at its close.** `RoundAudits._settle` (`audit_trail.py:121`) drops a close that lands with nothing buffered, so a round resumed with no fresh call keeps `finished_at: null` though it closed. Re-test: `probes/fold_rounds.py` `RoundAudits` status, 14 cycles now, target 0.
- **A restated close seats its arms in every Narration.** `Narration.read` on a restating `RoundClosedRecord` (`narration.py:202-205`) never calls `close()`, as `_StandingFold` does (`ledger_scan.py:484`). Re-test: `ActivityFeed` members, 1 cycle now, target 0.
