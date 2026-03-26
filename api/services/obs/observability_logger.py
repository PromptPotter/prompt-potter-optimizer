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
    obs.log_round_start(campaign_id, round_num)
    obs.log_prompt_version(prompt_fields_id, rendered_prompt, layer1_fields)
"""

import hashlib
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from api.services.stores.base import (
    append_jsonl, append_text, write_json, write_text, write_yaml_kv,
)

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
    pushes to cloud Langfuse (file-first, cloud-second).
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

        # Cloud Langfuse state (inlined from former CloudObsBackend)
        self._lf = None
        self._cloud_trace_ids: dict[str, str] = {}
        self._cloud_active_trace_id: str | None = None
        self._cloud_active_session_id: str | None = None
        self._cloud_active_round_obs_id: str | None = None
        self._cloud_active_step_obs_ids: dict[str, str] = {}

        if langfuse is _UNSET:
            try:
                from api.services.obs.langfuse_client import LangfuseLogger
                lf = LangfuseLogger.get_instance()
                if lf.enabled:
                    self._lf = lf
            except Exception:
                logger.debug("Cloud backend init failed; file-only mode", exc_info=True)
        elif langfuse is not None:
            self._lf = langfuse

    # --- Internal helpers ---

    def _log_event(self, event: dict) -> None:
        """Append event to ``obs/langfuse/events.jsonl`` with auto-timestamp.

        Adapted from TermNorm ``langfuse_logger._log_event()``.
        Every public method calls this to maintain the flat navigation log.
        """
        event["timestamp"] = _utcnow_iso()
        append_jsonl(self.obs_root / "langfuse" / "events.jsonl", event)

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
        write_json(
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
        write_json(obs_dir / f"{obs_id}.json", observation)
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
        append_jsonl(
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
            write_yaml_kv(meta_path, {
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
        write_yaml_kv(run_dir / "meta.yaml", {
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
        for key, value in params.items():
            write_text(run_dir / "params" / key, str(value))

        # metrics/ — one file per metric ("timestamp value step\n")
        timestamp_ms = now_ms
        for key, value in metrics.items():
            append_text(run_dir / "metrics" / key, f"{timestamp_ms} {value} 0\n")

        # tags/ — one file per tag
        if tags:
            for key, value in tags.items():
                write_text(run_dir / "tags" / key, str(value))

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
                write_json(ds_dir / f"{item_id}.json", item_data)
                query_to_item_id[query] = item_id

            n_skipped = len(eval_data) - len(query_to_item_id)
            if n_skipped > 0:
                logger.info(
                    "Dataset '%s': %d items registered, %d duplicates/empty "
                    "skipped (from %d input)",
                    dataset_name, len(query_to_item_id), n_skipped, len(eval_data),
                )

            self._log_event({
                "event": "dataset_registered",
                "dataset_name": dataset_name,
                "n_items": len(query_to_item_id),
                "n_input": len(eval_data),
                "n_skipped": n_skipped,
            })

            if self._lf:
                if len(eval_data) > 100:
                    logger.warning(
                        "Skipping Langfuse cloud dataset registration for %d items "
                        "(rate-limit risk). Use the dedicated Langfuse sync cell instead.",
                        len(eval_data),
                    )
                else:
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
        prompt_fields_id: str = "",
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
                    "prompt_fields_id": prompt_fields_id,
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
                "prompt_fields_id": prompt_fields_id,
            })

            if self._lf:
                try:
                    if self._cloud_active_trace_id:
                        self._lf.create_span(
                            trace_id=self._cloud_active_trace_id,
                            name=f"eval_{run_id[:8]}",
                            input={
                                "run_id": run_id,
                                "content_hash": content_hash,
                                "prompt_fields_id": prompt_fields_id,
                            },
                            output={
                                "accuracy": accuracy,
                                "hits": hits,
                                "total": total,
                            },
                            parent_observation_id=self._cloud_active_round_obs_id,
                            as_type="tool",
                        )
                except Exception:
                    logger.debug("Cloud Langfuse dataset_run failed", exc_info=True)

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

            if self._lf:
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
                        self._cloud_trace_ids[campaign_id] = cloud_id
                        self._cloud_active_trace_id = cloud_id
                        self._cloud_active_session_id = session_id
                except Exception:
                    logger.debug("Cloud Langfuse campaign_start failed", exc_info=True)

            trace_path = (
                self.obs_root / "langfuse" / "traces" / f"{trace_id}.json"
            )
            return trace_path
        except Exception:
            logger.debug(
                "ObsLogger.log_campaign_start failed", exc_info=True,
            )
            return None

    def get_file_trace_id(self, campaign_id: str) -> str | None:
        """Return file trace ID for a campaign, or None."""
        return self._campaign_traces.get(campaign_id)

    def log_node_start(
        self,
        trace_id: str,
        node_id: str,
        node_type: str,
        obs_type: str,
        input_data: dict,
        metadata: dict | None = None,
    ) -> str | None:
        """Open an observation for a node step. Returns obs_id for closing later.

        Reuses ``_write_observation()`` for file, ``_log_event()`` for events,
        and cloud Langfuse for traces.
        """
        if not self._enabled:
            return None
        try:
            obs_id = self._write_observation(
                trace_id=trace_id,
                obs_type=obs_type,
                name=node_id,
                input_data=input_data,
                metadata={"node_type": node_type, **(metadata or {})},
            )

            self._log_event({
                "event": "node_start",
                "trace_id": trace_id,
                "obs_id": obs_id,
                "node_id": node_id,
                "node_type": node_type,
            })

            if self._lf:
                try:
                    if self._cloud_active_trace_id:
                        as_type = obs_type if obs_type in ("generation", "span") else "span"
                        cloud_obs_id = self._lf.start_span(
                            trace_id=self._cloud_active_trace_id,
                            name=node_id,
                            input=input_data,
                            metadata={"node_type": node_type, **(metadata or {})},
                            parent_observation_id=self._cloud_active_round_obs_id,
                            as_type=as_type,
                        )
                        if cloud_obs_id:
                            self._cloud_active_step_obs_ids[node_id] = cloud_obs_id
                except Exception:
                    logger.debug("Cloud Langfuse node_start failed", exc_info=True)

            return obs_id
        except Exception:
            logger.warning("ObsLogger.log_node_start failed", exc_info=True)
            return None

    def log_node_end(
        self,
        obs_id: str,
        trace_id: str,
        node_id: str,
        output_data: dict | None = None,
        metrics: dict | None = None,
        error: str | None = None,
    ) -> None:
        """Close a node step observation with output and metrics.

        Updates the file observation JSON in place, appends to events.jsonl,
        and delegates to cloud Langfuse.
        """
        if not self._enabled:
            return
        try:
            # Update file observation with output + end_time
            obs_dir = self.obs_root / "langfuse" / "observations" / trace_id
            obs_path = obs_dir / f"{obs_id}.json"
            if obs_path.exists():
                obs_data = json.loads(obs_path.read_text(encoding="utf-8"))
                obs_data["output"] = output_data
                obs_data["end_time"] = _utcnow_iso()
                if metrics:
                    obs_data.setdefault("metadata", {})["metrics"] = metrics
                if error:
                    obs_data.setdefault("metadata", {})["error"] = error
                write_json(obs_path, obs_data)

            self._log_event({
                "event": "node_end",
                "trace_id": trace_id,
                "obs_id": obs_id,
                "node_id": node_id,
                "error": error,
            })

            if self._lf:
                try:
                    cloud_obs_id = self._cloud_active_step_obs_ids.pop(node_id, None)
                    if cloud_obs_id:
                        meta: dict = {}
                        if metrics:
                            meta["metrics"] = metrics
                        if error:
                            meta["error"] = error
                        self._lf.end_observation(
                            cloud_obs_id, output=output_data, metadata=meta or None,
                        )
                except Exception:
                    logger.debug("Cloud Langfuse node_end failed", exc_info=True)
        except Exception:
            logger.warning("ObsLogger.log_node_end failed", exc_info=True)

    def log_round_start(
        self,
        campaign_id: str,
        round_num: int,
    ) -> None:
        """Open a round observation (file + cloud). Call log_round_end() when done.

        Called from ``_execute_round()`` before candidate generation.
        """
        if not self._enabled:
            return
        try:
            trace_id = self._campaign_traces.get(campaign_id, "")
            if trace_id:
                self._write_observation(
                    trace_id=trace_id,
                    obs_type="span",
                    name=f"round_{round_num}_start",
                    input_data={"round": round_num},
                )

            if self._lf:
                try:
                    cloud_trace_id = self._cloud_trace_ids.get(campaign_id)
                    if cloud_trace_id:
                        obs_id = self._lf.start_span(
                            trace_id=cloud_trace_id,
                            name=f"round_{round_num}",
                            input={"round": round_num},
                            metadata={"round": round_num},
                            as_type="span",
                        )
                        self._cloud_active_round_obs_id = obs_id
                except Exception:
                    logger.debug("Cloud Langfuse round_start failed", exc_info=True)
        except Exception:
            logger.warning("ObsLogger.log_round_start failed", exc_info=True)

    def log_round_end(
        self,
        campaign_id: str,
        round_num: int,
        accuracy: float,
        hits: int,
        total: int,
        improved: bool,
        next_action: str,
        winner_prompt_fields_id: str,
        candidate_scores: list[dict],
        model: str = "",
        temperature: float = 0.0,
        n_variants: int = 0,
        optimizer_templates: list[str] | None = None,
    ) -> Path | None:
        """Close a round: file observation + score + MLflow run + events.jsonl + cloud.

        Called from ``_execute_round()`` after evaluation completes.
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
                        "winner_prompt_fields_id": winner_prompt_fields_id,
                    },
                    metadata={
                        "candidate_scores": candidate_scores,
                        **({"optimizer_templates": optimizer_templates}
                           if optimizer_templates else {}),
                    },
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
                    "winner_prompt_fields_id": winner_prompt_fields_id,
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
                "winner_prompt_fields_id": winner_prompt_fields_id,
                **({"optimizer_templates": optimizer_templates}
                   if optimizer_templates else {}),
            })

            if self._lf:
                try:
                    cloud_trace_id = self._cloud_trace_ids.get(campaign_id)
                    if cloud_trace_id:
                        if self._cloud_active_round_obs_id:
                            round_meta: dict = {
                                "round": round_num,
                                "candidates_evaluated": len(candidate_scores),
                            }
                            if optimizer_templates:
                                round_meta["optimizer_templates"] = optimizer_templates
                            self._lf.end_observation(
                                self._cloud_active_round_obs_id,
                                output={
                                    "winner_accuracy": accuracy,
                                    "improved": improved,
                                    "next_action": next_action,
                                    "candidates_evaluated": len(candidate_scores),
                                },
                                metadata=round_meta,
                            )
                        self._lf.create_score(
                            trace_id=cloud_trace_id,
                            name=f"accuracy_round_{round_num}",
                            value=accuracy,
                            comment=f"Round {round_num}: "
                                    f"{'improved' if improved else 'no change'}",
                        )
                except Exception:
                    logger.debug("Cloud Langfuse round_end failed", exc_info=True)
                finally:
                    self._cloud_active_round_obs_id = None

            obs_dir = self.obs_root / "langfuse" / "observations" / trace_id
            return obs_dir if trace_id else None
        except Exception:
            logger.warning("ObsLogger.log_round_end failed", exc_info=True)
            return None

    def log_prompt_version(
        self,
        prompt_fields_id: str,
        rendered_prompt: str,
        layer1_fields: dict,
        parent_id: str | None = None,
    ) -> Path | None:
        """Write prompt.txt + metadata.json + events.jsonl line.

        Adapted from TermNorm ``PromptRegistry.register_prompt()``.
        Family is always ``ranking_prompt``. Version is the OptSearchPoint ID prefix.
        """
        if not self._enabled:
            return None
        try:
            family = "ranking_prompt"
            version = prompt_fields_id[:8] if prompt_fields_id else "unknown"
            prompt_dir = self.obs_root / "prompts" / family / version

            write_text(prompt_dir / "prompt.txt", rendered_prompt)

            metadata = {
                "family": family,
                "version": version,
                "prompt_fields_id": prompt_fields_id,
                "parent_id": parent_id,
                "layer1_fields": layer1_fields,
                "created_at": _utcnow_iso(),
            }
            write_json(prompt_dir / "metadata.json", metadata)

            self._log_event({
                "event": "prompt_version",
                "prompt_fields_id": prompt_fields_id,
                "family": family,
                "version": version,
                "parent_id": parent_id,
            })

            if self._lf:
                try:
                    if self._cloud_trace_ids:
                        cloud_trace_id = next(reversed(self._cloud_trace_ids.values()))
                        self._lf.create_span(
                            trace_id=cloud_trace_id,
                            name="prompt_version",
                            input={
                                "prompt_fields_id": prompt_fields_id,
                                "parent_id": parent_id,
                            },
                            output={"family": family, "version": version},
                            metadata={"layer1_fields": layer1_fields},
                            parent_observation_id=self._cloud_active_round_obs_id,
                            as_type="tool",
                        )
                except Exception:
                    logger.debug("Cloud Langfuse prompt_version failed", exc_info=True)

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
                    write_json(trace_path, trace_data)

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

            if self._lf:
                try:
                    cloud_trace_id = self._cloud_trace_ids.get(campaign_id)
                    if cloud_trace_id:
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
                finally:
                    self._cloud_active_trace_id = None
                    self._cloud_active_session_id = None
        except Exception:
            logger.warning("ObsLogger.log_campaign_end failed", exc_info=True)

    def flush(self) -> None:
        """Flush cloud Langfuse (file I/O is already synchronous)."""
        if self._lf:
            try:
                self._lf.flush()
            except Exception:
                logger.debug("Cloud Langfuse flush failed", exc_info=True)

    def get_cloud_trace_id(self, campaign_id: str) -> str | None:
        """Return cloud Langfuse trace ID for a campaign, or None."""
        return self._cloud_trace_ids.get(campaign_id)


