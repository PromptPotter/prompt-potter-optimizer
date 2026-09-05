from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import TYPE_CHECKING, Any

from pydantic import ConfigDict, Field, field_validator

from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.hashing import content_hash

if TYPE_CHECKING:
    from collections.abc import Mapping

    from promptpotter.domain.pipeline_schema import PipelineSchema


PARAM_FORBIDDEN_KEYS: frozenset[str] = frozenset({"model", "provider"})
"""Optimizer-forbidden ``pipeline_params[node]`` keys (operator-fixed via dataset overlay)."""


PARAM_SCOPE_KEYS: frozenset[str] = frozenset(
    {"temperature", "max_tokens", "reasoning_effort", "top_p"}
)
"""Per-node LLM-call tunable axes (non-prompt). Drives param-scope discipline + continuous_envelope."""


def strip_rendered_prompt(pipeline_params: dict[str, Any] | None) -> dict[str, Any]:
    """SOLE writer of the strip, so no surface invents a second rule for what counts as config.

    A node's ``prompt`` is the RENDER of ``prompt_fields``, never configuration — persist it and it
    is stale the moment the fields move, while every reader rebuilds it. `promptpotter-self` is not
    an exception: an optimizer node's evolved content rides the six ``PROMPT_STRING_FIELDS`` beside
    this key, which survive."""
    return {
        node: ({k: v for k, v in cfg.items() if k != "prompt"} if isinstance(cfg, dict) else cfg)
        for node, cfg in (pipeline_params or {}).items()
    }


class SearchPoint(StrictModel):
    def render(self) -> str:
        raise NotImplementedError


class JobSearchPoint(SearchPoint):
    model_config = ConfigDict(frozen=True)

    # Empty is the ONE spelling for "carries none" on both. No reader distinguishes absent from
    # empty, and `content_hash` omits a falsy `pipeline_params` either way — so the archive key is
    # unmoved — while the nullable twin reaches `CycleResult` / `export.py` as a crash.
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
        """``pipeline_params`` minus the per-node rendered prompt, which rides ``prompt_fields``
        and :meth:`render`. The observe view reads it verbatim."""
        return strip_rendered_prompt(self.pipeline_params)

    def sp_hash(self, pipeline_schema: PipelineSchema) -> str:
        """The SEARCHPOINT id — over the SCHEMA-RESOLVED node configs alone, so it is the same
        across subsets while :meth:`content_hash` is not. It is stored on every archive row as
        ``prompt_fields_id``, and is what the δ ruler keys its arms on
        (``intelligence/hard_sample_archive.py``); it is not the archive's RUN key.
        A ``None`` schema would hash the raw params under this same name — so it is required."""
        return pipeline_schema.sp_hash(self.pipeline_params)

    def content_hash(self, dataset: list[Any]) -> str:
        """The measurement-archive RUN key — rendered prompt + dataset + the overlay-MERGED
        ``pipeline_params``, so two points differing only by model share no measurements, and one
        prompt scored on N subsets is N runs."""
        return content_hash(
            self.render(),
            dataset,
            self.pipeline_params,
        )


# ---------------------------------------------------------------------------
# TaskDecomposition — structured domain context for optimizer LLM calls
# ---------------------------------------------------------------------------

# The FRAMING half: operator-authored, never measured (no candidate carries them), FROZEN for
# the run — `merge` refuses them and L2's schema has no field for them. Why, with the numbers:
# `application/optimization/CLAUDE.md` § The framing is frozen.
FRAMING_FIELDS: frozenset[str] = frozenset(
    {
        "domain",
        "pipeline_purpose",
        "data_characteristics",
        "optimization_goals",
        "key_challenges",
    }
)

# Per-field authoring budget, enforced ONCE at mint (`TaskDecomposition.check_budget`) and
# never at render. That is the whole point: a budget a renderer enforces is a budget the
# author never sees until their words are already gone, and the author here is a human who
# can simply edit the file. Sized off what real framing needs — the widest field authored
# across the shipped datasets is ~420 chars, and 600 leaves room to say something without
# inviting a page.
FRAMING_VALUE_BUDGET = 600

# ...and a TOTAL, because five legal fields are not a legal framing: the per-field budget
# alone permits 3000 chars, which renders VERBATIM into every optimizer prompt. It has held
# only because a human wrote the shipped ones (640 / 1003 / 1186); the check-in decomposition
# writes these five with an LLM, which is where a page arrives.
FRAMING_TOTAL_BUDGET = 1500


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

    @classmethod
    def coerce(cls, v: TaskDecomposition | dict[str, Any] | None) -> TaskDecomposition:
        """The one "already typed ⇒ passthrough, else build" admission — shared by the OSP field
        validator, the runner seam and the L2 verbatim check, so those three cannot drift."""
        if isinstance(v, TaskDecomposition):
            return v
        return cls.from_dict(v)

    def merge(self, overrides: dict[str, Any]) -> TaskDecomposition:
        """Applies ``overrides`` but REFUSES every framing field (frozen for the run), so a caller
        meaning to re-frame the task fails loud instead of paraphrasing curated knowledge."""
        if forbidden := sorted(FRAMING_FIELDS & overrides.keys()):
            raise ValueError(
                f"task_context framing is frozen for the run — refusing to overwrite "
                f"{forbidden}. These fields are operator-authored evidence about the task; "
                f"a round's findings belong in the critique / axis_memory / mutation_memory "
                f"channels, which are derived from measurement. Mutable here: "
                f"{sorted({f.name for f in fields(self)} - FRAMING_FIELDS)}."
            )
        base = self.to_dict()
        base.update(overrides)
        return self.from_dict(base)

    def check_budget(self, *, source: str) -> None:
        """Both bounds, because the per-field one cannot see the sum. Called once at the run-start
        seam, so an over-budget field stops the campaign instead of clipping every render."""
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


def has_framing(data: Mapping[str, object] | None) -> bool:
    """Whether a SERIALIZED framing record says anything — :meth:`TaskDecomposition.__bool__` for
    the dict form, which is what every store, resolver and preflight actually holds.

    ``to_dict`` is ``asdict``, so a decomposition that produced nothing still serializes eight keys
    and is truthy as a container. Testing the dict instead of its values lets that stand in for
    real framing — and an empty record at a higher store tier shadows the dataset's own
    ``task_context.yaml`` for good."""
    return data is not None and any(str(v).strip() for v in data.values())


__all__ = [
    "PARAM_FORBIDDEN_KEYS",
    "PARAM_SCOPE_KEYS",
    "JobSearchPoint",
    "SearchPoint",
    "TaskDecomposition",
    "has_framing",
    "strip_rendered_prompt",
]
