# docs/specs -- Milestone Specs

## How to start a milestone

Each milestone has an executable spec in `docs/specs/`. One Claude Code session = one WBS work package.

**Steps:**
1. Read the milestone spec (`docs/specs/m{N}-*.md`) -- scope decisions, deliverables, API sketches
2. Read `docs/specs/wbs.md` to find your work package ID and dependencies
3. Read the service files listed in the deliverables table
4. Check the "Reading list per work package" table in the milestone spec for WP-specific files

| Milestone | Spec file | Pre-reading hint |
|-----------|-----------|-----------------|
| M5: Observability | Complete | See [`docs/observability.md`](../observability.md) for data exploration. |
| M6: PipelineSchema + Pipeline Composability | Waves 0-3 complete, Wave 4 → M7. [`m6-pipeline-composability.md`](m6-pipeline-composability.md) | Waves 5-6 (active): read `api/services/prompt_eval.py` (compute_accuracy) and `api/models/pipeline_schema.py` |
| M7: Multi-Connector | [`m7-multi-connector.md`](m7-multi-connector.md) | Read `docs/connectors/termnorm.md` and `api/services/backend_client.py` |
