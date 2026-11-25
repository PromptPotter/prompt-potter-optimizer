# Architecture review: the measuring loop's five slices

Read at `87cf6680a` (worktree `wt-arch`). CONFIRMED = checked in code; PLAUSIBLE = reasoned, unproven.

## Verdict

1. The backbone is sound. The 15–17 package files are the three landed commits (15, 17, 14), not five slices: slice 1 touched 4 package files and slice 3 touched 1. `run_walks` is still the one door, and capo, gepa and levi were UNMOVED throughout.
2. The breadth is slices 2, 4 and 5. Plumbing by design is 3 of 12, 6 of 17 and 4 of 14 files; 3 of slice 5's 14 are unrelated potter-w1 work.
3. The rest is three facts with too many holders: the race protocol (three modules), the rows a walk reads (six holders), and a walk's budget and depth (mirrored into the race).
4. Highest-leverage reshape: **one decision loop**. A race answers one `decide` (the stops, and the (arm, row) calls to send), and the phase reads one queue of calls sent; `batch` becomes a state of `rolling`. PLAUSIBLE; the offline run is its proof.
5. It is code-only, reshapes no run data, and starts after ml-6 and l4-key-b. Nothing found here needs the W1 wipe.

## 1. Seam map

Slices 1–3 are read from the pre-squash commits `a758e4dee`, `ef003dc4c`, `e3fe7d3c2`. Counts are package files.

| Slice | Files | (a) substance | (b) reader of a moved fact | (c) plumbing | (d) record reshape |
|---|---|---|---|---|---|
| 1 rows hook | 4 | `query_loop.py`, `search_point_scorer.py` (budget) | `nodes.py`, `potter/race.py` (protocol mirror) | none | none |
| 2 informative + refit | 12 | `rasch.py`, `race.py`, `pobb.py` | `nodes.py`, `round.py`, `potter/members.py`, `config_map.py`, `mint.py`, `preflight.py` | `knobs.py`, `pipeline.yaml`, `couplings.py` | none |
| 3 in flight | 1 | `query_loop.py` | none | none | none |
| 4 per_round | 17 | `race.py`, `pobb.py`, `query_loop.py`, `search_point_scorer.py` | `nodes.py`, `domain/sample.py`, `round.py`, `mint.py`, `preflight.py`, `optimizer_manifest.py` | `knobs.py`, `pipeline.yaml`, `couplings.py`, `behaviour_keys.json`, `ansi.py`, `phase_acts.py` | `run_records.py` (`sample_idx`), `EliminationCut` |
| 5 rolling | 14 | `race.py`, `query_loop.py`, `ledger_scan.py` | `nodes.py`, `domain/sample.py`, `pobb.py`, `round.py` | `knobs.py`, `pipeline.yaml`, `behaviour_keys.json`, `resolved_schemas.json` | `EliminationCut` (potter-w1) |

Slice 5's other three files are potter-w1's: `election.py`, `dispatch/layout.py`, `dispatch/schemas.py`.

**(b) clusters**

- **B1. The race protocol is spelled in three modules** (CONFIRMED). `nodes.py` changed in all three commits to mirror `choose_rows` (in slice 2 also for B2).
  - Holders: `scoring/query_loop.py::BlockRace`; `optimizers/nodes.py::Race.rolling`, `::Race.choose_rows`, `::Race.blocks`; `domain/sample.py::Untaken`, `::Stretch`, `::ReadOrder`, `::Dispatch`.
  - `Race.blocks` returns `self` in all three races (`potter/race.py:229`, `capo/members.py:334`, `gepa/members.py:282`): an identity door. `top_up` is a real switch (capo and gepa answer `None`).
  - Owner: one protocol module in `application/scoring/`; `nodes.Race` inherits it.
