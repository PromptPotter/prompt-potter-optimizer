# tests — Charter

**Three axes. A test is admitted only if ALL THREE hold.**

1. **BEHAVIOUR-COUPLED.** It breaks when a number or a decision changes. If it can break because a
   name, file path, doc heading, YAML key, field name or error-message string moved, it is rejected.
2. **SILENT.** No error and no visible symptom in normal use. A 500, a 422, a failed mint, a dead
   button, a blank dashboard, an operator-visible refusal string: rejected — found and fixed in use.
3. **UNRECOVERABLE.** It loses paid measurement, leaks, misspends, or carries a wrong number into a
   decision — which candidate wins, which is eliminated, when to stop. If a re-run fixes it, rejected.

Downtime is affordable; dragging effectless tests through every refactor is not.

- **Coupling in the assertion → delete; coupling in the setup → rewire.** A number asserted over a
  fixture loaded off disk keeps its assertion and builds its input from `factories.py`.
- **A renamed or restructured contract gets a fresh test.** Delete the old one; never port assertions.

## Run what you touched

**`python scripts/tests_owning.py --run`** runs the tests owning everything moved since the last whole
run. A new subject is a SECTION in the file that already owns its production path — a new file raises
the `surface-ledger` row `test_files`.

## One journey per section

**Each `# N.` section is ONE test function**: a journey down the production
path it owns, asserting every decision along the way. A fact is an `assert` with a message naming the
invariant; a table of values is a `for` inside that function, never a `parametrize`. <!-- probe: git grep -cE "parametrize" -- tests/*.py == 0 --> A path that forks
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

No pytest-mock: `monkeypatch`, stdlib `unittest.mock` when needed. <!-- probe: git grep -cE "mocker" -- tests/*.py == 0 --> **More than 2–3 monkeypatches in one
step** means that step tests wiring that should be an import-time assert.

**Every network edge is answered by `scripts/offline_world.py`**, installed by the `offline` fixture:
set `offline.llm.says` / `offline.backend.faults`, read `offline.llm.sent` / `offline.backend.asked`.
Never a stub client, a raw-response double or a patched `get_llm_client`. <!-- probe: git grep -cE "setattr\(.*get_llm_client" -- tests/*.py == 0 -->
Two seams sit above our wire: replacing `query_loop.measure_sample` when the input is each cell's
verdict, and a DSPy cell's LM `engine`.

**Never fake a strict model.** A `SimpleNamespace` for a Pydantic model survives a field rename with
every gate green; build the real model via `factories.py`. A namespace is fine only for a non-validated
wiring seam (a `Stores`-shaped stub, a session object).

**No test waits on the wall clock** — use `factories.on_jumping_clock` (`jumping_clock` for one
coroutine); bound a thread race by an event the writer sets, never a count of reads. <!-- probe: git grep -cE "time\.sleep\(" -- tests/*.py == 0 -->

## Fixtures (`conftest.py`) + builders (`factories.py`)

`built_stores` is a real `Stores` in `tmp_path`, built by `factories.workspace`; `offline` is the fake
world above. `factories.py` builders return REAL models; each takes only
the fields a test bends. **Add a parameter to bend one; never add a builder an existing one can express.**

**`measurement` stamps an `objective` that DIVERGES from `fitness`, deliberately** — pass
`objective=fitness` where a test means them equal.

## Frozen cycle fixtures

**None, on either side** — the webapp's is [`../webapp/CLAUDE.md`](../webapp/CLAUDE.md) § Testing
posture; an on-disk-compat guarantee is `deploy-linux/update.sh`'s `restamp` smoke check.
