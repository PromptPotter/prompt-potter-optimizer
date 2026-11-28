# bb-commands: move N12, one row per command (cloud · Opus)

`briefs/common.md` and `briefs/bb-shared.md` bind you. Spec: `briefs/backbone.md` § 4 row **N12**, and
`briefs/backbone-bottomup.md` § 1.15 and § M11 (root D10 in its § 3). The flat-package decision stands:
no `domain/commands/` package. The one table stays in `domain/command_kinds.py`.

Root: a command kind's facts are spread over the `CommandKind` enum row (5 columns) and 12 more per-kind
tables in 7 files: `PAYLOAD_MODEL_FOR_KIND`, five payload ClassVars, `_REPLAYS`, `keeps_answer`, `_rung`,
`LOOP_KINDS`, `RUN_VERB_KIND`, `_NAMED`, `DeleteKind`'s table, and a hand list in `admin_bot.py`. So a new
command is up to thirteen edits, and a kind can be in one table and missing from another.

Build: each per-kind fact becomes a column of the one row (or a field of the payload model the row
names), and every other table goes. A consumer reads the row. A surface (CLI, admin bot, API deps) derives
its verb list, grammar and replay facts from the rows, never from its own list. Census first: every holder
of each table, with `git grep -c`, and which surface reads which. A fact that belongs to the payload rather
than the kind stays on the payload model. Name each such case.

Kind strings stay byte-identical: the command log is run data and `kind` is its key, so nothing is wiped
and nothing re-keys. `served_contract.py` and `types.generated.ts` are regenerated if a served shape moves.
They should not move. Name it if they do.

Owns: `domain/command_kinds.py`, `domain/command_events.py`, `application/commands/dispatcher.py`,
`infrastructure/store/command_log.py`, `presentation/api/deps.py`, `presentation/admin_bot.py`,
`presentation/cli/main.py`, `domain/hierarchy.py` (the `DeleteKind` table only), the modules holding the
other tables as your census finds them (only their table and its readers), and their tests.
bb-prune may delete symbols elsewhere. bb-fence owns `application/optimizers/**`: if a member reads a
command table, re-point that read in the same link and name it (a member line change re-keys, so say so).

The size estimate is M (≈ −180 under `promptpotter/`).

Done when:
- One grep proves it: adding a test-only kind needs one row edit, and every surface's verb list and
  every replay rule picks it up. Write that as a fact, through the rows, with no fixture kind left in
  the product code.
- No per-kind dict, set or list of `CommandKind` members remains outside `command_kinds.py`: `git grep`
  for `CommandKind.` in a collection literal.
- The full gate is green and equals-main is unmoved.
