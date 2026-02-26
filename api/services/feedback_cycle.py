"""
Feedback cycling orchestrator for iterative prompt optimization.

Wraps optimizer nodes (InitNode → GrowFilterNode → AnalysisEvalNode) in a
counter-based loop with 3-path routing based on ``next_action``:

- **generate** (Layer 1): vary persona, instruction, thinking_style, etc.
- **refine_context** (Layer 2): adjust context and parameters
- **modify_plan** (Layer 3): change the overall strategy/plan
- **stop**: terminate the loop

Stopping conditions:
- ``max_rounds`` reached
- ``patience`` consecutive non-improving rounds exhausted
- ``next_action == "stop"`` from analysis node
- ``winner_accuracy >= 1.0`` (perfect score)
"""

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class CycleConfig(BaseModel):
    """Configuration for feedback cycling."""

    max_rounds: int = Field(10, description="Maximum optimization rounds")
    patience: int = Field(3, description="Stop after N consecutive non-improvements")
    n_variants: int = Field(5, description="Candidates per round")
    creativity: float = Field(0.7, description="Temperature for candidate generation")
    improvement_threshold: float = Field(0.01, description="Min accuracy delta")
    model: str | None = Field(None, description="LLM model identifier")
    provider: str | None = Field(None, description="LLM provider")
    backend_url: str = Field(..., description="Backend URL for evaluation")
    backend_id: str = Field("", description="Backend identifier for caching")
    project_root: str = Field("", description="Project root for store")
    generate_suggestions: bool = Field(False, description="LLM suggestions each round")
    pipeline_params: dict | None = Field(None, description="Pipeline parameter overrides")
    session_terms: list[str] | None = Field(None, description="Backend session terms")
    temperature: float = Field(0.0, description="Temperature for content hash")
    queries_per_eval: int = Field(0, description="Subsample size (0 = use all)")
    seed: int = Field(42, description="Random seed for subsampling")


class CycleRoundResult(BaseModel):
    """Result of a single feedback cycle round."""

    round: int
    label: str
    accuracy: float
    hits: int
    total: int
    improved: bool
    next_action: str
    prompt_state: dict
    results: list[dict] = Field(default_factory=list)
    candidates_evaluated: int
    candidate_scores: list[dict] = Field(default_factory=list)


class CycleResult(BaseModel):
    """Final result of the feedback cycling process."""

    rounds: list[CycleRoundResult]
    n_rounds: int
    best_accuracy: float
    best_round: int
    baseline_accuracy: float
    winner_prompt_state: dict
    stop_reason: str
    started_at: str
    finished_at: str
    langfuse_trace_id: str | None = None
    cycle_id: str | None = None
    resumed_from_round: int = 0


