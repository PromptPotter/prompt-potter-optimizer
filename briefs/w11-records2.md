# w11-records2: area 01, round 2 — the records spine settles before L4 spends (cloud · Opus)

`briefs/common.md`, `briefs/w10-shared.md` and `briefs/w11-shared.md` bind you. SPEC: `briefs/map.md`
§ 01 and the debt doc's `## 01` section (10 rows). This chain runs ALONE in area 01: every other chain
rebases on it, so it is dispatched once w11-spend and w11-runner have landed.

Target: a round is a neutral envelope (PROPOSE, MEASURE, SELECT, ADAPT), its headline derived, and no
field names a layer, a critique or an elimination rule (§0). Every ledger fact is a `CycleRecord` in a
module of its own under `domain/records/`, read by kind. No record holds a value another derives, and no
field is without a reader. A rename wipes run data, so shapes settle before L4 spends.

Links, central and reductive first:
1. **`run_records.py` is a module of 38 records and ~35 non-records** (1,428 lines, the top hotspot).
   - Each record goes to its own module under `domain/records/`, read by kind.
   - Each non-record goes to the layer that owns it.
   - Re-point every importer; add no re-export.
2. **A closed round banks its election twice**, **`Election.deciding_pair` admits a pair that is not
   read**, and **a round file stores a served word**: no record holds a value another derives, and no
   field is without a reader.
3. **Round health folds through three carriers**, and **what a child moved over its parent is computed
   twice**: one fold each.
4. **RM283: θ still rides the envelope** (`RoundResult.ranked`, `Election.ruler`, `ArmFit.theta*`).
   This is against §0, and it is operator-ordered before L4 banks a result. θ moves to the record the
   elimination rule owns. This reshapes run data: count it, wipe it, and name it.
5. **The layering is fictional at runtime** (the `type_cycles` baseline is 58 at HEAD) and **names collide
   across layers** (six basename pairs, `CycleFacts` twice): cut the knots link 1 leaves, and rename
   each collision once.
6. **An unknown error category reads as UNKNOWN**, and **backend wire records drop undeclared keys**:
   work each on its own root.
7. **Census:** `results_health.py`, `arm_reading.py`, `wire_record.py`, `export.py`, `candidate_diff.py`,
   `shared/errors.py`. Give each a verdict. Then answer: does every model nested in `RoundStatement`
   name its one reader?

OWNS: `domain/**` and `shared/**`, less what other areas claim in `briefs/map.md`. Re-point importers
anywhere, imports only. No other chain in flight is in area 01.

DONE WHEN:
- `run_records.py` is gone, or holds records alone.
- There is no `theta` field under `RoundStatement`.
- `BASELINE["type_cycles"]` falls by your count.
- Equals-main names every move.
- The keys and wipes are named, with counts.
