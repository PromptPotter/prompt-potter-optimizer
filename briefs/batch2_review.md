# batch/2 review (origin/batch/1..origin/batch/2, 5 arcs)

**Verdict:** land after one fix: w2f leaves the CLI (and so `/potter-run`) with no way to send `step_rounds`. Everything else is clean.

## INTRODUCED

1. **The CLI lost its only door to `step_rounds`**: `presentation/cli/commands/resume_command.py:61-69` (`_start_run` builds `StartRunPayload` without `step_rounds`) and `presentation/cli/commands/verbs.py:172` (`resume` has no `--step-rounds`).
   - Defect: on main the CLI reached `step_rounds` only through the generated `step-cycle --cycle X --step-rounds N` (`command_verb`). START_RUN's CLI verb is the hand-written `resume`, and it never offered the field. The w2f row says step-cycle is "a second door into `start-run {step_rounds}`", which is true for REST, the MCP `start-run` tool and `embedded_run`, but false for the CLI.
   - Scenario: an operator at the terminal, or `/potter-run`, wants a paused cycle to run one round and stop (`PauseCause.STEP`, `stepped`). No verb does it. `resume --max-rounds 1` sets a budget stop, which is a different ending.
   - Invariant broken: entry-point parity (root `CLAUDE.md` § Non-negotiables). The operator's own route, "advancing a round needs `campaign.run` (`start-run {step_rounds}`)", has no CLI spelling.
   - What holds it: `resume` takes `--step-rounds` into `StartRunPayload.step_rounds`. A tests/CLAUDE.md test is not admitted (a missing flag is a visible symptom). The structural form is an import-time check in `verbs.py` that every field of a written verb's payload model has an `Arg`, the way `command_verb` derives them.

## Asked judgements

- **w2g `p_served` 176→178: honest.** `halt_at_accuracy` is served on `OptimizationConfig` and `OptimizationOverrides`. On main, the only "read by name" was `args.halt_at_accuracy` in `new.py:103`. That is an argparse attribute that happened to share the property's name, so main undercounted by 2. The knob is live (`runner/termination.py::target_tripped`, `--set`, REST/MCP payloads), so nothing should go. Across the batch the count is net 0: w2f took it 178→176.
- **defaults.yaml reliance: none.**
  - Research: `promptpotter-self/family.yaml` names its own `backend_type`, `available_models` and `rounds`.
  - Tenant tier: gets no defaults (`dataset_access.py::tenant_dataset`).
  - Tests: build an install root under tmp with no `defaults.yaml` (`factories.py:337`).
  - bbeh: a tenant dataset.
  - Docs: `datasets.md` § Adding a dataset writes a tenant definition WHOLE.
  - Install: `justlogic/d234`, `d234-held` inherit from `family.yaml`.
- **node_roles: draft `dependencies` unchanged.**
  - Only TermNorm has `serves_uploads`, so drafts are TermNorm-only.
  - TermNorm HEAD `backend-api/config/pipeline.json:29,180` marks `candidate_source` on `fuzzy_matching` and `token_matching`.
  - `active` and the roles both come from the same `pipeline.chain`.
  - Core serves only `ranker` (dspy_module, inner tasks, defaults template), which `dependencies_from_node_roles` ignores.
- **Five entry points:** no `.claude/` skill, MCP listing, doc, webapp source or e2e spec names `step-cycle` (CHANGELOG only). The openapi and TS diff is exactly the path, `StepCyclePayload` and the kind entry.
- **w2d2 hand-resolved hunks:** correct. The row is gone; roadmap M4b drops only "the `BackendClient` forwarders". The census grep is clean: every remaining hit reads a Connector, the wiring record, the bundle or the facet.
- **Edits outside Owns:** `scripts/complexity_ledger.py` (w2f, w2g; the baseline edit root `CLAUDE.md` requires; net 0) and `docs/specs/roadmap.md:164` (w2d2, a doc it made stale). No action.
- No transition code found.

## PRE-EXISTING (rows)

- `webapp/` has no sender of `start-run {step_rounds}`. Row: "The browser cannot advance a paused cycle N rounds: `StartRunPayload.step_rounds` has no webapp sender, and it is not a named browser carve-out. **Rides with:** M6."
- `infrastructure/backend.py:173-175` `BackendClient.cell_hop` is a None-guarded forwarder over `connector.cell_hop`, with one caller (`scoring/sample_measurement.py:607`). Row: "`BackendClient.cell_hop` is the last forwarder beside `.connector`: its caller reads `client.connector.cell_hop` and keeps the None test. **Rides with:** M4b."

## Stale lines

- `docs/specs/code-debt-cleanup.md:373` (INTRODUCED, w2f): the re-test says "queue a `step` from the CLI behind a running cycle", and that verb is gone. It should read `start-run {step_rounds: 1}` over MCP/REST, or `resume --step-rounds 1` once finding 1 lands.
- `docs/specs/code-debt-cleanup.md:380` (INTRODUCED, w2g): the rewritten line is not re-wrapped (~135 chars). Cosmetic.
- Root `CLAUDE.md`: nothing stale.
