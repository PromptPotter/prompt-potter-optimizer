# store-3: the store rows store-2f and store-2g left (cloud)

`briefs/common.md` binds you. Base: **`origin/main`** at dispatch. Rows: `docs/specs/code-debt-cleanup.md`
§ "wave-5 · store (what store-2f and store-2g left at their landing)", every row there EXCEPT "A test
home is built beside `factories.workspace`" (tests-x's). Read `briefs/store-2f.md` and
`briefs/store-2g.md` first: their invariant stands (a write never re-makes a campaign or cycle a
delete removed; a level that exists but is no directory raises an `OSError`, never recursion).

## Links, in order
1. **One "gone" from every io door** (row "A gone unit is said two ways"): a replace whose temporary
   vanished raises what a create raises (`NotFoundError` naming the unit), and
   `read_model.py::LedgerIndex.shielded` skips a projection over a gone cycle instead of swallowing
   every `Exception` and warning into it. Narrow what `shielded` catches to what its projection can
   meet; anything else raises.
2. **The anchor matches whole from the data root** (row "An anchor is matched from the right"):
   `io.py::_ANCHORS` is matched on the path relative to the data root, so a data root nested at
   `…/projects/<x>/campaigns/<D>` and a dataset slug `projects/a/campaigns/b` write. ONE source names
   the data root; census how a store and its io doors learn it today (`data_root.py`, `stores.py`)
   before choosing. No flag or callable passed so a caller steers what io owns. An inner sandbox
   (`.inner/<key>/<id>`) stays a workspace.
3. **A draft is a deleted unit** (row "A draft is re-made after its discard"): an append after
   `draft_store.py::discard` raises `NotFoundError`, and every run-data write goes through the io
   doors (`jobs/mint.py`'s raw `mkdir(parents=True)`, the CLI export's raw `mkdir` + `write_text`
   in `presentation/cli/commands/reads.py`).
4. **The small two** (rows "The campaigns dir is spelled beside its constant" and "A cycle read still
   takes (campaign, hop)"): spell `io.py::CAMPAIGNS_DIR` where `"campaigns"` is spelled, and
   `cycle_reads.py::_closed` and `pipeline_resolve.py::permitted_narrowing` take
   `at: CampaignCycle`. Fold into link 3's commit if both are a few lines.

## Owns
`promptpotter/infrastructure/store/**` (`io.py`, `layout.py`, `read_model.py`, `draft_store.py`,
`data_root.py`, `stores.py`), `application/maintenance/{landing,storage_report}.py`,
`application/jobs/mint.py`, `presentation/cli/commands/reads.py`, `application/cycle_reads.py`,
`application/pipeline_resolve.py`, and the facts for them in `tests/test_archive.py` and
`tests/test_security.py`. NOT: `application/scoring/**`,
`application/bench/**`, `application/evidence/**`, `application/intelligence/**`,
`domain/{scoring,results,verification}.py`, `application/optimizers/**`, `application/runner/**`,
`infrastructure/llm/**`, `scripts/offline_world.py`, `tests/{factories,test_identity,test_ruler,
test_scoring,test_spend,test_world_edges}.py` (two chains land there first). A holder outside Owns
is cut per `common.md`.

## Target
`promptpotter` nets ≤ 0 or each added line named. Each row's Re-test before and after. Full
`python scripts/gate.py` green, `offline_run.py --against` UNMOVED, `--digests` identical (a moved
digest is named with its cause). Every new fact holds on Windows (`common.md`'s last rule).

## Report
Per `common.md`, with a Symptoms list.
