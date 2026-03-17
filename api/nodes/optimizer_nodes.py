"""Optimizer pipeline nodes (M7).

Node classes wrapping existing service logic with typed I/O:
- **InitNode** — restructure context into Layer 1 fields, produce initial PromptState
- **L1GenerateNode** — generate N candidate PromptState variants via LLM
- **L1EvaluateNode** — evaluate candidates via backend, select winner, critique results
- **L2RefineNode** — L2 refine_context transition (parameter/context adjustment)
- **L3ModifyPlanNode** — L3 modify_plan transition (strategic plan change)
"""

__all__ = [
    "InitNode",
    "L1GenerateNode",
    "L1EvaluateNode",
    "L2RefineNode",
    "L3ModifyPlanNode",
]

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
# L1GenerateNode — candidate generation
# ---------------------------------------------------------------------------


class L1GenerateInput(BaseModel):
    """Input for L1GenerateNode."""

    prompt_state: dict = Field(..., description="Current best PromptState (serialized)")
    accuracy: float = Field(..., description="Current accuracy (0.0-1.0)")
    results: list = Field(
        default_factory=list,
        description="Previous eval results for failure analysis",
    )
    critique_text: str = Field(
        "", description="Critique from previous round's evaluation",
    )
    thinking_styles: list[str] = Field(
        default_factory=list,
        description="Sampled mutation guidance styles",
    )
    scan_context: dict | None = Field(
        None, description="Pipeline-aware generation context from scan analytics",
    )


class L1GenerateOutput(BaseModel):
    """Output from L1GenerateNode."""

    candidates: list[dict] = Field(
        ..., description="List of candidate PromptStates (serialized)",
    )
    n_generated: int = Field(..., description="Number of candidates generated")


class L1GenerateNode(NodeBase[L1GenerateInput, L1GenerateOutput]):
    """Generate N candidate PromptState variants via LLM meta-prompt.

    Wraps ``generate_candidates()`` from ``api.services.prompt_optimizer``.

    Config:
        model: LLM model identifier
        provider: LLM provider
        n_variants: Number of candidates to generate (default: 5)
        creativity: Temperature for meta-prompt LLM call (default: 0.7)
    """

    @classmethod
    def get_input_model(cls) -> Type[L1GenerateInput]:
        return L1GenerateInput

    @classmethod
    def get_output_model(cls) -> Type[L1GenerateOutput]:
        return L1GenerateOutput

    async def _execute(self, input_data: L1GenerateInput) -> L1GenerateOutput:
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
            scan_context=input_data.scan_context,
            critique_text=input_data.critique_text,
            thinking_styles=input_data.thinking_styles,
        )

        return L1GenerateOutput(
            candidates=[
                c if isinstance(c, dict) else c.model_dump()
                for c in candidates
            ],
            n_generated=len(candidates),
        )


# ---------------------------------------------------------------------------
# L1EvaluateNode — evaluate, select winner, critique
# ---------------------------------------------------------------------------


class L1EvaluateInput(BaseModel):
    """Input for L1EvaluateNode."""

    candidates: list[dict] = Field(
        ..., description="Candidate PromptStates (serialized)",
    )
    eval_data: list = Field(..., description="Evaluation query dicts")
    current_best: dict = Field(
        ...,
        description=(
            "Current best round entry with keys: accuracy, prompt_state, "
            "results, label."
        ),
    )


class L1EvaluateOutput(BaseModel):
    """Output from L1EvaluateNode."""

    winner: dict = Field(..., description="Winner round entry dict")
    winner_prompt_state: dict = Field(..., description="Winner PromptState (serialized)")
    winner_accuracy: float = Field(..., description="Winner accuracy")
    improved: bool = Field(..., description="Whether improvement exceeded threshold")
    next_action: str = Field(
        "generate",
        description="Routing hint for feedback cycling: 'generate' or 'stop'",
    )
    candidate_scores: list[dict] = Field(
        default_factory=list,
        description="Per-candidate accuracy summary",
    )
    winner_results: list[dict] = Field(
        default_factory=list,
        description="Per-query eval results for the winner (for failure analysis)",
    )
    critique_text: str = Field(
        "", description="Critique of winner results for next round's generation",
    )
    thinking_styles: list[str] = Field(
        default_factory=list,
        description="Sampled thinking styles for next round's generation",
    )
    winner_composite: float = Field(
        0.0, description="Composite score of the winner",
    )
    winner_pipeline_params: dict | None = Field(
        None, description="Pipeline params from winner (if changed)",
    )


