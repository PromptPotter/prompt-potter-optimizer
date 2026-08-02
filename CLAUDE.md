# CLAUDE.md

> Read [`docs/architecture.md`](docs/architecture.md) first — §0 + §0.5 are what every PR measures against. Don't restate here.

## First — decide what KIND of ask this is, then load for it

**This repo is not only code.** The same root file serves launching a campaign, diagnosing a live run, editing a prompt, changing the engine, changing the webapp, and cutting a dataset — and those load almost **disjoint** context. Reading everything is not the answer; reading the wrong subset is how a run gets edited mid-flight or a prompt gets patched downstream of its cause. Classify first. Each row is *what to load*, then the **symptom** that kind produces when it goes wrong — the cure lives in the file the row points at, never in this table.

| The ask sounds like | Load | How this kind goes wrong |
|---|---|---|
| **Run / watch a campaign** — "run bbeh", "start it", "how's it going" | `/potter-run` · `.goldmine/latest.log` · [`operations/`](docs/operations/) | Fired and left; config edited mid-flight. |
| **Diagnose a live or stuck run** — "it's hung", "why did it stop" | ledger tail → `run_phase` → `.runtime/` flags → process → mtimes, **in that order** · [`persistence-and-state.md`](docs/operations/persistence-and-state.md) | Mtimes read first; a heartbeat called a hang. |
| **L4 / `promptpotter-self`** — self-improvement, the finish line | [`l4-outer-loop.md`](docs/specs/l4-outer-loop.md) § Finish line + § Running & supervising · `/l4-improve-l1-gen` | A leader believed from a panel that cannot resolve arms. |
| **Engine code (Python)** | the per-layer `CLAUDE.md` for the layer you touch · [`conventions.md`](docs/developer/conventions.md) | Green locally, red in CI — gated in the wrong env. |
| **Webapp code** | [`webapp/CLAUDE.md`](webapp/CLAUDE.md) · [`frontend-surface-contract.md`](docs/specs/frontend-surface-contract.md) · `/design` | A number computed in the browser. |
| **Prompt work — the optimizer's own prompts, not code** | `<dispatch-first>` below · [`optimization/CLAUDE.md`](promptpotter/application/optimization/CLAUDE.md) · [`dispatch-hub.md`](docs/developer/dispatch-hub.md) | A louder clause added downstream of the cause. |
| **Dataset / benchmark work** | [`datasets/CLAUDE.md`](datasets/CLAUDE.md) · [`adding-a-dataset.md`](docs/operations/adding-a-dataset.md) · [`dataset-reasoning-matrix.md`](docs/operations/dataset-reasoning-matrix.md) | Rows re-cut under the name they already had. |
| **Docs / specs** | [`docs/CLAUDE.md`](docs/CLAUDE.md) § Editing a doc · [`specs/CLAUDE.md`](docs/specs/CLAUDE.md) · `/spec-buddy` | Text added; nothing the change made untrue deleted. |
| **Cleanup / debt** | `/potter-debt-sweep` · [`code-debt-cleanup.md`](docs/specs/code-debt-cleanup.md) | Acted on a backlog entry that had already decayed. |

**Mixed asks are the norm** — a diagnosis becomes an engine change, a run becomes a prompt edit. Re-route when the kind changes instead of carrying the first row's context into the second. When the ask spans a run *and* a change: the run's rules win while it is live.

## Load-bearing

What this file owns, and where each rule is stated. Names only — the section is the text.

- Classify the ask before loading → § First — decide what KIND of ask this is, then load for it
- Delete shims, fallbacks and redundant paths → § STOP — no backward compatibility, ever
- Answer every gate before adding a concept → § Pre-flight gate
- Never commit or push unprompted → § Conventions
- Reach for a doctrine by its trigger → § Working principles
- The agent owns L4 end-to-end → § The closing directive
- Per-layer contracts — load only yours → § Pointers

## What this is

PromptPotter is **LLM-driven program evolution** for prompts and pipeline params. **Orchestration is the product — backends are pluggable and read-only.** Node tunables ride a per-call overlay (`datasets/{name}/pipeline.yaml::nodes.{name}.config`).

The **front door is a chat** — a human-in-the-loop copilot: the operator converses, the Potter posts its work into the thread Perplexity-style (tool calls, web search, each round as it lands), and decisions surface as buttons that fire existing control-plane verbs. The chat is a **canonical agent-chat template** built on a generic activity taxonomy — keep the core (thread model + activity stream + transport), delete the optimizer panes. Contract: [`docs/specs/chat-foundation.md`](docs/specs/chat-foundation.md).

