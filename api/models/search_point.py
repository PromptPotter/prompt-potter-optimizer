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

from api.shared.hashing import (
    HASH_TRUNCATE,
    PROMPT_STRING_FIELDS,
    eval_content_hash,
    sp_identity_hash,
)


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


# ---------------------------------------------------------------------------
# JobSearchPoint (target layer)
# ---------------------------------------------------------------------------

class JobSearchPoint(SearchPoint):
    """A point in the target evaluation space.

    Frozen (immutable). Use ``derive()`` to create variants.
    Carries model + temperature + pipeline_params + optional prompt_fields.
    The rendered prompt lives inside ``pipeline_params`` as a node config
    value — the prompt is just another tunable pipeline parameter.

    When ``prompt_fields`` is set (populated by
    ``OptSearchPoint.to_job_search_point()``), ``derive()`` can produce
    prompt-field variants without an ``OptSearchPoint``.
    """

    model_config = {"frozen": True}

    model: str = ""
    temperature: float = 0.0
    pipeline_params: dict | None = None
    prompt_fields: dict | None = None

    def render(self) -> str:
        """Render the prompt string.

        Assembles from ``prompt_fields`` when present (preserving
        ``PROMPT_STRING_FIELDS`` order), otherwise extracts from
        ``pipeline_params``.
        """
        if self.prompt_fields:
            return _render_from_fields(self.prompt_fields)
        pp = self.pipeline_params or {}
        for node_config in pp.values():
            if isinstance(node_config, dict) and "prompt" in node_config:
                return node_config["prompt"]
        return ""

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
        """Create a new JobSearchPoint with modifications.

        Accepts ``prompt_fields`` as a partial dict of field overrides.
        When prompt_fields are changed, the rendered prompt is re-assembled
        and injected into pipeline_params to keep sp_hash consistent.
        """
        new_pp = changes.get("pipeline_params", self.pipeline_params)
        new_pf = self.prompt_fields  # carry forward by default

        if "prompt_fields" in changes:
            base_pf = dict(self.prompt_fields or {})
            base_pf.update(changes["prompt_fields"])
            new_pf = base_pf

            # Re-render and inject into pipeline_params
            rendered = _render_from_fields(new_pf)
            new_pp = dict(new_pp or {})
            injected = False
            for node_name, node_cfg in new_pp.items():
                if isinstance(node_cfg, dict) and "prompt" in node_cfg:
                    new_pp[node_name] = {**node_cfg, "prompt": rendered}
                    injected = True
                    break
            if not injected and rendered:
                raise ValueError(
                    "Cannot inject rendered prompt: no node with a 'prompt' key "
                    "found in pipeline_params. Pass pipeline_params with the "
                    "target node pre-configured."
                )

        return JobSearchPoint(
            model=changes.get("model", self.model),
            temperature=changes.get("temperature", self.temperature),
            pipeline_params=new_pp,
            prompt_fields=new_pf,
        )


def _render_from_fields(fields: dict) -> str:
    """Assemble prompt string from a prompt_fields dict."""
    parts: list[str] = []
    for field_name in PROMPT_STRING_FIELDS:
        value = fields.get(field_name, "")
        if value:
            parts.append(value)
    few_shot = fields.get("few_shot_block", "")
    if few_shot:
        parts.append(few_shot)
    return "\n\n".join(parts)
