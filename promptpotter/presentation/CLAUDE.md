# presentation/ — entry-point adapters (read-only over application/)

## Out-of-bounds

- **No business logic here** (root `<entry-point-parity>`): `cli/` and `api/` parse, check, page and format; which rows in what order is `application/`'s, and every renderer is `application/views/`'s. An entry point builds no `ReadoutProjection` and hands at most a line sink to `build_run_observers`. <!-- probe: git grep -cE "ReadoutProjection" -- promptpotter/presentation/*.py == 0 -->
- **No campaign-artifact writes from entry-point code**: writes go through `CycleEventLog.append` or a declared projection.
- **A router's gate is its mount's** (`promptpotter/main.py::API_ROUTERS`; `None` is public by declaration), never a dependency each route remembers; an ASGI app is a row too, gated whole by `deps.py::mounted_caller`; a cross-user campaign is a 404.
- **A CLI verb is one `Verb` row in `cli/main.py::VERBS` and a handler in `cli/commands/`'s `launch`, `measure`, `reads` or `writes`** (`mcp`'s in `mcp/server.py`, so only it and the API's MCP door need the extra), NAMED (`module:function` under `cli/main.py::VERB_PACKAGE`) so `--help` imports none; a verb answering a `/commands/{kind}` is derived unless written; a written one spells each payload field of every kind naming it (`CommandKind.cli_verb`) by the positional or flag its payload declares (`CommandPayload.cli_spelling`, which spells every act too) or says why not (`unspelled`, refused at import: `cli/main.py::_unsaid`); a CLI-only one says why (`terminal_only`). A maintenance verb is dry-run by default and refuses while a writer could still write what it rewrites.
- **Every paid pass is filed by a command**, and stops by its command's `cancel` at every door.

## No ad-hoc mutating routes

**Every write touching campaign / cycle state goes through `CommandDispatcher.dispatch`** ([decisions](../../docs/decisions.md) § 0001), even behind a typed route; identity administration is an operator-admin channel (§ 0004). **Every route on a session mount is named by a `CommandKind` or a `ReadKind`** (refused at import).

## Everything on stdout is also on disk

Every readout line lands in the cycle's `readout.log`; stdout is one optional sink. `logs/latest-readout-path.txt` only NAMES the newest terminal launch's file. <!-- probe: git grep -cE "latest-readout-path" -- promptpotter/*.py <= 1 --> **A value already on disk is ADDRESSED in the readout, never reprinted.**
