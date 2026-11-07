# CLAUDE.md

> [`docs/architecture.md`](docs/architecture.md) §0 + §0.5 is the contract every PR measures against.

## What this is

PromptPotter is **LLM-driven program evolution** for prompts and pipeline params. The bench runs
optimizers and is not one; backends are pluggable and read-only — node tunables ride the per-call
overlay ([`promptpotter/assets/datasets/CLAUDE.md`](promptpotter/assets/datasets/CLAUDE.md) § Sole route).

- **The default operating context is the cloud team deployment** ([`deploy-linux/`](deploy-linux/)), not a local box.
- **Every run OPTIMIZES or COMPARES** ([`docs/decisions.md`](docs/decisions.md) § 0009); ask which before touching a mechanism that reads past the run.
- **Origin = campaign root = C0**; an individual's **parent** is what it was mutated from; never "baseline".
- **Fitness is formula- and mode-relative, and the backend serves every score** (architecture §0.5). **Name the column before you quote a number** ([`verdict-resolution.md`](docs/methods/verdict-resolution.md) § Reading a round).
- **The archive is filed by the dataset a row measured, never the campaign that paid** ([`store/CLAUDE.md`](promptpotter/infrastructure/store/CLAUDE.md) § The archive is not scoped by campaign).

## Load for the kind of ask

| The ask | Load |
|---|---|
| Run, watch or diagnose a campaign | `/potter-run` · the cycle's `readout.log` · [`persistence-and-state.md`](docs/operations/persistence-and-state.md) |
| L4 / `promptpotter-self` | `/potter-self` · [`l4-outer-loop.md`](docs/specs/l4-outer-loop.md) · [`optimizers/potter/CLAUDE.md`](promptpotter/application/optimizers/potter/CLAUDE.md) |
| Dataset / benchmark | [`assets/datasets/CLAUDE.md`](promptpotter/assets/datasets/CLAUDE.md) · [`datasets.md`](docs/operations/datasets.md) |
| Engine code | architecture §0 + §0.5 · the one layer's `CLAUDE.md` · [`conventions.md`](docs/developer/conventions.md) · [`tests/CLAUDE.md`](tests/CLAUDE.md) before a test |
| Does my change move what the loop decides? | [`offline-run.md`](docs/developer/offline-run.md) |
| Access / security | [`access-model.md`](docs/operations/access-model.md) |
| Webapp | [`webapp/CLAUDE.md`](webapp/CLAUDE.md) · [`frontend-surface-contract.md`](docs/specs/frontend-surface-contract.md) |
| The live box, a release | `/potter-box` · `/potter-release` |

## Non-negotiables

- **No backward compatibility, in code or run data.** Delete on sight, no ask, no TODO: shims, fallback chains, breadcrumb comments, a second mechanism beside the canonical one. Consolidate small duplications on sight; a change that could ride an existing channel does. A reshape of run data's on-disk form wipes the run data (count it first: `ls .promptpotter/projects/*/campaigns`), never a reader of two shapes.
- **A survivor is migrated, never wiped** — what an account is, agreed to, authored or was charged: the SURVIVOR rows of `store/data_root.py`. A reshape ships a one-shot migration under the landing ([`docs/decisions.md`](docs/decisions.md) § 0011).
- <root-fix>**Ship the root, never the patch.** A fix that compensates for what an upstream layer should make true belongs upstream: a join or fallback over an upstream wrongness, a relabel over a mixed concept, a field no one reads, a helper beside the mechanism, a callable or flag passed so a caller steers what the callee should own. A fix at N sites means the model has the wrong shape. "Multi-arc" and "touches N modules" are not reasons to keep a bridge, and the patch is never offered as an option. A sub-agent that "kept the bridge, filed the root" is sent back.</root-fix>
- <entry-point-parity>**Five entry points — CLI, `/potter-run`, `application/embedded_run.py`, REST API, webapp — share one orchestration layer.** A capability or a changed decision reaches all five, taught on an existing channel; parity is part of done. A rule spelled twice across adapters moves down into `application/`. Carve-outs: CLI-only verbs and browser-only sample look-ahead ([`presentation/CLAUDE.md`](promptpotter/presentation/CLAUDE.md) § Sample look-ahead).</entry-point-parity>
- **Names are distinct and self-describing.** Grep for collisions; a second word for a thing the repo already names is a deletion; a name that stopped describing its contents is renamed with its doc and `CLAUDE.md` row in the same commit.
- **Never `git commit` or `git push` unless the operator says so**; a commit ask is not a push ask. Also asked: `compact-archive purge-cold --apply`, and any change to the live box (reading it is free).
- **Spend.** `CLAUDE.local.md` (gitignored) picks the autonomy mode: **careful** (default) runs verification up to ~$0.30 unasked and asks above it or for anything minting a cycle (`new` / `resume` / `promptpotter-self`); **operator** (`autonomy: operator` + `daily_spend_cap_usd`) launches, resumes, pauses and relaunches up to the day's cap, starts needed services, reporting each command and its cost. <one-budget>A limit on one budget axis binds all of them, both directions: price a proposal in every axis; trading one for another is an increase, asked for. When the budget binds, get more from measurements already paid for.</one-budget>

