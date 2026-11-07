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

ARC w2f: delete `step-cycle`. Branch `arc/w2f` off `origin/arc/w2a` (the previous link: it regenerated the openapi/TS pair you regenerate again).

Row (`docs/specs/code-debt-cleanup.md`, ~383-386): "`step-cycle` is a second door into `start-run {step_rounds}`". Delete the row. At ~381, "`ForkCyclePayload` and `StepCyclePayload` take" becomes "`ForkCyclePayload` takes".

Operator decision (2026-11-07): delete it, and re-word `docs/decisions.md:41`. The `campaign.step` rung keeps its bounded controls (pause, origin-gate, submit-arms, skip-searchpoint) and launches nothing; advancing a round needs `campaign.run` (`start-run {step_rounds}`).

Owns: `promptpotter/domain/command_kinds.py`, `promptpotter/application/commands/launching.py`, `tests/test_security.py`, `docs/operations/persistence-and-state.md`, `docs/decisions.md` (line 41 only), comment/docstring-only edits in `promptpotter/domain/{job.py, phases.py, results.py, run_records.py}`, the regenerated `docs/specs/openapi.generated.json` + `webapp/lib/api/types.generated.ts`, and the ledger lines above.

Holders:
- `command_kinds.py:168-175`: the `STEP_CYCLE` kind and its `anyone("step-cycle")` verb. `:597-602`: `StepCyclePayload`.
- `launching.py:82` (import), `:200-216` (the `step_cycle` handler). `start_run` keeps `_launch_applier`, `launch` and `ExistingCycle`, so nothing else goes dead.
- `tests/test_security.py:110` (import), `:1788-1815`: the queued-launch replay journey used the step as its queued launch; its assertions are on replay and reaping. Rewire the setup to `StartRunPayload(campaign_id=…, cycle_id=…, step_rounds=1)` and `CallTool("start-run", …)`, with a `HostSession` principal holding `campaign.run`. Assertions unchanged.
- `persistence-and-state.md:51`: drop `step-cycle` from the verb list.
- `decisions.md:41`: re-word the "`campaign.step` advances `step-cycle`" clause as decided above. Keep the rest of the line.
- Comments: `job.py:112`, `phases.py:128`, `results.py:364`, `run_records.py:985`: "(`step-cycle`)" becomes "(`start-run {step_rounds}`)". `phases.py:278` (docstring) drops "/ `step-cycle`". Comments and docstrings only; these units are digest-reached, and only their code would move a digest.
- History, left: `CHANGELOG.md`.

Named served deltas (the only ones allowed): the CLI verb `step-cycle`, the REST route `/commands/step-cycle` and its MCP tool are gone. The openapi diff is exactly that path, its payload schema and its kind enum member.

Checks:
- `git grep -n -i "step-cycle\|step_cycle\|StepCycle" -- promptpotter tests docs webapp/app webapp/components` is empty except the ledger history and CHANGELOG;
- the openapi diff, as named above;
- `--digests` byte-equal to main; `--against /tmp/base`: decisions and requests unmoved for every optimizer.

Commit: `refactor(commands): delete step-cycle, a second door into start-run`, or close to it.
