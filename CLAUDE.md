# CLAUDE.md

> Read [`docs/architecture.md`](docs/architecture.md) §0 + §0.5 first — what every PR measures against.

## What this is

PromptPotter is **LLM-driven program evolution** for prompts and pipeline params. **Orchestration is the product; backends are pluggable and read-only** — node tunables ride the per-call overlay ([`promptpotter/assets/datasets/CLAUDE.md`](promptpotter/assets/datasets/CLAUDE.md) § Sole route).

**The default operating context is the cloud team-online deployment (tier 3)**, not local-secure (tier 2): [`deploy-linux/`](deploy-linux/) — Cloudflare tunnel, open OIDC signup, a per-account spend ceiling.

**Every run OPTIMIZES (anything on disk that helps is admissible) or COMPARES (a controlled arm, minted by `head-to-head`, reads only what a fresh run of itself would) — ask which before touching any mechanism that reads past the run** ([`docs/decisions.md`](docs/decisions.md) § 0009).

**Origin = campaign root = C0** (for a fork, the point it branches from). An *individual/candidate* is a configuration (`OptSearchPoint`); its **parent** is what it was mutated from — each arm's `reference_id`.

**Fitness is formula-relative and mode-relative; the backend serves every score** — owned by architecture.md §0.5.

⚠️ **Name the column before you quote or defend a number** — owned by [`docs/methods/verdict-resolution.md`](docs/methods/verdict-resolution.md) § Reading a round: an `ability` election is won on θ, not accuracy, unless a `ThetaCaveat` is served; `improved` is a bare point estimate, `rounds_to_separable` is what a result quotes.

## First — decide what KIND of ask this is, then load for it

| The ask | Load |
|---|---|
| Run / watch a campaign | `/potter-run` · the cycle's `readout.log` · [`persistence-and-state.md`](docs/operations/persistence-and-state.md) · [`observability.md`](docs/operations/observability.md) |
| Diagnose a live or stuck run | [`persistence-and-state.md`](docs/operations/persistence-and-state.md) § Diagnosing a live or stuck run — ledger first, mtimes last |
| L4 / `promptpotter-self` | [`l4-outer-loop.md`](docs/specs/l4-outer-loop.md) · `/potter-self` · [`optimizers/potter/CLAUDE.md`](promptpotter/application/optimizers/potter/CLAUDE.md) · [`dispatch-hub.md`](docs/developer/dispatch-hub.md) |
| Dataset / benchmark | [`promptpotter/assets/datasets/CLAUDE.md`](promptpotter/assets/datasets/CLAUDE.md) · [`datasets.md`](docs/operations/datasets.md) (adding one; § The model a dataset runs) |
| Engine code (Python) | architecture.md §0 + §0.5 · the one layer's `CLAUDE.md` · [`conventions.md`](docs/developer/conventions.md) · [`adding-a-surface.md`](docs/developer/adding-a-surface.md) · [`tests/CLAUDE.md`](tests/CLAUDE.md) before any test |
| Does my change move what the loop decides? | [`offline-run.md`](docs/developer/offline-run.md) |
| Changed the formula / PoBB / the engine: carry over or fork? | [`mask-projection.md`](docs/operations/mask-projection.md) |
| How a layer heals a failure | [`self-healing-internals.md`](docs/developer/self-healing-internals.md) |
| Access / security | [`access-model.md`](docs/operations/access-model.md) |
| Variation, pools, recombining optimizers | [`docs/decisions.md`](docs/decisions.md) § 0007 · [`variation-and-pools.md`](docs/specs/variation-and-pools.md) · [`roadmap.md`](docs/specs/roadmap.md) § Variation and pools |
| An outside source | [`docs/research/CLAUDE.md`](docs/research/CLAUDE.md) · [`landscape.md`](docs/research/landscape.md) |
| Webapp code | [`webapp/CLAUDE.md`](webapp/CLAUDE.md) · [`frontend-surface-contract.md`](docs/specs/frontend-surface-contract.md) § Invariants · `/design` |

**The archive is filed by the dataset a row MEASURED, never the campaign that paid for it** — [`infrastructure/store/CLAUDE.md`](promptpotter/infrastructure/store/CLAUDE.md) § The archive is not scoped by campaign.

