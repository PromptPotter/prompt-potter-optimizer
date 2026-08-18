# CLAUDE.md

> Read [`docs/architecture.md`](docs/architecture.md) first — §0 + §0.5 are what every PR measures against. Don't restate here.

## What this is

PromptPotter is **LLM-driven program evolution** for prompts and pipeline params. **Orchestration is the product — backends are pluggable and read-only.** Node tunables ride a per-call overlay (`datasets/{name}/pipeline.yaml::nodes.{name}.config`). 

**Our setting is the cloud team-online deployment (tier 3), NOT the local-secure one (tier 2)** — treat cloud/team as the default operating context in everything we build ([`deploy-linux/`](deploy-linux/): Cloudflare tunnel + open OIDC signup + a per-account spend ceiling). What shipped and what is in flight: [`docs/specs/roadmap.md`](docs/specs/roadmap.md).

**Origin = the campaign root = C0**, and for a fork the point it branches *from*. An *individual/candidate* is a configuration (they both are of the class `OptSearchPoint`). **The general relation is *parent*** — the individual a candidate was mutated from (`RoundParent`), which is the origin at round 0 and the prior winner after it.

**Fitness is never one fixed number — always ask "under which formula?"** It is formula-relative (**active** / **what-if** / **lens** / **replay**) and mode-relative (`measured` subset vs `all`). Every score — the active fitness, any alternative formula or mode, the hard-sample sort — is computed and **served by the backend; the webapp never recomputes.** Depth: [`docs/architecture.md`](docs/architecture.md) §0.5 (Composite-fitness resolution chain) + [`docs/concepts/scoring-and-memory.md`](docs/concepts/scoring-and-memory.md).

## First — decide what KIND of ask this is, then load for it

**This repo is not only code.** The same root file serves launching a campaign, diagnosing a live run, editing a prompt, changing the engine, changing the webapp, and cutting a dataset — and those load almost **disjoint** context. Reading everything is not the answer. Each row is *what to load*, then the **symptom** that kind produces when it goes wrong — the cure lives in the file the row points at, never in this table.

| The ask sounds like | Load | How this kind goes wrong |
|---|---|---|
| **Run / watch a campaign** — "run bbeh", "start it", "how's it going" | `/potter-run` · `logs/latest.log` · [`persistence-and-state.md`](docs/operations/persistence-and-state.md) (the verbs, resume/rewind/fork) · [`observability.md`](docs/operations/observability.md) (what is traced, the P(best) stream) | Fired and left; config edited mid-flight. |
| **Diagnose a live or stuck run** — "it's hung", "why did it stop" | [`persistence-and-state.md`](docs/operations/persistence-and-state.md) § Diagnosing a live or stuck run — the triage order, the phase vocabulary, the heartbeat invariant; then § Recovery for the verb | Mtimes read first, so a deliberate pause looks like a crash. |
| **L4 / `promptpotter-self`** self-improvement, the optimizer's own business logic, prompts and configurations alike. | [`l4-outer-loop.md`](docs/specs/l4-outer-loop.md) (what is true) · `/potter-self` (what to do) · [`optimization/CLAUDE.md`](promptpotter/application/optimization/CLAUDE.md) · [`dispatch-hub.md`](docs/developer/dispatch-hub.md) | A leader believed from a panel that cannot resolve arms. |
| **Dataset / benchmark work** | [`datasets/CLAUDE.md`](datasets/CLAUDE.md) | Rows re-cut under the name they already had. |
| **Engine code (Python)** | [`architecture.md`](docs/architecture.md) §0 + §0.5 · the ONE layer you touch, indexed by [`promptpotter/CLAUDE.md`](promptpotter/CLAUDE.md) · [`conventions.md`](docs/developer/conventions.md) · [`adding-a-surface.md`](docs/developer/adding-a-surface.md) § for the kind of thing you are adding | Green locally, red in CI — gated in the wrong env. |
| **Webapp code** | [`webapp/CLAUDE.md`](webapp/CLAUDE.md) · [`frontend-surface-contract.md`](docs/specs/frontend-surface-contract.md) § Invariants, then the ONE § Surfaces block you touch · `/design` | A number computed in the browser. |

