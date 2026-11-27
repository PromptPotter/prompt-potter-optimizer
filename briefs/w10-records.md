# w10-records: area 01 to its Target — θ off the round's statement, C1's tail (cloud · Opus)

`briefs/common.md` and `briefs/w10-shared.md` bind you. SPEC: the debt doc's `## 01 · Records spine and the
round envelope` section, its Target above all: a round is a neutral envelope, no field naming a layer, a
critique or an elimination rule (§0).

1. **θ off the round's statement** (roadmap § "θ off the round's statement, before an L4 campaign banks a
   result"; operator-ordered, decided): v0.8.21 decides a round on pairings (`PairedReading`), yet θ still
   rides every round's envelope: the scoreboard's order (`RoundResult.ranked`), `Election.ruler`,
   `ArmFit.theta*` and their caveats. θ stays the race's instrument (PoBB's cut and row choice) and L4's
   level series (`round_closed`'s `level_series`); the round carries its pairings alone. Restate
   `docs/architecture.md` §0.5's case for the δ ruler first, in that doc, so the code follows the text.
   Every reader re-points (about 60 Python files and 14 webapp files read `theta`, `.ranked` or `.ruler`):
   a reader that ordered by θ orders by the pairings or by what the race served; the browser derives
   nothing (root E). Run data reshapes: wipe and name each record.
2. **C1's tail** (row **Every pass is a recorded pass (root C1)**): the race's catch-ups are scheduled by
   `catch_up.py::CatchUpPool` beside `query_loop.py::run_walks`; they become walks of the one loop.
3. **Census:** the section's Unexamined modules (`domain/records/run_records.py` 1,428 lines and the top
   hotspot, `results_health.py`, `arm_reading.py`, `wire_record.py`, `export.py`, `candidate_diff.py`,
   `shared/errors.py`), plus area 01's recovered items.

OWNS: `domain/**` and `shared/**` (less `domain/job.py`, `command_kinds.py`, `command_events.py`,
`refusal.py`, which w10-jobs owns), `scoring/catch_up.py`, `scoring/query_loop.py`, and every θ reader's
re-point across `promptpotter/` and `webapp/`. Not yours beyond a re-point: `application/jobs/**`,
`application/commands/**`, `infrastructure/identity/**`, `application/account.py`.

Leave to their own chains: **The layering is fictional at runtime** and **Names collide across layers**
(unless the θ move closes a knot by itself; then say so).

DONE WHEN: `git grep -c "CatchUpPool\|BACKFILL\|\.overlap\b" -- promptpotter` is 0 · no `theta` field under
`RoundStatement` (`git grep -n "theta" -- promptpotter/domain/results.py` names none) · each model nested
in `RoundStatement` names its one reader (report the list).

Keys: every key moves; name each old→new. LEAN: θ leaves fields and readers; `promptpotter/` shrinks.
