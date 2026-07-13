# promptpotter/ — package orientation

This file is a **thin index over the per-layer CLAUDE.md tree** for the `promptpotter/` Python package. Each subpackage owns its own contract; load only the one you're touching.

> Architecture entry point: [`../docs/architecture.md`](../docs/architecture.md) §0 + §0.5 — read first.

## Per-layer contracts

| Subpackage | Owns | CLAUDE.md |
|---|---|---|
| `domain/` | Frozen Pydantic models, pure types, `JobSearchPoint` / `OptSearchPoint` / `PromptTemplate`, `PipelineSchema`. No I/O. | [`domain/CLAUDE.md`](domain/CLAUDE.md) |
| `application/` | Use-case layer: bootstrap, runner, scoring, intelligence. | [`application/CLAUDE.md`](application/CLAUDE.md) |
| `application/optimization/` | The L1 / L2 / L3 **agent contracts** + Cycle + dispatch + escalation + PoBB. What each layer reads / writes / decides, when each escalates / heals. | [`application/optimization/CLAUDE.md`](application/optimization/CLAUDE.md) |
| `infrastructure/` | I/O contracts: persistence (`CycleEventLog`), projections (`LiveDashboardView` / `AuditTrailView` / `PoBBStreamView`), stores, LLM clients, backend wire, tracing. | [`infrastructure/CLAUDE.md`](infrastructure/CLAUDE.md) |
| `presentation/` | Entry-point adapters: CLI, FastAPI, view formatters. Read-only over `application/`. | [`presentation/CLAUDE.md`](presentation/CLAUDE.md) |
| `connectors/` | Backend-specific hook bundles: `termnorm`, `promptpotter` (self-recursion / L4). Adding a connector = one new file under this package. | [`connectors/CLAUDE.md`](connectors/CLAUDE.md) |

`shared/` and `config/` are leaf utilities — no CLAUDE.md needed.

## Cross-cutting

- **The agent contracts (L1 / L2 / L3) live in [`application/optimization/CLAUDE.md`](application/optimization/CLAUDE.md)** — that's where the code is.
- **L4 = recursion via the `promptpotter` connector**, not a 4th layer driver. Two homes, and the split is the point: **`domain/l4/`** is the LAW (`proxies` — what one finished inner cycle says about a meta-prompt, the floor/exclude/measure trichotomy; `verdict` — what a round of them says about a variant). It is pure, so the law cannot grow a file read. **`application/runner/inner/`** is the MACHINERY (`tasks` — the panel a dataset declares; `cycle` — how one cell is run). Spec: [`../docs/specs/l4-outer-loop.md`](../docs/specs/l4-outer-loop.md); concept: [`../docs/concepts/optimizer-of-the-optimizer.md`](../docs/concepts/optimizer-of-the-optimizer.md).
- **Info-flow doc** (channels, signal routing, the two rendered wound signals `l1_wounds`/`guard_breaches`): [`../docs/developer/dispatch-hub.md`](../docs/developer/dispatch-hub.md).
- **Layer-import invariant** (fails loud at import; see [`../tests/CLAUDE.md`](../tests/CLAUDE.md)): `application/intelligence/` MUST NOT import from `application/optimization/`.
- **A membership test over NAMES is a bug — ask the typed predicate.** A hand-written set of dataset / node / stop-reason names silently *skips* what it fails to list (an arm, a fork, a new enum member) instead of rejecting it loudly, and it goes stale in both directions at once: the `_FINISHED_STOP_REASONS` frozenset ended up with three strings matching no `StopReason` while dropping seven real ones. Use `stop_reason_outcome` (`domain/phases.py`), `backend_type_of_dataset` (`application/bootstrap/wiring.py`), the connector registry — and where a set is genuinely needed, *derive* it from the typed table rather than authoring it.
