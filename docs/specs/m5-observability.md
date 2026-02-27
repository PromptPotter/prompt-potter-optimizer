# Milestone 5: Observability Layer

**Version:** 0.8.0
**Date:** 2026-02-25
**Status:** Complete
**Depends on:** [Roadmap M5](roadmap.md), [ADD v0.7.0](add.md), [PRD P1.10–P1.11](prd.md)

---

## Context

**Current state:** Langfuse SDK v2 (cloud-only) provides per-trial tracing via `langfuse_client.py`. No disk fallback — if credentials are missing, tracing silently skips. No MLflow-compatible experiment files. No prompt versioning on disk. LLM client has no retry logic (Groq 503s crash the feedback cycle).

**Why file-based:** Works offline, `mlflow ui` compatible out of the box, no credentials needed, human-readable JSON, fits the existing file-based ProjectStore pattern.

**Reference implementation:** TermNorm-excel's zero-dependency file patterns at `/c/Users/dsacc/OfficeAddinApps/TermNorm-excel/backend-api/utils/` — battle-tested in production.

---

## Scope Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Langfuse SDK | Augment, not replace | Keep cloud tracing for live campaigns; add file fallback |
| TermNorm patterns | Adapt, not copy verbatim | Different domain (optimization vs. pipeline); same file layout |
| Module placement | `api/services/observability_logger.py` (new module) | Not a store — it writes to `obs/` outside ProjectStore's `dataset_runs/` scope |
| Config flag | `OBS_ENABLED: bool = True` in settings | Allow disabling for tests and CI |
| LLM retry | Exponential backoff in `llm_client.py` | Fixes known Groq 503 crash risk; independent of obs logger |
| `events.jsonl` | Custom flat event log | Starting point for human data exploration. Not Langfuse/MLflow spec. Adapted from TermNorm `langfuse_logger._log_event()`. Every public ObsLogger method appends here. |
| MLflow viewer | Separate venv, no PromptPotter dependency | PromptPotter writes MLflow FileStore format; `mlflow ui` runs in a throwaway venv pointing at the files. Reference: TermNorm `FILE_ORGANIZATION_STRATEGY.md` line 128. |

---

## Deliverables

| # | File | Action | What |
|---|------|--------|------|
| 1 | `api/services/observability_logger.py` | CREATE | `ObsLogger` class: `log_dataset_run()`, `log_campaign_start()`, `log_round()`, `log_prompt_version()` |
| 2 | `api/services/llm_client.py` | MODIFY | Add exponential backoff for 503/429 in `_OpenAICompatibleClient._chat_completion()` |
| 3 | `api/services/feedback_cycle.py` | MODIFY | Wire `log_campaign_start()` and `log_round()` into `run_feedback_cycle()` |
| 4 | `api/services/prompt_eval.py` | MODIFY | Wire `log_dataset_run()` into `evaluate_prompt_cached()` after final result write |
| 5 | `api/config/settings.py` | MODIFY | Add `OBS_ENABLED: bool = True` to `Settings` |
| 6 | `tests/test_observability.py` | CREATE | Unit tests for ObsLogger file output and LLM retry logic |
| 7 | `api/services/search/eval_dataset.py` | MODIFY | Generic observation extraction via `OBS_EXTRACTION_MAP` |
| 8 | `tests/test_eval_dataset.py` | CREATE | Tests for mapping-driven extraction |

---

## File Layout

All observability output lives under the project directory, parallel to existing stores:

```
.promptpotter/projects/{backend_id}/
  obs/
    langfuse/
      events.jsonl                          # Flat nav log — START HERE for data exploration
      traces/{trace_id}.json                # One file per trace (campaign run, eval batch)
      observations/{trace_id}/{obs_id}.json # Nested observations (rounds, queries)
      scores/{trace_id}.jsonl               # Appended accuracy scores
    experiments/                            # MLflow FileStore format (read with mlflow ui)
      {experiment_id}/
        meta.yaml                           # MLflow ExperimentInfo (campaign metadata)
        {run_id}/
          meta.yaml                         # MLflow RunInfo (round metadata)
          params/{param_name}               # Individual parameter files
          metrics/{metric_name}             # Metric time-series (timestamp value step)
          tags/{tag_name}                   # Tags (model, provider, etc.)
    prompts/
      {prompt_family}/
        {version}/
          metadata.json                     # Version metadata + template_variables
          prompt.txt                        # Rendered Layer 1 prompt text
```

---

