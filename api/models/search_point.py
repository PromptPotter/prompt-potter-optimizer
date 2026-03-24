"""SearchPoint hierarchy — points in pipeline search spaces.

SearchPoint is the abstract base for any point in a pipeline's search space.
Two concrete subclasses:

  - ``JobSearchPoint`` — the user's evaluation space (model + temperature +
    pipeline_params). Frozen, content-hashable. The rendered prompt lives
    inside ``pipeline_params`` as a node config value.

  - ``OptSearchPoint`` (in opt_search_point.py) — the optimizer's working
    state (prompt decomposition, L2/L3 state, optimization memory). Mutable.

Formula: ``f(JobSearchPoint, PipelineSchema, eval_data) → scores``
"""
from __future__ import annotations

import hashlib

from pydantic import BaseModel

from api.models.hashing import HASH_TRUNCATE, eval_content_hash, sp_identity_hash


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class SearchPoint(BaseModel):
    """A point in any pipeline's search space.

    Subclassed by JobSearchPoint (target layer, frozen) and
    OptSearchPoint (optimizer layer, mutable).
    """

    def render(self) -> str:
        """Render the key output of this search point (e.g., the prompt string)."""
        raise NotImplementedError

    def point_hash(self) -> str:
        """Identity hash for this search point."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# JobSearchPoint (target layer)
# ---------------------------------------------------------------------------

class JobSearchPoint(SearchPoint):
    """A point in the target evaluation space.

    Frozen (immutable). Use ``derive()`` to create variants.
    Carries model + temperature + pipeline_params. The rendered prompt
    lives inside ``pipeline_params`` (e.g., ``{"llm_ranking": {"prompt": "..."}}``)
    — the prompt is just another tunable pipeline parameter.
    """

    model_config = {"frozen": True}

    model: str = ""
    temperature: float = 0.0
    pipeline_params: dict | None = None

    def render(self) -> str:
        """Render the prompt string from pipeline_params."""
        pp = self.pipeline_params or {}
        for node_config in pp.values():
            if isinstance(node_config, dict) and "prompt" in node_config:
                return node_config["prompt"]
        return ""

    def point_hash(self) -> str:
        """Alias for sp_hash()."""
        return self.sp_hash()

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

    def derive(self, **changes) -> JobSearchPoint:
        """Create a new JobSearchPoint with modifications."""
        return JobSearchPoint(
            model=changes.get("model", self.model),
            temperature=changes.get("temperature", self.temperature),
            pipeline_params=changes.get("pipeline_params", self.pipeline_params),
        )
