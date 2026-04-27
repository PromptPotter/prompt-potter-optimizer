# docs/specs -- Milestone Specs

## How to start a milestone

1. Read the milestone spec — scope decisions, deliverables, wave tables
2. Read the service files listed in the pre-reading hint

**M11 is the headline milestone** — multi-connector, competitor comparison, webapp Phase 2. M9 + M10 are backbone work in front of it. Order matches reading priority: start with M11 to know the destination, then M9/M10 to know what's left in the prep.

| Milestone | Spec file | Pre-reading hint |
|-----------|-----------|-----------------|
| M11 (headline): Multi-Connector, Competitor Comparison, Webapp Phase 2 | [`m11-multi-connector.md`](m11-multi-connector.md) | `promptpotter/infrastructure/backend/client.py`, `webapp/` (M10 output) |
| M11+: Backlog | [`m11-plus-backlog.md`](m11-plus-backlog.md) | (opportunistic; no pre-reading) |
| M10 (backbone, publication): Publication Benchmarks, Ablation Studies, Webapp Read-Only | [`m10-publication-benchmarks.md`](m10-publication-benchmarks.md) | `docs/research/benchmarks.md`, `datasets/hotpotqa/`, `datasets/gsm8k/`, `promptpotter/application/datasets/builder.py`, `promptpotter/main.py` |
| M9 (backbone, in progress): Stable Config, Hierarchy Refactor, Multi-Dataset/Pipeline, File-Directory UI v0 | [`m9-stable-config-and-scaffolding.md`](m9-stable-config-and-scaffolding.md) | `promptpotter/application/optimization/prompts/`, `promptpotter/application/` (full tree), `promptpotter/domain/scoring.py`, `notebooks/optimization_campaign.ipynb`, `promptpotter/presentation/views/` |
| Hard-Sample Sorter (capability spec; unscheduled) | [`hard-sample-sorter.md`](hard-sample-sorter.md) | `promptpotter/application/intelligence/{hard_sample_sorter,adaptive_prefix,rasch}.py`, `docs/methods/exploration-exploitation.md` |

Archived under [`archive/`](archive/): M9 Track 2 hierarchy refactor (DONE — [`archive/m9-hierarchy-refactor.md`](archive/m9-hierarchy-refactor.md)). Archived in git history: M0-M7, M8 (campaign intelligence), old M9 (publication/config/webapp combined and multi-connector). Material relevant to M11 has been inlined in `m11-multi-connector.md`.

Cross-repo: Proper Step Loop spec (eval security gate + backend pipeline refactor) lives in the backend repo at `docs/spec/proper-step-loop.md`.
