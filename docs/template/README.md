# Forking PromptPotter — Template Guide

PromptPotter ships as **a framework + one reference project** (TermNorm connector, AIME/GSM8K/BBEH/justlogic/… datasets, per-dataset scorers). Forking for a new Python + webtech project means swapping the **project specifics** while keeping the **framework** intact.

> Read [`../architecture.md`](../architecture.md) §0 + §0.5 first — that's the single page every fork measures against. Per-layer contracts live in `promptpotter/*/CLAUDE.md`.

## The split

| Layer | Who owns it |
|---|---|
| `promptpotter/domain/` | Framework. Frozen Pydantic models, pure types. Don't touch. |
| `promptpotter/application/` | Framework. Bootstrap, runner, scoring, intelligence, optimization, sweep. Don't touch. |
| `promptpotter/infrastructure/` | Framework. Ledger, projections (LiveDashboard, AuditTrail, PoBBStream, EventStream), stores, LLM clients, backend wire, tracing. Don't touch. |
| `promptpotter/presentation/` | Framework. CLI, FastAPI read-only API, view formatters. Don't touch. |
| `promptpotter/connectors/` | **Mixed.** The framework's connector Protocol lives in `domain/connector.py`. Each concrete connector (today: `termnorm.py`, `promptpotter.py` for L4 recursion) is a project artifact. **Add yours here.** |
| `datasets/<name>/` | **Project artifact.** One directory per dataset. Replace these with your own. |
| `webapp/` | Framework. Next.js read-only dashboard. Don't touch unless you're improving the framework. |

The framework reads `GET /pipeline` from your backend to discover tunable nodes; it reads `datasets/<name>/campaign.json` for scoring formula + optimization knobs; it reads `datasets/<name>/pipeline.json::nodes.{name}.config` for backend overlay. Three contracts, all on disk, all human-readable.

## The fork recipe

### 1. Add your connector

`promptpotter/connectors/<your_backend>.py` bundles three things behind the `Connector` shape (`promptpotter/domain/connector.py`):

- **WireAdapter** — outbound payload shaping: `(query, pipeline_params) → HTTP body`.
- **SessionProtocol** — session-lifecycle handshake for stateful backends (no-op for stateless).
- **Experiment-data extraction** — pulls measurement metadata off the response.

Look at `connectors/termnorm.py` as the reference. The wire layer, session lifecycle, and extraction logic all live in one file by design.

Register your connector in `promptpotter/connectors/__init__.py` and the bootstrap registry in `application/bootstrap/wiring.py`.

### 2. Add your datasets

For each dataset:

```
datasets/<name>/
  dataset.md              # human-readable description
  task_description.md     # what the LLM is being asked to do
  campaign.json           # scoring formula + optimization knobs + optimizer LLM
  pipeline.json           # backend overlay (nodes.{name}.config)
  prompts/{node}.json     # per-node prompt fields (PromptTemplate)
  <name>.json             # the actual samples (train/test split)
```

`campaign.json` shape is documented at `docs/operations/adding-a-dataset.md`. The `scoring` field is a formula string evaluated against the evaluator registry (`application/scoring/evaluators.py`); the framework ships `gsm8k_match`, `aime_match`, `exact_match`, `rr` as canonical answer-matcher functions you can reference from any project's scorer.

### 3. (Optional) Add evaluators

Need a metric the framework doesn't ship? Add an `Evaluator` to `application/scoring/evaluators.py::all_evaluators()`. Any new evaluator becomes addressable by name in any `campaign.json::scoring` formula.

### 4. (Optional) Swap the elimination strategy

The leader-elimination check is built at one swap point: `build_elimination_check` in `application/optimization/pobb/elimination/checks.py`. Today the only strategy is paired-sample PoBB; the docstring on that function describes the lifecycle contract a replacement strategy must satisfy. A second strategy gains a branch on a config field there + a per-strategy consumer split in `application/optimization/l1/score/loop.py`.

### 5. Run

```bash
pip install -e ".[all,dev]"
python -m promptpotter new <your_dataset>          # mint campaign + cycle, run from round 0
python -m promptpotter resume                      # resume the active cycle
python -m uvicorn promptpotter.main:app --port 8001  # serve the read-only /ui webapp
```

## What you get for free

- **Hexagonal layout** with enforced layer rules (`tests/test_invariants.py`).
- **L1/L2/L3 evolution loop** with critique-guided generate → score → critique, PoBB elimination, cross-run memory.
- **Self-healing rails** — four wound channels (validation / runtime / l2-guard / l3-guard) routed through the dispatch hub.
- **Persistence as a single ingress** — every event lands on the per-cycle `CycleEventLog`; four projections (`LiveDashboardView`, `AuditTrailView`, `PoBBStreamView`, `EventStreamView`) project to the on-disk dashboard files + SSE outbound.
- **Webapp dashboard** — read-only, polls `dashboard.json` every 2 s, lazy-fetches `round_NNNN.json` on drill-in.
- **Sweep + fork machinery** — per-cycle sibling-minting for cheap A/B ablation, automatic L2/L3 rebase proposals.
- **Multi-tenant identity foundation** (ADR-0002) — OIDC-ready Stage 0/1/2 staging.
- **Control-remote highway** (ADR-0001) — `POST /commands/{kind}` with `CommandDispatcher` for any operator-driven mutation.

## Constraints to honor

- **No backward compatibility** — `CLAUDE.md` STOP section. Rename, restructure, delete the old test, write the new one. No shims.
- **All material state on disk in human-readable form** — root CLAUDE.md AI-accessibility principle.
- **Never edit a backend's static config from inside this repo** — backend tunables ride the per-dataset `pipeline.json::nodes.{name}.config` overlay. Even backends you own.
- **Three I/O kinds** plus Control-remote (the 4th, shipped per ADR-0001). New I/O kinds need a §0 amendment first.

## Where to look next

- [`../architecture.md`](../architecture.md) — the load-bearing surface every PR measures against.
- [`../glossary.md`](../glossary.md) — domain vocabulary; check before introducing new words.
- [`../operations/adding-a-dataset.md`](../operations/adding-a-dataset.md) — full dataset-onboarding playbook.
- [`../operations/dataset-reasoning-matrix.md`](../operations/dataset-reasoning-matrix.md) — per-dataset model + `reasoning_effort` + `max_tokens` defaults.
- [`../../promptpotter/connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md) — connector boundary contract.
- [`../developer/conventions.md`](../developer/conventions.md) — full style + code-shape rules.
