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

Cloud Langfuse delegation is handled by ``CloudDelegate`` (see ``cloud_delegate.py``).

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
from datetime import datetime, timezone
from pathlib import Path

from api.services.obs.cloud_delegate import CloudDelegate
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

    Cloud Langfuse operations are delegated to ``CloudDelegate``.
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

        # Cloud Langfuse delegate (None = file-only mode)
        self._cloud: CloudDelegate | None = None

        if langfuse is _UNSET:
            try:
                from api.services.obs.langfuse_client import LangfuseLogger
                lf = LangfuseLogger.get_instance()
                if lf.enabled:
                    self._cloud = CloudDelegate(lf)
            except Exception:
                logger.debug("Cloud backend init failed; file-only mode", exc_info=True)
        elif langfuse is not None:
            self._cloud = CloudDelegate(langfuse)

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
        """Write a Langfuse trace JSON file. Returns trace_id."""
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
        """Write a Langfuse observation JSON file. Returns obs_id."""
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
        """Append a score to Langfuse scores JSONL."""
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
        """Create MLflow experiment directory + meta.yaml if not exists."""
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
        """Create an MLflow run directory with meta.yaml, params, metrics, tags."""
        run_id = _generate_obs_id()
        run_dir = experiment_dir / run_id
        now_ms = int(time.time() * 1000)

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

        for key, value in params.items():
            write_text(run_dir / "params" / key, str(value))

        timestamp_ms = now_ms
        for key, value in metrics.items():
            append_text(run_dir / "metrics" / key, f"{timestamp_ms} {value} 0\n")

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
        """Register dataset items in file store and cloud Langfuse."""
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

            if self._cloud:
                cloud_ids = self._cloud.on_register_dataset(dataset_name, eval_data)
                query_to_item_id.update(cloud_ids)

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
        """Write Langfuse trace + events.jsonl line for a completed eval run."""
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

            if self._cloud:
                self._cloud.on_dataset_run(
                    run_id, content_hash, accuracy, hits, total, prompt_fields_id,
                )

            return self.obs_root / "langfuse" / "traces" / f"{trace_id}.json"
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
        """Create MLflow experiment + Langfuse trace + events.jsonl for campaign start."""
        if not self._enabled:
            return None
        try:
            self._ensure_experiment(campaign_id)

            trace_id = self._write_trace(
                name="optimization_loop",
                input_data={
                    "campaign_id": campaign_id,
                    "baseline_accuracy": baseline_accuracy,
                    "config": config,
                },
                tags=["campaign", "optimization_loop"],
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

            return self.obs_root / "langfuse" / "traces" / f"{trace_id}.json"
        except Exception:
            logger.debug("ObsLogger.log_campaign_start failed", exc_info=True)
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
        """Open an observation for a node step. Returns obs_id for closing later."""
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

            if self._cloud:
                self._cloud.on_node_start(
                    node_id, node_type, obs_type, input_data, metadata,
                )

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
        """Close a node step observation with output and metrics."""
        if not self._enabled:
            return
        try:
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

            if self._cloud:
                self._cloud.on_node_end(node_id, output_data, metrics, error)
        except Exception:
            logger.warning("ObsLogger.log_node_end failed", exc_info=True)

    def log_round_start(
        self,
        campaign_id: str,
        round_num: int,
    ) -> None:
        """Open a round observation (file + cloud)."""
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

            if self._cloud:
                self._cloud.on_round_start(campaign_id, round_num)
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
        """Close a round: file observation + score + MLflow run + events.jsonl + cloud."""
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

            if self._cloud:
                self._cloud.on_round_end(
                    campaign_id, round_num, accuracy, improved,
                    next_action, candidate_scores, optimizer_templates,
                )

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
        """Write prompt.txt + metadata.json + events.jsonl line."""
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

            if self._cloud:
                self._cloud.on_prompt_version(
                    prompt_fields_id, layer1_fields, parent_id,
                )

            return prompt_dir / "prompt.txt"
        except Exception:
            logger.warning("ObsLogger.log_prompt_version failed", exc_info=True)
            return None

    def log_campaign_end(
        self,
        campaign_id: str,
        best_accuracy: float,
        n_rounds: int,
        stop_reason: str,
        best_round: int,
    ) -> None:
        """Finalize campaign: update file trace, write best_accuracy score, log event."""
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
