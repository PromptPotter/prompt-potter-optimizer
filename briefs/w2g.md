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
- Git: branch `arc/<id>` off the base the brief names. ONE commit, then `git push origin HEAD:refs/heads/arc/<id>`. No PR, no force-push, no `--no-verify`. A second push goes to a new branch `arc/<id>-2`. Commit text is technical only: a conventional subject ≤72 chars, and a body ≤900 chars of bullets.

FINAL REPORT (≤400 words):
- what went, as +added/−deleted lines;
- what stayed;
- each check, before and after;
- the gate result and the equals-main result;
- rows to file, one line each.

ARC w2g: delete the `--halt-at` CLI shorthand; `--set halt_at_accuracy=X` is the one spelling. Branch `arc/w2g` off `origin/arc/w2f` (the previous link; it edited `persistence-and-state.md` too).

Row (`docs/specs/code-debt-cleanup.md`, ~397): "Adapter-kept rules that belong under the dispatcher": its `--halt-at` clause only. The row stays, rewritten without "`--halt-at` is a second door to `--set halt_at_accuracy`;". Keep the rest of the row and its re-test intact.

Owns: `promptpotter/presentation/cli/commands/{verbs.py, new.py}`, `docs/methods/verdict-resolution.md`, `docs/operations/persistence-and-state.md`, and the ledger row.

Holders:
- `verbs.py:136-144`: the `Arg("--halt-at", dest="halt_at_accuracy", …)`. Delete it.
- `new.py:102-104`: `_sets_of` folds the shorthand into the sets. It goes; its callers (`:271`, `:299`) read `args.sets` directly.
- `verdict-resolution.md:85`: "The `--halt-at` stop" becomes "The `halt_at_accuracy` stop".
- `persistence-and-state.md:41`: drop "`, `--halt-at`".
- NOT holders (they name the knob, not the flag): `verbs.py:175`, `tests/test_security.py:1700-1701`, `docs/concepts/the-loop.md:23`.
- Confirm with `git grep -n -i "halt-at\b" -- promptpotter docs tests .claude scripts paper` that nothing else spells the flag.

Named served deltas: `new --help` loses one flag. Nothing else.

Checks:
- the grep above shows only the ledger's history, if any;
- `python -m promptpotter new --help` has no `--halt-at`, and `--set halt_at_accuracy=0.9` still parses (show the parsed sets once);
- `--digests` byte-equal to main; `--against /tmp/base`: decisions and requests unmoved for every optimizer.

Commit: `refactor(cli): drop --halt-at, a second spelling of halt_at_accuracy`, or close to it.
