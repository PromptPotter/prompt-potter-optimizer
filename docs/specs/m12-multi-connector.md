# M12: Multi-Connector, Competitor Comparison, L4 Closure, Composite Fitness

**Status:** Track 1 foundation shipped (`ed95509`). Second connector + competitor numbers + L4 closure + composite fitness open.

## What this covers

Generalize the connector boundary (TermNorm is currently the only registered connector), close the publication with cited competitor numbers, run the L4 self-optimization closure (PromptPotter optimizing its own meta-prompts), and land multi-objective fitness (accuracy / money / time).

## Status

- **Connector boundary — shipped.** `Connector` dataclass at `connectors/protocol.py`; registry at `connectors/__init__.py`; TermNorm at `connectors/termnorm.py`. Four hooks per connector: `wire_adapter`, `session_factory`, `extract_experiment`, `resolve_ground_truth`.
- **Webapp control plane — extracted** to [`m10-operator-control-loop.md`](m10-operator-control-loop.md) (single-operator) + [`m12-control-plane.md`](m12-control-plane.md) (multi-user SaaS).

## Open items

- **Second connector — `promptpotter/connectors/promptpotter.py`** (PromptPotter-as-backend). Validates the boundary, unlocks Track 4, headlines the self-referential demo. Detailed at [`m12-control-plane.md`](m12-control-plane.md) cross-ref.
- **Config-driven connector lookup.** `bootstrap.py:514` hardcodes `connectors.get("termnorm")`; read `pipeline.json::backend_type`.
- **Query parser registry.** `split_query_parts()` (`services/backend_client.py`) still TermNorm-shaped; hoist into per-connector hook when second connector lands.
- **Workflow nodes** (M6 Wave 4 holdover) — unblocked by the connector boundary.
- **Competitor numbers.** DSPy / MIPROv2 / GEPA / Promptomatix / adv-CoT / PromptWizard — cited; MIPROv2 reproduction only if reviewers object.
- **L4 closure.** Outer-loop campaign on `datasets/promptpotter/` using the PromptPotter connector; `proxy_lift_corr ≥ 0.6` re-validation on the meta-task; findings doc at `docs/research/l4-self-optimization-results.md`.
- **Composite fitness.** Per-candidate cost / latency rollup → multi-objective post-aggregate formula. Phases: P1 surface data (done by [`m11-spend-tracking.md`](m11-spend-tracking.md)) · P2 per-candidate rollup + dashboard scatter · P3 `compile_post_aggregate_fitness(formula)` + `campaign.json::scoring_post_aggregate` · P4 Pareto-aware PoBB (M12+ stretch).
- **Multi-tenant `TenantId` newtype + prompt-injection Phase 2** — see [`security-audit.md`](security-audit.md).

## Code surface

| Area | Files |
|---|---|
| Connector boundary | `connectors/protocol.py`, `connectors/__init__.py` |
| TermNorm | `connectors/termnorm.py` |
| Backend client | `infrastructure/backend.py` |
| Pipeline discovery | `infrastructure/backend.py::fetch_pipeline` |
| Tenant seam | `domain/tenant.py`, `Session.tenant` |
| Token usage | `domain/run_records.py::TokenUsageRecord` |
| Spend rollup | `infrastructure/projections/live_state.py`, `shared/spend.py` |
| Per-sample scorer | `application/scoring/formula/compiler.py::compile_scorer` |
