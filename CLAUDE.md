# CLAUDE.md

> Read [`docs/architecture.md`](docs/architecture.md) first — §0 + §0.5 are what every PR measures against. Don't restate here.

## What this is

PromptPotter is **LLM-driven program evolution** for prompts and pipeline params. **Orchestration is the product — backends are pluggable and read-only.** Node tunables ride a per-call overlay (`datasets/{name}/pipeline.yaml::nodes.{name}.config`).

**Our setting is the cloud team-online deployment (tier 3), NOT the local-secure one (tier 2)** — treat cloud/team as the default operating context in everything we build ([`deploy-linux/`](deploy-linux/): Cloudflare tunnel + open OIDC signup + a per-account spend ceiling). What shipped and what is in flight: [`docs/specs/roadmap.md`](docs/specs/roadmap.md).

**Origin = the campaign root = C0**, and for a fork the point it branches *from*. An *individual/candidate* is a configuration (both are `OptSearchPoint`). **The general relation is *parent*** — the individual a candidate was mutated from (`RoundParent`): the origin at round 0, the prior winner after it.

**Fitness is never one fixed number — always ask "under which formula?"** It is formula-relative (**active** / **mask**, picked by a **lens** / **replay**) and mode-relative (`measured` subset vs `all`). Every score — the active fitness, any alternative formula or mode, the hard-sample sort — is computed and **served by the backend; the webapp never recomputes.** Depth: [`docs/architecture.md`](docs/architecture.md) §0.5 (Composite-fitness resolution chain) + [`docs/concepts/scoring-and-memory.md`](docs/concepts/scoring-and-memory.md).

⚠️ **A round is won on θ, not accuracy — when the operator reads the accuracy column, say so.** Subsets move between rounds and PoBB truncates arms at different depths, so a lower-accuracy winner is normally *correct*: push back rather than accept the premise. **Five states make that pushback wrong**, all SERVED as a `ThetaCaveat` so no surface re-decides one — an arm at 0.0 on every cell, a cold ruler, a flat ruler, **a collapsed selection band**, and **unmeasured difficulty**. The last two are the SILENT pair, where every number still renders; against both, read the LIFT rather than the level. **Read the round's `overlap` line before arguing either way** — C0 and every winner since, on one shared set of cells, needing no ruler. Each caveat's meaning and derivation: [`docs/methods/verdict-resolution.md`](docs/methods/verdict-resolution.md) § Reading a round.

## First — decide what KIND of ask this is, then load for it

**This repo is not only code.** Launching a campaign, diagnosing a live run, editing a prompt, changing the engine, changing the webapp and cutting a dataset load almost **disjoint** context — reading everything is not the answer. Each row is *what to load*, then the **symptom** that kind produces when it goes wrong — the cure lives in the file the row points at, never in this table.

| The ask sounds like | Load | How this kind goes wrong |
|---|---|---|
| **Run / watch a campaign** — "run bbeh", "start it", "how's it going" | `/potter-run` · `logs/latest.log` · [`persistence-and-state.md`](docs/operations/persistence-and-state.md) (the verbs, resume/rewind/fork) · [`observability.md`](docs/operations/observability.md) (what is traced, the P(best) stream) | Fired and left; config edited mid-flight. |
| **Diagnose a live or stuck run** — "it's hung", "why did it stop" | [`persistence-and-state.md`](docs/operations/persistence-and-state.md) § Diagnosing a live or stuck run — the triage order, the phase vocabulary, the heartbeat invariant; then § Recovery for the verb | Mtimes read first, so a deliberate pause looks like a crash. |
| **L4 / `promptpotter-self`** self-improvement, the optimizer's own business logic, prompts and configurations alike. | [`l4-outer-loop.md`](docs/specs/l4-outer-loop.md) (what is true) · `/potter-self` (what to do) · [`optimization/CLAUDE.md`](promptpotter/application/optimization/CLAUDE.md) · [`dispatch-hub.md`](docs/developer/dispatch-hub.md) | A leader believed from a panel that cannot resolve arms. |
| **Dataset / benchmark work** | [`datasets/CLAUDE.md`](datasets/CLAUDE.md) | Rows re-cut under the name they already had. |
| **Engine code (Python)** | [`architecture.md`](docs/architecture.md) §0 + §0.5 · the ONE layer you touch, indexed by [`promptpotter/CLAUDE.md`](promptpotter/CLAUDE.md) · [`conventions.md`](docs/developer/conventions.md) · [`adding-a-surface.md`](docs/developer/adding-a-surface.md) § for the kind of thing you are adding · [`tests/CLAUDE.md`](tests/CLAUDE.md) BEFORE a test: three axes admit one, it rides one of six files, the ledger counts both | Green locally, red in CI — gated in the wrong env. A test file per feature. |
| **Webapp code** | [`webapp/CLAUDE.md`](webapp/CLAUDE.md) · [`frontend-surface-contract.md`](docs/specs/frontend-surface-contract.md) § Invariants · `/design` | A number computed in the browser. |