Ask fits no row, or you need one fact rather than a task's whole surface? The question→file table is [`docs/CLAUDE.md`](docs/CLAUDE.md) § Anchor docs for hot questions.

**Two pointer kinds below, deliberately not merged.** A `§` names a PLACE to go read. A `` `<tag>` `` names a delimited block you recall whole — cross-linked from inside other blocks, and cited by name from source (`diagnostics.py`, `seed_screen.py`, `test_complexity_ledger.py`), so it is an identifier, not a location. Don't convert one into the other.

## Load-bearing

What this file owns, and where each rule is stated. Names only — the section is the text.

- Classify the ask before loading → § First — decide what KIND of ask this is, then load for it
- Delete shims, fallbacks and redundant paths → § STOP — no backward compatibility, ever
- Never commit or push unprompted → § Conventions
- Reach for a doctrine by its trigger → § Working principles
- Per-layer contracts — load only yours → § Pointers

## STOP — no backward compatibility, ever

Zero released versions, zero stale on-disk data — nothing to be compatible with. **This is the rule that gets ignored most often.**

Delete on sight — don't ask, don't TODO, don't "remove later":
- **Shim code**, **Fallback chains**, **Breadcrumb comments**, **Redundant mechanisms** — fold it into the canonical mechanism, never add beside it.
- **Names: distinct + self-describing.** Grep for collisions; a name that could mean three things gets renamed now — naming is cheap. Two failures beyond collision, both found by asking *can a reader pinpoint the concept from this name alone?* — (1) **a second word for something the repo already names** is a deletion, not a synonym: check what the operator already sees on screen and on disk before coining one; (2) **a name that stopped describing its contents** as the module grew under it. Renaming a module means renaming its doc, its `CLAUDE.md` row and its prose in the same commit — a code/doc name split is the drift, not the fix.

**Lean to suspicion** — small consolidations ignored because each looks too small to bother should be corrected on sight, independent of whether they fit the arc's main subject. When a change *could* ride an existing channel, assume it *should*, and collapse redundant paths you pass mid-task instead of noting them for later.

**This covers ON-DISK SHAPE, not just code — and that is the half that keeps getting read backwards.** Do not leave a bad shape alone because data exists in it; it is an instruction to **rearchitect the right shape and then delete or migrate the data to fit**. Aggressively: wipe the store, restamp it, or partially both. The persistence layer is still being shaped, and it is far cheaper to shape it now than after release — **so a design decision must never be bent around what happens to be on disk today**. Measure whether the data is worth keeping (is the cache ever hit? is the field derivable from what sits beside it?), then act on the answer.

**Before invoking migration caution, COUNT the data — do not recall it:** `ls .promptpotter/projects/*/campaigns` answers in one command whether anything would actually have to migrate.

<root-fix>
When a fix would compensate for something an upstream layer should already have made true, the fix belongs upstream — not at the site where the symptom shows up. Name the structural cause and propose the upstream fix <em>before</em> touching the visible surface. The operator can still pick the patch, but they pick it knowingly. Default to root, not to symptom.
</root-fix>

<dispatch-first>
**★ FIRST PRINCIPLE, THIS PHASE — fix it in the dispatch.** The target model is small: a prompt is one *information package* it must reason from, and it finds the right path only when that package is short and every line is unique, high-value, and in the right place. The **dispatch hub** (`application/optimization/dispatch/`) is where raw measurement is recomposed into those packages and wired into every optimizer prompt through **deterministic wires** (`DispatchHub.fill`). So the dispatch is the FIRST place to fix a bloated / low-value / misplaced prompt — and the one place to be **creative**: add functions, processing, recomposition, new panel logic, whatever shapes the incoming information to be *perfectly formed for its slot*. The wires stay deterministic; the intelligence lives in the shaping. Corollaries: a panel renders only what adds signal **for this task/state** and stays silent otherwise (the `answer_distribution` / suppressed-RANK rule); duplication, paraphrase, filler, and task-mismatched blocks are dispatch problems to reshape at source — never a mechanical downstream patch (a render-time dedup, a louder optimizer prompt clause) or a symptom papered over.
</dispatch-first>

