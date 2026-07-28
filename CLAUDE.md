# CLAUDE.md

> Read [`docs/architecture.md`](docs/architecture.md) first — §0 + §0.5 are what every PR measures against. Don't restate here.

## What this is

PromptPotter is **LLM-driven program evolution** for prompts and pipeline params. **Orchestration is the product — backends are pluggable and read-only.** Node tunables ride a per-call overlay (`datasets/{name}/pipeline.json::nodes.{name}.config`).

The **front door is a chat** — a human-in-the-loop copilot: the operator converses, the Potter posts its work into the thread Perplexity-style (tool calls, web search, each round as it lands), and decisions surface as buttons that fire existing control-plane verbs. The chat is a **canonical agent-chat template** built on a generic activity taxonomy — keep the core (thread model + activity stream + transport), delete the optimizer panes. Contract: [`docs/specs/chat-foundation.md`](docs/specs/chat-foundation.md).

**Origin = the starting configuration = C0 — one word, one thing.** An individual **is** a configuration (the origin resolves to an `OptSearchPoint`, the same type every candidate is), so "the config the loop starts from" and "C0, the first candidate" are one statement. For a fork, the point it branches *from*. It **arrives incomplete** — *check-in* resolves the required inputs the pipeline declares and gates it; once through, it is the **parent of round 1's candidates** (round 0 isn't parented — round 0 *is* C0, measured). **The general relation is *parent***: the individual a candidate was mutated from (`RoundParent`). At round 0 the parent is the origin, after that the prior winner — so **reserve "origin" for offset 0 and the fork point, and say *parent* everywhere else**. Full definitions + the two gates (readiness + round-0 origin gate): [`docs/architecture.md`](docs/architecture.md) §0.5.

**Fitness is never one fixed number — always ask "under which formula?"** It is formula-relative (**active** / **what-if** / **lens** / **replay**) and mode-relative (`measured` subset vs `all`). Every score — the active fitness, any alternative formula or mode, the hard-sample sort — is computed and **served by the backend; the webapp never recomputes.** Depth: [`docs/architecture.md`](docs/architecture.md) §0.5 (Composite-fitness resolution chain) + [`docs/concepts/scoring-and-memory.md`](docs/concepts/scoring-and-memory.md).

## STOP — no backward compatibility, ever

Zero released versions, zero stale on-disk data — nothing to be compatible with. **This is the rule that gets ignored most often.**

Delete on sight — don't ask, don't TODO, don't "remove later":
- **Shim code**, **Fallback chains**, **Breadcrumb comments**, **Redundant mechanisms** (a second validator / surface / code path doing the same *kind* of work as an existing one — fold it into the canonical mechanism, never add beside it).

Many of the remaining wins are low-value-but-they-all-count consolidations, and they get ignored precisely because each looks too small to bother. **Lean to suspicion.** When a change *could* ride an existing channel, assume it *should*; when you pass redundant paths mid-task, collapse them then — don't note-and-move-on. Fewer lines beats more lines at equal behavior.

**This covers ON-DISK SHAPE, not just code — and that is the half that keeps getting read backwards.** "No backward compatibility" is not permission to leave a bad shape alone because data exists in it; it is an instruction to **pick the right shape and then delete or migrate the data to fit**. Aggressively: wipe the store, restamp it, or partially both. The persistence layer is still being shaped, and it is far cheaper to shape it now than after release — **so a design decision must never be bent around what happens to be on disk today**. Measure whether the data is worth keeping (is the cache ever hit? is the field derivable from what sits beside it?), then act on the answer.

**2026-07-26 — the store was reset: `.promptpotter/.inner` + `.promptpotter/projects` deleted (576 MB, 11 campaigns, 60 campaign-dirs of round files).** 0 campaigns on disk, so a shape change made now carries none of its usual migration risk. Re-read that date before invoking migration caution: if the store is still empty, there is nothing to migrate and the caution does not apply.

<root-fix>
When a fix would compensate for something an upstream layer should already have made true, the fix belongs upstream — not at the site where the symptom shows up. Name the structural cause and propose the upstream fix <em>before</em> touching the visible surface. The operator can still pick the patch, but they pick it knowingly. Default to root, not to symptom.
</root-fix>