## STOP — no backward compatibility, ever

Zero released versions, zero data worth keeping. **The most-ignored rule here.** Delete on sight, no ask, no TODO: shims, fallback chains, breadcrumb comments, a second mechanism beside the canonical one. Consolidate small duplications on sight; a change that could ride an existing channel does. **This covers on-disk shape**: design the right shape, then wipe the data — count it first (`ls .promptpotter/projects/*/campaigns`), never recall it.

**Names are distinct and self-describing.** Grep for collisions; a second word for something the repo already names is a deletion, not a synonym; a name that stopped describing its contents is renamed with its doc and `CLAUDE.md` row in the same commit.

<root-fix>
**A fix that compensates for something an upstream layer should make true belongs upstream — ship the root, never the patch, and never offer the patch as an option.** "Multi-arc", "identity change" and "touches N modules" are not reasons to keep a bridge: a join or fallback over an upstream wrongness, a relabel over a mixed concept, a field no one reads, a helper beside the mechanism, a callable or flag passed so a caller steers what the callee should own. A fix at N sites means the model has the wrong shape — realign it. A sub-agent that "kept the bridge, filed the root" is sent back.
</root-fix>

## Working principles

**Autonomy has two modes; `CLAUDE.local.md` (gitignored) picks one.**
- **careful** (default): verification up to ~$0.30 (a probe, an A/B, a `seed-screen`, a re-measure) runs without asking; above that, and anything minting a cycle (`new` / `resume` / `promptpotter-self`), asks.
- **operator** (`autonomy: operator` + `daily_spend_cap_usd`): launch, resume, pause and relaunch campaigns (backgrounded, stdout to `.scratch/`, supervised), start needed services, spend up to the day's cap; report each command and its cost.
- **Both ask** for a commit, a push, `compact-archive purge-cold --apply`, and any change to the live box (reading it is free: `/potter-box`).

- **the operator bounded ANY budget axis** → `<one-budget>`: a limit on one axis binds all of them, both directions. Price a proposal in every axis; trading one for another is an increase, asked for. When the budget binds, get more from measurements already paid for.
- **a number tempts "cannot be improved"** → `<reachable-by-default>`: every task is improvable by a good enough optimizer. Write what the tried prompts did, never "capability limit"; a flat result is first a fact about the proposer, its evidence, the instrument or the election.
- **slow / costly LLM call, OR adding anything to a prompt** → `<simplify-the-problem>`: tighten the prompt so the model needs fewer tokens; timeout, cap and provider are rails, not the fix. Length is a quality tax. Attribute one real payload by block first — the response JSON Schema is prompt text.
- **labelling a change "refactor"** → `<surface-ledger>`: `python scripts/complexity_ledger.py`; each row of the gate's `surface-ledger` check is a ceiling, so a raise edits the baseline with its reason in the commit body, and a "refactor" may not raise it.
- **planning, scoping, reviewing or ranking any change** → `<next-change>`: the target is the cost of the next changes; state that direction first. Score by sites a probe change touches before → after (rename a served field, add a prompt node field, add an optimizer member) and by test churn; lines are reported, never the goal.
- **changed what the engine DECIDES, added a capability at one entry point, or caught one rule implemented twice** → `<entry-point-parity>`: five entry points — CLI, `/potter-run`, the embedded launch (`application/embedded_run.py`), REST API, webapp — share one orchestration layer. Parity is part of done; a new value reaches the operator taught on an existing channel, never dumped. A rule spelled twice across adapters is urgent: move the shared piece down into `application/`, leave thin shells. Sample look-ahead is browser-only by design ([`presentation/CLAUDE.md`](promptpotter/presentation/CLAUDE.md) § Sample look-ahead).
- **reaching for the shell, a sub-agent, or a wait** → `<wall-clock>`: one batched call, N sub-agents in ONE message, never a sleep-poll; iterate on `gate.py --changed` / `--only NAME`.
- **opening ANY file, for any reason** → `<situational-awareness>`: read it critically, never only for the
  task. A shape that feels wrong gets one line in that turn's reply and an entry in
  [`code-debt-cleanup.md`](docs/specs/code-debt-cleanup.md), on-task or not; the operator judges it:
  - a global standing in for a keyed subject;
  - a flag keeping two concepts apart;
  - two doors into one state;
  - a comment explaining a workaround;
  - an error swallowed into a log.

  Filed early, a debt gathers evidence until its canonical shape is clear; noticed and kept silent, it
  grows. A sub-agent reports what it noticed beside what it did. It proposes, never widens the diff.
