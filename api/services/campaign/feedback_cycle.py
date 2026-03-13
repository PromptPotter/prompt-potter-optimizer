"""
Feedback cycling orchestrator for iterative prompt optimization.

Runs ``generate_candidates()`` → ``evaluate_and_select_winner()`` in a
counter-based loop.  Each round generates Layer 1 variants and evaluates
them against the current best.

Stopping conditions:
- ``max_rounds`` reached
- ``patience`` consecutive non-improving rounds exhausted
- ``next_action == "stop"`` from suggestion analysis (when enabled)
- ``winner_accuracy >= 1.0`` (perfect score)
"""

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from api.models.phase_event import PhaseEvent
from api.models.pipeline_schema import PipelineSchema
from api.models.prompt_state import PromptState, diff as prompt_diff
from api.models.search_point import SearchPoint
from api.nodes.optimizer_nodes import InitNode
from api.services.backend_client import BackendClient
from api.services.campaign import layer_transitions
from api.services.constants import DATASET_NAME
from api.services.prompt_eval import EvalContext, compute_composite_score
from api.services.query_utils import subsample_queries

# Module-level imports for functions that tests mock via monkeypatch.
# Using module references ensures patching at the source module works.
from api.services import llm_client as _llm_client
from api.services import prompt_optimizer as _prompt_optimizer

if TYPE_CHECKING:
    from api.services.obs.observability_logger import ObsLogger
    from api.services.project_store import ProjectStore
    from api.services.stores.campaign_store import CampaignStore

logger = logging.getLogger(__name__)


def _emit_phase(
    on_phase: Callable | None,
    phase: str,
    event: str,
    *,
    round: int | None = None,
    **data: Any,
) -> None:
    """Construct a PhaseEvent and call the callback if set."""
    if on_phase is None:
        return
    pe = PhaseEvent(phase=phase, event=event, round=round, data=data)
    on_phase(pe)


def _candidate_summaries(
    candidates: list[dict],
    current_ps_dict: dict,
) -> list[dict]:
    """Build compact per-candidate summary dicts for phase event data."""
    current_ps = PromptState(**current_ps_dict)
    summaries = []
    for i, c in enumerate(candidates):
        # Non-destructive read of pipeline override
        pp_override = c.get("__pipeline_params_override__")

        # Diff to find changed fields
        # Strip the dunder key before constructing PromptState
        c_clean = {k: v for k, v in c.items() if k != "__pipeline_params_override__"}
        try:
            candidate_ps = PromptState(**c_clean)
            delta = prompt_diff(current_ps, candidate_ps)
            changed_fields = [fc.field for fc in delta.field_changes]
        except Exception:
            changed_fields = []

        summary: dict = {
            "idx": i,
            "changes_description": c.get("changes_description", ""),
            "changed_fields": changed_fields,
        }
        if pp_override:
            summary["pipeline_params_override"] = pp_override
        summaries.append(summary)
    return summaries


class CycleConfig(BaseModel):
    """Configuration for feedback cycling."""

    model_config = {"arbitrary_types_allowed": True}

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
    sample_size: int = Field(0, description="Subsample size (0 = use all)")
    seed: int = Field(42, description="Random seed for subsampling")

    pipeline_schema: PipelineSchema | None = Field(None, description="Pipeline schema for eval")

    # Scan-aware optimization
    scan_context: dict | None = Field(None, description="Scan analytics context for candidate gen")

    # L2/L3 escalation
    enable_l2: bool = Field(False, description="Enable L2 refine_context loop")
    enable_l3: bool = Field(False, description="Enable L3 modify_plan loop")
    l2_patience: int = Field(2, description="L2 stalls before escalating to L3")
    l3_patience: int = Field(1, description="L3 stalls before stopping")

    @classmethod
    def from_campaign_config(
        cls,
        campaign_config: dict,
        *,
        backend_url: str = "",
        backend_id: str = "",
        project_root: str = "",
        pipeline_params: dict | None = None,
        session_terms: list[str] | None = None,
        pipeline_schema: PipelineSchema | None = None,
        scan_context: dict | None = None,
    ) -> "CycleConfig":
        """Build from the notebook's ``campaign_config`` dict."""
        opt = campaign_config.get("optimization", {})
        eval_llm = campaign_config.get("eval_llm", {})
        return cls(
            max_rounds=opt.get("max_rounds", 10),
            patience=opt.get("patience", 3),
            n_variants=opt.get("n_variants", 5),
            creativity=opt.get("creativity", 0.7),
            improvement_threshold=opt.get("improvement_threshold", 0.01),
            model=eval_llm.get("model"),
            provider=None,
            backend_url=backend_url,
            backend_id=backend_id,
            project_root=project_root,
            generate_suggestions=False,
            pipeline_params=pipeline_params,
            session_terms=session_terms,
            temperature=eval_llm.get("temperature", 0.0),
            sample_size=campaign_config.get("sample_size", 0),
            seed=42,
            pipeline_schema=pipeline_schema,
            scan_context=scan_context,
            enable_l2=opt.get("enable_l2", False),
            enable_l3=opt.get("enable_l3", False),
            l2_patience=opt.get("l2_patience", 2),
            l3_patience=opt.get("l3_patience", 1),
        )