<dispatch-first>
**★ FIRST PRINCIPLE, THIS PHASE — fix it in the dispatch.** The target model is small: a prompt is one *information package* it must reason from, and it finds the right path only when that package is short and every line is unique, high-value, and in the right place. The **dispatch hub** (`application/optimization/dispatch/`) is where raw measurement is recomposed into those packages and wired into every optimizer prompt through **deterministic wires** (`DispatchHub.fill`). So the dispatch is the FIRST place to fix a bloated / low-value / misplaced prompt — and the one place to be **creative**: add functions, processing, recomposition, new panel logic, whatever shapes the incoming information to be *perfectly formed for its slot*. The wires stay deterministic; the intelligence lives in the shaping. Corollaries: a panel renders only what adds signal **for this task/state** and stays silent otherwise (the `answer_distribution` / suppressed-RANK rule); duplication, paraphrase, filler, and task-mismatched blocks are dispatch problems to reshape at source — never a mechanical downstream patch (a render-time dedup, a louder meta-prompt clause) or a symptom papered over.
</dispatch-first>

**Working principles** — three *situational* guardrails against recurring AI blind spots live in [`docs/developer/conventions.md`](docs/developer/conventions.md) § Reasoning doctrine; reach for them by trigger:
- slow / costly / token-heavy LLM call → `<simplify-the-problem>`: tighten the prompt so the model doesn't *need* the tokens; the timeout/cap/provider are safety rails, not the fix.
- simplifying / labelling a change "refactor" / LOC work → `<surface-ledger>`: run `complexity_ledger`; a pass *called* refactor must move the total **down** — subtract a *named* concept, don't relocate one. The ledger records, it doesn't block: a raise that ships a feature or makes the codebase quicker to develop is fine — edit the baseline, write the reason.
- changed what the engine *decides* (a gate / metric / state) → `<reach-the-operator>`: engine-correct ≠ product-complete — webapp parity is part of done; teach a new value, don't dump it.

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

`new` and `resume` are the loop-mint verbs; lifecycle (`archive`/`delete`/`unarchive`/`reset`) + diagnostic (`verify`/`ab`/`noise-floor`/`reindex`) verbs also exist (cheap L1 A/B sweeps ride `new --sweep-batch`, not a verb) (`ab` = deterministic A/B replay — re-derive a recorded cycle's decisions under the current engine/scorer, zero LLM calls; `noise-floor` = re-score a campaign's cached origin `--k` times with `force_fresh` to read backend run-to-run noise — a fenced debug diagnostic, never wired into the loop). Reads happen by opening files (no read CLI). `.env` with `GROQ_API_KEY` (or OPENAI/ANTHROPIC/OPENROUTER) required; provider is per-dataset in `datasets/_optimizer/pipeline.json::nodes.{node}.config.provider`. CLI flags, identity/cycle/campaign identity, fork lineage → [`docs/operations/`](docs/operations/) + [`persistence-and-state.md`](docs/operations/persistence-and-state.md).

**The project file tree IS the dashboard**, alongside a read-only webapp at the root (Next.js at `webapp/`) polling the active cycle's `dashboard.json` every 2 s. The most-recent run's terminal readout (per-sample HIT/MISS, SP tables, round summaries) is mirrored ANSI-stripped to the gitignored **`.goldmine/latest.log`** — read it instead of asking the operator to paste console output (`LiveDisplay._write`). Onboarding: install → restart VS Code → `/potter-run`.

## Conventions

- Full style + code-shape + git rules → [`docs/developer/conventions.md`](docs/developer/conventions.md); enumerations → [`docs/glossary.md`](docs/glossary.md).
- **Git — don't commit by default:** **never `git commit` or `git push` unless the operator says so** (a commit ask is not a push ask). **Sole standing exception:** where a project instruction already grants autonomous commits — the L4 closing-phase "commit small green arcs" (see Current focus) and endorsed drain passes — and **even then never push**. Ruff format + check before any commit (see Commands).
- **Vocabulary:** say "origin" never "baseline" (R-23); "node" never service/building-block; domain framing = evolution (generation/population/fitness/mutation/selection/individual).
- **Fewest dependencies possible** in both repos — reach for the stdlib or a small hand-rolled helper before adding a package; every new dependency must earn its place.

