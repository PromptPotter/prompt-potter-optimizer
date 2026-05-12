# `.ai/` — AI-runtime index

Pre-computed orientation data that lets a Claude Code agent (or similar)
answer "where is X / how do I do Y" in one tool call instead of 5–15.
Software 2.0 framing: the AI is the runtime, tool-calls are wall-clock
cost, every token is paid on every turn.

## Files

| File | Purpose |
|---|---|
| [`CODEMAP.md`](CODEMAP.md) | Hand-curated orientation: backbone symbol index, module map, hot-workflow recipes, invariant landmarks, anti-shim graveyard, "where is X" lookup. Read this before grepping. |
| `SYMBOLS.txt` | Flat `symbol\tfile:line` for every public top-level `class` / `def` in `promptpotter/` + `tests/`. Generated. Grep it for fast lookup, e.g. `grep -P '^DispatchHub\t' .ai/SYMBOLS.txt`. |

## Regenerate `SYMBOLS.txt`

Run after large refactors or renames:

```
python scripts/build_ai_index.py
```

Idempotent — re-running produces byte-identical output if the AST is
unchanged. Not wired into a hook; manual regen is fine. Stale rows are
low-risk: worst case the AI follows a wrong line number and falls back
to grep.

## Why `.ai/` and not `.claude/`?

`.claude/` is harness-owned (skills, slash commands, settings).
`.ai/` is repo-owned data files an AI consumes. Keeping the boundary
visible means harness changes don't accidentally evict the codemap and
codemap edits don't leak into harness config.
