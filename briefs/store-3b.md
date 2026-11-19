# store-3b: store-3's fix pass — "gone" by state, not by fault type (cloud)

`briefs/common.md` binds you. Base: **`origin/arc/store-3`** (509e7b54d; read `briefs/store-3.md`,
whose invariant stands). Rebase it onto `origin/main` first (main moved: `package_lines` and the
ledger conflict; keep every reason, the ceiling ends at the measured count), then build below; push
to `arc/store-3b`. An independent review of store-3, run on Windows with probes, found these
CONFIRMED. All go in, one commit per numbered fix at most, small ones folded.

## Fixes
1. **BLOCK: `read_model.py::LedgerIndex.shielded` decides "gone" by the fault's type.**
   (a) `except NotFoundError: return` skips every `NotFoundError`, also a standing cycle's
   ("archive no longer holds samples…" after a purge or reclaim, `cycle_bank` "names no bank still
   held"), so log.md / review.md / export.json go stale with no `derived_file_stale` (main warned).
   (b) `FileUnreadableError` (not an `OSError`: a sibling cycle's whole-but-invalid index line read by
   `_load_sibling_indices`) now raises into the append whose line already landed: every resume
   crashes at the next close (main warned). (c) A gone cycle with an `OSError` fault still appends
   its warning into the deleted cycle. Decision (tested by the reviewer on a scratch copy): catch
   `(OSError, NotFoundError, FileUnreadableError)` and warn; the warning's `append_jsonl` is wrapped
   in `except NotFoundError: return`, since io's anchor refuses that append only when the cycle is
   really absent. Anything else (a `ValueError`) raises. **Re-test:** facts: a standing cycle's
   `NotFoundError` and a `FileUnreadableError` warn; a gone cycle receives nothing.
2. **`io.py`'s gone is read off the parent's path shape** (around `io.py:283`): a replace whose
   temporary vanished while the cycle STANDS answers `NotFoundError("cycle 'cy' is gone")` for
   `<cycle>/index.json` but a raw `FileNotFoundError` for `<cycle>/rounds/round_0001.json`
   (trigger: a reset's `**/.*.tmp` sweep, `data_root.py`, in the window before the replace). Raise
   the anchor's error only where the unit is absent; otherwise re-raise the original error.
3. **`io.py::_plain` normalizes but does not make absolute** where `_long_path` does: a relative path
   and an 8.3 short path re-make a deleted campaign. Make it absolute (`os.path.abspath`).
4. **`io.py::_cut_unterminated` opens `r+b` raw**: a delete between the write lock and that open
   leaks a raw `FileNotFoundError`. Through the same gone answer.
5. **`campaign_store/store.py`'s `root.mkdir()` is a raw mkdir of a campaign dir**, so the draft
   row's re-test (`git grep -n "\.mkdir(" -- promptpotter` names no run-data path) fails as written;
   through the io door (a campaign's exclusive create stays exclusive). `_created`'s docstring names
   drafts beside campaigns and cycles.
6. **`cycle_reads.py`'s CLI export is now created 0600** (mkstemp) on POSIX, where it followed the
   umask: a file a person points the export at follows the umask; decide where that belongs (the
   export's door, not every run-data write) and say it.
7. **`cycle_reads.py::_closed` takes `on: CampaignCycle`**; the repo spells it `at:` (37 sites) and
   the brief said `at:`. Rename.
8. **Your own gate was red**: `test_spend.py::test_a_set_limits_raced_by_a_delete_answers_not_found_and_leaves_no_campaign_dir`
   builds its account outside the data root (`_web_account(tmp_path, …)`), so io reads no anchor.
   The account mints at the fact's data root (the root `own_home` sets). Touch only that fact's
   setup in `test_spend.py` (other chains edit that file).
9. `tests/test_archive.py`'s `@pytest.mark.skipif(sys.platform == "win32", …)` on
   `test_a_cycle_deleted_under_a_replace…` goes: the reviewer ran it on Windows, it passes. The
   `test_security.py` reap skip stays (Windows refuses the `rmtree` under a held `producer.lock`).

## Not in this pass (report as a row to file, full text)
The data root has two sources: io reads `paths.py::user_data_root()` on every miss while a store
carries `Stores.projects_root`; where they disagree every door fails open silently, and in the suite
they always do (`conftest.py::own_home` sets `<tmp>-home`, `built_stores` is `workspace(tmp_path)`),
so the anchor is off in every fact but the four `homed_stores` ones. Root: one binding source
(`build_stores` refuses a root outside the data root) with test homes built under it. It rides with
tests-x's "A test home is built beside `factories.workspace`".

## Owns
store-3's Owns, plus `promptpotter/infrastructure/store/campaign_store/**` and the one fact's setup
in `tests/test_spend.py`. NOT `application/{scoring,bench,evidence,intelligence,optimizers,runner}/**`,
`domain/{scoring,results,verification,hierarchy}.py`, `infrastructure/llm/**`, `application/jobs/reaper.py`,
`webapp/**`, `tests/{factories,test_identity,test_ruler,test_scoring,test_world_edges}.py`.

## Target
Each fix's Re-test before and after. Full `python scripts/gate.py` GREEN (no red left as a row),
`offline_run.py --against` UNMOVED, `--digests` identical. `promptpotter` nets ≤ 0 or each added
line named. Every new fact holds on Windows.

## Report
Per `common.md`, with a Symptoms list.
