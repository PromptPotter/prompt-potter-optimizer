# Concept map — where does concept X live?

Hand-curated orientation table for the "where does this concept live?" question.
It complements the per-package `__init__.py` orientation docstrings (each
subpackage's surface narrates its own module map) — this page is the cross-cutting
index over them, not a generated symbol dump. (The old `.ai/CODEMAP` generator was
deliberately removed; don't resurrect it.)

Each row names **one orienting entry** (read it first — it summarizes the rest) and
the **key files** the concept spans. Paths are repo-relative.

| Concept | Home (one orienting entry) | Spans (key files) |
|---|---|---|
| **self-healing / escalation / wounds** | `promptpotter/application/optimization/escalation/__init__.py` — the concept HOME: the control law *decides* (L1→L2→L3 routing via `decide.py`'s `decide_escalation` over `rules.py::DEFAULT_ESCALATION_RULES`) and *grounds the decision in evidence* (wounds). | `escalation/state.py` (`EscalationFSM`, lives on `Cycle.escalation`), `escalation/firing.py` (`escalate_l2`), `domain/escalation_signals.py` (signal + wound TYPES), `dispatch/hub/injections/wounds.py` (wound RENDERING), `runner/round.py` (the post-round routing call) |
| **scoring** | `promptpotter/application/scoring/__init__.py` — `score_search_point()` is the single scoring ingress (§0.5); everything else is the gateway's implementation. | `scoring/formula/round_scorer.py` (`compile_round_scorer`), `scoring/evaluators.py`, `scoring/sample_measurement.py` (`measure_sample`), `scoring/metrics.py` (composite fitness), `scoring/query_loop.py` (`run_query_loop`), `scoring/search_point_scorer.py`, `domain/scoring.py` (pure scoring types) |
| **dispatch / injections** | `promptpotter/application/optimization/dispatch/hub/__init__.py` — single info-ingress to every optimizer prompt; the `INJECTIONS` registry + `DispatchHub` (one `fill` path per node, from `NODE_LAYOUTS`). To add a prompt input, add an injection — anything else is drift. | `dispatch/hub/bundle.py` (frozen `InjectionBundle` types), `dispatch/hub/facade.py` (`DispatchHub`, `build_bundle`), `dispatch/hub/injections/registry.py` (`INJECTIONS`), `dispatch/llm_call/call.py` (`llm_call` — the sole optimizer LLM call seam) |
| **lineage / fork / resume** | `promptpotter/application/optimization/resume_and_fork/__init__.py` — the `--from N` / `--fork-on-divergence` machinery; one public surface, no sidecar fork-mint path. | `resume_and_fork/fork_siblings.py` (`_mint_fork`, the unified mint primitive), `resume_and_fork/replayers.py` (`replay_decisions` + `REPLAYERS`), `resume_and_fork/resume.py` (`resume_with_divergence_check`), `domain/run_records.py` (`ForkSpec` / `CycleSeed` / `ResumeCheckpointKind`) |
| **identity / tenancy** | `promptpotter/infrastructure/identity/__init__.py` — OIDC identity foundation (provider config + allowlist + sessions + OAuth flows + default-tenant claim). | `shared/identity.py` (`IdentityContext`, Stage-0 tenant scope), `infrastructure/store/stores.py` (`build_stores(identity, …)` — `Stores.tenant_id` derived from identity, never an independent field), `docs/adr/0002-identity-foundation.md` (contract) |
| **spend / budget** | `promptpotter/application/runner/termination.py` — `BudgetGate` (spend/token termination armed at the cycle boundary). | `shared/spend.py` (spend math types), `infrastructure/projections/live_dashboard/view.py` (the `spend` rollup — sole writer of `backend` + `loop` buckets via `_handle_token_usage`; halt probe reads `spend_total_used_usd`) |
| **persistence / ledger** | `promptpotter/infrastructure/ledger.py` — `CycleEventLog` (`events.jsonl`), the sole per-cycle persistence ingress; forks via `inherit_from`. | `infrastructure/projections/` (`live_dashboard/view.py`, `audit_trail.py`, `pobb_stream.py` — newtype-guarded read projections; `base.py` owns the `DerivedView` dispatch), `infrastructure/store/stores.py` (`build_stores` composite over leaf stores), `application/run_observers.py` (`RunCallbacks`, the writer-side API over `CycleEventLog.append`) |

See also: per-layer `CLAUDE.md` contracts under `promptpotter/*/` (load only the
layer you touch) and the info-flow doc [`dispatch-hub.md`](dispatch-hub.md).
