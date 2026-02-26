"""Optimizer nodes for prompt optimization workflows.

Three nodes wrapping existing service logic:
- **InitNode** — restructure context into Layer 1 fields, produce initial PromptState
- **GrowFilterNode** — generate N candidate PromptState variants via LLM
- **AnalysisEvalNode** — evaluate candidates via backend, select winner, suggest next steps
"""

import logging
from typing import Any, Type

from pydantic import BaseModel, Field

from .base import NodeBase

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# InitNode — context decomposition into PromptState
# ---------------------------------------------------------------------------


class InitNodeInput(BaseModel):
    """Input for InitNode."""

    instruction: str = Field(..., description="Raw prompt instruction text")
    improvement_areas: str = Field(
        "", description="Domain-expert observations about where improvement is likely",
    )


class InitNodeOutput(BaseModel):
    """Output from InitNode."""

    prompt_state: dict = Field(..., description="Serialized PromptState (baseline)")
    prompt_state_id: str = Field(..., description="PromptState ID")
    layer1_fields: dict = Field(..., description="Decomposed Layer 1 fields")
    rendered_prompt: str = Field(..., description="Full rendered prompt")


class InitNode(NodeBase[InitNodeInput, InitNodeOutput]):
    """Decompose raw context into Layer 1 fields and create a baseline PromptState.

    Wraps ``restructure_context()`` from ``api.services.search.context``.

    Config:
        model: LLM model identifier (default: from settings)
        provider: LLM provider — "groq", "openai", "mock" (default: auto-detect)
    """

    @classmethod
    def get_input_model(cls) -> Type[InitNodeInput]:
        return InitNodeInput

    @classmethod
    def get_output_model(cls) -> Type[InitNodeOutput]:
        return InitNodeOutput

    async def _execute(self, input_data: InitNodeInput) -> InitNodeOutput:
        from api.models.prompt_state import PromptState
        from api.services.llm_client import get_llm_client
        from api.services.search.context import restructure_context

        client = get_llm_client(self.config.get("provider"))
        model = self.config.get("model")

        fields = await restructure_context(
            input_data.instruction, client,
            model=model,
            improvement_areas=input_data.improvement_areas,
        )

        # Fields from restructure may include 'instruction' — use it if present,
        # otherwise fall back to the raw input instruction.
        ps_kwargs = {k: v for k, v in fields.items() if v and k != "consultation"}
        ps_kwargs.setdefault("instruction", input_data.instruction)
        ps = PromptState(
            **ps_kwargs,
            changes_description="init_node_baseline",
        )

        return InitNodeOutput(
            prompt_state=ps.model_dump(),
            prompt_state_id=ps.id,
            layer1_fields=fields,
            rendered_prompt=ps.render(),
        )


# ---------------------------------------------------------------------------
# GrowFilterNode — candidate generation
# ---------------------------------------------------------------------------


class GrowFilterInput(BaseModel):
    """Input for GrowFilterNode."""

    prompt_state: dict = Field(..., description="Current best PromptState (serialized)")
    accuracy: float = Field(..., description="Current accuracy (0.0-1.0)")
    results: list = Field(
        default_factory=list,
        description="Previous eval results for failure analysis",
    )


class GrowFilterOutput(BaseModel):
    """Output from GrowFilterNode."""

    candidates: list[dict] = Field(
        ..., description="List of candidate PromptStates (serialized)",
    )
    n_generated: int = Field(..., description="Number of candidates generated")


class GrowFilterNode(NodeBase[GrowFilterInput, GrowFilterOutput]):
    """Generate N candidate PromptState variants via LLM meta-prompt.

    Wraps ``generate_candidates()`` from ``api.services.prompt_optimizer``.

    Config:
        model: LLM model identifier
        provider: LLM provider
        n_variants: Number of candidates to generate (default: 5)
        creativity: Temperature for meta-prompt LLM call (default: 0.7)
    """

    @classmethod
    def get_input_model(cls) -> Type[GrowFilterInput]:
        return GrowFilterInput

    @classmethod
    def get_output_model(cls) -> Type[GrowFilterOutput]:
        return GrowFilterOutput

    async def _execute(self, input_data: GrowFilterInput) -> GrowFilterOutput:
        from api.models.prompt_state import PromptState
        from api.services.llm_client import get_llm_client
        from api.services.prompt_optimizer import generate_candidates

        client = get_llm_client(self.config.get("provider"))
        model = self.config.get("model")
        n_variants = self.config.get("n_variants", 5)
        creativity = self.config.get("creativity", 0.7)

        ps = PromptState(**input_data.prompt_state)

        candidates = await generate_candidates(
            ps, input_data.accuracy, input_data.results,
            n_variants, creativity, client,
            model=model,
        )

        return GrowFilterOutput(
            candidates=[c.model_dump() for c in candidates],
            n_generated=len(candidates),
        )


