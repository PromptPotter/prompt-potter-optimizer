"""Optimizer nodes for prompt optimization workflows.

Three nodes wrapping existing service logic:
- **InitNode** — restructure context into Layer 1 fields, produce initial PromptState
- **GrowFilterNode** — generate N candidate PromptState variants via LLM
- **AnalysisEvalNode** — evaluate candidates via backend, select winner, suggest next steps
"""

import logging
from typing import Type

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

    Wraps ``evaluate_prompt_cached()``, ``_select_round_winner()``, and
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
        from api.services.backend_client import BackendClient
        from api.services.prompt_optimizer import (
            evaluate_and_select_winner,
            generate_suggestions,
        )

        backend_url = self.config.get("backend_url", "")
        if not backend_url:
            raise ValueError("AnalysisEvalNode requires 'backend_url' in config")

        # Build dependencies
        backend_client = BackendClient(backend_url)
        store = None
        project_root = self.config.get("project_root", "")
        if project_root:
            from api.services.project_store import ProjectStore
            store = ProjectStore(project_root)

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

        result = await evaluate_and_select_winner(
            input_data.candidates,
            input_data.eval_data,
            current_best,
            backend_client=backend_client,
            backend_id=self.config.get("backend_id", ""),
            store=store,
            improvement_threshold=self.config.get("improvement_threshold", 0.01),
            pipeline_params=self.config.get("pipeline_params"),
            model=self.config.get("model"),
            temperature=self.config.get("temperature", 0.0),
            on_candidate_eval=self.config.get("on_candidate_eval"),
            on_query_eval=self.config.get("on_query_eval"),
            obs=self.config.get("obs"),
            dataset_name=self.config.get("dataset_name"),
            dataset_item_map=self.config.get("dataset_item_map"),
        )

        # Generate suggestions separately if requested
        if (
            self.config.get("generate_suggestions", True)
            and input_data.campaign_rounds
        ):
            try:
                from api.services.llm_client import get_llm_client
                llm_client = get_llm_client(self.config.get("provider"))
                suggestions = await generate_suggestions(
                    input_data.campaign_rounds,
                    input_data.eval_data,
                    input_data.campaign_config,
                    llm_client,
                    model=self.config.get("model"),
                )
                result["suggestions"] = suggestions
                next_action = suggestions.get("next_action", "generate")
                if next_action in (
                    "generate", "refine_context", "modify_plan", "stop",
                ):
                    result["next_action"] = next_action
            except Exception:
                logger.warning("Suggestion generation failed", exc_info=True)

        return AnalysisEvalOutput(**result)
