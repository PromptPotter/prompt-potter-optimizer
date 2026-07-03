# CLAUDE.md

> Read [`docs/architecture.md`](docs/architecture.md) first — §0 + §0.5 are what every PR measures against. Don't restate here.

## What this is

PromptPotter is **LLM-driven program evolution** for prompts and pipeline params. **Orchestration is the product — backends are pluggable and read-only.** Node tunables ride a per-call overlay (`datasets/{name}/pipeline.json::nodes.{name}.config`).

The **front door is a chat** — a human-in-the-loop copilot: the operator converses, the Potter posts its work into the thread Perplexity-style (tool calls, web search, each round as it lands), and decisions surface as buttons that fire existing control-plane verbs. The chat is a **canonical agent-chat template** built on a generic activity taxonomy — keep the core (thread model + activity stream + transport), delete the optimizer panes. Contract: [`docs/specs/chat-foundation.md`](docs/specs/chat-foundation.md).

**Origin vs check-in vs round-0 — constantly conflated, keep them straight:** *origin* = the complete specification the loop starts from (prompt fields + per-node pipeline config + the required inputs a pipeline declares + dataset binding), and it exists fully formed *independent of measurement*. *Check-in* = the resolver+gate process that produces a complete origin from a raw upload. *Round 0 / C0* = the origin's measurement — downstream and separate, not part of the origin's definition. Full definitions + the two gates (readiness + round-0 origin gate): [`docs/architecture.md`](docs/architecture.md) §0.5.

**Fitness is never one fixed number — always ask "under which formula?"** It is formula-relative (**active** / **what-if** / **lens** / **replay**) and mode-relative (`measured` subset vs `all`). Every score — the active fitness, any alternative formula or mode, the hard-sample sort — is computed and **served by the backend; the webapp never recomputes.** Depth: [`docs/architecture.md`](docs/architecture.md) §0.5 (Composite-fitness resolution chain) + [`docs/concepts/scoring-and-memory.md`](docs/concepts/scoring-and-memory.md).

## STOP — no backward compatibility, ever

Zero released versions, zero stale on-disk data — nothing to be compatible with. **This is the rule that gets ignored most often.**

Delete on sight — don't ask, don't TODO, don't "remove later":
- **Shim code**, **Fallback chains**, **Breadcrumb comments**, **Redundant mechanisms** (a second validator / surface / code path doing the same *kind* of work as an existing one — fold it into the canonical mechanism, never add beside it).

Many of the remaining wins are low-value-but-they-all-count consolidations, and they get ignored precisely because each looks too small to bother. **Lean to suspicion.** When a change *could* ride an existing channel, assume it *should*; when you pass redundant paths mid-task, collapse them then — don't note-and-move-on. Fewer lines beats more lines at equal behavior.

<root-fix>
When a fix would compensate for something an upstream layer should already have made true, the fix belongs upstream — not at the site where the symptom shows up. Name the structural cause and propose the upstream fix <em>before</em> touching the visible surface. The operator can still pick the patch, but they pick it knowingly. Default to root, not to symptom.
</root-fix>

**Working principles** — three *situational* guardrails against recurring AI blind spots live in [`docs/developer/conventions.md`](docs/developer/conventions.md) § Reasoning doctrine; reach for them by trigger:
- slow / costly / token-heavy LLM call → `<simplify-the-problem>`: tighten the prompt so the model doesn't *need* the tokens; the timeout/cap/provider are safety rails, not the fix.
- simplifying / labelling a change "refactor" / LOC work → `<surface-ledger>`: run `complexity_ledger`, the pass must move the total **down**; subtract a *named* concept, don't relocate one.
- changed what the engine *decides* (a gate / metric / state) → `<reach-the-operator>`: engine-correct ≠ product-complete — webapp parity is part of done; teach a new value, don't dump it.

## Commands

