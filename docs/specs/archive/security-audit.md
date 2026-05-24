# Security audit — first hardening pass

First-pass audit completed 2026-05-05. Inventory below. Deferred items track M12 / webapp-hardening work; update the status column when they land.

## Threat model

- **Today.** Operator-local; trusted inputs are `campaign.json`, `datasets/{name}/*`, `pipeline.json`, `.env`. Untrusted-by-origin (flow into LLM context): dataset rows, pipeline warning strings.
- **M11 horizon.** Read-only FastAPI; cycle ids URL-addressable.
- **M12 horizon.** Multi-tenant + writeable webapp; campaigns / datasets / formulas may come from untrusted uploaders.

Axes evaluated: RCE via formula · path traversal in cycle/batch ids · credential leak via logs · prompt injection via dataset content · unauthenticated wire · multi-tenant data leakage · webapp endpoint hardening.

## Inventory

| # | Vulnerability | Severity | Status | Pointer |
|---|---|---|---|---|
| 1 | Restricted-eval bypass in `compile_scorer` (RCE) | P0 | LANDED | `application/scoring/formula/compiler.py::validate_ast` |
| 2 | Missing `validate_path_component` on `root_dir_for` / `sweep_batch_dir_for` | P1 | LANDED | `infrastructure/store/paths.py:63,91` |
| 3 | No log redaction for API-key values | P2 | LANDED | `config/log_redaction.py` |
| 4 | Prompt injection via dataset content (`diagnostics`, `validation_failures`, `runtime_failures`) | P1 (starter) | LANDED | `application/optimization/dispatch/hub/bundle.py::fence_untrusted` |
| 5 | TermNorm wire unauthenticated | P2 / P0-shared | LANDED (opt-in) | TermNorm `config/middleware.py::bearer_auth_middleware`; PP `infrastructure/backend.py::auth_token` |
| 6 | `tenant_id` not type-enforced — multi-tenant leakage risk | P1 at M12 | DEFERRED-M12 | `SafeName` / `TenantId` newtypes alongside `CycleDir` / `RootCycleDir`; migrate every store constructor in one coordinated diff |
| 7 | Webapp endpoint hardening | P0 at shared M11 | DEFERRED-WEBAPP | auth dep on every router; tighten `ALLOWED_ORIGINS`; Pydantic `extra=forbid`; slow-API rate limiter on cycle reads. M12 writeable adds CSRF + upload size/shape limits. |
| — | Prompt-injection Phase 2 | P1 at M12 | DEFERRED-M12 | separate `TrustedText` / `UntrustedText` renderer types; L1/L1-critique output validators; cross-call repeat detection circuit breaker |

## Test surface

`tests/test_security.py` (redaction, path-traversal, fence) · `tests/test_scoring.py` (AST validator — production formulas compile; `__class__` / `__import__` / lambda / comprehension / walrus rejected).

End-to-end TermNorm-bearer smoke (manual): start TermNorm with `TERMNORM_REQUIRE_AUTH=1` + token; matching `TERMNORM_TOKEN` in PromptPotter `.env`; campaign succeeds. Unset PromptPotter side → 401. After a run, `grep` `output.log` / `dashboard.json` / `events.jsonl` for the `GROQ_API_KEY` value — no hits.

## Cross-refs

Webapp items consumed by [`m11-publication-benchmarks.md`](m11-publication-benchmarks.md); TenantId + prompt-injection Phase 2 consumed by [`m12-multi-connector.md`](m12-multi-connector.md).
