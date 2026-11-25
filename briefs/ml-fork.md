# ml-fork: θ read on the scale's cells still differences; a declined fork is recorded (cloud)

`briefs/common.md` binds you (read BASE FIRST), with ONE override: the base is
**`origin/arc/ml-6` at `fc9a2e7b5`**: main `da08f4717` (W1) plus the measuring loop with its defaults
flipped (`informative`, `per_decision`, `per_round`, `rolling`, `vote: overlap`). Replace the ancestor
check with `git merge-base --is-ancestor da08f4717 fc9a2e7b5`; equals-main runs against
`fc9a2e7b5`'s offline home. Read `git show origin/out/ml-6-report:report.md` first (its first row to
file is this arc). Push to `arc/ml-fork` as ONE commit.

## What was measured (the operator's desk, at `fc9a2e7b5`)
`scripts/offline_run.py::run_rebasing` pins `pobb.row_choice: random` and `theta_election.vote: draw`.
With both pins dropped, the leg fails: "a fork that changes the config minted no fork". A temporary
print in `bench/difficulty.py::level_series` showed every line's series caveat `unmeasured_delta`:
the judging scale (the ruler less the parent line) carries 61 rows, while the line read 68–96 cells,
24–40 of them off it. Then:
- `LevelSeries.level` returns `None` for every round, because the series caveat is non-null.
- So `mask/load.py::load_lineage_spine` holds no θ, `backprop.py::select_branch_round` returns `None`,
  and `escalation.py::_rebase_request` drops the L2's `fork_proposal` with no record.

Mechanism: under `rolling`, `potter/race.py::_settles` never settles the parent (it stands no bar), so
`_hand_out` hands it its whole budget while rivals are cut. Its last rows have no other answerer, and
the scale leaves the parent line out by design (`verdict-resolution.md` § One ruler, one clock). A
fresh store (the landing drops the whole archive) meets this in every real run, not only offline.

## The links
The operator's standard: "the long-term cleanest solution". `informative` row choice is adaptive
testing: arms read different cells by design. A withholding keyed on "the cells differ" makes the
default's own instrument unreadable.
1. **`unmeasured_delta` is a footnote, never a withholding.**
   - On one warm `ruler.id`, a Rasch θ is read item-free: two levels read on different cells the scale
     carries still difference. A cell the scale does not carry costs precision (θ's SE, `cells`), not
     the scale. The caveat stays on the reading and every surface still shows it.
   - It no longer nulls `LevelSeries.level`, `plateau`, a lift (`AbilityReading.lift_over`), or the
     election column (`arm_reading.py::ElectionColumn.of`).
   - The other five caveats keep withholding as today. If you find another one's scope wrong (for
     example, a per-reading caveat unioned over a whole series), file it as a row; don't widen this
     arc.
   - ONE fact decides which caveat withholds: a property of `ThetaCaveat`, or a field of its
     `THETA_CAVEAT_INFO` row. Every reader of "is θ withheld" asks that fact; none tests
     `caveat is not None`. Grep them all, including `hierarchy.py:258`, `panels.py:1028`,
     `transcript.py:44`, `l4/proxies.py:145,151`, `ansi.py:261`, `markdown.py:114`.
   - Rewrite `verdict-resolution.md`'s "Unmeasured difficulty" bullet with this reasoning. Today it
     says the caveat "does not cancel inside a lift".
   - **Stop and report, don't build,** if the base shows a reason the withholding must hold (a test,
     a doc, a measured case).
2. **A declined fork is recorded.** When `_rebase_request` returns `None`, for any reason (cold ruler,
   no pick, no node on the spine):
   - the round's records name the L2's proposal and why it was declined, on an existing channel
     (the layer's fire record banked in `_fire_layer`, if it fits);
   - the readout prints one line.
   - A state the system can enter but not report is the costliest bug here.
   - **Re-test:** a proposal on a spine holding no θ leaves that record and line. Before: nothing.
3. **The rebase leg runs potter's defaults.**
   - Drop `run_rebasing`'s `row_choice` pin.
   - Drop the `vote: draw` pin too, unless the leg then fails for a separate reason. Its comment says
     "so the noise walk holds a promotion up for the doors to read". If it does fail, keep that pin,
     and state the failure and whether it is the fixture or a defect.
   - **Re-test:** "minted no fork" → the leg mints its forks under the defaults.

## Owns
- `domain/ruler.py`.
- The withholding readers named in link 1, at the caveat test only.
- `application/mask/load.py` and `backprop.py`.
- In `potter/escalation.py`: `_rebase_request`, `_layer_control` and the fire record they write.
- The readout line's renderer.
- In `scripts/offline_run.py`: `run_rebasing`'s knobs and comment only.
- Their tests, `docs/methods/verdict-resolution.md`, and the generated specs and keys, each by its
  script.

Beside you, **ml-7** owns:
- `application/scoring/`, `nodes.py`;
- potter's `race.py`, `pobb.py`, `rasch.py` and `knobs.py` pobb block;
- `runner/round.py`, `ledger_scan.py`, `live_dashboard.py`, `served_dashboard.py`'s walk builder;
- it may edit `offline_run.py` elsewhere.

Touch none of it. **l4-key-c** (another chain) edited `domain/l4/proxies.py`, so keep your edit
there to the caveat test: one line to merge.

## Proof
- **Equals-main against the base**: capo, capo-pobb, gepa and levi UNMOVED.
- Name every potter leg that moves, and say what moved (θ levels, plateau, escalation reads, forks).
  Attribute each move to its link.
- **Digests and keys:** name every move, old → new.
- **Wipes:** name any, or none.
- **Gate:** the full gate is green.
- **Report** on `out/ml-fork-report`:
  - the commit and its +added/−deleted;
  - the re-tests before → after;
  - the vote-pin finding;
  - decided in-run;
  - Rows closed, Rows to file, Symptoms.
