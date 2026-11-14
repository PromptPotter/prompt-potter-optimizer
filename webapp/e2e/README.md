# e2e — the browser walk

Operating and extending the Playwright suite. Why this layer is tested the way it is belongs
to [`../CLAUDE.md`](../CLAUDE.md) § Testing posture; this page is the runbook.

```bash
cd webapp
npm run e2e                 # build the app, then walk it (walk + cold)
npm run e2e:fast            # skip the build — iterating on specs
npm run e2e:watch           # the same, in a browser you can watch
npm run e2e:ui              # step, time-travel, pick locators
npx playwright test --project=walk -g "theme"    # one slice
```

First time on a machine: `npx playwright install chromium`.

**`gate.py` runs `cold`, `walk` and `touch`**, the walk (and `touch`, the same specs on a 390px touch
phone) against the `walk-world` check's run
(`scripts/gate.py::_playwright`); `spend` needs money. `walk` reads no operator campaign:
`PP_E2E_WALK_WORLD` (resolved to an absolute path and handed to `serve.mjs` as `PP_E2E_WORLD`) names a run — potter's straight run, with the L4 fixture's outer campaign nested in its data root
(`offline_run.py --straight --l4`), is what `gate.py --only walk-world` leaves
at `.gate_cache/walk-world/<key>/potter/data`, under that check's own key so a world is walked only
by a tree whose engine built it, and building one removes every other world beside it — and `walk_world.py` serves a throwaway copy of it (its projects and the inner runs' sandbox registry) whose
account agreed to the live terms, with the `offline` backend. Unset, `playwright.config.ts` asks
`gate.py --walk-world` for this tree's world, which builds it when absent.

**The backend is a posture too, argued like auth** (`serve.mjs --backend=offline|live`). A run
without `spend` serves the throwaway world `offline`: `serve_app.py` answers `TERMNORM_ENDPOINTS` with
`scripts/offline_world.py::FakeBackend`, so a check-in reads one schema on every machine. A desk
TermNorm at :8000 once turned a CI-red spec green at the push hook.

## Three worlds, because the app has two populations

| Project | Workspace | Covers |
|---|---|---|
| `walk` | a copy of `PP_E2E_WALK_WORLD`, **read-only** | every view against real campaigns and rounds — the only world where Dashboard, Compare, Verify and Files have anything to draw. Issues no write. |
| `touch` | the walk's | the walk's specs at 390px with `hasTouch` + `isMobile`, so `(pointer:coarse)` matches; a spec that walks the desktop layout says so in its `test.skip(isMobile, …)` |
| `cold` | a throwaway `PROMPTPOTTER_HOME` | the zero-campaign path a new account meets: consent gate, empty reads, dataset picker. |
| `spend` | the same throwaway world | two campaigns taken to a measured round, each under `PP_E2E_BUDGET_USD`. **Real money** — `PP_E2E_SPEND=1`, and the dataset's backend must be up. `run.spec.ts` is the browser ingest path, `l4.spec.ts` the recursion; each spec's header states why it is minted the way it is. |

Two servers rather than one, because `PROMPTPOTTER_HOME` is bound at import and one process
cannot hold both. **Neither binds 8001** — that port is the operator's.

**A run boots only the servers its projects need** (`playwright.config.ts::runs`, read off
`--project`): `walk` or `touch` starts the walk server, `cold` and `spend` the throwaway one, and no
`--project` at all — `npm run e2e` — starts both. No walk opens the operator's workspace.

**One spec inside `cold` brings a THIRD**, and it is not a fourth world: `onboarding-signed-in`
needs an identity that is signed in and is not the host, which neither server above can produce
because both start `serve.mjs --auth=off`. So `fake_issuer.ts` runs the same script with
`--auth=on --backend=offline` — same preflight, same fault tee, same interpreter — over a workspace it mints and
deletes per file. Auth posture is the whole difference, which is why `serve.mjs` refuses to start
without it rather than inherit one.

| Variable | Default | For |
|---|---|---|
| `PP_E2E_PORT` | `8123` | the walk server, started only when `walk` is among the projects run |
| `PP_E2E_COLD_PORT` | `8124` | the throwaway server |
| `PP_E2E_FAKEAUTH_PORT` | `8125` | the auth-CLOSED server `onboarding-signed-in.spec.ts` raises for itself |
| `PP_E2E_COLD_HOME` | `<tmp>/promptpotter-e2e` | where the throwaway world lives; it must sit under the system temp dir or `reset_world.py` refuses to wipe it |
| `PP_E2E_WALK_WORLD` | `gate.py --walk-world` | the run `walk` serves a copy of, at `PP_E2E_WALK_HOME`, with the `offline` backend; never reuses a server already up |
| `PP_E2E_WALK_HOME` | `<tmp>/promptpotter-e2e-walk` | where that copy lives; the gate hands each run its own, so two gates never walk one copy |
| `PP_E2E_BASE_URL` | — | walk a server already up (your `:8001`, a deployed box); the walk server then never starts |
| `PP_E2E_SPEND` | — | `1` arms the spend tier |
| `PP_E2E_DATASET` | `email-tagging` | what the spend tier starts from; must be a dataset the ingest seam resolves rows for |
| `PP_E2E_BUDGET_USD` | `0.05` | the spend tier's USD ceiling, applied DOWNWARD only — the account's own allowance often binds tighter |
| `PP_E2E_KEEP` | — | `1` keeps the throwaway workspace instead of resetting it |
| `PP_E2E_DROP_CACHES` | — | `1` resets the paid caches too, so the next spend pass re-records them |
| `PP_E2E_TAPE` | — | `1` REQUIRES the caches to answer — a pass that pays real money then fails |

## The second pass is free, and `PP_E2E_TAPE` is what proves it

The three caches
[`config/paths.py::SHARED_CACHE_DIRS`](../../promptpotter/config/paths.py) names are
content-addressed and survive the reset (`reset_world.py`), and **the optimizer's replies
chain** — round 1's messages carry round 0's results — so with all three warm the same campaign
re-runs to the same candidates, the same scores and the same winner, for nothing.

**Read the split off `SpendRollup`, which already separates it:** `total_used_usd` is the bill and
is what the ceiling caps, `total_incurred_usd` prices cache hits too. A replayed pass reads
`incurred > 0, used ≈ 0`, and cannot trip the budget however much the recording cost.

**Only one of the two paths can carry the tape's floor, and the reason is structural.** The
rendered target prompt is INSIDE `node_configs`, so whatever authors it decides whether anything
beneath it replays. `l4.spec.ts` mints from a dataset, whose origin prompt is a file — measured at
100%, 100%, 80.4% replayed across three passes, the round reached in ~2s against ~2min cold.
`run.spec.ts` mints through the check-in, where an **agent writes the origin prompt**: one call
that missed re-cut all fifteen origin cells under it, and the pass came in at 36%. That path
reports and does not assert until the authoring call is made deterministic — a floor drawn from a
coin flip is a flaky failure in the one tier that costs money.

**The floor that IS asserted is deliberately loose (0.5).** The dataset path is reproducible
without being identical — each run leaves inner-cycle residue the next re-measures around, which is
what took the third pass to 80.4%. It is there to catch a tape that has stopped answering at all; a
bar tuned to whatever last passed asserts nothing.

## Three rules for anything added here

Each binds one helper in `harness.ts` and is argued where it binds:

- **Discover, never name** (the file header, `richestCampaign`) — ask the API what exists and
  assert the app renders THAT; where a world cannot answer the question, `test.skip` with the
  reason. A spec naming a campaign id asserts the operator's disk rather than the app.
- **The console guard is the point** (`BENIGN`, `EXPECTED_4XX`) — it is `auto`, so no spec can
  forget it, and it stays empty of anything the app itself emits.
- **A hash-only `goto` does not remount** (`open`) — navigate with it, never `page.goto`.
