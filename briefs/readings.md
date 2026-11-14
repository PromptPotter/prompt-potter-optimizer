# readings: one estimate, one lift test, one ruler, then the statistics fixes (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `readings`, arc `arc/readings`.

- **Base:** `origin/main` at `f589cbe9a`: main's tip with the behaviour keys landed. L4's C0 is re-measured on it, beside you. Behind the keys a refactor is key-neutral; a link that changes a number moves its keys, and you name each move.
- **The code shrinks:** target ≤ −130 over `promptpotter/`. Expected per link:
  - link 1 ≈ −100;
  - link 2 ≈ −30;
  - link 3 ≈ −35;
  - link 4 ≈ +35.

  Run `git diff --shortstat <base> HEAD -- promptpotter` after each link. A link that leaves the chain net positive gets one rebuild, then the chain stops there.
- **Link 4's stop:** link 4 builds only while the running total stays below 0. If it can't, build its rows cheapest first, stop where the total would turn positive, and file the rest in full.
- **Spec:** the ledger section `### readings` of `docs/specs/code-debt-cleanup.md`, every row. Re-test each at your base: rk-dec-2 already moved the race onto one joint fit.

## Stale premises (read-only survey at 1c8b006ab)

- There are 30 `*Reading` classes, not 31: 16 estimates (544 lines) and 14 statuses.
- The "hysteresis" wording is closed.
- `sigma_theta` IS read. What is unused is `fit_theta`'s kwarg.
- rk-dec closed the ceiling stop (`anytime_level_lower`) and the lock-in half of the row.

## Decisions (orchestrator; build them, do not reopen them)

- **One `Estimate` (value, ci_lo, ci_hi, n, se).**
  - `BandedValue` and `LiftEstimate` merge into it.
  - The 11 printers become `Estimate.text`.
  - `origin_floor` reads `_lift`.
  - Status readings stay.
- **`EstimatorSpec.kind` is `paired_t | sign | theta`, and each kind returns `MeasuredLift`.**
  - `sign` is rk-dec's `sign_test_p`: exact McNemar on the discordant cells.
  - `theta` is `arm_lift`.
  - These fold into it: `ThetaLift`, `PriorStanding`, `BarSubject`, `theta_band`, `lift_over`, and `BarRead.quote`, which stores English in the round file. If it still stands at your base, `PriorRead` folds in too.
  - θ and paired t stay distinct measurands. CAPO, GEPA and LEVI keep their papers' bars.
- **One ruler and one row set for the race and the election.**
  - The election moves onto the race's ruler and rows, and δ folds afterwards.
  - `election_rows` and the three `pooled` paths go.
  - One bar decides the lost ideas, the side wholly below, for finished and cut arms alike.
  - `RoundAdvance`'s `tops` half goes.
  - The code is fixed, not the doc. Then `candidate-elimination.md` and `verdict-resolution.md` state it.
- **The statistics fixes, as one link.**
  - One bounded-mean interval in `shared/statistics.py`, chosen by `Measurand.binary`. An exact or Wilson interval for binary; for graded, a variance-bounded t whose bisection it shares with `anytime_level_lower`. `level_band`'s clip and `mean_ci` go.
  - The Rasch prior sits on the difference, not on each arm. δ uncertainty enters through 1+π·se_δ²/8.
  - A cold ruler refuses `comparable_to`.
  - φ reads `Measurand.binary`, never the values.
  - One "prior-dominated" caveat covers both the floor and the ceiling.
  - The head-to-head MDE uses Holm's α.

## Links (one commit each, in order)

1. One `Estimate`.
2. `EstimatorSpec.kind` and `MeasuredLift`.
3. One ruler and one row set.
4. The statistics fixes, under the stop above.

## Beside you

Name every file you share with these chains in its link's commit body.

- **lw** (cloud) moves webapp modules:
  - `lineage`, `poll`, `workspace`, `scoring-mask` and `chat/thread` go to `lib/state/`;
  - `cell-means` and `prompt-fields` go to `lib/derivations/`;
  - `shell/searchpoint/`, `shell/scoring/`, `shell/node-surface/`, `PairedLift` and `SummaryBlock` go to their surfaces' folders.

  Edit each webapp file where it stands at your base. The landing follows lw's renames.
- **rec** (cloud) makes an arm's act one record in `runner/round.py` and `scoring/query_loop.py`, turns state-fitting records into discriminated unions, and deletes unread fields. You share `runner/round.py`: touch only your rows' hunks. A field you need that rec's census calls unread: name it.
- **rn-conn** (cloud) renames `backend_type` to `connector` and reads `NodeCall` by its fields. Neither touches your rows.

## Proof (beyond common.md)

- Links 1–2: equals-main UNMOVED on every leg; the estimator digest moves, so name it.
- Link 3 moves decisions on every leg; name each.
- Link 4: name each move, and give the coverage numbers for the old and the new interval. 20/20 has an exact lower bound of 0.832, and at p=0.95, n=20 the coverage must reach at least 0.95.
- Webapp:
  - `types.generated.ts` holds 71 references and `SubjectReading` 43 in 15 files;
  - `ci_lo`/`ci_hi` appear 35 times in 13 files;
  - `format.ts` has 5 TypeScript twins of the printers.
  - The served shape changes, so the browser renders `Estimate` from the served text and keeps no printer of its own. `playwright` is green at the tip.
- Wipe: cycles (the round file's shape changes). Name it.
- Symptoms already seen. Close each one your links touch, or file it:
  - `EliminationGate` is a one-member enum;
  - `validators.py:397-398` is always true;
  - `CrowningBar.separates` never separates a graded measurand under the sign test or the e-process;
  - `comparison.py:71` hard-codes `binary=False`;
  - `VerifyReading.held` treats a recorded level as known.
