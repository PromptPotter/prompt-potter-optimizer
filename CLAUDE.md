# CLAUDE.md

> Read [`docs/architecture.md`](docs/architecture.md) first — §0 + §0.5 are what every PR measures against. Don't restate here.

## What this is

PromptPotter is **LLM-driven program evolution** for prompts and pipeline params. **Orchestration is the product — backends are pluggable and read-only.** Node tunables ride a per-call overlay (`datasets/{name}/pipeline.json::nodes.{name}.config`);

## STOP — no backward compatibility, ever

Zero released versions, zero stale on-disk data — nothing to be compatible with. **This is the rule that gets ignored most often.**

Delete on sight — don't ask, don't TODO, don't "remove later":
- **Shim code**, **Fallback chains**, **Breadcrumb comments**

<root-fix>
When a fix would compensate for something an upstream layer should already have made true, the fix belongs upstream — not at the site where the symptom shows up. Name the structural cause and propose the upstream fix <em>before</em> touching the visible surface. The operator can still pick the patch, but they pick it knowingly. Default to root, not to symptom.
</root-fix>

## Commands

```bash
pip install -e ".[all,dev]"
ruff check . && ruff format --check . && deptry . && mypy promptpotter/ && pytest -q   # CI runs same chain
git config core.hooksPath .githooks                           # one-time per clone: pre-commit ruff format + check
python -m promptpotter new <name>                            # fresh: mint campaign+root cycle from datasets/<name>/, run from round 0
python -m promptpotter new <file.csv> --set task_description=…  # fresh from RAW file: ingest → resolve origin check-in → run
python -m promptpotter resume                                # resume active cycle; Ctrl+C: 1st saves, 2nd force-quits
python -m promptpotter resume --from N                       # rewind in place
python -m promptpotter resume --fork-on-divergence           # sibling cycle at divergence point
python -m uvicorn promptpotter.main:app --port 8001          # read-only API + /ui webapp preview (http://localhost:8001/ui/)
```

`new` and `resume` are the only write verbs; reads happen by opening files (no read CLI). `.env` with `GROQ_API_KEY` (or OPENAI/ANTHROPIC/OPENROUTER) required; provider is per-campaign in `campaign.json::optimizer_llm.provider`. **Before any commit:** `python -m ruff format promptpotter/ tests/ && python -m ruff check promptpotter/ tests/` — CI fails on format drift. CLI flags, identity/cycle/campaign identity, fork lineage → [`docs/operations/`](docs/operations/) + [`persistence-and-state.md`](docs/operations/persistence-and-state.md).

The user is the operator. **The project file tree IS the dashboard**, plus a read-only `/ui` (Next.js at `webapp/`) polling the active cycle's `dashboard.json` every 2 s — used with the file tree, not in place of it. Onboarding: install → restart VS Code → `/potter-run`.

## Conventions

- Full style + code-shape + git rules → [`docs/developer/conventions.md`](docs/developer/conventions.md); enumerations → [`docs/glossary.md`](docs/glossary.md).
- **Fewest dependencies possible** in both repos — reach for the stdlib or a small hand-rolled helper before adding a package; every new dependency must earn its place.

## Known issues

- **TermNorm backend** lives in a sibling repo (`TermNorm-excel/backend-api`); clone alongside. It's not a third party — **same author, same project, just a separate repo for now**, and the goal is to eliminate the split and fold it in when practical. Cross-repo edits authorized; coordinate explicitly.

## Pre-flight gate

Before adding any new concept (class, projection, injection, prompt, field, dict, file), answer these. "I don't know" or "kind of" is a hard block:

- **Reuse before adding.** Does an existing channel/infrastructure already do this? Default **yes** — search/grep first. Ride the ledger / `INJECTIONS` / `OptSearchPoint` / dispatch hub; **no sidecar**.
- **Map to a §0 bucket.** If it fits none, stop — either §0 is incomplete (update it, separate PR landing first) or this is the wrong PR.
- **New I/O kind → amend §0 first.** The five are fixed (Persistence, Display, Control-local, Control-remote, Identity). New Control-remote command/event → declare schema in `docs/specs/m12-api-openapi.yaml` / `m12-events-asyncapi.yaml` *before* the handler lands.
- **Material facts land on disk, human-readable** — never surfaced only via stdout, in-memory state, or `--verbose`. Same for debug-state: if a bug needs an operator-only env to reproduce, the unblocker (mock/fixture/pin) is a separate PR landing first.
- **New LLM call or backend match → wrap with `observed_node()`.** Unwrapped LLM calls are an automatic block.
- **Names: distinct + self-describing.** Grep for collisions; a name that could mean three things gets renamed now — naming is cheap.

## Pointers

- **Architecture:** [`docs/architecture.md`](docs/architecture.md) §0/§0.5 — backbone primitives, five I/O kinds, the central loop + L1/L2/L3 escalation + L4-is-recursion, searchpoints, scoring, identity, token/cost ledger. **Extend primitives in place** — the wrong shape is meant to be hard to express, not policed by a test.
- **Roadmap:** multi-user beta at `https://app.promptpotter.dev` ([`deploy-linux/`](deploy-linux/); OIDC + allowlist + quotas). M0–M9 complete; M10–M13 in flight → [`docs/specs/roadmap.md`](docs/specs/roadmap.md).
- **Persistence:** [`docs/operations/persistence-and-state.md`](docs/operations/persistence-and-state.md) — four-entity tree (Workspace → Dataset → Campaign → Cycle), `.promptpotter/` layout, `archive/measurements/`, fork lineage, recovery.
- **Per-layer contracts** (load only the layer you touch): [`promptpotter/CLAUDE.md`](promptpotter/CLAUDE.md) (index) · [`application/CLAUDE.md`](promptpotter/application/CLAUDE.md) (orchestration + backend-overlay merge / never-edit-backend rule) · [`application/optimization/CLAUDE.md`](promptpotter/application/optimization/CLAUDE.md) (L1/L2/L3 agent contracts + L4 recursion + dispatch) · [`domain/CLAUDE.md`](promptpotter/domain/CLAUDE.md) · [`infrastructure/CLAUDE.md`](promptpotter/infrastructure/CLAUDE.md) (ledger + projections + stores) · [`presentation/CLAUDE.md`](promptpotter/presentation/CLAUDE.md) · [`connectors/CLAUDE.md`](promptpotter/connectors/CLAUDE.md).
- **Contracts:** ADRs [`0001`](docs/adr/0001-m12-control-plane.md) (control plane) · [`0002`](docs/adr/0002-identity-foundation.md) (identity) · [`0003`](docs/adr/0003-spend-and-tenancy.md) (spend/tenancy). Index maps: [`docs/CLAUDE.md`](docs/CLAUDE.md), `.ai/CODEMAP.md`.
- **Design surface:** [`.impeccable.md`](.impeccable.md) — theme-is-audience (dark=operator, light=buyer); the Potter is force-multiplier, not friendly-wizard.
