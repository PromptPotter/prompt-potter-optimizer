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

import hashlib
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
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def _append_jsonl(path: Path, data: dict) -> None:
    """Append one JSON line to a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")


# ---------------------------------------------------------------------------
# CloudObsBackend — all cloud Langfuse logic in one place
# ---------------------------------------------------------------------------


class CloudObsBackend:
    """Delegates observability events to cloud Langfuse.

    Each method mirrors an ObsLogger public method.  All methods are
    individually wrapped in try/except so cloud failures never propagate.
    """

    def __init__(self, langfuse):
        self._lf = langfuse
        self._trace_ids: dict[str, str] = {}
        self._active_trace_id: str | None = None
        self._active_session_id: str | None = None

    def on_dataset_registered(
        self,
        dataset_name: str,
        eval_data: list[dict],
        query_to_item_id: dict[str, str],
    ) -> dict[str, str]:
        """Push dataset + items to cloud. Returns updated query→item_id map."""
        try:
            self._lf.create_dataset(
                name=dataset_name,
                description="Ground truth queries for prompt evaluation",
                metadata={"n_items": len(eval_data)},
            )
            for entry in eval_data:
                query = entry.get("query", "")
                ground_truth = entry.get("ground_truth", "")
                if not query:
                    continue
                cloud_id = self._lf.create_dataset_item(
                    dataset_name=dataset_name,
                    input={"query": query},
                    expected_output=ground_truth,
                    metadata={"source": "eval_data"},
                )
                if cloud_id:
                    query_to_item_id[query] = cloud_id
        except Exception:
            logger.debug("Cloud Langfuse register_dataset failed", exc_info=True)
        return query_to_item_id

    def on_dataset_run(
        self,
        run_id: str,
        content_hash: str,
        accuracy: float,
        total: int,
        hits: int,
        model: str,
        temperature: float,
        prompt_state_id: str,
        dataset_name: str | None,
        dataset_item_map: dict[str, str] | None,
    ) -> None:
        """Push dataset_run as span (during campaign) or trace (standalone)."""
        try:
            cloud_trace_id: str | None = None
            if self._active_trace_id:
                self._lf.create_span(
                    trace_id=self._active_trace_id,
                    name=f"eval_{run_id}",
                    input={
                        "run_id": run_id,
                        "content_hash": content_hash,
                        "model": model,
                        "temperature": temperature,
                        "prompt_state_id": prompt_state_id,
                    },
                    output={
                        "accuracy": accuracy,
                        "hits": hits,
                        "total": total,
                    },
                )
                cloud_trace_id = self._active_trace_id
            else:
                cloud_id = self._lf.create_trace(
                    name="dataset_run",
                    input={
                        "run_id": run_id,
                        "content_hash": content_hash,
                        "model": model,
                        "temperature": temperature,
                        "prompt_state_id": prompt_state_id,
                    },
                    tags=["dataset_run"],
                )
                if cloud_id:
                    self._lf.create_score(
                        trace_id=cloud_id,
                        name="accuracy",
                        value=accuracy,
                    )
                    cloud_trace_id = cloud_id

            if cloud_trace_id and dataset_name and dataset_item_map:
                for query, item_id in dataset_item_map.items():
                    self._lf.link_item_to_run(
                        dataset_item_id=item_id,
                        trace_id=cloud_trace_id,
                        run_name=run_id,
                        run_metadata={
                            "accuracy": accuracy,
                            "prompt_state_id": prompt_state_id,
                        },
                    )
        except Exception:
            logger.debug("Cloud Langfuse dataset_run failed", exc_info=True)

    def on_campaign_start(
        self,
        campaign_id: str,
        config: dict,
        baseline_accuracy: float,
        session_id: str | None,
    ) -> None:
        """Create cloud trace for campaign, set active trace state."""
        try:
            cloud_id = self._lf.create_trace(
                name="feedback_cycle",
                input={
                    "campaign_id": campaign_id,
                    "baseline_accuracy": baseline_accuracy,
                    "config": config,
                },
                session_id=session_id,
                tags=["campaign", "feedback_cycle"],
            )
            if cloud_id:
                self._trace_ids[campaign_id] = cloud_id
                self._active_trace_id = cloud_id
                self._active_session_id = session_id
        except Exception:
            logger.debug("Cloud Langfuse campaign_start failed", exc_info=True)

    def on_round(
        self,
        campaign_id: str,
        round_num: int,
        accuracy: float,
        improved: bool,
        next_action: str,
        candidate_scores: list[dict],
    ) -> None:
        """Push round span + score to cloud."""
        cloud_trace_id = self._trace_ids.get(campaign_id)
        if not cloud_trace_id:
            return
        try:
            self._lf.create_span(
                trace_id=cloud_trace_id,
                name=f"round_{round_num}",
                input={
                    "n_candidates": len(candidate_scores),
                    "baseline_accuracy": accuracy,
                },
                output={
                    "winner_accuracy": accuracy,
                    "improved": improved,
                    "next_action": next_action,
                },
                metadata={
                    "round": round_num,
                    "candidates_evaluated": len(candidate_scores),
                },
            )
            self._lf.create_score(
                trace_id=cloud_trace_id,
                name=f"accuracy_round_{round_num}",
                value=accuracy,
                comment=f"Round {round_num}: "
                        f"{'improved' if improved else 'no change'}",
            )
        except Exception:
            logger.debug("Cloud Langfuse round failed", exc_info=True)

    def on_prompt_version(
        self,
        prompt_state_id: str,
        parent_id: str | None,
        family: str,
        version: str,
        layer1_fields: dict,
    ) -> None:
        """Attach prompt version span to most recent campaign trace."""
        if not self._trace_ids:
            return
        try:
            cloud_trace_id = next(reversed(self._trace_ids.values()))
            self._lf.create_span(
                trace_id=cloud_trace_id,
                name="prompt_version",
                input={
                    "prompt_state_id": prompt_state_id,
                    "parent_id": parent_id,
                },
                output={"family": family, "version": version},
                metadata={"layer1_fields": layer1_fields},
            )
        except Exception:
            logger.debug("Cloud Langfuse prompt_version failed", exc_info=True)

    def on_campaign_end(
        self,
        campaign_id: str,
        best_accuracy: float,
        n_rounds: int,
        stop_reason: str,
        best_round: int,
    ) -> None:
        """Finalize cloud trace: score + update + end. Clear active state."""
        cloud_trace_id = self._trace_ids.get(campaign_id)
        if cloud_trace_id:
            try:
                self._lf.create_score(
                    trace_id=cloud_trace_id,
                    name="best_accuracy",
                    value=best_accuracy,
                    comment=f"Best at round {best_round}, stop: {stop_reason}",
                )
                self._lf.update_trace(
                    trace_id=cloud_trace_id,
                    output={
                        "best_accuracy": best_accuracy,
                        "n_rounds": n_rounds,
                        "stop_reason": stop_reason,
                    },
                    metadata={
                        "stop_reason": stop_reason,
                        "best_round": best_round,
                    },
                )
                self._lf.end_trace(cloud_trace_id)
            except Exception:
                logger.debug("Cloud Langfuse campaign_end failed", exc_info=True)

        self._active_trace_id = None
        self._active_session_id = None

    def flush(self) -> None:
        """Flush cloud Langfuse."""
        try:
            self._lf.flush()
        except Exception:
            logger.debug("Cloud Langfuse flush failed", exc_info=True)

    def get_trace_id(self, campaign_id: str) -> str | None:
        """Return cloud trace ID for a campaign, or None."""
        return self._trace_ids.get(campaign_id)


# ---------------------------------------------------------------------------
# ObsLogger — file writes + thin cloud delegation
# ---------------------------------------------------------------------------


_UNSET = object()


class ObsLogger:
    """Single observability gateway — file writes + optional cloud Langfuse.

    Writes Langfuse-compatible traces + MLflow FileStore experiments +
    ``events.jsonl`` flat navigation log. Every public method also appends
    to ``events.jsonl`` — the human starting point for data exploration.

    When cloud Langfuse credentials are available, each public method also
    delegates to ``CloudObsBackend`` (file-first, cloud-second).
    Pass ``langfuse=None`` in tests to get file-only behaviour.

    All methods are no-ops when ``OBS_ENABLED = False``. All methods are
    wrapped in try/except to never crash the main flow.
    """

    def __init__(
        self,
        store_base_dir: str | Path,
        backend_id: str,
        langfuse: object = _UNSET,
    ):
        """Initialize ObsLogger.

        Args:
            store_base_dir: ProjectStore base directory — the path that already
                contains ``{backend_id}/`` subdirectories (e.g.
                ``.promptpotter/projects``).  Do NOT pass the project root.
            backend_id: Backend identifier (subdirectory name).
            langfuse: Cloud Langfuse logger instance.  Defaults to auto-detect
                via ``LangfuseLogger.get_instance()``.  Pass ``None`` for
                file-only mode (used in tests).
        """
        from api.config.settings import settings

        self.obs_root = Path(store_base_dir) / backend_id / "obs"
        self._enabled = settings.OBS_ENABLED
        self._campaign_traces: dict[str, str] = {}

        self._cloud: CloudObsBackend | None = None
        if langfuse is _UNSET:
            try:
                from api.services.obs.langfuse_client import LangfuseLogger
                lf = LangfuseLogger.get_instance()
                if lf.enabled:
                    self._cloud = CloudObsBackend(lf)
            except Exception:
                pass
        elif langfuse is not None:
            self._cloud = CloudObsBackend(langfuse)

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

    def register_dataset(
        self,
        dataset_name: str,
        eval_data: list[dict],
    ) -> dict[str, str]:
        """Register dataset items in file store and cloud Langfuse.

        Creates one dataset item per unique query in eval_data, setting
        ``expectedOutput`` to the ground truth label.

        Args:
            dataset_name: Langfuse dataset name (e.g. ``termnorm_ground_truth``).
            eval_data: List of dicts with ``query`` and ``ground_truth`` keys.

        Returns:
            Mapping of ``{query: item_id}`` for linking evaluations to items.
        """
        if not self._enabled:
            return {}

        query_to_item_id: dict[str, str] = {}

        try:
            ds_dir = self.obs_root / "langfuse" / "datasets" / dataset_name
            ds_dir.mkdir(parents=True, exist_ok=True)

            for entry in eval_data:
                query = entry.get("query", "")
                ground_truth = entry.get("ground_truth", "")
                if not query:
                    continue

                item_id = hashlib.sha256(
                    f"{dataset_name}:{query}".encode(),
                ).hexdigest()[:16]

                item_data = {
                    "id": item_id,
                    "dataset_name": dataset_name,
                    "input": {"query": query},
                    "expected_output": ground_truth,
                }
                _write_json(ds_dir / f"{item_id}.json", item_data)
                query_to_item_id[query] = item_id

            self._log_event({
                "event": "dataset_registered",
                "dataset_name": dataset_name,
                "n_items": len(query_to_item_id),
            })

            if self._cloud:
                query_to_item_id = self._cloud.on_dataset_registered(
                    dataset_name, eval_data, query_to_item_id,
                )

        except Exception:
            logger.warning("ObsLogger.register_dataset failed", exc_info=True)

        return query_to_item_id

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
        dataset_name: str | None = None,
        dataset_item_map: dict[str, str] | None = None,
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

            if self._cloud:
                self._cloud.on_dataset_run(
                    run_id, content_hash, accuracy, total, hits,
                    model, temperature, prompt_state_id,
                    dataset_name, dataset_item_map,
                )

            trace_path = (
                self.obs_root / "langfuse" / "traces" / f"{trace_id}.json"
            )
            return trace_path
        except Exception:
            logger.warning("ObsLogger.log_dataset_run failed", exc_info=True)
            return None

    def log_campaign_start(
        self,
        campaign_id: str,
        config: dict,
        baseline_accuracy: float,
        session_id: str | None = None,
    ) -> Path | None:
        """Create MLflow experiment + Langfuse trace + events.jsonl for campaign start.

        Called from ``run_feedback_cycle()`` at campaign start.

        Args:
            session_id: Optional Langfuse session ID for grouping cloud traces.
        """
        if not self._enabled:
            return None
        try:
            self._ensure_experiment(campaign_id)

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

            if self._cloud:
                self._cloud.on_campaign_start(
                    campaign_id, config, baseline_accuracy, session_id,
                )

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

            if self._cloud:
                self._cloud.on_round(
                    campaign_id, round_num, accuracy,
                    improved, next_action, candidate_scores,
                )

            obs_dir = self.obs_root / "langfuse" / "observations" / trace_id
            return obs_dir if trace_id else None
        except Exception:
            logger.warning("ObsLogger.log_round failed", exc_info=True)
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

            prompt_dir.mkdir(parents=True, exist_ok=True)
            (prompt_dir / "prompt.txt").write_text(
                rendered_prompt, encoding="utf-8",
            )

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

            if self._cloud:
                self._cloud.on_prompt_version(
                    prompt_state_id, parent_id, family, version, layer1_fields,
                )

            return prompt_dir / "prompt.txt"
        except Exception:
            logger.warning(
                "ObsLogger.log_prompt_version failed", exc_info=True,
            )
            return None

    def log_campaign_end(
        self,
        campaign_id: str,
        best_accuracy: float,
        n_rounds: int,
        stop_reason: str,
        best_round: int,
    ) -> None:
        """Finalize campaign: update file trace, write best_accuracy score, log event.

        Called from ``run_feedback_cycle()`` after the loop exits.
        """
        if not self._enabled:
            return
        try:
            trace_id = self._campaign_traces.get(campaign_id, "")

            if trace_id:
                trace_path = (
                    self.obs_root / "langfuse" / "traces" / f"{trace_id}.json"
                )
                if trace_path.exists():
                    trace_data = json.loads(
                        trace_path.read_text(encoding="utf-8"),
                    )
                    trace_data["output"] = {
                        "best_accuracy": best_accuracy,
                        "n_rounds": n_rounds,
                        "stop_reason": stop_reason,
                    }
                    _write_json(trace_path, trace_data)

                self._write_score(trace_id, "best_accuracy", best_accuracy)

            self._log_event({
                "event": "campaign_end",
                "trace_id": trace_id,
                "campaign_id": campaign_id,
                "best_accuracy": best_accuracy,
                "n_rounds": n_rounds,
                "stop_reason": stop_reason,
                "best_round": best_round,
            })

            if self._cloud:
                self._cloud.on_campaign_end(
                    campaign_id, best_accuracy, n_rounds, stop_reason, best_round,
                )
        except Exception:
            logger.warning("ObsLogger.log_campaign_end failed", exc_info=True)

    def flush(self) -> None:
        """Flush cloud Langfuse (file I/O is already synchronous)."""
        if self._cloud:
            self._cloud.flush()

    def get_cloud_trace_id(self, campaign_id: str) -> str | None:
        """Return cloud Langfuse trace ID for a campaign, or None."""
        if self._cloud:
            return self._cloud.get_trace_id(campaign_id)
        return None