class CycleRoundResult(BaseModel):
    """Result of a single feedback cycle round."""

    round: int
    label: str
    accuracy: float
    composite: float = 0.0
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


@dataclass
class _LoopState:
    """Mutable state threaded through the feedback cycle round loop."""

    rounds: list[CycleRoundResult] = field(default_factory=list)
    current_ps: dict = field(default_factory=dict)  # PromptState as dict
    current_accuracy: float = 0.0
    current_composite: float = 0.0
    current_results: list[dict] = field(default_factory=list)
    best_accuracy: float = 0.0
    best_composite: float = 0.0
    best_round: int = -1
    best_ps: dict = field(default_factory=dict)  # PromptState as dict
    stall_count: int = 0
    backend_client: "BackendClient | None" = None
    store: "ProjectStore | None" = None

    # L2/L3 state
    l2_stall_count: int = 0
    l3_stall_count: int = 0
    l2_round: int = 0
    l3_round: int = 0
    best_accuracy_at_l2_entry: float = 0.0
    best_accuracy_at_l3_entry: float = 0.0
    best_composite_at_l2_entry: float = 0.0
    best_composite_at_l3_entry: float = 0.0


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
            "sample_size": config.sample_size,
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


def _round_result_from_detail(detail: dict) -> CycleRoundResult:
    """Build a CycleRoundResult from a persisted trial detail dict."""
    acc = detail.get("accuracy", 0.0)
    return CycleRoundResult(
        round=detail["round"],
        label=detail.get("label", ""),
        accuracy=acc,
        composite=detail.get("composite", acc),
        hits=detail.get("hits", 0),
        total=detail.get("total", 0),
        improved=detail.get("improved", False),
        next_action=detail.get("next_action", "generate"),
        prompt_state=detail.get("prompt_state", {}),
        results=detail.get("results", []),
        candidates_evaluated=detail.get("candidates_evaluated", 0),
        candidate_scores=detail.get("candidate_scores", []),
    )


def _restore_completed_result(
    campaign_data: dict,
    store: "CampaignStore",
    backend_id: str,
    cycle_id: str,
) -> CycleResult:
    """Reconstruct a CycleResult from a completed campaign on disk."""
    rounds: list[CycleRoundResult] = []
    for trial_summary in campaign_data.get("trials", []):
        detail = store.load_trial(backend_id, cycle_id, trial_summary["round"])
        if detail is None:
            continue
        rounds.append(_round_result_from_detail(detail))

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