## Working principles

Six *situational* guardrails against recurring AI blind spots live in [`docs/developer/conventions.md`](docs/developer/conventions.md) § Reasoning doctrine; reach for them by trigger:
- **the operator bounded ANY budget axis** → `<one-budget>`: a limit on one axis binds **all** of them, in both directions — "don't spend more" bounds the clock, "we don't have five hours" bounds the dollars. Price a proposal in the axis they named *and* the ones they didn't; trading one for another is an increase, and is asked for explicitly. When the budget binds, get more from measurements already paid for.
- **slow / costly / token-heavy LLM call, OR adding anything to a prompt** → `<simplify-the-problem>`: tighten the prompt so the model doesn't *need* the tokens; the timeout, cap and provider are safety rails, not the fix. **Length is a quality tax, not only a bill** — every model degrades as its input grows, so this fires before anything is slow. Attribute a real payload per block before diagnosing, and count the response JSON Schema: it is prompt text.
- **labelling a change "refactor" / LOC work** → `<surface-ledger>`: run `complexity_ledger`; a pass *called* refactor must move the total **down**. The ratchet asserts EQUALITY, so every move costs a baseline edit and a written reason — a win nobody re-pinned rots the baseline exactly as an unexamined raise does.
- **changed what the engine DECIDES, or added a capability at one entry point** → `<entry-point-parity>`: five ways in — CLI, AIs (skill: `/potter-run`), the embedded launch a host program drives (`application/embedded_run.py` — the notebooks and the BBEH harness), REST API, Js-Webapp — and a capability reaching only the one you were editing is half-built. Teach a new value, never dump it.
- **reaching for the shell, a sub-agent, or a wait** → `<wall-clock>`: the file tools answer in ~0.1s and a shell call carries a fixed toll (median 2.4s, mean 9.4s), so batch shell work and never spend it on something `Read`/`Grep`/`Edit` does. A sub-agent is ~5 minutes: `<read-once>` prices delegation in tokens only, and the correction is that N searches go out in ONE message or not at all. Never `sleep`-poll — background it and let the notification arrive. And iterate on the one check that owns what you touched; the gate is the thing you run once.
- **about to open a file you'll WORK in, or search across >3 files** → `<read-once>`: a narrow read *feels* frugal and isn't — the window keeps every line, so N pokes cost N times. Read whole at four-plus touches; never read file content through `sed`/`cat`/`head`; delegate a >3-file search and ask for the verdict, not the excerpts.

## Commands

```bash
pip install -e ".[all,dev]"                                  # add `,benchmarks` ONLY to fetch a public bank — opt-in, third-party surface
python scripts/gate.py                                       # EVERY check CI runs, one invocation, nothing masking anything; re-execs itself into the locked env, so the verdict never depends on which python you had. --py / --web to halve it, --only NAME for the one check that owns what you touched
git config core.hooksPath .githooks                           # one-time per clone: `gate.py --staged`, the same list scoped to what you staged
python -m promptpotter new <name>                            # fresh: mint campaign+root cycle from datasets/<name>/, run from round 0
python -m promptpotter new <file.csv> --set task_description=…  # fresh from RAW file: ingest → resolve origin check-in → run
python -m promptpotter resume                                # resume active cycle; Ctrl+C: 1st pauses (resumable, exit 130), 2nd force-quits
python -m promptpotter resume --from N                       # rewind in place
python -m promptpotter resume --fork-on-divergence           # sibling cycle at divergence point
python -m uvicorn promptpotter.main:app --port 8001          # read-only API + webapp preview at the root (http://localhost:8001/)
```

