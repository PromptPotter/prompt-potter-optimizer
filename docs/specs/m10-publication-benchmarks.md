# M10: Publication Benchmarks, Ablation Studies, Webapp Read-Only

**Version:** 0.2.0
**Date:** 2026-04-12
**Status:** Planned
**Depends on:** M9 (Stable Config, Hierarchy Refactor, Multi-Dataset/Pipeline, File-Directory UI v0)

---

## Context

M9 delivered the foundation: stable meta-prompts, hexagonal layout, multi-dataset/pipeline support, and a file-directory view model that both CLI and notebook render from. M10 populates that foundation with the first publication-grade results and the first real webapp pass.

Three gaps M10 closes:

1. **Zero benchmark results.** The methodology exists (`docs/research/benchmarks.md`), the export pipeline is built. No PromptPotter campaigns have been run against the head-to-head infrastructure yet.
2. **Ablations are undone.** The paper's "which layer contributes what" story is unproven.
3. **No webapp.** The M9 file-directory view model is readable by humans but has no pixel UI. M10 puts the first pass on top.

### Benchmark Priority Pivot (2026-04-12)

Preliminary probing showed GSM8K and AIME 2025 are effectively **saturated** at `gpt-oss-120b` — the optimization loop has almost no headroom to demonstrate. They are deprioritized as primary targets. The new priority is:

1. **BBEH (Big-Bench Extra Hard)** — 23 diverse reasoning tasks, state-of-the-art reasoners still score ~54%, general-purpose ~24%. Ample headroom. Head-to-head infrastructure already exists at [`docs/research/bbeh-comparison/`](../research/bbeh-comparison/) — CAPO, GEPA, MIPROv2, and BootstrapFewShot notebooks run against the same `gpt-oss-120b` model and same 10/10 mini split, seed=42.
2. **HotPotQA** — multi-hop QA data point, pending saturation check. If it looks saturated on probe runs, demote to secondary-if-time-permits.
3. **GSM8K, AIME 2025** — secondary at best. Include in tables only if there is demonstrable headroom; otherwise cite existing literature numbers and move on.

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

**Problem:** The paper's differentiator story (three-layer escalation, SearchMemory, critique) needs quantitative backing.

**Deliverables:**

| Ablation | What it isolates |
|----------|-----------------|
| L1 only vs L1+L2 vs full | Value of each optimization layer |
| Scan vs no scan | Value of sensitivity scan seeding |
| SearchMemory on vs off | Value of cross-campaign learning |
| Critique on vs off | Value of separated failure analysis |

Each ablation runs 3 seeds on **BBEH mini** (same split as Track 1 head-to-head) and produces a row in the ablation table documented in the publication figures spec. BBEH's 23-task structure also enables a per-task ablation breakdown — which layers help on which reasoning categories — a differentiator competitors can't easily produce.

**Bonus (optional, feeds M11 publication push):** OptSearchPoint refinement — advanced L1/L2/L3 strategies surfaced by ablation findings. Keep scope tight; main goal is the table, not new features.

### Track 3: Webapp Read-Only Views (MVP)

**Problem:** M9's file-directory view model has no pixel UI.

**Technology:** Next.js + React + Tailwind CSS in `webapp/` directory, consuming the existing FastAPI API which in turn reads the M9 view model. Thin pixel layer on top of a flat data layer — no duplication of render logic.

**Deliverables:**

1. **Scaffolding** — `webapp/` directory with Next.js project, API client module, layout shell, dev proxy to FastAPI.
2. **Dashboard** — backend list, campaign summary cards, overall stats.
3. **Campaign detail** — convergence chart (accuracy vs round), trial timeline, best vs baseline comparison. Data comes from the M9 view model via the API.
4. **Trial inspector** — prompt diff view, per-query results table, failure analysis display.
5. **Benchmark results display** — comparison tables from `docs/research/benchmarks.md` data (populated in Track 1), interactive convergence plots.

**Out of scope (M11):** campaign launcher, live monitoring (WebSocket/SSE), API extensions for control, polish/deployment.

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

---

## Wave Sequencing

```
Wave 1: Track 4 (publication figures design) + Track 1 (HotPotQA saturation probe)
        — defines what Tracks 1 and 2 collect; probe decides HotPotQA in/out

Wave 2: Track 1 (BBEH PromptPotter + head-to-head notebooks) + Track 3 (webapp scaffold)
        — parallel; webapp consumes M9 file-directory view model

Wave 3: Track 1 (HotPotQA full run if non-saturated, result tables) + Track 2 (ablations on BBEH) + Track 3 (read-only views)
        — parallel; data and pixels converge

Wave 4: Track 3 (benchmark results display)
        — needs Track 1 data tables
```

## Entry Criteria

- M9 exit gate passed
- Stable meta-prompts committed
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

## Key Existing Code

| Area | Files (post-M9 hexagonal layout) |
|------|-------|
| Dataset loaders | `application/datasets/builder.py` |
| Scoring | `shared/scoring.py` |
| Export pipeline | `presentation/cli/` (was `cli/export_results.py`), `application/campaign/export.py`, `application/campaign/reporting.py` |
| FastAPI API | `presentation/api/` (was `main.py` + `routers/`) |
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
| Webapp scope creep | Unbounded frontend work | MVP only. Launcher + live monitoring is M11. Ship read-only first |
| View model schema churn | M9's v0 too unstable to render against | Lock the schema at M9 exit; webapp depends on frozen contract |
| Ablation results weaken the story | Layers not actually helpful | Publish honestly; re-scope Track 2 bonus if L2/L3 look weak |
