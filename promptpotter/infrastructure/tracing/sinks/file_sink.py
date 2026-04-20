"""File sink — per-cycle Langfuse shadow, events.jsonl, prompts, MLflow (SDK).

Writes a Langfuse-compatible JSON shadow plus the ``events.jsonl`` navigation
log under ``campaigns/{cycle_id}/`` for the active campaign. MLflow is
opt-in: when ``settings.MLFLOW_ENABLED`` is true the sink logs each round as
an MLflow run via the Python SDK, rooted at ``library/mlruns/``. Writes
arriving without a bound cycle (out-of-campaign ``file_only()`` callers)
fall back to a shared ``library/obs/`` pool.
"""

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
    CritiqueWritten,
    DatasetRegistered,
    DatasetRun,
    Event,
    L2Applied,
    L3Applied,
    NodeEnd,
    NodeStart,
    PromptVersion,
    RoundEnd,
    RoundStart,
    RoundWinnerChosen,
)
from promptpotter.infrastructure.tracing.sinks.base import EventSink

logger = logging.getLogger(__name__)


def _generate_obs_id(length: int = 32) -> str:
    prefix = datetime.now(UTC).strftime("%y%m%d%H%M%S")
    suffix = uuid.uuid4().hex[: length - len(prefix)]
    return f"{prefix}{suffix}"


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class FileSink(EventSink):
    """Append-only Langfuse-style file log + per-cycle MLflow runs (opt-in)."""

    def __init__(self, store_base_dir: str | Path, backend_id: str) -> None:
        from promptpotter.config.settings import settings

        self._tenant_root = Path(store_base_dir)
        self._tenant_id = self._tenant_root.name
        self._library_dir = self._tenant_root / "library"
        self._backend_id = backend_id
        self._obs_enabled: bool = settings.OBS_ENABLED
        self._cycle_id: str | None = None
        self._mlflow_initialized = False
        self._campaign_traces: dict[str, str] = {}
        # Per-RoundStart obs_id so nested node observations can set it as
        # parentObservationId, and RoundEnd can update the same JSON file
        # rather than creating a second observation per round.
        # Keyed by (campaign_id, round_num); value is (trace_id, obs_id).
        self._round_obs_ids: dict[tuple[str, int], tuple[str, str]] = {}
        # Per-NodeStart obs_id so NodeEnd can update the same JSON file.
        # Keyed by (campaign_id, round_num, node_id).
        self._node_obs: dict[tuple[str, int, str], tuple[str, str]] = {}

    @property
    def enabled(self) -> bool:
        return self._obs_enabled

    # --- Scope resolution ---

    def _scope_dir(self) -> Path:
        if self._cycle_id:
            return self._tenant_root / "campaigns" / self._cycle_id
        # Orphan fallback: out-of-campaign file_only() emits share this pool.
        return self._library_dir / "obs"

    # --- Accessors ---

    def get_file_trace_id(self, campaign_id: str) -> str | None:
        return self._campaign_traces.get(campaign_id)

    # --- File helpers ---

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

    # --- Event dispatch ---
    # Topology B (QueryEvalStart / QueryNodeSpan / QueryEvalEnd) is
    # Langfuse-only — the file sink has no analogue for backfill replay.

    _HANDLERS: ClassVar[dict[type[Event], str]] = {
        DatasetRegistered: "_on_dataset_registered",
        CampaignStart: "_on_campaign_start",
        DatasetRun: "_on_dataset_run",
        RoundStart: "_on_round_start",
        NodeStart: "_on_node_start",
        NodeEnd: "_on_node_end",
        RoundEnd: "_on_round_end",
        PromptVersion: "_on_prompt_version",
        CampaignEnd: "_on_campaign_end",
        CandidateCreated: "_on_candidate_created",
        CandidateScored: "_on_candidate_scored",
        RoundWinnerChosen: "_on_round_winner_chosen",
        CritiqueWritten: "_on_critique_written",
        L2Applied: "_on_l2_applied",
        L3Applied: "_on_l3_applied",
    }

    # --- Write-point wrappers (route to shared _on_write_point) ---

    def _on_candidate_created(self, event: CandidateCreated) -> None:
        self._on_write_point(
            "candidate_created",
            event,
            extra={"candidate_idx": event.candidate_idx, "candidate_id": event.candidate_id},
        )

    def _on_candidate_scored(self, event: CandidateScored) -> None:
        self._on_write_point(
            "candidate_scored",
            event,
            extra={"candidate_idx": event.candidate_idx, "report": event.report},
        )

    def _on_round_winner_chosen(self, event: RoundWinnerChosen) -> None:
        self._on_write_point(
            "round_winner_chosen",
            event,
            extra={
                "winner_candidate_id": event.winner_candidate_id,
                "winner_accuracy": event.winner_accuracy,
                "improved": event.improved,
            },
        )

    def _on_critique_written(self, event: CritiqueWritten) -> None:
        self._on_write_point(
            "critique_written",
            event,
            extra={"critique_text": event.critique_text},
        )

    def _on_l2_applied(self, event: L2Applied) -> None:
        self._on_write_point(
            "l2_applied",
            event,
            extra={"changes_description": event.changes_description},
        )

    def _on_l3_applied(self, event: L3Applied) -> None:
        self._on_write_point(
            "l3_applied",
            event,
            extra={"changes_description": event.changes_description},
        )

    # --- Fork-addressable write-point handler (shared) ---

    def _on_write_point(self, event_name: str, event: Any, extra: dict[str, Any]) -> None:
        """Append a mid-round observability event to events.jsonl.

        events.jsonl is a pure observability mirror — metadata only, no
        OptSearchPoint snapshots. Resume reads
        ``campaigns/{cycle_id}/trials/trial_NNNN.json``.
        """
        trace_id = self._campaign_traces.get(event.campaign_id, "")
        payload = {
            "event": event_name,
            "trace_id": trace_id,
            "campaign_id": event.campaign_id,
            "round": event.round_num,
            **extra,
        }
        self._log_event(payload)

    # --- Optimization topology (A) handlers ---

    def _on_dataset_registered(self, event: DatasetRegistered) -> None:
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

    def _on_campaign_start(self, event: CampaignStart) -> None:
        # Bind cycle_id so subsequent writes target campaigns/{cycle_id}/.
        # session_id carries the resolved cycle_id (see runner:start_campaign).
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
            obs_id = self._write_observation(
                trace_id=trace_id,
                obs_type="span",
                name=f"round_{event.round_num}",
                input_data={"round": event.round_num},
                metadata={"round": event.round_num},
            )
            self._round_obs_ids[(event.campaign_id, event.round_num)] = (trace_id, obs_id)

    def _on_node_start(self, event: NodeStart) -> None:
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

    def _on_node_end(self, event: NodeEnd) -> None:
        key = (event.campaign_id, event.round_num, event.node_id)
        ids = self._node_obs.pop(key, None)
        if ids is None:
            return
        trace_id, obs_id = ids
        obs_dir = self._scope_dir() / "langfuse" / "observations" / trace_id
        obs_path = obs_dir / f"{obs_id}.json"
        if obs_path.exists():
            obs_data = json.loads(obs_path.read_text(encoding="utf-8"))
            obs_data["output"] = event.output_data
            obs_data["endTime"] = _utcnow_iso()
            if event.metrics:
                obs_data.setdefault("metadata", {})["metrics"] = event.metrics
            if event.error:
                obs_data.setdefault("metadata", {})["error"] = event.error
            write_json(obs_path, obs_data)
        node_end_payload: dict[str, Any] = {
            "event": "node_end",
            "trace_id": trace_id,
            "obs_id": obs_id,
            "node_id": event.node_id,
            "error": event.error,
        }
        self._log_event(node_end_payload)

    def _on_round_end(self, event: RoundEnd) -> None:
        trace_id = self._campaign_traces.get(event.campaign_id, "")
        if trace_id:
            round_ids = self._round_obs_ids.pop((event.campaign_id, event.round_num), None)
            if round_ids is not None:
                _, obs_id = round_ids
                obs_path = (
                    self._scope_dir() / "langfuse" / "observations" / trace_id / f"{obs_id}.json"
                )
                if obs_path.exists():
                    obs_data = json.loads(obs_path.read_text(encoding="utf-8"))
                    obs_data["output"] = {
                        "accuracy": event.accuracy,
                        "hits": event.hits,
                        "total": event.total,
                        "improved": event.improved,
                        "next_action": event.next_action,
                        "winner_prompt_fields_id": event.winner_prompt_fields_id,
                    }
                    obs_data["endTime"] = _utcnow_iso()
                    meta = obs_data.setdefault("metadata", {})
                    meta["candidate_scores"] = event.candidate_scores
                    if event.optimizer_templates:
                        meta["optimizer_templates"] = event.optimizer_templates
                    write_json(obs_path, obs_data)
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

    def _on_prompt_version(self, event: PromptVersion) -> None:
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

    def _on_campaign_end(self, event: CampaignEnd) -> None:
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