## Adopt/Adapt Mapping

Each TermNorm function maps to a PromptPotter equivalent. All TermNorm sources are at `/c/Users/dsacc/OfficeAddinApps/TermNorm-excel/backend-api/utils/`.

### From `langfuse_logger.py`

| TermNorm Function | PromptPotter Equivalent | Notes |
|-------------------|------------------------|-------|
| `create_trace()` | `ObsLogger.log_campaign_start()` | Campaign = trace. Metadata includes CycleConfig, baseline accuracy. |
| `create_observation()` | `ObsLogger.log_round()` | Round = observation (type=span). Includes candidate scores, winner, next_action. |
| `create_score()` | Appended inside `log_round()` | Accuracy score per round, appended to `scores/{trace_id}.jsonl`. |
| `get_or_create_item()` | Not adopted | PromptPotter uses content-addressed `dataset_runs`, not dataset items. |
| `log_pipeline()` | Not adopted | TermNorm-specific (multi-step pipeline logging). |
| `generate_dated_id()` | Adopt as `_generate_obs_id()` | DateTime-prefixed hex IDs for trace/observation files. |
| `_log_event()` | `ObsLogger._log_event()` | Append to `obs/langfuse/events.jsonl` with auto-timestamp. Every public ObsLogger method calls this to maintain the flat navigation log. |

### From `standards_logger.py`

| TermNorm Class | PromptPotter Equivalent | Notes |
|----------------|------------------------|-------|
| `ExperimentManager` | `ObsLogger._ensure_experiment()` | One experiment per campaign. MLflow `meta.yaml` format. |
| `RunManager` | `ObsLogger.log_round()` | One run per optimization round. Params: model, temperature, n_variants. Metrics: accuracy, hits, total. |
| `TraceLogger` | Not adopted (dual-format) | PromptPotter keeps Langfuse JSON only (no MLflow span conversion). |
| `ConfigTreeManager` | Not adopted | PromptPotter tracks config changes via PromptState `derive()` + `diff()`. |
| `TaskDatasetManager` | Not adopted | PromptPotter uses `dataset_runs` store, not separate task datasets. |

### From `prompt_registry.py`

| TermNorm Function | PromptPotter Equivalent | Notes |
|-------------------|------------------------|-------|
| `register_prompt()` | `ObsLogger.log_prompt_version()` | Family = "ranking_prompt". Version = PromptState ID prefix. Template = `render()` output. |
| `get_prompt()` / `get_latest_version()` | Not adopted (read path) | PromptPotter reads prompts from PromptState, not from registry. Registry is write-only audit trail. |
| `render_prompt()` | Not adopted | PromptPotter has `PromptState.render()`. |
| `initialize_default_prompts()` | Not adopted | No bootstrap needed; baseline PromptState is created by InitNode. |

---

## ObsLogger API Sketch

```python
class ObsLogger:
    """File-based observability logger. Writes Langfuse + MLflow compatible files.

    Every public method also appends to events.jsonl — the flat navigation log
    that humans read first when exploring accumulated research data.
    """

    def __init__(self, project_root: str | Path, backend_id: str):
        self.obs_root = Path(project_root) / ".promptpotter/projects" / backend_id / "obs"
        self._enabled = settings.OBS_ENABLED

    # --- Internal helpers ---

    def _log_event(self, event: dict) -> None:
        """Append event to obs/langfuse/events.jsonl with auto-timestamp.
        Adapted from TermNorm langfuse_logger._log_event().
        Every public method calls this to maintain the flat navigation log."""

    # --- Public API ---

    def log_dataset_run(
        self, run_id: str, content_hash: str, accuracy: float,
        total: int, hits: int, model: str, temperature: float,
        prompt_state_id: str = "",
    ) -> Path | None:
        """Write Langfuse trace + events.jsonl line for a completed eval run."""

    def log_campaign_start(
        self, campaign_id: str, config: dict, baseline_accuracy: float,
    ) -> Path | None:
        """Create experiment dir (MLflow meta.yaml) + Langfuse trace + events.jsonl line."""

    def log_round(
        self, campaign_id: str, round_num: int, accuracy: float,
        hits: int, total: int, improved: bool, next_action: str,
        winner_prompt_state_id: str, candidate_scores: list[dict],
    ) -> Path | None:
        """Write observation + score + MLflow run dir + events.jsonl line."""

    def log_prompt_version(
        self, prompt_state_id: str, rendered_prompt: str,
        layer1_fields: dict, parent_id: str | None = None,
    ) -> Path | None:
        """Write prompt.txt + metadata.json + events.jsonl line."""
```

