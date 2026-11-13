# life-2b: life-2's build 3, on the landed wave (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `life-2b`, arc `arc/life-2b`. Read `briefs/life-2.md` too: its decisions stand, and this brief only adds to them.

- **Base:** `origin/land/wave-5` at `805feffd6`: v0.8.20 plus the symptoms batch, cfg-2 and rk-opt-10. Your `<base sha>` in common.md is this commit. Build on it, never beside it.
- **Start from `origin/wip/life-2` (`1a751a9d7`):** its four links, built on `e24dfda95`, and stopped at its second failed proof. Its report is `report.md` on `origin/out/life-2-report`; read it whole. Replay the four links onto the base, one commit each, and resolve cfg-2 by hunk in `presentation/cli/commands/new.py` and `application/commands/launching.py` (cfg-2 is landed now, so its hunks stand as they are).
- **Spec:** life-2's ledger rows, plus the two life-2's report files under "Rows to file". The re-tests are life-2's.

## Decisions (orchestrator; build them, do not reopen them)

- **The API's startup runs its sync preamble off the loop, once.** `main.py::lifespan` lands the data root, builds the identity bundle, checks and tells, prints the banner, attaches the registry and takes `this_producer`'s held lock. All of that runs in ONE `asyncio.to_thread`, which returns what the lifespan keeps. The two piecemeal offloads build 2 added go into it. A test starts the real lifespan (`TestClient(app)` as a context manager, which runs it on a loop) under link 1's guard. It fails on the wip tip and passes after. pytest then proves what only playwright saw.
- **The embedded door is async, like the run it opens.** `embedded_run.py::enter` and `host_dispatcher` become `async def`, landing the root off the loop, so a host inside a running loop enters the one door. A sync host calls it through `asyncio.run`, as it already does to run a campaign. Every caller moves: `tests/factories.py`, `presentation/teleprompter.py`, `paper/bbeh-comparison/bbeh_potter_runner.py`, `tests/test_security.py`. There is no sync twin.
- **A `CommandCall`'s door is required.** The internal calls in `commands/launching.py` name theirs; the HOST default goes.
- **Origin readiness is checked once, at the admission.** `launcher/checkin.py::open_checkin`'s second `assert_origin_ready` and `CheckinStart.open`'s second `draft_served` fetch go.
- **A client is closed only by its opener:** the bbeh runner's second `aclose` under `suppress(Exception)` goes.
- **LEAN:** life-2 grew the inner layers +305 net. Bring them to net ≤ 0 where a line can go. Name each surviving surplus line's reason in its commit body.

## Links (one commit each, in order)

1–4. life-2's four links, replayed on the base (build 2's two offloads stay folded into link 1 until link 5 moves them).
5. The lifespan's one offload and its real-lifespan test; the embedded door async; the door required; readiness once; the bbeh runner's close.

## Beside you (cloud, running now)

- **rk-dec** shares two files, each by hunk:
  - `application/runner/mint.py`: yours are `open_launch`'s money move, plus `onto_fork` and `_rebase_held` going async. rk-dec's is the origin's door, where `establish_campaign_origin` goes.
  - `application/runner/entry.py`: yours are `_mint_and_rebase_fork` going async and `_cut_rebase_fork`. rk-dec's are round 0's door and the last round decided once.

  Edit only your hunks and name each in the commit body.
- **Main also holds an unpushed commit** touching `application/health.py`, `deploy-linux/update.sh`, `scripts/gate.py::_install_digest`, the end of `tests/test_security.py`, `docs/research/external-constraints.md`, `docs/specs/roadmap.md` § Optimizer tuning and the ledger's `batch/31 · box` row. Leave those hunks alone.

## Proof (beyond common.md)

- `gate.py` full, `playwright` included, green at every link from 5 on. Links 1–4 may carry the known playwright fail, named in their bodies.
- Equals-main: every leg UNMOVED (no decision moves); name any digest move.
- The five-door line from life-2, now with the embedded door entered from inside a running loop.
