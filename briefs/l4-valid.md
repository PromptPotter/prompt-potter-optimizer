# l4-valid: one inner ruler per outer cycle; a hole and a kept cut stop with words (cloud)

`briefs/common.md` binds you. Base: `origin/main` (3784ed33b or later). Spec:
`docs/specs/code-debt-cleanup.md` § `## Held — no wave-4 chain yet`, three rows, one link each, in
this order. Run each row's Re-test at the base first; a row already closed is named, not built.
No dataset definition (`research/datasets/**`, `assets/datasets/**`) changes.

## Why
The L4 outer ranks optimizer prompts by its inner cells' held-out lift. An inner cell's δ ruler is
pinned at each OUTER ROUND's open (`runner/round.py::walk_round` → `runner/inner/tasks.py::pin_inner_ruler`
→ `CellOf`), so a later round's arms read a warmer ruler than the origin they are compared to, and a
seat whose panel the pinned ruler carries none of can never elect (seed-0 of
`promptpotter-self.dyck__2bd880`: 0 of 40 panel cells at v1052, `electable: 0` every round).

## Links
1. **An L4 inner cell elects on a ruler that may carry none of its panel**. ONE pin per outer
   cycle: its round 0's open reach, read from disk (round 0's banked opened-on stamp), so `resume`
   and a replay pin the same version. Each cell adds its own campaign's lines since that pin.
   `CellOf` is deleted: `enter_inner_cell` returns `ArmOf(walks, ruler_version=<the cycle's pin>)`,
   `ruler_clock`'s `ArmOf` arm serves a cell, and `InnerSpawnContext.ruler` carries the version alone.
   Never re-link a short ruler before an election: `a32639135` deleted `DifficultyView.link` so the race
   and the election read one ruler. Census first: every holder of `CellOf`, `pin_inner_ruler`,
   `InnerSpawnContext.ruler`, `RulerOwn`. Re-test (offline, a two-round L4 outer): every outer round's
   cells stamp one `election.ruler.version` base (the cycle's pin), and a cell whose panel the pin
   carries none of elects by its round 2 on its own lines. The re-key is accepted; name each moved
   digest and key, and wipe run data if a persisted shape moves.
2. **A kept cut and a round boundary halt with no words**, as the row says.
3. **A provider hole stops an inner cell as a bound, and halts the outer at its origin gate**. A hole
   the backend owes (an HTTP 5xx or timeout left unfilled) ends on a stop of its own with the backend's
   words (status and message), never `panel_cut`/`bound` with an empty detail. The outer re-asks a
   cell paused that way, once, before it grades its origin; only a second hole grades it errored.
   Re-test (offline, a fake backend): one injected 504 in an inner arm's cell leaves the outer running.

## Owns
Link 1: `infrastructure/store/archive_queries.py`, `application/bench/difficulty.py`,
`application/runner/inner/**`, `application/runner/round.py` (the pin hunk),
`application/intelligence/earned_blocks.py` (its match arm), `application/initialization/scoring_scope.py`,
`domain/ruler.py`, `domain/l4/**`. Links 2–3: `application/runner/round.py`, `runner/loop.py`, the
module holding `run_walks`, `runner/inner/**`, the stop-reason types in `domain/`,
`run_observers.py::declare_run_stop` only if link 2 needs it. Their tests and regenerated specs/types.

## Target
Net negative `promptpotter/` lines (`CellOf`, the per-round pin and an empty-detail stop go); link 3
may grow by its own substance, named.

## Report
Per `common.md`, plus: where the pin is read from on disk and how `resume` reads it; the `StopReason`
a hole now ends on; each moved digest and key.
