# presentation/ — entry-point adapters (read-only over application/)

**Thin shells: every adapter here gets the same orchestration call, never a private path** — parity is owned by
root [`CLAUDE.md`](../../CLAUDE.md) § Working principles.

## Layout

- **`cli/`** — `commands/verbs.py` holds each verb once; `parsers.py` builds argparse off `VERBS`. **Two launch
  verbs, `new` and `resume`, dispatch their command as DATA** (`CommandDispatcher(inline=Inline(…))`), so a
  terminal launch is a `CommandRecord` like the browser's; every other command verb derives from its payload
  model (`cli/commands/verbs.py::command_verb`). **Run-invocation (`HeldRun.run_inline` vs `HeldRun.detach`) is
  the only CLI↔web difference**; each terminal-only flag is declared with why no payload carries it.
- **`terminal/`** — what only a terminal prints (`completion.py`, `server_banner.py`). The readout and every
  renderer are the application's emit contract (`application/views/`), imported upward.
- **`api/`** — one module per router under `routers/`; a resource with unrelated concerns is a package whose
  `_router.py` owns its one `APIRouter`. Dataset reads go through `infrastructure/store/dataset_access.py::resolve_dataset`
  (a resolver, not a gate); what a campaign RUNS goes through `application/pipeline_resolve.py::resolve_pipeline_for_campaign`,
  ownership-gated (`owned_campaign`, 404 cross-user). **Three authorization gates, each denying via
  `shared/identity.py::require_capability`**: host privilege (the operator-admin channel), the command-verb gate
  (`CommandDispatcher._applier`), and the check-in-draft gate one layer in (`application/datasets/ingest.py::_open_checkin`).

## Out-of-bounds

- **No campaign-artifact writes from entry-point code** — writes go through `CycleEventLog.append` or a declared projection.
- **No business logic here** — `cli/` and `api/` parse, check, page and format; which rows in what order is
  `application/`'s, or the next entry point writes a copy.
- **`seed-screen`, `noise-floor` and `pin-roster` are CLI-only and declared so** — the first two score outside
  every cycle, the third writes the source tree.

## No ad-hoc mutating routes

**Add no mutating route touching campaign / cycle state**: every such write goes through `CommandDispatcher` and
the endpoints below. Identity administration is an operator-admin channel (`presentation/admin_bot.py`) —
[ADR-0004](../../docs/decisions.md) § 0004.

**Sanctioned mutating endpoints:**

- `POST /commands/{kind}` — the Control-remote highway over `domain/command_kinds.py::CommandKind`; who writes
  which ack is [ADR-0001](../../docs/decisions.md) § 0001's, and this layer writes none.
- `POST /datasets/ingest` — opens a durable check-in draft; nothing runs until `/commands/start-checkin`.
- `POST /datasets/draft/candidate-library` and `/from-column` — ingresses that dispatch `edit-draft-campaign`
  via `application/commands/draft_editing.py::dispatch_draft_patch`; any other draft mutation is a bug.
- `POST` / `DELETE /site-records/{table}` — the operator's website rows under one bearer token, outside every tenant.

**A 200 body never justifies bypassing the dispatcher** — a typed route still dispatches through
`CommandDispatcher.dispatch`, or the edit leaves no record of what changed or who changed it.

## Sample look-ahead — the one entry point that is deliberately NOT at parity

**`/commands/set-sample-lookahead` is browser-only, and the ABSENCE is the boundary** — a missing CLI verb,
config key or dataset knob is the gate, never an oversight. It is the sole `None` in
`domain/command_kinds.py::CommandKind.cli_verb`, so the absence is declared; who may press it is
[`access-model.md`](../../docs/operations/access-model.md) § host-admin ↔ user.

## Everything on stdout is also on disk

**Emit nothing to stdout that is not also findable as a file.** `ReadoutProjection._write`
(`application/views/readout.py`) writes every line to its cycle's `readout.log` at every entry point; stdout is
one optional sink of it. **`logs/latest-readout-path.txt` only NAMES the newest terminal launch's file**,
written by `HeldRun.run_inline` alone.

**A value already on disk is ADDRESSED in the readout, never reprinted** — name its home and size; a value with
no on-disk home is the bug.
