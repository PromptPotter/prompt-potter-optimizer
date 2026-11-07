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
  - Prove equals-main (`docs/developer/offline-run.md` § Proving parity): `git worktree add /tmp/main origin/main`, run `PROMPTPOTTER_HOME=/tmp/base python scripts/offline_run.py` there, then `PROMPTPOTTER_HOME=/tmp/after python scripts/offline_run.py --against /tmp/base` on your tip, plus `--digests` on both. Report, per optimizer, whether decisions, requests and digests moved. Any move the brief did not name is a failed proof: fix it once, or stop and report it.
  - Run the brief's own checks and give each result.
- ENV FIRST (operator-approved): the image's uv may differ from `pyproject.toml`'s `required-version`; fetch that exact uv into a temp dir (`python3 -m pip install -q --target /tmp/uvpin uv==<pinned>`) and put `/tmp/uvpin/bin` first on PATH. Run `npm ci` in `webapp/` (from the lockfile) so the gate's web lane runs. No other installs. No paid LLM calls. Never touch a live server.
- Read repo files with Read/Grep/`git show`. Shell reads may be hook-refused.
- Git: branch `arc/<id>` off the base the brief names. ONE commit, then `git push origin HEAD:refs/heads/arc/<id>`. No PR, no force-push, no `--no-verify`. A second push goes to a new branch `arc/<id>-2`. Commit text is technical only: a conventional subject ≤72 chars, and a body ≤900 chars of bullets.

FINAL REPORT (≤400 words):
- what went, as +added/−deleted lines;
- what stayed;
- each check, before and after;
- the gate result and the equals-main result;
- rows to file, one line each.

ARC w2i: `defaults.yaml` stops naming a connector, a model list and a budget for every definition. Branch `arc/w2i` off `origin/arc/w2b` (the previous link in this chain; files are disjoint, only the ledger is shared).

Row (`docs/specs/code-debt-cleanup.md`, ~290-302): "Two owners, two values, for what a definition leaves unsaid": first step S only. The row stays, rewritten with step S done and the connector-template work left; keep its re-test current.

Owns: `promptpotter/assets/datasets/defaults.yaml`, `promptpotter/assets/datasets/email-tagging/dataset.yaml`, `promptpotter/assets/datasets/justlogic/family.yaml`, `docs/manual/03-first-campaign.md`, and the ledger row.

Holders:
- `defaults.yaml:5` `backend_type: termnorm`, `:6-7` `available_models`, `:40` `budget: {rounds: 8}`: delete.
- Add `backend_type: termnorm` to `email-tagging/dataset.yaml` and `justlogic/family.yaml`, the two install definitions that relied on the default. Confirm with `git grep -n backend_type -- promptpotter/assets/datasets` that every other shipped definition names its own, and with `git grep -nE "available_models|rounds:" -- promptpotter/assets/datasets` that every shipped definition overrides both.
- `03-first-campaign.md:24`: "a dataset's calls run on a backend unless it names another connector. The default is …TermNorm-excel" becomes "a dataset names its connector; the shipped datasets name TermNorm-excel".

Named served deltas: an install definition without `backend_type` now meets the typed "backend_type missing" 422 (the row's intent). Nothing else.

Checks:
- every shipped definition's merged document is byte-equal before and after: dump each install and research dataset's merged definition through the dataset reader's own merge, on main and on the tip, and diff (empty);
- `--digests` byte-equal to main; `--against /tmp/base`: decisions and requests unmoved for every optimizer.

Commit: `refactor(datasets): defaults.yaml stops naming a connector`, or close to it.
