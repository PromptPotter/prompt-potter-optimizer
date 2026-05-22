# M11: Publication Benchmarks, Ablation Studies, Webapp Read-Only

**Version:** 0.3.0
**Date:** 2026-04-28
**Status:** Planned
**Depends on:** M10 (Prompt-Iteration Framework + L1-generate Tuning), M9 (Hierarchy Refactor, Multi-Dataset/Pipeline, File-Directory UI v0, Config Aggregate)

---

## Context

M9 delivered the structural foundation: hexagonal layout, multi-dataset/pipeline support, and a file-directory view model that both CLI and notebook render from. M10 followed with a configured optimizer-prompt set tuned to converge in ≤5 rounds. M11 populates that foundation with the first publication-grade results and the first real webapp pass.

Three gaps M11 closes:

1. **Zero benchmark results.** The methodology exists (`docs/research/benchmarks.md`), the export pipeline is built. No PromptPotter campaigns have been run against the head-to-head infrastructure yet.
2. **Ablations are undone.** The paper's "which layer contributes what" story is unproven.
3. **No webapp.** The M9 file-directory view model is readable by humans but has no pixel UI. M11 puts the first pass on top.

### Benchmark Priority Pivot (2026-04-12)

Preliminary probing showed GSM8K and AIME 2025 are effectively **saturated** at `gpt-oss-120b` — the optimization loop has almost no headroom to demonstrate. They are deprioritized as primary targets. The new priority is:

1. **BBEH (Big-Bench Extra Hard)** — 23 diverse reasoning tasks, state-of-the-art reasoners still score ~54%, general-purpose ~24%. Ample headroom. Head-to-head infrastructure already exists at [`docs/research/bbeh-comparison/`](../research/bbeh-comparison/) — CAPO, GEPA, MIPROv2, and BootstrapFewShot notebooks run against the same `gpt-oss-120b` model and same 10/10 mini split, seed=42.
2. **HotPotQA** — multi-hop QA data point, pending saturation check. If it looks saturated on probe runs, demote to secondary-if-time-permits.
3. **GSM8K, AIME 2025** — secondary at best. Include in tables only if there is demonstrable headroom; otherwise cite existing literature numbers and move on.
4. **IFBench** — unreviewed candidate, instruction-following benchmark. Needs a scoping pass before committing: what it measures, whether it fits our loop (scorable ground truth), and whether it has headroom at `gpt-oss-120b`.

## Tracks

### Track 1: Publication Benchmarks (BBEH Primary, HotPotQA Second)

**Problem:** Result tables in `docs/research/benchmarks.md` are placeholders and the head-to-head infrastructure has not yet been executed with PromptPotter.

**Deliverables:**

1. **BBEH PromptPotter run** — run the full optimization loop (L1+L2+L3) on BBEH mini (10/task train, 10/task test, seed=42) with M9's stable meta-prompts, matching the exact protocol already baked into [`bbeh-comparison/`](../research/bbeh-comparison/). Macro-average across 23 tasks.
2. **BBEH head-to-head** — execute `bbeh_capo.ipynb` and `bbeh_dspy.ipynb` (GEPA + MIPROv2 + BootstrapFewShot) so all five methods have results on identical splits.
3. **HotPotQA saturation probe first** — before any full run, probe PromptPotter on HotPotQA with a small sample at `gpt-oss-120b`. If headroom exists, proceed with loader + scorer (`DATASET_LOADERS["hotpotqa"]`, `SCORING_FUNCTIONS["hotpotqa_f1"]`, config at `datasets/hotpotqa/`) and full 3-seed benchmark. If saturated, demote to "secondary, literature-cited only" and document in `benchmarks.md`.
4. **Secondary-number decisions on GSM8K + AIME** — explicit decision recorded in `docs/research/benchmarks.md`: saturated → cite published numbers only, or non-saturated on our setup → run as secondary. No full benchmark runs unless a decision flips to non-saturated.
5. **Statistical rigor on BBEH** — 3 seeds per configuration, 95% Wilson CIs, McNemar's test for significance between methods on shared held-out split.
6. **Result tables filled** — `docs/research/benchmarks.md` BBEH table populated with real numbers from all five methods.
7. **First figures** — paper-quality convergence plots (accuracy vs round) per dataset that actually ran, with ±1 std shaded regions across 3 seeds. Per-task BBEH breakdown heatmap.

