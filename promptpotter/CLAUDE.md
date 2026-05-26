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
- **L4 = recursion via the `promptpotter` connector**, not a 4th layer driver. Spec: [`../docs/specs/m12-multi-connector.md`](../docs/specs/m12-multi-connector.md); concept: [`../docs/concepts/optimizer-of-the-optimizer.md`](../docs/concepts/optimizer-of-the-optimizer.md).
- **Info-flow doc** (channels, signal routing, four wound channels): [`../docs/developer/dispatch-hub.md`](../docs/developer/dispatch-hub.md).
- **Layer-import invariant** (enforced by `tests/test_invariants.py`): `application/intelligence/` MUST NOT import from `application/optimization/`.
