# small-11: a step's usage is its reply's; the MCP door owns its sessions (cloud)

`briefs/common.md` binds you. Base: `origin/main` (the tip at your start; it holds `ea020d03f`,
"a rate limit worded as quota stays a throttle"). Three disjoint links, one commit each, each
quoting its row's title from `docs/specs/code-debt-cleanup.md`; cut each row to what is left
(delete it when nothing is). Check each at its line first; a link that no longer names a defect
is reported, not built.

## Links
1. **A cell's step usage drops reasoning** (§ passes-6). `connectors/dspy_module.py::step_tokens`
   builds `StepUsage` from input/output alone; `connectors/harbor.py::_step_tokens` without
   `reasoning`; `infrastructure/llm/openai_compat.py::ReportedUsage.tokens` writes
   `cache_read=… or 0` (and `reasoning`, `cache_write` the same way) where `domain/spend.py`'s
   `TokenAccount` reads `None` as no breakdown reported. Root (the row's): `StepUsage` is built
   from the reply's `TokenAccount` by ONE constructor every connector calls, and an unreported
   breakdown stays `None` end to end. Grep every `StepUsage(` builder first (`dbllmbench.py`
   too) and name each in the commit. **Re-test:** a fact: a reply reporting reasoning tokens and
   no cache breakdown reaches a dspy cell's and a harbor trial's step usage with reasoning set and
   `cache_read is None`. Behaviour keys and `--digests` stay identical (usage is not in a key; if
   one moves, stop and report).
2. **The HTTP door's sweep reads the SDK's private `_server_instances`** (§ batch/29 · mcp),
   `presentation/mcp/server.py::_sweep`. Action per the row: the sweep rides the SDK's own
   session-end hook if the pinned `mcp` exposes one (read its source in the venv), else the door
   owns its session table (opened on the session's first request, closed on its end). **Re-test:**
   the row's (`git grep -n "_server_instances" -- promptpotter` is empty) plus a fact: a session
   that ends (DELETE) leaves the door's table at the next sweep, and an idle-expired one too if the
   SDK exposes the expiry.
3. **Two tests fail beside their claim**, its first half only: `tests/test_security.py` is not
   collectable in a venv lacking the `mcp` extra. Action: the mcp-needing facts skip on the
   missing extra the way the repo already does elsewhere (grep `importorskip`), the rest collect.
   Leave the row's `test_scoring.py::_peer_cycle` half filed (another chain owns `tests/` setup
   tonight). **Re-test:** collect `tests/test_security.py` with `mcp` hidden from the import
   (e.g. a `sys.modules` stub of `None` in a throwaway conftest you do not commit); report the count.

## Owns
`promptpotter/connectors/{dspy_module,harbor,dbllmbench}.py` (their usage builders only),
`promptpotter/infrastructure/llm/openai_compat.py` (`ReportedUsage` only), `promptpotter/domain/spend.py`
(`StepUsage` / `TokenAccount` only), `promptpotter/presentation/mcp/**`, `tests/test_security.py`,
`tests/test_spend.py`, `tests/test_connectors*.py` / `tests/test_harbor*.py` / `tests/test_dspy*.py`
as found, and the rows. NOT `promptpotter/infrastructure/store/**`, `application/jobs/*`,
`application/commands/*`, `application/runner/*`, `tests/factories.py`, `tests/test_scoring.py`,
`tests/test_archive.py` (store-2 runs beside you), NOT `application/optimizers/potter/`, NOT the
webapp.

## Target
Net ≤ 0 lines outside tests, or each added line named. `python scripts/gate.py --py` green (the full
gate if anything served moved). No visual change.

## Report
Per `common.md`, with a Symptoms list for anything off-row you saw.