# ---------------------------------------------------------------------------
# Node-level tracing (merged from node_tracer.py)
# ---------------------------------------------------------------------------


@dataclass
class NodeTrace:
    """Mutable bag for node output and metrics, yielded by ``observed_node``."""

    output: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    error: str | None = None


@asynccontextmanager
async def observed_node(
    node_id: str,
    node_type: str,
    obs: ObsLogger | None = None,
    trace_id: str | None = None,
    obs_type: str = "generation",
):
    """Async context manager for node-level timing + observability.

    Captures wall-clock duration and optionally writes Langfuse-compatible
    observations via ``obs.log_node_start/end``.  Non-fatal: observability
    failures are logged as warnings and never crash the caller.

    Yields:
        NodeTrace with ``.output`` (set by caller), ``.duration_ms``, ``.error``.
    """
    trace = NodeTrace()
    obs_id: str | None = None

    if obs and trace_id:
        try:
            obs_id = obs.log_node_start(
                trace_id=trace_id,
                node_id=node_id,
                node_type=node_type,
                obs_type=obs_type,
                input_data={},
            )
        except Exception:
            logger.warning("observed_node start failed for %s", node_id, exc_info=True)

    t0 = time.perf_counter()
    try:
        yield trace
    except Exception as exc:
        trace.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        trace.duration_ms = (time.perf_counter() - t0) * 1000
        if obs and trace_id and obs_id:
            try:
                obs.log_node_end(
                    obs_id=obs_id,
                    trace_id=trace_id,
                    node_id=node_id,
                    output_data=trace.output,
                    metrics={"duration_ms": trace.duration_ms},
                    error=trace.error,
                )
            except Exception:
                logger.warning("observed_node end failed for %s", node_id, exc_info=True)