## Known issues

- ⚠️ **Renaming a `CampaignConfig` / `Campaign` field is a data migration, not a code change.** `extra="forbid"`, and the config rides **two** on-disk surfaces: the minted manifest `campaigns/{id}/campaign.json::config` and the dataset template `datasets/{slug}/campaign.json::campaign_config` (read by three dataset endpoints + the launcher). A rename makes `load_campaign_config` raise `extra_forbidden` on every file still naming it — `resume`/`ab`/`verify`/`noise-floor`/L4 die at load on the manifest; the three dataset reads 500 on the template. Fired **three times**: `5c0722a1` (50/177 campaigns), an `optimization.exploration` flatten (156/169), and a live template 500 on `swiss-invoices-eval` (2026-07-17). **A distributable `promptpotter-self` cannot re-stamp a paying user's data** — those are measurements we don't own. "No backward compatibility" licenses breaking *code*, never a user's data.
  **What is in place.** Both surfaces persist the **delta from defaults** (`freeze_campaign_config`) — a knob nobody set is absent, so renaming it is free (the template joined the manifest here, at `_build_default_campaign_json`). A knob the operator *did* set is still written down and still breaks: the delta only shrinks the blast radius, the **fixtures are the guard** — both pinned through the real reader (`tests/fixtures/cycles/frozen_campaign/` manifest + `frozen_dataset_template/` template). `deploy-linux/update.sh` runs `restamp_campaign_configs.py --apply` on every deploy, repairing a stale-key file at deploy rather than on first read. Never `extra="allow"`, an alias, or a migration shim.

  **This note is about a DEPLOYED tenant's data, and it is routinely misread as a reason to be timid pre-release.** It is not. As of the 2026-07-26 reset there are **0 campaigns on disk**, so renaming or deleting a persisted field costs nothing today — check the store before invoking this caution, and if it is empty, shape the field correctly and move on. The rule it really encodes is narrow: *we may break our own code freely; we may not silently break measurements a paying tenant owns.* Two different situations.

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

The project is in its **closing phase: ship a distributable `promptpotter-self`** (the optimizer optimizing its own meta-prompts). The L4 recursion is SHIPPED + live-validated; the remaining work is the **living finish-line plan** in [`docs/specs/l4-outer-loop.md`](docs/specs/l4-outer-loop.md) § Finish line (headroom inner benchmark `justlogic-d234`, inner-spend rollup, bounded cheap default config; the specialized `_optimizer_meta/` outer prompts SHIPPED `28f9c720` and are no longer the gating slice — this line said they were for four months after they landed). **The AI agent owns L4 end-to-end and drives it autonomously** — refine the plan, build the slices, commit small green arcs. Escalate to the operator ONLY for genuine actions: real multi-campaign spend approval, a provider/account change, or a compaction handoff. Do **not** open new features until the config is distributable.

**What remains is empirical, not structural** — the loop, seams, recursion, and scoring gateway all exist and are green; what remains is making the optimizer *behave well*, found by running it (run `new promptpotter-self` on `justlogic-d234`, read what the loop produced, fix, re-run). **Every fix still goes to its ROOT (`<root-fix>` above)** — in this phase the root is usually a prompt rather than code, but that is an observation about where the causes have been, never a licence to patch the symptom. Name the cause, then pick the site. **Supervise every run actively — never fire-and-wait.** How to run + supervise + when to reach past prompts to code: [`docs/specs/l4-outer-loop.md`](docs/specs/l4-outer-loop.md) § Running & supervising.

**Deferred holistic reframes** (larger chunks — noted so they aren't mistaken for done; don't slip them into a release): the `ui/HoverCard` primitive currently rides ONE hover — the native `title=` tooltips spread across the webapp (~190 sites) are the same job and should consolidate onto it incrementally, alongside the three bespoke popovers vs `ui/Popover`. Small tail owed from the same arc: keep candidate-CI resolution one seam if a third whisker source ever appears (CLT default vs θ-band override).

