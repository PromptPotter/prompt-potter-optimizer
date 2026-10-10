from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import ConfigDict, Field, field_validator

from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.hashing import content_hash, shapes_optimizer_prompt

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema


# The decomposition field SET; the render ORDER is per class (`PromptTemplate.RENDER_ORDER`).
PROMPT_STRING_FIELDS: Annotated[list[str], shapes_optimizer_prompt] = [
    "persona",
    "task_intent",
    "problem_description",
    "instruction",
    "thinking_style",
    "answer_format",
]


# WHO ANSWERS the call: a steer touching only these inherits its done C0.
WHO_ANSWERS_KEYS: Annotated[frozenset[str], shapes_optimizer_prompt] = frozenset(
    {"model", "provider", "route_order"}
)


# Never a search axis: hosts of one model disagree, so an arm moving either measures the plumbing.
PARAM_FORBIDDEN_KEYS: Annotated[frozenset[str], shapes_optimizer_prompt] = frozenset(
    {"provider", "route_order"}
)

assert PARAM_FORBIDDEN_KEYS <= WHO_ANSWERS_KEYS


PARAM_SCOPE_KEYS: Annotated[frozenset[str], shapes_optimizer_prompt] = frozenset(
    {"temperature", "max_tokens", "reasoning_effort", "top_p"}
)


def strip_rendered_prompt(pipeline_params: dict[str, Any] | None) -> dict[str, Any]:
    """A node's ``prompt`` is the RENDER of ``prompt_fields``, never configuration: persisted, it goes stale."""
    return {
        node: ({k: v for k, v in cfg.items() if k != "prompt"} if isinstance(cfg, dict) else cfg)
        for node, cfg in (pipeline_params or {}).items()
    }


class JobSearchPoint(StrictModel):
    model_config = ConfigDict(frozen=True)

    # Empty is the ONE spelling of "carries none": `content_hash` omits a falsy `pipeline_params`.
    pipeline_params: dict[str, Any] = Field(default_factory=dict)
    prompt_fields: dict[str, Any] = Field(default_factory=dict)

    @field_validator("pipeline_params")
    @classmethod
    def _params_nested_by_node(cls, v: dict[str, Any]) -> dict[str, Any]:
        flat = sorted(k for k, val in v.items() if isinstance(val, (str, int, float)))
        if flat:
            raise ValueError(
                f"pipeline_params must be nested-by-node — keys {flat} carry "
                "scalar values. Expected {node: {param: value}}, not a flat map."
            )
        return v

    def render(self) -> str:
        for node_config in self.pipeline_params.values():
            if isinstance(node_config, dict) and "prompt" in node_config:
                return str(node_config["prompt"])
        return ""

    @property
    def config_params(self) -> dict[str, Any]:
        return strip_rendered_prompt(self.pipeline_params)

    def sp_hash(self, pipeline_schema: PipelineSchema) -> str:
        """Over the SCHEMA-RESOLVED node configs alone: the same across subsets, as :meth:`content_hash` is not."""
        return pipeline_schema.sp_hash(self.pipeline_params)

    def content_hash(self, dataset: list[Any]) -> str:
        """Names a campaign's root (``runner/campaign_ids.py``); no measurement is filed under it."""
        return content_hash(
            self.render(),
            dataset,
            self.pipeline_params,
        )


# Operator-authored, never measured, frozen for the run: `application/optimizers/potter/CLAUDE.md` § L2.
FRAMING_FIELDS: Annotated[frozenset[str], shapes_optimizer_prompt] = frozenset(
    {
        "domain",
        "pipeline_purpose",
        "data_characteristics",
        "optimization_goals",
        "key_challenges",
    }
)

# Enforced ONCE at mint, never at render: a render-time clip loses words the author never sees go.
FRAMING_VALUE_BUDGET: Annotated[int, shapes_optimizer_prompt] = 600

# The per-field budget alone permits five full fields, rendered VERBATIM into every optimizer prompt.
FRAMING_TOTAL_BUDGET: Annotated[int, shapes_optimizer_prompt] = 1500


@shapes_optimizer_prompt
@dataclass
class TaskDecomposition:
    domain: str = ""
    pipeline_purpose: str = ""
    data_characteristics: str = ""
    optimization_goals: str = ""
    key_challenges: str = ""
    upstream_context: str = ""
    downstream_context: str = ""
    raw_description: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> TaskDecomposition:
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

    def check_budget(self, *, source: str) -> None:
        sizes = {k: len(v) for k, v in self.to_dict().items() if k in FRAMING_FIELDS}
        over = [(k, n) for k, n in sizes.items() if n > FRAMING_VALUE_BUDGET]
        total = sum(sizes.values())
        if not over and total <= FRAMING_TOTAL_BUDGET:
            return
        detail = (
            ", ".join(f"{k} is {n} chars" for k, n in sorted(over))
            if over
            else f"the five fields total {total} chars"
        )
        budget = FRAMING_VALUE_BUDGET if over else FRAMING_TOTAL_BUDGET
        scope = "per-field" if over else "total"
        raise ValueError(
            f"task_context framing exceeds the {budget}-char {scope} budget "
            f"in {source}: {detail}. These fields render verbatim into every optimizer "
            f"prompt — edit them down rather than letting a renderer choose which half the "
            f"model sees. Put the detail in task_description.md, which has no budget."
        )

    def items(self) -> list[tuple[str, str]]:
        return [(f.name, getattr(self, f.name)) for f in fields(self)]

    def __len__(self) -> int:
        return sum(1 for f in fields(self) if getattr(self, f.name))

    def __bool__(self) -> bool:
        return any(getattr(self, f.name) for f in fields(self))


__all__ = [
    "PARAM_FORBIDDEN_KEYS",
    "PARAM_SCOPE_KEYS",
    "WHO_ANSWERS_KEYS",
    "JobSearchPoint",
    "TaskDecomposition",
    "strip_rendered_prompt",
]
