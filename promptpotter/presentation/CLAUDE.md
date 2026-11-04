# presentation/ — entry-point adapters (read-only over application/)

## Out-of-bounds

- **No business logic here** (root `<entry-point-parity>`): `cli/` and `api/` parse, check, page and format; which rows in what order is `application/`'s. Every renderer is `application/views/`'s; an entry point builds no `readout.py::ReadoutProjection` and hands at most a line sink to `build_run_observers`. <!-- probe: git grep -cE "ReadoutProjection" -- promptpotter/presentation/*.py == 0 -->
- **No campaign-artifact writes from entry-point code** — writes go through `CycleEventLog.append` or a declared projection.
- **A router's gate is its mount's** (`main.py::API_ROUTERS`; `None` is public by declaration), never a dependency each route remembers; a cross-user campaign is a 404 (`owned_campaign`).
- **`seed-screen`, `noise-floor` and `pin-roster` are CLI-only and declared so.**

## No ad-hoc mutating routes

**Every write touching campaign / cycle state goes through `CommandDispatcher.dispatch`** ([decisions](../../docs/decisions.md) § 0001), even behind a typed route; identity administration is an operator-admin channel (§ 0004). **Every route on a session mount is named by a `CommandKind` or a `ReadKind`**, and a read answers its row's application callable's return model, raised at import (`api/catalogue.py::bind_surface`). The sanctioned exceptions: the `POST /datasets/draft/*` routes dispatch `edit-draft-campaign` through `commands/draft_editing.py::dispatch_draft_patch`; `/site-records/{table}` is the operator's website rows under one bearer token, outside every tenant.

## Sample look-ahead — the one entry point that is deliberately NOT at parity

**`/commands/set-sample-lookahead` is browser-only, and the ABSENCE is the boundary** — declared by its `None` in `domain/command_kinds.py::CommandKind.cli_verb`, so a missing CLI verb, config key or dataset knob is never an oversight. Who may press it: [`access-model.md`](../../docs/operations/access-model.md) § host-admin ↔ user.

## Everything on stdout is also on disk

`ReadoutProjection._write` writes every line to the cycle's `readout.log`; stdout is one optional sink. `logs/latest-readout-path.txt` only NAMES the newest terminal launch's file, written by `HeldRun.run_inline` alone. <!-- probe: git grep -cE "latest-readout-path" -- promptpotter/*.py <= 1 --> **A value already on disk is ADDRESSED in the readout, never reprinted.**
