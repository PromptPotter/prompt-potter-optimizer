# w11-spend: area 05 to its Target, the nested send and the money fold (cloud · Opus)

`briefs/common.md`, `briefs/w10-shared.md` and `briefs/w11-shared.md` bind you. Dispatched once
w11-store landed: it moved nothing of `read_model.py`'s money fold, so the fold is yours now. SPEC:
`briefs/map.md` § 05 and the debt doc's `## 05` section. Target: one `Budget` at four positions (asked,
admitted, standing, spent); spent is a fold of spend facts; only the run's spend book halts a run; an L4
inner cycle spends under its root's book; a send is filed once, on the run that sent it; the payer is
resolved per cell by `wallet.py::sending_key`; spend is the provider's bill.

Links, deletion first:
1. **An outer run's cost is a fold over its inner ledgers.** Three rows share one root: a nested run's
   send is filed once, a nested spend book is wired beside the cell that nests it, and a cycle's sidebar
   spend reads its ledger alone, so a cut send reads $0.000. Decision 2: the spawning pass names the
   hold's kind, and an inner cell's is its connector's name. `mirrored` goes.
2. **The money fold leaves `store/read_model.py`** (about 54 importers re-point) for the spend module that
   owns it, and `served_dashboard.py::spend_counts` stops building a full `SpendBook` per cycle.
3. **A cycle's running job is found two ways**: one.
4. **The sub-cent money rule** (decision 15): three significant digits under a dollar in `usd_text`,
   which already feeds the webapp's `fmtUsd`.
5. **An inner L4 run inherits its outer's overshoot switch** (decision 23).
6. **A landing that drops other accounts' run data refunds them.** On 2026-11-26 a deploy wiped a
   stranger's campaign: the operator granted their coupon again by hand. Leave this one to the
   operator's choice, which is pending: read `briefs/w11-spend-refund.md` if it exists, else file the
   row with both options (refund + notify at the landing, or migrate their runs) and build nothing.
7. **Census:** `pricing.py`, `jobs/shares.py`, `jobs/quota.py`, `spend_book.py`, `domain/spend.py`, plus
   area 05's recovered items. Each gets a verdict. RM362 INVESTIGATE (a CAPO arm admitted on its round
   ceiling) is answered from the offline world's ledgers.

Held, paid: the default host pin's twins. Held, unmeasured: the wallet read under the admission gate.

OWNS: `infrastructure/llm/spend_book.py`, `wallet.py`, `pricing.py`, `send_pacing.py`,
`store/wallet_store.py`, `account_spend.py`, `store/read_model.py`'s money fold,
`application/jobs/quota.py`, `shares.py`, `install_spend.py`, `capacity.py`, `free_tier.py`,
`head_to_head_price.py`, `domain/spend.py`, `served_dashboard.py::spend_counts`.

DONE WHEN: `git grep -c mirrored -- promptpotter` is 0 · `git grep -n "SpendBook(" --
promptpotter/application` names no zero-budget book · pausing an outer run mid inner round makes the
inner row read its cut sends as unreported (a test through a world door) · the report traces every
dollar a surface shows to one charge record · keys: name each move.