`new` and `resume` are the loop-mint verbs; lifecycle (`archive`/`delete`/`unarchive`/`reset`), run-control (`pause` — stops a running cycle at its next checkpoint, resumable, the terminal's half of the webapp control; `set-budget` — raises or lowers an existing cycle's spend/token ceiling, which is how a budget-halted cycle is continued: raise, then `resume`), manifest-edit (`rename` — the campaign's display name; the id still addresses it), and diagnostic (`verify`/`ab`/`noise-floor`/`seed-screen`/`reindex`/`restamp`/`evidence` — the last reads any set of campaigns together, and is what answers whether they can be compared at all) verbs also exist. Reads happen by opening files; there is no read CLI. What each verb does, its flags, identity and fork lineage → [`persistence-and-state.md`](docs/operations/persistence-and-state.md).

**The project file tree IS the dashboard**, alongside a read-only webapp at the root (Next.js at `webapp/`) polling the active cycle's `dashboard.json` every 2 s. The most-recent run's terminal readout is mirrored ANSI-stripped to the gitignored **`logs/latest.log`** — read it instead of asking the operator to paste console output (`LiveDisplay._write`). Onboarding: install → restart VS Code → `/potter-run`.

The **front door is a browser chat** — a human-in-the-loop copilot: the operator converses, the Potter posts its work into the thread Perplexity-style (tool calls, web search, each round as it lands), and decisions surface as buttons that fire existing control-plane verbs. The chat is a **canonical agent-chat template** built on a generic activity taxonomy — keep the core (thread model + activity stream + transport), delete the optimizer panes. Contract: [`docs/specs/chat-foundation.md`](docs/specs/chat-foundation.md).

## Conventions

- Full style + code-shape + git rules, and the four banned words → [`docs/developer/conventions.md`](docs/developer/conventions.md).
- **Git — don't commit by default:** **never `git commit` or `git push` unless the operator says so** (a commit ask is not a push ask). **Sole standing exception:** where a project instruction already grants autonomous commits. Ruff format + check before any commit (see Commands).
- **Vocabulary:** say "node" never service/building-block; **"optimizer prompt" never "meta-prompt"** — a prompt the optimizer *runs on*, whose opposite is the **target prompt** it *produces* during optimization (`OptSearchPoint` vs `JobSearchPoint`); for L4 say **outer/inner** (position) and **self-optimization** (the arrangement), never "meta"; **"run init" / the INIT phase, never "bootstrap"** — the chain from `new`/`resume` to round 1 (`application/initialization/`), which the operator already sees as `INIT` / `✓ Initialized`, so a third word for it only hides the concept; the word survives ONLY for machine provisioning (`deploy-linux/bootstrap.sh`) and external proper nouns (DSPy `BootstrapFewShot`); domain framing = evolution (generation/population/fitness/mutation/selection/individual).
- **Sample look-ahead is the operator's, from the browser, and nowhere else.** The one deliberate `<entry-point-parity>` exception: arming the scoring walk to hold 2 samples in flight (`/commands/set-sample-lookahead`) is host-admin-capability-gated and has **no CLI verb, no config key, no dataset knob and no skill step — the absence IS the boundary.** Do not add one, do not make the depth a knob, and do not give it a sticky lifetime (it is spent by one round of scoring). Above all **never "recover" the discarded look-ahead sample**: recording it makes the run's rows depend on in-flight depth, which forces a `human_intervened` stamp and devalues the whole campaign. Rationale: [`access-model.md`](docs/operations/access-model.md).
- **Fewest dependencies possible** in both repos — reach for the stdlib or a small hand-rolled helper before adding a package; every new dependency must earn its place. **Core is the engine; a surface is an extra** — owned by [`ADR-0006`](docs/adr/0006-embeddable-core-and-extras.md): one that earns its place goes in an extra unless it is *measurably* reachable from `cli/campaign_runner.py` or `application/embedded_run.py`.

## Pre-flight gate

Before adding any new concept (class, projection, injection, prompt, field, dict, file), answer these. "I don't know" or "kind of" is a hard block:

- **Reuse before adding.** Does an existing channel/infrastructure already do this? Default **yes** — search/grep first. Ride the ledger / `INJECTIONS` / `OptSearchPoint` / dispatch hub; **no sidecar**.
- **Map to a §0 bucket.** If it fits none, stop — either §0 is incomplete (update it, separate PR landing first) or this is the wrong PR.
- **New I/O kind → amend §0 first.** The five are fixed (Persistence, Display, Control-local, Control-remote, Identity). New Control-remote command/event → declare schema in `docs/specs/m12-api-openapi.yaml` / `m12-events-asyncapi.yaml` *before* the handler lands.
- **Material facts land on disk, human-readable** — never surfaced only via stdout, in-memory state, or `--verbose`. Same for debug-state: if a bug needs an operator-only env to reproduce, the unblocker (mock/fixture/pin) is a separate PR landing first.

## The closing directive

**Ship a distributable `promptpotter-self`** — the optimizer optimizing its own optimizer prompts — **and open no new features until the config is distributable.** Escalate to the operator campaign spend approval, or a compaction handoff. What remains is [`docs/specs/l4-outer-loop.md`](docs/specs/l4-outer-loop.md) § Open; read it there and nowhere else.

**What remains is (mostly) empirical, (mostly) not structural** — the loop, seams, recursion and scoring gateway all exist and are green; what remains is making the optimizer *behave well*, which is found by running L3 campaigns as well as `promptpotter-self`, read what the loop produced, fix, re-run. **Every fix still goes to its ROOT (`<root-fix>` above)** — in this phase the root is usually a prompt or dispatch-hub. **Supervise every run actively — never fire-and-wait. (3 min intervals first, then adjust case dependent)** How to run + supervise + when to reach past prompts to code: `/potter-self`.

⚠️ **Read a leader against its own interval, never against its rank** — owned by [`docs/specs/l4-outer-loop.md`](docs/specs/l4-outer-loop.md) § What a panel may claim; deepen a candidate with `verify`, never by re-asking a cell it already answered.

⚠️ **The 2026-08-07 fixes are verified on the C0 panel only — the OUTER round-1 election is UNMEASURED.** Deliberate: a round-1 election costs ~14 more cells and the operator is building concurrency first. Re-check it on the next `new promptpotter-self`, not before — what to look for is [`docs/specs/l4-outer-loop.md`](docs/specs/l4-outer-loop.md) § Open.

**When adding a state, ask what DERIVES it — not just what writes it.** The costliest bugs in this package have all been one shape: a *state the system could enter but not report*, downstream of a predicate that conflated two facts, invisible because the failure mode was silence rather than an error. A writer with no reader is not a state; it is a note nobody reads.

## Pointers

- **Architecture:** [`docs/architecture.md`](docs/architecture.md) §0/§0.5 — backbone primitives, five I/O kinds, the central loop + L1/L2/L3 escalation + L4-is-recursion, searchpoints, scoring, identity, token/cost ledger. **Extend primitives in place** — the wrong shape is meant to be hard to express, not policed by a test.
- **Persistence:** [`docs/operations/persistence-and-state.md`](docs/operations/persistence-and-state.md) — four-entity tree (Workspace → Dataset → Campaign → Cycle), `.promptpotter/` layout, `measurements/`, fork lineage, recovery.
- **Per-layer contracts** — load only the layer you touch; each subpackage's own `CLAUDE.md` auto-loads by directory proximity. The index is [`promptpotter/CLAUDE.md`](promptpotter/CLAUDE.md); the roster is that directory listing.
- **Contracts:** the ADRs in [`docs/adr/`](docs/adr/) are the permanent constitutions. **The roster is that directory listing** — enumerating a subset anywhere is what let two of them go unindexed, and a second copy is what made the two indexes disagree. Index map: [`docs/CLAUDE.md`](docs/CLAUDE.md).
- **Design surface:** [`BRAND.md`](BRAND.md) (visual identity — theme-is-audience, dark=operator, light=buyer) + [`VOICE.md`](VOICE.md) (copy register — the Potter is force-multiplier, not friendly-wizard).
- **Skills:** [`.claude/skills/`](.claude/skills/) — `potter-run` (launch + supervise a campaign)
- **Product story, feature framing, docs index:** [`README.md`](README.md) — the buyer-facing front page. **Open it when the ask is about positioning, the peer table or how we describe a feature; it is deliberately NOT inlined.** It is written for a reader who has not cloned the repo — badges, BibTeX, and ~30% absolute GitHub URLs — so importing it spent ~4.6k tokens a session on bytes an agent in this tree cannot use, and put a second, buyer-voiced copy of the entry-point count and the run verbs beside the ones above. Don't restate it here either.
