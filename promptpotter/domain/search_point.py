"""SearchPoint hierarchy and TaskDecomposition.

SearchPoint is the abstract base for any point in a pipeline's search space.
Three-level hierarchy (see ``opt_search_point.py`` for full tree)::

    SearchPoint (abstract — render())
        ├── JobSearchPoint      — target evaluation space (pipeline_params, frozen)
        └── PromptTemplate      — 8-field prompt scheme (render/compile)
                └── OptSearchPoint  — optimizer working state (+ lineage, L2/L3, memory)

Formula: ``f(JobSearchPoint, PipelineSchema, dataset) → scores``

TaskDecomposition carries the structured domain context produced by
LLM-assisted decomposition of user task descriptions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel

from promptpotter.shared.constants import PROMPT_STRING_FIELDS
from promptpotter.shared.hashing import content_hash

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema


class SearchPoint(BaseModel):
    """A point in any pipeline's search space.

    Subclassed by JobSearchPoint (target layer, frozen) and
    OptSearchPoint (optimizer layer, mutable).
    """

    def render(self) -> str:
        """Render the key output of this search point (e.g., the prompt string)."""
        raise NotImplementedError


class JobSearchPoint(SearchPoint):
    """A point in the target evaluation space.

    Frozen (immutable). Use ``derive()`` to create variants.
    Carries pipeline_params + optional prompt_fields.
    The rendered prompt lives inside ``pipeline_params`` as a node config
    value — the prompt is just another tunable pipeline parameter.

    When ``prompt_fields`` is set (populated by
    ``OptSearchPoint.to_job_search_point()``), ``derive()`` can produce
    prompt-field variants without an ``OptSearchPoint``.
    """

    model_config = {"frozen": True}

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

    def sp_hash(self, pipeline_schema: PipelineSchema | None = None) -> str:
        """SearchPoint identity hash.

        With a schema, delegates to ``pipeline_schema.sp_hash``.
        Without one (display comparisons, tests), falls back to a flat
        hash of ``pipeline_params``.
        """
        if pipeline_schema is not None:
            return pipeline_schema.sp_hash(self.pipeline_params or {})
        from promptpotter.domain.pipeline_schema import stable_hash

        return stable_hash(self.pipeline_params or {})

    def content_hash(self, dataset: list) -> str:
        """Content-addressed hash for evaluation deduplication."""
        return content_hash(
            self.render(),
            dataset,
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
            pipeline_params=new_pp,
            prompt_fields=new_pf,
        )


def _render_from_fields(pf: dict) -> str:
    """Assemble prompt string from a prompt_fields dict."""
    parts: list[str] = []
    for field_name in PROMPT_STRING_FIELDS:
        value = pf.get(field_name, "")
        if value:
            parts.append(value)
    few_shot = pf.get("few_shot_block", "")
    if few_shot:
        parts.append(few_shot)
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# TaskDecomposition — structured domain context for optimizer LLM calls
# ---------------------------------------------------------------------------


@dataclass
class TaskDecomposition:
    """Typed domain context produced by task-description decomposition.

    All fields default to ``""`` so the object is always safe to read
    without ``.get()`` guards.
    """

    domain: str = ""
    pipeline_purpose: str = ""
    data_characteristics: str = ""
    optimization_goals: str = ""
    key_challenges: str = ""
    upstream_context: str = ""
    downstream_context: str = ""
    raw_description: str = ""

    # Display / iteration fields (excludes upstream/downstream/raw_description).
    FIELDS: ClassVar[tuple[str, ...]] = (
        "domain",
        "pipeline_purpose",
        "data_characteristics",
        "optimization_goals",
        "key_challenges",
        "upstream_context",
        "downstream_context",
    )

    # ── Serialization ────────────────────────────────────────────────

    def to_dict(self) -> dict[str, str]:
        """Serialize to plain dict (JSON-safe)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> TaskDecomposition:
        """Construct from dict, ignoring unknown keys."""
        if not d:
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    # ── Merge / copy ────────────────────────────────────────────────

    def merge(self, overrides: dict[str, Any]) -> TaskDecomposition:
        """Return a new TaskDecomposition with *overrides* applied on top."""
        base = self.to_dict()
        base.update(overrides)
        return self.from_dict(base)

    # ── Iteration helpers (support existing dict-like access patterns) ──

    def items(self) -> list[tuple[str, str]]:
        """Return (field, value) pairs — mirrors ``dict.items()``."""
        return [(f.name, getattr(self, f.name)) for f in fields(self)]

    def __len__(self) -> int:
        """Number of populated (non-empty) fields."""
        return sum(1 for f in fields(self) if getattr(self, f.name))

    def __bool__(self) -> bool:
        """True if any field is non-empty."""
        return any(getattr(self, f.name) for f in fields(self))