class L1EvaluateNode(NodeBase[L1EvaluateInput, L1EvaluateOutput]):
    """Evaluate candidates via backend, select winner, critique results.

    Wraps ``evaluate_and_select_winner()`` from ``api.services.prompt_optimizer``
    and ``CritiqueAgent`` from ``api.services.campaign.critique``.

    Config:
        eval_ctx: Pre-built EvalContext from orchestrator (required)
        improvement_threshold: Min accuracy improvement to accept (default: 0.01)
        on_candidate_eval: Optional callback per candidate
        on_query_eval: Optional callback per query
        model: LLM model identifier (for critique)
        provider: LLM provider (for critique)
        enable_critique: Whether to run critique (default: false)
        critique_positive_threshold: Accuracy threshold for positive critique (default: 0.7)
        thinking_styles_seed: Seed for thinking style sampling (default: 42)
    """

    def _node_obs_type(self) -> str:
        return "span"

    @classmethod
    def get_input_model(cls) -> Type[L1EvaluateInput]:
        return L1EvaluateInput

    @classmethod
    def get_output_model(cls) -> Type[L1EvaluateOutput]:
        return L1EvaluateOutput

    async def _execute(self, input_data: L1EvaluateInput) -> L1EvaluateOutput:
        from api.services.prompt_optimizer import evaluate_and_select_winner

        ctx = self.config.get("eval_ctx")
        if ctx is None:
            raise ValueError("L1EvaluateNode requires 'eval_ctx' in config")

        result = await evaluate_and_select_winner(
            input_data.candidates,
            input_data.eval_data,
            input_data.current_best,
            ctx,
            improvement_threshold=self.config.get("improvement_threshold", 0.01),
            on_candidate_eval=self.config.get("on_candidate_eval"),
            on_query_eval=self.config.get("on_query_eval"),
        )

        # Run critique on winner results (Option A: inside L1EvaluateNode)
        critique_text = ""
        if self.config.get("enable_critique", False):
            try:
                from api.services.campaign.critique import CritiqueAgent
                from api.services.llm_client import get_llm_client

                llm_client = get_llm_client(self.config.get("provider"))
                agent = CritiqueAgent(llm_client, model=self.config.get("model"))
                critique_text = await agent.run(
                    result["winner_results"],
                    result["winner_accuracy"],
                    threshold=self.config.get("critique_positive_threshold", 0.7),
                )
            except Exception:
                logger.warning("Critique failed in L1EvaluateNode", exc_info=True)

        # Sample thinking styles for next round
        from api.services.campaign.critique import sample_thinking_styles

        thinking_styles = sample_thinking_styles(
            n=3,
            seed=self.config.get("thinking_styles_seed", 42),
        )

        return L1EvaluateOutput(
            winner=result["winner"],
            winner_prompt_state=result["winner_prompt_state"],
            winner_accuracy=result["winner_accuracy"],
            improved=result["improved"],
            next_action=result.get("next_action", "generate"),
            candidate_scores=result.get("candidate_scores", []),
            winner_results=result.get("winner_results", []),
            critique_text=critique_text,
            thinking_styles=thinking_styles,
            winner_composite=result.get("winner_composite", result["winner_accuracy"]),
            winner_pipeline_params=result.get("winner_pipeline_params"),
        )


# ---------------------------------------------------------------------------
# L2RefineNode — L2 refine_context transition
# ---------------------------------------------------------------------------


class L2RefineInput(BaseModel):
    """Input for L2RefineNode."""

    prompt_state: dict = Field(..., description="Current PromptState (serialized)")
    stalled_rounds: list[dict] = Field(
        ..., description="Recent stalled round summaries",
    )
    eval_data: list[dict] = Field(..., description="Evaluation query dicts")
    pipeline_params: dict | None = Field(
        None, description="Current pipeline parameters",
    )


