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
4. **No shared view model across entry points.** Notebook renders from in-memory state. CLI dashboard polls live state. The future webapp would be a third independent renderer. Notebook ↔ CLI parity is a known gap.

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

**Problem:** Three entry points (notebook, CLI, FastAPI) and a fourth coming (webapp). Notebook renders from in-memory state; CLI dashboard polls live state; webapp would be a third independent renderer. No shared view model.

**Approach:** Instead of each entry point building its own render pipeline, the session writes a flat file-directory "view model" to disk. The CLI, the notebook, and the eventual webapp all read from the same files. The first cut mirrors exactly what the Jupyter notebook already displays — vanilla, no new information surfaces. Think: what a human sees when they `cd` into the session folder and `cat` a few files.

**Deliverables:**

1. A file-directory view model under `sessions/{session_id}/views/` (exact path open). Content is a superset of what the notebook currently displays: round summary, candidate leaderboard, current trajectory, critique text, active SearchPoint.
2. Format TBD during the track — likely a mix of small JSON files for structured data and pre-rendered Markdown snippets for human-readable dashboards. Open: temp vs permanent files, rolling vs append-only.
3. CLI `show-status` becomes a thin renderer that reads the view directory and pretty-prints. No live-state polling.
4. Notebook output becomes a thin renderer that reads the view directory. This closes the notebook ↔ CLI parity gap.
5. Documented "this is what the future webapp reads" contract. M10 Track 3 picks up from here.

**Intentionally open:** exact file layout, whether intermediate/temp views exist alongside permanent ones, how to version the view schema. Decided during the track via agile iteration — write the files, look at them, adjust.

**Non-goal:** pretty HTML, React, or any JS. That's M10/M11.

---

## Wave Sequencing

```
Wave 1: Track 2 (hierarchy refactor, move-only)
        — foundation; other tracks are easier once the layout is right

Wave 2: Track 3 (multi-dataset/pipeline) + Track 1 (meta-prompt eval protocol)
        — parallel; multi-dataset is prerequisite for meta-prompt evaluation on 2+ tasks

Wave 3: Track 4 (file-directory UI v0) + Track 1 (systematic improvements + final configs)
        — parallel; UI draft happens in the new presentation/ui/ location
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
