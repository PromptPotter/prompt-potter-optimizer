# w7-tests: one test world (root F) (cloud · Opus)

`briefs/common.md` and `briefs/w7-shared.md` bind you, with ONE override to BASE FIRST: your base is
**`origin/land/w7-wipe` at `50bbd7d22`**. That is land/w7 7b3e5e293 plus w7-res, w7-res2 and w7-spend,
which reshaped `tests/factories.py::loop_session` and the sessions tests build. It is not on main, so
skip the is-ancestor check and report `git log -1 origin/land/w7-wipe`.

SPEC: `docs/specs/code-debt-cleanup.md` § wave-6 · arch review 2, **One test world (root F)**, and
every row it names under **Closes** (`seat`, `_peer_cycle` and `close_round` replay production by
hand; `_peer_cycle` seats an origin the archive does not hold; a test seat binds a campaign disk does
not hold; smells the test-shape split left; a test stands outside the harness). Read each whole, and
read `tests/CLAUDE.md` whole before the first edit.

DECIDED:
1. Tests open runs through the one opening production uses: `opening.py::Opening` and the
   `LaunchRequest` kinds, the fork's `ForkLaunch` among them (root A has landed, which makes this
   cheap). A test builder that replays production by hand (`seat(`, `_peer_cycle(`, `close_round(`)
   goes, and its callers take the production door or an offline-world fixture that already pays the
   cost.
2. A fact a test asserts keeps its assertion: a builder deleted is never a fact deleted. When two tests
   prove one fact, one goes, named in its commit.
3. One link per builder family, largest caller count first. Re-tests: `git grep -c "def _peer_cycle"
   -- tests` is 0, `seat(` and `close_round(` fall to their production callers, and
   `tests/factories.py`'s builder count falls (57 at the row's count).

OWNS: `tests/**` (with `tests/factories.py` and `tests/conftest.py`) and the offline-world fixtures
under `scripts/` that tests import. Not `promptpotter/`: if a test needs a production door that does
not exist, stop that link and report the door. w7-rows2 and w7-web2 run beside you on land/w7 and
touch their own tests; expect small conflicts at the landing, and name the test files you rewrite
whole.

LEAN: tests shrink clearly. Equals-main is untouched, since no product code moves: report the gate
green and the pytest wall at base and tip, with the slowest ten durations. Run data: none.
