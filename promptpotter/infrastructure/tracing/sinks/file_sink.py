"""File sink — writes Langfuse-compatible JSON + MLflow run dirs + events.jsonl.

Writes Langfuse-compatible JSON traces, MLflow run dirs, and the
``events.jsonl`` navigation log. The bridge dispatches events to
:meth:`handle`, which fans out to typed private
handlers. All file writes are local and synchronous; there is no cloud
state here.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from promptpotter.infrastructure.store.base import (
    append_jsonl,
    append_text,
    write_json,
    write_text,
    write_yaml_kv,
)
from promptpotter.infrastructure.tracing.events import (
    CampaignEnd,
    CampaignStart,
    DatasetRegistered,
    DatasetRun,
    Event,
    NodeEnd,
    NodeStart,
    PromptVersion,
    RoundEnd,
    RoundStart,
)

logger = logging.getLogger(__name__)


def _generate_obs_id(length: int = 32) -> str:
    prefix = datetime.now(UTC).strftime("%y%m%d%H%M%S")
    suffix = uuid.uuid4().hex[: length - len(prefix)]
    return f"{prefix}{suffix}"


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class FileSink:
    """Append-only Langfuse-style file log + per-campaign MLflow dirs."""

    def __init__(self, store_base_dir: str | Path, backend_id: str) -> None:
        from promptpotter.config.settings import settings

        self.obs_root = Path(store_base_dir) / backend_id / "obs"
        self._enabled: bool = settings.OBS_ENABLED
        self._campaign_traces: dict[str, str] = {}
        # Per-NodeStart obs_id so NodeEnd can update the same JSON file.
        # Keyed by (campaign_id, round_num, node_id).
        self._node_obs: dict[tuple[str, int, str], tuple[str, str]] = {}

    # --- Accessors ---

    def get_file_trace_id(self, campaign_id: str) -> str | None:
        return self._campaign_traces.get(campaign_id)

    # --- File helpers ---

    def _log_event(self, event: dict) -> None:
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
        write_json(self.obs_root / "langfuse" / "traces" / f"{trace_id}.json", trace)
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
        score = {
            "id": f"score-{uuid.uuid4().hex[:8]}",
            "trace_id": trace_id,
            "name": name,
            "value": value,
            "data_type": data_type,
            "timestamp": _utcnow_iso(),
        }
        append_jsonl(self.obs_root / "langfuse" / "scores" / f"{trace_id}.jsonl", score)

    def _ensure_experiment(self, campaign_id: str) -> Path:
        exp_dir = self.obs_root / "experiments" / campaign_id
        meta_path = exp_dir / "meta.yaml"
        if not meta_path.exists():
            now_ms = int(time.time() * 1000)
            write_yaml_kv(
                meta_path,
                {
                    "experiment_id": campaign_id,
                    "name": campaign_id,
                    "artifact_location": str(exp_dir),
                    "lifecycle_stage": "active",
                    "creation_time": now_ms,
                    "last_update_time": now_ms,
                },
            )
        return exp_dir

    def _write_mlflow_run(
        self,
        experiment_dir: Path,
        run_name: str,
        params: dict[str, str],
        metrics: dict[str, float],
        tags: dict[str, str] | None = None,
    ) -> str:
        run_id = _generate_obs_id()
        run_dir = experiment_dir / run_id
        now_ms = int(time.time() * 1000)

        write_yaml_kv(
            run_dir / "meta.yaml",
            {
                "run_id": run_id,
                "run_uuid": run_id,
                "run_name": run_name,
                "experiment_id": experiment_dir.name,
                "status": 3,
                "start_time": now_ms,
                "end_time": now_ms,
                "lifecycle_stage": "active",
                "source_type": 4,
                "source_name": "",
                "source_version": "",
                "entry_point_name": "",
                "user_id": "promptpotter",
                "artifact_uri": f"file:///{(run_dir / 'artifacts').as_posix()}",
                "tags": [],
            },
        )

        for key, value in params.items():
            write_text(run_dir / "params" / key, str(value))

        timestamp_ms = now_ms
        for metric_key, metric_value in metrics.items():
            append_text(run_dir / "metrics" / metric_key, f"{timestamp_ms} {metric_value} 0\n")

        if tags:
            for key, value in tags.items():
                write_text(run_dir / "tags" / key, str(value))

        return run_id

    # --- Event dispatch ---

    def handle(self, event: Event) -> None:
        if not self._enabled:
            return
        if isinstance(event, DatasetRegistered):
            self._on_dataset_registered(event)
        elif isinstance(event, CampaignStart):
            self._on_campaign_start(event)
        elif isinstance(event, DatasetRun):
            self._on_dataset_run(event)
        elif isinstance(event, RoundStart):
            self._on_round_start(event)
        elif isinstance(event, NodeStart):
            self._on_node_start(event)
        elif isinstance(event, NodeEnd):
            self._on_node_end(event)
        elif isinstance(event, RoundEnd):
            self._on_round_end(event)
        elif isinstance(event, PromptVersion):
            self._on_prompt_version(event)
        elif isinstance(event, CampaignEnd):
            self._on_campaign_end(event)
        # Topology B (QueryEvalStart / QueryNodeSpan / QueryEvalEnd) is
        # Langfuse-only — the file sink has no analogue for backfill replay.

    # --- Optimization topology (A) handlers ---

    def _on_dataset_registered(self, event: DatasetRegistered) -> None:
        import hashlib

        ds_dir = self.obs_root / "langfuse" / "datasets" / event.dataset_name
        ds_dir.mkdir(parents=True, exist_ok=True)
        n_registered = 0
        seen: set[str] = set()
        for query, ground_truth in event.items:
            if not query or query in seen:
                continue
            seen.add(query)
            item_id = hashlib.sha256(
                f"{event.dataset_name}:{query}".encode(),
            ).hexdigest()[:16]
            item_data = {
                "id": item_id,
                "dataset_name": event.dataset_name,
                "input": {"query": query},
                "expected_output": ground_truth,
            }
            write_json(ds_dir / f"{item_id}.json", item_data)
            n_registered += 1

        n_input = len(event.items)
        n_skipped = n_input - n_registered
        if n_skipped > 0:
            logger.debug(
                "Dataset '%s': %d items registered, %d duplicates/empty skipped (from %d input)",
                event.dataset_name,
                n_registered,
                n_skipped,
                n_input,
            )

        self._log_event(
            {
                "event": "dataset_registered",
                "dataset_name": event.dataset_name,
                "n_items": n_registered,
                "n_input": n_input,
                "n_skipped": n_skipped,
            }
        )

    def _on_campaign_start(self, event: CampaignStart) -> None:
        self._ensure_experiment(event.campaign_id)
        trace_id = self._write_trace(
            name="optimization_loop",
            input_data={
                "campaign_id": event.campaign_id,
                "baseline_accuracy": event.baseline_accuracy,
                "config": event.config,
            },
            tags=["campaign", "optimization_loop"],
        )
        self._campaign_traces[event.campaign_id] = trace_id
        self._write_score(trace_id, "baseline_accuracy", event.baseline_accuracy)
        self._log_event(
            {
                "event": "campaign_start",
                "trace_id": trace_id,
                "campaign_id": event.campaign_id,
                "baseline_accuracy": event.baseline_accuracy,
            }
        )

    def _on_dataset_run(self, event: DatasetRun) -> None:
        trace_id = self._write_trace(
            name="dataset_run",
            input_data={
                "run_id": event.run_id,
                "content_hash": event.content_hash,
                "prompt_fields_id": event.prompt_fields_id,
            },
            output_data={
                "accuracy": event.accuracy,
                "hits": event.hits,
                "total": event.total,
            },
            tags=["dataset_run"],
        )
        self._write_score(trace_id, "accuracy", event.accuracy)
        self._log_event(
            {
                "event": "dataset_run",
                "trace_id": trace_id,
                "run_id": event.run_id,
                "content_hash": event.content_hash,
                "accuracy": event.accuracy,
                "hits": event.hits,
                "total": event.total,
                "prompt_fields_id": event.prompt_fields_id,
            }
        )

    def _on_round_start(self, event: RoundStart) -> None:
        trace_id = self._campaign_traces.get(event.campaign_id, "")
        if trace_id:
            self._write_observation(
                trace_id=trace_id,
                obs_type="span",
                name=f"round_{event.round_num}_start",
                input_data={"round": event.round_num},
            )

    def _on_node_start(self, event: NodeStart) -> None:
        trace_id = self._campaign_traces.get(event.campaign_id, "")
        if not trace_id:
            return
        obs_id = self._write_observation(
            trace_id=trace_id,
            obs_type=event.obs_type,
            name=event.node_id,
            input_data=event.input_data,
            metadata={"node_type": event.node_type, **(event.metadata or {})},
        )
        self._node_obs[(event.campaign_id, event.round_num, event.node_id)] = (trace_id, obs_id)
        self._log_event(
            {
                "event": "node_start",
                "trace_id": trace_id,
                "obs_id": obs_id,
                "node_id": event.node_id,
                "node_type": event.node_type,
            }
        )

    def _on_node_end(self, event: NodeEnd) -> None:
        key = (event.campaign_id, event.round_num, event.node_id)
        ids = self._node_obs.pop(key, None)
        if ids is None:
            return
        trace_id, obs_id = ids
        obs_dir = self.obs_root / "langfuse" / "observations" / trace_id
        obs_path = obs_dir / f"{obs_id}.json"
        if obs_path.exists():
            obs_data = json.loads(obs_path.read_text(encoding="utf-8"))
            obs_data["output"] = event.output_data
            obs_data["end_time"] = _utcnow_iso()
            if event.metrics:
                obs_data.setdefault("metadata", {})["metrics"] = event.metrics
            if event.error:
                obs_data.setdefault("metadata", {})["error"] = event.error
            write_json(obs_path, obs_data)
        self._log_event(
            {
                "event": "node_end",
                "trace_id": trace_id,
                "obs_id": obs_id,
                "node_id": event.node_id,
                "error": event.error,
            }
        )

    def _on_round_end(self, event: RoundEnd) -> None:
        trace_id = self._campaign_traces.get(event.campaign_id, "")
        if trace_id:
            metadata: dict[str, Any] = {"candidate_scores": event.candidate_scores}
            if event.optimizer_templates:
                metadata["optimizer_templates"] = event.optimizer_templates
            self._write_observation(
                trace_id=trace_id,
                obs_type="span",
                name=f"round_{event.round_num}",
                input_data={
                    "round": event.round_num,
                    "candidates_scored": len(event.candidate_scores),
                },
                output_data={
                    "accuracy": event.accuracy,
                    "hits": event.hits,
                    "total": event.total,
                    "improved": event.improved,
                    "next_action": event.next_action,
                    "winner_prompt_fields_id": event.winner_prompt_fields_id,
                },
                metadata=metadata,
            )
            self._write_score(trace_id, "accuracy", event.accuracy)

        exp_dir = self._ensure_experiment(event.campaign_id)
        params: dict[str, str] = {}
        if event.model:
            params["model"] = event.model
        params["temperature"] = str(event.temperature)
        if event.n_variants:
            params["n_variants"] = str(event.n_variants)
        params["round"] = str(event.round_num)
        self._write_mlflow_run(
            experiment_dir=exp_dir,
            run_name=f"round_{event.round_num}",
            params=params,
            metrics={
                "accuracy": event.accuracy,
                "hits": float(event.hits),
                "total": float(event.total),
            },
            tags={
                "improved": str(event.improved).lower(),
                "next_action": event.next_action,
                "winner_prompt_fields_id": event.winner_prompt_fields_id,
            },
        )

        log_entry: dict[str, Any] = {
            "event": "round_complete",
            "trace_id": trace_id,
            "campaign_id": event.campaign_id,
            "round": event.round_num,
            "accuracy": event.accuracy,
            "hits": event.hits,
            "total": event.total,
            "improved": event.improved,
            "next_action": event.next_action,
            "winner_prompt_fields_id": event.winner_prompt_fields_id,
        }
        if event.optimizer_templates:
            log_entry["optimizer_templates"] = event.optimizer_templates
        self._log_event(log_entry)

    def _on_prompt_version(self, event: PromptVersion) -> None:
        family = "optimizer_prompt"
        version = event.prompt_fields_id[:8] if event.prompt_fields_id else "unknown"
        prompt_dir = self.obs_root / "prompts" / family / version
        write_text(prompt_dir / "prompt.txt", event.rendered_prompt)
        metadata = {
            "family": family,
            "version": version,
            "prompt_fields_id": event.prompt_fields_id,
            "parent_id": event.parent_id,
            "layer1_fields": event.layer1_fields,
            "created_at": _utcnow_iso(),
        }
        write_json(prompt_dir / "metadata.json", metadata)
        self._log_event(
            {
                "event": "prompt_version",
                "prompt_fields_id": event.prompt_fields_id,
                "family": family,
                "version": version,
                "parent_id": event.parent_id,
            }
        )

    def _on_campaign_end(self, event: CampaignEnd) -> None:
        trace_id = self._campaign_traces.get(event.campaign_id, "")
        if trace_id:
            trace_path = self.obs_root / "langfuse" / "traces" / f"{trace_id}.json"
            if trace_path.exists():
                trace_data = json.loads(trace_path.read_text(encoding="utf-8"))
                trace_data["output"] = {
                    "best_accuracy": event.best_accuracy,
                    "n_rounds": event.n_rounds,
                    "stop_reason": event.stop_reason,
                }
                write_json(trace_path, trace_data)
            self._write_score(trace_id, "best_accuracy", event.best_accuracy)

        self._log_event(
            {
                "event": "campaign_end",
                "trace_id": trace_id,
                "campaign_id": event.campaign_id,
                "best_accuracy": event.best_accuracy,
                "n_rounds": event.n_rounds,
                "stop_reason": event.stop_reason,
                "best_round": event.best_round,
            }
        )

    def flush(self) -> None:
        # Disk writes are synchronous; nothing to do.
        return
