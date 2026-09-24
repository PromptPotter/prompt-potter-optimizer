# promptpotter/ — package orientation

A thin index over the per-layer `CLAUDE.md` tree for the `promptpotter/` Python package. Each subpackage states its own rules; this file routes and owns only what sits *between* layers. Load the one you're touching.

> Architecture entry point: [`../docs/architecture.md`](../docs/architecture.md) §0 + §0.5 — read first.

## Per-layer contracts

| Subpackage | Owns | CLAUDE.md |
|---|---|---|
| `domain/` | Frozen Pydantic models, pure types, `JobSearchPoint` / `OptSearchPoint` / `PromptTemplate`, `PipelineSchema`. No I/O. | [`domain/CLAUDE.md`](domain/CLAUDE.md) |
| `application/` | Use-case layer: initialization, runner, scoring, intelligence. | [`application/CLAUDE.md`](application/CLAUDE.md) |
| `application/optimization/` | The L1 / L2 / L3 **agent contracts** + Cycle + dispatch + escalation + PoBB. What each layer reads / writes / decides, when each escalates / heals. | [`application/optimization/CLAUDE.md`](application/optimization/CLAUDE.md) |
| `application/evidence/` | The cross-subject read (`GET /evidence`, CLI `evidence`): what a subject is, what a cell can be asked for, and what the roster jointly says. | [`application/evidence/CLAUDE.md`](application/evidence/CLAUDE.md) |
| `infrastructure/` | I/O contracts: persistence (`CycleEventLog`), projections (`LiveDashboardProjection` / `AuditTrailProjection` / `PoBBStreamProjection`), stores, LLM clients, backend wire, tracing. | [`infrastructure/CLAUDE.md`](infrastructure/CLAUDE.md) |
| `presentation/` | Entry-point adapters: CLI, FastAPI, view formatters. Read-only over `application/`. | [`presentation/CLAUDE.md`](presentation/CLAUDE.md) |
| `connectors/` | Backend-specific hook bundles: `termnorm`, `promptpotter` (self-recursion / L4). Adding a connector = one new file under this package. | [`connectors/CLAUDE.md`](connectors/CLAUDE.md) |
| `judges/` | LLM-as-judge graders for SCORING — where no deterministic matcher can grade a cell. A judge is a measurement banked into the row, never a formula term, and is declared apart from every model the loop uses. | [`judges/CLAUDE.md`](judges/CLAUDE.md) |

## What the chain costs

Each subpackage's `CLAUDE.md` auto-loads by directory proximity and **deepest wins**, so working in `application/optimization/` pulls root, this index, `application/` and the layer's own — every word spent before you type a character. Two rules follow.

**A page you add to a layer is paid by everyone who edits there**, not just the reader who wanted it. So a fact belongs in the layer's `CLAUDE.md` only if it is a RULE binding a set of symbols; mechanism belongs at its definition site, in the module's own docstring, where it costs nothing until someone opens the file.

**Every page is capped** — `scripts/gate.py::_CLAUDE_MD_MAX_WORDS`; one that reaches it is trimmed or split.

## Where L4 lives

**Keep L4's law and its machinery in separate packages — the split is the point.**
**`domain/l4/`** is the LAW: `proxies` — what one finished inner cycle says about the optimizer prompt that ran it, the floor / exclude / measure trichotomy. It sits in `domain/` because it is pure over `CycleResult`, and that purity is what stops the law growing a file read or a session dependency. **`application/runner/inner/`** is the MACHINERY: `tasks` (the panel a dataset declares, and the validator that IS its type), `spawn_context` (what a task spawns under — and **the one reader of that panel during a run**: it resolves it once at publish and carries it, so an edit to `inner_tasks.yaml` mid-run cannot split a run's cells across two panels) + `spawn` (how one cell is run) and `ruler` (the ONE δ scale every cell of a round reads on, fit at the outer boundary — a cell left to fit its own derives it from the arms under test). That L4 is a recursion rather than a 4th `LayerStrategy` — and the `l4_*.py` ban that follows from it — is owned by [`application/optimization/CLAUDE.md`](application/optimization/CLAUDE.md) § Add no 4th LayerStrategy. Spec: [`../docs/specs/l4-outer-loop.md`](../docs/specs/l4-outer-loop.md).

## Ask the typed predicate, never a set of names

**A membership test written as a hand-authored set of dataset / node / stop-reason names is a bug.** It silently *skips* whatever it failed to list — an arm, a fork, a new enum member, a route — instead of rejecting it loudly, and it rots in both directions at once: names matching nothing stay, real ones go missing. Ask `stop_reason_outcome` (`domain/phases.py`), `backend_type_of_dataset` (`infrastructure/store/dataset_access.py`), or the connector registry; where a set is genuinely needed, *derive* it from the typed table rather than authoring it.

**The rule stays review-time; the ENFORCEMENT is per-site.** A repo-wide guard is inadmissible twice over — [`../tests/CLAUDE.md`](../tests/CLAUDE.md) excludes shape scans, and the shape is undecidable anyway: most literal name sets are legitimate external vocabularies (English stopwords, JSON-Schema type names, a provider's rate-limit scope codes). What is enforceable is one line beside the set, and the house pattern is `infrastructure/store/family_ray_queries.py`: `_NEVER_KINDS` derived from the one declaration, `_INNER_KINDS` kept as a curated subset and pinned by `assert _INNER_KINDS <= _VALID_KINDS`. **Derive when the set IS the type; assert the subset when it is a real choice within one; never leave it bare.**

## Owned elsewhere

- **The L1 / L2 / L3 agent contracts** — owned by [`application/optimization/CLAUDE.md`](application/optimization/CLAUDE.md), beside the code they govern.
- **Layer-import rule** — owned by [`application/CLAUDE.md`](application/CLAUDE.md) § Layer rule. `application/intelligence/` may not import `application/optimization/`.
- **Info-flow: channels, signal routing, the rendered wound signals** — owned by [`../docs/developer/dispatch-hub.md`](../docs/developer/dispatch-hub.md).