Ask fits no row, or you need one fact rather than a task's whole surface? The question→file table is [`docs/CLAUDE.md`](docs/CLAUDE.md) § Anchor docs for hot questions.

**Two pointer kinds below, deliberately not merged.** A `§` names a PLACE to go read. A `` `<tag>` `` names a delimited block you recall whole — cross-linked from inside other blocks, and cited by name from source (`complexity_ledger.py`, `seed_screen.py`, `test_complexity_ledger.py`), so it is an identifier, not a location. Don't convert one into the other.

## Load-bearing

What this file owns, and where each rule is stated. Names only — the section is the text.

- Classify the ask before loading → § First — decide what KIND of ask this is, then load for it
- Delete shims, fallbacks and redundant paths → § STOP — no backward compatibility, ever
- Never commit or push unprompted → § Conventions
- Reach for a doctrine by its trigger → § Working principles
- A campaign does not own its measurements → § The archive is not scoped by campaign
- Answer every question before adding a concept → § Pre-flight gate
- Say which column a measured number is in → § The closing directive
- Print every operations command you invoke → § Commands
- Per-layer contracts — load only yours → § Pointers

## The archive is not scoped by campaign

**`measurements/` is ONE content-addressed tree per workspace, it outlives the campaigns that filled it, and a row is filed under the dataset it MEASURED — never under the campaign that paid for it.** So "what did this campaign cost on disk" is not a question the archive answers, and **counting beats recalling**: `compact-archive inventory --dataset <name>` prints runs, cells, bytes and replay rate by dataset, label and age. The consequences, and the reversibility that makes them survivable, are [`infrastructure/CLAUDE.md`](promptpotter/infrastructure/CLAUDE.md) § The archive is not scoped by campaign.

## STOP — no backward compatibility, ever

Zero released versions, zero stale on-disk data — nothing to be compatible with. **This is the rule that gets ignored most often.**

Delete on sight — don't ask, don't TODO, don't "remove later":
- **Shim code**, **Fallback chains**, **Breadcrumb comments**, **Redundant mechanisms** — fold it into the canonical mechanism, never add beside it.
- **Names: distinct + self-describing.** Grep for collisions; a name that could mean three things gets renamed now — naming is cheap. Two failures beyond collision, both found by asking *can a reader pinpoint the concept from this name alone?* — (1) **a second word for something the repo already names** is a deletion, not a synonym: check what the operator already sees on screen and on disk before coining one; (2) **a name that stopped describing its contents** as the module grew under it. Renaming a module means renaming its doc, its `CLAUDE.md` row and its prose in the same commit — a code/doc name split is the drift, not the fix.

**Lean to suspicion** — correct small consolidations on sight, however small, whether or not they fit the arc's main subject. When a change *could* ride an existing channel, assume it *should*, and collapse redundant paths you pass mid-task instead of noting them for later.

