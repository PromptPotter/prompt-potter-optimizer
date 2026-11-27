# w9-payer: a cell's payer is resolved per cell; `cell_payer` goes (cloud · Opus)

`briefs/common.md` and `briefs/w9-shared.md` bind you.

SPEC: `docs/specs/code-debt-cleanup.md`, two rows, one root:
1. **A library connector's cells are planned as the box's whatever keys the account holds**. Its row
   says a partial fix halts the run at its first cell, so every reader moves in one link.
2. **A connector's name is looked up again where its `Connector` is held**. The row's re-test greps
   `application/jobs` only. `application/pipeline_resolve.py:625` and
   `paper/bbeh-comparison/bbeh_potter_runner.py:163` pass `.name` too: they are in scope, and the
   re-test widens to `promptpotter/` and `paper/`.

Root: every reader resolves the payer by `infrastructure/llm/wallet.py::sending_key`'s rule.
`Connector.cell_payer` is deleted (18 production sites in 14 files), and `quota.py::sends_on` takes
the `Connector`.

OWNS: `application/jobs/quota.py`, `infrastructure/llm/wallet.py`, `connectors/protocol.py`, the
`cell_payer=` line of each connector (`dbllmbench`, `dspy_module`, `harbor`, `n8n`, `termnorm`,
`runner/inner/spawn.py`), `application/free_tier.py`, `infrastructure/backend.py`,
`application/runner/bench.py`, `runner/loop.py`, `runner/run_init.py`,
`jobs/launcher/mint_and_start.py`, `application/pipeline_resolve.py`, the bbeh runner's `.name`
call, and their tests (`tests/factories.py`: your own row only). Not yours:
`application/scoring/query_loop.py` (w9-pass's: edit its `self.payer` line only);
`jobs/launcher/admission.py` and `head_to_head.py` already pass the `Connector` (no edit expected).

Keys: none expected (offline cells cost $0). A key that moves stops the link: name it and why.
Wipe: none expected; name one if it happens.

LEAN: `promptpotter/` shrinks (expected about +120 / −150).
