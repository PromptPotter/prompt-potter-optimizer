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
- **An edge reads what the world ran.** A cycle a fact walks opens through run init's door, `factories.py::opened_round`, the backend answering what the fact declares (`FakeBackend.answers`) on the graph it declares (`FakeBackend.nodes`), an L4 outer's on its inner campaigns (`factories.py::l4_panel`); no fact seats a cycle by hand; `factories.py` keeps only builders of values no door produces.

## The world (`tests/world.py`)

- `world` (fixture): `World(stores, net, scratch)` — the fact's stores at its own data root (`own_home`, which binds its account, so a fact that never sends asks for no `offline`), the network answered by `scripts/offline_world.py` (`offline`).
- `await world.campaign(optimizer="potter", rounds=1, rows=40, step_rounds=None, knobs=None, hardness=0, origin_gate=None, down=False) -> Ran`: a fresh campaign minted and run through `embedded_run.py::run_campaign`, on the offline run's script (`ByCallIdentity`, `FakeBackend`, `synthetic_rows`); `step_rounds` pauses at that boundary, `down` has the backend refuse every cell.
- `await world.resume(hop, from_round=None, on_divergence=HALT, step_rounds=None) -> Ran`: the same entry, resuming; `from_round` rewinds as `--from` does.
- `await world.fork(hop, round, **ForkCyclePayload) -> Ran`: a fork through the `fork_cycle` command door, run inline.
- `await world.head_to_head(arms, h2h_id, rounds=1, rows=40) -> list[Ran]`: a head-to-head's arms minted in one act (`embedded_run.py::run_head_to_head`).
- `await world.minted(rows=40) -> CycleHop`: a potter campaign minted as `run_campaign` mints it (`jobs/mint.py::mint_framed_cycle`), no round run, no job admitted (~50 ms): for a fact reading what a mint writes. A fact needing the origin measured takes `campaign(rounds=0)` (~0.3 s).
- `async with world.opened(rows=40, connector=None) as session`: the real opening (`init_services` → `laid_session` → `arm_diagnostic_scoring`), for a fact measuring one cell outside a run. `connector` serves the dataset as a plugin's does (`world.py::serving` registers it, the family names it; a graph other than the family's lays its nodes on the family's model and ships no origin); an in-process one declares the world's graph (`pipeline_declaration=world.net.backend.declared`).
- `with world.family() as held`: the world's dataset family, edited as an operator edits a definition (an observation mapping, a node).
- `world.edit_proposer()`: the proposer's template edited in this home's manifest shadow, so a resume diverges.
- **A run several facts read is run once**: a module-scoped fixture returns `world.py::shared_run(tmp_path_factory, act)` (a `Shared`), and a fact that writes takes `world.adopt(shared)`, its own copy of that home (~25 ms) whose script the network answers, so it may rewind, fork or resume it; a fact or fixture that only reads takes `with shared.reading() as stores`, in place (no copy), never a re-pointed `PROMPTPOTTER_HOME`: a read that wrote fails at its exit (each file's size and write time, before and after). **Its fixture's line states the run's cost per module** (`~2.4 s once, ~25 ms a fact`). The process's cold start (its first imports; the landing's key is built, `maintenance/built_key.py`) is `conftest.py::cold_start`'s, a session fixture minting once after the dspy child: no fact or shared run pays it.
- **One run per shape, the fewest rows and rounds its facts need**, built `on=` the run it extends:

  | run | module | shape | once | facts |
  |---|---|---|---|---|
  | `line` | archive | potter, 3 rounds, 32 rows (24 starve its elections) | ~2.4 s | 16, 26 through the runs on it |
  | `displaced` | archive | on `line`: a rewind fork at 2; a paused campaign with a fork cut | ~1.6 s | 7 |
  | `family_tree` | archive | on `displaced`: a fork of the fork at 3, a steer off an uncrowned arm | ~1.7 s | 1 (the mask; the subtree's rules are tables) |
  | `origin_forks` | archive | on `line`: two steers off round 1's crown, origin unmodified and edited | ~1.6 s | 2 |
  | `gated` | archive | an origin the gate halts | ~0.3 s | 2 (one reads in place) |
  | `steered_line` | identity | a root on a served menu, forked at 0 on a wider one | ~2.7 s | 7 |
  | `banked_line` | scoring | a CAPO bench line opened through `opened_round`, round 1 closed, the origin's bench pass banked; its facts lay the backend's answers | ~0.6 s | 5 (2 read the run's `Cycle`, so walk their own) |
  | `l4_outer` | identity | an L4 outer over a potter and a CAPO seat (`l4_panel`) opened through `opened_round`; a fact re-opens its copy, the inner cells read off the archive | ~1.8 s | 3 (~0.3 s each) |
  | `head_to_head` | scoring | potter, capo, gepa as one head-to-head, and a lone capo, 60 rows (gepa's floor) | ~3.6 s | 3 |
- `Ran.session`, `Ran.result`, `Ran.hop`; `world.ledger(hop)`, `world.records(hop)`; `world.net` is the `Router` (`net.backend.faults`, `net.llm.sent`, `net.rates`).
- Its teardown is the fixtures': `offline` restores the network and asserts nothing went unrouted, `own_home`'s tree goes with `tmp_path`.

## Budget per test

- **≤ 60 lines**, a table included; a file's helpers count against its tests.
- **≤ 2 s** wall, a world campaign included (one round on 40 rows runs in ~3 s: share it through a fixture, never re-run it per fact).
- **≤ 2 MB** left in the process after it: nothing it imports or caches outlives it.
- **An extra (dspy, …) is never imported into the suite's process**: it is tested where it runs, in an offline leg or a child process. A child-process test runs early, or the parent is small when it does.
- **A child that sends is spawned by `scripts/offline_world.py::child`**: its code runs in the world (`world`, its `Router`), and a send no route answered fails the child at its exit.

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