**This covers ON-DISK SHAPE, not just code.** Do not leave a bad shape alone because data exists in it; **rearchitect the right shape, then delete or migrate the data to fit** — wipe the store, restamp it, or partially both. Shaping persistence now is far cheaper than after release, **so a design decision must never be bent around what happens to be on disk today**. Measure whether the data is worth keeping (is the cache ever hit? is the field derivable from what sits beside it?), then act on the answer.

**Before invoking migration caution, COUNT the data — do not recall it:** `ls .promptpotter/projects/*/campaigns` answers in one command whether anything would actually have to migrate.

<root-fix>
When a fix would compensate for something an upstream layer should already have made true, the fix belongs upstream — not at the site where the symptom shows up. Name the structural cause and propose the upstream fix <em>before</em> touching the visible surface. The operator can still pick the patch, but they pick it knowingly. Default to root, not to symptom.
A fix at N sites is the model serving the wrong shape — realign it and let the sites fall out. Net prose growth in a bug-fix diff is a SIGNAL to re-read the diff, never a proof: a root fix earns a test and states the corrected mechanism, and both are lines a patch never spends.
</root-fix>

## Working principles

Six *situational* guardrails against recurring AI blind spots. The trigger and the rule are here; the evidence behind each is [`docs/developer/conventions.md`](docs/developer/conventions.md) § Reasoning doctrine.

**Standing spend authorization: VERIFICATION up to ~$0.30 needs no approval** — a probe, an A/B, a `seed-screen`, a re-measure. Run it and report the number. Above that, and for anything minting a cycle (`new` / `resume` / any `promptpotter-self` run), ask. The same bound decides whether cheap undone work is reported or simply done.

- **the operator bounded ANY budget axis** → `<one-budget>`: a limit on one axis binds **all** of them, both directions — "don't spend more" bounds the clock, "we don't have five hours" bounds the dollars. Price a proposal in the axis they named *and* the ones they didn't; trading one for another is an increase, asked for explicitly. When the budget binds, get more from measurements already paid for.
- **slow / costly / token-heavy LLM call, OR adding anything to a prompt** → `<simplify-the-problem>`: tighten the prompt so the model doesn't *need* the tokens — the timeout, cap and provider are safety rails, not the fix. **Length is a quality tax, not only a bill**, so this fires before anything is slow. Attribute a real payload per block before diagnosing, and count the response JSON Schema: it is prompt text.
- **labelling a change "refactor" / LOC work** → `<surface-ledger>`: run `complexity_ledger`. It prices DECLARED surface, so it refuses a pass that adds a module, a knob, a served field or an injection while calling itself a simplification — and a FLAT total falsifies nothing, since folding helpers moves no name it can see. The ratchet asserts EQUALITY, so every move costs a baseline edit and a written reason.
- **changed what the engine DECIDES, added a capability at one entry point, or CAUGHT one rule implemented twice** → `<entry-point-parity>`: five ways in — CLI, the `/potter-run` skill, the embedded launch (`application/embedded_run.py`), REST API, webapp — and a capability reaching only the one you were editing is half-built. Teach a new value, never dump it. **Periphery instead of parity is urgent the moment it is seen**, and its root is a layer boundary, so the fix moves the shared piece down into `application/` rather than patching the copy.
- **reaching for the shell, a sub-agent, or a wait** → `<wall-clock>`: a shell call carries a fixed toll the file tools do not, so batch shell work and never spend it on something `Read`/`Grep`/`Edit` does. A sub-agent costs minutes, so N searches go out in ONE message or not at all. Never `sleep`-poll — background it and let the notification arrive. Iterate on the one check that owns what you touched; the gate is what you run once.
- **about to open a file you'll WORK in, or search across >3 files** → `<read-once>`: a narrow read *feels* frugal and isn't — the window keeps every line, so N pokes cost N times. Read whole at four-plus touches; never read file content through `sed`/`cat`/`head`; delegate a >3-file search and ask for the verdict, not the excerpts.