# ---------------------------------------------------------------------------
# AnalysisEvalNode — evaluate, select winner, suggest improvements
# ---------------------------------------------------------------------------


class AnalysisEvalInput(BaseModel):
    """Input for AnalysisEvalNode.

    ``current_best`` can be supplied as a pre-built dict **or** assembled
    from flat fields (``baseline_prompt_state``, ``baseline_accuracy``,
    ``baseline_results``, ``baseline_label``).  Flat fields are used when
    the node is wired inside a CWL workflow where each input resolves to
    a single source reference.
    """

    model_config = {"arbitrary_types_allowed": True}

    candidates: list[dict] = Field(
        ..., description="Candidate PromptStates (serialized)",
    )
    eval_data: list = Field(..., description="Evaluation query dicts")

    # Option A: pre-built current_best dict
    current_best: dict = Field(
        default_factory=dict,
        description=(
            "Current best round entry with keys: accuracy, prompt_state, "
            "results, label.  Leave empty when using flat baseline_* fields."
        ),
    )

    # Option B: flat baseline fields (for CWL workflow wiring)
    baseline_prompt_state: dict | None = Field(
        None, description="Baseline PromptState (serialized) — flat alternative",
    )
    baseline_accuracy: float = Field(
        0.0, description="Baseline accuracy — flat alternative",
    )
    baseline_results: list = Field(
        default_factory=list, description="Baseline eval results — flat alternative",
    )
    baseline_label: str = Field(
        "baseline", description="Baseline label — flat alternative",
    )

    campaign_rounds: list[dict] = Field(
        default_factory=list,
        description="Full campaign rounds history (for suggestion generation)",
    )
    campaign_config: dict = Field(
        default_factory=dict,
        description="Campaign config (for suggestion generation)",
    )


class AnalysisEvalOutput(BaseModel):
    """Output from AnalysisEvalNode."""

    winner: dict = Field(..., description="Winner round entry dict")
    winner_prompt_state: dict = Field(..., description="Winner PromptState (serialized)")
    winner_accuracy: float = Field(..., description="Winner accuracy")
    improved: bool = Field(..., description="Whether improvement exceeded threshold")
    next_action: str = Field(
        "generate",
        description=(
            "Routing hint for feedback cycling: "
            "'generate' (Layer 1), 'refine_context' (Layer 2), "
            "'modify_plan' (Layer 3), or 'stop'"
        ),
    )
    suggestions: dict = Field(
        default_factory=dict,
        description="LLM suggestions for next round",
    )
    candidate_scores: list[dict] = Field(
        default_factory=list,
        description="Per-candidate accuracy summary",
    )
    winner_results: list[dict] = Field(
        default_factory=list,
        description="Per-query eval results for the winner (for failure analysis)",
    )


