# Research

Publication-facing material for PromptPotter. This folder collects the methodology, metrics, competitive positioning, and head-to-head benchmark results that back the paper.

| Page | Purpose |
|------|---------|
| [benchmarks.md](benchmarks.md) | Datasets, splits, evaluation protocol, baselines, result tables |
| [metrics.md](metrics.md) | Beyond absolute accuracy: Headroom Captured, Sample Efficiency, R₉₀ |
| [related-work.md](related-work.md) | Competitor positioning, feature matrix, AutoML algorithm-configuration lineage |
| [bbeh-comparison/](bbeh-comparison/) | Head-to-head infrastructure — CAPO, GEPA, MIPROv2, BootstrapFewShot vs. PromptPotter on BBEH mini |

---

## Reading order

1. **[benchmarks.md](benchmarks.md)** — what we're measuring and how. BBEH as primary, HotPotQA as secondary (pending saturation probe), GSM8K/AIME as cited-only. Includes task cluster guidance for reading BBEH results and SOTA reference for HotPotQA.
2. **[metrics.md](metrics.md)** — the four-metric reporting convention. Absolute accuracy hides more than it reveals; Headroom Captured (HC), Sample Efficiency (SE), and Convergence Profile (R₉₀) separate *how good*, *how much of what's available*, and *how cheaply*.
3. **[related-work.md](related-work.md)** — how PromptPotter sits relative to PromptWizard, MIPROv2, GEPA, CAPO, AdalFlow, and the AutoML algorithm-configuration lineage (F-Race → irace → SMAC).

The reproducible head-to-head infrastructure (Colab notebooks running CAPO, GEPA, MIPROv2, BootstrapFewShot against the identical `gpt-oss-120b` model and identical seed-42 splits) lives in [`bbeh-comparison/`](bbeh-comparison/).
