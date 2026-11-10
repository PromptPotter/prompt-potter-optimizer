# tests — Charter

**A test is admitted only if ALL THREE hold:**

1. **BEHAVIOUR-COUPLED.** It breaks when a number or a decision changes, never because a name or a string moved.
2. **SILENT.** No error and no visible symptom in normal use; a 500 or a dead button is found and fixed in use.
3. **UNRECOVERABLE.** It loses paid measurement, leaks, misspends, or carries a wrong number into a decision; if a re-run fixes it, rejected.

- **Coupling in the assertion → delete; coupling in the setup → rewire** onto `factories.py`. A renamed or restructured contract gets a fresh test; never port assertions.
- **"This change moves no decision" is not a test** — prove it with the offline run ([`offline-run.md`](../docs/developer/offline-run.md)).
- **`python scripts/gate.py --only pytest`** runs the whole suite, kept green by its content key. A new subject is a SECTION in the file that already owns its production path (`complexity_ledger.py` row `test_files`).

## One journey per section

**Each `# N.` section is ONE test function**: a journey down the production path it owns, asserting every decision along the way; a table of values is a `for` inside it, never a `parametrize`. <!-- probe: git grep -cE "parametrize" -- tests/*.py == 0 -->

## Structural invariants live in production, not tests

**A wiring or shape guarantee becomes a check in the module owning the registry** — the accessor that completes it, or import time for a table built there ([`initialization/CLAUDE.md`](../promptpotter/application/initialization/CLAUDE.md) § Registries complete) — never a repo-wide scan.

## Mock strategy

- No pytest-mock: `monkeypatch`, stdlib `unittest.mock` when needed. <!-- probe: git grep -cE "mocker" -- tests/*.py == 0 -->
- **Every network edge is answered by `scripts/offline_world.py`** (the `offline` fixture: set `offline.llm.says` / `offline.backend.faults`, read `offline.llm.sent` / `offline.backend.asked`); never a stub client, a raw-response double or a patched `get_llm_client`. <!-- probe: git grep -cE "setattr\(.*get_llm_client" -- tests/*.py == 0 -->
- **Never fake a strict model** with a `SimpleNamespace` (it survives a field rename with every gate green); build the real model via `factories.py`. A namespace is fine only for a non-validated wiring seam.
- **No test waits on the wall clock** — `factories.on_jumping_clock` (`jumping_clock` for one coroutine); bound a thread race by an event the writer sets, never a count of reads. <!-- probe: git grep -cE "time\.sleep\(" -- tests/*.py == 0 -->

## Fixtures (`conftest.py`) + builders (`factories.py`)

Builders return REAL models and take only the fields a test bends: add a parameter, never a builder an existing one can express. **`measurement` stamps an `objective` that DIVERGES from `fitness`, deliberately** — pass `objective=fitness` where a test means them equal. No frozen cycle fixtures, on either side ([`webapp/CLAUDE.md`](../webapp/CLAUDE.md) § Testing posture).
