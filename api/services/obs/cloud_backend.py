"""Cloud Langfuse observability backend.

Delegates ObsLogger events to cloud Langfuse. Each method mirrors an
ObsLogger public method.  All methods are individually wrapped in
try/except so cloud failures never propagate.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class CloudObsBackend:
    """Delegates observability events to cloud Langfuse.

    Each method mirrors an ObsLogger public method.  All methods are
    individually wrapped in try/except so cloud failures never propagate.
    """

    def __init__(self, langfuse):
        self._lf = langfuse
        self._trace_ids: dict[str, str] = {}
        self._active_trace_id: str | None = None
        self._active_session_id: str | None = None
        self._active_round_obs_id: str | None = None

    def on_dataset_registered(
        self,
        dataset_name: str,
        eval_data: list[dict],
        query_to_item_id: dict[str, str],
    ) -> dict[str, str]:
        """Push dataset + items to cloud. Returns updated query->item_id map."""
        try:
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
                    query_to_item_id[query] = cloud_id
        except Exception:
            logger.debug("Cloud Langfuse register_dataset failed", exc_info=True)
        return query_to_item_id

    def on_campaign_start(
        self,
        campaign_id: str,
        config: dict,
        baseline_accuracy: float,
        session_id: str | None,
    ) -> None:
        """Create cloud trace for campaign, set active trace state."""
        try:
            cloud_id = self._lf.create_trace(
                name="feedback_cycle",
                input={
                    "campaign_id": campaign_id,
                    "baseline_accuracy": baseline_accuracy,
                    "config": config,
                },
                session_id=session_id,
                tags=["campaign", "feedback_cycle"],
            )
            if cloud_id:
                self._trace_ids[campaign_id] = cloud_id
                self._active_trace_id = cloud_id
                self._active_session_id = session_id
        except Exception:
            logger.debug("Cloud Langfuse campaign_start failed", exc_info=True)

    def on_round_start(self, campaign_id: str, round_num: int) -> None:
        """Open a long-running round span in cloud Langfuse."""
        cloud_trace_id = self._trace_ids.get(campaign_id)
        if not cloud_trace_id:
            return
        try:
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
        *,
        optimizer_templates: list[str] | None = None,
    ) -> None:
        """Close the round span, attach score."""
        cloud_trace_id = self._trace_ids.get(campaign_id)
        if not cloud_trace_id:
            self._active_round_obs_id = None
            return
        try:
            if self._active_round_obs_id:
                meta: dict = {
                    "round": round_num,
                    "candidates_evaluated": len(candidate_scores),
                }
                if optimizer_templates:
                    meta["optimizer_templates"] = optimizer_templates
                self._lf.end_observation(
                    self._active_round_obs_id,
                    output={
                        "winner_accuracy": accuracy,
                        "improved": improved,
                        "next_action": next_action,
                        "candidates_evaluated": len(candidate_scores),
                    },
                    metadata=meta,
                )
            self._lf.create_score(
                trace_id=cloud_trace_id,
                name=f"accuracy_round_{round_num}",
                value=accuracy,
                comment=f"Round {round_num}: "
                        f"{'improved' if improved else 'no change'}",
            )
        except Exception:
            logger.debug("Cloud Langfuse round_end failed", exc_info=True)
        finally:
            self._active_round_obs_id = None

    def on_round(
        self,
        campaign_id: str,
        round_num: int,
        accuracy: float,
        improved: bool,
        next_action: str,
        candidate_scores: list[dict],
    ) -> None:
        """Fire-and-forget round: open + close immediately (legacy path)."""
        self.on_round_start(campaign_id, round_num)
        self.on_round_end(
            campaign_id, round_num, accuracy,
            improved, next_action, candidate_scores,
        )

    def on_dataset_run(
        self,
        run_id: str,
        content_hash: str,
        accuracy: float,
        hits: int,
        total: int,
        prompt_state_id: str,
    ) -> None:
        """Push eval-run span to cloud, nesting under active round if present."""
        if not self._active_trace_id:
            return
        try:
            self._lf.create_span(
                trace_id=self._active_trace_id,
                name=f"eval_{run_id[:8]}",
                input={
                    "run_id": run_id,
                    "content_hash": content_hash,
                    "prompt_state_id": prompt_state_id,
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

    def on_prompt_version(
        self,
        prompt_state_id: str,
        parent_id: str | None,
        family: str,
        version: str,
        layer1_fields: dict,
    ) -> None:
        """Attach prompt version span to most recent campaign trace."""
        if not self._trace_ids:
            return
        try:
            cloud_trace_id = next(reversed(self._trace_ids.values()))
            self._lf.create_span(
                trace_id=cloud_trace_id,
                name="prompt_version",
                input={
                    "prompt_state_id": prompt_state_id,
                    "parent_id": parent_id,
                },
                output={"family": family, "version": version},
                metadata={"layer1_fields": layer1_fields},
                parent_observation_id=self._active_round_obs_id,
                as_type="tool",
            )
        except Exception:
            logger.debug("Cloud Langfuse prompt_version failed", exc_info=True)

    def on_campaign_end(
        self,
        campaign_id: str,
        best_accuracy: float,
        n_rounds: int,
        stop_reason: str,
        best_round: int,
    ) -> None:
        """Finalize cloud trace: score + update + end. Clear active state."""
        cloud_trace_id = self._trace_ids.get(campaign_id)
        if cloud_trace_id:
            try:
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

        self._active_trace_id = None
        self._active_session_id = None

    def flush(self) -> None:
        """Flush cloud Langfuse."""
        try:
            self._lf.flush()
        except Exception:
            logger.debug("Cloud Langfuse flush failed", exc_info=True)

    def get_trace_id(self, campaign_id: str) -> str | None:
        """Return cloud trace ID for a campaign, or None."""
        return self._trace_ids.get(campaign_id)
