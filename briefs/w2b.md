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

ARC w2b: delete `Campaign.backend_id` / `backend_url` and `DraftFile.reused_origin_id`. Branch `arc/w2b` off `origin/batch/1`.

Rows (`docs/specs/code-debt-cleanup.md`), both deleted:
- "`Campaign.backend_id` / `backend_url` store the connector twice and a URL nothing reads" (~303-307);
- "`checkin_draft.py::DraftFile.reused_origin_id` is written and read by nothing" (~1318-1321). Its either/or resolves to "it goes": nothing reads it.

This is a run-data reshape (`Campaign` and `DraftFile` are `Fate.RUN`): no migration, no reader of two shapes. The orchestrator wipes run data at landing.

Owns: `promptpotter/domain/{campaign.py, checkin_draft.py}`, `promptpotter/application/jobs/mint.py`, `promptpotter/application/mask/load.py`, `promptpotter/application/datasets/{draft_campaign.py, ingest.py}`, and the two ledger rows.

Holders:
- `campaign.py:307-308`: the `CampaignEdit` keys.
- `campaign.py:324-326`: the two fields and their false comment ("Every later launch of any of its cycles runs here").
- `mint.py:576-577`: where they are written.
- `mask/load.py:216`: `campaign_terms(campaign.backend_id)` becomes `campaign_terms(campaign.connector)`. `Campaign.connector` is `declaration.environment.connector`, the same value `Session.backend_id` carried at mint, so it is equal for every minted campaign.
- `checkin_draft.py:70-71`: the field.
- `draft_campaign.py:157`.
- `ingest.py:177`: `overrides: dict[str, Any] = {"reused_origin_id": origin_id}` becomes `{}`; `origin_prompts` is still seeded on the next line.
- NOT yours: `Session.backend_id` (`wiring.py:274`, the tests' `loop_session` / `seat` / `Session` uses), the `/backends/{backend_id}` path parameter, connector `_preflight`'s `backend_url` parameters. Leave every one.

Named served deltas: none.

Checks:
- `git grep -nE "backend_url|backend_id" -- promptpotter/domain/campaign.py` and `git grep -n reused_origin_id -- promptpotter tests` are empty;
- a `mask` read (dials lens) on a campaign the offline run minted answers the same terms on main and on the tip;
- `--digests` byte-equal to main; `--against /tmp/base`: decisions and requests unmoved for every optimizer.

Commit: `refactor(campaign): drop the twice-stored connector and dead draft field`, or close to it.