## Commands

```bash
# Every `python` below is the repo venv (`.venv/Scripts/python.exe` | `.venv/bin/python`). Bare `python` fails SILENTLY:
# it imports promptpotter but not its deps, so a run starts, replays cache, and dies at the first live LLM call.
# `gate.py` re-execs to dodge this; the CLI cannot, or it would break the Ctrl+C pause contract.
pip install -e ".[all,dev]"                                  # `benchmarks` and `harbor` are OUT of `all` deliberately — add one only when you need it; harbor wants a container runtime
python scripts/gate.py                                       # EVERY check CI runs, in the locked env. --py / --web to halve it, --only NAME for the one check that owns what you touched
python scripts/gate.py --release                             # BEFORE cutting a release: the dashboard lock against npm's advisory DB + every open Dependabot alert. Network-bound, so never in the default run
git config core.hooksPath .githooks                           # one-time per clone: `gate.py --staged`, the same list scoped to what you staged
python -m promptpotter new <name>                            # fresh: mint campaign+root cycle from datasets/<name>/, run from round 0
python -m promptpotter new <file.csv> --set task_description=…  # fresh from RAW file: ingest → resolve origin check-in → run
python -m promptpotter resume                                # resume active cycle; Ctrl+C: 1st pauses (resumable, exit 130), 2nd force-quits
python -m promptpotter resume --from N                       # rewind in place
python -m promptpotter resume --fork-on-divergence           # sibling cycle at divergence point
python -X utf8 -m uvicorn promptpotter.main:app --port 8001  # API + webapp control plane at the root (http://localhost:8001/)
```

**`-X utf8` on the server is load-bearing.** A cp1252 process serves every read but refuses every container-backed launch, so the box looks healthy until the operator presses Play. The boot banner says so when the posture is wrong.

**Print every operations command you invoke, verbatim, in the reply that reports it.** The operator reads the verb, the dataset and the flags — a number with no command behind it cannot be checked, re-run or corrected mid-flight. This covers `python -m promptpotter …` and the maintenance verbs; container and shell plumbing (`docker`, `curl`, `ls`) stays out. **Showing the command is not handing it over** — a verb that is yours to run stays yours, printed beside its result; quoting one for the operator to type is for the cases that genuinely need their hands (an interactive login, a spend decision).

`new` and `resume` are the loop-mint verbs. Beside them sit lifecycle, run-control (`pause`, `set-budget`, `cancel-queued`, `skip-searchpoint`, `step-cycle`), manifest-edit, diagnostic (`verify`/`ab`/`noise-floor`/`seed-screen`/`evidence`) and maintenance (`reindex`/`restamp`/`compact-archive`) verbs. **Two facts about the set matter here; everything else is [`persistence-and-state.md`](docs/operations/persistence-and-state.md)'s:** `evidence` is the one read VERB, because a comparison ACROSS subjects is in no single file — every other read is opening the tree. And `compact-archive purge-cold --apply` is the only verb that destroys paid measurement.

**The project file tree IS the dashboard**, alongside a webapp control plane at the root (Next.js at `webapp/`) polling the active cycle's `dashboard.json` and issuing control verbs through `POST /commands/{kind}`. The most-recent run's terminal readout is mirrored ANSI-stripped to the gitignored **`logs/latest.log`** — read it instead of asking the operator to paste console output. Onboarding: install → restart VS Code → `/potter-run`.

The **front door is a browser chat** — a human-in-the-loop copilot: the operator converses, the Potter posts its work into the thread (tool calls, web search, each round as it lands), and decisions surface as buttons that fire existing control-plane verbs. The chat is a **canonical agent-chat template** built on a generic activity taxonomy — keep the core (thread model + activity stream + transport), delete the optimizer panes. Contract: [`docs/specs/chat-foundation.md`](docs/specs/chat-foundation.md).

