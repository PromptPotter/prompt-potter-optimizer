"""Langfuse sink — owns ALL Langfuse id mappings, persisted across resume.

Handles both trace topologies:
- **Topology A** (live optimization) — CampaignStart/Round*/Node*/PromptVersion/
  DatasetRun/CampaignEnd → one trace per campaign, rounds nested.
- **Topology B** (per-query eval, backfill replay) — QueryEvalStart/
  QueryNodeSpan/QueryEvalEnd → one trace per query linked to dataset items.

The five id maps (campaign→trace, (campaign,round)→round_obs,
(campaign,round,node)→node_obs, (dataset,query)→item, campaign→session) are
persisted to ``campaigns/{cycle_id}/langfuse/state.json`` after every
mutation. This is the fix for the resume bug where the in-memory trace map
was lost and post-resume events attached to nothing.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.infrastructure.store.base import read_json_optional, write_json
from promptpotter.infrastructure.tracing.events import (
    CampaignEnd,
    CampaignStart,
    DatasetRegistered,
    DatasetRun,
    Event,
    NodeEnd,
    NodeStart,
    PromptVersion,
    QueryEvalEnd,
    QueryEvalStart,
    QueryNodeSpan,
    RoundEnd,
    RoundStart,
)

if TYPE_CHECKING:
    from promptpotter.infrastructure.tracing.langfuse_client import LangfuseLogger

logger = logging.getLogger(__name__)


class LangfuseSink:
    """Dispatches events to a ``LangfuseLogger``; owns persisted id state."""

    def __init__(
        self,
        store_base_dir: str | Path,
        backend_id: str,
        langfuse: LangfuseLogger,
    ) -> None:
        self._base = Path(store_base_dir)
        self._backend_id = backend_id
        self._lf = langfuse

        self._trace_ids: dict[str, str] = {}  # campaign_id → lf trace id
        self._round_obs_ids: dict[tuple[str, int], str] = {}
        self._node_obs_ids: dict[tuple[str, int, str], str] = {}
        self._dataset_item_ids: dict[tuple[str, str], str] = {}
        self._session_ids: dict[str, str] = {}  # campaign_id → langfuse session id
        # In-progress Topology B traces, keyed by (run_id, query).
        # Value = (trace_id, dataset_name, origin).
        self._query_trace_ids: dict[tuple[str, str], tuple[str, str, str]] = {}

        # The session-id-dependent state path is resolved lazily on the
        # first CampaignStart event (session_id lives on that event).
        self._state_session_id: str | None = None
        self._state_path: Path | None = None

    # --- Persistence ---

    def _session_state_path(self, session_id: str) -> Path:
        # ``session_id`` here is the campaign cycle_id (the sink uses
        # campaign-scoped state); the Langfuse shadow is per-campaign.
        return self._base / "campaigns" / session_id / "langfuse" / "state.json"

    def _bind_session(self, session_id: str) -> None:
        """Latch the session state path and restore any prior state."""
        if self._state_session_id == session_id:
            return
        self._state_session_id = session_id
        self._state_path = self._session_state_path(session_id)
        existing = read_json_optional(self._state_path)
        if existing:
            self._trace_ids.update(existing.get("trace_ids", {}))
            self._session_ids.update(existing.get("session_ids", {}))
            for key, value in (existing.get("round_obs_ids") or {}).items():
                cid, rn = key.rsplit("|", 1)
                self._round_obs_ids[(cid, int(rn))] = value
            for key, value in (existing.get("node_obs_ids") or {}).items():
                cid, rn, nid = key.split("|", 2)
                self._node_obs_ids[(cid, int(rn), nid)] = value
            for key, value in (existing.get("dataset_item_ids") or {}).items():
                dsname, query = key.split("|", 1)
                self._dataset_item_ids[(dsname, query)] = value

    def _persist(self) -> None:
        if self._state_path is None:
            return
        state: dict[str, Any] = {
            "trace_ids": dict(self._trace_ids),
            "session_ids": dict(self._session_ids),
            "round_obs_ids": {f"{cid}|{rn}": v for (cid, rn), v in self._round_obs_ids.items()},
            "node_obs_ids": {
                f"{cid}|{rn}|{nid}": v for (cid, rn, nid), v in self._node_obs_ids.items()
            },
            "dataset_item_ids": {
                f"{dsname}|{query}": v for (dsname, query), v in self._dataset_item_ids.items()
            },
        }
        write_json(self._state_path, state)

    # --- Accessors ---

    def get_langfuse_trace_id(self, campaign_id: str) -> str | None:
        return self._trace_ids.get(campaign_id)

    # --- Dispatch ---

    def handle(self, event: Event) -> None:
        if not self._lf.enabled:
            return
        if isinstance(event, CampaignStart):
            self._on_campaign_start(event)
        elif isinstance(event, DatasetRegistered):
            self._on_dataset_registered(event)
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
        elif isinstance(event, QueryEvalStart):
            self._on_query_eval_start(event)
        elif isinstance(event, QueryNodeSpan):
            self._on_query_node_span(event)
        elif isinstance(event, QueryEvalEnd):
            self._on_query_eval_end(event)

    # --- Topology A ---

    def _on_campaign_start(self, event: CampaignStart) -> None:
        if event.session_id:
            self._bind_session(event.session_id)
        cloud_id = self._lf.create_trace(
            name="optimization_loop",
            input={
                "campaign_id": event.campaign_id,
                "baseline_accuracy": event.baseline_accuracy,
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
                name="baseline_accuracy",
                value=event.baseline_accuracy,
            )
            self._persist()

    def _on_dataset_registered(self, event: DatasetRegistered) -> None:
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

    def _on_dataset_run(self, event: DatasetRun) -> None:
        trace_id = self._trace_ids.get(event.campaign_id)
        if not trace_id:
            return
        round_obs_id = self._round_obs_ids.get((event.campaign_id, event.round_num))
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
                "hits": event.hits,
                "total": event.total,
            },
            parent_observation_id=round_obs_id,
            as_type="tool",
        )

    def _on_round_start(self, event: RoundStart) -> None:
        trace_id = self._trace_ids.get(event.campaign_id)
        if not trace_id:
            return
        obs_id = self._lf.start_span(
            trace_id=trace_id,
            name=f"round_{event.round_num}",
            input={"round": event.round_num},
            metadata={"round": event.round_num},
            as_type="span",
        )
        if obs_id:
            self._round_obs_ids[(event.campaign_id, event.round_num)] = obs_id
            self._persist()

    def _on_node_start(self, event: NodeStart) -> None:
        trace_id = self._trace_ids.get(event.campaign_id)
        if not trace_id:
            return
        round_obs_id = self._round_obs_ids.get((event.campaign_id, event.round_num))
        as_type = event.obs_type if event.obs_type in ("generation", "span") else "span"
        obs_id = self._lf.start_span(
            trace_id=trace_id,
            name=event.node_id,
            input=event.input_data,
            metadata={"node_type": event.node_type, **(event.metadata or {})},
            parent_observation_id=round_obs_id,
            as_type=as_type,
        )
        if obs_id:
            self._node_obs_ids[(event.campaign_id, event.round_num, event.node_id)] = obs_id
            self._persist()

    def _on_node_end(self, event: NodeEnd) -> None:
        key = (event.campaign_id, event.round_num, event.node_id)
        obs_id = self._node_obs_ids.pop(key, None)
        if not obs_id:
            return
        meta: dict[str, Any] = {}
        if event.metrics:
            meta["metrics"] = event.metrics
        if event.error:
            meta["error"] = event.error
        self._lf.end_observation(
            obs_id,
            output=event.output_data,
            metadata=meta or None,
        )
        self._persist()

    def _on_round_end(self, event: RoundEnd) -> None:
        trace_id = self._trace_ids.get(event.campaign_id)
        if not trace_id:
            return
        round_obs_id = self._round_obs_ids.pop((event.campaign_id, event.round_num), None)
        if round_obs_id:
            round_meta: dict[str, Any] = {
                "round": event.round_num,
                "candidates_scored": len(event.candidate_scores),
            }
            if event.optimizer_templates:
                round_meta["optimizer_templates"] = event.optimizer_templates
            self._lf.end_observation(
                round_obs_id,
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
        for ev_name, ev_value in (event.evaluators or {}).items():
            try:
                numeric = float(ev_value)
            except (TypeError, ValueError):
                continue
            self._lf.create_score(
                trace_id=trace_id,
                name=f"{ev_name}_round_{event.round_num}",
                value=numeric,
            )
        self._persist()

    def _on_prompt_version(self, event: PromptVersion) -> None:
        trace_id = self._trace_ids.get(event.campaign_id)
        if not trace_id:
            return
        round_obs_id = self._round_obs_ids.get((event.campaign_id, event.round_num))
        self._lf.create_span(
            trace_id=trace_id,
            name="prompt_version",
            input={
                "prompt_fields_id": event.prompt_fields_id,
                "parent_id": event.parent_id,
            },
            output={
                "family": "optimizer_prompt",
                "version": event.prompt_fields_id[:8] if event.prompt_fields_id else "unknown",
            },
            metadata={"layer1_fields": event.layer1_fields},
            parent_observation_id=round_obs_id,
            as_type="tool",
        )

    def _on_campaign_end(self, event: CampaignEnd) -> None:
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
                "n_rounds": event.n_rounds,
                "stop_reason": event.stop_reason,
            },
            metadata={"stop_reason": event.stop_reason, "best_round": event.best_round},
        )
        self._lf.end_trace(trace_id)
        self._persist()

    # --- Topology B (backfill replay) ---

    def _on_query_eval_start(self, event: QueryEvalStart) -> None:
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
            input={"query": event.query, "ground_truth": event.ground_truth},
            session_id=event.session_id,
            tags=["eval", event.origin, "pipeline"],
            metadata=metadata,
        )
        if trace_id:
            self._query_trace_ids[(event.run_id, event.query)] = (
                trace_id,
                event.dataset_name,
                event.origin,
            )

    def _on_query_node_span(self, event: QueryNodeSpan) -> None:
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

    def _on_query_eval_end(self, event: QueryEvalEnd) -> None:
        entry = self._query_trace_ids.pop((event.run_id, event.query), None)
        if not entry:
            return
        trace_id, dataset_name, origin = entry
        self._lf.create_score(trace_id, "hit", 1.0 if event.hit else 0.0)
        trace_output: dict[str, Any] = {
            "predicted": event.predicted,
            "ground_truth": event.ground_truth,
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

    # --- Backfill helper ---

    def reconcile_dataset(
        self,
        dataset_name: str,
        gt_map: dict[str, str],
        seed_items: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Create/update a Langfuse dataset and reconcile item ids.

        Replaces the old ``langfuse_backfill._register_dataset_items``.
        Returns ``{query: item_id}`` and also populates the sink's
        ``_dataset_item_ids`` map so subsequent query eval events can
        link traces to items.
        """
        query_to_item_id: dict[str, str] = dict(seed_items or {})

        ok = self._lf.create_dataset(
            name=dataset_name,
            description="Production ground truth queries for prompt evaluation",
            metadata={"n_queries": len(gt_map)},
        )
        if not ok:
            raise RuntimeError(
                f"Langfuse: failed to create/access dataset '{dataset_name}'. "
                "Check LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY in .env."
            )

        existing_items: dict[str, Any] = {}
        ds = self._lf.get_dataset(dataset_name)
        if ds and hasattr(ds, "items"):
            for it in ds.items:  # type: ignore[attr-defined]
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