**Origin = the starting configuration = C0 — one word, one thing**, and for a fork the point it branches *from*. An individual **is** a configuration (the origin resolves to an `OptSearchPoint`, the same type every candidate is), so "the config the loop starts from" and "C0, the first candidate" are one statement. **The general relation is *parent*** — the individual a candidate was mutated from (`RoundParent`), which is the origin at round 0 and the prior winner after it — so **reserve "origin" for offset 0 and the fork point, and say *parent* everywhere else**. Definitions, check-in, and the two gates: [`docs/architecture.md`](docs/architecture.md) §0.5.

**Fitness is never one fixed number — always ask "under which formula?"** It is formula-relative (**active** / **what-if** / **lens** / **replay**) and mode-relative (`measured` subset vs `all`). Every score — the active fitness, any alternative formula or mode, the hard-sample sort — is computed and **served by the backend; the webapp never recomputes.** Depth: [`docs/architecture.md`](docs/architecture.md) §0.5 (Composite-fitness resolution chain) + [`docs/concepts/scoring-and-memory.md`](docs/concepts/scoring-and-memory.md).

## STOP — no backward compatibility, ever

Zero released versions, zero stale on-disk data — nothing to be compatible with. **This is the rule that gets ignored most often.**

Delete on sight — don't ask, don't TODO, don't "remove later":
- **Shim code**, **Fallback chains**, **Breadcrumb comments**, **Redundant mechanisms** (a second validator / surface / code path doing the same *kind* of work as an existing one — fold it into the canonical mechanism, never add beside it).

**Lean to suspicion** — the remaining wins are small consolidations ignored precisely because each looks too small to bother. When a change *could* ride an existing channel, assume it *should*, and collapse redundant paths you pass mid-task instead of noting them for later. Fewer lines beats more lines at equal behavior.

**This covers ON-DISK SHAPE, not just code — and that is the half that keeps getting read backwards.** "No backward compatibility" is not permission to leave a bad shape alone because data exists in it; it is an instruction to **pick the right shape and then delete or migrate the data to fit**. Aggressively: wipe the store, restamp it, or partially both. The persistence layer is still being shaped, and it is far cheaper to shape it now than after release — **so a design decision must never be bent around what happens to be on disk today**. Measure whether the data is worth keeping (is the cache ever hit? is the field derivable from what sits beside it?), then act on the answer.

**Before invoking migration caution, COUNT the data — do not recall it.** `ls .promptpotter/projects/*/campaigns` answers in one command whether anything would actually have to migrate, and the dev store gets wiped often enough that a remembered number is usually wrong in the direction that makes you timid. An empty store means a shape change carries none of its usual risk.

<root-fix>
When a fix would compensate for something an upstream layer should already have made true, the fix belongs upstream — not at the site where the symptom shows up. Name the structural cause and propose the upstream fix <em>before</em> touching the visible surface. The operator can still pick the patch, but they pick it knowingly. Default to root, not to symptom.
</root-fix>

<dispatch-first>
**★ FIRST PRINCIPLE, THIS PHASE — fix it in the dispatch.** The target model is small: a prompt is one *information package* it must reason from, and it finds the right path only when that package is short and every line is unique, high-value, and in the right place. The **dispatch hub** (`application/optimization/dispatch/`) is where raw measurement is recomposed into those packages and wired into every optimizer prompt through **deterministic wires** (`DispatchHub.fill`). So the dispatch is the FIRST place to fix a bloated / low-value / misplaced prompt — and the one place to be **creative**: add functions, processing, recomposition, new panel logic, whatever shapes the incoming information to be *perfectly formed for its slot*. The wires stay deterministic; the intelligence lives in the shaping. Corollaries: a panel renders only what adds signal **for this task/state** and stays silent otherwise (the `answer_distribution` / suppressed-RANK rule); duplication, paraphrase, filler, and task-mismatched blocks are dispatch problems to reshape at source — never a mechanical downstream patch (a render-time dedup, a louder optimizer prompt clause) or a symptom papered over.
</dispatch-first>

## Working principles

Four *situational* guardrails against recurring AI blind spots live in [`docs/developer/conventions.md`](docs/developer/conventions.md) § Reasoning doctrine; reach for them by trigger:
- **the operator bounded ANY budget axis** → `<one-budget>`: a limit on one axis binds **all** of them, in both directions — "don't spend more" bounds the clock, "we don't have five hours" bounds the dollars. Price a proposal in the axis they named *and* the ones they didn't; trading one for another is an increase, and is asked for explicitly. When the budget binds, get more from measurements already paid for.
- **slow / costly / token-heavy LLM call** → `<simplify-the-problem>`: tighten the prompt so the model doesn't *need* the tokens; the timeout, cap and provider are safety rails, not the fix.
- **labelling a change "refactor" / LOC work** → `<surface-ledger>`: run `complexity_ledger`; a pass *called* refactor must move the total **down** — subtract a *named* concept, don't relocate one. It records rather than blocks: a raise that ships a feature is fine, it just costs a baseline edit and a written reason.
- **changed what the engine *decides*** (a gate / metric / state) → `<reach-the-operator>`: engine-correct ≠ product-complete; webapp parity is part of done, and you teach a new value rather than dumping it.