## Vocabulary

"node", never service/building-block · **"optimizer prompt"** (`OptSearchPoint`) vs **"target prompt"** (`JobSearchPoint`), never "meta-prompt" · L4 is **outer/inner** and **self-optimization**, never "meta" · **"run init" / INIT**, never "bootstrap" (except machine provisioning and DSPy `BootstrapFewShot`) · evolutionary framing: generation, population, fitness, mutation, selection, individual. The banned words: [`conventions.md`](docs/developer/conventions.md) § Naming.

## Working

- **Before adding a concept** (class, projection, injection, prompt, field, file): reuse first — the ledger, `injection_table()`, `optimizer_state`, the dispatch hub; it maps to a §0 bucket; its facts land on disk; **a state the system can enter but not report is the costliest bug here** — ask what derives it.
- <situational-awareness>**Read every file critically.** A shape that feels wrong — a global standing in for a keyed subject, a flag keeping two concepts apart, two doors into one state, a workaround comment, an error swallowed into a log — gets one line in the reply and, if more than one edit closes it, a row in [`code-debt-cleanup.md`](docs/specs/code-debt-cleanup.md). Propose; never widen the diff.</situational-awareness>
- **A slow or costly LLM call is fixed by a tighter prompt**, not a timeout, cap or provider (rails, not the fix); length is a quality tax; attribute one real payload by block first.
- **Supervise every run actively, never fire-and-wait** (3-minute checks first). The file tree is the dashboard: each run writes `cycles/{cycle_id}/readout.log`; the read verbs whose answer is in no file are `evidence`, `cycles`, `machine-status`, `trail`, `price-head-to-head`.
- **Print every `python -m promptpotter …` command you invoke, verbatim, beside its result.**
- **An arc owns its files.** Arcs that run together touch disjoint files, or name the one they share. Arcs whose files overlap run as a chain: each link branches off the previous one's tip, and only disjoint chains run in parallel. Arcs that conflict get one fix pass on their merge ([`refactor-arcs.md`](docs/developer/refactor-arcs.md) owns the process).
- A "refactor" may not raise a row of the gate's `surface-ledger` check (`scripts/complexity_ledger.py`); a raise edits the baseline with its reason in the commit body.
- Fewest dependencies: stdlib or a small helper first; core is the engine, a surface is an extra ([`docs/decisions.md`](docs/decisions.md) § 0006).

## Commands

```bash
# `python` = the repo venv (.venv/Scripts/python.exe | .venv/bin/python); bare python refuses at import.
python scripts/gate.py                       # every CI check in the locked env (builds .venv first); --py / --web / --only NAME / --changed
python scripts/gate.py --release             # before a release: npm advisories + open Dependabot alerts
git config core.hooksPath .githooks          # once per clone
python -m promptpotter new <name>            # fresh campaign from promptpotter/assets/datasets/<name>/
python -m promptpotter resume                # Ctrl+C once pauses (exit 130), twice force-quits; --from N rewinds; --fork-on-divergence
python -X utf8 -m uvicorn promptpotter.main:app --port 8001   # API + webapp; -X utf8 is load-bearing
```

## Pointers

- What closes next: [`roadmap.md`](docs/specs/roadmap.md) § Sequence. Pick-up work: [`code-debt-cleanup.md`](docs/specs/code-debt-cleanup.md).
- Each subpackage's `CLAUDE.md` auto-loads by directory; load only yours, grep another layer's. Docs rules: [`docs/CLAUDE.md`](docs/CLAUDE.md).
- Design surface: `BRAND.md` in the site repo (`runfish5/promptpotter-web`); `webapp/app/styles/foundation/tokens.css` mirrors it.
- Compacting: keep the task and its closing check, every operator decision, paths touched, each measured number with its command, what is still owed.
