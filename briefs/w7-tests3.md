# w7-tests3: the last seats open through run init (root F, rest) (cloud · Opus)

`briefs/common.md` and `briefs/w7-shared.md` bind you, with ONE override to BASE FIRST: your base is
**`origin/land/w7-wipe`'s tip** (rebased onto land/w7, w7-tests2 the last landing on it). It is not
on main, so skip the is-ancestor check and report `git log -1 origin/land/w7-wipe`.

SPEC: `docs/specs/code-debt-cleanup.md`, the row **`seat`, `_peer_cycle` and `close_round` replay
production by hand**, read whole with the w7-tests and w7-tests2 commits on land/w7-wipe. Three
`seat` sites are left, because no offline door opens their graph:
- `test_identity.py::_gepa_seat` needs two prompt nodes, and the offline TermNorm serves only
  `llm_only`;
- `_view` and `_proposed` need an L4 outer whose run init walks whole inner campaigns.

DECIDED, one link each, in this order:
1. The offline world (`scripts/offline_world.py`) serves a node graph a test declares (the nodes and
   their prompt fields), next to `FakeBackend.answers`. `_gepa_seat` then opens through
   `factories.py::opened_round`.
2. An L4 outer opens through `opened_round` with its inner cells stood in by the offline world's
   answers. `_view` and `_proposed` take it.
3. `seat` and `close_round` go from `tests/factories.py`. Re-test: `git grep -c "def seat(\|def
   close_round(" -- tests/factories.py` is 0.

A fact a test asserts keeps its assertion. If link 2 needs a production door that does not exist
(the L4 outer's inner walk cannot be stood in from `tests/` and `scripts/`), stop it, report the door,
and still land link 1.

OWNS: `tests/**` and `scripts/offline_world.py`. Not `promptpotter/`.

LEAN: test lines fall. Report the pytest wall (`gate.py --only pytest`) at base and tip; it may not
rise more than the facts' own run init costs, and it is reported either way. Equals-main is
untouched. Run data: none.