## Commands

```bash
pip install -e ".[all,dev]"
ruff check . && ruff format --check . && deptry . && mypy promptpotter/ && pytest -q   # CI runs same chain; run ruff format+check before any commit — CI fails on format drift
git config core.hooksPath .githooks                           # one-time per clone: pre-commit ruff (py) + tsc & eslint (webapp — `next build` checks neither)
python -m promptpotter new <name>                            # fresh: mint campaign+root cycle from datasets/<name>/, run from round 0
python -m promptpotter new <file.csv> --set task_description=…  # fresh from RAW file: ingest → resolve origin check-in → run
python -m promptpotter resume                                # resume active cycle; Ctrl+C: 1st saves, 2nd force-quits
python -m promptpotter resume --from N                       # rewind in place
python -m promptpotter resume --fork-on-divergence           # sibling cycle at divergence point
python -m uvicorn promptpotter.main:app --port 8001          # read-only API + webapp preview at the root (http://localhost:8001/)
```

`new` and `resume` are the loop-mint verbs; lifecycle (`archive`/`delete`/`unarchive`/`reset`), run-control (`pause` — stops a running cycle at its next checkpoint, resumable, the terminal's half of the webapp control), and diagnostic (`verify`/`ab`/`noise-floor`/`seed-screen`/`reindex`) verbs also exist. Reads happen by opening files; there is no read CLI. `.env` with `GROQ_API_KEY` (or OPENAI/ANTHROPIC/OPENROUTER) required; provider is per-dataset in `promptpotter/assets/optimizer/pipeline.yaml::nodes.{node}.config.provider`. What each verb does, its flags, identity and fork lineage → [`persistence-and-state.md`](docs/operations/persistence-and-state.md).

**The project file tree IS the dashboard**, alongside a read-only webapp at the root (Next.js at `webapp/`) polling the active cycle's `dashboard.json` every 2 s. The most-recent run's terminal readout (per-sample HIT/MISS, SP tables, round summaries) is mirrored ANSI-stripped to the gitignored **`.goldmine/latest.log`** — read it instead of asking the operator to paste console output (`LiveDisplay._write`). Onboarding: install → restart VS Code → `/potter-run`.

## Conventions

- Full style + code-shape + git rules → [`docs/developer/conventions.md`](docs/developer/conventions.md); enumerations → [`docs/glossary.md`](docs/glossary.md).
- **Git — don't commit by default:** **never `git commit` or `git push` unless the operator says so** (a commit ask is not a push ask). **Sole standing exception:** where a project instruction already grants autonomous commits — the L4 closing-phase "commit small green arcs" (§ The closing directive) and endorsed drain passes — and **even then never push**. Ruff format + check before any commit (see Commands).
- **Vocabulary:** say "origin" never "baseline"; "node" never service/building-block; **"optimizer prompt" never "meta-prompt"** — a prompt the optimizer *runs on*, whose opposite is the **target prompt** it *produces*; for L4 say **outer/inner** (position) and **self-optimization** (the arrangement), never "meta"; domain framing = evolution (generation/population/fitness/mutation/selection/individual).
- **Fewest dependencies possible** in both repos — reach for the stdlib or a small hand-rolled helper before adding a package; every new dependency must earn its place.

## Pre-flight gate

Before adding any new concept (class, projection, injection, prompt, field, dict, file), answer these. "I don't know" or "kind of" is a hard block:

- **Reuse before adding.** Does an existing channel/infrastructure already do this? Default **yes** — search/grep first. Ride the ledger / `INJECTIONS` / `OptSearchPoint` / dispatch hub; **no sidecar**.
- **Map to a §0 bucket.** If it fits none, stop — either §0 is incomplete (update it, separate PR landing first) or this is the wrong PR.
- **New I/O kind → amend §0 first.** The five are fixed (Persistence, Display, Control-local, Control-remote, Identity). New Control-remote command/event → declare schema in `docs/specs/m12-api-openapi.yaml` / `m12-events-asyncapi.yaml` *before* the handler lands.
- **Material facts land on disk, human-readable** — never surfaced only via stdout, in-memory state, or `--verbose`. Same for debug-state: if a bug needs an operator-only env to reproduce, the unblocker (mock/fixture/pin) is a separate PR landing first.
- **New LLM call or backend match → wrap with `observed_node()`.** Unwrapped LLM calls are an automatic block.
- **Names: distinct + self-describing.** Grep for collisions; a name that could mean three things gets renamed now — naming is cheap.

## The closing directive

**Ship a distributable `promptpotter-self`** — the optimizer optimizing its own optimizer prompts — **and open no new features until the config is distributable.** **The AI agent owns L4 end-to-end and drives it autonomously**: refine the plan, build the slices, commit small green arcs. Escalate to the operator ONLY for genuine actions — real multi-campaign spend approval, a provider/account change, or a compaction handoff. The living finish-line plan is [`docs/specs/l4-outer-loop.md`](docs/specs/l4-outer-loop.md) § Finish line; read status there and nowhere else, remembering that several of its items stayed marked "gating" for months after they shipped.

**What remains is empirical, not structural** — the loop, seams, recursion and scoring gateway all exist and are green; what remains is making the optimizer *behave well*, which is found by running it (`new promptpotter-self` on `justlogic-d234`, read what the loop produced, fix, re-run). **Every fix still goes to its ROOT (`<root-fix>` above)** — in this phase the root is usually a prompt rather than code, but that is an observation about where the causes have been, never a licence to patch the symptom. Name the cause, then pick the site. **Supervise every run actively — never fire-and-wait.** How to run + supervise + when to reach past prompts to code: [`docs/specs/l4-outer-loop.md`](docs/specs/l4-outer-loop.md) § Running & supervising.

⚠️ **Read the panel's resolving power before its result** — owned by [`docs/specs/l4-outer-loop.md`](docs/specs/l4-outer-loop.md) § Finish line, item 7, which `python -m promptpotter rank-optimizer-prompts` serves live. Do that before quoting any outer number anywhere in this repo.

**When adding a state, ask what DERIVES it — not just what writes it.** The costliest bugs in this package have all been one shape: a *state the system could enter but not report*, downstream of a predicate that conflated two facts, invisible because the failure mode was silence rather than an error. A writer with no reader is not a state; it is a note nobody reads.

## Pointers

- **Architecture:** [`docs/architecture.md`](docs/architecture.md) §0/§0.5 — backbone primitives, five I/O kinds, the central loop + L1/L2/L3 escalation + L4-is-recursion, searchpoints, scoring, identity, token/cost ledger. **Extend primitives in place** — the wrong shape is meant to be hard to express, not policed by a test.
- **Related work / competitive landscape:** [`docs/research/related-work.md`](docs/research/related-work.md) — the umbrella systems, the classical algorithm-configuration lineage, the MCTS mapping, and the adjacent harness-/weight-tuning agents. Read before positioning against a "competitor".
- **Roadmap:** three ways to run it — **(1)** hosted beta, **(2)** local `/potter-run` (local-secure), **(3)** the self-hosted **team-online** stack ([`deploy-linux/`](deploy-linux/); Cloudflare tunnel + OIDC allowlist + quotas). **Our setting is the cloud team-online deployment (tier 3), NOT the local-secure one (tier 2)** — treat cloud/team as the default operating context in everything we build and reason about; tiers 1 & 3 share this stack. What shipped and what is in flight: [`docs/specs/roadmap.md`](docs/specs/roadmap.md).
- **Persistence:** [`docs/operations/persistence-and-state.md`](docs/operations/persistence-and-state.md) — four-entity tree (Workspace → Dataset → Campaign → Cycle), `.promptpotter/` layout, `measurements/`, fork lineage, recovery.
- **Per-layer contracts** — load only the layer you touch. The index is [`promptpotter/CLAUDE.md`](promptpotter/CLAUDE.md); it routes to `domain/` · `application/` · `application/optimization/` · `infrastructure/` · `presentation/` · `connectors/` and says what each owns.
- **Contracts:** ADRs [`0001`](docs/adr/0001-m12-control-plane.md) (control plane) · [`0002`](docs/adr/0002-identity-foundation.md) (identity) · [`0003`](docs/adr/0003-spend-and-tenancy.md) (spend/tenancy). Index map: [`docs/CLAUDE.md`](docs/CLAUDE.md).
- **Design surface:** [`BRAND.md`](BRAND.md) (visual identity — theme-is-audience, dark=operator, light=buyer) + [`VOICE.md`](VOICE.md) (copy register — the Potter is force-multiplier, not friendly-wizard).
- **Skills:** [`.claude/skills/`](.claude/skills/) — `potter-run` (launch + supervise a campaign)
