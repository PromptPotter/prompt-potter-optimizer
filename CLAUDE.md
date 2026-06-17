# CLAUDE.md

> Read [`docs/architecture.md`](docs/architecture.md) first — §0 + §0.5 are what every PR measures against. Don't restate here.

## What this is

PromptPotter is **LLM-driven program evolution** for prompts and pipeline params. **Orchestration is the product — backends are pluggable and read-only.** Node tunables ride a per-call overlay (`datasets/{name}/pipeline.json::nodes.{name}.config`);

## Origin & check-in — the two words that confuse

These are distinct and constantly conflated. Keep them straight:

- **Origin** = the **complete specification required to start the potter loop** — the *starting program* the optimizer evolves from. It is everything needed to begin: the prompt fields, the per-node pipeline config, the **required inputs** a pipeline declares (query/target column map, answer space, and any node-type-raised dependency like a `candidate_source` node's candidate library), and the dataset binding. It is **per-pipeline** — different backends require different inputs — and it is **independent of measurement**: the origin exists fully formed *before* anything is scored. Scoring it produces round 0 / **C0** / `origin_accuracy`, but that measurement is *downstream of* the origin, **not part of its definition** (the recurring conflation: "origin" the spec vs "the origin's round-0 score"). Resolution: `resolve_origin_opt_search_point` (`application/origin.py`); scoring it is a separate step (`establish_campaign_origin`). Say "origin", never "baseline" (R-23).

- **Check-in** = the **process that produces a complete origin** from a raw upload. One LLM resolver node (`application/datasets/origin_resolve.py`) *proposes* the column map, the decomposed Layer-1 prompt fields, and the 7-field `task_context`; a deterministic, no-LLM **readiness gate** (`origin_readiness.py`) *gates* — mint is blocked until query + ground_truth + framing + answer-space are all CONFIRMED. The check-in **nudges the operator** (the ingest UI surfaces each open gap + unfulfilled pipeline dependency) until the spec is complete, then it's stored as the per-pipeline origin under `projects/{tenant}/datasets/{slug}/`. Dependencies (e.g. a candidate library) are dropped in place here and committed alongside the origin, not chased at bootstrap.

The line: **origin is the complete start specification; check-in is the resolver+gate that produces a complete one; round 0 / C0 is its measurement, downstream and separate.** Loop seam: `docs/architecture.md` §0.5; `docs/specs/roadmap.md § Origin check-in`.

## STOP — no backward compatibility, ever

Zero released versions, zero stale on-disk data — nothing to be compatible with. **This is the rule that gets ignored most often.**

Delete on sight — don't ask, don't TODO, don't "remove later":
- **Shim code**, **Fallback chains**, **Breadcrumb comments**, **Redundant mechanisms** (a second validator / surface / code path doing the same *kind* of work as an existing one — fold it into the canonical mechanism, never add beside it).

The codebase is mature: the remaining wins are low-value-but-they-all-count consolidations, and they get ignored precisely because each looks too small to bother. **Lean to suspicion.** When a change *could* ride an existing channel, assume it *should*; when you pass redundant paths mid-task, collapse them then — don't note-and-move-on. Fewer lines beats more lines at equal behavior.

<root-fix>
When a fix would compensate for something an upstream layer should already have made true, the fix belongs upstream — not at the site where the symptom shows up. Name the structural cause and propose the upstream fix <em>before</em> touching the visible surface. The operator can still pick the patch, but they pick it knowingly. Default to root, not to symptom.
</root-fix>

- **Never `git commit` or `git push` unless the operator says so** (a commit ask is not a push ask).

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
python -m uvicorn promptpotter.main:app --port 8001          # read-only API + webapp preview at the root (http://localhost:8001/)
```

`new` and `resume` are the loop-mint verbs; lifecycle (`archive`/`delete`/`unarchive`/`reset`) + diagnostic (`verify`/`compare`/`sweep`) verbs also exist. Reads happen by opening files (no read CLI). `.env` with `GROQ_API_KEY` (or OPENAI/ANTHROPIC/OPENROUTER) required; provider is per-campaign in `campaign.json::optimizer_llm.provider`. **Before any commit:** `python -m ruff format promptpotter/ tests/ && python -m ruff check promptpotter/ tests/` — CI fails on format drift. CLI flags, identity/cycle/campaign identity, fork lineage → [`docs/operations/`](docs/operations/) + [`persistence-and-state.md`](docs/operations/persistence-and-state.md).

The user is the operator. **The project file tree IS the dashboard**, plus a read-only webapp served at the root (Next.js at `webapp/`) polling the active cycle's `dashboard.json` every 2 s — used with the file tree, not in place of it. Onboarding: install → restart VS Code → `/potter-run`. The live terminal readout of the **most recent** run (per-sample HIT/MISS, SP tables, round summaries) is mirrored, ANSI-stripped, to the gitignored **`.goldmine/latest.log`** — read it instead of asking the operator to paste console output (`LiveDisplay._write`; captures the display stream, not `logging`-level warnings).

## Conventions

- Full style + code-shape + git rules → [`docs/developer/conventions.md`](docs/developer/conventions.md); enumerations → [`docs/glossary.md`](docs/glossary.md).
- **Fewest dependencies possible** in both repos — reach for the stdlib or a small hand-rolled helper before adding a package; every new dependency must earn its place.

## Known issues

- **TermNorm backend** lives at `C:\Users\dsacc\OfficeAddinApps\TermNorm-excel` (backend under `backend-api/`). It's not a third party — **same project as PromptPotter, split into a separate repo only for security reasons**, and the goal is to eliminate the split and fold it in when practical. **Whenever TermNorm is involved, you have direct access:** edit the local repo at that path directly (cross-repo edits authorized — runfish5 is the author and is the one coding it right now). If for some reason the local repo is unavailable, coordinate with **runfish5 on GitHub**. Always coordinate cross-repo changes explicitly, since the PP↔TermNorm highway is a shape contract — touch one side, fix both.

### Debugging the PP↔TermNorm highway (hard-won 2026-06-16 — read before flailing)

A long debug session taught these; future-me: be systematic and code-first, not operational-first.

- **Diagnose from the code path, not by restarting.** When the backend "goes down" — `/status` itself times out, scoring stalls — the cause is almost always a **blocking call in an `async def` request path**, not a crash / SQLite lock / double-start. Symptom→action: grep the handler for sync I/O (`requests`, `ThreadPoolExecutor.map`, `time.sleep`, blocking DB) FIRST. Killing/restarting the worker and theorizing about ports/timeouts is the slow path and hid the real bug for an hour. Root found: `web_generate_entity_profile` (async) ran `_brave_search` + `list(executor.map(scrape_url…))` synchronously, freezing the single uvicorn worker for the whole web step → every concurrent request (incl. `/status`) stalled. Fix = offload via `asyncio.to_thread` / `run_in_executor`. **Backend async hygiene is a standing check: no sync I/O on the event loop.**
- **The highway IS a cross-repo contract — change one side, fix both.** PP consumes TermNorm response *shapes*, so a shape change on either side silently breaks the other. Known coupling points: the error envelope is TermNorm's `{status, message, code}` (a global handler in `main.py`), **not** FastAPI's `{detail}` — PP must read `message`. Session-loss self-heal keys on a stable machine-readable `code: "no_session"` (prefer codes over substring/shape guessing). The web_search warning `stats` dict keys are read by PP's display. When you touch a response field, grep the *other* repo for its consumer.
- **`--reload` wipes the in-memory session every backend code edit.** TermNorm holds sessions in `user_sessions = {}` (process memory). Any backend edit → uvicorn reload → in-flight PP runs hit `400 no_session`. PP now self-heals (re-`POST /sessions` + retry); keep it that way — a developer editing the backend mid-run must not abort the campaign.
- **openrouter latency is the recurring root.** The same provider slowness hit (a) the optimizer (`datasets/_optimizer/pipeline.json` loop nodes at `reasoning_effort=high` + `max_tokens=20000` on openrouter/gpt-oss-120b → blew the 360s `OPTIMIZER_CALL_DEADLINE_S`×2 deadline → `OPTIMIZER_TIMEOUT` before round 1) and (b) `entity_profiling` (openrouter/gpt-oss-20b, ~20 tok/s, 47s tails). Survival guards: bounded optimizer reasoning (`medium`) + request timeouts under PP's 120s `QUERY_TIMEOUT`. The durable fix is provider (groq is far faster) — but that's the operator's daily-volume knob; don't flip it unprompted.
- **`web_search` is a swept strategy axis, not a fixed scraper (2026-06-17).** TermNorm's `web_search.config.strategy` has **3 reachable modes on the same one metered Brave query/match**: `snippets` (use Brave's returned text — instant, never hangs), `scrape` (full pages under a hard `scrape_budget` deadline — the structural fix that retires the old multi-minute scrape freeze), `hybrid` *(default — scrape + per-source snippet fallback)*. New cross-repo contract field: per-match `web_cost` (`{strategy, brave_queries, scrape_ok, scrape_failed, evidence_chars}`) on `/matches` + a langfuse observation — `brave_queries==1` is the free-tier ceiling, the rest is the efficiency signal to weigh against accuracy. Sweep `strategy` on the LCA set to pick the winner (PP-side overlay wiring in `datasets/lca-termnorm/pipeline.json` still pending). Backend rationale: `TermNorm-excel/backend-api/docs/WEB_SEARCH_STRATEGY.md`; PP seam: [`docs/operations/backend-integration.md`](docs/operations/backend-integration.md).

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
- **Contracts:** ADRs [`0001`](docs/adr/0001-m12-control-plane.md) (control plane) · [`0002`](docs/adr/0002-identity-foundation.md) (identity) · [`0003`](docs/adr/0003-spend-and-tenancy.md) (spend/tenancy). Index map: [`docs/CLAUDE.md`](docs/CLAUDE.md).
- **Design surface:** [`BRAND.md`](BRAND.md) (visual identity — theme-is-audience, dark=operator, light=buyer) + [`VOICE.md`](VOICE.md) (copy register — the Potter is force-multiplier, not friendly-wizard).
- **Dev playbook + learned rules:** [`.claude/skills/potter-dev/`](.claude/skills/potter-dev/) — APPLY before editing/investigating `promptpotter/`; on a coding correction, record the lesson via potter-dev LEARN.
