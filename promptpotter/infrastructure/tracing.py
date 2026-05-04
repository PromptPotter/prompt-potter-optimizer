"""Event schema for the observability bridge.

Single source of truth for every observable thing the optimizer or the
backfill replayer can emit. Every public tracing method maps to exactly
one event dataclass; the bridge fans events out to sinks (file, Langfuse)
and each sink handles them by type. No implicit "active trace" state lives
on the producer side — every event is self-contained and carries the keys
the sink needs to look up its own id mappings.

Two families:

- :class:`OptimizationEvent` — Topology A (live campaign trace). One Langfuse
  trace per campaign; rounds and nodes nest underneath. Emitted inline from
  the optimization loop.
- :class:`MeasurementEvent` — Topology B (per-query dataset trace). One
  Langfuse trace per query, linked to dataset items. Emitted by the backfill
  replayer reading ``library/measurements/`` from disk.

The audit table at ``docs/planned/observability-refactor.md`` documents
exactly which Langfuse SDK calls each event triggers.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Union

import httpx
import mlflow
from langfuse import Langfuse

from promptpotter.config.settings import DATASET_NAME, settings
from promptpotter.config.settings import DATASET_NAME as _DEFAULT_DATASET_NAME
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.infrastructure.store import Stores
from promptpotter.infrastructure.store.base import (
    append_jsonl,
    read_json_optional,
    write_json,
    write_text,
)
from promptpotter.infrastructure.store.stores import campaign_dir_for
from promptpotter.shared.errors import graceful


@dataclass(frozen=True, slots=True)
class DatasetRegistered:
    """Dataset items registered in file store and (optionally) Langfuse.

    Used by both topologies — the cloud-side ``query → item_id`` mapping
    produced here is shared between the live forward path (which links
    DatasetRun spans) and the backfill replayer (which links per-query
    traces).
    """

    dataset_name: str
    items: tuple[tuple[str, str], ...]
    """Frozen ``(query, ground_truth)`` pairs in registration order."""


# --- Optimization (Topology A) ---


@dataclass(frozen=True, slots=True)
class CampaignStart:
    campaign_id: str
    config: dict[str, Any]
    baseline_accuracy: float
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class RoundStart:
    campaign_id: str
    round_num: int


@dataclass(frozen=True, slots=True)
class NodeStart:
    """Open a node observation under the active round.

    The sink looks up the campaign's trace id by ``campaign_id`` and the
    round's parent observation id by ``(campaign_id, round_num)``. Producers
    do not pass either directly — they pass identity, sinks own state.
    """

    campaign_id: str
    round_num: int
    node_id: str
    node_type: str
    obs_type: str
    input_data: dict[str, Any]
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class NodeEnd:
    campaign_id: str
    round_num: int
    node_id: str
    output_data: dict[str, Any] | None = None
    metrics: dict[str, float] | None = None
    error: str | None = None


# --- Mid-round write-point events ---
#
# Observability markers for durable mid-round transitions. events.jsonl is
# a pure observability mirror: nothing reads these events back for state
# reconstruction. Resume and fork are driven by
# ``campaigns/{cycle_id}/trials/trial_NNNN.json`` via ``CampaignStore``.


@dataclass(frozen=True, slots=True)
class CandidateCreated:
    """An L1-proposed candidate was registered for this round."""

    campaign_id: str
    round_num: int
    candidate_idx: int
    candidate_id: str


@dataclass(frozen=True, slots=True)
class CandidateScored:
    """A candidate finished its full scoring loop (report built)."""

    campaign_id: str
    round_num: int
    candidate_idx: int
    report: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RoundWinnerChosen:
    """Round winner picked from scored candidates."""

    campaign_id: str
    round_num: int
    winner_candidate_id: str
    winner_accuracy: float
    improved: bool


@dataclass(frozen=True, slots=True)
class L1CritiqueWritten:
    """L1 critique text produced after scoring (inline in ``_score_and_select``)."""

    campaign_id: str
    round_num: int
    l1_critique_text: str


@dataclass(frozen=True, slots=True)
class LayerApplied:
    """L2 (``refine_strategy``) or L3 (``modify_plan``) transition applied to the loop state."""

    layer: Literal["L2", "L3"]
    campaign_id: str
    round_num: int
    changes_description: str


@dataclass(frozen=True, slots=True)
class PromptVersion:
    """A new optimizer prompt was materialized for the active round."""

    campaign_id: str
    round_num: int
    prompt_fields_id: str
    rendered_prompt: str
    layer1_fields: dict[str, Any]
    parent_id: str | None = None


@dataclass(frozen=True, slots=True)
class DatasetRun:
    """Target-layer scoring report for a single SearchPoint, nested under the active round.

    This event sits at the layer boundary: the data is target-layer
    (``JobSearchPoint`` scoring) but the Langfuse parent is the
    optimizer-layer round span. Topology A intentionally entangles them so
    a campaign trace shows which scoring run each round depended on.
    """

    campaign_id: str
    round_num: int
    run_id: str
    content_hash: str
    prompt_fields_id: str
    accuracy: float
    hits: int
    total: int


@dataclass(frozen=True, slots=True)
class RoundEnd:
    campaign_id: str
    round_num: int
    accuracy: float
    hits: int
    total: int
    improved: bool
    winner_prompt_fields_id: str
    candidate_scores: list[dict[str, Any]]
    next_action: str = ""
    model: str = ""
    temperature: float = 0.0
    n_variants: int = 0
    optimizer_templates: list[str] | None = None
    evaluators: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CampaignEnd:
    campaign_id: str
    best_accuracy: float
    n_rounds: int
    stop_reason: str
    best_round: int


OptimizationEvent = Union[
    CampaignStart,
    RoundStart,
    NodeStart,
    NodeEnd,
    PromptVersion,
    DatasetRun,
    RoundEnd,
    CampaignEnd,
    CandidateCreated,
    CandidateScored,
    RoundWinnerChosen,
    L1CritiqueWritten,
    LayerApplied,
]


# --- Measurement (Topology B, replayed from library/measurements/) ---


@dataclass(frozen=True, slots=True)
class QueryNodeSpan:
    """One pipeline node's input/output captured during a target-layer measurement.

    Emitted as a child of a ``QueryScoreStart``/``QueryScoreEnd`` pair. The
    node's Langfuse ``as_type`` (generation/span/tool/...) is taken from
    the pipeline schema's ``langfuse_type`` for that node.
    """

    run_id: str
    query: str
    node_name: str
    as_type: str
    input_data: dict[str, Any]
    output_data: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    usage_details: dict[str, int] | None = None


@dataclass(frozen=True, slots=True)
class QueryScoreStart:
    run_id: str
    query: str
    ground_truth: str
    origin: str
    llm_provider: str
    prompt_fields_id: str
    pipeline_params: dict[str, Any] | None
    schema_name: str
    session_id: str
    dataset_name: str


@dataclass(frozen=True, slots=True)
class QueryScoreEnd:
    run_id: str
    query: str
    predicted: str
    ground_truth: str
    hit: bool
    total_time: float | None
    node_outputs: dict[str, Any]
    """Flat map of pipeline node output keys → values (for the trace output blob)."""


MeasurementEvent = Union[
    QueryScoreStart,
    QueryNodeSpan,
    QueryScoreEnd,
]


Event = Union[DatasetRegistered, OptimizationEvent, MeasurementEvent]
"""Per-cycle Langfuse shadow + events.jsonl + prompts under campaigns/{cycle_id}/langfuse/."""


logger = logging.getLogger(__name__)


def _generate_obs_id(length: int = 32) -> str:
    prefix = datetime.now(UTC).strftime("%y%m%d%H%M%S")
    suffix = uuid.uuid4().hex[: length - len(prefix)]
    return f"{prefix}{suffix}"


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class FileSink:
    """Append-only Langfuse-style file log per cycle."""

    def __init__(self, store_base_dir: str | Path, backend_id: str) -> None:
        self._tenant_root = Path(store_base_dir)
        self._backend_id = backend_id
        self._cycle_id: str | None = None
        self._campaign_traces: dict[str, str] = {}
        self._round_obs_ids: dict[tuple[str, int], tuple[str, str]] = {}
        self._node_obs: dict[tuple[str, int, str], tuple[str, str]] = {}

    def _scope_dir(self) -> Path:
        if self._cycle_id:
            # Route through campaign_dir_for so fork cycle_ids land in their
            # nested fork dir, not at flat-layout campaigns/{cycle_id}/.
            return campaign_dir_for(self._tenant_root, self._cycle_id)
        # Orphan fallback for out-of-campaign file_only() emits — tucked
        # under .archive/ so library/ top stays operator-facing.
        return self._tenant_root / "library" / ".archive" / "obs"

    def _log_event(self, event: dict) -> None:
        event["timestamp"] = _utcnow_iso()
        append_jsonl(self._scope_dir() / "langfuse" / "events.jsonl", event)

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


"""Langfuse sink owning all id mappings, persisted to campaigns/{cycle_id}/langfuse/state.json across resume."""


if TYPE_CHECKING:
    pass


class LangfuseSink:
    """Forwards events to ``LangfuseLogger`` and persists id state."""

    def __init__(
        self,
        store_base_dir: str | Path,
        backend_id: str,
        langfuse: LangfuseLogger,
    ) -> None:
        self._base = Path(store_base_dir)
        self._backend_id = backend_id
        self._lf = langfuse

        self._trace_ids: dict[str, str] = {}
        self._round_obs_ids: dict[tuple[str, int], str] = {}
        self._node_obs_ids: dict[tuple[str, int, str], str] = {}
        self._dataset_item_ids: dict[tuple[str, str], str] = {}
        self._session_ids: dict[str, str] = {}
        # (run_id, query) → (trace_id, dataset_name, origin) — Topology B in-flight.
        self._query_trace_ids: dict[tuple[str, str], tuple[str, str, str]] = {}

        # Resolved lazily on first CampaignStart (session_id lives there).
        self._state_session_id: str | None = None
        self._state_path: Path | None = None

    def _session_state_path(self, session_id: str) -> Path:
        # session_id == cycle_id here; state is per-campaign.
        return campaign_dir_for(self._base, session_id) / "langfuse" / "state.json"

    def _bind_session(self, session_id: str) -> None:
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

    def get_langfuse_trace_id(self, campaign_id: str) -> str | None:
        return self._trace_ids.get(campaign_id)

    def on_campaign_start(self, event: CampaignStart) -> None:
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

    def on_round_start(self, event: RoundStart) -> None:
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

    def on_node_start(self, event: NodeStart) -> None:
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

    def on_node_end(self, event: NodeEnd) -> None:
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

    def on_round_end(self, event: RoundEnd) -> None:
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

    def on_prompt_version(self, event: PromptVersion) -> None:
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
                "n_rounds": event.n_rounds,
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
            input={"query": event.query, "ground_truth": event.ground_truth},
            session_id=event.session_id,
            tags=["score", event.origin, "pipeline"],
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


"""Per-cycle MLflow sink — user-requested integration, opt-in.

