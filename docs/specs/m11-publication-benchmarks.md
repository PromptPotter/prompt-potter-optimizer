# M11: Publication Benchmarks, Ablation Studies, Webapp Read-Only

**Status:** partial. Webapp slice 1 + React port shipped 2026-05; benchmark + ablation + connector tracks open.

## What this covers

The publication arc — running PromptPotter against state-of-the-art prompt-optimization tooling on a benchmark with real headroom (BBEH), running ablations to back the differentiator story, and putting a pixel UI on top of the read-only view model.

## Status

- **Webapp read-only — shipped.** Next.js + TS + plain-CSS static export at `webapp/`; mounted at the domain root by FastAPI. Hard-sample leaderboard, per-searchpoint score histogram, family-tree speciation, dataset-preview-on-drop all live.
- **Benchmark priority — decided.** BBEH primary (ample headroom on `gpt-oss-120b`); HotPotQA pending saturation probe; GSM8K / AIME deprioritized (saturated). Decision recorded.
- **Benchmark runs + ablations — open.** No PromptPotter runs against the head-to-head infrastructure yet; no ablation rows produced.
- **PromptPotter-as-connector — open.** Smoke target for `datasets/promptpotter/` fixture; gates M12 L4 closure.

## Open items

- BBEH PromptPotter run + head-to-head notebooks (`bbeh_capo.ipynb`, `bbeh_dspy.ipynb`); 3 seeds, Wilson CIs, McNemar's test.
- HotPotQA saturation probe; decide in/out.
- Ablation rows: L1 / L1+L2 / full; scan vs none; SearchMemory on/off; critique on/off; zero-signal filter on/off.
- Publication figure designs (`docs/publication-figures.md`); main results table; per-task BBEH heatmap; convergence plots.
- `promptpotter/connectors/promptpotter.py` connector + smoke run against `datasets/promptpotter/`.

## Code surface

- Head-to-head infra: `docs/research/bbeh-comparison/` (notebooks pin lib versions; archive `results_*.json` next to each).
- Loaders + scorers: `promptpotter/application/datasets/`, `promptpotter/application/scoring/formula/`.
- Webapp surface: `webapp/` (Next.js source + static export under `webapp/out/`); FastAPI mount at the domain root.
- BBEH score anomaly to verify before publication: see `MEMORY.md::project_bbeh_score_anomaly`.

## Webapp endpoint hardening (prereq for exposure beyond localhost)

Before the FastAPI surface is exposed outside `127.0.0.1`, the read-only routers need a shared hardening pass:

- **Auth dependency on every router** — even read-only endpoints reject anonymous traffic by default. Local "MS Word mode" can pin auth-off via a feature flag; the production deployment must not.
- **Tighten `ALLOWED_ORIGINS`** — currently permissive for local dev. Pin to the deployed origin in production.
- **Pydantic `extra=forbid`** on every request model — drops the bystander-fields class of attacks.
- **Slow-API rate limiter on cycle reads** — `dashboard.json` polling at 2s intervals from N tenants needs a per-tenant ceiling.
- **The M12 writeable surface adds** CSRF on mutating routes + upload size/shape limits on dataset POSTs.

These are P0 for shared deployment; landed redaction + path-validation + AST-validator + fence-untrusted are the first-pass complements.

## Cross-refs

- [`m12-multi-connector.md`](m12-multi-connector.md) Track 1 names PromptPotter-as-connector as the second registered connector.
- [`../research/benchmarks.md`](../research/benchmarks.md) — methodology + results target.
