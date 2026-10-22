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

Axis 2 alone admits, one honest bug at a time, a suite coupled to filenames, YAML keys and
rendered prose — breaking on benign renames forever and on wrong behaviour never. A rule with
too few axes, applied faithfully, *is* the mechanism of organic growth. The corollaries:

- **A well-argued docstring does not admit a test.** The argument establishes axis 2 and then
  stops.
- **"A reader or a tool is lied to" is not an admissible class.** Doc-pointer rot, spec-vs-model
  drift, name censuses: caught by reading the diff when the thing moves. That is where this
  project does that work — not in a standing test that re-breaks on every reorganisation.
- **There are no sanctioned repo-wide scans.** Under axis 1 the correct number is zero, and a
  carve-out is how zero becomes four.
- **A rejected structural invariant is relocated, not dropped** — see below.
- **Where the coupling sits decides delete vs REWIRE.** A test whose *assertion* reads a name or
  a path is dead. A test whose assertion is a number and whose *setup* happens to load a fixture
  off disk is merely badly wired: build the input from `factories.py` and keep the assertion.

## You touched X — run Y

A file is named for the production code it owns, so the one to run is the one whose row names what
you edited. Each file's docstring lists its packages; its `# N.` headers are the sections.

| You touched | Run | Sections |
|-------------|-----|----------|
| `application/scoring/`, `shared/statistics.py`, potter's `pobb/` and `escalation/`, the runner's election, `diagnostics/verify.py`, `evidence/` | `test_scoring.py` | scorer formulas · composite fitness and coverage · electing a round winner · an authored round · elimination · escalation and stop, live and folded back on resume · paired readings over shared cells |
| `intelligence/rasch.py` and `adaptive_queue_mechanism.py`, `bench/difficulty.py`, `domain/ruler.py`, `domain/l4/proxies.py` | `test_ruler.py` | the estimator · the δ ruler and the cells a round reads it on · the L4 outer proxy |
| The hashes a measurement is filed under, `application/pipeline_resolve.py`, `domain/pipeline_overlay.py`, the held-out partition, anything a scored prompt is composed from | `test_identity.py` | what a measurement is filed under · the config a campaign runs · contamination of a scored prompt |
| `infrastructure/store/measurement_archive.py` and `archive_queries.py`, `application/maintenance/`, `bench/resume_and_fork/`, `application/origin.py` | `test_archive.py` | replay eligibility · the archive on disk · replayed decisions and forks |
| `infrastructure/llm/` (pricing, the spend book, wire cost), `application/jobs/quota.py`, `account_spend.py`, the runner's budget gate, judge billing | `test_spend.py` | what a call costs · what a run is billed · a campaign's ceiling · what a send is held at · an account's ceiling · spend outlives what spent it |
| `infrastructure/store/layout.py` path builders, `config/log_redaction.py`, the dispatch fence, `infrastructure/identity/grants.py`, `application/jobs/reaper.py` | `test_security.py` | leaks · delegation · unattended deletes |

The table is for a reader. The command is `python scripts/tests_owning.py --run`: the tests that
own everything moved since the last whole run, read off what each test RAN (its header says
what it cannot see). Every test, where it cannot say. **Between edits** — owned by
[`reasoning-doctrine.md`](../docs/developer/reasoning-doctrine.md) `<wall-clock>`; this suite is narrowed by it.

**Six files, and the roster is that table** — a test is filed under the production code whose
change should break it, never under the harm it prevents, and a subject with no row is a SECTION in
its owner's file, never a seventh file. `test_files` is a ledger dimension, which is what makes the
count binding rather than advisory.

## One journey per section

**The count is held structurally, not by review.** Each `# N.` section is ONE test function: a
journey down the production path the section owns — a round scored, elected, eliminated and
stopped; a run billed up to its ceiling; a cell filed, replayed and forked — asserting every
decision along the way. A fact is an `assert` with a message naming the invariant, inside the
journey that produces it; a table of values is a `for` over cases inside that same function, never
a `parametrize`. A path that forks (live vs. folded back on resume, root vs. fork) may be a second
function named for the fork; nothing else is.

- **A new fact rides its section's journey** as one more assertion. Adding a function is adding a
  section — a ledger event (`test_functions` moves) with its reason in the commit body.
- **A journey may be slow.** Minutes are fine; a second function to shave seconds is not.
- **What a journey would only re-check about a SHAPE** — a field that cannot be negative, a set that
  must be a subset, a table that must be complete — moves into the production seam as a raise or an
  import-time assert (§ Structural invariants) and leaves the suite.
- The first failing assert names its invariant; the rest of the journey is re-read, not re-split.

**The ledger ratchet** — owned by root [`CLAUDE.md`](../CLAUDE.md) `<surface-ledger>`; here it is
a gate check and never a test: a section added or removed re-stamps `test_functions`, a
production module a journey newly imports, or stops importing, re-stamps `test_reach`. The
widest journey's RAN modules are held where the suite runs whole, beside its memory ceiling
(`scripts/tests_owning.py::WIDEST_JOURNEY_MODULES`) — a section that sprawls is split along a
production seam, its function paid by a fork folded back into its journey.

