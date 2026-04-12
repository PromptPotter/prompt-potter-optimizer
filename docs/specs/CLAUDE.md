# docs/specs -- Milestone Specs

## How to start a milestone

1. Read the milestone spec — scope decisions, deliverables, wave tables
2. Read the service files listed in the pre-reading hint

| Milestone | Spec file | Pre-reading hint |
|-----------|-----------|-----------------|
| M9: Stable Config, Hierarchy Refactor, Multi-Dataset/Pipeline, File-Directory UI v0 | [`m9-stable-config-and-scaffolding.md`](m9-stable-config-and-scaffolding.md) | `promptpotter/config/optimizer_prompts/`, `promptpotter/application/` (full tree), `promptpotter/shared/scoring.py`, `notebooks/optimization_campaign.ipynb`, `promptpotter/presentation/ui/campaign/` |
| M9 Track 2: Hierarchy Refactor (standalone) — **DONE** | [`m9-hierarchy-refactor.md`](m9-hierarchy-refactor.md) | `promptpotter/{domain,application,infrastructure,presentation}/`, `tests/test_artifact_parity.py` |
| M10: Publication Benchmarks, Ablation Studies, Webapp Read-Only | [`m10-publication-benchmarks.md`](m10-publication-benchmarks.md) | `docs/research/benchmarks.md`, `datasets/hotpotqa/`, `datasets/gsm8k/`, `promptpotter/application/datasets/builder.py`, `promptpotter/main.py` |
| M11: Multi-Connector, Competitor Comparison, Webapp Phase 2 | [`m11-multi-connector.md`](m11-multi-connector.md) | `promptpotter/infrastructure/backend/client.py`, `archive/m9-multi-connector.md`, `archive/m6-pipeline-composability.md`, `webapp/` (M10 output) |
| M11+: Backlog | [`m11-plus-backlog.md`](m11-plus-backlog.md) | (opportunistic; no pre-reading) |
Archived: M0-M7 in git history, M8 at [`archive/m8-campaign-intelligence.md`](archive/m8-campaign-intelligence.md), old M9 (multi-connector) at [`archive/m9-multi-connector.md`](archive/m9-multi-connector.md), old M9 (publication/config/webapp combined) at [`archive/m9-publication-config-webapp.md`](archive/m9-publication-config-webapp.md).

Cross-repo: Proper Step Loop spec (eval security gate + backend pipeline refactor) lives in the backend repo at `docs/spec/proper-step-loop.md`.
