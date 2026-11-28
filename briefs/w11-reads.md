# w11-reads: area 11 to its Target, a value is its aggregate plus the live round (cloud · Opus)

`briefs/common.md`, `briefs/w10-shared.md` and `briefs/w11-shared.md` bind you. SPEC: `briefs/map.md`
§ 11 and the debt doc's `## 11` section (15 rows). No chain has worked this area yet.

Target:
- A served GET mutates nothing and resolves in `application/`.
- Every score is served and never recomputed in a consumer.
- A value is its aggregate plus the live round, never a copy of its history.
- The lineage tree is a read model that decides nothing.

Links, deletion first. Work each row along its root:
1. **The live reads re-serve history per poll**, **Head-to-head re-reads every pair's bench per GET** and
   **Closed rounds folded twice per readout**. One root: a closed round is folded once and its aggregate
   is read, never re-folded per request. Measure `_cycle_cells` per request before and after on a
   world campaign with many rounds; the Done-when is O(R·A).
2. **The consumer spells the lift's verdict**. The verdict is a field of `MeasuredLift`, and
   `metric_catalogue.py::CHANNELS` goes.
3. **A restatement reads as a round**. A restatement is a close of the round it restates, worded as one,
   in the narration and the readout. Re-test through a resumed offline leg.
4. **The served walk axis is a declared order**. The axis derives from the cells taken.
5. **An inner campaign serves a Delete no door presses**. The subtree entry shares a base with the listed
   entry, not the listed entry's acts.
6. The exhibit's four rows (the account feed never re-asks, `ActiveSessionResponse`'s two shapes, a
   follower reads with the host's whole `Stores`, a follower's live tail outlives its switch). One root:
   a `Stores` narrowed to the exhibit's campaign, which the feed's subject and the stream both read.
7. The map's unfiled find: the masthead's Candidates figure is `RunStanding.candidates`, a second count
   beside `ServedDashboard.line`. Re-test it first; it is a fix if it holds and nothing if it does not.

Leave these rows open, each for its named reason:
- **The lineage subtree sorts every candidate per candidate**: a new record kind, so it rides with a
  lineage-fold chain after w11-records2.
- **`cycle_reads.py` copies the live round's candidates**: blocked on `LineArm`.
- **A wiped campaign answers two failure kinds**: Held, unreproduced.
- **Readout words every look-ahead press as expiring**: it needs a field on `SampleStartedRecord`, in
  area 01, which w11-records2 owns.

Census: `application/evidence/**` (3,038 lines, one row), `read_params.py`, `views/render/phase.py`,
`render/export.py`, `render/primitives.py` and `domain/narration.py`. Give each module a verdict against
the Target.

Owners and shared files:
- OWNS: `application/served_dashboard.py`, `cycle_reads.py`, `hierarchy*.py`, `read_params.py`,
  `library.py`, `application/views/**`, `application/evidence/**`, `domain/dashboard_rows.py`,
  `domain/hierarchy.py`, `domain/narration.py`, `domain/paired_reading.py`'s `MeasuredLift`, the exhibit
  files the access rows name, the webapp consumers of a field you add or move, and their tests.
- w11-records2 runs at the same time and owns the rest of `domain/**` and `shared/**`. It may re-point
  import lines in your files. You edit no record model; a row that needs one is left open with its reason.
- Leave the spend rows inside `served_dashboard.py` (map § 11's L562 and L566) to area 05. Leave
  `application/health.py` unedited.

DONE WHEN:
- Each row is re-tested through a door, before and after.
- The map's Done-when lines are answered with their commands. On a running campaign, does every surface
  name the same active node and arm?
- Equals-main names each move, and the keys are named.
- `python scripts/gate.py` is green.