```bash
pip install -e ".[all,dev]"
ruff check . && ruff format --check . && deptry . && mypy promptpotter/ && pytest -q   # CI runs same chain; run ruff format+check before any commit — CI fails on format drift
git config core.hooksPath .githooks                           # one-time per clone: pre-commit ruff format + check
python -m promptpotter new <name>                            # fresh: mint campaign+root cycle from datasets/<name>/, run from round 0
python -m promptpotter new <file.csv> --set task_description=…  # fresh from RAW file: ingest → resolve origin check-in → run
python -m promptpotter resume                                # resume active cycle; Ctrl+C: 1st saves, 2nd force-quits
python -m promptpotter resume --from N                       # rewind in place
python -m promptpotter resume --fork-on-divergence           # sibling cycle at divergence point
python -m uvicorn promptpotter.main:app --port 8001          # read-only API + webapp preview at the root (http://localhost:8001/)
```

`new` and `resume` are the loop-mint verbs; lifecycle (`archive`/`delete`/`unarchive`/`reset`) + diagnostic (`verify`/`ab`/`sweep`) verbs also exist (`ab` = deterministic A/B replay — re-derive a recorded cycle's decisions under the current engine/scorer, zero LLM calls). Reads happen by opening files (no read CLI). `.env` with `GROQ_API_KEY` (or OPENAI/ANTHROPIC/OPENROUTER) required; provider is per-campaign in `campaign.json::optimizer_llm.provider`. CLI flags, identity/cycle/campaign identity, fork lineage → [`docs/operations/`](docs/operations/) + [`persistence-and-state.md`](docs/operations/persistence-and-state.md).

**The project file tree IS the dashboard**, alongside a read-only webapp at the root (Next.js at `webapp/`) polling the active cycle's `dashboard.json` every 2 s. The most-recent run's terminal readout (per-sample HIT/MISS, SP tables, round summaries) is mirrored ANSI-stripped to the gitignored **`.goldmine/latest.log`** — read it instead of asking the operator to paste console output (`LiveDisplay._write`). Onboarding: install → restart VS Code → `/potter-run`.

## Conventions

- Full style + code-shape + git rules → [`docs/developer/conventions.md`](docs/developer/conventions.md); enumerations → [`docs/glossary.md`](docs/glossary.md).
- **Git — don't commit by default:** **never `git commit` or `git push` unless the operator says so** (a commit ask is not a push ask). **Sole standing exception:** where a project instruction already grants autonomous commits — the L4 closing-phase "commit small green arcs" (see Current focus) and endorsed drain passes — and **even then never push**. Ruff format + check before any commit (see Commands).
- **Vocabulary:** say "origin" never "baseline" (R-23); "node" never service/building-block; domain framing = evolution (generation/population/fitness/mutation/selection/individual).
- **Fewest dependencies possible** in both repos — reach for the stdlib or a small hand-rolled helper before adding a package; every new dependency must earn its place.

## Known issues

- **TermNorm backend** lives at `C:\Users\dsacc\OfficeAddinApps\TermNorm-excel` (backend under `backend-api/`). It's not a third party — **same project as PromptPotter, split into a separate repo only for security reasons**; the goal is to eliminate the split and fold it in when practical. **Cross-repo edits authorized:** edit the local repo directly (runfish5 is the author, coding it now); if unavailable, coordinate with **runfish5 on GitHub**. The PP↔TermNorm highway is a shape contract — touch one side, fix both. Debugging war-stories (async hygiene, `--reload` session wipe, openrouter latency, `web_search` strategy axis) → [`docs/operations/backend-integration.md`](docs/operations/backend-integration.md) § Debugging the highway.

## Pre-flight gate

Before adding any new concept (class, projection, injection, prompt, field, dict, file), answer these. "I don't know" or "kind of" is a hard block:

- **Reuse before adding.** Does an existing channel/infrastructure already do this? Default **yes** — search/grep first. Ride the ledger / `INJECTIONS` / `OptSearchPoint` / dispatch hub; **no sidecar**.
- **Map to a §0 bucket.** If it fits none, stop — either §0 is incomplete (update it, separate PR landing first) or this is the wrong PR.
- **New I/O kind → amend §0 first.** The five are fixed (Persistence, Display, Control-local, Control-remote, Identity). New Control-remote command/event → declare schema in `docs/specs/m12-api-openapi.yaml` / `m12-events-asyncapi.yaml` *before* the handler lands.
- **Material facts land on disk, human-readable** — never surfaced only via stdout, in-memory state, or `--verbose`. Same for debug-state: if a bug needs an operator-only env to reproduce, the unblocker (mock/fixture/pin) is a separate PR landing first.
- **New LLM call or backend match → wrap with `observed_node()`.** Unwrapped LLM calls are an automatic block.
- **Names: distinct + self-describing.** Grep for collisions; a name that could mean three things gets renamed now — naming is cheap.

## Current focus — finishing L4 (agent-driven)

The project is in its **closing phase: ship a distributable `promptpotter-self`** (the optimizer optimizing its own meta-prompts). The L4 recursion is SHIPPED + live-validated; the remaining work is the **living finish-line plan** in [`docs/specs/l4-outer-loop.md`](docs/specs/l4-outer-loop.md) § Finish line (headroom inner benchmark `justlogic`, specialized `_optimizer_meta/` outer prompts — the gating slice, inner-spend rollup, bounded cheap default config). **The AI agent owns L4 end-to-end and drives it autonomously** — refine the plan, build the slices, commit small green arcs. Escalate to the operator ONLY for genuine actions: real multi-campaign spend approval, a provider/account change, or a compaction handoff. Do **not** open new features until the config is distributable.

**Close it bug-by-bug, not by adding infrastructure** — the loop, seams, recursion, and scoring gateway all exist and are green; what remains is making the optimizer *behave well*, found empirically (run `new promptpotter-self` on `justlogic`, read what the loop produced, fix, re-run). **Supervise every run actively — never fire-and-wait — and default the fix to the prompts.** How to run + supervise + when to reach past prompts to code: [`docs/specs/l4-outer-loop.md`](docs/specs/l4-outer-loop.md) § Running & supervising.

## Pointers

- **Architecture:** [`docs/architecture.md`](docs/architecture.md) §0/§0.5 — backbone primitives, five I/O kinds, the central loop + L1/L2/L3 escalation + L4-is-recursion, searchpoints, scoring, identity, token/cost ledger. **Extend primitives in place** — the wrong shape is meant to be hard to express, not policed by a test.
- **Roadmap:** multi-user beta at `https://app.promptpotter.dev` ([`deploy-linux/`](deploy-linux/); OIDC + allowlist + quotas). Engine + webapp + control plane + chat (Arc 1) + ingest shipped; Lane A (BYO keys) + Lane C (chat write-path, L4, composite fitness) in flight → [`docs/specs/roadmap.md`](docs/specs/roadmap.md).
- **Persistence:** [`docs/operations/persistence-and-state.md`](docs/operations/persistence-and-state.md) — four-entity tree (Workspace → Dataset → Campaign → Cycle), `.promptpotter/` layout, `archive/measurements/`, fork lineage, recovery.
- **Per-layer contracts** (load only the layer you touch): [`promptpotter/CLAUDE.md`](promptpotter/CLAUDE.md) (index) · [`application/CLAUDE.md`](promptpotter/application/CLAUDE.md) (orchestration + backend-overlay merge / never-edit-backend rule) · [`application/optimization/CLAUDE.md`](promptpotter/application/optimization/CLAUDE.md) (L1/L2/L3 agent contracts + L4 recursion + dispatch) · [`domain/CLAUDE.md`](promptpotter/domain/CLAUDE.md) · [`infrastructure/CLAUDE.md`](promptpotter/infrastructure/CLAUDE.md) (ledger + projections + stores) · [`presentation/CLAUDE.md`](promptpotter/presentation/CLAUDE.md) · [`connectors/CLAUDE.md`](promptpotter/connectors/CLAUDE.md).
- **Contracts:** ADRs [`0001`](docs/adr/0001-m12-control-plane.md) (control plane) · [`0002`](docs/adr/0002-identity-foundation.md) (identity) · [`0003`](docs/adr/0003-spend-and-tenancy.md) (spend/tenancy). Index map: [`docs/CLAUDE.md`](docs/CLAUDE.md).
- **Design surface:** [`BRAND.md`](BRAND.md) (visual identity — theme-is-audience, dark=operator, light=buyer) + [`VOICE.md`](VOICE.md) (copy register — the Potter is force-multiplier, not friendly-wizard).
- **Dev playbook + learned rules:** [`.claude/skills/potter-dev/`](.claude/skills/potter-dev/) — APPLY before editing/investigating `promptpotter/`; on a coding correction, record the lesson via potter-dev LEARN.
