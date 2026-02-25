"""
File-based observability logger.

Writes three layers of human-accessible data for reviewing research runs:

1. **events.jsonl** — flat navigation log (START HERE for data exploration)
2. **Langfuse traces/observations/scores** — structured detail files on disk
3. **MLflow experiments** — round-by-round metrics browsable in ``mlflow ui``

Adapted from TermNorm-excel's zero-dependency file patterns:
- ``langfuse_logger.py`` — trace/observation/score JSON schemas + ``_log_event()``
- ``standards_logger.py`` — MLflow FileStore format (meta.yaml, params/, metrics/, tags/)
- ``prompt_registry.py`` — family/version/prompt.txt layout

No external dependencies (pure JSON/YAML file I/O). PromptPotter has zero MLflow
dependency — the viewer runs in a separate throwaway venv.

Usage::

    obs = ObsLogger(project_root, backend_id)
    obs.log_dataset_run(run_id, content_hash, accuracy, ...)
    obs.log_campaign_start(campaign_id, config, baseline_accuracy)
    obs.log_round(campaign_id, round_num, accuracy, ...)
    obs.log_prompt_version(prompt_state_id, rendered_prompt, layer1_fields)
"""

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers (adapted from TermNorm standards_logger / langfuse_logger)
# ---------------------------------------------------------------------------


def _generate_obs_id(length: int = 32) -> str:
    """DateTime-prefixed hex ID.

    Format: YYMMDDHHMMSS + random hex to fill remaining length.
    Adapted from TermNorm ``standards_logger.generate_dated_id()``,
    using ``datetime.now(timezone.utc)`` instead of deprecated ``utcnow()``.

    Example (32 chars): ``260225143052a7b8c9d0e1f2345678ab``
    """
    prefix = datetime.now(timezone.utc).strftime("%y%m%d%H%M%S")
    suffix = uuid.uuid4().hex[: length - len(prefix)]
    return f"{prefix}{suffix}"


def _utcnow_iso() -> str:
    """UTC timestamp in ISO 8601 format with Z suffix."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_yaml(path: Path, data: dict) -> None:
    """Write dict as YAML-compatible ``key: value`` lines (no PyYAML dependency).

    Adapted from TermNorm ``RunManager._write_yaml()``. Handles str, int, float,
    bool, None, and list (as JSON arrays). Sufficient for MLflow meta.yaml format.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for key, value in data.items():
            if isinstance(value, str):
                f.write(f'{key}: "{value}"\n')
            elif value is None:
                f.write(f"{key}: null\n")
            elif isinstance(value, bool):
                f.write(f"{key}: {str(value).lower()}\n")
            elif isinstance(value, list):
                f.write(f"{key}: {json.dumps(value)}\n")
            else:
                f.write(f"{key}: {value}\n")


def _write_json(path: Path, data: dict) -> None:
    """Write pretty-printed JSON, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _append_jsonl(path: Path, data: dict) -> None:
    """Append one JSON line to a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# ObsLogger
# ---------------------------------------------------------------------------


