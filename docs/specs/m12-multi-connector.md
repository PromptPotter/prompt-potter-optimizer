# M12: Multi-Connector, Competitor Comparison, L4 Closure, Composite Fitness

> **Status:** Partial — Track 1 (connector boundary + TermNorm) shipped (`ed95509`); remainder forward direction.

Second connector + competitor numbers + L4 closure + composite fitness still open.

## What this covers

Generalize the connector boundary (TermNorm is currently the only registered connector), close the publication with cited competitor numbers, run the L4 self-optimization closure (PromptPotter optimizing its own meta-prompts), and land multi-objective fitness (accuracy / money / time).

## Status

- **Connector boundary — shipped.** `Connector` dataclass at `connectors/protocol.py`; registry at `connectors/__init__.py`; TermNorm at `connectors/termnorm.py`. Four hooks per connector: `wire_adapter`, `session_factory`, `extract_experiment`, `resolve_ground_truth`.
- **Webapp control plane — extracted** to [`m10-operator-control-loop.md`](m10-operator-control-loop.md) (single-operator) + [`0001-m12-control-plane.md`](../adr/0001-m12-control-plane.md) (multi-user SaaS).

## Open items

- **Second connector — `promptpotter/connectors/promptpotter.py`** (PromptPotter-as-backend). Validates the boundary, unlocks Track 4, headlines the self-referential demo. Detailed at [`0001-m12-control-plane.md`](../adr/0001-m12-control-plane.md) cross-ref.
- **Config-driven connector lookup.** `bootstrap.py:514` hardcodes `connectors.get("termnorm")`; read `pipeline.json::backend_type`.
- **Query parser registry.** `split_query_parts()` (`services/backend_client.py`) still TermNorm-shaped; hoist into per-connector hook when second connector lands.
- **Workflow nodes** (M6 Wave 4 holdover) — unblocked by the connector boundary.
- **Competitor numbers.** DSPy / MIPROv2 / GEPA / Promptomatix / adv-CoT / PromptWizard — cited; MIPROv2 reproduction only if reviewers object.
- **L4 closure.** Outer-loop campaign on `datasets/promptpotter/` using the PromptPotter connector; `proxy_lift_corr ≥ 0.6` re-validation on the meta-task; findings doc at `docs/research/l4-self-optimization-results.md`.
- **Composite fitness.** Per-candidate cost / latency rollup → multi-objective post-aggregate formula. Phases: P1 surface data (done by [`spend-and-tenancy.md`](spend-and-tenancy.md)) · P2 per-candidate rollup + dashboard scatter · P3 `compile_post_aggregate_fitness(formula)` + `campaign.json::scoring_post_aggregate` · P4 Pareto-aware PoBB (M12+ stretch).
- **Multi-tenant `TenantId` / `UserId` newtypes + `IdentityContext`** — see [`identity-foundation.md`](identity-foundation.md) (contracts) + [`spend-and-tenancy.md`](spend-and-tenancy.md) (Stage-0 reification) + [`0001-m12-control-plane.md`](../adr/0001-m12-control-plane.md) (Stage-1 OIDC).
- **Prompt-injection Phase 2.** First-pass `fence_untrusted` already wraps `diagnostics` / `validation_failures` / `runtime_failures` in the dispatch bundle. Phase 2 covers: separate `TrustedText` / `UntrustedText` renderer types so the type system catches accidental concatenation at the call site; L1 + L1-critique output validators that flag suspected prompt-injection echoes in generated candidates; a cross-call repeat-detection circuit breaker that halts a cycle when the optimizer's own outputs start echoing untrusted dataset content verbatim.

## Code surface

| Area | Files |
|---|---|
| Connector boundary | `connectors/protocol.py`, `connectors/__init__.py` |
| TermNorm | `connectors/termnorm.py` |
| Backend client | `infrastructure/backend.py` |
| Pipeline discovery | `infrastructure/backend.py::fetch_pipeline` |
| Identity seam | `promptpotter/domain/identity.py` (newtypes), `promptpotter/shared/identity.py` (`IdentityContext`), `Session.identity` (per [`identity-foundation.md`](identity-foundation.md) + [`spend-and-tenancy.md`](spend-and-tenancy.md)) |
| Token usage | `domain/run_records.py::TokenUsageRecord` (canonical ledger record; emitted via `emit_token_usage` from `infrastructure/llm/models.py`, reads `_CYCLE_LEDGER` ContextVar) |
| Spend rollup | `infrastructure/projections/live_dashboard/view.py::LiveDashboardView._handle_token_usage` (sole writer) + `spend_total_used_usd` accessor; shapes + resume backfill in `infrastructure/projections/live_state.py`; rate resolution in `shared/spend.py` |
| Per-sample scorer | `application/scoring/formula/compiler.py::compile_scorer` |
