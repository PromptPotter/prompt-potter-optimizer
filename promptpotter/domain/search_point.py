"""SearchPoint hierarchy + ``TaskDecomposition``."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, field_validator

from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.shared.hashing import content_hash

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema


PARAM_FORBIDDEN_KEYS: frozenset[str] = frozenset({"model", "provider"})
"""Optimizer-forbidden ``pipeline_params[node]`` keys (operator-fixed via dataset overlay)."""


PARAM_SCOPE_KEYS: frozenset[str] = frozenset(
    {"temperature", "max_tokens", "reasoning_effort", "top_p"}
)
"""Per-node LLM-call tunable axes (non-prompt). Drives param-scope discipline + continuous_envelope."""


class SearchPoint(BaseModel):
    """Abstract — subclassed by frozen ``JobSearchPoint`` and mutable ``OptSearchPoint``."""

    def render(self) -> str:
        raise NotImplementedError


class JobSearchPoint(SearchPoint):
    """Frozen target-layer point: ``pipeline_params`` + optional ``prompt_fields``."""

    model_config = {"frozen": True}

    pipeline_params: dict[str, Any] | None = None
    prompt_fields: dict[str, Any] | None = None

    @field_validator("pipeline_params")
    @classmethod
    def _params_nested_by_node(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        """Reject flat ``{param: value}`` — shape is ``{node: {param: value}, "steps": [...]}``."""
        if v is None:
            return v
        flat = sorted(k for k, val in v.items() if isinstance(val, (str, int, float)))
        if flat:
            raise ValueError(
                f"pipeline_params must be nested-by-node — keys {flat} carry "
                "scalar values. Expected {node: {param: value}}, not a flat map."
            )
        return v

    def render(self) -> str:
        """Read the cached rendered prompt out of ``pipeline_params``."""
        for node_config in (self.pipeline_params or {}).values():
            if isinstance(node_config, dict) and "prompt" in node_config:
                return str(node_config["prompt"])
        return ""

    def sp_hash(self, pipeline_schema: PipelineSchema | None = None) -> str:
        """SearchPoint identity hash; falls back to flat ``pipeline_params`` hash without schema.

        Role split (the codebase has TWO hashes — don't conflate): ``sp_hash`` is the
        optimizer/prompt-side dedup over the node-config structure; ``content_hash``
        (below) is the **measurement-archive key**. "Will this re-score?" is answered by
        ``content_hash`` / ``node_configs``, not this one."""
        if pipeline_schema is not None:
            return pipeline_schema.sp_hash(self.pipeline_params or {})
        from promptpotter.domain.pipeline_schema import stable_hash

        return stable_hash(self.pipeline_params or {})

    def content_hash(self, dataset: list[Any]) -> str:
        """Content-addressed hash for measurement deduplication — the ``archive/
        measurements/`` key (rendered prompt + dataset + ``pipeline_params``). Because
        ``pipeline_params`` here is the overlay-merged set (config.py folds the connector
        ``model``/config in), two points differing only by model hash DIFFERENTLY and do
        NOT share measurements. This is the hash that decides re-score vs cache-replay."""
        return content_hash(
            self.render(),
            dataset,
            self.pipeline_params,
        )

    def derive(self, **changes: Any) -> JobSearchPoint:
        """New JobSearchPoint with modifications. Re-renders prompt on prompt_fields change."""
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
    without ``.get()`` guards. Operator/LLM-facing — these strings ride
    the ``task_context`` injection into every optimizer prompt (L1, L1
    critique, L2, L3), so the per-field semantics are load-bearing.

    Field semantics (also reflected in the L2 meta-prompt):

    - ``domain`` — one-line task family ("competition mathematics",
      "biomedical entity normalization"). Steers L1's persona /
      thinking_style; rarely changes after origin.
    - ``pipeline_purpose`` — one-sentence "what this campaign is
      trying to produce", framed for an outside reader. Set at
      origin, refined by L2 only on plan-level shifts.
    - ``data_characteristics`` — concrete properties of the sample
      pool L1 should account for (length, modality, distribution
      skew, known bias). Evidence-anchored — cites observations from
      `parent_panel` / `sibling_yield`, not speculation.
    - ``optimization_goals`` — what we're optimising for, in
      operator vocabulary (accuracy vs latency vs compactness). Maps
      conceptually to the composite-fitness formula but is not its
      verbatim text.
    - ``key_challenges`` — the failure patterns L1 should defend
      against next round. L2's primary refinement surface;
      accumulative, merged on each L2 fire. The
      `l2_task_context_verbatim_repeat` validator fires here when L2
      restates without delta.
    - ``upstream_context`` — task-framing prepended around
      ``problem_description`` at render time (see
      ``OptSearchPoint._field_value``).
    - ``downstream_context`` — task-framing appended around
      ``problem_description`` at render time.
    - ``raw_description`` — verbatim operator-supplied task
      description (``datasets/{name}/task_description.md``);
      preserved separately so refinements never overwrite the source.
    """

    domain: str = ""
    pipeline_purpose: str = ""
    data_characteristics: str = ""
    optimization_goals: str = ""
    key_challenges: str = ""
    upstream_context: str = ""
    downstream_context: str = ""
    raw_description: str = ""

    FIELDS: ClassVar[tuple[str, ...]] = (
        "domain",
        "pipeline_purpose",
        "data_characteristics",
        "optimization_goals",
        "key_challenges",
        "upstream_context",
        "downstream_context",
    )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> TaskDecomposition:
        """Construct from dict; coerces list values to comma-joined strings."""
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

    def merge(self, overrides: dict[str, Any]) -> TaskDecomposition:
        base = self.to_dict()
        base.update(overrides)
        return self.from_dict(base)

    def items(self) -> list[tuple[str, str]]:
        return [(f.name, getattr(self, f.name)) for f in fields(self)]

    def __len__(self) -> int:
        return sum(1 for f in fields(self) if getattr(self, f.name))

    def __bool__(self) -> bool:
        return any(getattr(self, f.name) for f in fields(self))


__all__ = [
    "PARAM_FORBIDDEN_KEYS",
    "PARAM_SCOPE_KEYS",
    "JobSearchPoint",
    "SearchPoint",
    "TaskDecomposition",
]
