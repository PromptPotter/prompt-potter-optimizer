from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import asdict, dataclass, fields
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import ConfigDict, Field

from promptpotter.domain.optimizer_template import OptimizerPromptEdits
from promptpotter.domain.pipeline_params import NodeCall, PipelineParams, _Roster
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.hashing import content_hash, shapes_optimizer_prompt

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema


# A prompt node's shots locus; no field may take its name, nor the render's (`prompt`).
SHOTS_KEY: Annotated[str, shapes_optimizer_prompt] = "shots"


@shapes_optimizer_prompt
class NodeConfig(NodeCall):
    """An llm node's resolved config: its call keys, naming its provider and model, and the prompt
    fields it sets over its template; its member's knobs are the member's (`optimizer_manifest.py`)."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    # A float as every route keys and sends it: an int laid here is the same call.
    temperature: float | None = None
    top_p: float | None = None
    # Rendered into the template, never sent: no call key.
    prompt_edits: OptimizerPromptEdits = Field(default_factory=OptimizerPromptEdits)

    def call_settings(self) -> dict[str, str | int | float | list[str] | None]:
        """Its engine call keys as laid, each to its value: what a call is keyed and sent by."""
        return self.model_dump(include=set(CALL_CONFIG_KEYS), exclude_unset=True)

    def laid(self, update: Mapping[str, object]) -> NodeConfig:
        """*update* laid over it, typed as a config is: a value of the wrong type refuses."""
        return NodeConfig.model_validate({**self.model_dump(exclude_unset=True), **update})


def _call_keys_in(roster: _Roster) -> frozenset[str]:
    return frozenset(
        name for name, info in NodeCall.model_fields.items() if roster in info.metadata
    )


WHO_ANSWERS_KEYS: Annotated[frozenset[str], shapes_optimizer_prompt] = _call_keys_in(
    _Roster.WHO_ANSWERS
)
PARAM_FORBIDDEN_KEYS: Annotated[frozenset[str], shapes_optimizer_prompt] = _call_keys_in(
    _Roster.FORBIDDEN
)
# A forbidden key changes who answers: else a steer of one inherits a C0 measured by another host.
assert PARAM_FORBIDDEN_KEYS <= WHO_ANSWERS_KEYS, PARAM_FORBIDDEN_KEYS - WHO_ANSWERS_KEYS
PARAM_SCOPE_KEYS: Annotated[frozenset[str], shapes_optimizer_prompt] = _call_keys_in(_Roster.SCOPE)
# Every key an llm node's config may set beside its knobs and prompt fields.
CALL_CONFIG_KEYS: Annotated[frozenset[str], shapes_optimizer_prompt] = frozenset(
    NodeCall.model_fields
)


def missing_template_vars(
    rendered: str, declared: list[str], *, fields: Collection[str]
) -> list[str]:
    """*fields* are excluded: the render assembles them, nothing substitutes them."""
    return [v for v in declared if v not in fields and "{{" + v + "}}" not in rendered]


def split_rendered_prompt(cfg: Mapping[str, object]) -> tuple[object, dict[str, object]]:
    """A node's ``prompt``, its prompt loci's RENDER (persisted, it goes stale), off its config."""
    config = dict(cfg)
    return config.pop("prompt", None), config


def strip_rendered_prompt(pipeline_params: PipelineParams) -> PipelineParams:
    return pipeline_params.with_nodes(
        {node: split_rendered_prompt(cfg)[1] for node, cfg in pipeline_params.laid_nodes.items()}
    )


class JobSearchPoint(FrozenModel):
    # Empty is the ONE spelling of "carries none": `content_hash` omits an empty dump.
    pipeline_params: PipelineParams = Field(default_factory=PipelineParams)
    # Per prompt node, each field as SCORED (framing spliced, shots as `few_shot_block`), in render order.
    prompt_fields: dict[str, dict[str, str]] = Field(default_factory=dict)

    @property
    def config_params(self) -> PipelineParams:
        return strip_rendered_prompt(self.pipeline_params)

    def sp_hash(self, pipeline_schema: PipelineSchema) -> str:
        """Over the SCHEMA-RESOLVED node configs alone: the same across subsets, as :meth:`content_hash` is not."""
        return pipeline_schema.sp_hash(self.pipeline_params)

    def content_hash(self, dataset: list[Any], *, candidate_library: Collection[str]) -> str:
        """Names a campaign's root (``runner/campaign_ids.py``); no measurement is filed under it."""
        return content_hash(
            dataset, self.pipeline_params.dump(), candidate_library=candidate_library
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
            f"model sees. Put the detail in task.md's body, which has no budget."
        )

    def __len__(self) -> int:
        return sum(1 for f in fields(self) if getattr(self, f.name))