## Conventions

- Full style + code-shape + git rules, and the four banned words → [`docs/developer/conventions.md`](docs/developer/conventions.md).
- **Git — don't commit by default:** **never `git commit` or `git push` unless the operator says so** (a commit ask is not a push ask). **Sole standing exception:** where a project instruction already grants autonomous commits. Ruff format + check before any commit — `scripts/gate.py` runs both, and `--staged` is the pre-commit hook.
- **Vocabulary:** say "node" never service/building-block; **"optimizer prompt" never "meta-prompt"** — a prompt the optimizer *runs on*, whose opposite is the **target prompt** it *produces* during optimization (`OptSearchPoint` vs `JobSearchPoint`); for L4 say **outer/inner** (position) and **self-optimization** (the arrangement), never "meta"; **"run init" / the INIT phase, never "bootstrap"** — the chain from `new`/`resume` to round 1 (`application/initialization/`), which the operator already sees as `INIT` / `✓ Initialized`; the word survives ONLY for machine provisioning (`deploy-linux/bootstrap.sh`) and external proper nouns (DSPy `BootstrapFewShot`); domain framing = evolution (generation/population/fitness/mutation/selection/individual).
- **Sample look-ahead is browser-only, and the ABSENCE is the boundary** — the one deliberate `<entry-point-parity>` inversion, so a missing CLI verb / config key / dataset knob is the gate, never an oversight to fix. Why it is declared rather than merely unimplemented: [`presentation/CLAUDE.md`](promptpotter/presentation/CLAUDE.md) § Sample look-ahead.
- **Fewest dependencies possible** in both repos — reach for the stdlib or a small hand-rolled helper before adding a package; every new dependency must earn its place. **Core is the engine; a surface is an extra** — owned by [`ADR-0006`](docs/adr/0006-embeddable-core-and-extras.md), which settles the boundary on MEASURED reachability rather than on argument.

## Pre-flight gate

Before adding any new concept (class, projection, injection, prompt, field, dict, file), answer these. "I don't know" or "kind of" is a hard block:

- **Reuse before adding.** Does an existing channel/infrastructure already do this? Default **yes** — search/grep first. Ride the ledger / `injection_table()` / `OptSearchPoint` / dispatch hub; **no sidecar**.
- **Map to a §0 bucket.** If it fits none, stop — either §0 is incomplete (update it, separate PR landing first) or this is the wrong PR.
- **New I/O kind → amend §0 first.** The five are fixed (Persistence, Display, Control-local, Control-remote, Identity). New Control-remote command/event → declare schema in `docs/specs/api-openapi.yaml` / `events-asyncapi.yaml` *before* the handler lands.
- **Material facts land on disk, human-readable** — never surfaced only via stdout, in-memory state, or `--verbose`. Same for debug-state: if a bug needs an operator-only env to reproduce, the unblocker (mock/fixture/pin) is a separate PR landing first.

## The closing directive

**M13 — the bench.** Every peer ships an **algorithm with no interface** — DSPy/GEPA/MIPROv2, CAPO, PromptWizard, Promptolution, SkillOpt, WikiSkill; Opik's `BaseOptimizer` interface lacks a guard. We ship an algorithm **and** an interface, and ours **wraps theirs**: a peer's method plugs in as the round's candidate source and inherits PoBB, the δ ruler, subset-invariant θ, the content-addressed archive, one spend ledger, five entry points and the control plane. That makes this an open-source **R&D bench for recursive self-improvement and optimizers**, not one more optimizer. **It closes when ONE peer runs end-to-end in a real campaign** — a head-to-head whose only difference is the proposer. Features M13 needs get opened. The seam, the three conditions that keep it off the L4 ban and the identity rule that must flip between a normal campaign and an L4 inner cycle are [`docs/specs/roadmap.md`](docs/specs/roadmap.md) § The optimizer plug point; the running record is `.scratch/m13-bench.md`.

