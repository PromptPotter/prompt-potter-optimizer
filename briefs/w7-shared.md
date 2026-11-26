# wave 7: what every w7 chain shares (read beside `briefs/common.md`)

Six chains run off **`origin/main` at `2c7dff34e`** (v0.8.21, tagged): w7-line, w7-host, w7-text,
w7-small, w7-open, w7-gate. Each owns the paths its brief lists; a holder outside them is cut (one
line under "rows to file"), per common.md's CENSUS FIRST.

- **A path another w7 brief names explicitly is that chain's**: you cut the row that needs it, never
  edit it. **A minimal re-point outside your Owns is allowed and is not a cut**: an import line, a call
  site of a symbol you moved or deleted, or a comment naming one; name each in the commit body.
- **Generated files** (`webapp/lib/types.generated.ts`, `docs/**/openapi*`, `events-asyncapi*`,
  `docs/specs/data-root.generated.json`): regenerate by their script on your branch where your gate
  needs it; the orchestrator regenerates once on the merged tree. Never hand-edit one.
- **`scripts/complexity_ledger.py::BASELINE`**: edit only your own rows, each with its reason in the
  commit body.
- **Hubs** (`application/read_params.py`, `domain/command_kinds.py`, `tests/test_spend.py`,
  `main.py::API_ROUTERS`, `webapp/lib/test-fixtures.ts`): add or rename only your own row, block or
  field; never reorder or rewrite another's. The orchestrator merges.
- **`promptpotter/assets/behaviour_keys.json`**: no w7 chain moves it. A link that moves a behaviour
  key stops, is reported with the key and why, and is not pushed as done.
- `docs/specs/roadmap.md` stays untouched (common.md). Name the roadmap item you close in the report;
  the orchestrator edits the roadmap at landing.
- The operator is asleep and nobody answers mid-run: a design fork your brief does not decide is
  decided by you along the root (CLAUDE.md § Ship the root), named under "decided in-run".
- Landing order (the orchestrator squashes each chain into one or a few commits): w7-host (it deploys
  first), w7-small, w7-text, w7-open, w7-line, w7-gate.
