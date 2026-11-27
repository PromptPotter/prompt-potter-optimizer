# w11-store: area 02 to its Target, one keyed line read (cloud · Opus)

`briefs/common.md`, `briefs/w10-shared.md` and `briefs/w11-shared.md` bind you. SPEC: `briefs/map.md`
§ 02 and the debt doc's `## 02` section.

Links, deletion first:
1. **A line's fold is stated once and read by one keyed read** `(hop, scope, fold)`. There are 21 `scan_`
   functions in `ledger_scan.py` (1,082 lines): they become one read over the line plus each fold, stated
   once. Its stale `:823/:827` citations go with it.
2. **A protocol with one implementation** goes. Leave `read_model.py`'s money fold where it is: area 05's
   chain moves it once you have landed.
3. **The feed's version spans a whole store**: a mounted subtree keeps its ETag while another campaign of
   its store appends.
4. **A landing key re-derives per call**, plus `maintenance/built_key.py`, a 5-line generated constant
   committed 31 times. Each is either derived, or written once by the landing and never committed again.
5. **Census:** `campaign_store/store.py` (734 lines, 5th-highest churn, no row), `store/stores.py` (the hub
   of the bench knot), `store/layout.py`, `maintenance/archive_maintenance.py`, `store/io.py`,
   `store/data_root.py`, plus area 02's recovered items. Each gets a verdict.

OWNS: `infrastructure/store/**`, less the wallet, account and dataset stores, the stores w11-access owns,
`ReplayFeed`, and `read_model.py`'s money fold; `infrastructure/projections/**`, `ledger.py`,
`run_state.py`, `run_scope.py`, `holders.py`, `producer_lock.py`, `application/maintenance/**`,
`domain/sample.py`, `cycle_index.py`, `cycle_paths.py`, `config/paths.py`.

Held as filed: the Windows replace (decision 8), `write_json`'s indent (decision 12), the landing's wait
on live producers (it needs `update.sh`, the operator's).

DONE WHEN: `git grep -c "for span in spans" -- promptpotter` is 1 · `ledger_scan.py` holds no per-fold
scan · a mounted subtree's ETag is unchanged by another campaign's append (a test through a world door) ·
`store.py`'s verdict is in the report · keys: name any move. LEAN: `promptpotter/` shrinks.