class AnalysisEvalNode(NodeBase[AnalysisEvalInput, AnalysisEvalOutput]):
    """Evaluate candidates via backend, select winner, generate suggestions.

    Wraps ``evaluate_prompt_cached()``, ``select_round_winner()``, and
    ``generate_suggestions()`` from ``api.services``.  Evaluation uses the
    evaluator framework (``api.evaluators.exact_match.ExactMatchEvaluator``)
    via the ``prompt_eval`` service.

    Config:
        model: LLM model identifier
        provider: LLM provider
        backend_url: Backend URL for evaluation (required)
        backend_id: Backend identifier for caching (default: "")
        project_root: Project root for ProjectStore (default: "")
        improvement_threshold: Min accuracy improvement to accept (default: 0.01)
        pipeline_params: Pipeline parameter overrides (default: {})
        temperature: Temperature for content hash (default: 0.0)
        generate_suggestions: Whether to call LLM for suggestions (default: true)
    """

    @classmethod
    def get_input_model(cls) -> Type[AnalysisEvalInput]:
        return AnalysisEvalInput

    @classmethod
    def get_output_model(cls) -> Type[AnalysisEvalOutput]:
        return AnalysisEvalOutput

    async def _execute(self, input_data: AnalysisEvalInput) -> AnalysisEvalOutput:
        from api.models.prompt_state import PromptState
        from api.services.backend_client import BackendClient
        from api.services.llm_client import get_llm_client
        from api.services.prompt_eval import evaluate_prompt_cached
        from api.services.prompt_optimizer import (
            generate_suggestions,
            select_round_winner,
        )

        model = self.config.get("model")
        provider = self.config.get("provider")
        backend_url = self.config.get("backend_url", "")
        backend_id = self.config.get("backend_id", "")
        project_root = self.config.get("project_root", "")
        threshold = self.config.get("improvement_threshold", 0.01)
        pipeline_params = self.config.get("pipeline_params")
        temperature = self.config.get("temperature", 0.0)
        dataset_name = self.config.get("dataset_name")
        dataset_item_map = self.config.get("dataset_item_map")
        obs = self.config.get("obs")

        # Create backend client
        if not backend_url:
            raise ValueError("AnalysisEvalNode requires 'backend_url' in config")
        backend_client = BackendClient(backend_url)

        # Create store if project_root provided
        store = None
        if project_root:
            from api.services.project_store import ProjectStore
            store = ProjectStore(project_root)

        # Reconstruct PromptStates and evaluate each candidate
        candidates = [PromptState(**c) for c in input_data.candidates]
        all_candidate_results: dict[str, list[dict[str, Any]]] = {}
        candidate_scores: list[dict] = []
        on_candidate_eval = self.config.get("on_candidate_eval")
        on_query_eval = self.config.get("on_query_eval")

        for idx, c in enumerate(candidates):
            # Build per-query callback scoped to this candidate
            _on_result = None
            if on_query_eval:
                def _on_result(result, qi, qt, _ci=idx, _ct=len(candidates)):
                    on_query_eval(_ci, _ct, qi, qt, result)

            results, scores, cached = await evaluate_prompt_cached(
                c, input_data.eval_data, backend_client,
                pipeline_params=pipeline_params,
                store=store, backend_id=backend_id,
                label=f"candidate_{idx}",
                model=model or "", temperature=temperature,
                on_result=_on_result,
                dataset_name=dataset_name,
                dataset_item_map=dataset_item_map,
                obs=obs,
            )
            all_candidate_results[c.id] = results
            candidate_scores.append({
                "candidate_id": c.id,
                "accuracy": scores["accuracy"],
                "hits": scores["hits"],
                "total": scores["total"],
                "cached": cached,
            })
            if on_candidate_eval:
                on_candidate_eval(idx, len(candidates), scores)

        # Assemble current_best — prefer pre-built dict, fall back to flat fields
        if input_data.current_best:
            current_best = dict(input_data.current_best)
        elif input_data.baseline_prompt_state is not None:
            current_best = {
                "accuracy": input_data.baseline_accuracy,
                "prompt_state": input_data.baseline_prompt_state,
                "results": input_data.baseline_results,
                "label": input_data.baseline_label,
            }
        else:
            raise ValueError(
                "AnalysisEvalNode requires either 'current_best' dict or "
                "'baseline_prompt_state' flat field"
            )

        # Reconstruct PromptState object for select_round_winner
        if isinstance(current_best.get("prompt_state"), dict):
            current_best["prompt_state"] = PromptState(
                **current_best["prompt_state"],
            )

        # Select winner using evaluator-backed scores
        winner_entry = select_round_winner(
            candidates, all_candidate_results,
            current_best, threshold,
        )

        # Generate suggestions if requested and rounds are available
        suggestions: dict = {}
        if (self.config.get("generate_suggestions", True)
                and input_data.campaign_rounds):
            llm_client = get_llm_client(provider)
            suggestions = await generate_suggestions(
                input_data.campaign_rounds,
                input_data.eval_data,
                input_data.campaign_config,
                llm_client, model=model,
            )

        # Determine next_action routing hint
        next_action = suggestions.get("next_action", "generate")
        if next_action not in ("generate", "refine_context", "modify_plan", "stop"):
            next_action = "generate"

        winner_ps = winner_entry["prompt_state"]
        return AnalysisEvalOutput(
            winner={
                "label": winner_entry["label"],
                "accuracy": winner_entry["accuracy"],
                "hits": winner_entry["hits"],
                "total": winner_entry["total"],
                "improved": winner_entry["improved"],
                "candidates_evaluated": winner_entry["candidates_evaluated"],
            },
            winner_prompt_state=winner_ps.model_dump(),
            winner_accuracy=winner_entry["accuracy"],
            improved=winner_entry["improved"],
            next_action=next_action,
            suggestions=suggestions,
            candidate_scores=candidate_scores,
            winner_results=winner_entry.get("results", []),
        )
