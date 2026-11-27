# w7-tests2: the test world's wall back under its base (cloud · Opus)

`briefs/common.md` and `briefs/w7-shared.md` bind you, with ONE override to BASE FIRST: your base is
**`origin/land/w7-wipe`'s tip** (w7-tests the last landing on it). It is not on main, so skip the
is-ancestor check and report `git log -1 origin/land/w7-wipe`.

SPEC: `docs/specs/code-debt-cleanup.md`, the row **Smells the test-shape split left**, read whole
with the w7-tests commit on land/w7-wipe (`git log --grep=w7-tests origin/land/w7-wipe`). w7-tests
opened the peer facts through real run init, which is right, but the pytest wall went from 77.8 s to
87 s, and five facts run over the 2 s budget.

DECIDED, in this order, one link each:
1. The bench facts share one module-scoped opened round (`factories.py::opened_round` at module
   scope, or a fixture over it), so a table of cases reads one round instead of opening a campaign
   per case. The budget-cut table (`tests/test_scoring.py`, 3.4 s) comes first. A fact that writes to
   its round keeps its own round, and its test says why in one line.
2. `factories.py::spend_book` binds an account without the offline world, so a fact that never
   touches the network drops the `offline` fixture.
3. No fact is deleted to buy wall, and no assertion is weakened. Two tests proving one fact are the
   exception: one goes, named in its commit.

OWNS: `tests/**` and `scripts/offline_world.py`. Not `promptpotter/`: if a link needs a production
door, stop it and report the door.

LEAN: the wall is back under 77.8 s on the same measure (`gate.py --only pytest`, reported at base
and tip), with `pytest --durations=10` at the tip and no peer fact over 2 s. Test lines hold or fall.
Equals-main is untouched (no product code moves). Run data: none.
