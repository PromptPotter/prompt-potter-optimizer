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

ARC w2e: delete `Connector.node_roles`; the draft reads each node's role from the served pipeline. Branch `arc/w2e` off `origin/arc/w2i` (the previous link in this chain; files are disjoint, only the ledger is shared).

Row (`docs/specs/code-debt-cleanup.md`, ~330-333): "`Connector.node_roles` repeats the role the served pipeline carries". Delete the row. The ledger's history line (~276) stays.

Owns: `promptpotter/connectors/{protocol.py, termnorm.py}`, `promptpotter/application/datasets/draft_build.py`, and the ledger row.

Holders:
- `protocol.py:238`: the `node_roles` field. Delete it.
- `termnorm.py:155-158`: its declaration. Delete it.
- `draft_build.py:8`: `from promptpotter import connectors` goes (its only use is `:124`).
- `draft_build.py:123-125`: `_dependencies(connector, active)` becomes `_dependencies(chain, active)`. It builds `{n.name: n.role for n in chain.nodes if n.name in active and n.role is not None}` and passes it to `pipeline_schema.py::dependencies_from_node_roles`, which is kept.
- `draft_build.py:228`: the caller passes `pipeline.chain`. `PipelineNode.role` is `pipeline_schema.py:350`, parsed from the served `node_role` (`domain/pipeline_parsing.py:389`).
- Confirm with `git grep -n "node_roles" -- promptpotter tests scripts paper` that nothing else reads it.

Named served deltas: none. The draft's `dependencies` must come out equal for a TermNorm declaration whose served pipeline marks `node_role: candidate_source` on `fuzzy_matching` and `token_matching` (TermNorm HEAD `backend-api/config/pipeline.json` does).

Checks:
- the served draft `dependencies` are equal before and after: build a draft against a TermNorm-shaped declaration with those two nodes active and their `node_role` set (a stub served pipeline in a scratch script is fine; no network), on main and on the tip, and diff;
- `git grep -n "node_roles" -- promptpotter` is empty;
- `--digests` byte-equal to main; `--against /tmp/base`: decisions and requests unmoved for every optimizer.

Commit: `refactor(connectors): drop node_roles; the served pipeline names roles`, or close to it.
