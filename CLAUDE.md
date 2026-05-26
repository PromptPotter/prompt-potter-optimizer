# CLAUDE.md

> Read [`docs/architecture.md`](docs/architecture.md) first — §0 + §0.5 are what every PR measures against.
> Per-layer CLAUDE.md tree listed under **Pointers** below.

## What this is

PromptPotter is **LLM-driven program evolution** for prompts and pipeline params. The backend declares tunable params via `GET /pipeline`; the optimizer runs critique-guided generate→score→critique with PoBB elimination (ε=0.05, n_min=4), cross-run memory, and self-healing rails. Python 3.13+, hexagonal. **Orchestration is the product — backends are pluggable and read-only.** Node tunables ride a per-call overlay (`datasets/{name}/pipeline.json::nodes.{name}.config`); we never edit a backend's static config, even one we own. TermNorm is the only registered connector today (`promptpotter/connectors/termnorm.py` — bundles wire adapter, session lifecycle, and experiment-data extraction under one `Connector` shape); BBEH is the headline benchmark.

The user is the operator. The project file tree IS the dashboard, plus a **read-only operator dashboard** at `/ui` (Next.js project at `webapp/`, static export at `webapp/out/`) that polls the active cycle's `dashboard.json` every 2 s — used in concert with the file tree, not in place of it. Full M12 webapp (control plane, monitoring, multi-cycle) is the headline milestone. Onboarding: install → restart VS Code → `/potter-run` (downloads TermNorm, starts its `.bat`, converts datasets, prompts for API keys).

## STOP — read this before writing any code

**No backward compatibility, ever.** Zero released versions, zero stale on-disk data. Nothing to be compatible with. **This is the rule that gets ignored most often.**

**Delete on sight — don't ask, don't TODO, don't "remove later":**
- **Shim code** mapping old ↔ new: re-export aliases, `try/except ImportError` for renamed modules, methods/properties that only forward to new names, no-op stubs "kept for X".
- **Fallback chains** over renamed keys/fields: `dict.get(new, dict.get(old, …))`, `getattr(obj, "new", getattr(obj, "old", …))`, `dict.get("new") or dict.get("old", …)`.
- **Breadcrumb comments**: `// removed`, `# old name`, `# kept for parity / callers`, `# legacy dict|format|payload`, `(formerly module.x)`.
- **Future-tense docstrings**: "Phase 2/3 will replace this", "kept for X". Document current state, not half-done plans.

**Changing a contract:** rename, restructure, delete the old test, write the new one. No compat test, no deprecation warning, no shim, no fallback default. Found a shim someone else wrote? Delete it in the same PR you noticed it.

The word `legacy` in a comment or docstring is a code smell — either the path is dead (delete it) or the word is wrong (delete the word). The only sanctioned uses of `deprecated` are the fatal-warning sample lifecycle (`is_deprecated`, `deprecated_samples`, `retry_of_deprecated_cache`, `RoundResult.deprecated`).

## Backbone

**Backbone primitives — see [`docs/architecture.md` §0.5](docs/architecture.md).** An inline table here has drifted before; that section is the source of truth. Extend primitives in place; if you genuinely need to change a shape, change the primitive itself. The wrong shape is meant to be hard to express, not policed by a test.

## Commands

```bash
pip install -e ".[all,dev]"
ruff check . && ruff format --check . && deptry . && mypy promptpotter/ && pytest -q   # CI runs same chain
git config core.hooksPath .githooks                           # one-time per clone: enables .githooks/pre-commit (ruff format + check on staged .py)
python -m promptpotter new <name>                            # fresh: mint session+cycle from datasets/<name>/, run from round 0
python -m promptpotter resume                                # resume active; Ctrl+C: 1st saves, 2nd force-quits
python -m promptpotter resume --from N                       # resume: rewind in place
python -m promptpotter resume --fork-on-divergence           # resume: sibling cycle at divergence point
python -m uvicorn promptpotter.main:app --port 8001          # read-only API + /ui webapp preview
```

`new` and `resume` are the two write verbs. `new <name>` mints a fresh Campaign every call — `campaign_id = {dataset}__{rand6_hex}`, distinct per invocation — plus its root cycle, running from round 0. The declaration (target hash + optimizer-prompt hash) is recorded as properties on `campaign.json` for drift detection on resume, not as the id. Two `new` calls on an unchanged declaration share their root cycle id (`cycle_<target_hash>` is content-addressed) and origin score (the dataset-scoped archive cache-hits every sample), but get independent `campaign_id`s and diverge from round 1 onward. `resume` (bare) reads `active_session.json::{campaign_id, cycle_id}` and picks up that cycle.

