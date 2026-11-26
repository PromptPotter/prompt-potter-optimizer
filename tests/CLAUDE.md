# tests — Charter

**A test is admitted only if ALL THREE hold:**

1. **BEHAVIOUR-COUPLED.** It breaks when a number or a decision changes, never because a name or a string moved.
2. **SILENT.** No error and no visible symptom in normal use; a 500 or a dead button is found and fixed in use.
3. **UNRECOVERABLE.** It loses paid measurement, leaks, misspends, or carries a wrong number into a decision; if a re-run fixes it, rejected.

- **Coupling in the assertion → delete; coupling in the setup → rewire** onto the tier below. A renamed or restructured contract gets a fresh test; never port assertions.
- **"This change moves no decision" is not a test**: prove it with the offline run ([`offline-run.md`](../docs/developer/offline-run.md)).

## Three tiers: each kind of fact lives in one

| tier | the fact | where | its shape |
|---|---|---|---|
| **Rules** | what a pure function returns | `tests/test_<area>.py`, beside its area's edges | a table: rows of input → expected, one function per rule; no stores, no session, no event loop |
| **Journeys** | the loop's invariants over the REAL optimizers | `scripts/offline_run.py` legs, run by the gate's `offline-run` check | a leg compares records: press 1 against `PRESS`, straight against paused-and-resumed, interrupted, wounded, healed, forked, rebased, L4 |
| **Edges** | store, archive, landing, reclaim, security, tenancy, the REST/CLI/MCP doors, served shapes | `tests/test_<area>.py` on the `world` fixture | through the production door, on what the world ran |

- **A rule a test reaches only through a walk is a rule left unexposed**: expose it in production as its own function, never a test seam. `scoring/row_policy.py::send_rows` is the model: pure, so its facts are a table.
- **A journey is never replayed by hand in pytest.** No fake race, no hand-driven `run_walks`, no hand-seated cycle: an invariant of the walk is a leg, every optimizer at once. A fact a leg cannot carry yet is a leg added (`run_one`'s knobs), never a pytest stand-in.
- **An edge reads what the world ran.** `seat`, `_peer_cycle`, `close_round` and hand-seated cycles are the old shape: a file migrating to the world deletes them; `factories.py` keeps only builders of values no door produces.

## The world (`tests/world.py`)

- `world` (fixture): `World(stores, net, scratch)` — the fact's stores at its own data root (`own_home`), the network answered by `scripts/offline_world.py` (`offline`).
- `await world.campaign(optimizer="potter", rounds=1, rows=40, step_rounds=None, knobs=None, hardness=0) -> Ran`: a fresh campaign minted and run through `embedded_run.py::run_campaign`, on the offline run's script (`ByCallIdentity`, `FakeBackend`, `synthetic_rows`); `step_rounds` pauses at that boundary.
- `await world.resume(hop) -> Ran`: the same entry, resuming.
- `Ran.session`, `Ran.result`, `Ran.hop`; `world.ledger(hop)`, `world.records(hop)`; `world.net` is the `Router` (`net.backend.faults`, `net.llm.sent`, `net.rates`).
- Its teardown is the fixtures': `offline` restores the network and asserts nothing went unrouted, `own_home`'s tree goes with `tmp_path`.

## Budget per test

- **≤ 60 lines**, a table included; a file's helpers count against its tests.
- **≤ 2 s** wall, a world campaign included (one round on 40 rows runs in ~3 s: share it through a fixture, never re-run it per fact).
- **≤ 2 MB** left in the process after it: nothing it imports or caches outlives it.
- **An extra (dspy, …) is never imported into the suite's process**: it is tested where it runs, in an offline leg or a child process. A child-process test runs early, or the parent is small when it does.

## Shape

- **`python scripts/gate.py --only pytest`** runs the suite. A new subject is a function under the `# N.` section of the file that already owns its production path.
- **One function per independent fact**, named as the fact; a table of values is a `for` inside it. <!-- probe: git grep -cE "parametrize" -- tests/*.py == 0 -->
- **A journey stays one function only where each step's state is the next step's input**, and one line above it names that dependency.
- **Setup two facts share is a fixture or a `_helper` in the module**, never a frozen file. A fact never leans on state another fact left: it builds that state itself.

## Structural invariants live in production, not tests

**A wiring or shape guarantee becomes a check in the module owning the registry** (the accessor that completes it, a model's class definition, or import time for a table built there: [`application/CLAUDE.md`](../promptpotter/application/CLAUDE.md) § Registries complete at import), never a repo-wide scan or a pinned test. `OptimizerResponseModel` refuses an id list opening before its fields; `select_optimizer` refuses a reply layout out of its schema's order.

## Mocks, fixtures, builders

- No pytest-mock: `monkeypatch`, stdlib `unittest.mock` when needed. <!-- probe: git grep -cE "mocker" -- tests/*.py == 0 -->
- **Every network edge is answered by `scripts/offline_world.py`** (the `offline` fixture); never a stub client or a patched `get_llm_client`. <!-- probe: git grep -cE "setattr\(.*get_llm_client" -- tests/*.py == 0 -->
- **Never fake a strict model** with a `SimpleNamespace` (it survives a field rename with every gate green); build the real model via `factories.py`, adding a parameter, never a builder an existing one can express.
- **A spy on a process global (`os`, `shutil`, `time`) stands for the act it measures alone** (`with monkeypatch.context()`) and passes the real signature through (`*args, **kwargs`): the fixture's `monkeypatch` is undone after pytest's `tmp_path` cleanup, whose POSIX walk opens by `dir_fd`. <!-- probe: git grep -cE "monkeypatch\.setattr\((os|shutil|time)," -- tests/*.py == 0 -->
- **No test waits on the wall clock** (`factories.on_jumping_clock`); bound a thread race by an event the writer sets. <!-- probe: git grep -cE "time\.sleep\(" -- tests/*.py == 0 -->
- **`measurement` stamps an `objective` that DIVERGES from `fitness`, deliberately**: pass `objective=fitness` where a test means them equal. No frozen cycle fixtures, on either side ([`webapp/CLAUDE.md`](../webapp/CLAUDE.md) § Testing posture).