- **B2. The rows a walk reads, in order** (CONFIRMED). Slice 2 moved five readers off the sampler's fixed draw; slice 5 added a re-file rule.
  - Holders: `query_loop.py::Walk.dataset[:n]`, read from outside at `round.py:914,941`; `domain/arms.py::Measuring.sample_order`, re-filed on each move (`round.py:916-937`); `live_dashboard.py::declared_sample_order`; `ledger_scan.py::StandingRounds.reads` (taken rows only, dropped at the round's close); `nodes.py::RoundSamples.read_on`; `round.py` `asked`.
  - Owner: the walk states its order (taken, then untaken) through the phase's turn callback, keyed by arm.
- **B3. A walk's budget and depth** (CONFIRMED).
  - Holders: `Walk.budget`; `potter/race.py::PoBBRace._budgets` (rebuilt each decision, `:271`); `_Walking.depth` (`:313`); `Untaken.left`; `EliminationContext.total_queries`; `sample_total` on both sample records; `round.py:931` (`"of"`).
  - Slice 5 deleted `PoBBCheck.n_samples` for this reason.
  - Owner: the walk, handed to the race in the decision's view.
- **B4. Where a coupling is refused** (CONFIRMED, closed). Slice 2 added `preflight.py::refuse_couplings` and a call in `mint.py`; slice 4 moved both into `optimizer_manifest.py::_select`.

## 2. Missing seams, ranked

Each names the future mechanisms it makes local. Crossover lands in none of them: it is a proposer, and this review found no measuring-loop seam it needs.

1. **Two schedulers share `_ScoringPhase`** (CONFIRMED structure). The turn wheel (`advance`, `:910`) and the call queue (`roll`, `:859`) are joined by 11 branches on `rolling`. `Walk` carries a copy of the mode (`:424`) and positional state: `boundary`, `owed`, `waits`, `enter_block`, and `reorder`, which re-seats three dicts by index (`:447-464`). Local after: any new dispatch rule; held-out reads and L4 inner runs are PLAUSIBLE (both ride `run_walks`, unchecked).
2. **A decision is two calls** (CONFIRMED). `close_block` always ends in `open_block` (`:954`), so `close` and `choose_rows` are one event. `BlockRace.block_size` is read by no scheduler code, only `round.py:931-932` for a record. `per_round` is encoded as a reordered pool plus a count (`Stretch`). Local after: a different elimination rule.
3. **The row policy is welded to PoBB** (CONFIRMED). `informative`, `_hand_out` and `_settles` live in `potter/race.py`; capo's and gepa's races inherit the pass-through. `_hand_out` needs only a `FieldRead`. Local after: a different elimination rule, new optimizers as switches.
4. **The dashboard holds one declared order per round** (CONFIRMED structure; wrong axis PLAUSIBLE). `live_dashboard.py:648-649` overwrites one list from whichever arm filed last. Under `per_round` each arm has its own rows.
5. **"Replay" names three things** (CONFIRMED). Decision records (`Replayed`), archived answers (`ReplayFeed`, built once per walk at `search_point_scorer.py:233`), and the read order (`StandingRounds.reads` → `run_walks(reads=)` → `_ScoringPhase.replaying`). Whether per-walk feeds duplicate state was not examined.
6. **The race mirrors the walk** (B3). Closes with finding 2.

## 3. Proposed reshapes

**R1. One decision loop (the largest excision).** PLAUSIBLE.
- Target: the race answers `decide(view) -> Decision(stops, sends)`. `sends` is (arm, row) pairs; `view` holds each walk's taken rows, untaken rows, calls out and budget. The phase keeps one queue of calls sent, read in order `walk | launch | arrival`. The trigger is "queue empty" (`batch`) or "each read" (`rolling`). A no-race pass is one decision that sends every row.
- Deletes: the turn wheel; `Walk.boundary`, `owed`, `waits`, `enter_block`, `rolling`, positional `reorder` (cells key by sample id); `Untaken`, `Stretch`; `BlockRace.close`, `choose_rows`, `block_size`; `Race.blocks`; `PoBBRace._budgets`.
- Stays: `top_up`; the walk's cell life; the per-row `StopRule` door (`Walk.judge`, `launch_horizon`), a second cut door this does not merge.
- Open: the press's calls ahead of a decision are batch-only today (`:473`, `past = 0 if self.rolling`). Either a press sends from the race's next rows under every trigger, or the branch stays.
- Closes B1 and B3. Backlog rows closed: none confirmed; **A locked-in leader's caught-up cells reach the scoreboard only at the round's close** gets a natural home (PLAUSIBLE).
- Run data: none. `docs/architecture.md` names none of the deleted symbols.
- Size: about 7 files; the estimate is deletion-heavy but unmeasured until the press question is settled.
- Saved: the `nodes.py` mirror in slices 1, 2, 4 and 5; `domain/sample.py` in 4 and 5; slice 5 becomes a trigger and a read order.
- Proof: the offline run's legs UNMOVED against ml-6's home.

**R2. A walk's order is keyed by arm and stated by the phase.** PLAUSIBLE.
- Target: the phase's turn callback hands the walk's order; `round.py` stops reading `walk.dataset`. `live_dashboard.py` keys the order by arm. `Measuring.sample_order` stays: it holds untaken rows that `readout.py:399` prints and no sample record names.
- Deletes: the `filed` set and half the `moved` rule (`round.py:913-924`).
- Closes: none. Run data: none (the served dashboard state changes; `types.generated.ts` regenerates).
- Size: about 5 files, small. Saved: slice 5's `round.py` touch.

**R3. The row policy leaves PoBB.** CONFIRMED location.
- Target: one function in `application/scoring/` takes the view, an optional `FieldRead` and the knobs, and returns `sends`. The knobs stay on the eliminator node's config (architecture §0), as a group any eliminator includes.
- Moves about 150 lines; deletes little. Closes: none. Run data: none while knob paths stay. Saved the slices: nothing.

**R4. The read order rides the phase.** `reads=` leaves `run_walks`; the phase asks the store. Closes: none. Saved: slice 5's `round.py` line.

## 4. What not to change

- **`run_walks` as the one door**: two callers (`round.py:948`, `search_point_scorer.py:176`).
- **The walk's cell life** (`launch`, `take`, `bank`, `end`): slice 3 was one package file.
- **Knob confinement**: the five knobs are read at `pobb.py:131` and `race.py:197-198` only.
- **Decision replay**: `replayers.py` is 59 lines with five `Replayed` kinds; `EliminationCut.cells` names keys.
- **Lazy per-row walk state** (`QueryLoopState.filed`, `free`): 318 → 59 ms per the slice-4 body.
- **`RowsRan` on `MeasureOpened`**: a state the system enters and reports.
- **Couplings asked once in `_select`.**

## 5. Sequencing

- All four are one chain, in order R1 → R3 → R4 → R2: each edits `round.py`, `nodes.py` or `query_loop.py`.
- The chain starts after ml-6 lands (`query_loop.py`, `round.py`, `potter/`). R1's move of types out of `domain/sample.py` also waits for l4-key-b.
- None reshapes a ledger record or an address, so none depends on the W1 wipe. Behaviour keys move by package code.
- spend-tomb (`scoring/passes.py`, `domain/spend.py`) and the web chains share no file with the chain.

## Appendix: Symptoms

| `file:line` | Symptom | Suspected root |
|---|---|---|
| `scoring/query_loop.py:766-769` | the phase copies its mode onto every walk | two schedulers (R1) |
| `scoring/query_loop.py:473,622,692,828,839,849,911,958,1025,1044,1102` | branches on `rolling` | same |
| `scoring/query_loop.py:447-464` | `reorder` re-seats three dicts by position | cells keyed by index, not sample id |
| `scoring/query_loop.py:823-825` | assert "a decision moved a call already out" | the race returns an order where it means a send |
| `scoring/query_loop.py:954` | `close_block` always calls `open_block` | one decision, two protocol methods |
| `scoring/query_loop.py:123` | `block_size` unread by the scheduler | protocol member kept for a record |
| `optimizers/potter/race.py:229`, `capo/members.py:334`, `gepa/members.py:282` | `blocks` returns `self` | `Race` / `BlockRace` split |
| `optimizers/potter/race.py:271-274,313` | the race rebuilds each walk's budget and depth | B3 |
| `optimizers/nodes.py:562-574` | `Race` re-declares `BlockRace` members | B1 |
| `domain/sample.py:63-88` | scheduler types in the sample module | B1 |
| `runner/round.py:914,941` | the round reads `walk.dataset[: walk.n]` | B2 |
| `runner/round.py:916-924` | `moved` and `filed` decide when to re-file an arm | B2 |
| `infrastructure/projections/live_dashboard.py:648-649` | one order per round, last arm wins | B2: a global for a keyed subject |
| `application/served_dashboard.py:342-343` | comment guards against "a walk off the declared order" | B2 |
| `runner/round.py:957` | `reads=standing.reads.get(...)` passed through | R4: the caller steers what the phase owns |
| `optimizers/nodes.py:373-392` | `RoundSamples` has five views of one draw | B2 |
| `domain/arms.py:175`, `domain/cells.py:126` | two fields named `sample_order` (an arm's walk order, the heatmap's hard-sample order) | name collision |
| `tests/test_spend.py` | 6,711 lines; the walk's tests live here (32 walk references) | a name that stopped describing its contents |
| `scoring/query_loop.py:1211-1218` | `keep_cut` filters the refused cell back out of `results` | a refusal taken as a row |
