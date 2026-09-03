# CLAUDE.md

> Read [`docs/architecture.md`](docs/architecture.md) first — §0 + §0.5 are what every PR measures against. Don't restate here.

## What this is

PromptPotter is **LLM-driven program evolution** for prompts and pipeline params. **Orchestration is the product — backends are pluggable and read-only.** Node tunables ride a per-call overlay (`datasets/{name}/pipeline.yaml::nodes.{name}.config`). 

**Our setting is the cloud team-online deployment (tier 3), NOT the local-secure one (tier 2)** — treat cloud/team as the default operating context in everything we build ([`deploy-linux/`](deploy-linux/): Cloudflare tunnel + open OIDC signup + a per-account spend ceiling). What shipped and what is in flight: [`docs/specs/roadmap.md`](docs/specs/roadmap.md).

**Origin = the campaign root = C0**, and for a fork the point it branches *from*. An *individual/candidate* is a configuration (they both are of the class `OptSearchPoint`). **The general relation is *parent*** — the individual a candidate was mutated from (`RoundParent`), which is the origin at round 0 and the prior winner after it.

**Fitness is never one fixed number — always ask "under which formula?"** It is formula-relative (**active** / **mask**, picked by a **lens** / **replay**) and mode-relative (`measured` subset vs `all`). Every score — the active fitness, any alternative formula or mode, the hard-sample sort — is computed and **served by the backend; the webapp never recomputes.** Depth: [`docs/architecture.md`](docs/architecture.md) §0.5 (Composite-fitness resolution chain) + [`docs/concepts/scoring-and-memory.md`](docs/concepts/scoring-and-memory.md).

