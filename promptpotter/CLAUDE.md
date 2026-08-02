# promptpotter/ — package orientation

A thin index over the per-layer `CLAUDE.md` tree for the `promptpotter/` Python package. Each subpackage states its own rules; this file routes and owns only what sits *between* layers. Load the one you're touching.

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

## Where L4 lives

**Keep L4's law and its machinery in separate packages — the split is the point.**
**`domain/l4/`** is the LAW: `proxies` (what one finished inner cycle says about the optimizer prompt that ran it — the floor / exclude / measure trichotomy) and `verdict` (what a round of them says about a variant). It sits in `domain/` because it is pure over `CycleResult`, and that purity is exactly what stops the law growing a file read or a session dependency, which is how it drifted before. **`application/runner/inner/`** is the MACHINERY: `tasks` (the panel a dataset declares) and `cycle` (how one cell is run). That L4 is a recursion rather than a 4th `LayerStrategy` — and the `l4_*.py` ban that follows from it — is owned by [`application/optimization/CLAUDE.md`](application/optimization/CLAUDE.md) § Add no 4th LayerStrategy. Spec: [`../docs/specs/l4-outer-loop.md`](../docs/specs/l4-outer-loop.md); concept: [`../docs/concepts/optimizer-of-the-optimizer.md`](../docs/concepts/optimizer-of-the-optimizer.md).

## Ask the typed predicate, never a set of names

**A membership test written as a hand-authored set of dataset / node / stop-reason names is a bug.** It silently *skips* whatever it failed to list — an arm, a fork, a new enum member — instead of rejecting it loudly, and it rots in both directions at once: the `_FINISHED_STOP_REASONS` frozenset ended up holding three strings matching no `StopReason` while dropping seven real ones. Ask `stop_reason_outcome` (`domain/phases.py`), `backend_type_of_dataset` (`application/bootstrap/wiring.py`), or the connector registry; where a set is genuinely needed, *derive* it from the typed table rather than authoring it.

## Owned elsewhere

- **The L1 / L2 / L3 agent contracts** — owned by [`application/optimization/CLAUDE.md`](application/optimization/CLAUDE.md), beside the code they govern.
- **Layer-import rule** — owned by [`application/CLAUDE.md`](application/CLAUDE.md) § Layer rule. `application/intelligence/` may not import `application/optimization/`.
- **Info-flow: channels, signal routing, the rendered wound signals** — owned by [`../docs/developer/dispatch-hub.md`](../docs/developer/dispatch-hub.md).