All methods are synchronous (file I/O only). All methods are no-ops when `OBS_ENABLED = False`. All methods are wrapped in try/except to never crash the main flow.

---

## LLM Retry Logic

Add to `_OpenAICompatibleClient._chat_completion()` in `llm_client.py`:

- **Retry on:** HTTP 429 (rate limit), 503 (service unavailable), connection errors
- **Strategy:** Exponential backoff: 1s, 2s, 4s, 8s (max 3 retries)
- **Logging:** `logger.warning(f"LLM request failed ({status}), retrying in {delay}s...")`
- **No retry on:** 400, 401, 404 (client errors are not transient)

---

## Data Exploration Guide

**Start here:** `obs/langfuse/events.jsonl` — a flat, human-readable log where every significant action is one JSON line. This is a custom extension (not part of the Langfuse or MLflow spec), adapted from TermNorm's `langfuse_logger._log_event()`.

### events.jsonl format

Each line is a self-contained JSON object with `event`, `timestamp`, and navigation IDs:

```jsonl
{"event": "dataset_run", "trace_id": "260225...", "run_id": "baseline_816203b2", "accuracy": 0.75, "hits": 30, "total": 40, "model": "llama-4-maverick", "timestamp": "2026-02-25T14:00:00Z"}
{"event": "campaign_start", "trace_id": "260225...", "campaign_id": "campaign_abc", "baseline_accuracy": 0.75, "timestamp": "2026-02-25T14:01:00Z"}
{"event": "round_complete", "trace_id": "260225...", "campaign_id": "campaign_abc", "round": 0, "accuracy": 0.80, "improved": true, "next_action": "generate", "timestamp": "2026-02-25T14:05:00Z"}
{"event": "prompt_version", "prompt_state_id": "abc12345", "family": "ranking_prompt", "parent_id": "def67890", "timestamp": "2026-02-25T14:05:01Z"}
```

### Navigating from events.jsonl

| Field in event | Navigate to | What you'll find |
|---------------|-------------|-----------------|
| `trace_id` | `langfuse/traces/{trace_id}.json` | Full trace metadata (input, output, tags) |
| `trace_id` | `langfuse/observations/{trace_id}/` | Detailed step-by-step observations |
| `trace_id` | `langfuse/scores/{trace_id}.jsonl` | Evaluation scores over time |
| `campaign_id` | `experiments/{campaign_id}/meta.yaml` | MLflow experiment metadata |
| `campaign_id` | `experiments/{campaign_id}/{run_id}/` | Per-round MLflow runs with params/metrics |
| `prompt_state_id` | `prompts/ranking_prompt/{id_prefix}/` | Rendered prompt text + Layer 1 field metadata |

---

## MLflow Viewer Setup

PromptPotter has **zero MLflow dependency**. It writes experiment data in MLflow FileStore format using pure JSON/YAML file I/O. To visualize the data, use a separate throwaway Python environment:

```bash
# Create isolated viewer environment (do NOT install mlflow in PromptPotter's venv)
python -m venv C:\temp\mlflow-viewer\venv
C:\temp\mlflow-viewer\venv\Scripts\activate
pip install mlflow

# Point at the experiment data PromptPotter wrote
mlflow ui --backend-store-uri file:./.promptpotter/projects/{backend_id}/obs/experiments
# Opens at http://localhost:5000
```

This pattern is proven in production by TermNorm-excel (reference: `backend-api/docs/FILE_ORGANIZATION_STRATEGY.md` line 128).

**What you'll see in mlflow ui:**
- One experiment per optimization campaign
- One run per optimization round (with params: model, temperature, n_variants)
- Metrics: accuracy, hits, total (time-series per round)
- Tags: provider, next_action, improved

---

## Work Packages

