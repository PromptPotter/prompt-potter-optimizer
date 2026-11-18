# spend-served: every door serves the count the book stops on (cloud)

`briefs/common.md` binds you. Base: `origin/main` (2975f7225 or later). The rows are in
`docs/specs/code-debt-cleanup.md`; quote each row's title in the commit that closes it and delete it.
One link per row. Root law (`CLAUDE.md` `<one-budget>`; memory: spend = the provider's bill): a cap
stops on a count, so every door serves THAT count, by part, and a send never drops out of it.

## Symptom (observed on the desk, 2026-11-18)
C0 `promptpotter-self.run__265c4d` halted `spend_budget` inside round 1 while `dashboard` and
`set-limits` served "$1.09 spent" of $2.50. The book counted $1.09 billed + $1.11 unreported (52
lapsed cells) + $0.18 set aside for the bench = $2.38. The kept cut's words landed with `l4-valid`
(on main): what is still open is that no door serves the set-aside, the stop record's campaign line
reads the bills alone, and a send a pause cuts is never filed at all.

## Links
1. **The bench's set-aside lives only on the run's book, so no door serves it** (and the open half
   of **The cap halts on money its served reading omits**): `SpendBook.set_aside` is set in memory
   by `runner/bench.py::reserve_selection_pass`, `runner/loop.py:238` and `runner/entry.py:317`;
   `exhausted()` counts it, `spend_book.py::cycle_count` serves 0 ("short only the set-aside").
   Action: the set-aside lands where the book's fold reads it back (a ledger fact or a hold on the
   charge book: pick the one `read_model.py::Money` already folds, say why), so `cycle_count`,
   `exhausted()` and every door read one count. Delete the "short only" carve-out and its docstring.
   **Re-test:** the C0 shape (billed + lapsed + a bench reserve) served by `dashboard` and
   `set-limits` equals what `exhausted()` reads at the halt, part by part.
2. **The stop record's campaign spend reads the bills alone**: `run_observers.py::declare_run_stop`
   fills `StopSpend.campaign` from `metered_spend`, so its cap lines omit unreported money. Action:
   `StopSpend` carries the book's `SpendCount` (campaign level), and `meter_lines` words its parts
   once, where `spend_book.py:568` already words them. **Re-test:** a halted run's stop record
   names its lapsed cells and its set-aside.
3. **A send a pause cuts is never filed, so a paused inner run reads $0.000**: read the row whole.
   Action: a send a stop cuts is filed at the cut as sent-unreported (bounded, as
   `MeteredSpend.sends_unreported` already renders it), and its late usage files when it returns;
   never dropped. Find the ONE place a send is cancelled (grep the cancel path from the pause first;
   name it), not each caller. **Re-test:** the row's.

## Owns
`infrastructure/llm/spend_book.py`, `domain/spend.py`, `application/run_observers.py`,
`infrastructure/store/read_model.py` (the money fold), `application/served_dashboard.py`, the
set-aside lines of `runner/bench.py`, `runner/loop.py`, `runner/entry.py`, the send-cancel path link 3
finds, their tests. NOT `runner/termination.py`, `runner/round.py`, `domain/phases.py`,
`runner/inner/spawn.py`, `domain/cycle_result.py` (the `l4-words` chain), NOT
`store/dataset_access.py`, `domain/layer_fold.py`, `datasets/prompts.py`, `runner/inner/tasks.py`
(the `panel-rule` chain). A file outside Owns that the root needs: report it, do not edit it.

## Target
Net ≤ 0 `promptpotter/` lines across the chain (link 1 deletes a carve-out). Run the `--against`
legs; name every leg and digest that moves, old→new. Name any run-data shape that moves (a wipe).

## Report
Per `common.md`.
