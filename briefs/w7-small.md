# w7-small: single-owner roots, each its own link (cloud · Sonnet)

`briefs/common.md` and `briefs/w7-shared.md` bind you (read BASE FIRST). Base **`2c7dff34e`**.

SPEC: these `docs/specs/code-debt-cleanup.md` rows, one link each, in this order; read each row whole,
re-test it at the base (a row already closed is named, not built), build its Root or Door exactly:
1. **`scoring/query_loop.py::keep_cut` takes a refusal as a row** (a refusal is never a row: fix where
   the refused cell becomes a row, so `keep_cut` holds no `_budget_refusal` filter).
2. **A sandbox delete names its owner twice** (`delete_inner_sandbox` reads its own owner file).
3. **Dead producers' locks accumulate** (the sweep that releases a dead producer's job removes its
   lock). Windows: never delete a lock another handle holds; a dead producer's is not held, so prove the
   pid dead first, exactly as the sweep already does.
4. **Health fails a backend no offered dataset names** (health checks the connectors the unit's offer
   reaches, the set `offer_faults()` reads).
5. **A wiped campaign answers two failure kinds** (an absent campaign is a 404 at every campaign read;
   the `…/pipeline` read answers 400 today). Trace the door first; if its owner is a file of
   `w7-line` (`served_dashboard.py`, `live_dashboard.py`, `hierarchy_subtree.py`), cut the row.
6. **`sample_order` names two things** (the heatmap's hard-sample order is renamed for what it orders,
   in `domain/cells.py`, its served field and its webapp readers; regenerate the types).

OWNS: `promptpotter/application/scoring/query_loop.py` and the refusal's producer if it lives in no
other w7 brief's explicit path (else cut the row), `promptpotter/application/jobs/reaper.py`,
`promptpotter/infrastructure/store/campaign_store/store.py` (the delete only), the sandbox delete's
module (`infrastructure/store/layout.py::read_sandbox_owner` and its readers in `maintenance/reset.py`
and `store/account_trail.py`), the producer sweep's module unless it is `jobs/registry.py` or under
`jobs/launcher/` (w7-open's: then cut the row), `promptpotter/application/health.py` (not `webapp_bundle`:
w7-gate's), the campaign read routers for link 5, `promptpotter/domain/cells.py` and the heatmap's
webapp readers outside `webapp/components/{candidates,pipeline,node-surface}/` (w7-line's: a reader
there is one renamed field, named in the report), each row's tests (a new block in `tests/test_spend.py` if a row's test lives there).

LEAN: every link deletes more than it adds, except where a row's door must add a check (name it).
Run data: link 6 renames a served field; if a run folder file holds it, name the wipe. Report each
row's re-test before and after.
