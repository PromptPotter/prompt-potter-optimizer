# docs/specs -- Milestone Specs

## How to start a milestone

Each milestone has an executable spec in `docs/specs/`. One Claude Code session = one WBS work package.

**Steps:**
1. Read the milestone spec (`docs/specs/m{N}-*.md`) -- scope decisions, deliverables, API sketches
2. Read `docs/specs/work-breakdown.md` to find your work package ID and dependencies
3. Read the service files listed in the deliverables table
4. Check the "Reading list per work package" table in the milestone spec for WP-specific files

| Milestone | Spec file | Pre-reading hint |
|-----------|-----------|-----------------|
| M6: PipelineSchema + Pipeline Composability | Waves 0-3, 5-7 complete; Wave 4 → M9. [`m6-pipeline-composability.md`](m6-pipeline-composability.md) | Read `api/services/prompt_eval.py` and `api/models/pipeline_schema.py` |
| M7: Optimizer-as-Pipeline | Complete. [`m7-optimizer-pipeline.md`](m7-optimizer-pipeline.md) | Read `api/services/campaign/feedback_cycle.py`, `api/config/optimizer_pipeline.json`, `api/models/opt_search_point.py` |
| M8: Campaign Intelligence | [`m8-campaign-intelligence.md`](m8-campaign-intelligence.md) | Read `api/services/prompt_eval.py`, `api/services/search/sensitivity_scan.py`, `api/services/l1_optimizer.py` |
| M9: Multi-Connector | [`m9-multi-connector.md`](m9-multi-connector.md) | Read `api/services/backend_client.py` |

Archived specs (superseded or deferred) are in `docs/specs/archive/`.
