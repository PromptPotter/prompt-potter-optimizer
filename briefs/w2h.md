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

ARC w2h: delete four served fields no surface reads: `CouponStatus.state` / `.held` / `.left` and `LaunchRefusal.provider`. Branch `arc/w2h` off `origin/batch/2` (it carries every earlier wave-2 arc, so the generated pair is current).

Row (`docs/specs/code-debt-cleanup.md`, ~1099-1100): the sub-bullet "`CouponStatus.state` / `.held` / `.left` and `LaunchRefusal.provider` are served with no reader." Delete the sub-bullet; its parent list stays.

Owns: `promptpotter/application/jobs/quota.py`, `tests/test_spend.py`, `webapp/lib/test-fixtures.ts`, the regenerated `docs/specs/openapi.generated.json` + `webapp/lib/api/types.generated.ts`, and the ledger sub-bullet.

Holders:
- `quota.py:358-361`: the `provider` field. `:371`: the `provider=refused.provider` argument. The error classes keep `.provider`, which `refusal_words` reads.
- `quota.py:388` (`state`) and `:394-398` (`held`, `left`); the construction at `:441, 450, 451`. `wallet.state` stays read for `headline` (`:443`).
- `tests/test_spend.py:2974`: `(refused.reason, refused.provider)` becomes `(refused.reason, box_paid.value.provider)`; the provider decision is still asserted on the raise. `:2990`: `(status.left.usd, status.state)` reads `account_wallet(spent_out)` (`.headroom.usd`, `.state`), the model `coupon_status` projects. A setup rewire; the assertion stays.
- `webapp/lib/test-fixtures.ts:111, 120-121`: the fixture fields go (excess properties fail `tsc`).
- Confirm no reader: `git grep -nE "LaunchRefusal\b|CouponStatus|coupon_status|launch_refusal\b" -- webapp promptpotter tests scripts paper`, and that `IngestConversation.tsx` reads only `refusal.reason` / `.sentence`, `AccountKeysTab.tsx` only `headline` / `routes`.
- `draft_build.py:109` serves `LaunchRefusal` inside the draft wire; that file is untouched, only its served shape changes.

Named served deltas (the only ones allowed): `/auth/coupon` loses `state`, `held` and `left`; every `launch_refusal` loses `provider`. The openapi diff is exactly those four properties.

Checks: the openapi diff as named; `--digests` byte-equal to main; `--against /tmp/base`: decisions and requests unmoved for every optimizer; the full gate (web lane included).

Commit: `refactor(quota): drop four served fields no surface reads`, or close to it.
