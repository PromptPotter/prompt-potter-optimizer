"""SearchPoint hierarchy (abstract → JobSearchPoint + PromptTemplate → OptSearchPoint).

Formula: ``f(JobSearchPoint, PipelineSchema, dataset) → scores``. ``TaskDecomposition``
carries LLM-decomposed structured domain context.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel

from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.shared.hashing import content_hash

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema


PARAM_FORBIDDEN_KEYS: frozenset[str] = frozenset({"model", "provider"})
"""``pipeline_params[node]`` keys the optimizer must never mutate.

Operator-fixed at the dataset overlay
(``datasets/{name}/pipeline.json::nodes.{name}.config``). The L1 strict
validator rejects any candidate whose ``pipeline_params_override`` carries
one of these keys; downstream signal renderers (axis digests, runtime-
failure injections) likewise scrub them so L2/L3 prompts don't surface
them as candidate axes.
"""


class SearchPoint(BaseModel):
    """A point in any pipeline's search space.

    Subclassed by JobSearchPoint (target layer, frozen) and
    OptSearchPoint (optimizer layer, mutable).
    """

    def render(self) -> str:
        """Render the key output of this search point (e.g., the prompt string)."""
        raise NotImplementedError


class JobSearchPoint(SearchPoint):
    """Frozen point in the target scoring space: pipeline_params + optional prompt_fields.

    Rendered prompt is injected into ``pipeline_params`` as a node config value — the
    prompt is just another tunable pipeline parameter. Use ``derive()`` for variants;
    when ``prompt_fields`` is set, ``derive()`` can vary prompt fields without an
    ``OptSearchPoint``.
    """

    model_config = {"frozen": True}

    pipeline_params: dict | None = None
    prompt_fields: dict | None = None

    def render(self) -> str:
        """Read the cached rendered prompt out of ``pipeline_params``.

        ``to_job_search_point`` and ``derive`` are the only constructors
        that set ``prompt_fields``, and both also inject the rendered
        string into ``pipeline_params[prompt_node]["prompt"]``, so the
        cached copy is the single source.
        """
        for node_config in (self.pipeline_params or {}).values():
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
        """Content-addressed hash for measurement deduplication."""
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
        new_pf = self.prompt_fields

        if "prompt_fields" in changes:
            new_pf = {**(self.prompt_fields or {}), **changes["prompt_fields"]}
            sections = [v for f in PROMPT_STRING_FIELDS if (v := new_pf.get(f))]
            if block := new_pf.get("few_shot_block"):
                sections.append(block)
            rendered = "\n\n".join(sections)
            new_pp = dict(new_pp or {})
            for node_name, node_cfg in new_pp.items():
                if isinstance(node_cfg, dict) and "prompt" in node_cfg:
                    new_pp[node_name] = {**node_cfg, "prompt": rendered}
                    break
            else:
                if rendered:
                    raise ValueError(
                        "Cannot inject rendered prompt: no node with a 'prompt' key "
                        "found in pipeline_params."
                    )

        return JobSearchPoint(pipeline_params=new_pp, prompt_fields=new_pf)


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
        """Construct from dict, ignoring unknown keys.

        Coerces list values to comma-joined strings. L2's response schema
        types ``task_context`` as ``dict[str, Any]`` (permissive at the wire),
        and LLMs frequently return enumeration-style fields like
        ``key_challenges`` as a JSON list. The TaskDecomposition fields are
        typed ``str`` for stable display + serialization, so list payloads
        get joined here at the ingest boundary.
        """
        if not d:
            return cls()
        known = {f.name for f in fields(cls)}
        coerced: dict[str, str] = {}
        for k, v in d.items():
            if k not in known:
                continue
            if isinstance(v, list):
                coerced[k] = ", ".join(str(item) for item in v)
            elif v is None:
                coerced[k] = ""
            else:
                coerced[k] = str(v)
        return cls(**coerced)

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


__all__ = ["PARAM_FORBIDDEN_KEYS", "JobSearchPoint", "SearchPoint", "TaskDecomposition"]
