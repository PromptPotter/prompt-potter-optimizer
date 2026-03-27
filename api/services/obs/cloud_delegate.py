"""Cloud Langfuse delegation — extracted from ObsLogger.

Encapsulates all cloud Langfuse state and operations so ObsLogger
methods only need a single ``if self._cloud: self._cloud.on_X(...)`` call
instead of 10-25 line inline blocks with nested try/except.

All methods are no-ops on failure (log + swallow), matching ObsLogger's
"observability must never crash the main flow" contract.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from api.services.obs.langfuse_client import LangfuseLogger

logger = logging.getLogger(__name__)


class CloudDelegate:
    """Manages cloud Langfuse trace/observation lifecycle."""

    def __init__(self, lf: "LangfuseLogger") -> None:
        self._lf = lf
        self._trace_ids: dict[str, str] = {}
        self._active_trace_id: str | None = None
        self._active_session_id: str | None = None
        self._active_round_obs_id: str | None = None
        self._active_step_obs_ids: dict[str, str] = {}

    # --- Datasets ---

    def on_register_dataset(
        self,
        dataset_name: str,
        eval_data: list[dict],
    ) -> dict[str, str]:
        """Register dataset items in cloud Langfuse. Returns {query: item_id}."""
        result: dict[str, str] = {}
        try:
            if len(eval_data) > 100:
                logger.warning(
                    "Skipping Langfuse cloud dataset registration for %d items "
                    "(rate-limit risk). Use the dedicated Langfuse sync cell instead.",
                    len(eval_data),
                )
                return result

            self._lf.create_dataset(
                name=dataset_name,
                description="Ground truth queries for prompt evaluation",
                metadata={"n_items": len(eval_data)},
            )
            for entry in eval_data:
                query = entry.get("query", "")
                ground_truth = entry.get("ground_truth", "")
                if not query:
                    continue
                cloud_id = self._lf.create_dataset_item(
                    dataset_name=dataset_name,
                    input={"query": query},
                    expected_output=ground_truth,
                    metadata={"source": "eval_data"},
                )
                if cloud_id:
                    result[query] = cloud_id
        except Exception:
            logger.debug("Cloud Langfuse register_dataset failed", exc_info=True)
        return result

    # --- Dataset runs ---

    def on_dataset_run(
        self,
        run_id: str,
        content_hash: str,
        accuracy: float,
        hits: int,
        total: int,
        prompt_fields_id: str,
    ) -> None:
        """Push a dataset run span to cloud Langfuse."""
        try:
            if self._active_trace_id:
                self._lf.create_span(
                    trace_id=self._active_trace_id,
                    name=f"eval_{run_id[:8]}",
                    input={
                        "run_id": run_id,
                        "content_hash": content_hash,
                        "prompt_fields_id": prompt_fields_id,
                    },
                    output={
                        "accuracy": accuracy,
                        "hits": hits,
                        "total": total,
                    },
                    parent_observation_id=self._active_round_obs_id,
                    as_type="tool",
                )
        except Exception:
            logger.debug("Cloud Langfuse dataset_run failed", exc_info=True)

    # --- Campaign lifecycle ---

    def on_campaign_start(
        self,
        campaign_id: str,
        config: dict,
        baseline_accuracy: float,
        session_id: str | None = None,
    ) -> None:
        """Create cloud trace for campaign start."""
        try:
            cloud_id = self._lf.create_trace(
                name="optimization_loop",
                input={
                    "campaign_id": campaign_id,
                    "baseline_accuracy": baseline_accuracy,
                    "config": config,
                },
                session_id=session_id,
                tags=["campaign", "optimization_loop"],
            )
            if cloud_id:
                self._trace_ids[campaign_id] = cloud_id
                self._active_trace_id = cloud_id
                self._active_session_id = session_id
        except Exception:
            logger.debug("Cloud Langfuse campaign_start failed", exc_info=True)

    def on_campaign_end(
        self,
        campaign_id: str,
        best_accuracy: float,
        n_rounds: int,
        stop_reason: str,
        best_round: int,
    ) -> None:
        """Finalize cloud trace for campaign end."""
        try:
            cloud_trace_id = self._trace_ids.get(campaign_id)
            if cloud_trace_id:
                self._lf.create_score(
                    trace_id=cloud_trace_id,
                    name="best_accuracy",
                    value=best_accuracy,
                    comment=f"Best at round {best_round}, stop: {stop_reason}",
                )
                self._lf.update_trace(
                    trace_id=cloud_trace_id,
                    output={
                        "best_accuracy": best_accuracy,
                        "n_rounds": n_rounds,
                        "stop_reason": stop_reason,
                    },
                    metadata={
                        "stop_reason": stop_reason,
                        "best_round": best_round,
                    },
                )
                self._lf.end_trace(cloud_trace_id)
        except Exception:
            logger.debug("Cloud Langfuse campaign_end failed", exc_info=True)
        finally:
            self._active_trace_id = None
            self._active_session_id = None

    # --- Rounds ---

    def on_round_start(self, campaign_id: str, round_num: int) -> None:
        """Open a cloud span for a round."""
        try:
            cloud_trace_id = self._trace_ids.get(campaign_id)
            if cloud_trace_id:
                obs_id = self._lf.start_span(
                    trace_id=cloud_trace_id,
                    name=f"round_{round_num}",
                    input={"round": round_num},
                    metadata={"round": round_num},
                    as_type="span",
                )
                self._active_round_obs_id = obs_id
        except Exception:
            logger.debug("Cloud Langfuse round_start failed", exc_info=True)

    def on_round_end(
        self,
        campaign_id: str,
        round_num: int,
        accuracy: float,
        improved: bool,
        next_action: str,
        candidate_scores: list[dict],
        optimizer_templates: list[str] | None = None,
    ) -> None:
        """Close cloud round span + write accuracy score."""
        try:
            cloud_trace_id = self._trace_ids.get(campaign_id)
            if cloud_trace_id:
                if self._active_round_obs_id:
                    round_meta: dict = {
                        "round": round_num,
                        "candidates_evaluated": len(candidate_scores),
                    }
                    if optimizer_templates:
                        round_meta["optimizer_templates"] = optimizer_templates
                    self._lf.end_observation(
                        self._active_round_obs_id,
                        output={
                            "winner_accuracy": accuracy,
                            "improved": improved,
                            "next_action": next_action,
                            "candidates_evaluated": len(candidate_scores),
                        },
                        metadata=round_meta,
                    )
                self._lf.create_score(
                    trace_id=cloud_trace_id,
                    name=f"accuracy_round_{round_num}",
                    value=accuracy,
                    comment=f"Round {round_num}: {'improved' if improved else 'no change'}",
                )
        except Exception:
            logger.debug("Cloud Langfuse round_end failed", exc_info=True)
        finally:
            self._active_round_obs_id = None

    # --- Node-level ---

    def on_node_start(
        self,
        node_id: str,
        node_type: str,
        obs_type: str,
        input_data: dict,
        metadata: dict | None = None,
    ) -> None:
        """Open a cloud span for a node step."""
        try:
            if self._active_trace_id:
                as_type = obs_type if obs_type in ("generation", "span") else "span"
                cloud_obs_id = self._lf.start_span(
                    trace_id=self._active_trace_id,
                    name=node_id,
                    input=input_data,
                    metadata={"node_type": node_type, **(metadata or {})},
                    parent_observation_id=self._active_round_obs_id,
                    as_type=as_type,
                )
                if cloud_obs_id:
                    self._active_step_obs_ids[node_id] = cloud_obs_id
        except Exception:
            logger.debug("Cloud Langfuse node_start failed", exc_info=True)

    def on_node_end(
        self,
        node_id: str,
        output_data: dict | None = None,
        metrics: dict | None = None,
        error: str | None = None,
    ) -> None:
        """Close a cloud span for a node step."""
        try:
            cloud_obs_id = self._active_step_obs_ids.pop(node_id, None)
            if cloud_obs_id:
                meta: dict = {}
                if metrics:
                    meta["metrics"] = metrics
                if error:
                    meta["error"] = error
                self._lf.end_observation(
                    cloud_obs_id,
                    output=output_data,
                    metadata=meta or None,
                )
        except Exception:
            logger.debug("Cloud Langfuse node_end failed", exc_info=True)

    # --- Prompts ---

    def on_prompt_version(
        self,
        prompt_fields_id: str,
        layer1_fields: dict,
        parent_id: str | None = None,
    ) -> None:
        """Push prompt version span to cloud Langfuse."""
        try:
            if self._trace_ids:
                cloud_trace_id = next(reversed(self._trace_ids.values()))
                self._lf.create_span(
                    trace_id=cloud_trace_id,
                    name="prompt_version",
                    input={
                        "prompt_fields_id": prompt_fields_id,
                        "parent_id": parent_id,
                    },
                    output={
                        "family": "ranking_prompt",
                        "version": prompt_fields_id[:8] if prompt_fields_id else "unknown",
                    },
                    metadata={"layer1_fields": layer1_fields},
                    parent_observation_id=self._active_round_obs_id,
                    as_type="tool",
                )
        except Exception:
            logger.debug("Cloud Langfuse prompt_version failed", exc_info=True)

    # --- Utility ---

    def get_trace_id(self, campaign_id: str) -> str | None:
        """Return cloud trace ID for a campaign."""
        return self._trace_ids.get(campaign_id)

    def flush(self) -> None:
        """Flush cloud Langfuse."""
        try:
            self._lf.flush()
        except Exception:
            logger.debug("Cloud Langfuse flush failed", exc_info=True)
