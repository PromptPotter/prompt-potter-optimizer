# presentation/ — entry-point adapters (read-only over application/)

The thin shells wiring the operator's three entry points (CLI, notebook,
webapp) to one orchestration layer. Orchestration itself lives in
[`../application/CLAUDE.md`](../application/CLAUDE.md)'s tree and the disk
seams in [`../infrastructure/CLAUDE.md`](../infrastructure/CLAUDE.md)'s;
what may not happen here is § Out-of-bounds.

## Layout

| Module | Owns |
|---|---|
| `cli/` | `campaign_runner.py` (CLI entry-point — COMMANDS dispatch + `main()`), `session.py` (session-state plumbing — `SessionCtx` typed accessors), `parsers.py` (argparse). Thin shells over `application/runner/` and `application/initialization`. **Two write verbs:** `new` (dataset name *or* raw file) + `resume` — plus `pause` and the three lifecycle verbs, which are thin shells over `CommandDispatcher` rather than run invocations (`commands/lifecycle.py`), so a terminal interrupt lands on the ledger exactly as the webapp's does. The raw-file form folds onto the durable check-in path — `new`'s `Path(arg).is_file()` branch (`commands/new.py::_ingest_and_prepare_checkin`) ingests → mints a `checkin` campaign → origin-resolves, then runs the shared `launcher.checkin::prepare_checkin_run` (gate + commit + flip to `active`) and runs the loop **inline** with `LiveDisplay`. The web Start (`/commands/start-checkin`) shares `prepare_checkin_run` but **detaches**; run-invocation is the only CLI↔web difference. There is no separate `ingest` verb. |
| `views/` | **Terminal display only**: `display.py` (ANSI primitives — CYAN/DIM/RESET/etc.), `render.py` (`to_text` + `render_sp_diff` ANSI renderers only — `to_markdown`/heatmap/sweep-summary are imported straight from `application.views.render`), `live/` (`LiveDisplay` ledger subscriber + per-sample / per-candidate / phase formatters), `notebook_run.py`, `startup_checklist.py`. The typed View dataclasses + the `PhaseEvent → View` builder (`from_phase_event`) + markdown rendering are the **application's emit contract** and live in [`application/views/`](../application/views/) — presentation imports them UPWARD (`presentation → application`, the allowed direction). Disk-side reconstruction (`from_disk_log`) lives in `application/output.py`. |
| `api/` | FastAPI. One module per router under `routers/`; `campaigns/` is a package whose `__init__` imports its route submodules so their decorators run — emptying it mounts zero routes. `deps.py` chains `resolve_identity` → `IdentityDep` → `build_stores_from_identity` → `StoreDep`. Dataset reads go through one gateway, `store/dataset_access.py::readable_dataset_dir` (tenant content, then install content), 404ing an unresolvable slug — a **resolver, not a capability gate**, since install content is git-tracked and gating it guards nothing. Authorization lives elsewhere: the host-admin tier (`ADMIN_CAPABILITIES`, on the ADR-0004 channel) and the command-verb gate (`_require_capability_for`). An OIDC swap replaces only `resolve_identity`; every route keeps consuming `IdentityDep` / `StoreDep` unchanged. |

## Out-of-bounds

- **No campaign-artifact writes from entry-point code.** Disk writes go
  through `CycleEventLog.append` (orchestration) or a declared projection
  (display); the per-cycle markdown writers (`log.md`, `review.md`) now live
  in `application/output.py` (orchestration-side), not here.
- **No business logic here** — `cli/` and `api/` parse, route, and format; anything else is drift into the wrong layer.
- **Three entry points, one orchestration layer.** A behavior reachable from the CLI but not the notebook or webapp is a bug, not a feature.

## Read-only API stance

**Add no mutating route touching campaign / cycle state** — that is
Control-remote highway territory, and out of charter here. `api/` is
read-only by design beyond the sanctioned endpoints listed below; webapp
panels poll `dashboard.json` plus a few JSON endpoints and never drive
the loop.