class L2RefineOutput(BaseModel):
    """Output from L2RefineNode."""

    prompt_state: dict = Field(..., description="New PromptState (serialized)")
    pipeline_params: dict | None = Field(
        None, description="Updated pipeline params (None if unchanged)",
    )
    changes_description: str = Field(
        "", description="Description of changes made",
    )


class L2RefineNode(NodeBase[L2RefineInput, L2RefineOutput]):
    """L2 refine_context transition: adjust parameters and context.

    Wraps ``refine_context()`` from ``api.services.campaign.layer_transitions``.

    Config:
        model: LLM model identifier
        provider: LLM provider
        temperature: Temperature for L2 LLM call (default: 0.3)
        pipeline_schema: PipelineSchema object (optional)
    """

    @classmethod
    def get_input_model(cls) -> Type[L2RefineInput]:
        return L2RefineInput

    @classmethod
    def get_output_model(cls) -> Type[L2RefineOutput]:
        return L2RefineOutput

    async def _execute(self, input_data: L2RefineInput) -> L2RefineOutput:
        from api.models.prompt_state import PromptState
        from api.services.campaign.layer_transitions import refine_context
        from api.services.llm_client import get_llm_client

        client = get_llm_client(self.config.get("provider"))
        ps = PromptState(**input_data.prompt_state)

        tr = await refine_context(
            ps,
            input_data.stalled_rounds,
            input_data.eval_data,
            client,
            model=self.config.get("model"),
            temperature=self.config.get("temperature", 0.3),
            pipeline_params=input_data.pipeline_params,
            pipeline_schema=self.config.get("pipeline_schema"),
        )

        return L2RefineOutput(
            prompt_state=tr.prompt_state.model_dump(),
            pipeline_params=tr.pipeline_params,
            changes_description=tr.prompt_state.changes_description or "",
        )


# ---------------------------------------------------------------------------
# L3ModifyPlanNode — L3 modify_plan transition
# ---------------------------------------------------------------------------


class L3ModifyPlanInput(BaseModel):
    """Input for L3ModifyPlanNode."""

    prompt_state: dict = Field(..., description="Current PromptState (serialized)")
    l2_history: list[dict] = Field(
        ..., description="L2 adjustment history summaries",
    )
    eval_data: list[dict] = Field(..., description="Evaluation query dicts")
    pipeline_params: dict | None = Field(
        None, description="Current pipeline parameters",
    )


class L3ModifyPlanOutput(BaseModel):
    """Output from L3ModifyPlanNode."""

    prompt_state: dict = Field(..., description="New PromptState (serialized)")
    pipeline_params: dict | None = Field(
        None, description="Updated pipeline params (None if unchanged)",
    )
    changes_description: str = Field(
        "", description="Description of changes made",
    )


class L3ModifyPlanNode(NodeBase[L3ModifyPlanInput, L3ModifyPlanOutput]):
    """L3 modify_plan transition: change strategic optimization plan.

    Wraps ``modify_plan()`` from ``api.services.campaign.layer_transitions``.

    Config:
        model: LLM model identifier
        provider: LLM provider
        temperature: Temperature for L3 LLM call (default: 0.5)
        pipeline_schema: PipelineSchema object (optional)
    """

    @classmethod
    def get_input_model(cls) -> Type[L3ModifyPlanInput]:
        return L3ModifyPlanInput

    @classmethod
    def get_output_model(cls) -> Type[L3ModifyPlanOutput]:
        return L3ModifyPlanOutput

    async def _execute(self, input_data: L3ModifyPlanInput) -> L3ModifyPlanOutput:
        from api.models.prompt_state import PromptState
        from api.services.campaign.layer_transitions import modify_plan
        from api.services.llm_client import get_llm_client

        client = get_llm_client(self.config.get("provider"))
        ps = PromptState(**input_data.prompt_state)

        tr = await modify_plan(
            ps,
            input_data.l2_history,
            input_data.eval_data,
            client,
            model=self.config.get("model"),
            temperature=self.config.get("temperature", 0.5),
            pipeline_params=input_data.pipeline_params,
            pipeline_schema=self.config.get("pipeline_schema"),
        )

        return L3ModifyPlanOutput(
            prompt_state=tr.prompt_state.model_dump(),
            pipeline_params=tr.pipeline_params,
            changes_description=tr.prompt_state.changes_description or "",
        )
