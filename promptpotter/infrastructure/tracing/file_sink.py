from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
from promptpotter.infrastructure.store.io import (
    append_jsonl,
    read_json_optional,
    write_json,
    write_text,
)
from promptpotter.infrastructure.store.layout import cycle_dir_for
from promptpotter.infrastructure.tracing.events import (
    CampaignStart,
    DatasetRegistered,
    TraceSink,
    dataset_item_id,
    generate_observation_id,
)
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.measurement_context import instrument_depth

if TYPE_CHECKING:
    from promptpotter.domain.run_records import (
        CandidateScoredRecord,
        RoundClosedRecord,
        RoundEnteredRecord,
        RunPhaseRecord,
    )


class FileSink(TraceSink):
    def __init__(self, store_base_dir: str | Path, hop: CycleHop) -> None:
        self._scope = cycle_dir_for(WorkspaceDir(Path(store_base_dir)), hop)
        self._campaign_traces: dict[str, str] = {}
        self._round_observation_ids: dict[tuple[str, int], tuple[str, str]] = {}
        self._node_observations: dict[tuple[str, int | None, str], tuple[str, str]] = {}

    def _log_event(self, event: dict[str, Any]) -> None:
        event["timestamp"] = utcnow_iso()
        append_jsonl(self._scope / "langfuse" / "events.jsonl", event)

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
        write_json(self._scope / "langfuse" / "traces" / f"{trace_id}.json", trace)
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
        # An inner cycle dumps no per-observation file (bulk, and the package's deepest path); the id is still returned.
        if not instrument_depth():
            obs_dir = self._scope / "langfuse" / "observations" / trace_id
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
        append_jsonl(self._scope / "langfuse" / "scores" / f"{trace_id}.jsonl", score)

    def _finalize_observation(
        self,
        trace_id: str,
        observation_id: str,
        output: Any,
        metadata_extra: dict[str, Any] | None = None,
    ) -> None:
        obs_path = self._scope / "langfuse" / "observations" / trace_id / f"{observation_id}.json"
        obs_data = read_json_optional(obs_path)
        if obs_data is None:
            return
        obs_data["output"] = output
        obs_data["endTime"] = utcnow_iso()
        if metadata_extra:
            obs_data.setdefault("metadata", {}).update(metadata_extra)
        write_json(obs_path, obs_data)

    def on_dataset_registered(self, event: DatasetRegistered) -> None:

        ds_dir = self._scope / "langfuse" / "datasets" / event.dataset_name
        ds_dir.mkdir(parents=True, exist_ok=True)
        for query, ground_truth in event.items:
            item_id = dataset_item_id(event.dataset_name, query)
            # Every launch registers the whole panel again, and rows are never re-cut under a used name.
            if (ds_dir / f"{item_id}.json").exists():
                continue
            item_data = {
                "id": item_id,
                "dataset_name": event.dataset_name,
                "input": {"query": query},
                "expected_output": ground_truth,
            }
            write_json(ds_dir / f"{item_id}.json", item_data)

        self._log_event(
            {
                "event": "dataset_registered",
                "dataset_name": event.dataset_name,
                "n_items": len(event.items),
            }
        )

    def on_campaign_start(self, event: CampaignStart) -> None:
        trace_id = self._write_trace(
            name="optimization_loop",
            input_data={"campaign_id": event.campaign_id, "config": event.config},
            tags=["campaign", "optimization_loop"],
        )
        self._campaign_traces[event.campaign_id] = trace_id
        self._log_event(
            {"event": "campaign_start", "trace_id": trace_id, "campaign_id": event.campaign_id}
        )

    def on_candidate_scored(self, campaign_id: str, record: CandidateScoredRecord) -> None:
        scores = record.scores
        walked = {
            "label": scores.label,
            "candidate_id": scores.candidate_id,
            "prompt_fields_id": scores.sp_hash,
        }
        accuracy = scores.accuracy
        trace_id = self._write_trace(
            name="dataset_run",
            input_data=walked,
            output_data={"accuracy": accuracy, "total": scores.total},
            tags=["dataset_run"],
        )
        if accuracy is not None:
            self._write_score(trace_id, "accuracy", accuracy)
        self._log_event(
            {
                "event": "dataset_run",
                "trace_id": trace_id,
                "round": record.round,
                **walked,
                "accuracy": accuracy,
                "total": scores.total,
            }
        )

    def on_round_entered(self, campaign_id: str, record: RoundEnteredRecord) -> None:
        trace_id = self._campaign_traces.get(campaign_id, "")
        if trace_id:
            observation_id = self._write_observation(
                trace_id=trace_id,
                as_type="span",
                name=f"round_{record.round}",
                input_data={"round": record.round},
                metadata={"round": record.round},
            )
            self._round_observation_ids[(campaign_id, record.round)] = (trace_id, observation_id)

    def on_span_open(
        self,
        campaign_id: str,
        round_num: int | None,
        span_id: str,
        *,
        name: str,
        node_type: str,
        as_type: str,
        metadata: dict[str, Any],
    ) -> None:
        trace_id = self._campaign_traces.get(campaign_id, "")
        if not trace_id:
            return
        round_ids = (
            None if round_num is None else self._round_observation_ids.get((campaign_id, round_num))
        )
        observation_id = self._write_observation(
            trace_id=trace_id,
            as_type=as_type,
            name=name,
            input_data={},
            metadata={"node_type": node_type, **metadata},
            parent_observation_id=round_ids[1] if round_ids else None,
        )
        self._node_observations[(campaign_id, round_num, span_id)] = (trace_id, observation_id)
        self._log_event(
            {
                "event": "node_start",
                "trace_id": trace_id,
                "observation_id": observation_id,
                "node_id": name,
                "node_type": node_type,
            }
        )

    def on_span_close(
        self, campaign_id: str, round_num: int | None, span_id: str, *, error: str | None
    ) -> None:
        ids = self._node_observations.pop((campaign_id, round_num, span_id), None)
        if ids is None:
            return
        trace_id, observation_id = ids
        self._finalize_observation(
            trace_id, observation_id, None, {"error": error} if error else None
        )
        self._log_event(
            {
                "event": "node_end",
                "trace_id": trace_id,
                "observation_id": observation_id,
                "error": error,
            }
        )

    def on_round_closed(self, campaign_id: str, record: RoundClosedRecord) -> None:
        trace_id = self._campaign_traces.get(campaign_id, "")
        winner = record.opt_sp
        winner_id = winner.id if winner is not None else ""
        if winner is not None:
            self._write_prompt_version(record)
        if trace_id:
            round_ids = self._round_observation_ids.pop((campaign_id, record.round), None)
            if round_ids is not None:
                _, observation_id = round_ids
                self._finalize_observation(
                    trace_id,
                    observation_id,
                    {
                        "accuracy": record.accuracy,
                        "total": record.total,
                        "improved": record.improved,
                        "winner_lineage_id": winner_id,
                    },
                    {"candidate_scores": [c.model_dump() for c in record.candidate_scores]},
                )
            if record.accuracy is not None:
                self._write_score(trace_id, "accuracy", record.accuracy)

        self._log_event(
            {
                "event": "round_complete",
                "trace_id": trace_id,
                "campaign_id": campaign_id,
                "round": record.round,
                "accuracy": record.accuracy,
                "total": record.total,
                "improved": record.improved,
                "winner_lineage_id": winner_id,
            }
        )

    def _write_prompt_version(self, record: RoundClosedRecord) -> None:
        winner = record.opt_sp
        assert winner is not None
        family = "target_prompt"
        version = winner.id[:8] if winner.id else "unknown"
        prompt_dir = self._scope / "prompts" / family / version
        write_text(prompt_dir / "prompt.txt", winner.render())
        parent_ids = list(winner.lineage.parent_ids)
        metadata = {
            "family": family,
            "version": version,
            "lineage_id": winner.id,
            "parent_ids": parent_ids,
            "layer1_fields": record.prompt_fields,
            "created_at": utcnow_iso(),
        }
        write_json(prompt_dir / "metadata.json", metadata)
        self._log_event(
            {
                "event": "prompt_version",
                "lineage_id": winner.id,
                "family": family,
                "version": version,
                "parent_ids": parent_ids,
            }
        )

    def on_run_stopped(
        self, campaign_id: str, record: RunPhaseRecord, *, rounds_closed: int
    ) -> None:
        trace_id = self._campaign_traces.get(campaign_id, "")
        ending = {
            "run_phase": record.run_phase.value,
            "stop_reason": record.stop_reason,
            "rounds_closed": rounds_closed,
        }
        if trace_id:
            trace_path = self._scope / "langfuse" / "traces" / f"{trace_id}.json"
            trace_data = read_json_optional(trace_path)
            if trace_data is not None:
                trace_data["output"] = ending
                write_json(trace_path, trace_data)
        self._log_event(
            {"event": "campaign_end", "trace_id": trace_id, "campaign_id": campaign_id, **ending}
        )


__all__ = ["FileSink"]
