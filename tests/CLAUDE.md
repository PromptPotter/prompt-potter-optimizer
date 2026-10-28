# tests — Charter

**Three axes. A test is admitted only if ALL THREE hold.**

1. **BEHAVIOUR-COUPLED.** It breaks when a number or a decision changes. If it can break because a
   name, file path, doc heading, YAML key, field name or error-message string moved, it is rejected.
2. **SILENT.** No error and no visible symptom in normal use. A 500, a 422, a failed mint, a dead
   button, a blank dashboard, an operator-visible refusal string: rejected — found and fixed in use.
3. **UNRECOVERABLE.** It loses paid measurement, leaks, misspends, or carries a wrong number into a
   decision — which candidate wins, which is eliminated, when to stop. If a re-run fixes it, rejected.

Downtime is affordable; dragging effectless tests through every refactor is not.

- **No repo-wide scans.** A shape worth enforcing moves to production (§ Structural invariants).
- **Coupling in the assertion → delete; coupling in the setup → rewire.** A number asserted over a
  fixture loaded off disk keeps its assertion and builds its input from `factories.py`.
- **A renamed or restructured contract gets a fresh test.** Delete the old one; never port assertions.

## You touched X — run Y

| You touched | Run | Sections |
|-------------|-----|----------|
| `application/scoring/`, `shared/statistics.py`, potter's `pobb/` and `escalation/`, the runner's election, `diagnostics/verify.py`, `evidence/` | `test_scoring.py` | scorer formulas · composite fitness and coverage · electing a round winner · an authored round · elimination · escalation and stop, live and folded back on resume · paired readings over shared cells |
| `intelligence/rasch.py` and `adaptive_queue_mechanism.py`, `bench/difficulty.py`, `domain/ruler.py`, `domain/l4/proxies.py` | `test_ruler.py` | the estimator · the δ ruler and the cells a round reads it on · the L4 outer proxy |
| The hashes a measurement is filed under, `application/pipeline_resolve.py`, `domain/pipeline_overlay.py`, the held-out partition, anything a scored prompt is composed from | `test_identity.py` | what a measurement is filed under · the config a campaign runs · contamination of a scored prompt |
| `infrastructure/store/measurement_archive.py` and `archive_queries.py`, `application/maintenance/`, `bench/resume_and_fork/`, `application/origin.py` | `test_archive.py` | replay eligibility · the archive on disk · replayed decisions and forks |
| `infrastructure/llm/` (pricing, the spend book, wire cost), `application/jobs/quota.py`, `account_spend.py`, the runner's budget gate, judge billing | `test_spend.py` | what a call costs · what a run is billed · a campaign's ceiling · what a send is held at · an account's ceiling · spend outlives what spent it |
| `infrastructure/store/layout.py` path builders, `config/log_redaction.py`, the dispatch fence, `infrastructure/identity/grants.py`, `application/jobs/reaper.py` | `test_security.py` | leaks · delegation · unattended deletes |

The command is `python scripts/tests_owning.py --run`: the tests owning everything moved since the
last whole run. **Six files** — a subject with no row is a SECTION in its owner's file; the
`surface-ledger` rows `test_files` and `test_functions` hold both counts.

## One journey per section

**Each `# N.` section is ONE test function**: a journey down the production
path it owns, asserting every decision along the way. A fact is an `assert` with a message naming the
invariant; a table of values is a `for` inside that function, never a `parametrize`. A path that forks
(live vs. folded back on resume, root vs. fork) may take a second function named for the fork.
A new fact is one more assertion in its section's journey. Slow journeys are fine.
The widest journey's modules are pinned in `scripts/tests_owning.py::WIDEST_JOURNEY_MODULES`.

## Structural invariants live in production, not tests

**A wiring or shape guarantee becomes a check in the module owning the registry** — in the accessor that
completes it, or at import for a table built there (`application/initialization/CLAUDE.md` § Registries
complete) — beside the thing it validates, never a repo-wide scan.

## Adding a test

**"This change moves no decision" is not a test** — prove it with the offline run
([`../docs/developer/offline-run.md`](../docs/developer/offline-run.md)) and put the diff in the commit
body. Otherwise answer the three axes in order, then prefer what the CODE can carry (a raise, an
import-time assert, a type) before adding an assertion to the owner's journey.

## Mock strategy

No pytest-mock: `monkeypatch`, stdlib `unittest.mock` when needed. **More than 2–3 monkeypatches in one
step** means that step tests wiring that should be an import-time assert.

**One fake world.** Every network edge is answered by `scripts/offline_world.py`, installed by the
`offline` fixture: set `offline.llm.says` / `offline.backend.faults`, read `offline.llm.sent` /
`offline.backend.asked`. Never a stub client, a raw-response double or a patched `get_llm_client`. Two
seams sit above our wire: replacing `query_loop.measure_sample` when the input is each cell's verdict,
and a DSPy cell's LM `engine`.

**Never fake a strict model.** A `SimpleNamespace` for a Pydantic model survives a field rename with
every gate green; build the real model via `factories.py`. A namespace is fine only for a non-validated
wiring seam (a `Stores`-shaped stub, a session object).

**No test waits on the wall clock** — use `factories.on_jumping_clock` (`jumping_clock` for one
coroutine); bound a thread race by an event the writer sets, never a count of reads.

## Fixtures (`conftest.py`) + builders (`factories.py`)

`built_stores` is a real `Stores` in `tmp_path` (`factories.workspace`, the one place a test tree is
built); `offline` is the fake world above. `factories.py` builders return REAL models; each takes only
the fields a test bends. **Add a parameter to bend one; never add a builder an existing one can express.**

**`measurement` stamps an `objective` that DIVERGES from `fitness`, deliberately** — pass
`objective=fitness` where a test means them equal.

## Frozen cycle fixtures

**None, on either side** — the webapp's is [`../webapp/CLAUDE.md`](../webapp/CLAUDE.md) § Testing
posture; an on-disk-compat guarantee is `deploy-linux/update.sh`'s `restamp` smoke check.