Logs each round as an MLflow run under ``library/mlruns/``. Disabled by
default; flip ``settings.MLFLOW_ENABLED`` to turn on. Kept on purpose
even when off — operators have requested MLflow as a first-class
observability target.
"""


if TYPE_CHECKING:
    pass


class MLflowSink:
    """Logs each round as an MLflow run; experiment = ``{tenant_id}/{cycle_id}``."""

    def __init__(self, store_base_dir: str | Path, backend_id: str) -> None:
        self._tenant_root = Path(store_base_dir)
        self._tenant_id = self._tenant_root.name
        self._library_dir = self._tenant_root / "library"
        self._backend_id = backend_id
        self._cycle_id: str | None = None
        self._initialized = False

    def on_campaign_start(self, event: CampaignStart) -> None:
        if event.session_id:
            self._cycle_id = event.session_id

    def on_round_end(self, event: RoundEnd) -> None:

        if not settings.MLFLOW_ENABLED or not self._cycle_id:
            return

        if not self._initialized:
            tracking_uri = (self._library_dir / "mlruns").resolve().as_uri()
            mlflow.set_tracking_uri(tracking_uri)
            mlflow.set_experiment(name=f"{self._tenant_id}/{self._cycle_id}")
            self._initialized = True

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


"""Langfuse SDK wrapper — singleton client with project isolation via API keys.