⚠️ **A round is won on θ, not accuracy — when the operator reads the accuracy column, say so.** Subsets move between rounds and PoBB truncates arms at different depths, so a lower-accuracy winner is normally *correct*, not a bug: push back rather than accept the premise. Four states where θ is NOT ability and the pushback is wrong, all SERVED as a `ThetaCaveat` so no surface re-decides one — an arm at 0.0 on every cell (pins to a floor constant, every lift reads `0.000`; per-CANDIDATE, so it rides the candidate row while the other three ride the round's reading), a cold ruler (θ is just logit-accuracy on its own subset), a flat ruler (the instrument, not the draw — no round could have read wider), and **a collapsed selection band** (the acquisition buys one narrow δ range, so θ reduces to logit-accuracy + a constant while the ruler stays warm and every number renders — the SILENT one). **Read the round's `overlap` line before arguing either way** — C0 and every winner since, on one shared set of cells, needing no ruler. Owned by [`docs/methods/verdict-resolution.md`](docs/methods/verdict-resolution.md) § Reading a round.

## First — decide what KIND of ask this is, then load for it

**This repo is not only code.** The same root file serves launching a campaign, diagnosing a live run, editing a prompt, changing the engine, changing the webapp, and cutting a dataset — and those load almost **disjoint** context. Reading everything is not the answer. Each row is *what to load*, then the **symptom** that kind produces when it goes wrong — the cure lives in the file the row points at, never in this table.

| The ask sounds like | Load | How this kind goes wrong |
|---|---|---|
| **Run / watch a campaign** — "run bbeh", "start it", "how's it going" | `/potter-run` · `logs/latest.log` · [`persistence-and-state.md`](docs/operations/persistence-and-state.md) (the verbs, resume/rewind/fork) · [`observability.md`](docs/operations/observability.md) (what is traced, the P(best) stream) | Fired and left; config edited mid-flight. |
| **Diagnose a live or stuck run** — "it's hung", "why did it stop" | [`persistence-and-state.md`](docs/operations/persistence-and-state.md) § Diagnosing a live or stuck run — the triage order, the phase vocabulary, the heartbeat invariant; then § Recovery for the verb | Mtimes read first, so a deliberate pause looks like a crash. |
| **L4 / `promptpotter-self`** self-improvement, the optimizer's own business logic, prompts and configurations alike. | [`l4-outer-loop.md`](docs/specs/l4-outer-loop.md) (what is true) · `/potter-self` (what to do) · [`optimization/CLAUDE.md`](promptpotter/application/optimization/CLAUDE.md) · [`dispatch-hub.md`](docs/developer/dispatch-hub.md) | A leader believed from a panel that cannot resolve arms. |
| **Dataset / benchmark work** | [`datasets/CLAUDE.md`](datasets/CLAUDE.md) | Rows re-cut under the name they already had. |
| **Engine code (Python)** | [`architecture.md`](docs/architecture.md) §0 + §0.5 · the ONE layer you touch, indexed by [`promptpotter/CLAUDE.md`](promptpotter/CLAUDE.md) · [`conventions.md`](docs/developer/conventions.md) · [`adding-a-surface.md`](docs/developer/adding-a-surface.md) § for the kind of thing you are adding | Green locally, red in CI — gated in the wrong env. |
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
- Per-layer contracts — load only yours → § Pointers

## The archive is not scoped by campaign

**`measurements/` is ONE content-addressed tree per workspace, and it outlives the campaigns that filled it.** Three consequences, each of which has already been read backwards:

- **A row is filed under the dataset it MEASURED, never under the campaign that paid for it.** On the recursion that is the *inner* benchmark (`datasets/{name}/inner_tasks.yaml::inner_benchmark`) — an inner sandbox isolates campaign state but deliberately shares `shared_root`, so **`promptpotter-self`'s bytes are almost all filed under the inner dataset's name.** Scoping anything by `--dataset promptpotter-self` reaches the outer cells and essentially nothing L4 actually cost. Count before concluding: `compact-archive compact --dataset <name>` dry-runs and prints the split by label.
- **Nothing on a run names a campaign.** The index entry is content, provenance and a label — no `campaign_id`, no `cycle_id`, because a cache hit is supposed to cross campaigns. So "what did this campaign cost on disk" is not a question the archive answers, and the join a surface needs is `LineageNode.sp_hash` → the row's `prompt_fields_id` (`docs/developer/README.md` § Cross-run memory).
- **Cycle state is disposable and the rows are not**, so the rows routinely outlive every campaign that could select them: an emptied `.inner/` leaves its measurements addressable only by dataset. Selecting a family and acting on "what it produced" is therefore a claim about *surviving* state — say so, rather than reporting a smaller number as if it were the whole.

Reversibility is what makes the first two survivable: `compact` keeps every field the δ ruler re-grades from and the replay cache needs, so compacting rows another campaign replays from costs it nothing. Only `purge-cold` needs the attribution, and only it is irreversible.

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
A fix at N sites is the model serving the wrong shape — realign it and let the sites fall out; net prose growth in a bug-fix diff proves it was a patch.
</root-fix>

<dispatch-first>
**★ FIRST PRINCIPLE, THIS PHASE — fix it in the dispatch.** A prompt is one *information package* for a small model: short, every line unique, high-value, in the right slot. The **dispatch hub** (`application/optimization/dispatch/`) is where raw measurement is recomposed into those packages and wired in through **deterministic wires** (`DispatchHub.fill`) — so it is the FIRST place to fix a bloated / low-value / misplaced prompt, and the one place to be **creative**: new functions, processing, recomposition, panel logic, whatever forms the information *perfectly for its slot*. The wires stay deterministic; the intelligence lives in the shaping. Corollaries: a panel renders only what adds signal **for this task/state** and is otherwise silent (the `answer_distribution` / suppressed-RANK rule); duplication, paraphrase, filler and task-mismatched blocks are reshaped at source — never patched downstream (a render-time dedup, a louder optimizer prompt clause).
</dispatch-first>

## Working principles

Six *situational* guardrails against recurring AI blind spots live in [`docs/developer/conventions.md`](docs/developer/conventions.md) § Reasoning doctrine; reach for them by trigger:
- **the operator bounded ANY budget axis** → `<one-budget>`: a limit on one axis binds **all** of them, in both directions — "don't spend more" bounds the clock, "we don't have five hours" bounds the dollars. Price a proposal in the axis they named *and* the ones they didn't; trading one for another is an increase, and is asked for explicitly. When the budget binds, get more from measurements already paid for.
- **slow / costly / token-heavy LLM call, OR adding anything to a prompt** → `<simplify-the-problem>`: tighten the prompt so the model doesn't *need* the tokens; the timeout, cap and provider are safety rails, not the fix. **Length is a quality tax, not only a bill** — every model degrades as its input grows, so this fires before anything is slow. Attribute a real payload per block before diagnosing, and count the response JSON Schema: it is prompt text.
- **labelling a change "refactor" / LOC work** → `<surface-ledger>`: run `complexity_ledger`; a pass *called* refactor must move the total **down**. The ratchet asserts EQUALITY, so every move costs a baseline edit and a written reason — a win nobody re-pinned rots the baseline exactly as an unexamined raise does.
- **changed what the engine DECIDES, added a capability at one entry point, or CAUGHT one rule implemented twice** → `<entry-point-parity>`: five ways in — CLI, AIs (skill: `/potter-run`), the embedded launch a host program drives (`application/embedded_run.py` — the notebooks and the BBEH harness), REST API, Js-Webapp — and a capability reaching only the one you were editing is half-built. Teach a new value, never dump it. **Periphery instead of parity is urgent the moment it is seen** — and its root is a layer boundary, so the fix is to move the shared piece down into `application/`, never to patch the copy.
- **reaching for the shell, a sub-agent, or a wait** → `<wall-clock>`: the file tools answer in ~0.1s and a shell call carries a fixed toll (median 2.4s, mean 9.4s), so batch shell work and never spend it on something `Read`/`Grep`/`Edit` does. A sub-agent is ~5 minutes: `<read-once>` prices delegation in tokens only, and the correction is that N searches go out in ONE message or not at all. Never `sleep`-poll — background it and let the notification arrive. And iterate on the one check that owns what you touched; the gate is the thing you run once.
- **about to open a file you'll WORK in, or search across >3 files** → `<read-once>`: a narrow read *feels* frugal and isn't — the window keeps every line, so N pokes cost N times. Read whole at four-plus touches; never read file content through `sed`/`cat`/`head`; delegate a >3-file search and ask for the verdict, not the excerpts.

## Commands

```bash
# Every `python` below is the repo venv — `.venv\Scripts\python.exe` (Windows) / `.venv/bin/python`. Bare `python` is a PATH lottery, and the losing ticket is silent: a system interpreter imports promptpotter fine but not its dependencies, so a run starts, replays cache, spawns an inner campaign and only dies at the first live LLM call. `gate.py` re-execs to dodge this; the CLI cannot, because wrapping a run in a subprocess would break the Ctrl+C pause contract.
pip install -e ".[all,dev]"                                  # add `,benchmarks` ONLY to fetch a public bank — opt-in, third-party surface
python scripts/gate.py                                       # EVERY check CI runs, one invocation, nothing masking anything; re-execs itself into the locked env, so the verdict never depends on which python you had. --py / --web to halve it, --only NAME for the one check that owns what you touched
git config core.hooksPath .githooks                           # one-time per clone: `gate.py --staged`, the same list scoped to what you staged
python -m promptpotter new <name>                            # fresh: mint campaign+root cycle from datasets/<name>/, run from round 0
python -m promptpotter new <file.csv> --set task_description=…  # fresh from RAW file: ingest → resolve origin check-in → run
python -m promptpotter resume                                # resume active cycle; Ctrl+C: 1st pauses (resumable, exit 130), 2nd force-quits
python -m promptpotter resume --from N                       # rewind in place
python -m promptpotter resume --fork-on-divergence           # sibling cycle at divergence point
python -m uvicorn promptpotter.main:app --port 8001          # API + webapp control plane at the root (http://localhost:8001/)
```

`new` and `resume` are the loop-mint verbs; lifecycle (`archive`/`delete`/`unarchive`/`reset`, plus `delete-cycle`/`cleanup-empty-cycles` — one named stub and every stub under a campaign), run-control (`pause` — stops a running cycle at its next checkpoint, resumable, the terminal's half of the webapp control; `set-budget` — raises or lowers an existing cycle's spend/token ceiling, which is how a budget-halted cycle is continued: raise, then `resume`; `cancel-queued` — withdraws a launch still waiting for a machine slot, which `pause` cannot serve because a queued mint has no cycle yet; `skip-searchpoint`/`step-cycle`), manifest-edit (`rename` — the campaign's display name; the id still addresses it; `set-allowed-models`/`replace-dataset`), diagnostic (`verify`/`ab`/`noise-floor`/`seed-screen`/`evidence` — the last reads any set of SUBJECTS together (a campaign, one branch, or one searchpoint, at any L4 depth), and is what answers whether they can be compared at all), and maintenance (`reindex`/`restamp`/`compact-archive` — the three that REWRITE stored artifacts rather than reading them; `compact-archive purge-cold --apply` is the only one that destroys paid measurement) verbs also exist. Reads happen by opening files; `evidence` is the one read VERB, because a comparison ACROSS subjects is in no single file. What each verb does, its flags, identity and fork lineage → [`persistence-and-state.md`](docs/operations/persistence-and-state.md).

**The project file tree IS the dashboard**, alongside a webapp control plane at the root (Next.js at `webapp/`) polling the active cycle's `dashboard.json` every 2 s and issuing control verbs through `POST /commands/{kind}`. The most-recent run's terminal readout is mirrored ANSI-stripped to the gitignored **`logs/latest.log`** — read it instead of asking the operator to paste console output (`LiveDisplay._write`). Onboarding: install → restart VS Code → `/potter-run`.

The **front door is a browser chat** — a human-in-the-loop copilot: the operator converses, the Potter posts its work into the thread Perplexity-style (tool calls, web search, each round as it lands), and decisions surface as buttons that fire existing control-plane verbs. The chat is a **canonical agent-chat template** built on a generic activity taxonomy — keep the core (thread model + activity stream + transport), delete the optimizer panes. Contract: [`docs/specs/chat-foundation.md`](docs/specs/chat-foundation.md).

## Conventions

- Full style + code-shape + git rules, and the four banned words → [`docs/developer/conventions.md`](docs/developer/conventions.md).
- **Git — don't commit by default:** **never `git commit` or `git push` unless the operator says so** (a commit ask is not a push ask). **Sole standing exception:** where a project instruction already grants autonomous commits. Ruff format + check before any commit (see Commands).
- **Vocabulary:** say "node" never service/building-block; **"optimizer prompt" never "meta-prompt"** — a prompt the optimizer *runs on*, whose opposite is the **target prompt** it *produces* during optimization (`OptSearchPoint` vs `JobSearchPoint`); for L4 say **outer/inner** (position) and **self-optimization** (the arrangement), never "meta"; **"run init" / the INIT phase, never "bootstrap"** — the chain from `new`/`resume` to round 1 (`application/initialization/`), which the operator already sees as `INIT` / `✓ Initialized`, so a third word for it only hides the concept; the word survives ONLY for machine provisioning (`deploy-linux/bootstrap.sh`) and external proper nouns (DSPy `BootstrapFewShot`); domain framing = evolution (generation/population/fitness/mutation/selection/individual).
- **Sample look-ahead (`/commands/set-sample-lookahead`) is browser-only, and the ABSENCE is the boundary** — the one deliberate `<entry-point-parity>` inversion, so a missing CLI verb / config key / dataset knob is the gate, never an oversight to fix. It is the sole `None` in `cli/campaign_runner.py::CLI_VERB_FOR_KIND`, which is total over the dispatched command set — so the absence is *declared* rather than merely unimplemented, and every other browser-only kind fails at import. Every clause of it — who may press, what one press buys, why the overshot sample is discarded and never recovered — is [`access-model.md`](docs/operations/access-model.md) § host-admin ↔ user.
- **Fewest dependencies possible** in both repos — reach for the stdlib or a small hand-rolled helper before adding a package; every new dependency must earn its place. **Core is the engine; a surface is an extra** — owned by [`ADR-0006`](docs/adr/0006-embeddable-core-and-extras.md): one that earns its place goes in an extra unless it is *measurably* reachable from `cli/campaign_runner.py` or `application/embedded_run.py`.

## Pre-flight gate

Before adding any new concept (class, projection, injection, prompt, field, dict, file), answer these. "I don't know" or "kind of" is a hard block:

- **Reuse before adding.** Does an existing channel/infrastructure already do this? Default **yes** — search/grep first. Ride the ledger / `INJECTIONS` / `OptSearchPoint` / dispatch hub; **no sidecar**.
- **Map to a §0 bucket.** If it fits none, stop — either §0 is incomplete (update it, separate PR landing first) or this is the wrong PR.
- **New I/O kind → amend §0 first.** The five are fixed (Persistence, Display, Control-local, Control-remote, Identity). New Control-remote command/event → declare schema in `docs/specs/api-openapi.yaml` / `events-asyncapi.yaml` *before* the handler lands.
- **Material facts land on disk, human-readable** — never surfaced only via stdout, in-memory state, or `--verbose`. Same for debug-state: if a bug needs an operator-only env to reproduce, the unblocker (mock/fixture/pin) is a separate PR landing first.

## The closing directive

**Ship a distributable `promptpotter-self`** — the optimizer optimizing its own optimizer prompts — **and open no new features until the config is distributable.** Escalate to the operator campaign spend approval, or a compaction handoff. What remains is [`docs/specs/l4-outer-loop.md`](docs/specs/l4-outer-loop.md) § Open; read it there and nowhere else.

**What remains is (mostly) empirical, (mostly) not structural** — the loop, seams, recursion and scoring gateway all exist and are green; what remains is making the optimizer *behave well*, which is found by running L3 campaigns as well as `promptpotter-self`, read what the loop produced, fix, re-run. **Every fix still goes to its ROOT (`<root-fix>` above)** — in this phase the root is usually a prompt or dispatch-hub. **Supervise every run actively — never fire-and-wait. (3 min intervals first, then adjust case dependent)** How to run + supervise + when to reach past prompts to code: `/potter-self`.

**When adding a state, ask what DERIVES it — not just what writes it.** The costliest bugs in this package have all been one shape: a *state the system could enter but not report*, downstream of a predicate that conflated two facts, invisible because the failure mode was silence rather than an error. A writer with no reader is not a state; it is a note nobody reads.

## Pointers

- **Architecture:** [`docs/architecture.md`](docs/architecture.md) §0/§0.5 — backbone primitives, five I/O kinds, the central loop + L1/L2/L3 escalation + L4-is-recursion, searchpoints, scoring, identity, token/cost ledger. **Extend primitives in place** — the wrong shape is meant to be hard to express, not policed by a test.
- **What to pick up next:** [`docs/specs/code-debt-cleanup.md`](docs/specs/code-debt-cleanup.md) — **multi-arc** work and **blocked** work, one line each. It answers "what should I do" when nothing is already in hand; an item ships by being DELETED from it. **Nothing smaller goes there** — a fix you could make in the pass that found it is made there, not filed, and an adjacent finding is part of the topic you are already on.
- **Persistence:** [`docs/operations/persistence-and-state.md`](docs/operations/persistence-and-state.md) — four-entity tree (Workspace → Dataset → Campaign → Cycle), `.promptpotter/` layout, `measurements/`, fork lineage, recovery.
- **Per-layer contracts** — load only the layer you touch; each subpackage's own `CLAUDE.md` auto-loads by directory proximity, and **that chain is already spent before you type**. Deepest wins: `application/optimization/` pulls four files and costs roughly triple a session start, `webapp/` and `infrastructure/` about double. Two rules follow — never open a second layer's file to check one fact (grep it), and a page you add to a layer is paid by everyone who edits there, not just the reader who wanted it. The index is [`promptpotter/CLAUDE.md`](promptpotter/CLAUDE.md); the roster is that directory listing.
- **Contracts:** the ADRs in [`docs/adr/`](docs/adr/) are the permanent constitutions. **The roster is that directory listing** — enumerating a subset anywhere is what let two of them go unindexed, and a second copy is what made the two indexes disagree. Index map: [`docs/CLAUDE.md`](docs/CLAUDE.md).
- **Design surface:** [`BRAND.md`](BRAND.md) (visual identity — theme-is-audience, dark=operator, light=buyer) + [`VOICE.md`](VOICE.md) (copy register — the Potter is force-multiplier, not friendly-wizard).
- **Skills:** [`.claude/skills/`](.claude/skills/) — `potter-run` (launch + supervise a campaign)
- **Product story, feature framing, docs index:** [`README.md`](README.md) — the buyer-facing front page. **Open it when the ask is about positioning, the peer table or how we describe a feature; it is deliberately NOT inlined.** It is written for a reader who has not cloned the repo — badges, BibTeX, and ~30% absolute GitHub URLs — so importing it spent ~4.6k tokens a session on bytes an agent in this tree cannot use, and put a second, buyer-voiced copy of the entry-point count and the run verbs beside the ones above. Don't restate it here either.
