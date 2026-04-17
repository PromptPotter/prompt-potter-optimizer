# M9: Stable Config, Hierarchy Refactor, Multi-Dataset/Pipeline, File-Directory UI v0

**Version:** 0.1.0
**Date:** 2026-04-12
**Status:** Planned
**Depends on:** M8 Campaign Intelligence (Complete)

---

## Context

M9 is foundation work. The optimization loop (L1/L2/L3) is functionally complete through M8 with SearchMemory cross-campaign intelligence, but four gaps block publication and production:

1. **Meta-prompts are proof-of-concept.** `promptpotter/config/optimizer_prompts/` are functional but untuned. They were developed against a multi-node retrieval pipeline and need systematic evaluation before any benchmark number is meaningful.
2. **Flat service layout.** `promptpotter/services/` mixes orchestration with I/O and has files up to 37KB. A multi-tenant webapp lands on top of this as duplication or leakage.
3. **Single dataset/pipeline assumption.** Nothing in store paths or campaign state cleanly distinguishes HotPotQA from GSM8K from TermNorm running in the same project.
4. **No shared view model across entry points.** Notebook renders from in-memory state. CLI dashboard polls live state. The future webapp would be a third independent renderer. Artifact-write parity is closed (`run_optimization` auto-mints a session when the caller passes `session_id=""`, so notebook/smoke/future-API produce the same five `CAMPAIGN_SESSION_ARTIFACTS` as CLI `init`); **view-model unification remains** — Track 4 below.

M9 delivers the foundation. M10 populates it with benchmark results. M11 generalizes the connector.

## Tracks

### Track 1: Stable Optimizer Configuration

**Problem:** Meta-prompts in `promptpotter/config/optimizer_prompts/` are functional but proof-of-concept:

| Prompt | File | Temperature | Max Tokens | State |
|--------|------|-------------|------------|-------|
| L1 Generate | `meta_scan_aware.json` | 0.7 | 8192 | Working, tuned for multi-node pipeline references |
| Critique | `critique.json` | 0.3 | 4096 | Working, extensive stat assembly |
| Critique (negative) | `critique_negative.json` | 0.3 | 4096 | Fallback for low accuracy |
| L2 Refine | `l2_refine_strategy.json` | 0.3 | 2048 | Working, clean layer transition |
| L3 Replan | `l3_modify_plan.json` | 0.5 | 2048 | Working, strategic pivots |

Current optimizer model: `openai/gpt-oss-120b` via Groq.

**Approach:** Generic prompts adapt via `task_context` injection — no task-specific sets. The `problem_description` and `instruction` fields use template variables; task details flow through `task_context`.

**Deliverables:**

1. **Evaluation protocol.** Second-order metrics measured at campaign level:

   | Metric | What It Measures | Better = |
   |--------|-----------------|----------|
   | Rounds to convergence | How quickly optimizer finds a good prompt | Lower |
   | Final accuracy | Best accuracy achieved | Higher |
   | L2/L3 escalation frequency | How often L1 stalls and needs meta-intervention | Lower |
   | Candidate diversity | Variety of generated candidates per round | Higher (avoids mode collapse) |
   | Optimizer cost | Total tokens spent on optimizer LLM calls | Lower |

2. **Systematic improvements.** Prompt language refinement, temperature/max_tokens tuning per node, `thinking_style` variants, `answer_format` schema variations, model selection.
3. **Final configs committed** to `promptpotter/config/optimizer_prompts/` with rationale. Feeds paper's "method" section.

