# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.7.0] — 2026-05-26

> Note: existing entries below predate M9. Headline M10 beta-hosting (OIDC + lifecycle + quotas + browser start surface), Stage-1 identity foundation, M12 control-plane (ADR-0001/0002/0003), webapp Next.js port, and the mypy-strict-default migration are not enumerated here — see the v0.7.0 GitHub release notes for the headline summary.

### Added — Routed Dispatch arc
- Typed `dispatch_hub.SIGNALS` (`dict[str, _Signal]` with `name`/`kind`/`render`/`doc`); load-time `validate_template` raises on unknown `{{slot}}` names.
- New `axis_memory` signal — `cycle.axes.digest()` flows into L1, L2, L3 prompts.
- Cadence rules engine (`application/optimization/cadence/{rules,evaluator}.py`); `EscalationState.observe_round` delegates to `evaluate_round(SignalInputs)` over `DEFAULT_ROUND_RULES`. Opt-in `l2_axis_yield_drought` rule via `campaign.json::optimization.escalate_on_yield_drought`.
- `domain/decision_trace.py` — frozen Pydantic `DecisionTrace` (extra-forbid, JSON-roundtrip-stable). PoBB writes traces at promote/eliminate decision points → `RoundResult.decision_traces`; surfaced to `l1_critique` via the new `decision_trace_summary` signal.
- New `SignalsProjection` (`infrastructure/projections/signals.py`) appends `cadence/rule_fired` PhaseRecords to `.runtime/signals.jsonl`; `LiveDashboardProjection` mirrors firings into `dashboard.json::recent_rules` (rolling 8) + `current_signals` (latest per layer); webapp gains `SignalsPanel.tsx` (chronological readout) + `StuckDiagnosis.tsx` (per-layer verdict from latest `signal_inputs`).

### Changed
- Replaced Wilcoxon+Holm sequential elimination with Bayesian Posterior-of-Being-Best (PoBB)
  population-aware stopping. New `OptimizationConfig.pobb_epsilon` (default 0.05) replaces
  `elimination_alpha`. PoBB uses joint Normal-CLT posterior over candidate accuracy means;
  per-query Monte Carlo argmax computes each candidate's `P(round-best)`; stop when below ε.
- Consolidated `ScoringSetConfig` + `HardSampleSorterConfig` into one `ExplorationConfig`.
- Trimmed redundant Rasch refit: `hard_sample_sorter` now reuses the round-end posterior
  cached on `Cycle.last_rasch_posterior` instead of refitting at finalize.
- Per-query P(best) snapshot stream: new `streams/round_NNNN_p_best.jsonl` (append-only),
  surfaced on `dashboard.json::current_round.candidates[].p_best` + `current_round.p_best_top`,
  in CLI/notebook live display, and as ASCII sparklines in `log.md` round digests.
- New `PoBBStreamProjection` (subscribes to the per-cycle ledger, writes JSONL).
- Modernized all type hints to PEP 604 (`X | None`, `list[str]`, `dict[K, V]`) across 12 files
- Replaced `print()` with `logger.warning()` in evaluators
- Fixed all 12 ruff lint errors (E501 line length, E402 import order)
- Added project metadata to `pyproject.toml` (license, authors, keywords, classifiers, URLs)
- Standardized `api/services/stores/` facade pattern in `ProjectStore`
- Refactored grid search and API router conventions

## [0.6.0] — Spec rewrite and M2 close

### Changed
- Complete rewrite of all spec documents (project-charter, PRD, ADD, WBS, roadmap) to v0.6.0,
  reflecting the actual codebase state after M2
- M3 (Registry and Tracking) absorbed into M2; milestones renumbered
- Evaluator/workflow infrastructure documented as architectural north star for M3 migration
- Removed unused settings `MAX_DATASET_SIZE` and `MAX_ITERATIONS`
- Removed dead code: `OptimizationDefaults`, `_layer_for_field()`
- Migrated Pydantic V1 `class Config` to V2 `model_config` in settings and workflow models
- API version bump to 0.6.0

## [0.4.0] — M2: Core Optimizer

### Added
- **HITL Campaign Notebook** (`notebooks/optimization_campaign.ipynb`): interactive optimization
  with editable config, candidate coverage diagnostics, iterative prompt optimization,
  LLM-generated phrase fragment suggestions, patience-based stopping
- **Grid Search** (`api/services/grid_search.py`): cartesian product over Layer 1 prompt axes,
  distance-weighted stratified sampling with `grid_budget` + `exploration_rate`, two eval modes
  (backend full-pipeline via `/matches` + local LLM fallback), per-point caching + incremental
  writes + partial-run resume
- `_campaign_lib.py` notebook helper extracted from inline notebook code
- Eval caching at service level with content-addressed SHA256 keys
- Incremental `.partial.jsonl` writes for crash protection and resume
- Per-query HIT/MISS progress logging and training-style progress display
- Rate-limit backoff for Groq API (exponential backoff on 429s)
- Two primary optimization knobs: `n_samples` (queries per eval) + `exploration_rate`
- Exploration strategy presets for grid search
- Trace sync from backend with Langfuse-style eval data parsing

### Changed
- Optimization architecture: two primary knobs replace multi-parameter config
- `_campaign_lib.py` refactored into thin wrapper over `api/services/`

## [0.3.0] — M1: Foundation

### Added
- **PromptState model** (`api/models/prompt_state.py`): immutable 3-layer architecture
  (Generate / Refine Context / Modify Plan) with `render()`, `derive()`, and `OptimizationDefaults`
- **ProjectStore** (`api/services/project_store.py`): file-based storage under
  `.promptpotter/projects/` with incremental writes
- **Backends router** (`api/routers/backends.py`): register, sync, execute, compare endpoints
- **Comparison service** (`api/services/comparison.py`): McNemar's test, Wilcoxon signed-rank,
  hit@k, MRR
- **Pipeline parameter passthrough**: 11 controllable TermNorm pipeline knobs forwarded,
  echoed, and logged
- Test suite: evaluators, workflow runner, PromptState, incremental writes, API endpoints
- Test fixtures and dataset helpers in `tests/conftest.py`
- GitHub Actions CI (lint + test)

### Changed
- Replaced ablation system with project-based backend storage
- Replaced flat search optimizer with DAG-based optimization workflow

## [0.2.0] — M0: Specifications

### Added
- Project charter, PRD, ADD, WBS, roadmap
- Literature review of prompt optimization frameworks (DSPy, TextGrad, EvoPrompt)
- User guide with setup, optimization workflow, configuration reference
- TermNorm connector contract documentation

## [0.1.0] — Initial Setup

### Added
- FastAPI application skeleton with health, workflow, and backend routers
- Multi-provider LLM client (OpenAI, Anthropic, Groq via OpenAI-compatible SDK)
- Node-based workflow execution system (DAG runner with topological sort)
- Evaluators: ExactMatch and CriteriaEvaluator (LLM-as-judge)
- Langfuse cloud integration for observability
- TermNorm-to-Langfuse sync script
- Docker setup with JupyterLab + FastAPI
- Exploration notebook (`notebooks/termnorm_backend.ipynb`)
