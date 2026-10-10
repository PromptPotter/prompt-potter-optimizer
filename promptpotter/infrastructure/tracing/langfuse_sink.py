from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
from promptpotter.infrastructure.store.io import read_json_optional, write_json
from promptpotter.infrastructure.store.layout import cycle_dir_for
from promptpotter.infrastructure.tracing.events import (
    CampaignStart,
    DatasetRegistered,
    QueryNodeSpan,
    QueryScoreEnd,
    QueryScoreStart,
    TraceSink,
)
from promptpotter.infrastructure.tracing.langfuse_client import LangfuseLogger

if TYPE_CHECKING:
    from promptpotter.domain.run_records import (
        CandidateScoredRecord,
        RoundClosedRecord,
        RoundEnteredRecord,
        RunPhaseRecord,
    )

logger = logging.getLogger(__name__)


class LangfuseSink(TraceSink):
    def __init__(
        self,
        store_base_dir: str | Path,
        campaign_id: str,
        langfuse: LangfuseLogger,
    ) -> None:
        self._base = WorkspaceDir(Path(store_base_dir))
        self._campaign_id = campaign_id
        self._lf = langfuse

        self._trace_ids: dict[str, str] = {}
        self._round_observation_ids: dict[tuple[str, int], str] = {}
        self._node_observation_ids: dict[tuple[str, int | None, str], str] = {}
        self._dataset_item_ids: dict[tuple[str, str], str] = {}
        self._session_ids: dict[str, str] = {}
        self._query_trace_ids: dict[tuple[str, str], tuple[str, str, str]] = {}

        self._state_cycle_id: str | None = None
        self._state_path: Path | None = None

    def _bind_cycle(self, cycle_id: str) -> None:
        if self._state_cycle_id == cycle_id:
            return
        self._state_cycle_id = cycle_id
        hop = CycleHop(campaign_id=self._campaign_id, cycle_id=cycle_id)
        self._state_path = cycle_dir_for(self._base, hop) / "langfuse" / "state.json"
        existing = read_json_optional(self._state_path)
        if existing:
            self._trace_ids.update(existing.get("trace_ids", {}))
            self._session_ids.update(existing.get("session_ids", {}))
            for key, value in (existing.get("round_observation_ids") or {}).items():
                cid, rn = key.rsplit("|", 1)
                self._round_observation_ids[(cid, int(rn))] = value
            for key, value in (existing.get("node_observation_ids") or {}).items():
                cid, rn, nid = key.split("|", 2)
                self._node_observation_ids[(cid, int(rn) if rn else None, nid)] = value
            for key, value in (existing.get("dataset_item_ids") or {}).items():
                dsname, query = key.split("|", 1)
                self._dataset_item_ids[(dsname, query)] = value

    def _persist(self) -> None:
        if self._state_path is None:
            return
        state: dict[str, Any] = {
            "trace_ids": dict(self._trace_ids),
            "session_ids": dict(self._session_ids),
            "round_observation_ids": {
                f"{cid}|{rn}": v for (cid, rn), v in self._round_observation_ids.items()
            },
            "node_observation_ids": {
                f"{cid}|{'' if rn is None else rn}|{nid}": v
                for (cid, rn, nid), v in self._node_observation_ids.items()
            },
            "dataset_item_ids": {
                f"{dsname}|{query}": v for (dsname, query), v in self._dataset_item_ids.items()
            },
        }
        write_json(self._state_path, state)

    def get_langfuse_trace_id(self, campaign_id: str) -> str | None:
        return self._trace_ids.get(campaign_id)

    def on_campaign_start(self, event: CampaignStart) -> None:
        self._bind_cycle(event.cycle_id)
        cloud_id = self._lf.create_trace(
            name="optimization_loop",
            input={"campaign_id": event.campaign_id, "config": event.config},
            session_id=event.session_id,
            tags=["campaign", "optimization_loop"],
        )
        if cloud_id:
            self._trace_ids[event.campaign_id] = cloud_id
            if event.session_id:
                self._session_ids[event.campaign_id] = event.session_id
            self._persist()

    def on_dataset_registered(self, event: DatasetRegistered) -> None:
        if len(event.items) > 100:
            logger.warning(
                "Skipping Langfuse cloud dataset registration for %d items "
                "(rate-limit risk). Use the dedicated Langfuse sync cell instead.",
                len(event.items),
            )
            return

        # One `update`, never key-by-key: a concurrent `_persist` must not see the dict mid-growth.
        def _register() -> None:
            self._lf.create_dataset(
                name=event.dataset_name,
                description="Ground truth queries for prompt evaluation",
                metadata={"n_items": len(event.items)},
            )
            minted: dict[tuple[str, str], str] = {}
            for query, ground_truth in event.items:
                cloud_id = self._lf.create_dataset_item(
                    dataset_name=event.dataset_name,
                    input={"query": query},
                    expected_output=ground_truth,
                    metadata={"source": "dataset"},
                )
                if cloud_id:
                    minted[(event.dataset_name, query)] = cloud_id
            self._dataset_item_ids.update(minted)
            self._persist()

        threading.Thread(target=_register, name="langfuse-dataset", daemon=True).start()

    def on_candidate_scored(self, campaign_id: str, record: CandidateScoredRecord) -> None:
        trace_id = self._trace_ids.get(campaign_id)
        if not trace_id:
            return
        scores = record.scores
        self._lf.create_span(
            trace_id=trace_id,
            name=f"walk_{scores.label}",
            input={"candidate_id": scores.candidate_id, "prompt_fields_id": scores.sp_hash},
            output={"accuracy": scores.accuracy, "total": scores.total},
            parent_observation_id=self._round_observation_ids.get((campaign_id, record.round)),
            as_type="span",
        )

    def on_round_entered(self, campaign_id: str, record: RoundEnteredRecord) -> None:
        trace_id = self._trace_ids.get(campaign_id)
        if not trace_id:
            return
        observation_id = self._lf.start_span(
            trace_id=trace_id,
            name=f"round_{record.round}",
            input={"round": record.round},
            metadata={"round": record.round},
            as_type="span",
        )
        if observation_id:
            self._round_observation_ids[(campaign_id, record.round)] = observation_id

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
        trace_id = self._trace_ids.get(campaign_id)
        if not trace_id:
            return
        observation_id = self._lf.start_span(
            trace_id=trace_id,
            name=name,
            input={},
            metadata={"node_type": node_type, **metadata},
            parent_observation_id=(
                None
                if round_num is None
                else self._round_observation_ids.get((campaign_id, round_num))
            ),
            as_type=as_type,
        )
        if observation_id:
            self._node_observation_ids[(campaign_id, round_num, span_id)] = observation_id

    def on_span_close(
        self, campaign_id: str, round_num: int | None, span_id: str, *, error: str | None
    ) -> None:
        observation_id = self._node_observation_ids.pop((campaign_id, round_num, span_id), None)
        if not observation_id:
            return
        self._lf.end_observation(
            observation_id, output=None, metadata={"error": error} if error else None
        )

    def on_round_closed(self, campaign_id: str, record: RoundClosedRecord) -> None:
        trace_id = self._trace_ids.get(campaign_id)
        if not trace_id:
            return
        round_observation_id = self._round_observation_ids.pop((campaign_id, record.round), None)
        winner = record.opt_sp
        if winner is not None:
            self._lf.create_span(
                trace_id=trace_id,
                name="prompt_version",
                input={"lineage_id": winner.id, "parent_ids": list(winner.lineage.parent_ids)},
                output={
                    "family": "target_prompt",
                    "version": winner.id[:8] if winner.id else "unknown",
                },
                metadata={"layer1_fields": record.prompt_fields},
                parent_observation_id=round_observation_id,
                as_type="span",
            )
        if round_observation_id:
            self._lf.end_observation(
                round_observation_id,
                output={
                    "winner_accuracy": record.accuracy,
                    "improved": record.improved,
                    "candidates_scored": record.candidates_scored,
                },
                metadata={"round": record.round, "candidates_scored": record.candidates_scored},
            )
        if record.accuracy is not None:
            self._lf.create_score(
                trace_id=trace_id,
                name=f"accuracy_round_{record.round}",
                value=record.accuracy,
                comment=f"Round {record.round}: {'improved' if record.improved else 'no change'}",
            )
        for ev_name, ev_value in record.evaluators.items():
            self._lf.create_score(
                trace_id=trace_id, name=f"{ev_name}_round_{record.round}", value=float(ev_value)
            )

    def on_run_stopped(
        self, campaign_id: str, record: RunPhaseRecord, *, rounds_closed: int
    ) -> None:
        trace_id = self._trace_ids.get(campaign_id)
        if not trace_id:
            return
        self._lf.update_trace(
            trace_id=trace_id,
            output={
                "run_phase": record.run_phase.value,
                "stop_reason": record.stop_reason,
                "rounds_closed": rounds_closed,
            },
            metadata={"stop_reason": record.stop_reason},
        )
        self._lf.end_trace(trace_id)
        self._persist()

    def on_query_score_start(self, event: QueryScoreStart) -> None:
        if event.session_id:
            self._bind_cycle(event.session_id)
        metadata: dict[str, Any] = {
            "run_id": event.run_id,
            "llm_provider": event.llm_provider,
            "prompt_fields_id": event.prompt_fields_id,
        }
        if event.pipeline_params:
            metadata["pipeline_params"] = event.pipeline_params
        trace_name = f"{event.schema_name}_pipeline" if event.schema_name else "pipeline"
        trace_id = self._lf.create_trace(
            name=trace_name,
            input={"query": event.query, "expected_output": event.ground_truth},
            session_id=event.session_id,
            tags=["query", event.origin, "pipeline"],
            metadata=metadata,
        )
        if trace_id:
            self._query_trace_ids[(event.run_id, event.query)] = (
                trace_id,
                event.dataset_name,
                event.origin,
            )

    def on_query_node_span(self, event: QueryNodeSpan) -> None:
        entry = self._query_trace_ids.get((event.run_id, event.query))
        if not entry:
            return
        trace_id = entry[0]
        self._lf.create_span(
            trace_id,
            event.node_name,
            event.input_data,
            event.output_data,
            event.metadata,
            as_type=event.as_type,
            model=event.model,
            usage_details=event.usage_details,
        )

    def on_query_score_end(self, event: QueryScoreEnd) -> None:
        entry = self._query_trace_ids.pop((event.run_id, event.query), None)
        if not entry:
            return
        trace_id, dataset_name, origin = entry
        self._lf.create_score(trace_id, "hit", 1.0 if event.hit else 0.0)
        trace_output: dict[str, Any] = {
            "predicted": event.predicted,
            "expected_output": event.ground_truth,
            "hit": event.hit,
            "total_time": event.total_time,
        }
        trace_output.update(event.node_outputs)
        self._lf.update_trace(trace_id, output=trace_output)
        self._lf.end_trace(trace_id)
        if not self._lf.rate_limited:
            item_id = self._dataset_item_ids.get((dataset_name, event.query))
            if item_id:
                self._lf.link_item_to_run(
                    dataset_item_id=item_id,
                    trace_id=trace_id,
                    run_name=event.run_id,
                    run_metadata={"origin": origin},
                )

    def flush(self) -> None:
        self._lf.flush()

    def reconcile_dataset(
        self,
        dataset_name: str,
        gt_map: dict[str, str],
        seed_items: dict[str, str] | None = None,
    ) -> dict[str, str]:
        query_to_item_id: dict[str, str] = dict(seed_items or {})

        ok = self._lf.create_dataset(
            name=dataset_name,
            description="Production ground truth queries for prompt evaluation",
            metadata={"n_samples": len(gt_map)},
        )
        if not ok:
            raise RuntimeError(
                f"Langfuse: failed to create/access dataset '{dataset_name}'. "
                "Check LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY in .env."
            )

        existing_items: dict[str, Any] = {}
        ds = self._lf.get_dataset(dataset_name)
        if ds and hasattr(ds, "items"):
            for it in ds.items:
                input_data = getattr(it, "input", None) or {}
                q = input_data.get("query", "") if isinstance(input_data, dict) else ""
                if q:
                    existing_items[q] = it

        for query, ground_truth in gt_map.items():
            if query in query_to_item_id:
                existing = existing_items.get(query)
                if existing and getattr(existing, "expected_output", None) is None:
                    self._lf.update_dataset_item(
                        item_id=existing.id,
                        expected_output=ground_truth,
                    )
                continue

            existing = existing_items.get(query)
            if existing:
                item_id = existing.id
                if getattr(existing, "expected_output", None) is None:
                    self._lf.update_dataset_item(
                        item_id=item_id,
                        expected_output=ground_truth,
                    )
            else:
                item_id = self._lf.create_dataset_item(
                    dataset_name=dataset_name,
                    input={"query": query},
                    expected_output=ground_truth,
                    metadata={"source": "dataset"},
                )
            if item_id:
                query_to_item_id[query] = item_id
                self._dataset_item_ids[(dataset_name, query)] = item_id

        self._persist()
        return query_to_item_id


__all__ = ["LangfuseSink"]
