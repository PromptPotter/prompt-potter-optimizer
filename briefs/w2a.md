You run ONE small deletion arc of PromptPotter's code-debt plan, in a cloud session. Work autonomously to the end; nobody answers questions mid-run.

PROCESS: `docs/developer/refactor-arcs.md` (§ Size and scope, § The brief items 1–7, § Landing) binds you. Your arc was cut from the ledger by a census; the brief below carries its holders, so trust it and grep only to confirm. Line numbers in it are origin/main 33ca03a8f's; grep if one has drifted. Your spec is the ledger rows named below, in `docs/specs/code-debt-cleanup.md`. These bind you too: the "Decided once" rows of § The excision pass and § The quantum-leap pass, and the root `CLAUDE.md` § Non-negotiables.

RULES
- Equals main, except the changes named below: the wire, the optimizer treatment digests, the served responses, the read cadences.
- A holder the brief missed that sits in a file outside Owns is cut: one line in your report, never built in. A decision the brief did not settle is not built ahead: take the reading closest to main, and name it.
- No backward compatibility: no shim, fallback, alias, breadcrumb comment or second mechanism. A run-data reshape gets no migration and no reader of two shapes; the orchestrator wipes the run data at landing. Never run `land-data --reset`.
- Delete the ledger rows you ship, in the same commit. Fix every doc and layer `CLAUDE.md` you make stale, but not the root `CLAUDE.md`: report its stale lines.
- An invariant you introduce gets what holds it, so reverting the change fails it. That is a test only where `tests/CLAUDE.md`'s charter admits one; a deletion usually needs none. Do not over-polish: a standing problem you notice outside the brief is one line under "rows to file", never fixed.
- Proof. Your proof alone puts your arc into its batch; the batch is reviewed before it reaches main.
  - While iterating, run `python scripts/gate.py --changed`.
  - Before pushing, run the full `python scripts/gate.py`. It must be green; otherwise say exactly what is red and why.
  - Prove equals-main (`docs/developer/offline-run.md` § Proving parity): `git fetch origin main && git worktree add /tmp/main origin/main` (the image's clone can hold a stale `origin/main`), run `PROMPTPOTTER_HOME=/tmp/base python scripts/offline_run.py` there, then `PROMPTPOTTER_HOME=/tmp/after python scripts/offline_run.py --against /tmp/base` on your tip, plus `--digests` on both. Report, per optimizer, whether decisions, requests and digests moved. Any move the brief did not name is a failed proof: fix it once, or stop and report it.
  - Run the brief's own checks and give each result.
- ENV FIRST (operator-approved): the image's uv may differ from `pyproject.toml`'s `required-version`; fetch that exact uv into a temp dir (`python3 -m pip install -q --target /tmp/uvpin uv==<pinned>`) and put `/tmp/uvpin/bin` first on PATH. Run `npm ci` in `webapp/` (from the lockfile) so the gate's web lane runs. No other installs. No paid LLM calls. Never touch a live server.
- Read repo files with Read/Grep/`git show`. Shell reads may be hook-refused.
- Git: branch `arc/<id>` off the base the brief names. ONE commit, then `git push origin HEAD:refs/heads/arc/<id>`. No PR, no force-push, no `--no-verify`. A second push goes to a new branch `arc/<id>-2`. Commit text is technical only: a conventional subject ≤72 chars, and a body ≤900 chars of bullets. The body ends with three lines the orchestrator reads instead of your log: `Proof: gate <N checks green | RED: what>; equals-main <decisions/requests unmoved ×N, digests byte-equal | what moved>`, `Cut: <holders cut, one phrase each | none>`, `Rows: <rows to file, one phrase each | none>`.

FINAL REPORT (≤400 words):
- what went, as +added/−deleted lines;
- what stayed;
- each check, before and after;
- the gate result and the equals-main result;
- rows to file, one line each.

ARC w2a: delete `FileSink`, the local Langfuse shadow. Branch `arc/w2a` off `origin/batch/1`.

Rows (`docs/specs/code-debt-cleanup.md`):
- "`tracing/file_sink.py::FileSink`, the local Langfuse shadow, has no reader and runs on every cycle" (~1045-1053): delete the row.
- "ET, inside U3: tracing translates the ledger twice." (~508-514): drop only the `FileSink` clause ("`tracing/file_sink.py::FileSink` has no reader", and "and the file sink" in the Action). ET's projection work stays.
- ~820: "§ What a cycle writes to disk (`FileSink`) and" goes; "§ One record shape for every scoring pass" stays.

Owns: `promptpotter/infrastructure/tracing/{file_sink.py (delete), bridge.py, events.py}`, `promptpotter/application/initialization/wiring.py`, `promptpotter/infrastructure/store/data_root.py`, `docs/operations/observability.md`, `docs/specs/roadmap.md` (the M10 cell only), the regenerated `docs/specs/openapi.generated.json` + `webapp/lib/api/types.generated.ts` (via `scripts/build_specs.py` / `scripts/build_ts_types.py`), and the ledger hunks above.

Holders:
- `file_sink.py`: the whole file.
- `bridge.py:21` (import) and `:66`: `sinks: list[TraceSink] = [FileSink(store_base_dir, hop)]` becomes `[]`. The two sink loops then iterate nothing; leave them. Do not touch `run_observers.py`.
- `events.py:20-28`: `generate_observation_id` and `dataset_item_id` lose their only caller; delete them and their now-unused imports (`uuid`, `UTC, datetime`, `stable_hash`). `langfuse_sink.py`'s `dataset_item_id` is a local keyword, not this function.
- `wiring.py:288`: the comment "drops only the cloud sink, never ``FileSink``" becomes "drops the cloud sink".
- `data_root.py`: drop the rows for events (~382), trace (~384), observation (~385-390), score (~391) and the dataset mirror (~392). KEEP `state.json` (~383, `LangfuseSink`'s id map). `Leaf.DATASET` (~302) and its `LEAF_INFO` row (~320) then have no row: delete both. `Leaf.TRACE`'s words (~325) "prompts, the langfuse trace" become "prompts, the Langfuse id map". The served `Leaf` enum loses `dataset`, so regenerate the pair.
- `observability.md:3`: drop the `tracing/file_sink.py` cite and the local mirror from the sink list. `:24`: "The local shadow `cycles/{cycle_id}/langfuse/` mirrors what is sent, with an id map in `state.json`" becomes "the id map is `cycles/{cycle_id}/langfuse/state.json`".
- `roadmap.md` (~170, M10 cell): the "`FileSink`" scope item and the "delete `FileSink` (yes)" decision go. Nothing else in roadmap.md.
- Not stale: `persistence-and-state.md:81`.

Named served deltas (the only ones allowed):
- the storage report's `leaves` loses `dataset` (it read 0 everywhere);
- a cycle's file listing loses the `langfuse/` mirror files.

Checks:
- `git grep -n "FileSink" -- promptpotter docs` is empty except history lines in the ledger, if any;
- after the offline run with Langfuse off, `find /tmp/after -path '*langfuse*' -type f | wc -l` is 0;
- `--digests` byte-equal to main; `--against /tmp/base`: decisions and requests unmoved for every optimizer.

Commit: `refactor(tracing): delete FileSink, the local Langfuse shadow`, or close to it.