**M14 — ship the preprint, and the bench IS the contribution.** Prior work fragmented the components of prompt and agent-harness search — proposer, evaluator, selector, memory, budget, **stopping rule**, **comparability guard** — and the last two are where nobody stands. Pricing a lift in wall clock and dollars is the EVIDENCE, not the claim; the claim is that both are **executable** here and that every peer ran under them. Running record `.scratch/m14-preprint.md`, manuscript in `paper/`. **Neither milestone has dates and neither gets any**: each is a dependency sequence whose stages close on a verification, not on a calendar.

**M13 is structural; everything beside it is empirical.** The loop, seams, recursion and scoring gateway exist; what remains *there* is making the optimizer *behave well* — run L3 campaigns as well as `promptpotter-self`, read what the loop produced, fix, re-run. **Every fix still goes to its ROOT (`<root-fix>` above)** — in that half the root is usually a prompt or dispatch-hub. **Supervise every run actively — never fire-and-wait. (3 min intervals first, then adjust case dependent)** How to run + supervise + when to reach past prompts to code: `/potter-self`.

**A measured number is not a result until you know which column it is in.** `improved` reads as a verdict and is not one — a bare point estimate (`lift > 0.0`, no interval, no correction; with three arms, P(some arm positive | every arm identical to the parent) is 0.875 per round). It is the PROMOTION clock, and `rounds_to_separable` beside it is the one a result quotes. Say which column a claim rests on, or do not make it.

**When adding a state, ask what DERIVES it — not just what writes it.** The costliest bug shape here is a *state the system could enter but not report*, downstream of a predicate that conflated two facts, invisible because the failure mode is silence rather than an error. A writer with no reader is not a state; it is a note nobody reads.

## Pointers

- **Architecture:** [`docs/architecture.md`](docs/architecture.md) §0/§0.5 — backbone primitives, five I/O kinds, the central loop + L1/L2/L3 escalation + L4-is-recursion, searchpoints, scoring, identity, token/cost ledger. **Extend primitives in place** — the wrong shape is meant to be hard to express, not policed by a test.
- **What to pick up next:** [`docs/specs/code-debt-cleanup.md`](docs/specs/code-debt-cleanup.md) — **multi-arc** work and **blocked** work, one line each. It answers "what should I do" when nothing is already in hand, and an item ships by being DELETED from it. What may and may not be filed there is stated in the file.
- **Persistence:** [`docs/operations/persistence-and-state.md`](docs/operations/persistence-and-state.md) — four-entity tree (Workspace → Dataset → Campaign → Cycle), `.promptpotter/` layout, `measurements/`, fork lineage, recovery.
- **Per-layer contracts** — load only the layer you touch, and **never open a second layer's file to check one fact: grep it.** Each subpackage's `CLAUDE.md` auto-loads by directory proximity, so that chain is spent before you type — what that means for anything you add to a layer is [`promptpotter/CLAUDE.md`](promptpotter/CLAUDE.md) § What the chain costs. The index is that file; the roster is its directory listing.
- **Contracts:** the ADRs in [`docs/adr/`](docs/adr/) are the permanent constitutions, and **the roster is that directory listing** — never an enumeration. Index map: [`docs/CLAUDE.md`](docs/CLAUDE.md).
- **Design surface:** `promptpotter-web/BRAND.md` — visual identity (theme-is-audience, dark=operator, light=buyer) and copy register (the Potter is force-multiplier, not friendly-wizard), owned by the sibling site repo. `webapp/app/styles/foundation/tokens.css` is a mirror of it, never a second spec.
- **Skills:** [`.claude/skills/`](.claude/skills/) — `potter-run` (launch + supervise a campaign)
- **Product story, feature framing, docs index:** [`README.md`](README.md) — the buyer-facing front page, **deliberately NOT inlined** (written for a reader who has not cloned the repo). Open it when the ask is about positioning, the peer table or how we describe a feature. Don't restate it here either.
