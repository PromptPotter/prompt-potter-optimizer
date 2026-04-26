"""Per-cycle Langfuse shadow + events.jsonl + prompts + (opt-in) MLflow under campaigns/{cycle_id}/."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from promptpotter.infrastructure.store.base import (
    append_jsonl,
    write_json,
    write_text,
)
from promptpotter.infrastructure.tracing.events import (
    CampaignEnd,
    CampaignStart,
    CandidateCreated,
    CandidateScored,
    DatasetRegistered,
    DatasetRun,
    L1CritiqueWritten,
    LayerApplied,
    NodeEnd,
    NodeStart,
    PromptVersion,
    RoundEnd,
    RoundStart,
    RoundWinnerChosen,
)

logger = logging.getLogger(__name__)


def _generate_obs_id(length: int = 32) -> str:
    prefix = datetime.now(UTC).strftime("%y%m%d%H%M%S")
    suffix = uuid.uuid4().hex[: length - len(prefix)]
    return f"{prefix}{suffix}"


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class FileSink:
    """Append-only Langfuse-style file log + per-cycle MLflow runs (opt-in)."""

    def __init__(self, store_base_dir: str | Path, backend_id: str) -> None:
        self._tenant_root = Path(store_base_dir)
        self._tenant_id = self._tenant_root.name
        self._library_dir = self._tenant_root / "library"
        self._backend_id = backend_id
        self._cycle_id: str | None = None
        self._mlflow_initialized = False
        self._campaign_traces: dict[str, str] = {}
        self._round_obs_ids: dict[tuple[str, int], tuple[str, str]] = {}
        self._node_obs: dict[tuple[str, int, str], tuple[str, str]] = {}

    def _scope_dir(self) -> Path:
        if self._cycle_id:
            return self._tenant_root / "campaigns" / self._cycle_id
        # Orphan fallback for out-of-campaign file_only() emits.
        return self._library_dir / "obs"

    def _log_event(self, event: dict) -> None:
        event["timestamp"] = _utcnow_iso()
        append_jsonl(self._scope_dir() / "events.jsonl", event)

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
        write_json(self._scope_dir() / "langfuse" / "traces" / f"{trace_id}.json", trace)
        return trace_id

    def _write_observation(
        self,
        trace_id: str,
        obs_type: str,
        name: str,
        input_data: dict | None = None,
        output_data: dict | None = None,
        metadata: dict | None = None,
        parent_observation_id: str | None = None,
    ) -> str:
        obs_id = f"obs-{uuid.uuid4().hex[:12]}"
        now = _utcnow_iso()
        observation: dict[str, Any] = {
            "id": obs_id,
            "traceId": trace_id,
            "type": obs_type,
            "name": name,
            "startTime": now,
            "endTime": now,
            "input": input_data,
            "output": output_data,
            "metadata": metadata or {},
        }
        if parent_observation_id is not None:
            observation["parentObservationId"] = parent_observation_id
        obs_dir = self._scope_dir() / "langfuse" / "observations" / trace_id
        write_json(obs_dir / f"{obs_id}.json", observation)
        return obs_id

    def _write_score(
        self, trace_id: str, name: str, value: float, data_type: str = "NUMERIC"
    ) -> None:
        score = {
            "id": f"score-{uuid.uuid4().hex[:8]}",
            "traceId": trace_id,
            "name": name,
            "value": value,
            "dataType": data_type,
            "timestamp": _utcnow_iso(),
        }
        append_jsonl(self._scope_dir() / "langfuse" / "scores" / f"{trace_id}.jsonl", score)

    def _finalize_observation(
        self,
        trace_id: str,
        obs_id: str,
        output: Any,
        metadata_extra: dict[str, Any] | None = None,
    ) -> None:
        obs_path = self._scope_dir() / "langfuse" / "observations" / trace_id / f"{obs_id}.json"
        if not obs_path.exists():
            return
        obs_data = json.loads(obs_path.read_text(encoding="utf-8"))
        obs_data["output"] = output
        obs_data["endTime"] = _utcnow_iso()
        if metadata_extra:
            obs_data.setdefault("metadata", {}).update(metadata_extra)
        write_json(obs_path, obs_data)

    # --- MLflow (opt-in via MLFLOW_ENABLED) ---

    def _log_mlflow_run(self, event: RoundEnd) -> None:
        from promptpotter.config.settings import settings

        if not settings.MLFLOW_ENABLED or not self._cycle_id:
            return
        import mlflow

        if not self._mlflow_initialized:
            tracking_uri = (self._library_dir / "mlruns").resolve().as_uri()
            mlflow.set_tracking_uri(tracking_uri)
            mlflow.set_experiment(name=f"{self._tenant_id}/{self._cycle_id}")
            self._mlflow_initialized = True

        params: dict[str, str] = {
            "round": str(event.round_num),
            "temperature": str(event.temperature),
        }
        if event.model:
            params["model"] = event.model
        if event.n_variants:
            params["n_variants"] = str(event.n_variants)

        metrics = {
            "accuracy": event.accuracy,
            "hits": float(event.hits),
            "total": float(event.total),
        }
        tags = {
            "improved": str(event.improved).lower(),
            "next_action": event.next_action,
            "winner_prompt_fields_id": event.winner_prompt_fields_id,
        }

        with mlflow.start_run(run_name=f"round_{event.round_num}"):
            mlflow.log_params(params)
            mlflow.log_metrics(metrics)
            mlflow.set_tags(tags)

    # Fields per event-class mirrored into events.jsonl.
    _WRITE_POINT_FIELDS: ClassVar[dict[type, tuple[str, tuple[str, ...]]]] = {
        CandidateCreated: ("candidate_created", ("candidate_idx", "candidate_id")),
        CandidateScored: ("candidate_scored", ("candidate_idx", "report")),
        RoundWinnerChosen: (
            "round_winner_chosen",
            ("winner_candidate_id", "winner_accuracy", "improved"),
        ),
        L1CritiqueWritten: ("l1_critique_written", ("l1_critique_text",)),
    }

    def on_write_point(self, event: Any) -> None:
        event_name, fields = self._WRITE_POINT_FIELDS[type(event)]
        payload = {
            "event": event_name,
            "trace_id": self._campaign_traces.get(event.campaign_id, ""),
            "campaign_id": event.campaign_id,
            "round": event.round_num,
            **{f: getattr(event, f) for f in fields},
        }
        self._log_event(payload)

    def on_layer_applied(self, event: LayerApplied) -> None:
        """Append an ``l2_applied`` / ``l3_applied`` mirror to events.jsonl.

        Event-name string (``l2_applied`` / ``l3_applied``) is preserved on
        disk — downstream consumers key on that string, not the dataclass.
        """
        self._log_event(
            {
                "event": f"{event.layer.lower()}_applied",
                "trace_id": self._campaign_traces.get(event.campaign_id, ""),
                "campaign_id": event.campaign_id,
                "round": event.round_num,
                "changes_description": event.changes_description,
            }
        )

    def on_dataset_registered(self, event: DatasetRegistered) -> None:
        import hashlib

        ds_dir = self._scope_dir() / "langfuse" / "datasets" / event.dataset_name
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

    def on_campaign_start(self, event: CampaignStart) -> None:
        # event.session_id carries the cycle_id; bind so writes target campaigns/{cycle_id}/.
        if event.session_id:
            self._cycle_id = event.session_id
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

    def on_dataset_run(self, event: DatasetRun) -> None:
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

    def on_round_start(self, event: RoundStart) -> None:
        trace_id = self._campaign_traces.get(event.campaign_id, "")
        if trace_id:
            obs_id = self._write_observation(
                trace_id=trace_id,
                obs_type="span",
                name=f"round_{event.round_num}",
                input_data={"round": event.round_num},
                metadata={"round": event.round_num},
            )
            self._round_obs_ids[(event.campaign_id, event.round_num)] = (trace_id, obs_id)

    def on_node_start(self, event: NodeStart) -> None:
        trace_id = self._campaign_traces.get(event.campaign_id, "")
        if not trace_id:
            return
        round_ids = self._round_obs_ids.get((event.campaign_id, event.round_num))
        parent_obs_id = round_ids[1] if round_ids else None
        obs_id = self._write_observation(
            trace_id=trace_id,
            obs_type=event.obs_type,
            name=event.node_id,
            input_data=event.input_data,
            metadata={"node_type": event.node_type, **(event.metadata or {})},
            parent_observation_id=parent_obs_id,
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

    def on_node_end(self, event: NodeEnd) -> None:
        key = (event.campaign_id, event.round_num, event.node_id)
        ids = self._node_obs.pop(key, None)
        if ids is None:
            return
        trace_id, obs_id = ids
        meta_extra: dict[str, Any] = {}
        if event.metrics:
            meta_extra["metrics"] = event.metrics
        if event.error:
            meta_extra["error"] = event.error
        self._finalize_observation(trace_id, obs_id, event.output_data, meta_extra or None)
        self._log_event(
            {
                "event": "node_end",
                "trace_id": trace_id,
                "obs_id": obs_id,
                "node_id": event.node_id,
                "error": event.error,
            }
        )

    def on_round_end(self, event: RoundEnd) -> None:
        trace_id = self._campaign_traces.get(event.campaign_id, "")
        if trace_id:
            round_ids = self._round_obs_ids.pop((event.campaign_id, event.round_num), None)
            if round_ids is not None:
                _, obs_id = round_ids
                meta_extra: dict[str, Any] = {"candidate_scores": event.candidate_scores}
                if event.optimizer_templates:
                    meta_extra["optimizer_templates"] = event.optimizer_templates
                self._finalize_observation(
                    trace_id,
                    obs_id,
                    {
                        "accuracy": event.accuracy,
                        "hits": event.hits,
                        "total": event.total,
                        "improved": event.improved,
                        "next_action": event.next_action,
                        "winner_prompt_fields_id": event.winner_prompt_fields_id,
                    },
                    meta_extra,
                )
            self._write_score(trace_id, "accuracy", event.accuracy)

        self._log_mlflow_run(event)

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

    def on_prompt_version(self, event: PromptVersion) -> None:
        family = "optimizer_prompt"
        version = event.prompt_fields_id[:8] if event.prompt_fields_id else "unknown"
        prompt_dir = self._scope_dir() / "prompts" / family / version
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

    def on_campaign_end(self, event: CampaignEnd) -> None:
        trace_id = self._campaign_traces.get(event.campaign_id, "")
        if trace_id:
            trace_path = self._scope_dir() / "langfuse" / "traces" / f"{trace_id}.json"
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