class ObsLogger:
    """File-based observability logger.

    Writes Langfuse-compatible traces + MLflow FileStore experiments +
    ``events.jsonl`` flat navigation log. Every public method also appends
    to ``events.jsonl`` — the human starting point for data exploration.

    All methods are synchronous (file I/O only). All methods are no-ops when
    ``OBS_ENABLED = False``. All methods are wrapped in try/except to never
    crash the main flow.
    """

    def __init__(self, project_root: str | Path, backend_id: str):
        from api.config.settings import settings

        self.obs_root = (
            Path(project_root) / ".promptpotter" / "projects" / backend_id / "obs"
        )
        self._enabled = settings.OBS_ENABLED
        # Track campaign trace IDs for linking rounds to their campaign trace
        self._campaign_traces: dict[str, str] = {}

    # --- Internal helpers ---

    def _log_event(self, event: dict) -> None:
        """Append event to ``obs/langfuse/events.jsonl`` with auto-timestamp.

        Adapted from TermNorm ``langfuse_logger._log_event()``.
        Every public method calls this to maintain the flat navigation log.
        """
        event["timestamp"] = _utcnow_iso()
        _append_jsonl(self.obs_root / "langfuse" / "events.jsonl", event)

    def _write_trace(
        self,
        name: str,
        input_data: dict,
        output_data: dict | None = None,
        metadata: dict | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Write a Langfuse trace JSON file. Returns trace_id.

        Adapted from TermNorm ``langfuse_logger.create_trace()``.
        """
        trace_id = _generate_obs_id()
        trace = {
            "id": trace_id,
            "name": name,
            "timestamp": _utcnow_iso(),
            "input": input_data,
            "output": output_data,
            "metadata": metadata or {},
            "tags": tags or [],
        }
        _write_json(
            self.obs_root / "langfuse" / "traces" / f"{trace_id}.json",
            trace,
        )
        return trace_id

    def _write_observation(
        self,
        trace_id: str,
        obs_type: str,
        name: str,
        input_data: dict | None = None,
        output_data: dict | None = None,
        metadata: dict | None = None,
    ) -> str:
        """Write a Langfuse observation JSON file. Returns obs_id.

        Adapted from TermNorm ``langfuse_logger.create_observation()``.
        """
        obs_id = f"obs-{uuid.uuid4().hex[:12]}"
        now = _utcnow_iso()
        observation = {
            "id": obs_id,
            "trace_id": trace_id,
            "type": obs_type,
            "name": name,
            "start_time": now,
            "end_time": now,
            "input": input_data,
            "output": output_data,
            "metadata": metadata or {},
        }
        obs_dir = self.obs_root / "langfuse" / "observations" / trace_id
        _write_json(obs_dir / f"{obs_id}.json", observation)
        return obs_id

    def _write_score(
        self, trace_id: str, name: str, value: float, data_type: str = "NUMERIC"
    ) -> None:
        """Append a score to Langfuse scores JSONL.

        Adapted from TermNorm ``langfuse_logger.create_score()``.
        """
        score = {
            "id": f"score-{uuid.uuid4().hex[:8]}",
            "trace_id": trace_id,
            "name": name,
            "value": value,
            "data_type": data_type,
            "timestamp": _utcnow_iso(),
        }
        _append_jsonl(
            self.obs_root / "langfuse" / "scores" / f"{trace_id}.jsonl",
            score,
        )

    def _ensure_experiment(self, campaign_id: str) -> Path:
        """Create MLflow experiment directory + meta.yaml if not exists.

        Adapted from TermNorm ``ExperimentManager.create_experiment()``.
        Returns experiment directory path.
        """
        exp_dir = self.obs_root / "experiments" / campaign_id
        meta_path = exp_dir / "meta.yaml"
        if not meta_path.exists():
            now_ms = int(time.time() * 1000)
            _write_yaml(meta_path, {
                "experiment_id": campaign_id,
                "name": campaign_id,
                "artifact_location": str(exp_dir),
                "lifecycle_stage": "active",
                "creation_time": now_ms,
                "last_update_time": now_ms,
            })
        return exp_dir

    def _write_mlflow_run(
        self,
        experiment_dir: Path,
        run_name: str,
        params: dict[str, str],
        metrics: dict[str, float],
        tags: dict[str, str] | None = None,
    ) -> str:
        """Create an MLflow run directory with meta.yaml, params, metrics, tags.

        Adapted from TermNorm ``RunManager.start_run()`` + ``log_params()``
        + ``log_metrics()`` + ``set_tags()`` + ``end_run()``.
        Returns run_id.
        """
        run_id = _generate_obs_id()
        run_dir = experiment_dir / run_id
        now_ms = int(time.time() * 1000)

        # meta.yaml — MLflow RunInfo format
        _write_yaml(run_dir / "meta.yaml", {
            "run_id": run_id,
            "run_uuid": run_id,
            "run_name": run_name,
            "experiment_id": experiment_dir.name,
            "status": 3,  # FINISHED (MLflow numeric status)
            "start_time": now_ms,
            "end_time": now_ms,
            "lifecycle_stage": "active",
            "source_type": 4,  # LOCAL
            "source_name": "",
            "source_version": "",
            "entry_point_name": "",
            "user_id": "promptpotter",
            "artifact_uri": f"file:///{(run_dir / 'artifacts').as_posix()}",
            "tags": [],
        })

        # params/ — one file per param
        params_dir = run_dir / "params"
        params_dir.mkdir(parents=True, exist_ok=True)
        for key, value in params.items():
            (params_dir / key).write_text(str(value), encoding="utf-8")

        # metrics/ — one file per metric ("timestamp value step\n")
        metrics_dir = run_dir / "metrics"
        metrics_dir.mkdir(parents=True, exist_ok=True)
        timestamp_ms = now_ms
        for key, value in metrics.items():
            with open(metrics_dir / key, "a", encoding="utf-8") as f:
                f.write(f"{timestamp_ms} {value} 0\n")

        # tags/ — one file per tag
        if tags:
            tags_dir = run_dir / "tags"
            tags_dir.mkdir(parents=True, exist_ok=True)
            for key, value in tags.items():
                (tags_dir / key).write_text(str(value), encoding="utf-8")

        return run_id

    # --- Public API ---

    def log_dataset_run(
        self,
        run_id: str,
        content_hash: str,
        accuracy: float,
        total: int,
        hits: int,
        model: str,
        temperature: float,
        prompt_state_id: str = "",
    ) -> Path | None:
        """Write Langfuse trace + events.jsonl line for a completed eval run.

        Called from ``evaluate_prompt_cached()`` after finalization.
        """
        if not self._enabled:
            return None
        try:
            trace_id = self._write_trace(
                name="dataset_run",
                input_data={
                    "run_id": run_id,
                    "content_hash": content_hash,
                    "model": model,
                    "temperature": temperature,
                    "prompt_state_id": prompt_state_id,
                },
                output_data={
                    "accuracy": accuracy,
                    "hits": hits,
                    "total": total,
                },
                tags=["dataset_run"],
            )
            self._write_score(trace_id, "accuracy", accuracy)

            self._log_event({
                "event": "dataset_run",
                "trace_id": trace_id,
                "run_id": run_id,
                "content_hash": content_hash,
                "accuracy": accuracy,
                "hits": hits,
                "total": total,
                "model": model,
                "prompt_state_id": prompt_state_id,
            })

            trace_path = (
                self.obs_root / "langfuse" / "traces" / f"{trace_id}.json"
            )
            return trace_path
        except Exception:
            logger.debug("ObsLogger.log_dataset_run failed", exc_info=True)
            return None

    def log_campaign_start(
        self,
        campaign_id: str,
        config: dict,
        baseline_accuracy: float,
    ) -> Path | None:
        """Create MLflow experiment + Langfuse trace + events.jsonl for campaign start.

        Called from ``run_feedback_cycle()`` at campaign start.
        """
        if not self._enabled:
            return None
        try:
            # MLflow experiment
            self._ensure_experiment(campaign_id)

            # Langfuse trace
            trace_id = self._write_trace(
                name="feedback_cycle",
                input_data={
                    "campaign_id": campaign_id,
                    "baseline_accuracy": baseline_accuracy,
                    "config": config,
                },
                tags=["campaign", "feedback_cycle"],
            )
            self._campaign_traces[campaign_id] = trace_id
            self._write_score(trace_id, "baseline_accuracy", baseline_accuracy)

            self._log_event({
                "event": "campaign_start",
                "trace_id": trace_id,
                "campaign_id": campaign_id,
                "baseline_accuracy": baseline_accuracy,
            })

            trace_path = (
                self.obs_root / "langfuse" / "traces" / f"{trace_id}.json"
            )
            return trace_path
        except Exception:
            logger.debug(
                "ObsLogger.log_campaign_start failed", exc_info=True,
            )
            return None

    def log_round(
        self,
        campaign_id: str,
        round_num: int,
        accuracy: float,
        hits: int,
        total: int,
        improved: bool,
        next_action: str,
        winner_prompt_state_id: str,
        candidate_scores: list[dict],
        model: str = "",
        temperature: float = 0.0,
        n_variants: int = 0,
    ) -> Path | None:
        """Write observation + score + MLflow run + events.jsonl for one round.

        Called from ``run_feedback_cycle()`` after each optimization round.
        """
        if not self._enabled:
            return None
        try:
            trace_id = self._campaign_traces.get(campaign_id, "")

            # Langfuse observation (linked to campaign trace)
            if trace_id:
                self._write_observation(
                    trace_id=trace_id,
                    obs_type="span",
                    name=f"round_{round_num}",
                    input_data={
                        "round": round_num,
                        "candidates_evaluated": len(candidate_scores),
                    },
                    output_data={
                        "accuracy": accuracy,
                        "hits": hits,
                        "total": total,
                        "improved": improved,
                        "next_action": next_action,
                        "winner_prompt_state_id": winner_prompt_state_id,
                    },
                    metadata={"candidate_scores": candidate_scores},
                )
                self._write_score(trace_id, "accuracy", accuracy)

            # MLflow run
            exp_dir = self._ensure_experiment(campaign_id)
            run_name = f"round_{round_num}"
            params: dict[str, str] = {}
            if model:
                params["model"] = model
            params["temperature"] = str(temperature)
            if n_variants:
                params["n_variants"] = str(n_variants)
            params["round"] = str(round_num)

            self._write_mlflow_run(
                experiment_dir=exp_dir,
                run_name=run_name,
                params=params,
                metrics={
                    "accuracy": accuracy,
                    "hits": float(hits),
                    "total": float(total),
                },
                tags={
                    "improved": str(improved).lower(),
                    "next_action": next_action,
                    "winner_prompt_state_id": winner_prompt_state_id,
                },
            )

            self._log_event({
                "event": "round_complete",
                "trace_id": trace_id,
                "campaign_id": campaign_id,
                "round": round_num,
                "accuracy": accuracy,
                "hits": hits,
                "total": total,
                "improved": improved,
                "next_action": next_action,
                "winner_prompt_state_id": winner_prompt_state_id,
            })

            # Return the observation dir for this trace
            obs_dir = self.obs_root / "langfuse" / "observations" / trace_id
            return obs_dir if trace_id else None
        except Exception:
            logger.debug("ObsLogger.log_round failed", exc_info=True)
            return None

    def log_prompt_version(
        self,
        prompt_state_id: str,
        rendered_prompt: str,
        layer1_fields: dict,
        parent_id: str | None = None,
    ) -> Path | None:
        """Write prompt.txt + metadata.json + events.jsonl line.

        Adapted from TermNorm ``PromptRegistry.register_prompt()``.
        Family is always ``ranking_prompt``. Version is the PromptState ID prefix.
        """
        if not self._enabled:
            return None
        try:
            family = "ranking_prompt"
            version = prompt_state_id[:8] if prompt_state_id else "unknown"
            prompt_dir = self.obs_root / "prompts" / family / version

            # prompt.txt — rendered text
            prompt_dir.mkdir(parents=True, exist_ok=True)
            (prompt_dir / "prompt.txt").write_text(
                rendered_prompt, encoding="utf-8",
            )

            # metadata.json
            metadata = {
                "family": family,
                "version": version,
                "prompt_state_id": prompt_state_id,
                "parent_id": parent_id,
                "layer1_fields": layer1_fields,
                "created_at": _utcnow_iso(),
            }
            _write_json(prompt_dir / "metadata.json", metadata)

            self._log_event({
                "event": "prompt_version",
                "prompt_state_id": prompt_state_id,
                "family": family,
                "version": version,
                "parent_id": parent_id,
            })

            return prompt_dir / "prompt.txt"
        except Exception:
            logger.debug(
                "ObsLogger.log_prompt_version failed", exc_info=True,
            )
            return None