def cycle_config_identity(
    config: CycleConfig,
    baseline_rendered: str,
    eval_data: list[dict],
) -> str:
    """Compute a stable identity hash for a feedback cycle configuration.

    Covers optimization-relevant fields only so the same config produces the
    same cycle_id across kernel restarts.  Infrastructure fields (backend_url,
    project_root, etc.) are excluded.
    """
    payload = json.dumps(
        {
            "max_rounds": config.max_rounds,
            "patience": config.patience,
            "n_variants": config.n_variants,
            "creativity": config.creativity,
            "improvement_threshold": config.improvement_threshold,
            "model": config.model,
            "provider": config.provider,
            "temperature": config.temperature,
            "queries_per_eval": config.queries_per_eval,
            "seed": config.seed,
            "baseline_rendered": baseline_rendered,
            "eval_data_pairs": sorted(
                (d.get("query", ""), d.get("ground_truth", ""))
                for d in eval_data
            ),
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return f"cycle_{digest}"


def _restore_completed_result(
    campaign_data: dict,
    store: Any,
    backend_id: str,
    cycle_id: str,
) -> CycleResult:
    """Reconstruct a CycleResult from a completed campaign on disk."""
    rounds: list[CycleRoundResult] = []
    for trial_summary in campaign_data.get("trials", []):
        detail = store.load_trial(backend_id, cycle_id, trial_summary["round"])
        if detail is None:
            continue
        rounds.append(CycleRoundResult(
            round=detail["round"],
            label=detail.get("label", ""),
            accuracy=detail.get("accuracy", 0.0),
            hits=detail.get("hits", 0),
            total=detail.get("total", 0),
            improved=detail.get("improved", False),
            next_action=detail.get("next_action", "generate"),
            prompt_state=detail.get("prompt_state", {}),
            results=detail.get("results", []),
            candidates_evaluated=detail.get("candidates_evaluated", 0),
            candidate_scores=detail.get("candidate_scores", []),
        ))

    return CycleResult(
        rounds=rounds,
        n_rounds=campaign_data.get("n_rounds", len(rounds)),
        best_accuracy=campaign_data.get("best_accuracy", 0.0),
        best_round=campaign_data.get("best_round", -1),
        baseline_accuracy=campaign_data.get("baseline_accuracy", 0.0),
        winner_prompt_state=rounds[-1].prompt_state if rounds else {},
        stop_reason=campaign_data.get("stop_reason", "completed"),
        started_at=campaign_data.get("created_at", ""),
        finished_at=campaign_data.get("finished_at", ""),
        cycle_id=cycle_id,
        resumed_from_round=0,
    )


def _save_round_candidates(
    campaign_store: Any,
    backend_id: str,
    cycle_id: str,
    round_num: int,
    candidates: list[dict],
) -> None:
    """Persist generated candidates before evaluation (mid-round checkpoint)."""
    from api.services.stores.base import write_json

    path = (
        campaign_store._trial_dir(backend_id, cycle_id)
        / f"round_{round_num:04d}_candidates.json"
    )
    write_json(path, candidates)
    logger.info("Saved %d candidates for round %d → %s", len(candidates), round_num, path.name)


def _load_round_candidates(
    campaign_store: Any,
    backend_id: str,
    cycle_id: str,
    round_num: int,
) -> list[dict] | None:
    """Load persisted candidates for a round.  Returns None if not on disk."""
    from api.services.stores.base import read_json

    path = (
        campaign_store._trial_dir(backend_id, cycle_id)
        / f"round_{round_num:04d}_candidates.json"
    )
    if not path.exists():
        return None
    return read_json(path)


def _restore_round_state(
    campaign_data: dict,
    store: Any,
    backend_id: str,
    cycle_id: str,
    threshold: float,
) -> tuple:
    """Restore in-progress round state from persisted trials.

    Returns:
        (rounds, current_ps, current_accuracy, current_results,
         best_accuracy, best_round, best_ps, stall_count)
    """
    rounds: list[CycleRoundResult] = []
    current_ps: dict = {}
    current_accuracy = 0.0
    current_results: list = []
    best_accuracy = 0.0
    best_round = -1
    best_ps: dict = {}
    stall_count = 0

    for trial_summary in campaign_data.get("trials", []):
        detail = store.load_trial(backend_id, cycle_id, trial_summary["round"])
        if detail is None:
            continue
        rr = CycleRoundResult(
            round=detail["round"],
            label=detail.get("label", ""),
            accuracy=detail.get("accuracy", 0.0),
            hits=detail.get("hits", 0),
            total=detail.get("total", 0),
            improved=detail.get("improved", False),
            next_action=detail.get("next_action", "generate"),
            prompt_state=detail.get("prompt_state", {}),
            results=detail.get("results", []),
            candidates_evaluated=detail.get("candidates_evaluated", 0),
            candidate_scores=detail.get("candidate_scores", []),
        )
        rounds.append(rr)

        current_ps = rr.prompt_state
        current_accuracy = rr.accuracy
        current_results = rr.results

        if rr.accuracy > best_accuracy:
            best_accuracy = rr.accuracy
            best_round = rr.round
            best_ps = rr.prompt_state

        # Reconstruct stall_count from trial detail (preferred) or improved flag
        if "stall_count" in detail:
            stall_count = detail["stall_count"]
        elif rr.improved:
            stall_count = 0
        else:
            stall_count += 1

    return (
        rounds, current_ps, current_accuracy, current_results,
        best_accuracy, best_round, best_ps, stall_count,
    )


async def run_feedback_cycle(
    instruction: str,
    eval_data: list[dict[str, Any]],
    config: CycleConfig,
    *,
    improvement_areas: str = "",
    baseline_prompt_state: dict | None = None,
    baseline_accuracy: float = 0.0,
    baseline_results: list | None = None,
    on_round_complete: Callable | None = None,
    on_candidate_eval: Callable | None = None,
    on_query_eval: Callable | None = None,
    langfuse_session_id: str | None = None,
) -> CycleResult:
    """Run iterative optimization with feedback cycling.

    Executes:
    1. InitNode — create baseline PromptState (skipped if baseline_prompt_state provided)
    2. Loop: GrowFilterNode → AnalysisEvalNode → route by next_action
    3. Stop when patience exhausted, max_rounds reached, or perfect score

    Args:
        instruction: Raw prompt instruction text (used by InitNode if no baseline).
        eval_data: Evaluation dataset (list of query dicts).
        config: Cycle configuration.
        improvement_areas: Domain-expert guidance for context restructuring.
        baseline_prompt_state: Existing baseline PromptState (serialized dict).
            If provided, InitNode is skipped and this is used as the starting point.
        baseline_accuracy: Accuracy of the baseline (used when baseline_prompt_state
            is provided).
        baseline_results: Eval results for the baseline (for failure analysis).
        on_round_complete: Optional callback after each round:
            ``(round_result: CycleRoundResult, stall_count: int)``
        on_candidate_eval: Optional callback after each candidate evaluation:
            ``(candidate_idx: int, n_candidates: int, scores: dict)``
        langfuse_session_id: Optional session ID for grouping Langfuse traces.

    Returns:
        CycleResult with all rounds and final winner.
    """
    import random as _random

    from api.nodes.optimizer_nodes import (
        AnalysisEvalNode,
        GrowFilterNode,
        InitNode,
    )
    from api.services.backend_client import BackendClient
    from api.services.obs.observability_logger import ObsLogger

    started_at = datetime.now(timezone.utc).isoformat()

    # Single observability gateway (file + cloud Langfuse)
    obs: ObsLogger | None = None
    if config.project_root and config.backend_id:
        try:
            obs = ObsLogger(config.project_root, config.backend_id)
        except Exception:
            logger.warning("Failed to create ObsLogger", exc_info=True)

    # Initialize backend session (required before /matches calls)
    if config.session_terms:
        bc = BackendClient(config.backend_url)
        await bc.init_session(config.session_terms)

    # Subsample eval data if configured
    if config.queries_per_eval > 0 and len(eval_data) > config.queries_per_eval:
        rng = _random.Random(config.seed)
        round_eval_data = rng.sample(eval_data, config.queries_per_eval)
    else:
        round_eval_data = eval_data

    # -- Step 1: Initialize baseline --
    if baseline_prompt_state is not None:
        # Use provided baseline — skip InitNode
        current_ps = baseline_prompt_state
        current_accuracy = baseline_accuracy
        current_results: list = baseline_results or []
        logger.info(
            "Using provided baseline (acc=%.3f)", current_accuracy,
        )
    else:
        # Run InitNode to create baseline from instruction
        init_node = InitNode(
            node_id="cycle_init",
            config={"model": config.model, "provider": config.provider},
        )
        init_out = await init_node.process({
            "instruction": instruction,
            "improvement_areas": improvement_areas,
        })
        current_ps = init_out.prompt_state
        current_accuracy = 0.0
        current_results = []

    # -- Resume detection --
    campaign_store = None
    cycle_id: str | None = None
    resumed_from_round = 0

    if config.project_root and config.backend_id:
        try:
            from api.models.prompt_state import PromptState as _PS
            from api.services.stores.campaign_store import CampaignStore

            store_base = __import__("pathlib").Path(config.project_root)
            campaign_store = CampaignStore(store_base)

            # Compute baseline rendered prompt for identity hash
            if baseline_prompt_state is not None:
                _bl_rendered = _PS(**baseline_prompt_state).render()
            else:
                _bl_rendered = _PS(**current_ps).render()

            cycle_id = cycle_config_identity(config, _bl_rendered, eval_data)
            logger.info("Cycle identity: %s", cycle_id)

            existing = campaign_store.load(config.backend_id, cycle_id)
            if existing is not None:
                if existing.get("status") == "completed":
                    logger.info(
                        "Cycle %s already completed — returning cached result",
                        cycle_id,
                    )
                    return _restore_completed_result(
                        existing, campaign_store, config.backend_id, cycle_id,
                    )

                # In-progress: restore round state
                if existing.get("trials"):
                    (
                        rounds, current_ps, current_accuracy, current_results,
                        best_accuracy, best_round, best_ps, stall_count,
                    ) = _restore_round_state(
                        existing, campaign_store, config.backend_id,
                        cycle_id, config.improvement_threshold,
                    )
                    resumed_from_round = len(rounds)
                    logger.info(
                        "Resuming cycle %s from round %d (best_acc=%.3f)",
                        cycle_id, resumed_from_round, best_accuracy,
                    )
                else:
                    logger.info(
                        "Cycle %s exists but has no completed rounds "
                        "— starting fresh",
                        cycle_id,
                    )
            else:
                # Create new campaign entry
                campaign_store.create(config.backend_id, cycle_id, {
                    "type": "feedback_cycle",
                    "config": config.model_dump(),
                    "baseline_accuracy": baseline_accuracy,
                })
        except Exception:
            logger.warning("Cycle resume setup failed — running fresh", exc_info=True)
            campaign_store = None
            cycle_id = None

    # Campaign ID for observability
    obs_campaign_id = cycle_id or f"campaign_{started_at[:19].replace(':', '')}"
    dataset_name: str | None = None
    dataset_item_map: dict[str, str] | None = None
    if obs:
        try:
            obs.log_campaign_start(
                campaign_id=obs_campaign_id,
                config=config.model_dump(),
                baseline_accuracy=baseline_accuracy,
                session_id=langfuse_session_id,
            )
        except Exception:
            logger.warning("ObsLogger.log_campaign_start failed", exc_info=True)

        # Register dataset items for linking evaluations
        try:
            dataset_name = "termnorm_ground_truth"
            dataset_item_map = obs.register_dataset(dataset_name, eval_data)
            if dataset_item_map:
                logger.info(
                    "Registered %d dataset items for '%s'",
                    len(dataset_item_map), dataset_name,
                )
        except Exception:
            logger.warning("Dataset registration failed", exc_info=True)
            dataset_item_map = None

    # Initialize round state for fresh start (skip if resumed)
    if resumed_from_round == 0:
        rounds = []
        best_accuracy = current_accuracy
        best_round = -1
        best_ps = current_ps
        stall_count = 0
    stop_reason = "max_rounds"

    # -- Step 2: Iterative loop --
    for round_num in range(resumed_from_round, config.max_rounds):
        logger.info(
            "Feedback cycle round %d (acc=%.3f, stall=%d/%d)",
            round_num, current_accuracy, stall_count, config.patience,
        )

        # Try loading persisted candidates (mid-round resume)
        persisted = None
        if campaign_store and cycle_id:
            persisted = _load_round_candidates(
                campaign_store, config.backend_id, cycle_id, round_num,
            )

        if persisted is not None:
            logger.info(
                "Loaded %d persisted candidates for round %d",
                len(persisted), round_num,
            )
            candidates_for_eval = persisted
        else:
            logger.debug("No persisted candidates for round %d — generating fresh", round_num)
            # Grow: generate fresh candidates via LLM
            grow_node = GrowFilterNode(
                node_id=f"cycle_grow_{round_num}",
                config={
                    "model": config.model,
                    "provider": config.provider,
                    "n_variants": config.n_variants,
                    "creativity": config.creativity,
                },
            )
            grow_out = await grow_node.process({
                "prompt_state": current_ps,
                "accuracy": current_accuracy,
                "results": current_results,
            })
            candidates_for_eval = grow_out.candidates

            # Persist before evaluation (crash-safe checkpoint)
            if campaign_store and cycle_id:
                _save_round_candidates(
                    campaign_store, config.backend_id, cycle_id,
                    round_num, candidates_for_eval,
                )

        # Evaluate: score candidates and select winner
        eval_config: dict[str, Any] = {
            "model": config.model,
            "provider": config.provider,
            "backend_url": config.backend_url,
            "backend_id": config.backend_id,
            "project_root": config.project_root,
            "improvement_threshold": config.improvement_threshold,
            "generate_suggestions": config.generate_suggestions,
            "pipeline_params": config.pipeline_params,
            "temperature": config.temperature,
            "on_candidate_eval": on_candidate_eval,
            "on_query_eval": on_query_eval,
            "obs": obs,
        }
        if dataset_name and dataset_item_map:
            eval_config["dataset_name"] = dataset_name
            eval_config["dataset_item_map"] = dataset_item_map

        eval_node = AnalysisEvalNode(
            node_id=f"cycle_eval_{round_num}",
            config=eval_config,
        )
        eval_out = await eval_node.process({
            "candidates": candidates_for_eval,
            "eval_data": round_eval_data,
            "baseline_prompt_state": current_ps,
            "baseline_accuracy": current_accuracy,
            "baseline_results": current_results,
            "baseline_label": f"round_{round_num}" if round_num > 0 else "baseline",
        })

        # Record round
        round_result = CycleRoundResult(
            round=round_num,
            label=eval_out.winner.get("label", f"round_{round_num}"),
            accuracy=eval_out.winner_accuracy,
            hits=eval_out.winner.get("hits", 0),
            total=eval_out.winner.get("total", 0),
            improved=eval_out.improved,
            next_action=eval_out.next_action,
            prompt_state=eval_out.winner_prompt_state,
            results=eval_out.winner_results,
            candidates_evaluated=eval_out.winner.get("candidates_evaluated", 0),
            candidate_scores=eval_out.candidate_scores,
        )
        rounds.append(round_result)

        # Observability: round logging (file + cloud Langfuse via ObsLogger)
        if obs:
            try:
                obs.log_round(
                    campaign_id=obs_campaign_id,
                    round_num=round_num,
                    accuracy=eval_out.winner_accuracy,
                    hits=eval_out.winner.get("hits", 0),
                    total=eval_out.winner.get("total", 0),
                    improved=eval_out.improved,
                    next_action=eval_out.next_action,
                    winner_prompt_state_id=eval_out.winner_prompt_state.get(
                        "id", "",
                    ),
                    candidate_scores=eval_out.candidate_scores,
                    model=config.model or "",
                    temperature=config.temperature,
                    n_variants=config.n_variants,
                )
            except Exception:
                logger.warning("ObsLogger.log_round failed", exc_info=True)

        # Prompt version logging (separate so round logging isn't blocked)
        if obs:
            try:
                from api.models.prompt_state import PromptState
                winner_ps = PromptState(**eval_out.winner_prompt_state)
                obs.log_prompt_version(
                    prompt_state_id=winner_ps.id,
                    rendered_prompt=winner_ps.render(),
                    layer1_fields={
                        f: getattr(winner_ps, f)
                        for f in [
                            "persona", "task_intent", "problem_description",
                            "instruction", "thinking_style", "answer_format",
                        ]
                    },
                    parent_id=winner_ps.parent_id,
                )
            except Exception:
                logger.warning(
                    "ObsLogger.log_prompt_version failed", exc_info=True,
                )

        # Update current state — pass results forward for failure analysis
        current_ps = eval_out.winner_prompt_state
        current_accuracy = eval_out.winner_accuracy
        current_results = eval_out.winner_results

        if current_accuracy > best_accuracy:
            best_accuracy = current_accuracy
            best_round = round_num
            best_ps = current_ps

        # Track stalls
        if eval_out.improved:
            stall_count = 0
        else:
            stall_count += 1

        # Notify callback
        if on_round_complete:
            on_round_complete(round_result, stall_count)

        # Checkpoint round to disk
        if campaign_store and cycle_id:
            try:
                campaign_store.add_trial(config.backend_id, cycle_id, {
                    "trial_id": f"round_{round_num}",
                    "round": round_num,
                    "label": round_result.label,
                    "accuracy": round_result.accuracy,
                    "hits": round_result.hits,
                    "total": round_result.total,
                    "improved": round_result.improved,
                    "next_action": round_result.next_action,
                    "prompt_state": round_result.prompt_state,
                    "results": round_result.results,
                    "candidates_evaluated": round_result.candidates_evaluated,
                    "candidate_scores": [
                        s if isinstance(s, dict) else s
                        for s in round_result.candidate_scores
                    ],
                    "stall_count": stall_count,
                })
            except Exception:
                logger.warning("Round checkpoint failed", exc_info=True)

        # Check stopping conditions
        if current_accuracy >= 1.0:
            stop_reason = "perfect_score"
            logger.info("Perfect score reached at round %d", round_num)
            break

        if stall_count >= config.patience:
            stop_reason = "patience_exhausted"
            logger.info(
                "Patience exhausted after %d stalls at round %d",
                stall_count, round_num,
            )
            break

        if eval_out.next_action == "stop":
            stop_reason = "next_action_stop"
            logger.info("Analysis node signaled stop at round %d", round_num)
            break

    finished_at = datetime.now(timezone.utc).isoformat()

    # Mark campaign as completed on disk
    if campaign_store and cycle_id:
        try:
            campaign_store.update(config.backend_id, cycle_id, {
                "status": "completed",
                "stop_reason": stop_reason,
                "best_accuracy": best_accuracy,
                "best_round": best_round,
                "n_rounds": len(rounds),
                "finished_at": finished_at,
            })
        except Exception:
            logger.warning("Campaign completion update failed", exc_info=True)

    # Finalize campaign observability (file + cloud via ObsLogger)
    cloud_trace_id: str | None = None
    if obs:
        try:
            obs.log_campaign_end(
                campaign_id=obs_campaign_id,
                best_accuracy=best_accuracy,
                n_rounds=len(rounds),
                stop_reason=stop_reason,
                best_round=best_round,
            )
            obs.flush()
            cloud_trace_id = obs.get_cloud_trace_id(obs_campaign_id)
        except Exception:
            logger.warning("ObsLogger campaign end failed", exc_info=True)

    return CycleResult(
        rounds=rounds,
        n_rounds=len(rounds),
        best_accuracy=best_accuracy,
        best_round=best_round,
        baseline_accuracy=rounds[0].accuracy if rounds else current_accuracy,
        winner_prompt_state=best_ps,
        stop_reason=stop_reason,
        started_at=started_at,
        finished_at=finished_at,
        langfuse_trace_id=cloud_trace_id,
        cycle_id=cycle_id,
        resumed_from_round=resumed_from_round,
    )
