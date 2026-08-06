"""Langfuse cloud sink — owns the trace/observation id maps and persists
them to ``campaigns/{cycle_id}/langfuse/state.json`` so CLI-interrupted
resumes produce one continuous trace.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
from promptpotter.infrastructure.store.io import read_json_optional, write_json
from promptpotter.infrastructure.store.layout import cycle_dir_for
from promptpotter.infrastructure.tracing.events import (
    CampaignEnd,
    CampaignStart,
    DatasetRegistered,
    DatasetRun,
    NodeEnd,
    NodeStart,
    PromptVersion,
    QueryNodeSpan,
    QueryScoreEnd,
    QueryScoreStart,
    RoundEnd,
    RoundStart,
)
from promptpotter.infrastructure.tracing.langfuse_client import LangfuseLogger

logger = logging.getLogger(__name__)


class LangfuseSink:
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
        self._node_observation_ids: dict[tuple[str, int, str], str] = {}
        self._dataset_item_ids: dict[tuple[str, str], str] = {}
        self._session_ids: dict[str, str] = {}
        # (run_id, query) → (trace_id, dataset_name, origin) — Topology B in-flight.
        self._query_trace_ids: dict[tuple[str, str], tuple[str, str, str]] = {}

        # Resolved lazily on first CampaignStart (session_id lives there).
        self._state_session_id: str | None = None
        self._state_path: Path | None = None

    def _session_state_path(self, session_id: str) -> Path:
        # session_id == cycle_id here; langfuse state lives in the cycle dir.
        hop = CycleHop(campaign_id=self._campaign_id, cycle_id=session_id)
        return cycle_dir_for(self._base, hop) / "langfuse" / "state.json"

    def _bind_session(self, session_id: str) -> None:
        if self._state_session_id == session_id:
            return
        self._state_session_id = session_id
        self._state_path = self._session_state_path(session_id)
        existing = read_json_optional(self._state_path)
        if existing:
            self._trace_ids.update(existing.get("trace_ids", {}))
            self._session_ids.update(existing.get("session_ids", {}))
            for key, value in (existing.get("round_observation_ids") or {}).items():
                cid, rn = key.rsplit("|", 1)
                self._round_observation_ids[(cid, int(rn))] = value
            for key, value in (existing.get("node_observation_ids") or {}).items():
                cid, rn, nid = key.split("|", 2)
                self._node_observation_ids[(cid, int(rn), nid)] = value
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
                f"{cid}|{rn}|{nid}": v for (cid, rn, nid), v in self._node_observation_ids.items()
            },
            "dataset_item_ids": {
                f"{dsname}|{query}": v for (dsname, query), v in self._dataset_item_ids.items()
            },
        }
        write_json(self._state_path, state)

    def get_langfuse_trace_id(self, campaign_id: str) -> str | None:
        return self._trace_ids.get(campaign_id)

    def on_campaign_start(self, event: CampaignStart) -> None:
        if event.session_id:
            self._bind_session(event.session_id)
        cloud_id = self._lf.create_trace(
            name="optimization_loop",
            input={
                "campaign_id": event.campaign_id,
                "origin_accuracy": event.origin_accuracy,
                "config": event.config,
            },
            session_id=event.session_id,
            tags=["campaign", "optimization_loop"],
        )
        if cloud_id:
            self._trace_ids[event.campaign_id] = cloud_id
            if event.session_id:
                self._session_ids[event.campaign_id] = event.session_id
            self._lf.create_score(
                trace_id=cloud_id,
                name="origin_accuracy",
                value=event.origin_accuracy,
            )
            self._persist()

    def on_dataset_registered(self, event: DatasetRegistered) -> None:
        if len(event.items) > 100:
            logger.warning(
                "Skipping Langfuse cloud dataset registration for %d items "
                "(rate-limit risk). Use the dedicated Langfuse sync cell instead.",
                len(event.items),
            )
            return
        self._lf.create_dataset(
            name=event.dataset_name,
            description="Ground truth queries for prompt evaluation",
            metadata={"n_items": len(event.items)},
        )
        for query, ground_truth in event.items:
            if not query:
                continue
            cloud_id = self._lf.create_dataset_item(
                dataset_name=event.dataset_name,
                input={"query": query},
                expected_output=ground_truth,
                metadata={"source": "dataset"},
            )
            if cloud_id:
                self._dataset_item_ids[(event.dataset_name, query)] = cloud_id
        self._persist()

    def on_dataset_run(self, event: DatasetRun) -> None:
        trace_id = self._trace_ids.get(event.campaign_id)
        if not trace_id:
            return
        round_observation_id = self._round_observation_ids.get((event.campaign_id, event.round_num))
        self._lf.create_span(
            trace_id=trace_id,
            name=f"run_{event.run_id[:8]}",
            input={
                "run_id": event.run_id,
                "content_hash": event.content_hash,
                "prompt_fields_id": event.prompt_fields_id,
            },
            output={
                "accuracy": event.accuracy,
                "total": event.total,
            },
            parent_observation_id=round_observation_id,
            as_type="span",
        )

    def on_round_start(self, event: RoundStart) -> None:
        trace_id = self._trace_ids.get(event.campaign_id)
        if not trace_id:
            return
        observation_id = self._lf.start_span(
            trace_id=trace_id,
            name=f"round_{event.round_num}",
            input={"round": event.round_num},
            metadata={"round": event.round_num},
            as_type="span",
        )
        if observation_id:
            self._round_observation_ids[(event.campaign_id, event.round_num)] = observation_id

    def on_node_start(self, event: NodeStart) -> None:
        trace_id = self._trace_ids.get(event.campaign_id)
        if not trace_id:
            return
        round_observation_id = self._round_observation_ids.get((event.campaign_id, event.round_num))
        as_type = event.as_type if event.as_type in ("generation", "span") else "span"
        observation_id = self._lf.start_span(
            trace_id=trace_id,
            name=event.node_id,
            input=event.input_data,
            metadata={"node_type": event.node_type, **(event.metadata or {})},
            parent_observation_id=round_observation_id,
            as_type=as_type,
        )
        if observation_id:
            self._node_observation_ids[(event.campaign_id, event.round_num, event.node_id)] = (
                observation_id
            )

    def on_node_end(self, event: NodeEnd) -> None:
        key = (event.campaign_id, event.round_num, event.node_id)
        observation_id = self._node_observation_ids.pop(key, None)
        if not observation_id:
            return
        meta: dict[str, Any] = {}
        if event.metrics:
            meta["metrics"] = event.metrics
        if event.error:
            meta["error"] = event.error
        self._lf.end_observation(
            observation_id,
            output=event.output_data,
            metadata=meta or None,
        )

    def on_round_end(self, event: RoundEnd) -> None:
        trace_id = self._trace_ids.get(event.campaign_id)
        if not trace_id:
            return
        round_observation_id = self._round_observation_ids.pop(
            (event.campaign_id, event.round_num), None
        )
        if round_observation_id:
            round_meta: dict[str, Any] = {
                "round": event.round_num,
                "candidates_scored": len(event.candidate_scores),
            }
            if event.optimizer_templates:
                round_meta["optimizer_templates"] = event.optimizer_templates
            self._lf.end_observation(
                round_observation_id,
                output={
                    "winner_accuracy": event.accuracy,
                    "improved": event.improved,
                    "next_action": event.next_action,
                    "candidates_scored": len(event.candidate_scores),
                },
                metadata=round_meta,
            )
        self._lf.create_score(
            trace_id=trace_id,
            name=f"accuracy_round_{event.round_num}",
            value=event.accuracy,
            comment=f"Round {event.round_num}: {'improved' if event.improved else 'no change'}",
        )
        # Emit one Langfuse score per evaluator value. Each evaluator's name
        # is suffixed with the round number so the cloud UI shows them as a
        # per-round time series.
        for ev_name, ev_value in event.evaluators.items():
            try:
                numeric = float(ev_value)
            except (TypeError, ValueError):
                continue
            self._lf.create_score(
                trace_id=trace_id,
                name=f"{ev_name}_round_{event.round_num}",
                value=numeric,
            )

    def on_prompt_version(self, event: PromptVersion) -> None:
        trace_id = self._trace_ids.get(event.campaign_id)
        if not trace_id:
            return
        round_observation_id = self._round_observation_ids.get((event.campaign_id, event.round_num))
        self._lf.create_span(
            trace_id=trace_id,
            name="prompt_version",
            input={
                "prompt_fields_id": event.prompt_fields_id,
                "parent_id": event.parent_id,
            },
            output={
                "family": "target_prompt",
                "version": event.prompt_fields_id[:8] if event.prompt_fields_id else "unknown",
            },
            metadata={"layer1_fields": event.layer1_fields},
            parent_observation_id=round_observation_id,
            as_type="span",
        )

    def on_campaign_end(self, event: CampaignEnd) -> None:
        trace_id = self._trace_ids.get(event.campaign_id)
        if not trace_id:
            return
        self._lf.create_score(
            trace_id=trace_id,
            name="best_accuracy",
            value=event.best_accuracy,
            comment=f"Best at round {event.best_round}, stop: {event.stop_reason}",
        )
        self._lf.update_trace(
            trace_id=trace_id,
            output={
                "best_accuracy": event.best_accuracy,
                "n_l1_rounds": event.n_l1_rounds,
                "stop_reason": event.stop_reason,
            },
            metadata={"stop_reason": event.stop_reason, "best_round": event.best_round},
        )
        self._lf.end_trace(trace_id)
        self._persist()

    def on_query_score_start(self, event: QueryScoreStart) -> None:
        if event.session_id:
            self._bind_session(event.session_id)
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
        """Create/update Langfuse dataset, returning ``{query: item_id}`` and
        populating ``_dataset_item_ids`` for query-score link-back."""
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
