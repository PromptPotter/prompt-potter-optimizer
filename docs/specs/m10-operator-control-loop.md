# M10 Operator Control Loop — mini-milestone

**Status:** Specced. Mini-milestone carved from M12 Track 3 / 3.5.
**Goal:** Launch, stop, resume, and fork a campaign from the webapp; a freshly
minted campaign appears live with no browser reload; each cycle's
`l1_generate` / `l1_critique` / `l2_context` revision is readable + diffable in
the UI.
**Why a mini-milestone:** the M10 exit gate is reached by many short
tune-run-review cycles on the four optimizer meta-prompts. That loop runs
today on the CLI + file tree + manual reloads. Pulling the single-operator
write surface forward from M12 makes the loop smooth *before* the M10 gate,
not after. It lands **alongside** the M10 framework tracks
([`m10-prompt-iteration-framework.md`](m10-prompt-iteration-framework.md)),
not after them.

## Scope

**In:** in-process background-job execution, the `Control-remote` I/O kind,
launch / stop / resume / fork over HTTP, SSE reactivity, a webapp control
surface, a read-only meta-prompt panel. **Single default tenant, no auth.**

**Out — stays M12** ([`m12-control-plane.md`](m12-control-plane.md)): auth,
multi-tenant path isolation, the multi-user hub, whitelabel, the chat-panel
launcher. **Out — post-M13:** distributed / out-of-process workers; splitting
the API from a worker fleet.

The split principle: **this mini-milestone = the single-operator write
surface; M12 control plane = the multi-user / SaaS hardening on top.** It maps
to the operator's own framing — "MS Word": every machine self-hosts and runs
its own loop; the hub where people connect is the M12 layer.

## Relationship to other specs

- **`state-sync-cleanup.md` is a prerequisite.** Its Phases 1–3 (identity
  collapse → per-cycle `state.json` → ledger-driven `GET /api/v1/live`) must
  land first; Track D's SSE channels are the streaming form of that `/live`
  endpoint and reuse its `derive_live_state(ledger)` helper. Sequence
  state-sync Phases 1–3 ahead of Track B.
- **This mini-milestone supersedes two `state-sync-cleanup.md` non-goals** —
  "Not a control plane" and "Not a daemon." That spec already defers those
  mutations to M12; this carve-out brings them to M10. No edit to
  `state-sync-cleanup.md` is required — the supersession is recorded here.
- **`m10-prompt-iteration-framework.md`** keeps its own ≤500 LOC / "no new I/O
  kind" envelope. This mini-milestone is a separate spec with its own (larger)
  envelope precisely because it *does* add an I/O kind.

## Tracks

### Track A — §0 amendment: the `Control-remote` I/O kind

`docs/architecture.md` §0 already foreshadows this: *"M12's orchestrator
daemon will add a fourth (Control-remote) on the same persistence ingress."*
This track promotes that sentence into a full fourth-kind definition.

- The pre-flight gate (CLAUDE.md Q4 sub-rule + Q7) requires §0 to be amended
  **before** any control code lands. Track A is therefore implementation
  **PR #1** — a docs-only change to `docs/architecture.md` §0.
- The "orchestrator daemon" is **in-process**: the long-lived uvicorn process
  *is* the host. There is no separate process. The §0 text must say so —
  drop any "separate daemon process" reading.
