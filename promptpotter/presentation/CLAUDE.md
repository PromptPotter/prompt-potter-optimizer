# presentation/ — entry-point adapters (read-only over application/)

## Layout

- **`cli/`** — each verb once in `commands/verbs.py`, whose docstring holds the launch-as-data rule.
- **`terminal/`** prints only what a terminal alone prints; the readout and every renderer are
  `application/views/`'s, imported upward.
- **`api/`** — **a router's gate is its mount's** (`main.py::API_ROUTERS`; `None` is public by
  declaration), never a dependency each route remembers; **a request body is a `RequestBody` (built at
  import) and a `StrictModel` at every depth**, asserted at import (`main.py::_request_bodies`). Dataset reads go through
  `infrastructure/store/dataset_access.py::resolve_dataset` (a resolver, not a gate); what a campaign RUNS goes through
  `application/pipeline_resolve.py::resolve_pipeline_for_campaign`, ownership-gated (`owned_campaign`,
  404 cross-user). **Authorization denies via `shared/identity.py::require_capability`** at host
  privilege (the operator-admin channel), the command-verb gate (`CommandDispatcher._applier`), and the
  check-in-draft gate one layer in (`application/datasets/ingest.py::_open_checkin`).

## Out-of-bounds

- **No business logic here** — entry-point parity is owned by root [`CLAUDE.md`](../../CLAUDE.md)
  `<entry-point-parity>`: `cli/` and `api/` parse, check, page and format; which rows in what order is
  `application/`'s.
- **No campaign-artifact writes from entry-point code** — writes go through `CycleEventLog.append` or a declared projection.
- **`seed-screen`, `noise-floor` and `pin-roster` are CLI-only and declared so** — the first two score outside
  every cycle, the third writes the source tree.

## No ad-hoc mutating routes

**Add no mutating route touching campaign / cycle state**: every such write goes through
`CommandDispatcher.dispatch`, even behind a typed route, or the edit leaves no record of what changed or
who changed it. Identity administration is an operator-admin channel (`presentation/admin_bot.py`) —
[ADR-0004](../../docs/decisions.md) § 0004. The sanctioned mutating endpoints:

- `POST /commands/{kind}` — the Control-remote highway over `domain/command_kinds.py::CommandKind`; who writes
  which ack is [ADR-0001](../../docs/decisions.md) § 0001's, and this layer writes none.
- `POST /datasets/ingest` — opens a durable check-in draft; nothing runs until `/commands/start-checkin`.
- `POST /datasets/draft/candidate-library` and `/from-column` — dispatch `edit-draft-campaign` via
  `application/commands/draft_editing.py::dispatch_draft_patch`.
- `POST` / `DELETE /site-records/{table}` — the operator's website rows under one bearer token, outside every tenant.

## Sample look-ahead — the one entry point that is deliberately NOT at parity

**`/commands/set-sample-lookahead` is browser-only, and the ABSENCE is the boundary** — declared by its `None`
in `domain/command_kinds.py::CommandKind.cli_verb`, so a missing CLI verb, config key or dataset knob is never
an oversight. Who may press it: [`access-model.md`](../../docs/operations/access-model.md) § host-admin ↔ user.

## Everything on stdout is also on disk

**Emit nothing to stdout that is not also findable as a file.** `ReadoutProjection._write`
(`application/views/readout.py`) writes every line to its cycle's `readout.log` at every entry point; stdout is
one optional sink of it. **`logs/latest-readout-path.txt` only NAMES the newest terminal launch's file**,
written by `HeldRun.run_inline` alone. <!-- probe: git grep -cE "latest-readout-path" -- promptpotter/*.py <= 1 -->

**A value already on disk is ADDRESSED in the readout, never reprinted** — name its home and size; a value with
no on-disk home is the bug.