| ID | Work Package | Sessions | Depends on | Description |
|----|-------------|:--------:|------------|-------------|
| 5.0 | Write M5 spec | 1 | — | This document |
| 5.1 | ObsLogger core | 1 | 5.0 | Create `observability_logger.py` with `log_dataset_run()`, `log_campaign_start()`, `log_round()`. File layout creation. Unit tests for file output. |
| 5.2 | Prompt registry | 1 | 5.1 | Add `log_prompt_version()` to ObsLogger. Write prompt text + metadata files. |
| 5.3 | LLM retry logic | 1 | 5.0 | Exponential backoff in `llm_client.py`. Unit tests with mock HTTP responses. |
| 5.4 | Wire into services | 1 | 5.1, 5.3 | Wire `log_dataset_run()` into `evaluate_prompt_cached()`, wire campaign logging into `feedback_cycle.py`. Config flag gating. |
| 5.5 | Integration test | 1 | 5.4 | E2E test: run feedback cycle with mocked backend, verify obs files are written in correct layout. |
| 5.6 | Generic pipeline observation extraction | 1 | 5.0 | Replace hardcoded `if/elif` in `eval_dataset.py` with mapping-driven extraction. `OBS_EXTRACTION_MAP` defines obs→pipeline_data field rules. Extract all 4 observations + model metadata + timing. Tests. |

### Reading list per work package

| WP | Read first |
|----|-----------|
| 5.1 | TermNorm `langfuse_logger.py` (traces/observations/scores layout), TermNorm `standards_logger.py` (MLflow meta.yaml format), `api/services/prompt_eval.py` (evaluate_prompt_cached) |
| 5.2 | TermNorm `prompt_registry.py` (family/version/prompt.txt), `api/models/prompt_state.py` (render(), Layer 1 fields) |
| 5.3 | `api/services/llm_client.py` (_chat_completion method), search for existing error handling |
| 5.4 | `api/services/prompt_eval.py` (evaluate_prompt_cached final write), `api/services/feedback_cycle.py` (run_feedback_cycle loop body) |
| 5.5 | `tests/test_e2e_optimization.py` (existing E2E pattern), `api/config/settings.py` (OBS_ENABLED flag) |
| 5.6 | `api/services/search/eval_dataset.py` (current extraction), `api/services/backend_client.py` (PIPELINE_STEP_PARAMS pattern, load_pipeline_config), `api/models/workflow.py` (StepDefinition for M6 reference) |

---

## Entry Criteria

- M4 exit gate passed (or waived by project owner)
- TermNorm reference files accessible at `/c/Users/dsacc/OfficeAddinApps/TermNorm-excel/backend-api/utils/`
- Existing tests pass (`pytest -v --tb=short`)

## Exit Criteria

- `evaluate_prompt_cached()` writes Langfuse-compatible trace files to `obs/langfuse/`
- `run_feedback_cycle()` writes MLflow-compatible experiment files to `obs/experiments/`
- Prompt versions written to `obs/prompts/` for each PromptState used in optimization
- `events.jsonl` written for every dataset_run, campaign, round, and prompt version
- `mlflow ui --backend-store-uri file:./.promptpotter/projects/{id}/obs/experiments` can read experiment data
- LLM client retries transient 503/429 errors with exponential backoff
- All existing tests still pass
- New tests in `tests/test_observability.py` cover file output format, events.jsonl, and retry logic
- `_extract_eval_from_traces()` extracts all pipeline observations via declarative mapping (no hardcoded step names)
- `OBS_EXTRACTION_MAP` is the single config point for observation-to-field mapping

## Test Strategy

| Test | Type | What it verifies |
|------|------|-----------------|
| `test_obs_logger_dataset_run` | Unit | `log_dataset_run()` creates trace JSON with correct schema |
| `test_obs_logger_campaign` | Unit | `log_campaign_start()` creates experiment dir + meta.yaml |
| `test_obs_logger_round` | Unit | `log_round()` creates observation JSON + MLflow run dir |
| `test_obs_logger_prompt_version` | Unit | `log_prompt_version()` writes prompt.txt + metadata.json |
| `test_events_jsonl` | Unit | Every public method appends a line to `events.jsonl` with correct `event` type and navigation IDs |
| `test_events_jsonl_navigation` | Unit | Parse events.jsonl, verify `trace_id`/`campaign_id` point to actual files on disk |
| `test_obs_disabled` | Unit | All methods are no-ops when `OBS_ENABLED = False` |
| `test_llm_retry_503` | Unit | Mock 503 response, verify retry with backoff, eventual success |
| `test_llm_retry_429` | Unit | Mock 429 response, verify retry |
| `test_llm_no_retry_400` | Unit | Mock 400 response, verify immediate raise |
| `test_llm_retry_exhausted` | Unit | Mock 503 every attempt, verify exception after max retries |
| `test_obs_integration` | Integration | Run feedback cycle with mocked backend, verify complete obs/ tree including events.jsonl |
| `test_eval_dataset_*` | Unit | Mapping-driven extraction produces correct pipeline_data from trace observations |