One root span per trace, child spans nested via ``start_observation``.
"""


class LangfuseLogger:
    """Langfuse SDK wrapper. Disabled if credentials are missing.

    Error-level policy: per-call observability failures log at DEBUG (a lost
    span is expected during network blips). Setup + post-retry failures log
    at WARNING.
    """

    _instance: LangfuseLogger | None = None

    def __init__(self) -> None:

        self.enabled = bool(
            settings.LANGFUSE_ENABLED
            and settings.LANGFUSE_SECRET_KEY
            and settings.LANGFUSE_PUBLIC_KEY
        )
        self.client = None
        self._trace_metadata: dict[str, Any] = {}  # trace_id → root SDK observation
        self._open_observations: dict[str, Any] = {}  # obs_id → open SDK observation
        self._rate_limit_until: float = 0.0  # unix ts when quota resets

        if self.enabled:
            try:
                os.environ["LANGFUSE_PUBLIC_KEY"] = settings.LANGFUSE_PUBLIC_KEY
                os.environ["LANGFUSE_SECRET_KEY"] = settings.LANGFUSE_SECRET_KEY
                os.environ["LANGFUSE_HOST"] = settings.LANGFUSE_HOST

                self.client = Langfuse()
            except ImportError:
                logger.warning(
                    "langfuse package not installed — observability disabled. "
                    'Install: pip install -e ".[observability]"'
                )
                self.enabled = False
            except Exception:
                logger.warning("Failed to initialize Langfuse", exc_info=True)
                self.enabled = False

    @classmethod
    def get_instance(cls) -> LangfuseLogger:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    def create_trace_id(self) -> str | None:
        """Bare trace ID without a root observation — keeps pipeline traces
        from collapsing into a root chain in the Langfuse graph view."""
        if not self.enabled or not self.client:
            return None
        try:
            return self.client.create_trace_id()
        except Exception:
            logger.debug("Failed to create Langfuse trace ID", exc_info=True)
            return None

    def create_trace(
        self,
        name: str,
        input: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        tags: list[str] | None = None,
    ) -> str | None:
        """Create trace with a root span; pushes metadata via ``update_trace``
        so the cloud UI shows full info instead of an auto-stub."""
        if not self.enabled or not self.client:
            return None

        try:
            trace_id = self.client.create_trace_id()
            root = self.client.start_observation(
                trace_context={"trace_id": trace_id},
                as_type="chain",
                name=name,
                input=input,
                metadata=metadata or {},
            )
            root.update_trace(
                name=name,
                user_id=user_id,
                session_id=session_id,
                input=input,
                metadata=metadata,
                tags=tags or [],
            )
            self._trace_metadata[trace_id] = root
            return trace_id
        except Exception:
            logger.debug("Failed to create Langfuse trace", exc_info=True)
            return None

    def _resolve_parent(self, trace_id: str, parent_observation_id: str | None) -> Any | None:
        """Find an SDK observation to nest under: explicit open obs → root → None."""
        if parent_observation_id:
            parent = self._open_observations.get(parent_observation_id)
            if parent is not None:
                return parent
        return self._trace_metadata.get(trace_id)

    def start_span(
        self,
        trace_id: str,
        name: str,
        input: Any = None,
        metadata: dict[str, Any] | None = None,
        *,
        parent_observation_id: str | None = None,
        as_type: str = "span",
    ) -> str | None:
        """Start a long-running observation; pair with ``end_observation``."""
        if not self.enabled or not self.client or not trace_id:
            return None

        try:
            parent = self._resolve_parent(trace_id, parent_observation_id)
            if parent is None:
                return None
            child = parent.start_observation(
                as_type=as_type,
                name=name,
                input=input,
                metadata=metadata or {},
            )
            obs_id = getattr(child, "id", uuid.uuid4().hex[:12])
            self._open_observations[obs_id] = child
            return obs_id
        except Exception:
            logger.debug("Failed to start Langfuse span", exc_info=True)
            return None

    def end_observation(
        self,
        obs_id: str,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self.enabled or not obs_id:
            return

        with graceful("Failed to end Langfuse observation"):
            child = self._open_observations.pop(obs_id, None)
            if child is None:
                return
            kwargs: dict[str, Any] = {}
            if output is not None:
                kwargs["output"] = output
            if metadata is not None:
                kwargs["metadata"] = metadata
            if kwargs:
                child.update(**kwargs)
            child.end()

    def create_span(
        self,
        trace_id: str,
        name: str,
        input: Any,
        output: Any,
        metadata: dict[str, Any] | None = None,
        *,
        parent_observation_id: str | None = None,
        as_type: str = "span",
        model: str | None = None,
        usage_details: dict[str, int] | None = None,
    ) -> str | None:
        """Log a closed observation nested under root or a parent obs."""
        if not self.enabled or not self.client or not trace_id:
            return None

        try:
            parent = self._resolve_parent(trace_id, parent_observation_id)
            if parent is None:
                return None
            kwargs: dict[str, Any] = {
                "as_type": as_type,
                "name": name,
                "input": input,
                "output": output,
                "metadata": metadata or {},
            }
            if model:
                kwargs["model"] = model
            if usage_details:
                kwargs["usage_details"] = usage_details

            child = parent.start_observation(**kwargs)
            child.end()
            return getattr(child, "id", uuid.uuid4().hex[:12])
        except Exception:
            logger.debug("Failed to log Langfuse span", exc_info=True)
            return None

    def create_score(
        self,
        trace_id: str,
        name: str,
        value: float,
        data_type: str = "NUMERIC",
        comment: str | None = None,
    ) -> bool:
        if not self.enabled or not self.client or not trace_id:
            return False

        try:
            self.client.create_score(
                trace_id=trace_id,
                name=name,
                value=value,
                data_type=data_type,
                comment=comment,
            )
            return True
        except Exception:
            logger.debug("Failed to log Langfuse score", exc_info=True)
            return False

    def update_trace(
        self,
        trace_id: str,
        output: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Push trace-level output/metadata via the root span's ``update_trace``."""
        if not self.enabled or not self.client or not trace_id:
            return False

        try:
            root = self._trace_metadata.get(trace_id)
            if root:
                kwargs: dict[str, Any] = {}
                if output is not None:
                    kwargs["output"] = output
                if metadata is not None:
                    kwargs["metadata"] = metadata
                if kwargs:
                    root.update_trace(**kwargs)
            return True
        except Exception:
            logger.debug("Failed to update Langfuse trace", exc_info=True)
            return False

    def end_trace(self, trace_id: str) -> None:
        if not self.enabled or not self.client or not trace_id:
            return

        with graceful("Failed to end Langfuse trace"):
            root = self._trace_metadata.get(trace_id)
            if root:
                root.end()

    # -- Dataset API ---------------------------------------------------------

    def create_dataset(
        self,
        name: str,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Idempotent — no error if the dataset exists."""
        if not self.enabled or not self.client:
            return False
        try:
            self.client.create_dataset(
                name=name,
                description=description or "",
                metadata=metadata or {},
            )
            return True
        except Exception:
            logger.debug("Failed to create Langfuse dataset", exc_info=True)
            return False

    def create_dataset_item(
        self,
        dataset_name: str,
        input: Any,
        expected_output: Any = None,
        metadata: dict[str, Any] | None = None,
        _max_retries: int = 3,
    ) -> str | None:
        """Retries with exponential backoff on 429."""
        if not self.enabled or not self.client:
            return None
        for attempt in range(_max_retries):
            try:
                item = self.client.create_dataset_item(
                    dataset_name=dataset_name,
                    input=input,
                    expected_output=expected_output,
                    metadata=metadata or {},
                )
                return getattr(item, "id", None)
            except Exception as exc:
                if "429" in str(exc) and attempt < _max_retries - 1:
                    delay = 2**attempt
                    logger.debug("Langfuse 429 rate limit, retry in %ds", delay)
                    time.sleep(delay)
                    continue
                logger.warning("Failed to create Langfuse dataset item", exc_info=True)
                return None
        return None

    def get_dataset(self, name: str) -> object | None:
        if not self.enabled or not self.client:
            return None
        try:
            return self.client.get_dataset(name=name)
        except Exception:
            logger.debug("Failed to get Langfuse dataset", exc_info=True)
            return None

    def update_dataset_item(
        self,
        item_id: str,
        expected_output: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        if not self.enabled or not self.client:
            return False
        try:
            kwargs: dict[str, Any] = {"id": item_id}
            if expected_output is not None:
                kwargs["expected_output"] = expected_output
            if metadata is not None:
                kwargs["metadata"] = metadata
            self.client.create_dataset_item(**kwargs)
            return True
        except Exception:
            logger.debug("Failed to update Langfuse dataset item", exc_info=True)
            return False

    @property
    def rate_limited(self) -> bool:
        return time.time() < self._rate_limit_until

    def link_item_to_run(
        self,
        dataset_item_id: str,
        trace_id: str,
        observation_id: str | None = None,
        run_name: str = "",
        run_metadata: dict[str, Any] | None = None,
        *,
        max_retries: int = 3,
    ) -> bool:
        """Link trace/observation to a dataset item via REST (the SDK only
        exposes a context-manager approach unsuitable for backfill).

        429 with Retry-After > 300s sets ``rate_limited`` and returns False
        so callers can stop early instead of retrying for hours.
        """
        if not self.enabled or not self.client:
            return False
        if self.rate_limited:
            return False
        try:
            body: dict[str, Any] = {
                "datasetItemId": dataset_item_id,
                "traceId": trace_id,
                "runName": run_name,
                "metadata": run_metadata or {},
            }
            if observation_id:
                body["observationId"] = observation_id

            url = f"{settings.LANGFUSE_HOST}/api/public/dataset-run-items"
            auth = (settings.LANGFUSE_PUBLIC_KEY, settings.LANGFUSE_SECRET_KEY)

            for attempt in range(max_retries):
                resp = httpx.post(url, auth=auth, json=body, timeout=30)

                if resp.status_code in (200, 201):
                    return True

                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "0"))
                    remaining = int(resp.headers.get("X-RateLimit-Remaining", "-1"))

                    # Daily quota exhausted (Retry-After > 5 min) — stop trying
                    if retry_after > 300 or remaining == 0:
                        self._rate_limit_until = time.time() + retry_after
                        logger.warning(
                            "Langfuse daily rate limit hit (resets in %ds). "
                            "Skipping remaining link_item_to_run calls.",
                            retry_after,
                        )
                        return False

                    # Short-term rate limit — back off and retry
                    wait = min(2**attempt, retry_after or 2**attempt)
                    logger.debug("Rate limited, waiting %ds (attempt %d)", wait, attempt + 1)
                    time.sleep(wait)
                    continue

                # Other HTTP error
                logger.warning(
                    "Link item to run HTTP %s: %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return False

            logger.warning("link_item_to_run exhausted %d retries", max_retries)
            return False
        except Exception:
            logger.warning("Failed to link dataset item to run", exc_info=True)
            return False

    def flush(self) -> None:
        if self.enabled and self.client:
            with graceful("Failed to flush Langfuse events"):
                self.client.flush()


"""Replay historical ``library/measurements/`` entries into the Langfuse sink.

Reads JSON from disk and emits :class:`QueryScoreStart` / :class:`QueryNodeSpan`
/ :class:`QueryScoreEnd` events into an :class:`ObservabilityBridge`. The sink
owns every Langfuse SDK call — this file is just a disk walker.

Idempotency tracking (``backfilled_run_ids`` and ``dataset_items`` seed)
lives in ``obs/langfuse/backfill_state.json``; the sink's own persisted
id maps in ``campaigns/{cycle_id}/langfuse_state.json`` are the source
of truth after the first replay.
"""


if TYPE_CHECKING:
    pass

ORIGIN_ORDER = [
    "baseline",
    "feedback_cycle",
    "optimization_loop",
    "other",
]


def classify_run_origin(source: str = "") -> str:
    """Classify a dataset run's origin from its ``source`` field."""
    if source in ORIGIN_ORDER:
        return source
    return "other"


@dataclass(frozen=True)
class LangfuseObservation:
    """A pipeline node extracted from ``library/measurements/`` ready to emit."""

    name: str
    as_type: str
    input: dict[str, Any]
    output: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    usage_details: dict[str, int] | None = None


def extract_pipeline_nodes(
    pipeline_data: dict,
    query: str,
    schema: PipelineSchema,
) -> list[LangfuseObservation]:
    """Parse pipeline_data into an ordered list of typed nodes."""
    nodes: list[LangfuseObservation] = []
    timings = pipeline_data.get("step_timings") or {}
    llm_provider = pipeline_data.get("llm_provider", "")
    node_params = pipeline_data.get("pipeline_params") or {}
    param_keys_by_node = schema.node_param_keys()

    for node in schema.nodes:
        output = {k: pipeline_data[k] for k in node.output_keys if pipeline_data.get(k) is not None}
        if not output:
            continue
        meta: dict[str, Any] = {}
        if node.name in timings:
            meta["duration_s"] = timings[node.name]
        matched = {
            k: node_params[k] for k in param_keys_by_node.get(node.name, set()) if k in node_params
        }
        if matched:
            meta["pipeline_params"] = matched
        nodes.append(
            LangfuseObservation(
                name=node.name,
                as_type=node.langfuse_type,
                input={"query": query},
                output=output,
                metadata=meta,
                model=llm_provider or None if node.is_llm else None,
            )
        )
    return nodes


def _state_path(store: Stores, backend_id: str) -> Path:
    return store.base_dir / backend_id / "obs" / "langfuse" / "backfill_state.json"


def _fresh_state() -> dict[str, Any]:
    return {
        "backfilled_run_ids": [],
        "last_backfill_at": None,
        "langfuse_trace_ids": {},
        "dataset_items": {},
    }


def _load_state(store: Stores, backend_id: str) -> dict[str, Any]:
    return read_json_optional(_state_path(store, backend_id)) or _fresh_state()


def _save_state(store: Stores, backend_id: str, state: dict[str, Any]) -> None:
    write_json(_state_path(store, backend_id), state)


def _collect_ground_truth(
    store: Stores,
    backend_id: str,
    summaries: list[dict],
) -> dict[str, str]:
    gt_map: dict[str, str] = {}
    for s in summaries:
        rid = s.get("run_id", "")
        detail = store.archive.load_by_id(backend_id, rid)
        if not detail:
            continue
        for item in detail.get("measurements", []):
            query = item.get("query", "")
            ground_truth = item.get("ground_truth", "")
            if query and ground_truth:
                gt_map[query] = ground_truth
    return gt_map


def _replay_run(
    bridge: ObservabilityBridge,
    store: Stores,
    backend_id: str,
    run_id: str,
    schema: PipelineSchema | None,
    session_id: str,
    dataset_name: str,
) -> bool:
    """Replay one dataset_run's items as events. Returns ``True`` on success."""
    detail = store.archive.load_by_id(backend_id, run_id)
    if not detail:
        logger.warning("replay: could not load detail for run %s", run_id)
        return False

    items = detail.get("measurements", [])
    origin = classify_run_origin(detail.get("source", ""))
    any_pushed = False

    for it in items:
        query = it.get("query", "")
        if not query:
            continue
        pipeline = it.get("pipeline_data") or {}
        hit = bool(it.get("hit", False))

        bridge.emit(
            QueryScoreStart(
                run_id=run_id,
                query=query,
                ground_truth=it.get("ground_truth", ""),
                origin=origin,
                llm_provider=pipeline.get("llm_provider", detail.get("model", "")),
                prompt_fields_id=detail.get("prompt_fields_id", ""),
                pipeline_params=pipeline.get("pipeline_params") or None,
                schema_name=schema.name if schema and schema.name else "",
                session_id=session_id,
                dataset_name=dataset_name,
            )
        )

        if pipeline and schema:
            for node in extract_pipeline_nodes(pipeline, query, schema=schema):
                bridge.emit(
                    QueryNodeSpan(
                        run_id=run_id,
                        query=query,
                        node_name=node.name,
                        as_type=node.as_type,
                        input_data=node.input,
                        output_data=node.output,
                        metadata=node.metadata,
                        model=node.model,
                        usage_details=node.usage_details,
                    )
                )

        node_outputs: dict[str, Any] = {}
        if schema:
            for schema_node in schema.nodes:
                for k in schema_node.output_keys:
                    val = pipeline.get(k)
                    if val is not None:
                        node_outputs[k] = val

        bridge.emit(
            QueryScoreEnd(
                run_id=run_id,
                query=query,
                predicted=it.get("predicted", ""),
                ground_truth=it.get("ground_truth", ""),
                hit=hit,
                total_time=pipeline.get("total_time"),
                node_outputs=node_outputs,
            )
        )
        any_pushed = True

    return any_pushed


def sync_langfuse_runs(
    store: Stores,
    backend_id: str,
    *,
    dataset_name: str = "ground_truth",
    backfill: bool = True,
    reset: bool = False,
) -> dict | None:
    """Configure Langfuse dataset name and optionally replay all runs."""
    if not backfill:
        logger.info("Langfuse dataset: %s (backfill disabled)", dataset_name)
        return None

    if reset:
        _save_state(store, backend_id, _fresh_state())
        logger.info("Langfuse push state reset — will re-push all runs.")

    n_runs = len(store.archive.list_all(backend_id))
    if n_runs == 0:
        logger.info("No completed dataset runs — skipping Langfuse backfill.")
        return None

    return push_all_runs(store, backend_id, dataset_name=dataset_name)


def push_all_runs(
    store: Stores,
    backend_id: str,
    *,
    dataset_name: str = _DEFAULT_DATASET_NAME,
    flush_every: int = 20,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Replay all historical measurement runs through a Langfuse bridge."""
    bridge = ObservabilityBridge.from_settings(store.base_dir, backend_id)
    lf_sink = bridge.langfuse_sink
    if lf_sink is None:
        return {"error": "Langfuse is disabled (missing credentials or LANGFUSE_ENABLED=false)"}

    state = _load_state(store, backend_id)
    already_done = set(state["backfilled_run_ids"])

    summaries = store.archive.list_all(backend_id)
    total_on_disk = len(summaries)

    def _emit(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    _emit("Collecting ground truth from dataset runs...")
    gt_map = _collect_ground_truth(store, backend_id, summaries)
    try:
        query_to_item_id = lf_sink.reconcile_dataset(
            dataset_name,
            gt_map,
            seed_items=state.get("dataset_items") or None,
        )
    except RuntimeError as exc:
        _emit(f"  SKIP: {exc}")
        return {"skipped": True, "reason": str(exc)}
    state["dataset_items"] = query_to_item_id

    groups: dict[str, list[dict]] = {origin: [] for origin in ORIGIN_ORDER}
    for s in summaries:
        rid = s.get("run_id", "")
        if rid in already_done:
            continue
        origin = classify_run_origin(s.get("source", ""))
        groups[origin].append(s)

    new_runs = sum(len(v) for v in groups.values())
    run_counter = 0
    origin_stats: dict[str, dict[str, Any]] = {}
    session_id = f"dataset_{backend_id}"

    for origin in ORIGIN_ORDER:
        runs = groups[origin]
        if not runs:
            continue

        _emit(f"  {origin}: {len(runs)} runs")

        accuracies: list[float] = []
        total_items = 0

        for run_summary in runs:
            rid = run_summary["run_id"]
            ok = _replay_run(
                bridge,
                store,
                backend_id,
                rid,
                schema=None,
                session_id=session_id,
                dataset_name=dataset_name,
            )
            if ok:
                detail = store.archive.load_by_id(backend_id, rid)
                if detail:
                    scores = detail.get("scores", {})
                    accuracies.append(scores.get("accuracy", 0.0))
                    total_items += len(detail.get("measurements", []))
                state["backfilled_run_ids"].append(rid)

            run_counter += 1
            if run_counter % flush_every == 0:
                bridge.flush()
                _emit(f"    [{run_counter}/{new_runs}] flushed")

        state["last_backfill_at"] = datetime.now(UTC).isoformat()
        _save_state(store, backend_id, state)

        best_acc = max(accuracies) if accuracies else 0.0
        avg_acc = sum(accuracies) / len(accuracies) if accuracies else 0.0
        origin_stats[origin] = {
            "n_runs": len(runs),
            "total_items": total_items,
            "best_accuracy": best_acc,
            "avg_accuracy": avg_acc,
        }

    bridge.flush()
    state["last_backfill_at"] = datetime.now(UTC).isoformat()
    _save_state(store, backend_id, state)

    return {
        "total_on_disk": total_on_disk,
        "new_runs": new_runs,
        "already_done": total_on_disk - new_runs,
        "origins": origin_stats,
        "session_id": session_id,
        "dataset_name": dataset_name,
        "dataset_items": len(query_to_item_id),
        "rate_limit_hit": False,
    }


"""Observability bridge — fans events from :mod:`events` to file + Langfuse sinks
under :func:`graceful` so observability never crashes the loop.

LangfuseSink persists id state to campaigns/{cycle_id}/langfuse/state.json
after every mutation so CLI-interrupted resumes produce one continuous trace.
"""


if TYPE_CHECKING:
    pass
__all__ = ["NodeTrace", "ObservabilityBridge", "observed_node"]


# Event-type → sink-method dispatch. Sinks lacking a method are silently
# skipped. The 4 mid-round write-points share ``on_write_point`` (file sink).
_DISPATCH: dict[type[Event], str] = {
    DatasetRegistered: "on_dataset_registered",
    CampaignStart: "on_campaign_start",
    DatasetRun: "on_dataset_run",
    RoundStart: "on_round_start",
    NodeStart: "on_node_start",
    NodeEnd: "on_node_end",
    RoundEnd: "on_round_end",
    PromptVersion: "on_prompt_version",
    CampaignEnd: "on_campaign_end",
    CandidateCreated: "on_write_point",
    CandidateScored: "on_write_point",
    RoundWinnerChosen: "on_write_point",
    L1CritiqueWritten: "on_write_point",
    LayerApplied: "on_layer_applied",
    QueryScoreStart: "on_query_score_start",
    QueryNodeSpan: "on_query_node_span",
    QueryScoreEnd: "on_query_score_end",
}


@dataclass
class NodeTrace:
    output: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    error: str | None = None


class ObservabilityBridge:
    """Fans events out to file + Langfuse sinks under :func:`graceful`."""

    def __init__(
        self,
        *,
        file_sink: FileSink,
        langfuse_sink: LangfuseSink | None,
        mlflow_sink: MLflowSink | None = None,
    ) -> None:

        self._enabled: bool = settings.OBS_ENABLED
        self._file = file_sink
        self._langfuse = langfuse_sink
        self._mlflow = mlflow_sink

    @classmethod
    def from_settings(
        cls,
        store_base_dir: str | Path,
        backend_id: str,
        *,
        langfuse: LangfuseLogger | None = None,
    ) -> ObservabilityBridge:

        file_sink = FileSink(store_base_dir, backend_id)
        lf_sink: LangfuseSink | None = None
        if langfuse is None:
            with graceful("Cloud backend init failed; file-only mode", level=logging.DEBUG):
                lf = LangfuseLogger.get_instance()
                if lf.enabled:
                    lf_sink = LangfuseSink(store_base_dir, backend_id, lf)
        elif langfuse.enabled:
            lf_sink = LangfuseSink(store_base_dir, backend_id, langfuse)
        mlflow_sink = MLflowSink(store_base_dir, backend_id) if settings.MLFLOW_ENABLED else None
        return cls(file_sink=file_sink, langfuse_sink=lf_sink, mlflow_sink=mlflow_sink)

    @classmethod
    def file_only(
        cls,
        store_base_dir: str | Path,
        backend_id: str,
    ) -> ObservabilityBridge:

        return cls(
            file_sink=FileSink(store_base_dir, backend_id),
            langfuse_sink=None,
            mlflow_sink=MLflowSink(store_base_dir, backend_id) if settings.MLFLOW_ENABLED else None,
        )

    def emit(self, event: Event) -> None:
        if not self._enabled:
            return
        method = _DISPATCH.get(type(event))
        if method is None:
            return
        for label, sink in (
            ("file", self._file),
            ("langfuse", self._langfuse),
            ("mlflow", self._mlflow),
        ):
            if sink is None:
                continue
            fn = getattr(sink, method, None)
            if fn is not None:
                with graceful(f"{label} sink {method} failed on {type(event).__name__}"):
                    fn(event)

    def emit_write_point(
        self,
        event_cls: type,
        *,
        campaign_id: str,
        round_num: int,
        **extra: Any,
    ) -> None:
        # events.jsonl is a pure mirror; resume uses trials/trial_NNNN.json.
        if not self._enabled:
            return
        with graceful(f"{event_cls.__name__} emit failed"):
            self.emit(event_cls(campaign_id=campaign_id, round_num=round_num, **extra))

    def flush(self) -> None:
        with graceful("Langfuse sink flush failed"):
            if self._langfuse is not None:
                self._langfuse.flush()

    def get_langfuse_trace_id(self, campaign_id: str) -> str | None:
        return self._langfuse.get_langfuse_trace_id(campaign_id) if self._langfuse else None

    @property
    def langfuse_sink(self) -> LangfuseSink | None:
        return self._langfuse

    def register_dataset(self, dataset_name: str, dataset: list) -> dict[str, str]:
        """Emit ``DatasetRegistered``, return ``{query: file_item_id}``."""
        if not self._enabled:
            return {}

        query_to_item_id: dict[str, str] = {}
        seen: set[str] = set()
        items: list[tuple[str, str]] = []
        for entry in dataset:
            if hasattr(entry, "query"):
                query = entry.query
                ground_truth = entry.ground_truth
            else:
                query = entry.get("query", "")
                ground_truth = entry.get("ground_truth", "")
            if not query or query in seen:
                continue
            seen.add(query)
            items.append((query, ground_truth))
            query_to_item_id[query] = hashlib.sha256(
                f"{dataset_name}:{query}".encode(),
            ).hexdigest()[:16]

        self.emit(DatasetRegistered(dataset_name=dataset_name, items=tuple(items)))
        return query_to_item_id

    @classmethod
    def start_campaign(
        cls,
        project_root: str | Path | None,
        backend_id: str | None,
        *,
        config_snapshot: dict[str, Any],
        baseline_accuracy: float,
        dataset: list,
        obs_campaign_id: str,
        langfuse_session_id: str | None,
    ) -> ObservabilityBridge | None:
        """Build a bridge, emit ``CampaignStart``, register the dataset."""

        if not (project_root and backend_id):
            return None

        bridge: ObservabilityBridge | None = None
        with graceful("Failed to create ObservabilityBridge"):
            bridge = cls.from_settings(project_root, backend_id)
        if bridge is None:
            return None

        with graceful("CampaignStart emit failed"):
            bridge.emit(
                CampaignStart(
                    campaign_id=obs_campaign_id,
                    config=config_snapshot,
                    baseline_accuracy=baseline_accuracy,
                    session_id=langfuse_session_id,
                )
            )
        with graceful("Dataset registration failed"):
            dataset_item_map = bridge.register_dataset(DATASET_NAME, dataset)
            if dataset_item_map:
                logger.debug(
                    "Registered %d dataset items for '%s'",
                    len(dataset_item_map),
                    DATASET_NAME,
                )
        return bridge

    def end_campaign(
        self,
        obs_campaign_id: str,
        *,
        best_accuracy: float,
        n_rounds: int,
        stop_reason: str,
        best_round: int,
    ) -> str | None:
        """Emit ``CampaignEnd``, flush, return the Langfuse trace id."""
        langfuse_trace_id: str | None = None
        with graceful("Bridge campaign end failed"):
            self.emit(
                CampaignEnd(
                    campaign_id=obs_campaign_id,
                    best_accuracy=best_accuracy,
                    n_rounds=n_rounds,
                    stop_reason=stop_reason,
                    best_round=best_round,
                )
            )
            self.flush()
            langfuse_trace_id = self.get_langfuse_trace_id(obs_campaign_id)
        return langfuse_trace_id


@asynccontextmanager
async def observed_node(
    node_id: str,
    node_type: str,
    *,
    obs: ObservabilityBridge | None,
    campaign_id: str,
    round_num: int,
    obs_type: str = "generation",
):
    """Time a node and emit ``NodeStart`` / ``NodeEnd`` around the body."""
    trace = NodeTrace()
    opened = False

    if obs is not None:
        with graceful(f"observed_node start failed for {node_id}"):
            obs.emit(
                NodeStart(
                    campaign_id=campaign_id,
                    round_num=round_num,
                    node_id=node_id,
                    node_type=node_type,
                    obs_type=obs_type,
                    input_data={},
                )
            )
            opened = True

    t0 = time.perf_counter()
    try:
        yield trace
    except Exception as exc:
        trace.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        trace.duration_ms = (time.perf_counter() - t0) * 1000
        if obs is not None and opened:
            with graceful(f"observed_node end failed for {node_id}"):
                obs.emit(
                    NodeEnd(
                        campaign_id=campaign_id,
                        round_num=round_num,
                        node_id=node_id,
                        output_data=trace.output,
                        metrics={"duration_ms": trace.duration_ms},
                        error=trace.error,
                    )
                )
