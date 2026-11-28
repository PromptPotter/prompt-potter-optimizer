# w11-spend2: area 05, round 2: a send is filed once, and the outer folds its inner ledgers (cloud · Opus)

`briefs/common.md`, `briefs/w10-shared.md` and `briefs/w11-shared.md` bind you. SPEC: `briefs/map.md`
§ 05 and the debt doc's `## 05` section (13 rows). w11-spend cut its central row because the row's
holders sit in areas 02 and 11. Both have now landed, so this chain owns all three areas' spend paths
at once. It runs before L4 (map § 10: each re-key or wipe here voids a paid L4 run).

Target: the Target line of `## 05`, in particular "a send is filed once, on the run that sent it" and
"an L4 inner cycle spends under its root's book".

Links, deletion first. Work each row along its root:
1. **An outer run's cost is folded from copies its inner runs write onto its ledger**, with **A nested
   run's send is filed once, on the run that sent it** and **A nested spend book is wired beside the
   cell that nests it**. One root: the outer's ledger folds take its inner ledgers
   (`stores.py::inner_sandbox_store`), so `SpendBook.mirror` and `TokenUsageRecord.mirrored` go. Re-test
   as the row says: pause an outer run mid inner round.
2. **A cycle's sidebar spend reads its ledger alone, so a cut send reads $0.000**, and **An unbilled
   run's spend reads three ways**. One served spend headline that every surface renders; an empty
   series says so instead of drawing nothing.
3. **A measurand's USD reads four fixed places beside the three-digit rule**: one money text.
4. **The wallet read under the admission gate grows with the account's charges**: the read is a fold
   of what it needs.
5. **`StepUsage` re-declares `TokenAccount`'s parts**: one shape.
6. **A free account's shipped dataset opens refused**: re-test it on the auth-on offline world, as the row
   says.
Leave open, each for its reason:
- **The default host pin is a trial with no verdict**: tune 3 (paid).
- **A landing that drops other accounts' run data refunds nobody**: it waits on the operator's choice.
- **dbllmbench's cells send on the box's key read off `settings`, not `sending_key`** and **A remote
  backend's cells ask the bound account**: w11-connectors3's, since their holders are in `connectors/`
  and `infrastructure/backend.py`.
- **A mint's job names its hop before its cycle share lands**: w11-jobs3's, beside `Job.hop`.

Census: `infrastructure/llm/pricing.py`, `jobs/shares.py`, `jobs/quota.py`, `spend_book.py` and
`domain/spend.py`. Each has not yet been examined; give each a verdict.

OWNS: area 05's files, plus the spend paths in `infrastructure/store/**` (`ledger_scan.py`'s spend folds,
`stores.py::inner_sandbox_store`), `application/hierarchy.py`, `served_dashboard.py`, the live dashboard
projection, `records/money.py`, `run_observers.py`, `runner/inner/tasks.py`'s spend lines, the spend
surfaces in `webapp/`, and their tests. Other chains running at the same time: w11-jobs3 (`jobs/**` less
area 05's files), w11-connectors3 (`connectors/**`, `infrastructure/llm/**` less `pricing.py`) and w11-web3
(webapp acts and focus). Wipe only after counting, and name each wipe.

DONE WHEN:
- Each row is re-tested through a door, before and after.
- `git grep -c mirrored -- promptpotter` is 0.
- Equals-main names each move, and the keys are named.
- `python scripts/gate.py` is green.
