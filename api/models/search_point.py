"""SearchPoint — a point in the optimization search space.

From mathematical optimization literature: a *search point* is a specific
point in the search space being evaluated.  Standard across optimization
subdisciplines.

Bundles the four dimensions that fully specify an evaluation:
  - PromptState    — the prompt configuration (layers 1-3)
  - model          — LLM model identifier
  - temperature    — LLM inference temperature
  - pipeline_params — backend pipeline overrides (node_config)

Related objects:
  - ``EvalContext`` carries infrastructure (backend_client, store, obs,
    model, temperature, pipeline_params) for running evaluations.
  - ``_LoopState`` tracks ``current_sp`` and ``best_sp`` SearchPoints
    as the feedback cycle evolves through optimization rounds.
  - ``CycleConfig`` configures the feedback loop parameters.
"""
from __future__ import annotations

from pydantic import BaseModel

from api.models.prompt_state import LAYER_FIELDS, PromptState
from api.models.hashing import eval_content_hash


# All PromptState field names (used by derive to route kwargs)
_PROMPT_STATE_FIELDS = set()
for _fields in LAYER_FIELDS.values():
    _PROMPT_STATE_FIELDS.update(_fields)
_PROMPT_STATE_FIELDS |= {"id", "parent_id", "changes_description", "created_at"}


class SearchPoint(BaseModel):
    """A point in the optimization search space.

    Frozen (immutable). Use ``derive()`` to create variants.

    Formula: ``f(SearchPoint, PipelineSchema, eval_data) → scores``
    """

    model_config = {"frozen": True}

    prompt_state: PromptState
    model: str = ""
    temperature: float = 0.0
    pipeline_params: dict | None = None

    def render(self) -> str:
        """Delegate to ``prompt_state.render()``."""
        return self.prompt_state.render()

    def content_hash(self, eval_data: list) -> str:
        """Content-addressed hash for evaluation deduplication.

        Delegates to ``eval_content_hash()`` with this point's parameters,
        including ``pipeline_params`` when non-empty.
        """
        return eval_content_hash(
            self.prompt_state.render(),
            eval_data,
            self.model,
            self.temperature,
            self.pipeline_params,
        )

    def derive(self, **changes) -> SearchPoint:
        """Create a new SearchPoint with modifications.

        PromptState fields are routed to ``prompt_state.derive()``.
        SearchPoint-level fields (model, temperature, pipeline_params)
        are updated directly.
        """
        ps_changes = {}
        sp_changes = {}
        for key, value in changes.items():
            if key in _PROMPT_STATE_FIELDS:
                ps_changes[key] = value
            elif key == "prompt_state":
                sp_changes[key] = value
            else:
                sp_changes[key] = value

        new_ps = sp_changes.pop("prompt_state", None)
        if ps_changes:
            new_ps = (new_ps or self.prompt_state).derive(**ps_changes)
        elif new_ps is None:
            new_ps = self.prompt_state

        return SearchPoint(
            prompt_state=new_ps,
            model=sp_changes.get("model", self.model),
            temperature=sp_changes.get("temperature", self.temperature),
            pipeline_params=sp_changes.get("pipeline_params", self.pipeline_params),
        )
