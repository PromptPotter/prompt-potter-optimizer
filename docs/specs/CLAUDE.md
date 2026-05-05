# docs/specs -- Milestone Specs

## How to start a milestone

1. Read the milestone spec — scope decisions, deliverables, wave tables
2. Read the service files listed in the pre-reading hint

**M12 is the headline milestone** — multi-connector, competitor comparison, webapp Phase 2. M9 + M10 + M11 are backbone work in front of it. Order matches reading priority: start with M12 to know the destination, then M9/M10/M11 to know what's left in the prep.

| Milestone | Spec file | Pre-reading hint |
|-----------|-----------|-----------------|
| M12 (headline): Multi-Connector, Competitor Comparison, Webapp Phase 2 | [`m12-multi-connector.md`](m12-multi-connector.md) | `promptpotter/infrastructure/backend.py`, `webapp/` (M11 output) |
| M12+: Backlog | [`m12-plus-backlog.md`](m12-plus-backlog.md) | (opportunistic; no pre-reading) |
| M11 (backbone, publication): Publication Benchmarks, Ablation Studies, Webapp Read-Only | [`m11-publication-benchmarks.md`](m11-publication-benchmarks.md) | `docs/research/benchmarks.md`, `datasets/hotpotqa/`, `datasets/gsm8k/`, `promptpotter/application/datasets/datasets.py`, `promptpotter/main.py` |
| M10 (backbone, optimizer-prompts; also L4 partial): Prompt-Iteration Framework + L1-generate Tuning | [`m10-prompt-iteration-framework.md`](m10-prompt-iteration-framework.md) | `promptpotter/application/optimization/optimizer_pipeline.json` (`resolved_prompts` + `resolved_schemas` registries), `promptpotter/application/optimization/pipeline.py` (`compile_l1_surface`, `compile_l2_surface`, `L1GenerateField`, `OptimizerAction`, `load_optimizer_prompt`), `promptpotter/presentation/views/log_md.py`, `promptpotter/application/runner.py`, `tests/test_artifact_parity.py`. Cross-ref: [`m12-plus-backlog.md § Self-optimization`](m12-plus-backlog.md). |
| M9 (complete): Hierarchy Refactor, Multi-Dataset/Pipeline, File-Directory UI v0, Config Aggregate | [`m9-stable-config-and-scaffolding.md`](m9-stable-config-and-scaffolding.md) | reference only; spec retained for historical context |
| Hard-Sample Sorter (capability spec; unscheduled) | [`hard-sample-sorter.md`](hard-sample-sorter.md) | `promptpotter/application/intelligence/{hard_sample_sorter,exploration}.py`, `docs/methods/exploration-exploitation.md` |
| Security audit — first hardening pass (2026-05-05) | [`security-audit.md`](security-audit.md) | `promptpotter/application/scoring/formula.py`, `promptpotter/config/log_redaction.py`, `promptpotter/application/optimization/dispatch_hub.py` |

Archived under [`archive/`](archive/): M9 Track 2 hierarchy refactor (DONE — [`archive/m9-hierarchy-refactor.md`](archive/m9-hierarchy-refactor.md)). Archived in git history: M0-M7, M8 (campaign intelligence), old M9 (publication/config/webapp combined and multi-connector). Material relevant to M12 has been inlined in `m12-multi-connector.md`.

Cross-repo: Proper Step Loop spec (eval security gate + backend pipeline refactor) lives in the backend repo at `docs/spec/proper-step-loop.md`.