Webapp preview at `http://localhost:8001/ui/` once uvicorn is running; reload after a fresh `new` (page reads `active_session.json` on load). `dashboard.json` only refreshes while `python -m promptpotter resume` is running in another terminal.

`.env` with `GROQ_API_KEY` (or OPENAI/ANTHROPIC/OPENROUTER) required. Provider is per-campaign in `campaign.json::optimizer_llm.provider`.

**Before any commit:** run `python -m ruff format promptpotter/ tests/ && python -m ruff check promptpotter/ tests/`. CI fails on format drift, not just lint errors — the `.githooks/pre-commit` hook enforces this once `git config core.hooksPath .githooks` has been set in the clone, but a fresh clone or session without it is still on the hook for it.

## Architecture

- **Hexagonal.** `domain/` (pure) → `application/` (use cases) → `infrastructure/` (I/O) → `presentation/` (adapters), plus leaf `shared/`, `config/`. **Strict:** `application/intelligence/` MUST NOT import from `application/optimization/` (`tests/test_invariants.py`).
- **Three-layer loop + recursive L4.** L1 generates every round; L2 fires on L1 stall (refines `task_context`, never `pipeline_params`); L3 fires on L2 stall (replans). **L4 is recursion, not a 4th layer** — the same PromptPotter applied to itself via the `promptpotter` connector (`promptpotter/connectors/promptpotter.py`), mutating an inner cycle's meta-prompt templates as `pipeline_params`. Conceptually L2 / L3 / L4 are the **same family** — each mutates a slower-changing surface of the level below (L2 → `task_context`; L3 → `plan`; L4 → meta-prompt templates) — but structurally L4 is another cycle, NOT a new `LayerStrategy` driver inside `application/optimization/`. There is no `l4_*.py` and there will not be one. All four optimizer LLM calls go through `build_bundle → DispatchHub.fill_l1|fill_fixed → compile_prompt → LLM`. **No prompt site summarizes its own data.** Agent contracts: [`promptpotter/application/optimization/CLAUDE.md`](promptpotter/application/optimization/CLAUDE.md). L4 spec: [`docs/specs/m12-multi-connector.md#track-15--promptpotter-as-connector`](docs/specs/m12-multi-connector.md); concept: [`docs/concepts/optimizer-of-the-optimizer.md`](docs/concepts/optimizer-of-the-optimizer.md); dataset: [`datasets/promptpotter-self/`](datasets/promptpotter-self/). Signal routing + healing + four wound channels: [`docs/developer/dispatch-hub.md`](docs/developer/dispatch-hub.md).
- **Four I/O kinds (invariant).** **Persistence** — sole ingress `CycleEventLog.append`; `RunCallbacks` is the writer API; ledger-subscribed projections own everything else. **Display** — ledger subscribers; allowlist-enforced. **Control-local** — `stop_check` on `Session` + sanctioned `.runtime/stop.flag`. **Control-remote** — HTTP commands ride the canonical ledger as `CommandRecord` via sole `CommandDispatcher` (kwargs-only `emit_command`); the runner acks via `CommandAckRecord` (sole `RunnerCommandSubscriber`); outbound `ProjectionEnvelope` frames fan out over SSE via sole `EventStreamView`. Closed inbound set: `docs/specs/m12-api-openapi.yaml`. Closed outbound set: `docs/specs/m12-events-asyncapi.yaml`. Permanent contract: [`docs/adr/0001-m12-control-plane.md`](docs/adr/0001-m12-control-plane.md). Forks ride Persistence via `inherit_from(parent, offset)`. **Entry points MUST NOT write campaign artifacts directly.** Layer contract: [`promptpotter/infrastructure/CLAUDE.md`](promptpotter/infrastructure/CLAUDE.md).
- **Searchpoints.** `JobSearchPoint` (frozen target spec, content-hashed) + `PromptTemplate` (8-field) → `OptSearchPoint` (optimizer state + lineage + memory). **New optimizer state MUST flow through `OptSearchPoint`** — no sidecar state. Twin tracing: target → `archive/measurements/`; optimizer → `.runtime/cache/rounds/`.
- **Pipeline params + scoring.** `pipeline_params` is node-keyed config dicts plus a reserved `steps` list — no flat format, no `override_map`. `PipelineSchema` built entirely from `GET /pipeline` — zero backend constants. `score_search_point()` is the single scoring gateway. Each trace carries `{scorer_id: {score, hit, formula}}`; resume halts on first decision divergence; `--fork-on-divergence` mints a sibling rooted there. Hot-swap composite via `campaigns/{campaign_id}/cycles/{cycle_id}/scoring_steer.json`.
- **Cycle + campaign identity, scoring-set mutations.** Cycle id = `cycle_{target_hash[:12]}` from origin `JobSearchPoint.content_hash(dataset)` (rendered target prompt + dataset + target `pipeline_params`) — content-addressed. Campaign id = `{dataset}__{rand6_hex}` — minted fresh per `new` call, not derived from declaration content. `campaign.json` records `root_content_hash` (target) + `optimizer_prompt_hash` as drift-detection properties; resume recomputes both and warns. Path resolution is `(campaign_id, cycle_id)`. The scoring bank is the full train split; the zero-signal filter (off by default) is the sole round-boundary writer that mutates it (`datasets/{name}.json::excluded`). The per-round eval subset is a round-local read-and-select — the adaptive queue mechanism (`select_round_subset`) narrows the bank to `sp_budget_ttest` samples each round — not a writer of the bank.
- **Identity seam (Stage 0 shipped).** `IdentityContext` (`shared/identity.py`) is the sole carrier past the resolver; `TenantId`/`UserId`/`Issuer`/`SafeName` newtypes live in `domain/identity.py`. `build_stores(identity, *, projects_root=…, datasets_root=…)` is the only `Stores` construction route; `Session.identity` replaces the deleted `TenantContext`; FastAPI seam is `resolve_identity` → `IdentityDep` → `StoreDep` (`presentation/api/deps.py`). Stage 1 (M12 OIDC client) only flips `resolve_identity` — no other call site changes. Full spec: [`docs/specs/identity-foundation.md`](docs/specs/identity-foundation.md), first consumer [`docs/specs/spend-and-tenancy.md`](docs/specs/spend-and-tenancy.md). The Stage-1 Identity I/O kind amends §0 first per Pre-flight gate Q4 sub-rule below.
- **Token/cost on the canonical ledger.** Every LLM call — optimizer (`dispatch/llm_call/call.py`) and backend (`scoring/sample_measurement.py`, per-step over `step_tokens` on uncached samples) — emits one `TokenUsageRecord` through `emit_token_usage(**kwargs)` (`infrastructure/llm/models.py`), which reads the active cycle ledger + round from the `_CYCLE_LEDGER`/`_CURRENT_ROUND` ContextVars and appends to the same `events.jsonl` that carries `PhaseRecord`/`SnapshotRecord`/`LLMCallRecord`. No process global, no wrapper dataclass, no parallel pipeline — tokens ride the canonical Persistence stream alongside every other record. `LiveDashboardView._handle_token_usage` is the **sole writer** of `dashboard.json::spend` (backend + loop buckets routed by `record.kind`); the halt loop reads `LiveDashboardView.spend_total_used_usd` (clean accessor, no probe into `state['spend']`). `AuditTrailView` continues to record the same `TokenUsageRecord`s into `round_NNNN.json` for the audit trail. New per-call telemetry follows the same shape: define a `*Record` in `domain/run_records.py`, add an `emit_*` helper that reads the ContextVar and appends, register the projection subscriber. Full spec: [`docs/specs/spend-and-tenancy.md`](docs/specs/spend-and-tenancy.md).