- **opening a file you'll WORK in, or searching >3 files** → `<read-once>`: read it whole once, independent calls in ONE message; a lookup that missed fixes the pointer that should have answered it.

## Commands

```bash
# `python` = the repo venv (.venv/Scripts/python.exe | .venv/bin/python); bare python refuses at import.
python scripts/gate.py                       # every CI check in the locked env (builds .venv first); --py / --web / --only NAME / --changed
python scripts/gate.py --release             # before a release: npm advisories + open Dependabot alerts
git config core.hooksPath .githooks          # once per clone
python -m promptpotter new <name>            # fresh campaign from promptpotter/assets/datasets/<name>/
python -m promptpotter new <file.csv> --set task_description=…
python -m promptpotter resume                # Ctrl+C once pauses (exit 130), twice force-quits; --from N rewinds; --fork-on-divergence
python -X utf8 -m uvicorn promptpotter.main:app --port 8001   # API + webapp at http://localhost:8001/
```

**`-X utf8` is load-bearing**: a cp1252 server serves every read and refuses every container-backed launch.

**Print every operations command you invoke, verbatim, beside its result** (`python -m promptpotter …` and maintenance verbs; not `docker`/`curl`/`ls`). Showing it is not handing it over.

The verbs are [`persistence-and-state.md`](docs/operations/persistence-and-state.md)'s. **The read verbs are the ones whose answer is in no file**: `evidence`, `cycles`, `machine-status`, `trail`, `price-head-to-head`; every other read is opening the tree. **The file tree is the dashboard**: each run writes `cycles/{cycle_id}/readout.log` — read it instead of asking for console output.

## Conventions

- Style, code shape and git: [`docs/developer/conventions.md`](docs/developer/conventions.md).
- **Never `git commit` or `git push` unless the operator says so** (a commit ask is not a push ask).
- **Vocabulary:** "node", never service/building-block; **"optimizer prompt"** (runs the optimizer, `OptSearchPoint`) vs **"target prompt"** (what it produces, `JobSearchPoint`), never "meta-prompt"; L4 is **outer/inner** and **self-optimization**, never "meta"; **"run init" / INIT**, never "bootstrap" (except machine provisioning and DSPy `BootstrapFewShot`); framing is evolutionary (generation, population, fitness, mutation, selection, individual).
- **Fewest dependencies** — stdlib or a small helper first. Core is the engine, a surface is an extra ([`docs/decisions.md`](docs/decisions.md) § 0006).

## Pre-flight gate

Before adding a concept (class, projection, injection, prompt, field, dict, file) — "I don't know" blocks:
- **Reuse first**: ride the ledger / `injection_table()` / `optimizer_state` / dispatch hub; no sidecar.
- **It maps to a §0 bucket** ([`architecture.md`](docs/architecture.md) §0), a new route or command declares its model before the handler, and its facts land on disk (architecture § Everything material). A bug needing an operator-only env gets its mock/fixture/pin first.
- **When adding a state, ask what DERIVES it.** A state the system can enter but not report is the costliest bug shape here; a writer with no reader is not a state.

## The closing directive

**What closes next is [`roadmap.md`](docs/specs/roadmap.md) § Sequence's.** **Supervise every run actively — never fire-and-wait** (3-minute checks first): `/potter-self`.

## Compact instructions

Keep: the task and the check that closes it, every operator decision, paths touched, each measured number with its command, what is still owed. Drop file contents and tool output.

## Pointers

- **Pick-up work:** [`docs/specs/code-debt-cleanup.md`](docs/specs/code-debt-cleanup.md) — multi-arc and blocked work only.
- **Per-layer contracts:** each subpackage's `CLAUDE.md` auto-loads by directory; load only yours, grep another layer's.
- **Design surface:** `BRAND.md` in the site repo (`runfish5/promptpotter-web`); `webapp/app/styles/foundation/tokens.css` mirrors it.
