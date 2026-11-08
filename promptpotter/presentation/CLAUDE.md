# presentation/ — entry-point adapters (read-only over application/)

## Out-of-bounds

- **No business logic here** (root `<entry-point-parity>`): `cli/` and `api/` parse, check, page and format; which rows in what order is `application/`'s. Every renderer is `application/views/`'s; an entry point builds no `readout.py::ReadoutProjection` and hands at most a line sink to `build_run_observers`. <!-- probe: git grep -cE "ReadoutProjection" -- promptpotter/presentation/*.py == 0 -->
- **No campaign-artifact writes from entry-point code** — writes go through `CycleEventLog.append` or a declared projection.
- **A router's gate is its mount's** (`main.py::API_ROUTERS`; `None` is public by declaration), never a dependency each route remembers; a cross-user campaign is a 404 (`owned_campaign`).
- **A CLI-only verb says why on its `Verb` row (`terminal_only`), and a written verb spells each payload field of every kind naming it (`CommandKind.cli_verb`) or says why not (`unspelled`)** — refused at import (`cli/commands/verbs.py::_unsaid`).
- **A paid pass no command files is CLI-only, and its terminal is its one door**: `seed-screen` and `probe-reasoning` (no cycle) stop by their Ctrl+C alone; `noise-floor` by its cycle's `pause`, from its Ctrl+C or another terminal. Every other pass stops by its command's `cancel` at every door (`application/commands/cancelling.py`).

## No ad-hoc mutating routes

**Every write touching campaign / cycle state goes through `CommandDispatcher.dispatch`** ([decisions](../../docs/decisions.md) § 0001), even behind a typed route; identity administration is an operator-admin channel (§ 0004). **Every route on a session mount is named by a `CommandKind` or a `ReadKind`**, and a command route serves the model its kind applies: both refused at import over the `open_caller` mounts (`main.py::_off_catalogue`). A read is mounted by one loop at its own row's `application/read_params.py::ReadParams` model's `route`, described by its docstring and answering its `answer` (`api/routers/reads.py`); any other route a read names is refused at import too (`reads.py::READ_ROUTES`); a streamed answer is no host tool (`application/host_surface.py::_answers_once`). The sanctioned exception: `/site-records/{table}` is the operator's website rows under one bearer token, outside every tenant.

## Sample look-ahead — the one entry point that is deliberately NOT at parity

**`/commands/set-sample-lookahead` is browser-only, and the ABSENCE is the boundary** — declared by its `BROWSER` press (`domain/command_kinds.py::CommandKind.browser_only`), so a missing CLI verb, host tool, config key or dataset knob is never an oversight. `set-api-key` and `remove-api-key` are browser-only too, each for the reason beside its row. Who may press it: [`access-model.md`](../../docs/operations/access-model.md) § host-admin ↔ user.

## Everything on stdout is also on disk

`ReadoutProjection._write` writes every line to the cycle's `readout.log`; stdout is one optional sink. `logs/latest-readout-path.txt` only NAMES the newest terminal launch's file, written by `HeldRun.run_inline` alone. <!-- probe: git grep -cE "latest-readout-path" -- promptpotter/*.py <= 1 --> **A value already on disk is ADDRESSED in the readout, never reprinted.**
