# M12: Multi-Connector, Competitor Comparison, L4 Closure, Composite Fitness

> **Status:** Partial — Track 1 (connector boundary + TermNorm) shipped (`ed95509`); **config-driven lookup shipped** (`pipeline.json::backend_type` read in `bootstrap/wiring.py`, not hardcoded); **second connector `promptpotter.py` shipped + registered** (`connectors/__init__.py`); **control plane shipped** ([`ADR-0001`](../adr/0001-m12-control-plane.md)); **composite P1 = spend shipped** ([`ADR-0003`](../adr/0003-spend-and-tenancy.md)). Still open: the **L4 closure run** (inner-cycle dispatch + the actual campaign + `proxy_lift_corr` re-validation), **composite P2–P4**, **competitor numbers**.

L4-closure run + competitor numbers + composite P2–P4 still open; the connector *boundary* and the self-connector are done.

## What this covers

Generalize the connector boundary (TermNorm is currently the only registered connector), close the publication with cited competitor numbers, run the L4 self-optimization closure (PromptPotter optimizing its own meta-prompts), and land multi-objective fitness (accuracy / money / time).

## Status

- **Connector boundary — shipped.** `Connector` dataclass at `connectors/protocol.py`; registry at `connectors/__init__.py`; TermNorm at `connectors/termnorm.py`. Three hooks per connector: `wire_adapter`, `session_factory`, `extract_experiment`.
- **Webapp control plane** — the Control-remote contract at [`0001-m12-control-plane.md`](../adr/0001-m12-control-plane.md); single-operator surface decayed into [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md).

## Open items

- **Second connector — `promptpotter/connectors/promptpotter.py`** (PromptPotter-as-backend). Validates the boundary, unlocks Track 4, headlines the self-referential demo. Detailed at [`0001-m12-control-plane.md`](../adr/0001-m12-control-plane.md) cross-ref.
- **Query parser registry.** `split_query_parts()` (`services/backend_client.py`) still TermNorm-shaped; hoist into per-connector hook when second connector lands.
- **Workflow nodes** (M6 Wave 4 holdover) — unblocked by the connector boundary.
- **Competitor numbers.** DSPy / MIPROv2 / GEPA / Promptomatix / adv-CoT / PromptWizard — cited; MIPROv2 reproduction only if reviewers object.
- **L4 closure.** Outer-loop campaign on `datasets/promptpotter/` using the PromptPotter connector; `proxy_lift_corr ≥ 0.6` re-validation on the meta-task; findings doc at `docs/research/l4-self-optimization-results.md`.
- **Composite fitness.** Per-candidate cost / latency rollup → multi-objective post-aggregate formula. Phases: P1 surface data (done by [`ADR-0003 spend-and-tenancy`](../adr/0003-spend-and-tenancy.md)) · P2 per-candidate rollup + dashboard scatter · P3 `compile_post_aggregate_fitness(formula)` + `campaign.json::scoring_post_aggregate` · P4 Pareto-aware PoBB (M12+ stretch).
- **Multi-tenant `TenantId` / `UserId` newtypes + `IdentityContext`** — see [`ADR-0002 identity-foundation`](../adr/0002-identity-foundation.md) (contracts) + [`ADR-0003 spend-and-tenancy`](../adr/0003-spend-and-tenancy.md) (Stage-0 reification) + [`ADR-0001 m12-control-plane`](../adr/0001-m12-control-plane.md) (Stage-1 OIDC).
- **Prompt-injection Phase 2.** First-pass `fence_untrusted` already wraps `diagnostics` / `validation_failures` / `runtime_failures` in the dispatch bundle. Phase 2 covers: separate `TrustedText` / `UntrustedText` renderer types so the type system catches accidental concatenation at the call site; L1 + L1-critique output validators that flag suspected prompt-injection echoes in generated candidates; a cross-call repeat-detection circuit breaker that halts a cycle when the optimizer's own outputs start echoing untrusted dataset content verbatim.

## Track 1.5 — inner-cycle execution (L4 self-recursion)

The connector boundary lets PromptPotter be its own backend (`connectors/promptpotter.py`); Track 1.5 is the path that actually *runs* an inner cycle when a connector is in-process.

**Declared now (shipped).** A connector declares how its backend runs via `Connector.execution` (`ConnectorExecution`): `remote_http` (default — posts to a live `/matches`) or `in_process` (runs an inner cycle, L4). `BackendClient.run_query` **dispatches on this declared mode, never on the connector name** — transport is a capability a backend declares, not a branch in the core loop. `promptpotter` declares `execution="in_process"`; such a connector loads + validates normally (and is covered by the `test_every_connector_implements_protocol` completeness guard), then `run_query` raises a pointed `NotImplementedError` on the first match request rather than a confusing transport error against a backend that isn't there. A future hosted `service` / `worker` mode is a new enum value, dispatched on uniformly — no core-loop edit.

**Deferred (Lane C3).** The inner-cycle **run** itself — consuming the wire payload, running the inner cycle, producing the three proxy metrics (`proxy_lift_corr` re-validation on the meta-task). Two design options to settle when C3 lands:

- **Localhost endpoint.** Add `POST /inner/matches` to `promptpotter.main:app`. Cleanest boundary; generalizes to a worker/job in the hosted multi-tenant world (aligns with per-user quotas); requires uvicorn running alongside.
- **In-process dispatch.** `run_query`'s `in_process` arm dispatches to `runner.run_optimization` with isolated stores under `.runtime/inner/`. Faster, no extra process; keeps everything in one runtime.

Canonical layer doc: [`../../promptpotter/connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md) § "Execution mode (declared) + inner-cycle run (Lane C3)". Concept + cost realism: [`../concepts/optimizer-of-the-optimizer.md`](../concepts/optimizer-of-the-optimizer.md).

## Code surface

| Area | Files |
|---|---|
| Connector boundary | `connectors/protocol.py`, `connectors/__init__.py` |
| TermNorm | `connectors/termnorm.py` |
| Backend client | `infrastructure/backend.py` |
| Pipeline discovery | `infrastructure/backend.py::fetch_pipeline` |
| Identity seam | `promptpotter/domain/identity.py` (newtypes), `promptpotter/shared/identity.py` (`IdentityContext`), `Session.identity` (per [`ADR-0002`](../adr/0002-identity-foundation.md) + [`ADR-0003`](../adr/0003-spend-and-tenancy.md)) |
| Token usage | `domain/run_records.py::TokenUsageRecord` (canonical ledger record; emitted via `emit_token_usage` from `infrastructure/llm/models.py`, reads `_CYCLE_LEDGER` ContextVar) |
| Spend rollup | `infrastructure/projections/live_dashboard/view.py::LiveDashboardView._handle_token_usage` (sole writer) + `spend_total_used_usd` accessor; shapes + resume backfill in `infrastructure/projections/live_state.py`; rate resolution in `shared/spend.py` |
| Per-sample scorer | `application/scoring/formula/compiler.py::compile_scorer` |
