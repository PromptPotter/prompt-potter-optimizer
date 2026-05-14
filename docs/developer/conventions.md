# Conventions

Rules contributors follow that aren't derivable from reading the code.
Non-negotiables (the no-backward-compat pledge, three I/O kinds, vocabulary
discipline) live in the root [`CLAUDE.md`](../../CLAUDE.md); this page
collects everything else.

## Code style

- **PEP 604** type hints (`X | None`, `list[str]`); never `Optional[X]` /
  `List[str]`.
- **`logging` module only** — no `print()` in `promptpotter/`. Setup via
  `promptpotter/config/logging.py`.
- **Ruff line-length: 100.** Run `ruff check . && ruff format --check .` in
  the CI chain.
- **Direct field access** — `dict[key]` for guaranteed fields, not
  `.get(key, fallback)`. Fallbacks announce uncertainty; if you have a
  contract, lean on it.
- **Comments default to none.** Only non-obvious *why*. Never explain *what*
  the code does — the names already do that.
- **Docstring trimming is out of charter for LOC-shrink work.** Existing
  module/class/function docstrings explain WHY (invariants, contracts,
  hidden constraints) and are user-facing value. Real LOC wins come from
  pattern unification, dead-code removal, inlining single-use helpers,
  fixing god-objects — never from shrinking explainers. If a docstring is
  genuinely an essay restating what the code does, ask first.
- **Pipeline components are nodes** — never "building blocks", never
  "services".

## Code shape

- **No fallbacks in service code.** Two sanctioned exceptions:
  `score_population()` synthetic-0 on `validation_failures`; load-boundary
  deprecated-sample gate (uses `classify_result()` fatal codes). Any new
  fallback must be documented alongside these.
- **Optimizer LLM calls go through `llm_call()`**
  (`application/optimization/dispatch/llm_call.py`), never `chat()`.
- **Escalation flows via return value** (`QueryLoopResult.escalation_signal`),
  not exception. Use `graceful()` (`shared/errors.py`) where exceptions must
  escape.

## Tests

- **Subtractive.** Each guards a named invariant. No volume tests, ≤2–3
  monkeypatches per test. See [`tests/CLAUDE.md`](../../tests/CLAUDE.md).
- **Delete-don't-update.** When a contract is renamed/restructured, delete
  the old test and write a fresh one. Never port assertions.

## CLI / running

- **Timeouts: 30s default for ALL commands.** Increase only when explicitly
  told "ready for data collection".
- **Never run `campaign_runner` with `run_in_background`** — always
  foreground.

## Git

- **Conventional commits** — `feat:`, `fix:`, `docs:`, `refactor:`, etc.
- **Commit messages: hard cap 800 chars** total (incl. trailer); title <70.
  Terse bullets — no motivation essays. Over 800 → rewrite, do not
  commit-and-fix-later.
