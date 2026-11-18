# spend-served-fix: the review's fix pass, on `arc/spend-served` (cloud)

`briefs/common.md` binds you. Base: `origin/arc/spend-served` (8022198bd); push
`arc/spend-served-fix`, `wip/spend-served-fix`, `out/spend-served-fix-report`. Read
`briefs/spend-served.md` (the chain's brief) and `git diff 2975f7225 origin/arc/spend-served` first.
An independent review found the defects below; verify each at its line before building, report any
that reads false. One commit per finding, or one for the pass if each is a few lines.

## Findings
1. **A run on the account's own keys now holds back coupon money for other launches**:
   `infrastructure/llm/spend_book.py` (the set-aside hold, ~:557-571) files `key_source="host"`. A
   run whose sends draw no coupon gets a $0 job hold on a bounded coupon (`jobs/quota.py::_nothing_on`),
   so `Money.kept(job, host=True)` equals the set-aside and `read_account_wallet` subtracts it from
   the account's coupon room (`quota.py` ~:338); before the chain that figure was 0. Root: the
   set-aside is held on the key source the run's own sends draw. **Re-test:** a run on the
   account's own keys holding a bench reserve leaves a bounded coupon's room unchanged.
2. **The set-aside re-test passes trivially** (`tests/test_scoring.py` ~:3088): `set_aside(1e-9, 1)`
   reads back as 0.0 through `Budget.amount`'s 6 places, so `reserve_selection_pass` returns early and
   the assertion is `0 == approx(0)`. A set-aside the fold can see; show the test red at base.
3. **The stop record's first line still sets the cycle's bills alone against the share the book
   stopped on** (`domain/spend.py::StopSpend.meter_lines`, fed by `run_observers.py`): `exhausted()`
   stopped on billed + unreported + set-aside; the record says "this cycle $1.09 of its $2.50 share".
   The cycle line reads the book's count by part, as the campaign line now does.
4. **A budget halt's record and the set-aside**: `SPEND_BUDGET` grades the selection, so the
   set-aside is released before `close_cycle` declares the stop (`runner/entry.py` ~:315-336), and
   the new test reaches the set-aside only by calling `declare_run_stop` in an order production never
   makes. Decide what the record names there (the bench pass spent it, so it may rightly read as
   bills), and make the test follow the runner's order.
5. **Link 3's row**: replace `code-debt-cleanup.md`'s "A send a pause cuts is never filed…" with the
   report's "A cycle's sidebar spend reads its ledger alone, so a cut send reads $0.000" row (the
   filing half was true at base).
6. **Raise `scripts/complexity_ledger.py`'s `package_lines` with its reason** for the chain's growth,
   or subtract.
7. Low (fix if one edit, else file a row): `campaign_cap_lines` states `counted`, which excludes
   calls out, beside parts that list them; `campaign_count` adds the campaign's bills from every cycle
   to this cycle's unreported, out and set-aside only. The door test calls `campaign_count`, not a door.

## Owns
As `spend-served`, plus `jobs/quota.py` where finding 1's root needs it. NOT `runner/inner/spawn.py`,
`runner/termination.py`, `domain/phases.py`, `application/maintenance/**` (`l4-words-fix`), NOT
`presentation/cli/main.py`.

## Target
Net ≤ 0 `promptpotter/` lines for the pass. Run the `--against` legs and `--digests`; name every leg
and digest that moves, old→new.

## Report
Per `common.md`.