This is *not* a ban on every mutation in the codebase. **Identity-surface
administration** (editing the sign-in allowlist, provider config) is a
different I/O kind — it is delivered by an **operator-admin channel**
(a deployment-side, outbound-only companion such as
`presentation/admin_bot.py`), **not** an inbound API route and **not**
`/commands/{kind}`. Permanent contract:
[`../../docs/adr/0004-operator-admin-channels.md`](../../docs/adr/0004-operator-admin-channels.md).
Don't reach for the command highway when the right home is the
operator-admin channel.

**Sanctioned mutating endpoints:**

- `POST /commands/{kind}` — the Control-remote highway. Closed inbound
  set declared in `docs/specs/m12-api-openapi.yaml`; sole writer at the
  seam is `CommandDispatcher` (`presentation/api/middleware/`). Every
  command is appended to its target ledger (per-cycle, campaign root
  cycle, or workspace ledger at `projects/{tenant}/.workspace/events.jsonl`)
  as a `CommandRecord`; inline-applied; paired `CommandAckRecord` written
  by the same dispatcher — every kind, cycle-scoped included. A separate
  `RunnerCommandSubscriber` for the cycle-scoped acks was specified and
  never written; nothing else has ever produced one.
- `POST /datasets/ingest` — multipart CSV upload; mints a durable `checkin` campaign and returns its `DraftCampaign` (`draft_id` IS the `campaign_id`; declared in `docs/specs/m12-api-openapi.yaml`; spec at `docs/specs/roadmap.md § Ingest`). Workspace-scoped, identity-bound; the check-in shows in the sidebar + survives a restart, but nothing runs until the operator starts it via the separate `/commands/start-checkin` verb. Mutation verbs (`edit-draft-campaign`, `resolve-origin`) key on the `campaign_id` and persist to `campaigns/{id}/checkin/` via `CheckinDraftStore`.
- `POST /datasets/draft/candidate-library` and its `/from-column` sibling — **ingresses, not write paths.** A multipart upload and a column name are two ways to *derive* a `candidate_library`; both hand the derived terms to `commands.py::dispatch_draft_patch` and dispatch as `edit-draft-campaign`, so the edit is a `CommandRecord` on the check-in ledger. A route that mutates a draft outside that function is the bug this collapsed.

**A 200 body never justifies bypassing the dispatcher.** `edit-draft-campaign` / `resolve-origin` / `start-checkin` are typed routes because each answers a domain object rather than a `CommandAcceptedBody` — but they dispatch through `CommandDispatcher.dispatch_checkin_command`, whose `CommandOutcome.result` carries that object back. They once applied inline for exactly this reason, and the consequence was that **no origin edit was recorded anywhere on disk, nor who made it** — a standing violation of `architecture.md` §0 ("sole `CommandDispatcher`"). The target is the check-in cycle `cycle_chk_*`, which exists from the first ingest action and is retained across the flip to `active`; a fork inherits its records via `CycleEventLog.inherit_from`. If a future verb needs a bespoke response, give it a typed route — never its own write path.

## Everything on stdout is also on disk

**Emit nothing to stdout that is not also findable as a file someone — or
something — can open later**, and write markdown only to documented paths.
The general rule is owned by root [`CLAUDE.md`](../../CLAUDE.md) § Pre-flight gate;
this section owns the terminal stream's half of it.

Concretely: `LiveDisplay._write` (`views/live/display.py`) is the single stdout
funnel for the live run readout, and it mirrors every line — ANSI-stripped — to the
gitignored **`.goldmine/latest.log`**, truncated per run (most-recent-only) and
best-effort (a filesystem error disables the mirror, never aborts the campaign). This
is the "findable on disk" guarantee for the terminal stream, so a headless reader can
open the last run instead of relying on a captured console. Caveat: it carries the
**display** stream only — `logging`-level warnings route through Python `logging`, not
`_write`, so full parity would need a sibling logging `FileHandler`.