## Persistence

Four entities, outermost → innermost: **Workspace** (`projects/{tenant}/` — tenant datastore) → **Dataset** (`datasets/{name}/`) → **Campaign** (first-class — `campaigns/{campaign_id}/`, `campaign_id = {dataset}__{rand6_hex}`, fresh per `new` call) → **Cycle** (root | fork | diag | sweep, `cycle_{hash[:12]}`). Each campaign holds one **session** — the `new` invocation that minted it; `resume` extends that session, `resume --fork-on-divergence` adds sibling cycles. The session id is the existing `session_id`. `.promptpotter/` holds two trees: `sessions/{session_id}/` (operator workspace) and `campaigns/{campaign_id}/`. The campaign dir holds `campaign.json` (manifest), `log.md` (digest covering the session + forks + rounds), `hard_samples.json` — with every cycle (root + forks + diags + sweeps) flat under `cycles/{cycle_id}/`; sibling kind + sweep batch id are `index.json` metadata, never directory nesting. `dashboard.json` lives in the session-family root cycle dir (`cycles/{session_root}/dashboard.json`). Per-cycle audit (`index.json`, `log.md`, `rounds/`, `langfuse/`, `prompts/`) at each cycle's top level; runtime internals (ledger, cache, P(best) streams) under `.runtime/`. `archive/measurements/` is the MeasurementArchive — DB core, peer of `campaigns/`, dataset-scoped + queryable at three scopes (campaign / dataset / workspace); cross-campaign evidence pooling on the same declaration rides this layer, not the campaign-id. Pre-existing campaigns minted under the prior content-addressed scheme (multi-session forest with `_s{N}` cycle roots) remain readable in place. **Reads happen by opening files** — no read CLI. Full tree, fork lineage, and recovery workflows: [`docs/operations/persistence-and-state.md`](docs/operations/persistence-and-state.md). Layer contracts: [`promptpotter/infrastructure/CLAUDE.md`](promptpotter/infrastructure/CLAUDE.md).

