from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
from promptpotter.infrastructure.store.io import append_jsonl, write_json, write_text
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

Document = dict[str, Any]


class FileSink(TraceSink):
    def __init__(self, store_base_dir: str | Path, hop: CycleHop) -> None:
        self._scope = cycle_dir_for(WorkspaceDir(Path(store_base_dir)), hop)
        self._campaign_traces: dict[str, Document] = {}
        self._round_observations: dict[tuple[str, int], Document] = {}
        self._node_observations: dict[tuple[str, int | None, str], Document] = {}

    def _trace_id(self, campaign_id: str) -> str:
        trace = self._campaign_traces.get(campaign_id)
        return "" if trace is None else str(trace["id"])

    def _log_event(self, event: Document) -> None:
        event["timestamp"] = utcnow_iso()
        append_jsonl(self._scope / "langfuse" / "events.jsonl", event)

    def _write_trace(
        self, name: str, input_data: Document, tags: list[str], output_data: Document | None = None
    ) -> Document:
        trace: Document = {
            "id": generate_observation_id(),
            "name": name,
            "timestamp": utcnow_iso(),
            "input": input_data,
            "output": output_data,
            "metadata": {},
            "tags": tags,
        }
        self._dump_trace(trace)
        return trace

    def _dump_trace(self, trace: Document) -> None:
        write_json(self._scope / "langfuse" / "traces" / f"{trace['id']}.json", trace)

    def _write_observation(
        self,
        trace_id: str,
        as_type: str,
        name: str,
        input_data: Document,
        metadata: Document,
        parent_observation_id: str | None = None,
    ) -> Document:
        now = utcnow_iso()
        observation: Document = {
            "id": f"obs-{uuid.uuid4().hex[:12]}",
            "traceId": trace_id,
            "type": as_type,
            "name": name,
            "startTime": now,
            "endTime": now,
            "input": input_data,
            "output": None,
            "metadata": metadata,
        }
        if parent_observation_id is not None:
            observation["parentObservationId"] = parent_observation_id
        self._dump_observation(observation)
        return observation

    def _dump_observation(self, observation: Document) -> None:
        # An inner cycle dumps no per-observation file (bulk, and the package's deepest path).
        if instrument_depth():
            return
        obs_dir = self._scope / "langfuse" / "observations" / observation["traceId"]
        write_json(obs_dir / f"{observation['id']}.json", observation)

    def _write_score(self, trace_id: str, name: str, value: float) -> None:
        score = {
            "id": f"score-{uuid.uuid4().hex[:8]}",
            "traceId": trace_id,
            "name": name,
            "value": value,
            "dataType": "NUMERIC",
            "timestamp": utcnow_iso(),
        }
        append_jsonl(self._scope / "langfuse" / "scores" / f"{trace_id}.jsonl", score)

    def _finalize_observation(
        self, observation: Document, output: Any, metadata_extra: Document | None
    ) -> None:
        observation["output"] = output
        observation["endTime"] = utcnow_iso()
        if metadata_extra:
            observation["metadata"].update(metadata_extra)
        self._dump_observation(observation)

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
        trace = self._write_trace(
            name="optimization_loop",
            input_data={"campaign_id": event.campaign_id, "config": event.config},
            tags=["campaign", "optimization_loop"],
        )
        self._campaign_traces[event.campaign_id] = trace
        self._log_event(
            {"event": "campaign_start", "trace_id": trace["id"], "campaign_id": event.campaign_id}
        )

    def on_candidate_scored(self, campaign_id: str, record: CandidateScoredRecord) -> None:
        scores = record.scores
        walked = {
            "label": scores.label,
            "candidate_id": scores.candidate_id,
            "prompt_fields_id": scores.sp_hash,
        }
        accuracy = scores.accuracy
        trace = self._write_trace(
            name="dataset_run",
            input_data=walked,
            tags=["dataset_run"],
            output_data={"accuracy": accuracy, "total": scores.total},
        )
        if accuracy is not None:
            self._write_score(trace["id"], "accuracy", accuracy)
        self._log_event(
            {
                "event": "dataset_run",
                "trace_id": trace["id"],
                "round": record.round,
                **walked,
                "accuracy": accuracy,
                "total": scores.total,
            }
        )

    def on_round_entered(self, campaign_id: str, record: RoundEnteredRecord) -> None:
        trace_id = self._trace_id(campaign_id)
        if trace_id:
            self._round_observations[(campaign_id, record.round)] = self._write_observation(
                trace_id=trace_id,
                as_type="span",
                name=f"round_{record.round}",
                input_data={"round": record.round},
                metadata={"round": record.round},
            )

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
        trace_id = self._trace_id(campaign_id)
        if not trace_id:
            return
        parent = (
            None if round_num is None else self._round_observations.get((campaign_id, round_num))
        )
        observation = self._write_observation(
            trace_id=trace_id,
            as_type=as_type,
            name=name,
            input_data={},
            metadata={"node_type": node_type, **metadata},
            parent_observation_id=None if parent is None else parent["id"],
        )
        self._node_observations[(campaign_id, round_num, span_id)] = observation
        self._log_event(
            {
                "event": "node_start",
                "trace_id": trace_id,
                "observation_id": observation["id"],
                "node_id": name,
                "node_type": node_type,
            }
        )

    def on_span_close(
        self, campaign_id: str, round_num: int | None, span_id: str, *, error: str | None
    ) -> None:
        observation = self._node_observations.pop((campaign_id, round_num, span_id), None)
        if observation is None:
            return
        self._finalize_observation(observation, None, {"error": error} if error else None)
        self._log_event(
            {
                "event": "node_end",
                "trace_id": observation["traceId"],
                "observation_id": observation["id"],
                "error": error,
            }
        )

    def on_round_closed(self, campaign_id: str, record: RoundClosedRecord) -> None:
        trace_id = self._trace_id(campaign_id)
        winner = record.opt_sp
        winner_id = winner.id if winner is not None else ""
        if winner is not None:
            self._write_prompt_version(record)
        if trace_id:
            observation = self._round_observations.pop((campaign_id, record.round), None)
            if observation is not None:
                self._finalize_observation(
                    observation,
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
        trace = self._campaign_traces.get(campaign_id)
        ending = {
            "run_phase": record.run_phase.value,
            "stop_reason": record.stop_reason,
            "rounds_closed": rounds_closed,
        }
        if trace is not None:
            trace["output"] = ending
            self._dump_trace(trace)
        self._log_event(
            {
                "event": "campaign_end",
                "trace_id": self._trace_id(campaign_id),
                "campaign_id": campaign_id,
                **ending,
            }
        )


__all__ = ["FileSink"]
