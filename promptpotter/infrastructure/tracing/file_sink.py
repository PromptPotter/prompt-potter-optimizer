"""Per-cycle file sink — Langfuse-shape trace + observation JSON under ``campaigns/{cycle_id}/langfuse/``.

Append-only observation mirror: ``events.jsonl`` is never read back for
state reconstruction. Resume + fork are driven by ``rounds/round_NNNN.json``
via ``CampaignStore``.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, ClassVar

from promptpotter.infrastructure.store.io import (
    append_jsonl,
    read_json_optional,
    write_json,
    write_text,
)
from promptpotter.infrastructure.store.layout import cycle_dir_for
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
    dataset_item_id,
    generate_observation_id,
)
from promptpotter.shared.clock import utcnow_iso

logger = logging.getLogger(__name__)


class FileSink:
    """Append-only Langfuse-style file log per cycle."""

    def __init__(self, store_base_dir: str | Path, campaign_id: str = "") -> None:
        self._tenant_root = Path(store_base_dir)
        self._campaign_id = campaign_id
        self._cycle_id: str | None = None
        self._campaign_traces: dict[str, str] = {}
        self._round_observation_ids: dict[tuple[str, int], tuple[str, str]] = {}
        self._node_observations: dict[tuple[str, int, str], tuple[str, str]] = {}

    def _scope_dir(self) -> Path:
        if self._campaign_id and self._cycle_id:
            return cycle_dir_for(self._tenant_root, self._campaign_id, self._cycle_id)
        # Orphan fallback for out-of-campaign file_only() emits — tucked
        # under archive/obs/ so it doesn't compete with operator views.
        return self._tenant_root / "archive" / "obs"

    def _log_event(self, event: dict[str, Any]) -> None:
        event["timestamp"] = utcnow_iso()
        append_jsonl(self._scope_dir() / "langfuse" / "events.jsonl", event)

    def _write_trace(
        self,
        name: str,
        input_data: dict[str, Any],
        output_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> str:
        trace_id = generate_observation_id()
        trace = {
            "id": trace_id,
            "name": name,
            "timestamp": utcnow_iso(),
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
        as_type: str,
        name: str,
        input_data: dict[str, Any] | None = None,
        output_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        parent_observation_id: str | None = None,
    ) -> str:
        observation_id = f"obs-{uuid.uuid4().hex[:12]}"
        now = utcnow_iso()
        observation: dict[str, Any] = {
            "id": observation_id,
            "traceId": trace_id,
            "type": as_type,
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
        write_json(obs_dir / f"{observation_id}.json", observation)
        return observation_id

    def _write_score(
        self, trace_id: str, name: str, value: float, data_type: str = "NUMERIC"
    ) -> None:
        score = {
            "id": f"score-{uuid.uuid4().hex[:8]}",
            "traceId": trace_id,
            "name": name,
            "value": value,
            "dataType": data_type,
            "timestamp": utcnow_iso(),
        }
        append_jsonl(self._scope_dir() / "langfuse" / "scores" / f"{trace_id}.jsonl", score)

    def _finalize_observation(
        self,
        trace_id: str,
        observation_id: str,
        output: Any,
        metadata_extra: dict[str, Any] | None = None,
    ) -> None:
        obs_path = (
            self._scope_dir() / "langfuse" / "observations" / trace_id / f"{observation_id}.json"
        )
        obs_data = read_json_optional(obs_path)
        if obs_data is None:
            return
        obs_data["output"] = output
        obs_data["endTime"] = utcnow_iso()
        if metadata_extra:
            obs_data.setdefault("metadata", {}).update(metadata_extra)
        write_json(obs_path, obs_data)

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

        ds_dir = self._scope_dir() / "langfuse" / "datasets" / event.dataset_name
        ds_dir.mkdir(parents=True, exist_ok=True)
        n_registered = 0
        seen: set[str] = set()
        for query, ground_truth in event.items:
            if not query or query in seen:
                continue
            seen.add(query)
            item_id = dataset_item_id(event.dataset_name, query)
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
                "origin_accuracy": event.origin_accuracy,
                "config": event.config,
            },
            tags=["campaign", "optimization_loop"],
        )
        self._campaign_traces[event.campaign_id] = trace_id
        self._write_score(trace_id, "origin_accuracy", event.origin_accuracy)
        self._log_event(
            {
                "event": "campaign_start",
                "trace_id": trace_id,
                "campaign_id": event.campaign_id,
                "origin_accuracy": event.origin_accuracy,
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
            observation_id = self._write_observation(
                trace_id=trace_id,
                as_type="span",
                name=f"round_{event.round_num}",
                input_data={"round": event.round_num},
                metadata={"round": event.round_num},
            )
            self._round_observation_ids[(event.campaign_id, event.round_num)] = (
                trace_id,
                observation_id,
            )

    def on_node_start(self, event: NodeStart) -> None:
        trace_id = self._campaign_traces.get(event.campaign_id, "")
        if not trace_id:
            return
        round_ids = self._round_observation_ids.get((event.campaign_id, event.round_num))
        parent_observation_id = round_ids[1] if round_ids else None
        observation_id = self._write_observation(
            trace_id=trace_id,
            as_type=event.as_type,
            name=event.node_id,
            input_data=event.input_data,
            metadata={"node_type": event.node_type, **(event.metadata or {})},
            parent_observation_id=parent_observation_id,
        )
        self._node_observations[(event.campaign_id, event.round_num, event.node_id)] = (
            trace_id,
            observation_id,
        )
        self._log_event(
            {
                "event": "node_start",
                "trace_id": trace_id,
                "observation_id": observation_id,
                "node_id": event.node_id,
                "node_type": event.node_type,
            }
        )

    def on_node_end(self, event: NodeEnd) -> None:
        key = (event.campaign_id, event.round_num, event.node_id)
        ids = self._node_observations.pop(key, None)
        if ids is None:
            return
        trace_id, observation_id = ids
        meta_extra: dict[str, Any] = {}
        if event.metrics:
            meta_extra["metrics"] = event.metrics
        if event.error:
            meta_extra["error"] = event.error
        self._finalize_observation(trace_id, observation_id, event.output_data, meta_extra or None)
        self._log_event(
            {
                "event": "node_end",
                "trace_id": trace_id,
                "observation_id": observation_id,
                "node_id": event.node_id,
                "error": event.error,
            }
        )

    def on_round_end(self, event: RoundEnd) -> None:
        trace_id = self._campaign_traces.get(event.campaign_id, "")
        if trace_id:
            round_ids = self._round_observation_ids.pop((event.campaign_id, event.round_num), None)
            if round_ids is not None:
                _, observation_id = round_ids
                meta_extra: dict[str, Any] = {"candidate_scores": event.candidate_scores}
                if event.optimizer_templates:
                    meta_extra["optimizer_templates"] = event.optimizer_templates
                self._finalize_observation(
                    trace_id,
                    observation_id,
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
            "created_at": utcnow_iso(),
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
            trace_data = read_json_optional(trace_path)
            if trace_data is not None:
                trace_data["output"] = {
                    "best_accuracy": event.best_accuracy,
                    "n_l1_rounds": event.n_l1_rounds,
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
                "n_l1_rounds": event.n_l1_rounds,
                "stop_reason": event.stop_reason,
                "best_round": event.best_round,
            }
        )


__all__ = ["FileSink"]