**Bootstrap cost mitigation:** Tune meta-prompts against **BBEH mini** (10/task train subset, seed=42 — same split as M10's head-to-head). Small sample, diverse reasoning tasks, known non-saturated at `gpt-oss-120b`. Reserve the full 3-seed protocol for M10's publication numbers. GSM8K and AIME are saturated at this model and are not useful signal for meta-prompt tuning; HotPotQA's saturation is unknown and decided in M10 Wave 1.

**Risk:** Multi-node meta-prompts on LLM-only tasks — pipeline references are irrelevant for benchmarks. Mitigation: generic prompts via `task_context` injection.

### Track 2: Hierarchy Refactor (Hexagonal Layout)

See standalone spec: [`m9-hierarchy-refactor.md`](m9-hierarchy-refactor.md).

Shape `promptpotter/` into `domain / application / infrastructure / presentation / shared / config`. Move-only; fat-file splits deferred to follow-up specs. Tenant seam shaped (`domain/tenant.py` + optional `SessionEnv.tenant`) but not enforced.

### Track 3: Multi-Dataset / Multi-Pipeline Support

**Problem:** A project today implicitly assumes one dataset and one pipeline per backend. Multi-dataset benchmark work (HotPotQA + GSM8K + TermNorm sharing a project) needs dataset/pipeline to be first-class identifiers in campaign state, store paths, and the active-session pointer.

**Deliverables:**

1. `dataset_name` and `pipeline_name` become required fields on campaign state and session env, propagated through `SessionEnv`.
2. Store paths extend to `{backend_id}/{dataset}/{pipeline}/campaigns/{cycle_id}/...`. Legacy paths migrate or coexist (decision open).
3. `active_session.json` carries `{backend_id, dataset_name, pipeline_name, session_id}`.
4. CLI commands accept `--dataset` and `--pipeline` overrides; default comes from the active session.
5. Two datasets demonstrably coexist (`datasets/lca-termnorm/` + one benchmark dataset) in a single project store without collision.

**Open decisions during the track:** migration vs coexistence for legacy data, how `show-status` aggregates across datasets, whether `recon_variants` libraries are per-dataset or shared.

### Track 4: File-Directory UI v0 (Webapp Preparation)

**Problem:** Three entry points (notebook, CLI, FastAPI) and a fourth coming (webapp). Notebook renders from in-memory state; CLI dashboard polls live state; webapp would be a third independent renderer. No shared view model. (Artifact-write parity is closed — `run_optimization` auto-mints a session when `session_id=""`, and the recon-path auto-mint is the next follow-up. Track 4 is about renderer unification only.)

**Approach:** Instead of each entry point building its own render pipeline, the session writes a flat file-directory "view model" to disk. The CLI, the notebook, and the eventual webapp all read from the same files. The first cut mirrors exactly what the Jupyter notebook already displays — vanilla, no new information surfaces. Think: what a human sees when they `cd` into the session folder and `cat` a few files.

**Deliverables:**

1. A file-directory view model under `sessions/{session_id}/views/` (exact path open). Content is a superset of what the notebook currently displays: round summary, candidate leaderboard, current trajectory, critique text, active SearchPoint.
2. Format TBD during the track — likely a mix of small JSON files for structured data and pre-rendered Markdown snippets for human-readable dashboards. Open: temp vs permanent files, rolling vs append-only.
3. CLI `show-status` becomes a thin renderer that reads the view directory and pretty-prints. No live-state polling.
4. Notebook output becomes a thin renderer that reads the view directory. This closes the remaining notebook ↔ CLI parity gap (renderer divergence); artifact-write parity is already closed via the `run_optimization` auto-mint.
5. Documented "this is what the future webapp reads" contract. M10 Track 3 picks up from here.

**Intentionally open:** exact file layout, whether intermediate/temp views exist alongside permanent ones, how to version the view schema. Decided during the track via agile iteration — write the files, look at them, adjust.

**Non-goal:** pretty HTML, React, or any JS. That's M10/M11.

### Track 5: CLI Unification — Collapse `init` + `optimize`, Unify Seed Sources

**Problem:** `init` and `optimize` are two CLI verbs for what is conceptually one workflow. Nobody runs `init` alone — it sets up a session and sits there; `optimize` is always the next command. The split is an implementation artifact (session creation vs loop execution), not a user-facing distinction.

On top of that, there are already at least four ways a cycle can start, and only one has a flag:

- Fresh baseline from `datasets/{name}/prompts/` (implicit, default)
- Resume from last checkpoint in the active session (implicit, `optimize` with no args)
- Fork from an events.jsonl write-point (`optimize --from <cycle>:<ref>` — landed in the interim between M8 and M9, see `docs/architecture/optimization.md § Forking a campaign`)
- Recon-brief-seeded start (implicit, lives in session state)

All four are "where does the baseline `OptSearchPoint` come from?" but they're scattered across different flags and implicit behaviors. The interim `optimize --from` change is a strict improvement over `init --fork-from`, but it's not the long-term shape — it normalizes the fork case while leaving the other three as-is.

**Why now (M9, not earlier):** Doing this as a standalone change would thrash the notebook UI layer, the API routers, and the active-session-pointer semantics for a gain that's mostly aesthetic. M9's stable-config / hierarchy / file-directory UI refactor is already touching all of these surfaces — Track 5 is cheap when it rides on top of Tracks 2 + 4, and expensive if it lands on its own.

**Deliverables:**

1. **Single loop verb.** Collapse `init` + `optimize` into one command. Working name: `run` (or keep `optimize` and remove `init` as a standalone verb — decided during the track). Creates the session if needed, then runs the loop. The three-command invocation `init → set-task → optimize` collapses to one (with `set-task` staying as an orthogonal concern, optionally merged via flag).
2. **Unified `--from` / `--seed` argument** with a typed vocabulary covering all four starting conditions:
   - `--from fresh` (default) — load baseline prompt from `datasets/{name}/prompts/`
   - `--from resume` — last checkpoint of the active session
   - `--from cycle:<cycle_id>:<event_ref>` — fork from events.jsonl write-point (current `optimize --from` behavior)
   - `--from recon:<recon_id>` — recon-brief-seeded (currently implicit from session state)
   One concept, one knob, discoverable in `--help`.
3. **First-class lineage model.** Today `parent_cycle_id` / `parent_event_ref` / `fork_from` are stamped as untyped strings into `campaign_config` so they ride along to `CampaignStart`. Promote to a typed `Lineage` model on the cycle record (or on `OptSearchPoint`) so "show me the fork tree" is a structured query, not a config-blob walk.
4. **Notebook + API parity.** The notebook's `run_optimization_notebook()` and FastAPI's `/api/v1/campaigns` routes both need to accept the unified seed vocabulary. This is the part that would thrash the other entry points if done in isolation — M9 Track 4's shared view model and Track 2's hexagonal layout make it tractable.
5. **Migration of interim `optimize --from`.** The `optimize --from cycle:<...>` flag added in the interim is already the right mechanism — this track just generalizes it to the other three seed sources and moves it onto the unified verb. No user-visible breakage beyond the verb rename.

**Sequencing:** Runs in Wave 3 or later, after Track 2 (hexagonal layout) and Track 4 (file-directory UI v0) are in place. Depends on the active-session pointer semantics being stable, which Track 4 clarifies.

**Non-goal:** reshaping what the loop itself does. This is entirely a CLI / entry-point / lineage-model refactor — the L1→L2→L3 mechanics are untouched.

---

## Wave Sequencing

```
Wave 1: Track 2 (hierarchy refactor, move-only)
        — foundation; other tracks are easier once the layout is right

Wave 2: Track 3 (multi-dataset/pipeline) + Track 1 (meta-prompt eval protocol)
        — parallel; multi-dataset is prerequisite for meta-prompt evaluation on 2+ tasks

Wave 3: Track 4 (file-directory UI v0) + Track 1 (systematic improvements + final configs)
        — parallel; UI draft happens in the new presentation/ui/ location

Wave 4: Track 5 (CLI unification — collapse init+optimize, unify seed sources)
        — runs last; depends on Track 2 (hexagonal layout) and Track 4
        (stable active-session-pointer semantics)
```

## Entry Criteria

- M8 exit gate passed ✅
- All existing tests pass

## Exit Criteria

- [ ] Stable meta-prompts documented with rationale, committed to `promptpotter/config/optimizer_prompts/`
- [ ] Hexagonal layout in place; all tests green; no `from promptpotter.services` imports remain
- [ ] `TenantContext` importable from `promptpotter.domain.tenant`; `SessionEnv.tenant` exists
- [ ] Multi-dataset/pipeline working on at least two datasets in a single project store
- [ ] File-directory UI v0 readable by a human browsing the session folder; CLI `show-status` and notebook both render from it
- [ ] Single loop verb (`init` + `optimize` collapsed); unified `--from {fresh,resume,cycle:<ref>,recon:<id>}` seed vocabulary; typed `Lineage` model replacing stringly-typed `parent_cycle_id` / `fork_from` in `campaign_config`; notebook + API accept the same vocabulary
- [ ] `CLAUDE.md` Architecture section updated to reflect new hierarchy

## Key Existing Code

| Area | Files |
|------|-------|
| Meta-prompts | `promptpotter/config/optimizer_prompts/*.json` |
| Optimizer pipeline | `promptpotter/services/optimizer/pipeline.py`, `optimizer_pipeline.json` |
| LLM client | `promptpotter/services/llm_client.py` |
| Scoring | `promptpotter/shared/scoring.py` |
| Dataset builder | `promptpotter/services/dataset_builder.py` |
| Dataset store | `promptpotter/services/store/dataset_run_store.py` |
| Session store | `promptpotter/services/store/session_store.py` |
| CLI dashboard | `promptpotter/cli/campaign_runner.py` (show-status) |
| Notebook | `notebooks/optimization_campaign.ipynb` |
| UI layer | `promptpotter/ui/campaign/` |

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Meta-prompt bootstrap cost | 15K+ LLM calls per variant at full size | Tune on BBEH mini (10/task train, 230 samples); full 3-seed protocol deferred to M10 |
| Multi-node meta-prompts on LLM-only tasks | Pipeline references irrelevant for benchmarks | Generic prompts via `task_context` injection |
| Hierarchy refactor touches every file | High churn, merge risk | Move-only (no splits). Single-commit-per-step. Tree compiles between steps |
| View model over-design | Architectural astronautics before there's a real consumer | Agile: write files, look at them, adjust. Mirror notebook exactly in v0 |
| Multi-dataset path migration | Legacy data becomes inaccessible | Decide migration vs coexistence early in the track; document the rule |
