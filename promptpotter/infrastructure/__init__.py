"""infrastructure/ — I/O contracts: persistence, projections, stores, LLM, wire.

ledger.py — CycleEventLog (.runtime/ledger.jsonl), the sole per-cycle persistence ingress.
projections/ — newtype-guarded read views (live_dashboard, audit_trail,
  pobb_stream, event_stream); base.py owns the DerivedView dispatch.
store/ — Stores composite + build_stores(identity); leaf stores + MeasurementArchive.
identity/ — OIDC foundation (provider config, allowlist, sessions, OAuth).
llm/ — OpenAICompatibleClient + AnthropicClient + the emit_* telemetry seam.
backend.py — connector-agnostic BackendClient wire.
tracing/ — fan-out only, no read API.

full contract: infrastructure/CLAUDE.md
"""
