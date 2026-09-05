# tests — Charter

**Three axes. A test is admitted only if ALL THREE hold.**

1. **BEHAVIOUR-COUPLED.** It breaks when a number or a decision changes. If it can break
   because a name, file path, doc heading, YAML key, field name or error-message string
   moved, it is rejected — however silent the harm is. A test that breaks on renames is a
   tax paid at every rename and collected once.
2. **SILENT.** No error and no visible symptom in normal use. A 500, a 422, a failed mint,
   a dead button, a blank dashboard, an operator-visible refusal string: rejected. You find
   these in use, and you fix them then.
3. **UNRECOVERABLE.** It loses paid measurement, leaks, misspends, or carries a wrong number
   into a decision — which candidate wins, which is eliminated, when to stop. If a re-run
   fixes it, rejected.

This project can afford downtime. What it cannot afford is dragging effectless tests along
every refactor.

## Why three and not one

The rule used to be axis 2 alone. Every test this charter's own pass deleted had passed that
rule honestly, one bug at a time — and roughly half the suite ended up coupled to filenames,
YAML keys and rendered prose, breaking on benign renames forever and on wrong behaviour never.
A rule with too few axes, applied faithfully, *is* the mechanism of organic growth. These
corollaries are the parts that were being got wrong:

- **A well-argued docstring does not admit a test.** Every deleted test had one. The argument
  establishes axis 2 and then stops.
- **"A reader or a tool is lied to" is not an admissible class.** Doc-pointer rot, spec-vs-model
  drift, name censuses: caught by reading the diff when the thing moves. That is where this
  project does that work — not in a standing test that re-breaks on every reorganisation.
- **There are no sanctioned repo-wide scans.** The old charter sanctioned two; four had
  accumulated. Under axis 1 the correct number is zero, and a carve-out is how two becomes four.
- **A rejected structural invariant is relocated, not dropped** — see below.
- **Where the coupling sits decides delete vs REWIRE.** A test whose *assertion* reads a name or
  a path is dead. A test whose assertion is a number and whose *setup* happens to load a fixture
  off disk is merely badly wired: build the input from `factories.py` and keep the assertion.

## What each file is for

A file states its subject and holds only that. A test that fits no section does not belong —
which is a thing a flat file can never say, and is why `test_integrity.py` became the sink.

| File | Its subject |
|------|-------------|
| `test_numerics.py` | A wrong score or a wrong selection. The run completes, the dashboard looks fine, every result is subtly wrong. Ten sections: scorer formulas · composite fitness · the δ ruler · electing a round winner · PoBB elimination · which cells a round buys · paired readings · the L4 outer proxy · L1 proposal validators · escalation and spend. |
| `test_integrity.py` | A wrong identity or a quiet cross-contamination. Eight sections: measurement identity · replay eligibility · contamination of a scored prompt · the searchpoint's param surface · the dispatch frame · L4 steering · money · where the package reads and writes. |
| `test_security.py` | A leak, or money. A key reaching the logs, dataset content reaching the optimizer LLM unfenced, a path segment escaping its tenant dir, a spend ceiling that stops binding. Irreversible in a multi-tenant product. |
| `test_resume.py` | Lost or corrupted measurement. A rescore that corrupts prior fitness, a replay that misses a flipped outcome, a fork that inherits the wrong origin, a compaction that drops a paid row. |
| `test_reaper.py` | The unattended recursive delete, and spend banked before an rmtree. Not the phase label: a wrong terminal stamp neither blocks resume nor survives `_finalize_run`, so it is loud and self-healing. |
| `test_complexity_ledger.py` | Conceptual-surface creep and its quieter twin, a win nobody recorded. The ratchet asserts EQUALITY, so the surface never moves unexamined in either direction. |

## Structural invariants live in production, not tests

This is the **destination** for anything axis 1 rejects, not a footnote. A wiring guarantee worth
enforcing becomes an **import-time assert in the module that owns the registry** — it fails loud
at import, costs nothing to maintain, and needs no test to update. They exist across the package, e.g.
`RESUME_CHECKPOINT_GATING` exhaustiveness (`application/optimization/resume_and_fork/decisions.py`),
`L1_POSSIBLE ⊆ INJECTIONS` (`dispatch/injections/registry.py`), the `L1_MANDATORY`/origin-layout
subset checks (`domain/l1_layout.py`), the unread/abandoned row-key checks (`domain/scoring.py`),
the divergence-hint exhaustiveness (`cli/commands/_shared.py`). Add new ones the same way — beside
the thing they validate, never as a repo-wide structure scan.

## Adding a test

Answer the three axes in order. The first "no" ends it. If all three are yes, it rides an
existing file's existing section by adding a function — **never a new file**, and never a new
section invented to house it.

## Mock strategy

No pytest-mock plugin. `monkeypatch` for async, stdlib `unittest.mock` when needed. More than
2–3 monkeypatches in one test means it is testing wiring that should be an import-time assert.

**Never fake a strict model.** A `SimpleNamespace` stand-in for a Pydantic model is the one
construct that can carry a wrong number past every gate: rename a field and ruff, mypy and pytest
all stay green while the real read path breaks. It can also assert a shape the model cannot
produce. Build the real model via `factories.py`. A namespace is fine only for a wiring seam that
is not a validated document — a `Stores`-shaped stub, a session object.

## Fixtures (`conftest.py`) + builders (`factories.py`)

| Fixture | Purpose |
|---------|---------|
| `built_stores` | A real `Stores` rooted in `tmp_path` (default identity), used by the resume data-integrity tests. |

`factories.py` is not a test file (no `test_` prefix, collects nothing). It holds builders that
return REAL models — `round_result`, `cycle_result`, `scored_candidate`, `degradation_health`,
`lost_round`, `cycle_slice`, `injection_bundle`, plus `measurement` / `measurements`, the one
MEASURED-CELL row (`QueryMeasurement` is a `TypedDict`, so the dict *is* the model). Domain models
and the few application models the dispatch seam needs.

Each builder takes only the fields a test bends. **Add a parameter when a test needs to bend one;
never add a builder for a shape an existing one can express** — eight local copies of the cell row
had drifted apart here before, and adding `objective` to the loop had to find every one of them.

## Frozen cycle fixtures (`tests/fixtures/cycles/`)

`l2_terminal/` only, and it is **Vitest's** — reached via `webapp/lib/test-utils/fixtures.ts`,
owned by [`../webapp/CLAUDE.md`](../webapp/CLAUDE.md) § Testing posture. It sits here rather than
under `webapp/` for that reason.

The two Python frozen manifests that used to live beside it were deleted with their loaders: both
were engineered to fire on a **field rename** (axis 1) and the harm they named was an
`extra_forbidden` exception **raised at load** (axis 2 — loud). The on-disk-compat guarantee, if
it is wanted back, belongs in `deploy-linux/update.sh`'s existing `restamp` step as a
load-every-manifest smoke check, not as a pytest fixture.
