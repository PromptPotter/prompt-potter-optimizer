# tests — Charter

**A test is admitted only if ALL THREE hold:**

1. **BEHAVIOUR-COUPLED.** It breaks when a number or a decision changes, never because a name or a string moved.
2. **SILENT.** No error and no visible symptom in normal use; a 500 or a dead button is found and fixed in use.
3. **UNRECOVERABLE.** It loses paid measurement, leaks, misspends, or carries a wrong number into a decision; if a re-run fixes it, rejected.

- **Coupling in the assertion → delete; coupling in the setup → rewire** onto `factories.py`. A renamed or restructured contract gets a fresh test; never port assertions.
- **"This change moves no decision" is not a test**: prove it with the offline run ([`offline-run.md`](../docs/developer/offline-run.md)).
- **`python scripts/gate.py --only pytest`** runs the suite. A new subject is a function under the `# N.` section of the file that already owns its production path.
- **One function per independent fact**, named as the fact, at most ~150 lines; a table of values is a `for` inside it. <!-- probe: git grep -cE "parametrize" -- tests/*.py == 0 -->
- **A journey stays one function only where each step's state is the next step's input**, and one line above it names that dependency.
- **Setup two facts share is a fixture or a `_helper` in the module**, built through `factories.py`, never a frozen file; a closure several facts call is a fixture returning it. A fact never leans on state another fact left: it builds that state itself.

## Structural invariants live in production, not tests

**A wiring or shape guarantee becomes a check in the module owning the registry** (the accessor that completes it, or import time for a table built there: [`application/CLAUDE.md`](../promptpotter/application/CLAUDE.md) § Registries complete at import), never a repo-wide scan.

## Mocks, fixtures, builders

- No pytest-mock: `monkeypatch`, stdlib `unittest.mock` when needed. <!-- probe: git grep -cE "mocker" -- tests/*.py == 0 -->
- **Every network edge is answered by `scripts/offline_world.py`** (the `offline` fixture); never a stub client or a patched `get_llm_client`. <!-- probe: git grep -cE "setattr\(.*get_llm_client" -- tests/*.py == 0 -->
- **Never fake a strict model** with a `SimpleNamespace` (it survives a field rename with every gate green); build the real model via `factories.py`, adding a parameter, never a builder an existing one can express.
- **A spy on a process global (`os`, `shutil`, `time`) stands for the act it measures alone** (`with monkeypatch.context()`) and passes the real signature through (`*args, **kwargs`): the fixture's `monkeypatch` is undone after pytest's `tmp_path` cleanup, whose POSIX walk opens by `dir_fd`. <!-- probe: git grep -cE "monkeypatch\.setattr\((os|shutil|time)," -- tests/*.py == 0 -->
- **No test waits on the wall clock** (`factories.on_jumping_clock`); bound a thread race by an event the writer sets. <!-- probe: git grep -cE "time\.sleep\(" -- tests/*.py == 0 -->
- **`measurement` stamps an `objective` that DIVERGES from `fitness`, deliberately**: pass `objective=fitness` where a test means them equal. No frozen cycle fixtures, on either side ([`webapp/CLAUDE.md`](../webapp/CLAUDE.md) § Testing posture).