- Amendment draft text is in the [Appendix](#appendix--§0-amendment-draft).

### Track B — In-process job execution (`JobRegistry`)

The core blocker: `await run_optimization()`
(`application/runner/entry.py:47`) blocks its caller for the whole campaign.
The CLI tolerates this; an HTTP request cannot.

- **`JobRegistry`** — one instance, owned by the uvicorn process, holding N
  `Job` entries. A `Job` carries `job_id`, `campaign_id`, `cycle_id`,
  `kind` (`new` | `resume`), `state` (`queued` | `running` | `stopping` |
  `finished` | `failed` | `interrupted`), `started_at`, `finished_at`,
  `error`, plus a handle to the `asyncio.Task` running `run_optimization()`.
- **Shared launch core.** Extract the orchestration body of
  `cli/commands/new.py::cmd_new` and `resume.py::cmd_resume` into a launch
  function callable from both the CLI and the API — `_mint_fresh_session` /
  `load_session` / `_orch_run_optimization` are reused verbatim, no logic
  copy. The persistence contract is unchanged: the run still writes only
  through `RunCallbacks` → `CycleEventLog.append`, so "entry points MUST NOT
  write campaign artifacts directly" still holds.
- **On-disk job index** — `.promptpotter/.runtime/jobs/{job_id}.json`. The
  registry is in-memory state, but the index is its disk projection so the
  operator and an AI assistant can read what's running without calling the
  API (architecture.md "everything material lives on disk"). The API restart
  path reconciles the index against reality (below).
- **Process-restart behavior.** `asyncio.Task`s die with the process; jobs do
  **not** auto-resume. On startup the registry scans `.runtime/jobs/`, marks
  every `running` entry whose process is gone as `interrupted`, and the
  operator resumes it from the webapp. No work is lost — incremental
  per-round persistence already guarantees a hard kill loses nothing.
- **Concurrency.** N jobs run concurrently; each touches its own per-cycle
  `CycleEventLog` (`infrastructure/ledger.py`), so there is no cross-job
  ledger contention. The shared `MeasurementArchive` is content-addressed;
  concurrent writes to the same content-hash dir are a known low-risk item
  (see Risks) — archive write coordination is left to the M12 multi-tenant
  pass, which touches the archive anyway. M10 tuning use is typically 1–2
  concurrent jobs.
- `active_session.json` keeps its meaning — the **operator's focused
  session** (what bare `resume` picks up, what the webapp defaults to). "What
  is running" is now the `JobRegistry` index. Two different questions, two
  surfaces; no conflict with state-sync-cleanup, which owns the focus pointer.

### Track C — Control + launch API routes

The sanctioned `Control-remote` mutations. All four are listed in
`promptpotter/presentation/CLAUDE.md`'s allowlist of sanctioned mutations.

| Route | Effect |
|---|---|
| `POST /api/v1/runs` | Body `{dataset, kind: "new"}` → find-or-create campaign, mint session + root cycle, create a `Job`, spawn its task. **Returns immediately** with `{job_id, campaign_id, cycle_id}` — never awaits the loop. The HTTP equivalent of `python -m promptpotter new <dataset>`. |
| `POST /api/v1/runs` | Body `{kind: "resume", campaign_id, cycle_id}` → resume an interrupted / finished cycle. The HTTP equivalent of `python -m promptpotter resume`. |
| `POST /api/v1/runs/{job_id}/control` | `{action: "stop" \| "pause"}`. `stop` writes `.runtime/stop.flag` — the existing **Control-local** mechanism (`active.py:221`); the loop polls it via `session.stop_check`. `pause` = stop + mark the cycle resumable. |
| `POST /api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/forks` | Already shipped (`active.py:140`). Unchanged — kept in the control surface. |

The launch route is non-blocking by construction: it constructs the `Job`,
schedules the `asyncio.Task`, and returns. Control-remote triggers Persistence
and Control-local; it never bypasses them.

### Track D — SSE reactivity

Two Server-Sent-Events channels. SSE over WebSocket — the traffic is
server→client telemetry plus occasional control, and SSE reconnects for free.

| Channel | Pushes |
|---|---|
| `GET /api/v1/runs/stream` | **Registry channel** — job lifecycle: created, state change, finished. This is what kills the hard reload: a freshly minted campaign emits a registry event, the webapp adds it to the list, no reload. |
| `GET /api/v1/campaigns/{id}/cycles/{id}/ledger/stream` | **Per-cycle channel** — ledger events as appended; the push form of the existing `GET .../ledger?since=N`. |

- Each SSE channel is a new **Display-kind** ledger subscriber that fans
  events to connected HTTP clients. It is read-only and writes no campaign
  artifact — consistent with the Display invariant. SSE therefore rides the
  existing ledger-subscription mechanism; **no sidecar** (pre-flight Q5).
- Polling stays as the fallback for clients without a live SSE connection and
  as the reconnect-gap path. The existing 2 s / 3 s polls
  (`webapp/lib/poll.tsx`, `workspace.tsx`) are not removed, only demoted.
- The per-cycle channel pushes the same state
  `state-sync-cleanup.md` Phase 3's `derive_live_state(ledger)` derives.

### Track E — Webapp control surface

- **Launcher form** — dataset dropdown (from `GET /api/v1/datasets`) + a
  Launch button → `POST /api/v1/runs`. The minimal "campaign configuration
  form" shape; full pipeline / scan-variant editing stays M12.
- **Run controls** — start / stop / resume / fork buttons wired to the
  Track C routes. `StopButton.tsx` already exists; it gets siblings.
- **Live job list** — a panel listing every `JobRegistry` job, fed by the
  registry SSE channel. Replaces the single-`active_session` identity model
  in `webapp/lib/workspace.tsx`.
- **Reactivity** — `WorkspaceProvider` and `CycleStreamProvider` subscribe to
  the two SSE channels; the hard-reload requirement is dropped.

### Track F — Meta-prompt read panel *(the M10-specific value)*

Surfaces which optimizer meta-prompt revision each cycle ran, and diffs across
cycles — so the round-1 gate / L4 review can see exactly which `l1_generate` /
`l1_critique` / `l2_context` produced a cycle and how it differs from the
prior revision. **Read-only** — edits stay in files via the
`potter-l1-meta-campaign` / `l4-improve-l1-gen` skills.

- The data is already on disk: prompt snapshots at
  `campaigns/{cycle_id}/prompts/optimizer_prompt/{hash}/`; the four
  prompt-template hashes in each `review.md` header; `optimizer_prompt_hash`
  on `campaign.json`; the cross-cycle leaderboard
  (`m10-prompt-iteration-framework.md` Track 4) already clusters by
  `l1_generate_hash`.
- **New API** — `GET /api/v1/campaigns/{id}/cycles/{id}/meta-prompts`
  returns the resolved `l1_generate` / `l1_critique` / `l2_context` /
  `l3_plan` template content + hashes the cycle ran;
  `GET .../meta-prompts/diff?against={other_cycle_id}` returns a unified diff.
- **Webapp panel** — per-cycle revision display + a cross-cycle diff viewer.

## Pre-flight gate

| Q | `JobRegistry` | `Control-remote` (I/O kind) | SSE subscriber |
|---|---|---|---|
| 1 — §0 bucket | Bucket 5 (I/O kinds) | Bucket 5 — *is* the new kind | Bucket 5 (Display sub-kind) |
| 2 — existing channel? | No — the CLI blocks; no job runner exists | No — §0 names only three | No — polling is pull, not push |
| 3 — name distinct? | Yes — no `Job*` / `*Registry` collision in the tree (`connectors`, `INJECTIONS` registries are unrelated) | Yes — parallels Control-local | Rides existing subscriber base; no new top-level name |
| 4 — self-describing? | Yes | Yes — §0 amendment (Track A) lands first | Yes |
| 5 — rides existing infra? | Runs ride `CycleEventLog`; only the registry list is new in-process state — exactly what Control-remote sanctions | Same persistence ingress (`CycleEventLog.append`) | Yes — a Display-kind ledger subscriber |
| 6 — readable from a file? | Yes — `.runtime/jobs/{job_id}.json` | n/a (a kind, not a fact) | n/a (transport) |
| 7 — §0 update needed? | Covered by Track A | Yes — Track A is PR #1 | Covered by Track A |
| 8 — Langfuse trace? | No new LLM call | No new LLM call | No new LLM call |

## Wave sequencing

```
PR #1: Track A — §0 amendment (docs-only; lands before any control code)

Wave 1: state-sync-cleanup Phases 1–3 (prerequisite — identity collapse,
        per-cycle state.json, GET /api/v1/live)

Wave 2: Track B — JobRegistry + shared launch core + on-disk job index

Wave 3: Track C — control + launch routes  ‖  Track D — SSE channels

Wave 4: Track E — webapp control surface  ‖  Track F — meta-prompt panel
```

## Entry / exit

**Entry:** M10 framework tracks in flight; `state-sync-cleanup.md`
Phases 1–3 landable.

**Exit:**
- [ ] `docs/architecture.md` §0 defines `Control-remote` (Allowed / Not-allowed).
- [ ] `JobRegistry` runs N concurrent campaigns in the uvicorn process; the
      job index is on disk; an API restart reconciles it (`running` → `interrupted`).
- [ ] Webapp can launch, stop, resume, and fork a campaign end-to-end.
- [ ] A campaign minted from the browser appears in the webapp **with no
      reload**.
- [ ] Meta-prompt panel shows each cycle's `l1_generate` / `l1_critique` /
      `l2_context` revision + cross-cycle diff.
- [ ] The four Control-remote routes are listed in
      `presentation/CLAUDE.md`'s sanctioned-mutation allowlist.

## Key existing code

| Area | Files |
|---|---|
| Loop entry (reused unchanged) | `application/runner/entry.py:47` (`run_optimization`), `runner/loop.py:56` |
| CLI launch bodies (extract shared core) | `presentation/cli/commands/new.py::cmd_new`, `resume.py::cmd_resume` |
| Persistence ingress | `infrastructure/ledger.py::CycleEventLog.append`; writer API `application/run_observers.py::RunCallbacks` |
| Stop control (Control-local) | `presentation/api/routers/active.py:221` (`stop` route); `session.stop_check` wiring in `cli/commands/{new,resume}.py` |
| Existing API routers | `promptpotter/main.py`; `presentation/api/routers/{active,campaigns,datasets}.py` |
| Active pointer | `infrastructure/store/__init__.py` (`save_active_pointer` / `read_active_pointer`) |
| Live-state derivation (state-sync Phase 3) | `state-sync-cleanup.md` Phase 3 — `derive_live_state(ledger)` |
| Webapp reactivity | `webapp/lib/{poll,workspace}.tsx`, `webapp/lib/api.ts`, `webapp/components/dashboard/StopButton.tsx` |
| Prompt snapshots / hashes | `campaigns/{cycle_id}/prompts/optimizer_prompt/{hash}/`; `optimizer_pipeline.json::resolved_prompts` |

## Risks

| Risk | Mitigation |
|---|---|
| API restart kills in-flight runs | Incremental per-round persistence loses no work; restart marks jobs `interrupted`, operator resumes from the webapp. |
| Concurrent `MeasurementArchive` writes to the same content hash | Low-risk (content-addressed, idempotent payloads); archive write coordination folded into the M12 multi-tenant pass, which touches the archive regardless. |
| Scope creep into M12 (auth pulled in early) | Single default tenant, no auth — hard line; auth is `m12-control-plane.md`. |
| Launch route accidentally awaits the loop | Exit gate explicitly tests non-blocking return; the route schedules the task and returns the `job_id`. |
| SSE connection churn on flaky networks | Polling stays as fallback + reconnect-gap path; SSE carries a `since` offset so a reconnect replays missed ledger events. |

## Appendix — §0 amendment draft

Proposed addition to `docs/architecture.md` §0 "State + persistence", promoting
the existing foreshadowing sentence into a full fourth kind:

> **(4) Control-remote** — HTTP routes that receive control commands from a
> remote client (the webapp; a future CLI-over-HTTP). The in-process
> `JobRegistry`, owned by the long-lived API process, holds every
> web-triggered run as a background task. **Allowed:** receive control
> commands (launch / stop / resume / fork), expose `Session` / `LoopState`
> snapshots, route campaign output to the requesting client. **Not allowed:**
> writing campaign artifacts directly — still through `CycleEventLog.append`;
> reading tracing data — still fan-out only via Display subscribers.
> Control-remote *triggers* Persistence and Control-local and never bypasses
> them: the launch route makes the same `run_optimization()` call the CLI
> makes; the stop route writes the same `.runtime/stop.flag` the Control-local
> kind defines. The host is the API process itself — there is no separate
> daemon process.