def _restore_round_state(
    campaign_data: dict,
    store: "CampaignStore",
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
    current_composite = 0.0
    current_results: list = []
    best_accuracy = 0.0
    best_composite = 0.0
    best_round = -1
    best_ps: dict = {}
    stall_count = 0
    l2_stall_count = 0
    l3_stall_count = 0
    l2_round = 0
    l3_round = 0

    for trial_summary in campaign_data.get("trials", []):
        detail = store.load_trial(backend_id, cycle_id, trial_summary["round"])
        if detail is None:
            continue
        rr = _round_result_from_detail(detail)
        rounds.append(rr)

        current_ps = rr.prompt_state
        current_accuracy = rr.accuracy
        current_composite = rr.composite
        current_results = rr.results

        if rr.composite > best_composite:
            best_composite = rr.composite
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

        # L2/L3 state
        l2_stall_count = detail.get("l2_stall_count", l2_stall_count)
        l3_stall_count = detail.get("l3_stall_count", l3_stall_count)
        l2_round = detail.get("l2_round", l2_round)
        l3_round = detail.get("l3_round", l3_round)

    return (
        rounds, current_ps, current_accuracy, current_composite, current_results,
        best_accuracy, best_composite, best_round, best_ps, stall_count,
        l2_stall_count, l3_stall_count, l2_round, l3_round,
    )


def _init_obs(
    config: CycleConfig,
    obs_campaign_id: str,
    baseline_accuracy: float,
    eval_data: list[dict],
    langfuse_session_id: str | None,
) -> tuple[Any | None, str | None, dict[str, str] | None]:
    """Create ObsLogger, log campaign start, register dataset.

    Returns:
        (obs, dataset_name, dataset_item_map)
    """
    from api.services.obs.observability_logger import ObsLogger  # lazy: heavy dep

    obs: ObsLogger | None = None
    if config.project_root and config.backend_id:
        try:
            obs = ObsLogger(config.project_root, config.backend_id)
        except Exception:
            logger.warning("Failed to create ObsLogger", exc_info=True)

    dataset_name: str | None = None
    dataset_item_map: dict[str, str] | None = None
    if not obs:
        return obs, dataset_name, dataset_item_map

    try:
        obs.log_campaign_start(
            campaign_id=obs_campaign_id,
            config=config.model_dump(),
            baseline_accuracy=baseline_accuracy,
            session_id=langfuse_session_id,
        )
    except Exception:
        logger.warning("ObsLogger.log_campaign_start failed", exc_info=True)

    try:
        dataset_name = DATASET_NAME
        dataset_item_map = obs.register_dataset(dataset_name, eval_data)
        if dataset_item_map:
            logger.info(
                "Registered %d dataset items for '%s'",
                len(dataset_item_map), dataset_name,
            )
    except Exception:
        logger.warning("Dataset registration failed", exc_info=True)
        dataset_item_map = None

    return obs, dataset_name, dataset_item_map


def _load_completed_campaign(
    campaign_data: dict,
    campaign_store: "CampaignStore",
    backend_id: str,
    cycle_id: str,
) -> CycleResult | None:
    """Return CycleResult if campaign is completed, else None."""
    if campaign_data.get("status") != "completed":
        return None
    if not campaign_data.get("trials"):
        logger.info(
            "Cycle %s marked completed but has no trials — will restart",
            cycle_id,
        )
        return None
    logger.info("Cycle %s already completed — returning cached result", cycle_id)
    return _restore_completed_result(campaign_data, campaign_store, backend_id, cycle_id)


def _restore_in_progress_campaign(
    campaign_data: dict,
    campaign_store: "CampaignStore",
    config: CycleConfig,
    cycle_id: str,
) -> tuple[int, _LoopState] | None:
    """Restore loop state from an in-progress campaign, or None if no trials."""
    if not campaign_data.get("trials"):
        logger.info(
            "Cycle %s exists but has no completed rounds — starting fresh",
            cycle_id,
        )
        return None

    (
        rounds, cur_ps, cur_acc, cur_composite, cur_results,
        best_acc, best_composite, best_rnd, best_ps, stall,
        l2_stall, l3_stall, l2_rnd, l3_rnd,
    ) = _restore_round_state(
        campaign_data, campaign_store, config.backend_id,
        cycle_id, config.improvement_threshold,
    )
    resumed_from = len(rounds)
    logger.info(
        "Resuming cycle %s from round %d (best_acc=%.3f)",
        cycle_id, resumed_from, best_acc,
    )
    restored = _LoopState(
        rounds=rounds,
        current_ps=cur_ps,
        current_accuracy=cur_acc,
        current_composite=cur_composite,
        current_results=cur_results,
        best_accuracy=best_acc,
        best_composite=best_composite,
        best_round=best_rnd,
        best_ps=best_ps,
        stall_count=stall,
        l2_stall_count=l2_stall,
        l3_stall_count=l3_stall,
        l2_round=l2_rnd,
        l3_round=l3_rnd,
    )
    return resumed_from, restored


def _resume_or_create_campaign(
    config: CycleConfig,
    eval_data: list[dict],
    current_ps: dict,
    baseline_prompt_state: dict | None,
    baseline_accuracy: float,
) -> tuple[Any | None, str | None, int, _LoopState | None, CycleResult | None]:
    """Handle campaign resume detection.

    Returns:
        (campaign_store, cycle_id, resumed_from_round, restored_state, completed_result)
    """
    if not (config.project_root and config.backend_id):
        return None, None, 0, None, None

    try:
        from api.services.stores.campaign_store import CampaignStore  # lazy: heavy dep

        store_base = Path(config.project_root)
        campaign_store = CampaignStore(store_base)

        bl_ps = baseline_prompt_state if baseline_prompt_state is not None else current_ps
        cycle_id = cycle_config_identity(config, PromptState(**bl_ps).render(), eval_data)
        logger.info("Cycle identity: %s", cycle_id)

        existing = campaign_store.load(config.backend_id, cycle_id)
        if existing is not None:
            completed = _load_completed_campaign(
                existing, campaign_store, config.backend_id, cycle_id,
            )
            if completed is not None:
                return campaign_store, cycle_id, 0, None, completed

            restored = _restore_in_progress_campaign(
                existing, campaign_store, config, cycle_id,
            )
            if restored is not None:
                resumed_from, state = restored
                return campaign_store, cycle_id, resumed_from, state, None
        else:
            campaign_store.create(config.backend_id, cycle_id, {
                "type": "feedback_cycle",
                "config": config.model_dump(),
                "baseline_accuracy": baseline_accuracy,
            })

        return campaign_store, cycle_id, 0, None, None
    except Exception:
        logger.warning("Cycle resume setup failed — running fresh", exc_info=True)
        return None, None, 0, None, None


async def _generate_or_load_candidates(
    round_num: int,
    state: _LoopState,
    config: CycleConfig,
    campaign_store: "CampaignStore | None",
    cycle_id: str | None,
    on_phase: Callable | None = None,
    n_eval_queries: int = 0,
) -> list[dict]:
    """Load persisted candidates or generate fresh ones via LLM."""
    current_ps = PromptState(**state.current_ps)
    prompt_preview = current_ps.render()[:120]

    _emit_phase(on_phase, "growth", "enter", round=round_num,
                current_accuracy=state.current_accuracy,
                prompt_preview=prompt_preview,
                n_variants=config.n_variants,
                creativity=config.creativity,
                model=config.model or "(default)",
                has_scan_context=config.scan_context is not None)

    if campaign_store and cycle_id:
        persisted = campaign_store.load_round_candidates(
            config.backend_id, cycle_id, round_num,
        )
        if persisted is not None:
            logger.info(
                "Loaded %d persisted candidates for round %d",
                len(persisted), round_num,
            )
            _emit_phase(on_phase, "growth", "exit", round=round_num,
                        n_candidates=len(persisted),
                        n_eval_queries=n_eval_queries,
                        loaded_from_disk=True,
                        candidates=_candidate_summaries(
                            persisted, state.current_ps))
            return persisted

    logger.debug("No persisted candidates for round %d — generating fresh", round_num)
    llm_client = _llm_client.get_llm_client(config.provider)

    # L2 meta-param overrides from PromptState.parameters
    n_variants = current_ps.parameters.get("n_variants", config.n_variants)
    creativity = current_ps.parameters.get("creativity", config.creativity)

    raw_candidates = await _prompt_optimizer.generate_candidates(
        current_ps, state.current_accuracy, state.current_results,
        n_variants, creativity, llm_client,
        model=config.model,
        scan_context=config.scan_context,
    )
    # Normalize to dicts (generate_candidates returns dicts, but mocks may return PromptState)
    candidates = [
        c if isinstance(c, dict) else c.model_dump()
        for c in raw_candidates
    ]

    if campaign_store and cycle_id:
        campaign_store.save_round_candidates(
            config.backend_id, cycle_id, round_num, candidates,
        )

    _emit_phase(on_phase, "growth", "exit", round=round_num,
                n_candidates=len(candidates),
                n_eval_queries=n_eval_queries,
                loaded_from_disk=False,
                candidates=_candidate_summaries(
                    candidates, state.current_ps))

    return candidates


async def _evaluate_candidates(
    candidates: list[dict],
    round_num: int,
    state: _LoopState,
    round_eval_data: list[dict],
    config: CycleConfig,
    obs: "ObsLogger | None",
    dataset_name: str | None,
    dataset_item_map: dict[str, str] | None,
    on_candidate_eval: Callable | None,
    on_query_eval: Callable | None,
    on_phase: Callable | None = None,
) -> dict:
    """Evaluate candidates, select winner, optionally generate suggestions."""
    from api.services.prompt_optimizer import generate_suggestions  # lazy: rarely used

    _emit_phase(on_phase, "analysis_eval", "enter", round=round_num,
                n_candidates=len(candidates),
                n_queries=len(round_eval_data),
                current_best_accuracy=state.current_accuracy,
                improvement_threshold=config.improvement_threshold)

    baseline_label = f"round_{round_num}" if round_num > 0 else "baseline"
    current_best = {
        "accuracy": state.current_accuracy,
        "prompt_state": state.current_ps,
        "results": state.current_results,
        "label": baseline_label,
    }

    base_sp = SearchPoint(
        prompt_state=PromptState(**state.current_ps),
        model=config.model or "",
        temperature=config.temperature,
        pipeline_params=config.pipeline_params,
    )
    ctx = EvalContext(
        search_point=base_sp,
        backend_client=state.backend_client,
        store=state.store,
        backend_id=config.backend_id,
        pipeline_schema=config.pipeline_schema,
        obs=obs,
        dataset_name=dataset_name if dataset_name and dataset_item_map else None,
        dataset_item_map=dataset_item_map if dataset_name and dataset_item_map else None,
        source="feedback_cycle",
    )

    eval_out = await _prompt_optimizer.evaluate_and_select_winner(
        candidates, round_eval_data, current_best,
        improvement_threshold=config.improvement_threshold,
        on_candidate_eval=on_candidate_eval,
        on_query_eval=on_query_eval,
        ctx=ctx,
    )

    if config.generate_suggestions:
        try:
            llm_client = _llm_client.get_llm_client(config.provider)
            rounds_for_suggestions = [
                {"round": r.round, "label": r.label, "accuracy": r.accuracy,
                 "prompt_state": PromptState(**r.prompt_state),
                 "results": r.results}
                for r in state.rounds
            ]
            rounds_for_suggestions.append({
                "round": round_num, "label": eval_out["winner"]["label"],
                "accuracy": eval_out["winner_accuracy"],
                "prompt_state": PromptState(**eval_out["winner_prompt_state"]),
                "results": eval_out["winner_results"],
            })
            suggestions = await generate_suggestions(
                rounds_for_suggestions, round_eval_data, {},
                llm_client, model=config.model,
            )
            next_action = suggestions.get("next_action", "generate")
            if next_action in ("generate", "stop", "refine_context", "modify_plan"):
                eval_out["next_action"] = next_action
            eval_out["suggestions"] = suggestions
        except Exception:
            logger.warning("Suggestion generation failed", exc_info=True)

    _emit_phase(on_phase, "analysis_eval", "exit", round=round_num,
                winner_label=eval_out["winner"].get("label", ""),
                winner_accuracy=eval_out["winner_accuracy"],
                winner_composite=eval_out.get("winner_composite",
                                              eval_out["winner_accuracy"]),
                improved=eval_out["improved"],
                next_action=eval_out["next_action"],
                candidate_scores=eval_out["candidate_scores"])

    return eval_out


def _log_round_obs(
    eval_out: dict,
    round_num: int,
    config: CycleConfig,
    obs: "ObsLogger",
    obs_campaign_id: str,
) -> None:
    """Log round-end metrics and prompt version to observability."""
    try:
        obs.log_round_end(
            campaign_id=obs_campaign_id,
            round_num=round_num,
            accuracy=eval_out["winner_accuracy"],
            hits=eval_out["winner"].get("hits", 0),
            total=eval_out["winner"].get("total", 0),
            improved=eval_out["improved"],
            next_action=eval_out["next_action"],
            winner_prompt_state_id=eval_out["winner_prompt_state"].get("id", ""),
            candidate_scores=eval_out["candidate_scores"],
            model=config.model or "",
            temperature=config.temperature,
            n_variants=config.n_variants,
        )
    except Exception:
        logger.warning("ObsLogger.log_round_end failed", exc_info=True)

    try:
        winner_ps = PromptState(**eval_out["winner_prompt_state"])
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
        logger.warning("ObsLogger.log_prompt_version failed", exc_info=True)


async def _execute_round(
    round_num: int,
    state: _LoopState,
    round_eval_data: list[dict],
    config: CycleConfig,
    obs: "ObsLogger | None",
    obs_campaign_id: str,
    dataset_name: str | None,
    dataset_item_map: dict[str, str] | None,
    campaign_store: "CampaignStore | None",
    cycle_id: str | None,
    on_candidate_eval: Callable | None,
    on_query_eval: Callable | None,
    on_phase: Callable | None = None,
) -> CycleRoundResult:
    """Execute one optimization round: generate → evaluate → select winner → obs log."""
    if obs:
        try:
            obs.log_round_start(obs_campaign_id, round_num)
        except Exception:
            logger.warning("ObsLogger.log_round_start failed", exc_info=True)

    candidates = await _generate_or_load_candidates(
        round_num, state, config, campaign_store, cycle_id, on_phase,
        n_eval_queries=len(round_eval_data),
    )

    eval_out = await _evaluate_candidates(
        candidates, round_num, state, round_eval_data, config,
        obs, dataset_name, dataset_item_map,
        on_candidate_eval, on_query_eval, on_phase,
    )

    round_result = CycleRoundResult(
        round=round_num,
        label=eval_out["winner"].get("label", f"round_{round_num}"),
        accuracy=eval_out["winner_accuracy"],
        composite=eval_out.get("winner_composite", eval_out["winner_accuracy"]),
        hits=eval_out["winner"].get("hits", 0),
        total=eval_out["winner"].get("total", 0),
        improved=eval_out["improved"],
        next_action=eval_out["next_action"],
        prompt_state=eval_out["winner_prompt_state"],
        results=eval_out["winner_results"],
        candidates_evaluated=eval_out["winner"].get("candidates_evaluated", 0),
        candidate_scores=eval_out["candidate_scores"],
    )

    if obs:
        _log_round_obs(eval_out, round_num, config, obs, obs_campaign_id)

    return round_result


def _finalize_campaign(
    campaign_store: "CampaignStore | None",
    cycle_id: str | None,
    config: CycleConfig,
    state: _LoopState,
    stop_reason: str,
    finished_at: str,
    obs: "ObsLogger | None",
    obs_campaign_id: str,
    *,
    status: str = "completed",
) -> str | None:
    """Mark campaign on disk and finalize observability.

    Returns:
        Cloud Langfuse trace ID (or None).
    """
    if campaign_store and cycle_id:
        try:
            campaign_store.update(config.backend_id, cycle_id, {
                "status": status,
                "stop_reason": stop_reason,
                "best_accuracy": state.best_accuracy,
                "best_round": state.best_round,
                "n_rounds": len(state.rounds),
                "finished_at": finished_at,
            })
        except Exception:
            logger.warning("Campaign completion update failed", exc_info=True)

    cloud_trace_id: str | None = None
    if obs:
        try:
            obs.log_campaign_end(
                campaign_id=obs_campaign_id,
                best_accuracy=state.best_accuracy,
                n_rounds=len(state.rounds),
                stop_reason=stop_reason,
                best_round=state.best_round,
            )
            obs.flush()
            cloud_trace_id = obs.get_cloud_trace_id(obs_campaign_id)
        except Exception:
            logger.warning("ObsLogger campaign end failed", exc_info=True)

    return cloud_trace_id


# ---------------------------------------------------------------------------
# Layer escalation helpers
# ---------------------------------------------------------------------------


async def _escalate_l2(
    state: _LoopState,
    config: CycleConfig,
    round_num: int,
    eval_data: list[dict],
    on_phase: Callable | None = None,
) -> str | None:
    """Handle L1→L2 escalation and optionally L2→L3.

    Returns a stop_reason string if the cycle should stop, or None to continue.
    """
    llm_client = _llm_client.get_llm_client(config.provider)
    current_ps = PromptState(**state.current_ps)

    # Check if L2 itself has stalled (no improvement since last L2 entry)
    l2_improved = state.best_composite > state.best_composite_at_l2_entry
    if not l2_improved and state.l2_round > 0:
        state.l2_stall_count += 1
    else:
        state.l2_stall_count = 0

    # L2 stalled → check L3
    if state.l2_stall_count >= config.l2_patience:
        if config.enable_l3:
            # L2→L3 escalation
            l3_improved = state.best_composite > state.best_composite_at_l3_entry
            if not l3_improved and state.l3_round > 0:
                state.l3_stall_count += 1
            else:
                state.l3_stall_count = 0

            if state.l3_stall_count >= config.l3_patience:
                logger.info(
                    "L3 patience exhausted (%d stalls) at round %d",
                    state.l3_stall_count, round_num,
                )
                return "l3_patience_exhausted"

            # Perform L3 transition
            l2_history = [{
                "l2_round": state.l2_round,
                "parameters": current_ps.parameters,
                "accuracy_change": state.best_composite - state.best_composite_at_l3_entry,
            }]
            _emit_phase(on_phase, "modify_plan", "enter", round=round_num,
                        l3_round=state.l3_round,
                        l2_stall_count=state.l2_stall_count,
                        current_plan_preview=str(current_ps.plan)[:120])
            new_ps = await layer_transitions.modify_plan(
                current_ps, l2_history, eval_data, llm_client, model=config.model,
            )
            state.current_ps = new_ps.model_dump()
            state.l3_round += 1
            state.best_accuracy_at_l3_entry = state.best_accuracy
            state.best_composite_at_l3_entry = state.best_composite
            state.l2_stall_count = 0
            state.l2_round = 0
            state.stall_count = 0
            state.best_accuracy_at_l2_entry = state.best_accuracy
            state.best_composite_at_l2_entry = state.best_composite
            _emit_phase(on_phase, "modify_plan", "exit", round=round_num,
                        l3_round=state.l3_round,
                        new_plan_preview=str(new_ps.plan)[:120],
                        changes_description=new_ps.changes_description or "")
            logger.info(
                "L3 modify_plan at round %d (l3_round=%d)",
                round_num, state.l3_round,
            )
            return None  # continue
        else:
            logger.info(
                "L2 patience exhausted (%d stalls) at round %d",
                state.l2_stall_count, round_num,
            )
            return "l2_patience_exhausted"

    # Perform L2 transition
    stalled_rounds = [
        {
            "round": r.round,
            "accuracy": r.accuracy,
            "results": r.results,
        }
        for r in state.rounds[-config.patience:]
    ]
    _emit_phase(on_phase, "refine_context", "enter", round=round_num,
                l2_round=state.l2_round,
                stall_count=state.stall_count,
                current_params=current_ps.parameters,
                current_accuracy=state.current_accuracy,
                best_accuracy=state.best_accuracy)
    new_ps = await layer_transitions.refine_context(
        current_ps, stalled_rounds, eval_data, llm_client, model=config.model,
    )
    state.current_ps = new_ps.model_dump()
    state.l2_round += 1
    state.best_accuracy_at_l2_entry = state.best_accuracy
    state.best_composite_at_l2_entry = state.best_composite
    state.stall_count = 0  # reset L1 patience
    _emit_phase(on_phase, "refine_context", "exit", round=round_num,
                l2_round=state.l2_round,
                param_changes_count=len(new_ps.parameters),
                context_changed=new_ps.context != current_ps.context,
                changes_description=new_ps.changes_description or "")
    logger.info(
        "L2 refine_context at round %d (l2_round=%d)",
        round_num, state.l2_round,
    )
    return None  # continue


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


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
    on_phase: Callable | None = None,
    langfuse_session_id: str | None = None,
    scan_context: dict | None = None,
) -> CycleResult:
    """Run iterative optimization with feedback cycling.

    Executes:
    1. InitNode — create baseline PromptState (skipped if baseline_prompt_state provided)
    2. Loop: generate_candidates → evaluate_and_select_winner
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
        scan_context: Scan analytics context for scan-aware candidate generation.
            When provided, overrides config.scan_context for this run.

    Returns:
        CycleResult with all rounds and final winner.
    """
    if scan_context is not None:
        config = config.model_copy(update={"scan_context": scan_context})
    started_at = datetime.now(timezone.utc).isoformat()

    _emit_phase(on_phase, "init", "enter",
                max_rounds=config.max_rounds,
                patience=config.patience,
                n_variants=config.n_variants,
                model=config.model or "(default)",
                sample_size=config.sample_size,
                enable_l2=config.enable_l2,
                enable_l3=config.enable_l3,
                eval_data_count=len(eval_data),
                baseline_accuracy=baseline_accuracy,
                has_scan_context=config.scan_context is not None)

    # Initialize backend session (required before /matches calls)
    if config.session_terms:
        bc = BackendClient(config.backend_url)
        await bc.init_session(config.session_terms)

    round_eval_data = subsample_queries(eval_data, config.sample_size, config.seed)

    # -- Step 1: Initialize baseline --
    if baseline_prompt_state is not None:
        current_ps = baseline_prompt_state
        current_accuracy = baseline_accuracy
        current_results: list = baseline_results or []
        logger.info("Using provided baseline (acc=%.3f)", current_accuracy)
    else:
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

    # -- Step 2: Resume detection --
    (
        campaign_store, cycle_id, resumed_from_round,
        restored_state, completed_result,
    ) = _resume_or_create_campaign(
        config, eval_data, current_ps, baseline_prompt_state, baseline_accuracy,
    )
    if completed_result is not None:
        return completed_result

    # -- Step 3: Observability setup --
    obs_campaign_id = cycle_id or f"campaign_{started_at[:19].replace(':', '')}"
    obs, dataset_name, dataset_item_map = _init_obs(
        config, obs_campaign_id, baseline_accuracy, eval_data, langfuse_session_id,
    )

    # -- Step 4: Initialize loop state (always fresh from baseline) --
    # Even on resume, start fresh — cached rounds replay from disk/cache.
    if current_results:
        _bl_scores = compute_composite_score(current_results, config.pipeline_schema)
        _bl_composite = _bl_scores["composite"]
    else:
        _bl_composite = current_accuracy
    state = _LoopState(
        current_ps=current_ps,
        current_accuracy=current_accuracy,
        current_composite=_bl_composite,
        current_results=current_results,
        best_accuracy=current_accuracy,
        best_composite=_bl_composite,
        best_ps=current_ps,
    )

    _emit_phase(on_phase, "init", "exit",
                cycle_id=cycle_id,
                resumed_from_round=resumed_from_round,
                baseline_accuracy=current_accuracy,
                obs_enabled=obs is not None,
                sample_count=len(round_eval_data))

    # Build shared clients once (reused across all rounds)
    state.backend_client = BackendClient(config.backend_url)
    if config.project_root:
        from api.services.project_store import ProjectStore
        state.store = ProjectStore(config.project_root)

    stop_reason = "max_rounds"

    # -- Step 5: Round loop --
    try:
        for round_num in range(config.max_rounds):
            logger.info(
                "Feedback cycle round %d (acc=%.3f, stall=%d/%d)",
                round_num, state.current_accuracy, state.stall_count, config.patience,
            )

            round_result = await _execute_round(
                round_num, state, round_eval_data, config,
                obs, obs_campaign_id, dataset_name, dataset_item_map,
                campaign_store, cycle_id,
                on_candidate_eval, on_query_eval, on_phase,
            )

            # Update loop state
            state.rounds.append(round_result)
            state.current_ps = round_result.prompt_state
            state.current_accuracy = round_result.accuracy
            state.current_composite = round_result.composite
            state.current_results = round_result.results

            if state.current_composite > state.best_composite:
                state.best_composite = state.current_composite
                state.best_accuracy = state.current_accuracy
                state.best_round = round_num
                state.best_ps = state.current_ps

            state.stall_count = 0 if round_result.improved else state.stall_count + 1

            if on_round_complete:
                on_round_complete(round_result, state.stall_count)

            # Checkpoint round to disk
            if campaign_store and cycle_id:
                try:
                    campaign_store.add_trial(config.backend_id, cycle_id, {
                        "trial_id": f"round_{round_num}",
                        "round": round_num,
                        "label": round_result.label,
                        "accuracy": round_result.accuracy,
                        "composite": round_result.composite,
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
                        "stall_count": state.stall_count,
                        "l2_stall_count": state.l2_stall_count,
                        "l3_stall_count": state.l3_stall_count,
                        "l2_round": state.l2_round,
                        "l3_round": state.l3_round,
                    })
                except Exception:
                    logger.warning("Round checkpoint failed", exc_info=True)

            # Check stopping conditions
            if state.current_accuracy >= 1.0:
                stop_reason = "perfect_score"
                logger.info("Perfect score reached at round %d", round_num)
                break
            if round_result.next_action == "stop":
                stop_reason = "next_action_stop"
                logger.info("Analysis node signaled stop at round %d", round_num)
                break

            # Hierarchical patience escalation
            if state.stall_count >= config.patience:
                if config.enable_l2:
                    # L1 stalled → escalate to L2
                    stop_reason = await _escalate_l2(
                        state, config, round_num, round_eval_data, on_phase,
                    )
                    if stop_reason:
                        break
                else:
                    stop_reason = "patience_exhausted"
                    logger.info(
                        "Patience exhausted after %d stalls at round %d",
                        state.stall_count, round_num,
                    )
                    break
    except (KeyboardInterrupt, asyncio.CancelledError):
        stop_reason = "interrupted"
        logger.warning(
            "Feedback cycle interrupted at round %d. "
            "Completed rounds are checkpointed.",
            len(state.rounds),
        )

    # -- Step 6: Finalize --
    finished_at = datetime.now(timezone.utc).isoformat()
    campaign_status = "interrupted" if stop_reason == "interrupted" else "completed"
    cloud_trace_id = _finalize_campaign(
        campaign_store, cycle_id, config, state, stop_reason, finished_at,
        obs, obs_campaign_id, status=campaign_status,
    )

    return CycleResult(
        rounds=state.rounds,
        n_rounds=len(state.rounds),
        best_accuracy=state.best_accuracy,
        best_round=state.best_round,
        baseline_accuracy=(
            state.rounds[0].accuracy if state.rounds else state.current_accuracy
        ),
        winner_prompt_state=state.best_ps,
        stop_reason=stop_reason,
        started_at=started_at,
        finished_at=finished_at,
        langfuse_trace_id=cloud_trace_id,
        cycle_id=cycle_id,
        resumed_from_round=resumed_from_round,
    )