## Per-dataset configuration

`datasets/{name}/`: `pipeline.json`, `campaign.json`, `task_description.md`, `prompts/{node}.json`, `dataset.md`. **Configs are the source of truth** — no parallel default ladders. Backend overlay (`nodes.{name}.config` in `pipeline.json`) is the sole route for backend tunable changes — model, provider, temperature, anything in `optimizer.param_keys`. Merge contract + the "never edit backend repo, even co-owned" rule: [`promptpotter/application/CLAUDE.md`](promptpotter/application/CLAUDE.md).

**Dataset reference points** — consult on every dataset question, not just when wiring a new one. Source of truth for wire/reject rationale, projection-bias findings, per-dataset model defaults.

- Adding a dataset / canonical splits → [`docs/operations/adding-a-dataset.md`](docs/operations/adding-a-dataset.md). Research the canonical split; never invent one.
- Why X is/isn't wired, trialed-and-rejected list, projection-bias findings → [`docs/operations/dataset-selection-rationale.md`](docs/operations/dataset-selection-rationale.md). Check first when asked "why didn't we use Y?" or "have we trialed Z?".
- Per-dataset model + `reasoning_effort` + `max_tokens` defaults, BBEH output-ceiling traps, Groq daily-volume swap protocol → [`docs/operations/dataset-reasoning-matrix.md`](docs/operations/dataset-reasoning-matrix.md).

## Conventions (non-derivable)

Non-negotiables only — full style, code-shape, tests, CLI, git rules in [`docs/developer/conventions.md`](docs/developer/conventions.md). No-backward-compat is its own section above.

- **`eval` banned from identifiers and prose.** Exception: the `Evaluator` class + direct registry consumers (`evaluators` field, `all_evaluators()`, `materialize_*_values`). Use loop / round / searchpoint / sample / measurement / scoring / fitness / trial / critique. Domain vocabulary: evolve, generation, population, mutation, selection, individual.
- **Vocabulary.** A dataset row is a **sample**; its input-string field is `query` (parallel naming: `Sample.query`, `BackendResult.query`, `QueryMeasurement.query`). Use `query` *only* as a field name or for genuine retrieval / TermNorm wire — never as a synonym for "sample". Use `sample` for everything that aggregates over rows (`n_samples`, `per_sample`, `SampleProfile/Difficulty/Record`, …). **Never say "query ranking"** — pick `posterior elimination` (PoBB), `Rasch sort`, or the backend's `llm_ranking` node. The umbrella for "how query budget is spent across N candidates per round" is `candidate budget allocation`. `candidate` = a prompt SearchPoint variant; retrieval-list items are `ranked_items`. `meta-prompt` = the L1/L2/L3/Critique LLM template (= "optimizer prompt"). Full enumerations + canonical file pointers: [`docs/glossary.md`](docs/glossary.md).

## Known issues

- **TermNorm backend** lives in a sibling repo (`TermNorm-excel/backend-api`); clone alongside PromptPotter. Co-owned by this project's maintainer — cross-repo edits authorized; coordinate explicitly.
- **`llm_ranking` broken — always set `"exclude_nodes": ["llm_ranking"]`** (`json_validate_failed` ~50% of queries). Effective pipeline: `cache_lookup → fuzzy_matching → web_search → entity_profiling → token_matching`.

## Roadmap

**M12 is the headline** — multi-connector, competitor head-to-head, webapp Phase 2. **M10 active** — prompt-iteration framework + L1-generate tuning; **targeting** ≥95% in ≤5 rounds (cleanup arc closed pass-2; framework + benchmark hit are the open M10 half). **M11** — BBEH benchmarks, ablation, webapp read-only (Slice 1 vanilla shipped + cut over to Next.js port — see `docs/specs/archive/m11-webapp-react-port.md`). M0–M9 complete. See [`docs/specs/roadmap.md`](docs/specs/roadmap.md).