### Track 2: Ablation Studies

**Problem:** The paper's differentiator story (three-layer escalation, SearchMemory, L1 critique) needs quantitative backing.

**Deliverables:**

| Ablation | What it isolates |
|----------|-----------------|
| L1 only vs L1+L2 vs full | Value of each optimization layer |
| Scan vs no scan | Value of sensitivity scan seeding |
| SearchMemory on vs off | Value of cross-campaign learning |
| Critique on vs off | Value of separated failure analysis |

Each ablation runs 3 seeds on **BBEH mini** (same split as Track 1 head-to-head) and produces a row in the ablation table documented in the publication figures spec. BBEH's 23-task structure also enables a per-task ablation breakdown — which layers help on which reasoning categories — a differentiator competitors can't easily produce.

**Bonus (optional, feeds M12 publication push):** OptSearchPoint refinement — advanced L1/L2/L3 strategies surfaced by ablation findings. Keep scope tight; main goal is the table, not new features.

### Track 2b: Zero-Signal Sample Filter Refinement

**Problem:** The v0 zero-signal filter (landed 2026-04-14, on by default) is deliberately rudimentary. It works — it catches always-hit/always-miss queries after `min_observations=5` and physically moves them into `datasets/{name}.json::excluded` — but it has two known weaknesses we should close before publication:

1. **Hard exclusion vs. tiered storage.** Excluded queries are dropped outright. A better design would keep them in a *second tier* that's still queryable — e.g. for L1 critique / L2 as negative-evidence context ("these 12 queries are trivially solved by every config, these 7 are intractable across everything we tried"), as degradation canaries (an always-hit query suddenly missing is a strong regression signal), or as a rehabilitation pool (probation → rotate back in after N rounds when the pipeline has materially changed). Right now the information is persisted but only read by `restore_dataset_items()` as a manual recovery path — nothing in the loop uses it as live signal.

