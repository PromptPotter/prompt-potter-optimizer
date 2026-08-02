# tests — Charter

**One bar: a test earns its place only if it catches SILENT, irreversible harm.**

This project can afford downtime — weeks of it, if it came to that. What it
cannot afford is dragging effectless tests along every refactor. So the question
for any test is not "does this guard a contract?" — almost everything does — but
"if this breaks in production, do I *see* it?"

- **Breaks loud → no test.** A wrong API envelope, a layer-import drift, a
  changed model shape, a route that 500s, a dashboard that stops updating, a
  failed mint — you notice these in use, in a log, in the file tree. You fix
  them when they bite. A test for them is pure maintenance tax: it breaks on
  every benign rename and catches nothing you wouldn't have caught anyway.
- **Breaks silent → test it.** Some failures produce no error and no visible
  symptom — they just quietly carry the wrong number forward or leak something.
  Those are the only failures worth a standing test, because they are the only
  ones you *cannot* rediscover in use.

## The silent-harm classes (the whole suite)

| File | Silent harm it catches |
|------|------------------------|
| `test_numerics.py` | A wrong score. Rasch / PoBB / composite-fitness / scorer-formula errors are invisible — the run completes, the dashboard looks fine, every result is subtly wrong. The math must be wrong-reveal. |
| `test_integrity.py` | A wrong identity / a quiet cross-contamination. Content-hash collisions, a flat pipeline-param map slipping through, an L3 plan leaking into the target prompt, a hit-cache reused across datasets, a config value that changed TYPE on the way to disk (YAML 1.1 resolves bare `off`/`no`/`TRUE` to booleans — the boolean census pins where one may legitimately live) — each carries the wrong number or content forward with no error. |
| `test_security.py` | A leak. A key reaching the logs, dataset content reaching the optimizer LLM unfenced (prompt injection), a path-segment escaping its tenant dir — no error, just harm, often irreversible in a multi-tenant product. |
| `test_resume.py` | Lost / corrupted measurement data. A rescore that corrupts prior fitness, a replay that misses a flipped outcome, a fork that inherits the wrong origin, an aborted-run merge that shrinks an already-fuller archive. A killed-and-restarted run raises nothing — it just loses or mis-carries expensive, irreplaceable data. |
| `test_reaper.py` | A reap that clobbers a paused or check-in cycle's resumability — with no error. |
| `test_complexity_ledger.py` | Conceptual-surface creep. The ratchet: the package's counted surface never grows *unexamined* — a raise is allowed, it just costs a baseline edit and a written reason. It is the enforcement teeth behind the `<surface-ledger>` doctrine, and root `CLAUDE.md` tells you to run it on any "refactor"/LOC pass. |

That is the suite — **six files**. No structural / wire / persistence / identity /
quota / lifecycle / event-stream / display / shape tests — all of those fail loud.

**Two sanctioned repo-wide scans** — and the reasoning matters, because both look like the
thing the line above bans. Each catches a tool or a reader being lied to, which no other
run reveals; each test's docstring carries the detail.

- `test_no_raw_nul_bytes_in_tracked_text_files` — a raw NUL makes ripgrep skip the whole
  file **silently** while `tsc`/eslint/`next build`/`pytest` all stay green, so every later
  audit reads a codebase with that file missing. Self-concealing, too: searching `\0` skips
  exactly the files containing one. Two such files manufactured false dead-code findings twice.
- `test_claude_md_claims_resolve` — **nothing but an agent reads a `CLAUDE.md`, and an agent
  following a dead pointer does not raise.** It reads a rule that is not there, or misses one
  that is, and edits the code accordingly with every gate green throughout. A claim scan, not
  a shape scan: it asserts nothing about how the tree is organized, only that what the files
  already claim still resolves.

Neither can be an import-time assert: no production module owns the repo's file bytes.

## Structural invariants live in production, not tests

A few wiring guarantees worth enforcing are **import-time asserts in the module
that owns the registry** (they fail loud at import, cost nothing to maintain, and
need no test to update): e.g. `RESUME_CHECKPOINT_GATING` exhaustiveness
(`application/optimization/resume_and_fork/decisions.py`), `L1_POSSIBLE ⊆ INJECTIONS`
(`dispatch/injections/registry.py`), the `L1_MANDATORY`/origin-layout subset
checks (`domain/l1_layout.py`), the divergence-hint exhaustiveness
(`cli/commands/_shared.py`). Add new ones the same way — beside the thing they
validate, never as a repo-wide structure scan.

## Adding a test

Before writing one, answer: *if this broke in production, would I see it?* If
yes, do not write the test — let it break and fix it then. If genuinely no (it
corrupts a number or leaks something with no symptom), it rides one of the files
above by adding a function — never a new file.

## Mock strategy

No pytest-mock plugin. `monkeypatch` for async, stdlib `unittest.mock` when
needed. More than 2–3 monkeypatches in one test means it's testing wiring that
should be an import-time assert, not a test.

**Never fake a strict model.** A `SimpleNamespace` stand-in for a Pydantic model is the
one construct that can carry this file's own silent-harm class past every gate: rename a
field and ruff, mypy and pytest all stay green while the real read path breaks. It can
also assert a shape the model cannot produce — the `RoundResult` fake `factories.py`
replaced stamped `l1_n_no_op` directly, though it is a `@computed_field` derived from
`candidate_scores`, which `test_integrity.py` pins in the other direction. Build the real
model via `factories.py`. A namespace is fine only for a wiring seam that is not a
validated document (a `Stores`-shaped stub, a session object).

## Fixtures (`conftest.py`) + builders (`factories.py`)

| Fixture | Purpose |
|---------|---------|
| `built_stores` | A real `Stores` rooted in `tmp_path` (default identity). The one surviving fixture — used by the resume data-integrity tests. |

`factories.py` is not a seventh test file (no `test_` prefix, collects nothing). It holds
builders that return REAL domain models — `round_result`, `cycle_result`,
`scored_candidate`, `degradation_health`, `lost_round` — each taking only the fields a
test bends. Add a parameter when a test needs to bend one, never a whole new builder for
a shape an existing one can express.
