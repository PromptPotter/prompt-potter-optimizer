"""SearchPoint — a point in the target evaluation space.

Flat, frozen, content-hashable. The lightweight twin of OptSearchPoint.

Bundles the dimensions that fully specify a target evaluation:
  - model          — LLM model identifier
  - temperature    — LLM inference temperature
  - pipeline_params — backend pipeline overrides (rendered prompt lives here)

The rendered prompt is a value inside ``pipeline_params`` (e.g.,
``{"llm_ranking": {"prompt": "..."}}``) — not a separate field. This
means the prompt is just another tunable pipeline parameter.

Related objects:
  - ``OptSearchPoint`` is the more capable twin — holds prompt decomposition
    fields, optimizer state, and produces SearchPoints via ``to_search_point()``.
  - ``EvalContext`` carries infrastructure (backend_client, store, obs)
    for running evaluations.

Migration note: ``prompt_state`` field is retained temporarily while
consumers are migrated to the flat pattern. It will be removed once all
services use ``OptSearchPoint.to_search_point()`` or construct SearchPoint
with prompt already baked into ``pipeline_params``.
"""
from __future__ import annotations

import hashlib

from pydantic import BaseModel, Field

from api.models.hashing import HASH_TRUNCATE, eval_content_hash, sp_identity_hash


# Kept for backward compat during migration — will be removed
from api.models.prompt_state import LAYER_FIELDS, PromptState

_PROMPT_STATE_FIELDS = set()
for _fields in LAYER_FIELDS.values():
    _PROMPT_STATE_FIELDS.update(_fields)
_PROMPT_STATE_FIELDS |= {"id", "parent_id", "changes_description", "created_at"}


class SearchPoint(BaseModel):
    """A point in the target evaluation space.

    Frozen (immutable). Use ``derive()`` to create variants.

    Formula: ``f(SearchPoint, PipelineSchema, eval_data) → scores``
    """

    model_config = {"frozen": True}

    # --- Legacy field (migration) ---
    prompt_state: PromptState = Field(default_factory=PromptState)

    # --- Core fields ---
    model: str = ""
    temperature: float = 0.0
    pipeline_params: dict | None = None

    def render(self) -> str:
        """Render the prompt string.

        Prefers prompt from pipeline_params if set, falls back to
        prompt_state.render() for backward compat during migration.
        """
        # New path: prompt in pipeline_params
        pp = self.pipeline_params or {}
        for node_config in pp.values():
            if isinstance(node_config, dict) and "prompt" in node_config:
                return node_config["prompt"]
        # Legacy path
        return self.prompt_state.render()

    def sp_hash(self) -> str:
        """SearchPoint identity hash — eval_data independent."""
        rendered = self.render()
        rp_hash = hashlib.sha256(rendered.encode()).hexdigest()[:HASH_TRUNCATE]
        return sp_identity_hash(
            rp_hash,
            self.model,
            self.temperature,
            self.pipeline_params,
        )

    def content_hash(self, eval_data: list) -> str:
        """Content-addressed hash for evaluation deduplication."""
        return eval_content_hash(
            self.render(),
            eval_data,
            self.model,
            self.temperature,
            self.pipeline_params,
        )

    def derive(self, **changes) -> SearchPoint:
        """Create a new SearchPoint with modifications.

        PromptState fields are routed to prompt_state.derive() (legacy).
        SearchPoint-level fields updated directly.
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
