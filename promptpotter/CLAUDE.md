# promptpotter/ — package orientation

A thin index over the per-layer `CLAUDE.md` tree for the `promptpotter/` Python package. Each subpackage states its own rules; this file routes and owns only what sits *between* layers. Load the one you're touching.

> Architecture entry point: [`../docs/architecture.md`](../docs/architecture.md) §0 + §0.5 — read first.

## Per-layer contracts

| Subpackage | Owns | CLAUDE.md |
|---|---|---|
| `domain/` | Frozen Pydantic models, pure types, `JobSearchPoint` / `OptSearchPoint` / `PromptTemplate`, `PipelineSchema`. No I/O. | [`domain/CLAUDE.md`](domain/CLAUDE.md) |
| `application/` | Use-case layer: initialization, runner, scoring, intelligence. | [`application/CLAUDE.md`](application/CLAUDE.md) |
| `application/optimization/` | The L1 / L2 / L3 **agent contracts** + Cycle + dispatch + escalation + PoBB. What each layer reads / writes / decides, when each escalates / heals. | [`application/optimization/CLAUDE.md`](application/optimization/CLAUDE.md) |
| `infrastructure/` | I/O contracts: persistence (`CycleEventLog`), projections (`LiveDashboardView` / `AuditTrailView` / `PoBBStreamView`), stores, LLM clients, backend wire, tracing. | [`infrastructure/CLAUDE.md`](infrastructure/CLAUDE.md) |
| `presentation/` | Entry-point adapters: CLI, FastAPI, view formatters. Read-only over `application/`. | [`presentation/CLAUDE.md`](presentation/CLAUDE.md) |
| `connectors/` | Backend-specific hook bundles: `termnorm`, `promptpotter` (self-recursion / L4). Adding a connector = one new file under this package. | [`connectors/CLAUDE.md`](connectors/CLAUDE.md) |

## Where L4 lives

**Keep L4's law and its machinery in separate packages — the split is the point.**
**`domain/l4/`** is the LAW: `proxies` — what one finished inner cycle says about the optimizer prompt that ran it, the floor / exclude / measure trichotomy. It sits in `domain/` because it is pure over `CycleResult`, and that purity is exactly what stops the law growing a file read or a session dependency, which is how it drifted before. **`application/runner/inner/`** is the MACHINERY: `tasks` (the panel a dataset declares), `spawn` (how one cell is run) and `ruler` (the ONE δ scale every cell of a round reads on, fit at the outer boundary — a cell left to fit its own derives it from the arms under test). That L4 is a recursion rather than a 4th `LayerStrategy` — and the `l4_*.py` ban that follows from it — is owned by [`application/optimization/CLAUDE.md`](application/optimization/CLAUDE.md) § Add no 4th LayerStrategy. Spec: [`../docs/specs/l4-outer-loop.md`](../docs/specs/l4-outer-loop.md).

## Ask the typed predicate, never a set of names

**A membership test written as a hand-authored set of dataset / node / stop-reason names is a bug.** It silently *skips* whatever it failed to list — an arm, a fork, a new enum member — instead of rejecting it loudly, and it rots in both directions at once: the `_FINISHED_STOP_REASONS` frozenset ended up holding three strings matching no `StopReason` while dropping seven real ones. It reaches route names too — `config/logging.py`'s poll filter named `/api/v1/active`, which nothing serves, so the poll it was written for logged every tick while `machine-status` was never listed at all. Ask `stop_reason_outcome` (`domain/phases.py`), `backend_type_of_dataset` (`application/initialization/wiring.py`), or the connector registry; where a set is genuinely needed, *derive* it from the typed table rather than authoring it.

**The rule stays review-time; the ENFORCEMENT is per-site.** A repo-wide guard is inadmissible twice over — [`../tests/CLAUDE.md`](../tests/CLAUDE.md) excludes shape scans, and the shape is undecidable anyway: sweeping the package for literal name sets turns up around twenty, most of them legitimate external vocabularies (English stopwords, JSON-Schema type names, a provider's rate-limit scope codes, an eval namespace). What is enforceable is one line beside the set, and the house pattern is `infrastructure/store/family_ray_views.py`: `_NEVER_KINDS` derived from the one declaration, `_INNER_KINDS` kept as a curated subset and pinned by `assert _INNER_KINDS <= _VALID_KINDS`. **Derive when the set IS the type; assert the subset when it is a real choice within one; never leave it bare.**

## Owned elsewhere

- **The L1 / L2 / L3 agent contracts** — owned by [`application/optimization/CLAUDE.md`](application/optimization/CLAUDE.md), beside the code they govern.
- **Layer-import rule** — owned by [`application/CLAUDE.md`](application/CLAUDE.md) § Layer rule. `application/intelligence/` may not import `application/optimization/`.
- **Info-flow: channels, signal routing, the rendered wound signals** — owned by [`../docs/developer/dispatch-hub.md`](../docs/developer/dispatch-hub.md).