> **The `.inner` half of the deletion/liveness redesign LANDED 2026-07-26.** Recorded because the note it replaces got two things wrong, and both mistakes are the instructive part.
>
> **What was measured:** `.inner` had reached **343 MB across 9 sandboxes — 60% of the entire store** — holding **0.04 MB** of actual cached measurements. Essentially all of the rest was ONE write: `FileSink._write_observation`, a per-(sample × candidate × round) JSON dump that **no code in the repo reads back** except its own finalizer.
>
> **Wrong #1 — "it needs the `robocopy /MIR` trick".** No: `Remove-Item` (PowerShell) fails on these trees, but the package already had a working long-path deleter. Probed on a 668-char tree: plain `shutil.rmtree` leaves it standing, `\\?\`-prefixed removes it. The real defect was that the *pre-mint sweep* used a bare `shutil.rmtree(…, ignore_errors=True)` — so it failed **silently** and left the stale sandbox exactly where its comment promised it was gone. There is now ONE deleter, `store/io.py::rmtree_robust`; a bare `shutil.rmtree` in this package is a bug.
>
> **Wrong #2 — "reclamation is the requirement".** Reclamation is the *symptom's* requirement. The root fix is upstream: an inner cycle is an **instrument**, and an instrument does not dump per-observation traces — the identical argument that already force-disables the *cloud* sink for inner cycles (`runner/inner/cycle.py`), which had simply never been applied to the local one. Gated on `instrument_depth()`, so top-level campaigns are untouched. That also removes the deepest path the package produces, which is why long-path deletion stopped being the interesting problem.
>
> Reclamation still exists, narrowly: `reaper.py::reclaim_orphan_sandboxes` deletes a sandbox whose **owner cycle is gone** — unreachable by every reader, a fact rather than a policy. It deliberately does NOT reclaim on a *terminal* owner: drilling into a finished L4 campaign walks into exactly those trees, and a reaper that guesses at that difference destroys measurement history to save disk. `test_reaper.py` pins both directions.
>
> **Deletion "failing in practice" — REPRODUCED and fixed, same day.** Cause: `_is_active_campaign` refused Archive *and* Delete whenever the campaign was merely the ACTIVE one. In a single-operator workspace the campaign in view IS the active one, so both verbs answered "switch first" — naming an escape that exists in **neither** the command vocabulary nor the webapp (there is no switch/activate gesture anywhere). Two dead buttons, and the error read like a precondition rather than a dead end.
>
> The guard conflated two facts its own docstring listed separately — "would strand the pointer" and "open `.runtime/` handles". Only the second is a hazard, and it is about a LIVE cycle, not a selected one. Now: refuse while any cycle derives `RUNNING` (through the one liveness function, not a second opinion), otherwise release the pointer and proceed. Deleting what you are looking at is the ordinary case. `test_reaper.py` pins both directions; `mutations.ts` had a comment asserting "deletion is never physical at this site", which was false and is gone.
>
> **The dock — CLOSED, and it was one missing derivation, not a redesign.** The webapp was already correct: one `IN_FLIGHT_PHASES` set, one shared ordering, a `.phase-gate` style, an "Origin gate" label. All of it dormant, because **`gate` could never arrive**. It is DECLARED by the runner (no `.runtime/` flag — only the runner can know it), and `derive_run_phase` never read the declaration, so every non-live reader saw plain `running`.
>
> Underneath that sat a live data-loss bug. The gate's wait is the package's only UNBOUNDED await — it ends when a human decides — and it wrote nothing. So: at 30s the cycle read `detached` and **left the operator's dock while waiting on that operator**; at 15 min the reaper stamped it TERMINAL (`producer_vanished`) though the process was alive and still polling; and a decision arriving later resumed a run into a cycle marked finished. Nothing raised at any step.
>
> Three fixes, each at its own root: the gate rides the ONE shared heartbeat as its fourth caller (`heartbeat.py` now states the rule — *an await that outlasts `RUN_FRESH_S` and writes nothing MUST heartbeat*, because silence is how this package says "dead"); `derive_run_phase` reads the declared `gate`, still gated on freshness so a genuinely-dead gated cycle stays reapable; and `mark_producer_vanished` refuses a gated cycle, alongside its existing pause/check-in invariants. Dock ordering now sorts by **what needs you** (gate → running → paused), not by what is busy, and the single-unit button carries its phase class — one live unit is the common case, and it used to render a held gate identically to a healthy run.