## Pointers

**Per-layer contracts** (progressive disclosure — load only the layer you're touching): [`promptpotter/CLAUDE.md`](promptpotter/CLAUDE.md) (package index) · [`promptpotter/application/CLAUDE.md`](promptpotter/application/CLAUDE.md) (orchestration shape) · [`promptpotter/application/optimization/CLAUDE.md`](promptpotter/application/optimization/CLAUDE.md) (**L1/L2/L3 agent contracts + L4 recursion**) · [`promptpotter/domain/CLAUDE.md`](promptpotter/domain/CLAUDE.md) (frozen models) · [`promptpotter/infrastructure/CLAUDE.md`](promptpotter/infrastructure/CLAUDE.md) (ledger + projections + stores) · [`promptpotter/presentation/CLAUDE.md`](promptpotter/presentation/CLAUDE.md) (CLI + API + views) · [`promptpotter/connectors/CLAUDE.md`](promptpotter/connectors/CLAUDE.md) (backend hook bundles, incl. `promptpotter` self-connector).

**Index files** (AI-readable maps of subtrees): [`datasets/CLAUDE.md`](datasets/CLAUDE.md) (per-dataset layout + reference points + `promptpotter-self` L4 case) · [`docs/CLAUDE.md`](docs/CLAUDE.md) (docs-tree map + hot-question anchors).

**Topical docs:** `docs/manual/` install→first run→reading→troubleshooting · `docs/concepts/` how it works · `docs/operations/` CLI/env/persistence/rewind-and-fork/observability · `docs/developer/README.md` architecture brief (prompt structure, dispatch, scoring node, cross-run memory) · [`docs/developer/conventions.md`](docs/developer/conventions.md) full style + code-shape rules · [`docs/developer/stable-api.md`](docs/developer/stable-api.md) v1 fork-readiness surface · [`docs/glossary.md`](docs/glossary.md) domain vocabulary + canonical file pointers · `tests/CLAUDE.md` test charter.

**Design surface:** [`.impeccable.md`](.impeccable.md) — design context (theme-is-audience: dark=operator, light=buyer; the Potter is force-multiplier not friendly-wizard; accessibility is a positioning feature). Loaded by `frontend-design`, `critique`, `polish`, etc.

## Pre-flight gate

Before adding any new concept (class, projection, injection, prompt,
field, dict, file), the PR description answers these eight questions.
"I don't know" or "kind of" on any answer is a hard block.

1. **Which §0 bucket does this belong to?** (central loop /
   escalation / errors-heal / dispatch / state+persistence / on-disk
   / tracing / archive). If "none of them," stop — either §0 is
   incomplete (update it deliberately) or this is the wrong PR.
2. **Does an existing channel already do this?** Default answer:
   yes. Search before adding.
3. **Is the name distinct from every existing concept in the
   codebase?** Grep first. Two `signals` was avoidable; the next
   collision is too.
4. **Is the name self-describing without opening another file?** Read
   the name in isolation. If it could mean three different things
   (`Decision`, `Bundle`, `Signal`), rename now — naming is cheap, the
   alternative is every future reader paying for it.
   - **Sub-rule: new I/O kind?** §0 names four (Persistence, Display,
     Control-local, Control-remote). A genuinely new kind is an
     architecture-spec change — **amend §0 first**, then write the code.
     New Control-remote command or event kinds also need their schema
     declared in `docs/specs/m12-api-openapi.yaml` /
     `docs/specs/m12-events-asyncapi.yaml` *before* a handler lands.
5. **Can this ride existing infrastructure (ledger, INJECTIONS,
   `OptSearchPoint`, dispatch hub) without adding a sidecar?**
   Default: yes.
6. **Can the AI/operator read this fact from a file without running
   the CLI?** If the new code surfaces something material only via
   stdout, only via in-memory state, or only via "ask me to re-run
   with --verbose," it violates the AI-accessibility principle.
   Material facts land on disk in human-readable form.
7. **Does §0 (`docs/architecture.md`) need updating to mention
   this?** If yes, that's a separate PR landing first. Code that
   requires §0 to drift cannot land before §0 has been updated.
8. **Does this code emit a Langfuse-shape trace event for any new
   LLM call or backend match?** If yes, wrap the call site with
   `observed_node()`. New unwrapped LLM calls are an automatic block.