2. **No shrink-below-budget guard.** The filter will happily prune the active dataset below `sp_budget_ttest`, leaving fewer samples than the scoring loop expects. At some point it's better to *keep* a zero-signal sample than to run on an under-sized eval set. The right threshold isn't obvious — strict (`len(items) >= sp_budget_ttest`), soft (`>= 2 * sp_budget_ttest` so PoBB's Normal-CLT posterior has tightening room), or *never shrink past the last confirmed informative set*. Needs empirical grounding from the BBEH runs.

**Deliverables:**

1. **Tiered storage design.** Replace the binary `items` / `excluded` split with tiers: `items` (active), `probation` (recently excluded, eligible for re-admission after N rounds or on pipeline change), `cold` (archived, read-only, surfaced to L1 critique/L2 as context). Define promotion/demotion rules.
2. **Exposure to L2/L1 critique.** Thread the cold tier into the SearchMemory digest as a dedicated signal ("zero-signal inventory: 12 trivial, 7 intractable") so the LLM tiers can reason about dataset shape rather than just per-sample outcomes.
3. **Shrink guard.** Add a hard floor tied to `sp_budget_ttest`. Decide strict vs soft empirically on BBEH — log how often the guard fires, whether it blocks the filter during long runs, and whether campaigns that hit the guard underperform.
4. **Degradation-canary role for always-hit.** Re-examine the symmetric treatment. Always-hit queries have value as regression signals; exclusion may be the wrong action even when they're zero-signal for *candidate ranking*. Possibly split the two branches: always-miss → excluded outright, always-hit → demoted to probation + used as canary.
5. **Ablation row.** Add a "zero-signal filter on vs off" row to the Track 2 ablation table on BBEH. Confirms the feature actually earns its place before it lands in the paper.

**Why this matters for M11.** BBEH runs will be the first campaigns where the filter sees serious mileage across diverse reasoning tasks. If the v0 implementation is leaving signal on the table (or worse, removing degradation canaries), M11 is when that'll become visible — better to refine it inside M11's measurement discipline than ship the paper with a known-rudimentary mechanism.

### Track 3: Webapp Read-Only Views (MVP)

**Problem:** M9's file-directory view model has no pixel UI.

**Technology:** Next.js + React in `webapp/` directory, consuming the existing FastAPI API which in turn reads the M9 view model. Thin pixel layer on top of a flat data layer — no duplication of render logic. Styling continues from the vanilla preview's plain CSS + design-token approach (CSS Modules ship with Next.js by default; no Tailwind dependency).

**Status (2026-05-07):** Slice 1 shipped 2026-05-05 as a vanilla `webapp/index.html` preview (see [`archive/m11-webapp-minimal-preview.md`](archive/m11-webapp-minimal-preview.md)). Next.js + React migration is now imminent — the vanilla file is the migration preservation list, not an iteration target. All Track 3 deliverables below land in the Next.js codebase.

**Deliverables:**

1. **Scaffolding** — `webapp/` directory with Next.js project, API client module, layout shell, dev proxy to FastAPI.
2. **Dashboard** — backend list, campaign summary cards, overall stats.
3. **Campaign detail** — convergence chart (accuracy vs round), trial timeline, best vs origin comparison. Data comes from the M9 view model via the API.
4. **Trial inspector** — prompt diff view, per-sample results table, failure analysis display.
5. **Migration of M11-vanilla views** — port the shipped Dashboard, Files, View Results pane, workflow canvas, and What-If ablation card into the Next.js + React codebase. Preserve every wired / held-real-estate / load-bearing element from the vanilla preservation list. Bake in origin a11y + semantic HTML + `prefers-reduced-motion` + typed data shapes (`dashboard.json` / `OptSearchPoint` / `archive/measurements/`) as new-codebase requirements.
6. **Additive monitoring containers** — read-only views the operator has flagged as M11 expansion homes:
   - **Hard-sample leaderboard** *alongside* the existing live-samples card (don't replace it). Surfaces samples that consistently fail across candidates and rounds via `SampleIndex.dead()`. Reference: [`hard-sample-sorter.md`](hard-sample-sorter.md). Likely sidebar slot: Analytics.
   - **Per-searchpoint score histogram across rounds.** Pairs with hard-sample leaderboard — see which samples each searchpoint got right/wrong and how that distribution shifted round over round. Source: `archive/measurements/` via `measurements_for_config(predicate)`. Likely sidebar slot: Analytics.
   - **Family-tree / speciation view.** Read-only. Root on the left (origin searchpoint), branches rightward as the population speciates through L2/L3 transitions. Source: `OptSearchPoint.lineage` + `campaigns/{cycle_id}/rounds/round_NNNN.json`. Likely sidebar slot: Evaluations. Operator-flagged "very important."
   - **Dataset preview view on drop.** When the user attaches a dataset, render a dedicated preview surface (not just a filename pill). Pairs with the canonical entry-flow shape — drop dataset → see preview → wand toggle on → quiet evolution starts.
7. **M12 launcher draft scaffolding** — first-draft surfaces staged in M11 to inform M12's launcher / control design (the resolved shape ships in M12 Wave 3). Both drafts coexist with the structured-form shape from deliverable 3 above; M11–M12 designs *one* launcher that covers both.
   - Chat panel staged as a candidate user-facing front-end for the existing `checkin` optimizer node (downstream of `l3_plan`). Prefilled conversation is illustrative of the eventual UX. Yet to fulfill what the campaign configuration form covers — both shapes are in play.
   - Wand "always-on background optimization" toggle staged as a candidate control surface (live optimization vs offline campaign framing). Coexists with discrete start / pause / resume / stop until M12 resolves which surface ships.
8. **Benchmark results display** — comparison tables from `docs/research/benchmarks.md` data (populated in Track 1), interactive convergence plots.

**Out of scope (M12):** campaign launcher wire-up to real backend, live monitoring (WebSocket/SSE), API extensions for control, polish/deployment.

**Security gate (must land before this track is exposed beyond localhost):** see [`security-audit.md`](security-audit.md) § Webapp endpoint hardening — auth dep on every router, CORS allowlist, Pydantic `extra=forbid`, slow-API rate limiter on cycle-read endpoints.

### Track 4: Publication Figures Design

**Problem:** Figures and tables must be designed before data collection so Tracks 1 and 2 produce the right data.

**Deliverables:** Document all figure and table designs in `docs/publication-figures.md` with mock layouts. Serves as the data collection checklist for Track 1.

- **Main results table** — columns: Method, BBEH Overall, BBEH per-task avg (top/bottom 3), HotPotQA F1 (if non-saturated), Optimizer cost, Source. Methods: Zero-shot, Few-shot (manual), PromptPotter (L1 only / L1+L2 / full), CAPO, GEPA, MIPROv2, BootstrapFewShot. All head-to-head methods use the identical BBEH mini split (seed=42, 10/10 per task) and the same `gpt-oss-120b` model.
- **BBEH per-task heatmap** — 23 tasks × methods, color-coded by accuracy. PromptPotter's differentiator: which reasoning categories each layer helps on.
- **Convergence figure** — accuracy vs round, PromptPotter variants as curves, competitor final numbers as horizontal refs, ±1 std shaded. One figure for BBEH, optionally one for HotPotQA if it runs.
- **Ablation table** — rows per Track 2 ablation, columns for BBEH overall + BBEH per-task-category (if slice is informative).
- **Analysis figures** — SearchMemory parameter impact heatmap, failure cluster visualization, per-node cache hit rate over rounds, L2/L3 escalation timeline. Paper vs supplemental split decided here.
- **Cost/efficiency scatter** — optimizer LLM calls vs BBEH accuracy gain. Positions against cost-aware competitors (Promptomatix, PromptWizard).
- **Saturation appendix** — explicit note on GSM8K/AIME saturation at `gpt-oss-120b` with cited literature numbers for context. Documents the methodological decision rather than hiding it.

Track 4 runs first or in parallel with Track 1a.

### Track 5: PromptPotter-as-backend Connector

**Problem:** M12's L4 self-optimization closure (running PromptPotter
on its own meta-prompts) needs a `Connector` that wraps L1/L2/L3
behind the same wire shape as TermNorm. The data shape and contract
parity test land in M10 (`docs/specs/archive/m10-cleanup.md` §3.5 +
self-optimization fixture under `datasets/promptpotter/`); M11 is
the right milestone to land the connector itself, because:

- M11 ablation work touches the connector boundary anyway (Track 2
  exercises L1/L2/L3 individually).
- Registering a second connector exercises the
  `promptpotter/connectors/` abstraction end-to-end without the
  cross-repo burden of coordinating with TermNorm.
- M12's "second connector" Track 1 deliverable then has a real
  candidate already on disk — M12 doesn't need to invent one.

**Deliverables:**

1. **`promptpotter/connectors/promptpotter.py`** — wraps L1, L2, L3,
   `l1_critique`, and `checkin` as a `Connector` (sibling shape
   to `promptpotter/connectors/termnorm.py`). Self-registers via
   `register(Connector(...))` at import.
2. **Connector wire shape.** Implements the four hooks bundled per
   `Connector`: `wire_adapter` (translates a `JobSearchPoint` into
   an L1/L2/L3 invocation against a fixed trace-replay fixture),
   `session_factory` (in-process, no external service),
   `extract_experiment` (reads `archive/measurements/` rows for the
   PromptPotter dataset), `resolve_ground_truth` (compares the
   meta-prompt's `next_brief` to the archived `score_delta`).
3. **`datasets/promptpotter/pipeline.json`** — describes the L1 /
   L2 / L3 / `l1_critique` / `checkin` nodes against the
   pinned `pipeline.json` contract from M10 §3.5. Validates against
   the M10 parity test alongside `optimizer_pipeline.json`.
4. **Bootstrap lookup hook.** `bootstrap.py` `connectors.get(...)`
   call already reads `pipeline.json::backend_type` (M12 Track 1
   work) — confirm the PromptPotter connector loads correctly via
   `backend_type: "promptpotter"`.
5. **Smoke run.** A 1-round campaign against the M10 trace-replay
   fixture (`datasets/promptpotter/`) — exercises the connector
   end-to-end without an actual outer-loop optimization (that's
   M12's Track 4). Test added under `tests/`.

**Out of scope for M11 Track 5:** the actual L4 closure run (M12).
Track 5 ships the connector + smoke-tests it; the outer-loop
optimization that improves L1/L2/L3 prompts is M12 work.

**Cross-ref:** `docs/specs/archive/m10-cleanup.md` §3.5 +
self-optimization fixture; `docs/specs/m12-multi-connector.md`
Track 1 (now names this connector explicitly as the "second
connector").

---

## Wave Sequencing

```
Wave 1: Track 4 (publication figures design) + Track 1 (HotPotQA saturation probe)
        — defines what Tracks 1 and 2 collect; probe decides HotPotQA in/out

Wave 2: Track 1 (BBEH PromptPotter + head-to-head notebooks) + Track 3 (webapp scaffold)
        — parallel; webapp consumes M9 file-directory view model

Wave 3: Track 1 (HotPotQA full run if non-saturated, result tables) + Track 2 (ablations on BBEH) + Track 3 (read-only views)
        — parallel; data and pixels converge

Wave 4: Track 3 (benchmark results display) + Track 5 (PromptPotter-as-backend connector)
        — needs Track 1 data tables; Track 5 lands once M10 §3.5 + fixture are in place
```

## Entry Criteria

- M10 exit gate passed (optimizer prompts converge in ≤5 rounds on at least two pipelines)
- M9 exit gate passed
- Hexagonal layout in place
- Multi-dataset/pipeline working
- File-directory UI v0 readable

## Exit Criteria

- [ ] BBEH results with statistical rigor (3 seeds, CIs, McNemar's test) for PromptPotter + CAPO + GEPA + MIPROv2 + BootstrapFewShot on identical splits
- [ ] HotPotQA saturation decision recorded; full run completed if non-saturated, otherwise saturation note in benchmarks.md
- [ ] GSM8K + AIME decision recorded (saturated → cite; non-saturated → run as secondary)
- [ ] `docs/research/benchmarks.md` result tables filled with real numbers
- [ ] All four ablations run on BBEH with results documented
- [ ] `docs/publication-figures.md` designed and populated
- [ ] Webapp read-only views live: dashboard, campaign detail, trial inspector, benchmark results
- [ ] Webapp reads from M9 view model (no parallel render pipeline)
- [ ] `promptpotter/connectors/promptpotter.py` registered; `datasets/promptpotter/pipeline.json` validates against the M10 §3.5 contract; smoke run completes against the M10 trace-replay fixture

## Key Existing Code

| Area | Files (post-M9 hexagonal layout) |
|------|-------|
| Dataset loaders | `application/datasets/loaders.py` |
| Scoring | `application/scoring/formula/` |
| Export pipeline | _Deleted (2026-04-27); restore from git history when supplement work resumes._ |
| FastAPI API | `presentation/api.py` |
| Benchmark methodology | `docs/research/benchmarks.md` |
| Dataset configs | `datasets/hotpotqa/`, `datasets/gsm8k/`, `datasets/lca-termnorm/` |
| BBEH head-to-head | `docs/research/bbeh-comparison/` (notebooks: `bbeh_capo.ipynb`, `bbeh_dspy.ipynb`) |
| View model | `sessions/{session_id}/views/` (M9 Track 4 output) |

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| BBEH difficulty produces flat results | All methods score near-random, no separation | Run PromptPotter probe before committing to 3-seed rigor; adjust sample size or switch to BBEH-full if mini is too small |
| HotPotQA also saturated | Publication loses multi-hop QA data point | Saturation probe is Wave 1; decision recorded early. Acceptable to publish BBEH-only if the story is strong |
| Head-to-head Colab drift | CAPO/GEPA/MIPROv2 notebook results not reproducible later | Pin library versions in notebooks; archive `results_*.json` next to the notebook |
| Webapp scope creep | Unbounded frontend work | MVP only. Launcher + live monitoring is M12. Ship read-only first |
| View model schema churn | M9's v0 too unstable to render against | Lock the schema at M9 exit; webapp depends on frozen contract |
| Ablation results weaken the story | Layers not actually helpful | Publish honestly; re-scope Track 2 bonus if L2/L3 look weak |
