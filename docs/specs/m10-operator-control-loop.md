# M10 Operator Control Loop — mini-milestone

**Status:** specced. Mini-milestone carved from M12 Track 3 / 3.5.

## What this covers

Launch, stop, resume, and fork a campaign from the webapp; a freshly minted campaign appears live with no browser reload; each cycle's `l1_generate` / `l1_critique` / `l2_context` revision is readable + diffable in the UI.

**Why a mini-milestone:** the M10 exit gate is reached by many short tune-run-review cycles on the four optimizer meta-prompts. Pulling the single-operator write surface forward from M12 makes the loop smooth *before* the M10 gate. Single default tenant, no auth — multi-user hardening stays [`0001-m12-control-plane.md`](../adr/0001-m12-control-plane.md).

**Prerequisite:** [`state-sync-cleanup.md`](state-sync-cleanup.md) Phases 1–3 (identity collapse → per-cycle `state.json` → ledger-driven `GET /api/v1/live`) land first; Track D's SSE channels reuse `derive_live_state(ledger)`.

## Open tracks

- **A — §0 amendment.** `Control-remote` becomes the fourth I/O kind in `docs/architecture.md` §0. Docs-only PR; lands first per pre-flight Q4/Q7. Host is the long-lived uvicorn process — there is no separate daemon. Draft text in original spec history.
- **B — `JobRegistry`.** One in-memory registry owned by uvicorn; `Job` carries `job_id`, `campaign_id`, `cycle_id`, `kind`, `state`, timestamps, asyncio task handle. On-disk projection at `.promptpotter/.runtime/jobs/{job_id}.json`. Restart marks `running` → `interrupted`. N concurrent jobs; per-cycle `CycleEventLog` keeps them ledger-isolated. Shared launch core extracted from `cmd_new` / `cmd_resume`; persistence contract unchanged (`CycleEventLog.append`).
- **C — Control routes.** `POST /api/v1/runs` (`new` + `resume` kinds); `POST /api/v1/runs/{job_id}/control` (`stop` writes `.runtime/stop.flag`; `pause` = stop + mark resumable); forks route already shipped at `active.py:140`. Launch route is non-blocking by construction.
- **D — SSE channels.** `GET /api/v1/runs/stream` (registry lifecycle — kills the hard reload). `GET /api/v1/campaigns/{id}/cycles/{id}/ledger/stream` (per-cycle ledger push form of existing `?since=N` poll). Each channel is a Display-kind ledger subscriber. Polling stays as fallback + reconnect-gap path.
- **E — Webapp control surface.** Launcher form (dataset dropdown + Launch button); start / stop / resume / fork buttons; live job list fed by the registry SSE channel; `WorkspaceProvider` + `CycleStreamProvider` subscribe to SSE.
- **F — Meta-prompt read panel.** `GET /api/v1/campaigns/{id}/cycles/{id}/meta-prompts` returns resolved `l1_generate` / `l1_critique` / `l2_context` / `l3_plan` template content + hashes; `?against={other_cycle_id}` returns unified diff. Read-only — edits stay in files via the meta-campaign skills.

## Exit gate

- `docs/architecture.md` §0 defines `Control-remote` (Allowed / Not-allowed).
- `JobRegistry` runs N concurrent campaigns; job index on disk; restart reconciles.
- Webapp launches / stops / resumes / forks end-to-end; no reload after mint.
- Meta-prompt panel shows each cycle's revisions + cross-cycle diff.
- Four Control-remote routes in `presentation/CLAUDE.md`'s sanctioned-mutation allowlist.

## Code surface

| Area | Files |
|---|---|
| Loop entry (reused) | `application/runner/entry.py::run_optimization`, `runner/loop.py` |
| CLI launch bodies (extract shared core) | `presentation/cli/commands/{new,resume}.py` |
| Persistence ingress | `infrastructure/ledger.py::CycleEventLog.append`; `application/run_observers.py::RunCallbacks` |
| Stop control | `presentation/api/routers/active.py:221`; `session.stop_check` |
| API routers | `promptpotter/main.py`; `presentation/api/routers/{active,campaigns,datasets}.py` |
| Webapp reactivity | `webapp/lib/{poll,workspace}.tsx`, `webapp/lib/api.ts`, `webapp/components/dashboard/StopButton.tsx` |
| Prompt snapshots | `campaigns/{cycle_id}/prompts/optimizer_prompt/{hash}/` |

## Out of scope

Auth + multi-tenant isolation + multi-user hub + whitelabel + chat-panel launcher → [`0001-m12-control-plane.md`](../adr/0001-m12-control-plane.md). Distributed / out-of-process workers → post-M13.
