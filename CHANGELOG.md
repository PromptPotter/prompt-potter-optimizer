# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Discovery-driven pipeline protocol in specs (P1.7): `GET /pipeline` endpoint contract,
  `get_pipeline_schema()` integration, 4-stage pipeline schema discovery
- WP 2.10-2.11 work packages in WBS and roadmap for schema discovery
- Evaluation constraint added to project charter

### Changed
- Project charter version bump to 0.5.0
- API version bump to 0.5.0

## [0.4.0] — M2: Core Optimizer (in progress)

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