## Structural invariants live in production, not tests

This is the **destination** for anything axis 1 rejects, not a footnote. A wiring guarantee worth
enforcing becomes a **check in the module that owns the registry** — in the accessor that completes
it, or at import for a table built there (`application/initialization/CLAUDE.md` § Registries complete says which) — so it
fails loud before a run spends, costs nothing to maintain, and needs no
test to update. They exist across the package, e.g.
one replayer per decision kind (`application/bench/resume_and_fork/replayers.py::replayers`),
`L1_POSSIBLE ⊆ injection_table()` (`dispatch/injections/registry.py`), the `L1_MANDATORY`/origin-layout
subset checks (`optimizers/potter/dispatch/layout.py`), the unread row-key check (`domain/scoring.py`),
the divergence-hint exhaustiveness (`cli/commands/launch.py`). Add new ones the same way — beside
the thing they validate, never as a repo-wide structure scan.

## Adding a test

**"This change moves no decision" is not a test** — prove it once with the offline run
([`../docs/developer/offline-run.md`](../docs/developer/offline-run.md)) and put the diff in the
commit body. Answer the three axes in order. The first "no" ends it. Then ask whether the CODE can
carry it instead — an import-time assert, a raise at the seam, a type that cannot hold the wrong
value — and prefer that. If a test it is, it is one more assertion in its owner's journey
(§ One journey per section).

## Mock strategy

No pytest-mock plugin. `monkeypatch` for async, stdlib `unittest.mock` when needed. More than
2–3 monkeypatches in one step of a journey means that step is testing wiring that should be an
import-time assert; a journey of many steps carries each step's patches.

**One fake world.** A network edge — an LLM provider, a backend — is answered by the offline
run's world (`scripts/offline_world.py`), installed by the `offline` fixture; a journey sets what
it answers (`offline.llm.says`, `offline.backend.faults`) and reads what was sent
(`offline.llm.sent`, `offline.backend.asked`). Never a stub client, a raw-response double or a
patched `get_llm_client`: the journey drives the real client over the wire it would send. Two
seams sit above any wire of ours and are no edge: a journey whose input is each cell's verdict or
finishing order replaces `query_loop.measure_sample`, and a DSPy cell's LM is handed an `engine`,
DSPy's own transport seam — what the connector reads is the reply that seam returns.

**Never fake a strict model.** A `SimpleNamespace` stand-in for a Pydantic model is the one
construct that can carry a wrong number past every gate: rename a field and ruff, mypy and pytest
all stay green while the real read path breaks. It can also assert a shape the model cannot
produce. Build the real model via `factories.py`. A namespace is fine only for a wiring seam that
is not a validated document — a `Stores`-shaped stub, a session object.

**No test waits on the wall clock.** A test that orders work by `asyncio.sleep` or a timeout runs
on `factories.on_jumping_clock` (`jumping_clock` for one coroutine), whose clock jumps to the next timer: the order is the
schedule the test wrote, not the box's load. A thread race is bounded by an event the writer sets,
never by a fixed count of reads.

## Fixtures (`conftest.py`) + builders (`factories.py`)

| Fixture | Purpose |
|---------|---------|
| `built_stores` | A real `Stores` rooted in `tmp_path` (default identity) — `factories.workspace`, the one place a test tree is built. |
| `offline` | The offline run's world over every network edge (`offline_world.Router`), sentinel provider keys, no rate cap, the default account's rate share bound. |

`factories.py` is not a test file (no `test_` prefix, collects nothing). It holds builders that
return REAL models — `round_result`, `cycle_result`, `arm_state`, plus `measurement` /
`measurements`, the one
graded cell (`GradedCell`; `.facts` is the `MeasuredCell` a scorer takes, and `sheet()` holds them as given). Domain models
and the few application models the dispatch seam needs, plus the real objects more than one
file builds: `spend_book`, `inner_sandbox`, `workspace`, `loop_session`, `seat` (a round of a real
`Cycle`), and the records the runner appends (`arm_walk`, `close_round`).

Each builder takes only the fields a test bends. **Add a parameter when a test needs to bend one;
never add a builder for a shape an existing one can express** — local copies of a row drift apart,
and a new field then has to find every one.

**`measurement` stamps an `objective` that DIVERGES from `fitness`, deliberately** — pass
`objective=fitness` where a test means them equal. Under no `per_cell` composite the two really are
one float, and fixtures built on that identity hide every confusion of correctness with the
composite (a hit threshold reading the composite, a stop reading accuracy).

## Frozen cycle fixtures

**None, on either side.** **The webapp's** — owned by [`../webapp/CLAUDE.md`](../webapp/CLAUDE.md) § Testing posture.

**No Python frozen manifests** — one fires on a **field rename** (axis 1), and the harm it names,
an `extra_forbidden` **raised at load**, is loud (axis 2). An on-disk-compat guarantee belongs in
`deploy-linux/update.sh`'s `restamp` step as a load-every-manifest smoke check, not a pytest fixture.
