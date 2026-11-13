# presentation/ — entry-point adapters (read-only over application/)

## Out-of-bounds

- **No business logic here** (root `<entry-point-parity>`): `cli/` and `api/` parse, check, page and format; which rows in what order is `application/`'s, and every renderer is `application/views/`'s. An entry point builds no `ReadoutProjection` and hands at most a line sink to `build_run_observers`. <!-- probe: git grep -cE "ReadoutProjection" -- promptpotter/presentation/*.py == 0 -->
- **No campaign-artifact writes from entry-point code**: writes go through `CycleEventLog.append` or a declared projection.
- **A router's gate is its mount's** (`main.py::API_ROUTERS`; `None` is public by declaration), never a dependency each route remembers; a cross-user campaign is a 404.
- **A CLI verb is one module under `cli/commands/` and one `Verb` row in `cli/commands/verbs.py::VERBS`**, its handler NAMED (`module:function`) so `--help` imports none; a verb answering a `/commands/{kind}` is derived unless written; a written one spells each payload field of every kind naming it (`CommandKind.cli_verb`) or says why not (`unspelled`, refused at import: `verbs.py::_unsaid`); a CLI-only one says why (`terminal_only`). A maintenance verb is dry-run by default and refuses while a writer could still write what it rewrites.
- **Every paid pass is filed by a command**, and stops by its command's `cancel` at every door.

## No ad-hoc mutating routes

**Every write touching campaign / cycle state goes through `CommandDispatcher.dispatch`** ([decisions](../../docs/decisions.md) § 0001), even behind a typed route; identity administration is an operator-admin channel (§ 0004). **Every route on a session mount is named by a `CommandKind` or a `ReadKind`** (refused at import).

## Sample look-ahead — the one entry point that is deliberately NOT at parity

**`/commands/set-sample-lookahead` is browser-only, and the ABSENCE is the boundary** (its `BROWSER` press, which `CommandDispatcher.open` holds at the door a `CommandCall` names: `CommandKind.pressed_at`), so a missing CLI verb, host tool, config key or dataset knob is never an oversight. `set-api-key` and `remove-api-key` are browser-only too, each for the reason beside its row. Who may press it: [`access-model.md`](../../docs/operations/access-model.md) § host-admin ↔ user.

## Everything on stdout is also on disk

Every readout line lands in the cycle's `readout.log`; stdout is one optional sink. `logs/latest-readout-path.txt` only NAMES the newest terminal launch's file. <!-- probe: git grep -cE "latest-readout-path" -- promptpotter/*.py <= 1 --> **A value already on disk is ADDRESSED in the readout, never reprinted.**
