# w11-connectors2: area 08, round 2: a send's failure is typed at its door (cloud · Opus)

`briefs/common.md`, `briefs/w10-shared.md` and `briefs/w11-shared.md` bind you. SPEC: `briefs/map.md`
§ 08 and the debt doc's `## 08` section. w11-connectors filed most of these rows.

Target:
- Connectors are the only place backend identity is named.
- A send's failure reaches core as one typed fault, never raw.
- Clients are `ProviderSpec` with explicit provider selection.

Links, deletion first:
1. **A harbor trial's harness fault escapes raw** and **dspy's send raises its raw failure**. They share
   one root: each adapter's failure becomes the typed fault core reads (`send_failure.py`), at the
   adapter's door.
2. **`send_pacing.py::SendBudget` stops at the relay's door**: work it on its root.
3. **A draft serves every optimizer model twice, once under an empty provider**: one entry per
   (provider, model), resolved where the capabilities are read.
4. **Harbor's identity is named in core**: its holders move behind the connector. The map lists
   holders outside 08's files; re-point those only, and name each.
5. **The offline world sleeps an LLM resend on the wall clock**: the world's resend reads the world's
   clock. `scripts/offline_world.py` is area 14's file, so make a minimal re-point only and name it in
   the report.

Not now:
- The 4 TermNorm rows wait on TermNorm's `xconn` merge, which is the operator's.
- **A remote backend's cells ask the bound account** is area 05's payer; w11-spend is in flight.
- **The box keeps 21 dead producers' locks** waits on a box read.

OWNS: `promptpotter/connectors/**`, `infrastructure/llm/**` except `spend_book.py`, `wallet.py`,
`pricing.py` and `send_pacing.py`'s budget arithmetic, which are w11-spend's. You may edit
`send_pacing.py`'s relay door only, named in your report. Also `infrastructure/backend.py`, and their
tests.

DONE WHEN:
- No adapter raises an untyped provider failure into core: a test per adapter, through a world door.
- No `model_capabilities` key is the empty string.
- Equals-main names each move.
- The keys are named.
- The gate is green.