**Standing lesson from this arc (three bugs, one shape):** each was a *state the system could enter but not report* — `gate` declared-but-underivable, `.inner` written-but-unreclaimable, the active campaign selected-but-undeletable. All three were downstream of a predicate that conflated two facts, and all three were invisible because the failure mode was silence rather than an error. When adding a state, ask what derives it, not just what writes it.

## Pointers

- **Architecture:** [`docs/architecture.md`](docs/architecture.md) §0/§0.5 — backbone primitives, five I/O kinds, the central loop + L1/L2/L3 escalation + L4-is-recursion, searchpoints, scoring, identity, token/cost ledger. **Extend primitives in place** — the wrong shape is meant to be hard to express, not policed by a test.
- **Related work / competitive landscape:** [`docs/research/related-work.md`](docs/research/related-work.md) — the nine umbrella systems (AlphaEvolve · OpenEvolve · AlgoTuner · autoresearch/Karpathy · PromptWizard · SkillOpt · MIPROv2 · GEPA · PromptPotter), the classical algorithm-configuration lineage, the MCTS mapping, and the adjacent harness-/weight-tuning agents (LangChain's Nemotron harness playbook, NVIDIA AutoResearch, Palantir AIP Evolve). Read before positioning against a "competitor".
- **Roadmap:** three ways to run it — **(1)** hosted beta (`app.promptpotter.dev`), **(2)** local `/potter-run` (the local-secure setup), **(3)** the self-hosted **team-online** stack ([`deploy-linux/`](deploy-linux/); Cloudflare tunnel + OIDC allowlist + quotas). **Our setting is the cloud team-online deployment (tier 3), NOT the local-secure one (tier 2)** — treat cloud/team as the default operating context in everything we build and reason about; tiers 1 & 3 share this stack. Engine + webapp + control plane + chat (Arc 1) + ingest shipped; Lane A (BYO keys) + Lane C (chat write-path, L4, composite fitness) in flight → [`docs/specs/roadmap.md`](docs/specs/roadmap.md).
- **Persistence:** [`docs/operations/persistence-and-state.md`](docs/operations/persistence-and-state.md) — four-entity tree (Workspace → Dataset → Campaign → Cycle), `.promptpotter/` layout, `measurements/`, fork lineage, recovery.
- **Per-layer contracts** (load only the layer you touch): [`promptpotter/CLAUDE.md`](promptpotter/CLAUDE.md) (index) · [`application/CLAUDE.md`](promptpotter/application/CLAUDE.md) (orchestration + backend-overlay merge / never-edit-backend rule) · [`application/optimization/CLAUDE.md`](promptpotter/application/optimization/CLAUDE.md) (L1/L2/L3 agent contracts + L4 recursion + dispatch) · [`domain/CLAUDE.md`](promptpotter/domain/CLAUDE.md) · [`infrastructure/CLAUDE.md`](promptpotter/infrastructure/CLAUDE.md) (ledger + projections + stores) · [`presentation/CLAUDE.md`](promptpotter/presentation/CLAUDE.md) · [`connectors/CLAUDE.md`](promptpotter/connectors/CLAUDE.md).
- **Contracts:** ADRs [`0001`](docs/adr/0001-m12-control-plane.md) (control plane) · [`0002`](docs/adr/0002-identity-foundation.md) (identity) · [`0003`](docs/adr/0003-spend-and-tenancy.md) (spend/tenancy). Index map: [`docs/CLAUDE.md`](docs/CLAUDE.md).
- **Design surface:** [`BRAND.md`](BRAND.md) (visual identity — theme-is-audience, dark=operator, light=buyer) + [`VOICE.md`](VOICE.md) (copy register — the Potter is force-multiplier, not friendly-wizard).
- **Skills:** [`.claude/skills/`](.claude/skills/) — `potter-run` (launch + supervise a campaign)
