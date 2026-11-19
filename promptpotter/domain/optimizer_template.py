import re
from collections.abc import Collection, Mapping
from typing import ClassVar, Self

from pydantic import create_model

from promptpotter.domain.strict_model import StrictModel

TEMPLATE_TOKEN_RE: re.Pattern[str] = re.compile(r"\{\{(\w+)\}\}")
PROMPT_FIELDS: tuple[str, ...] = (
    "persona",
    "task_intent",
    "problem_description",
    "instruction",
    "thinking_style",
    "answer_format",
)
"""The six fields a check-in decomposes a task into, in the order a prompt node declares them."""


class OptimizerPromptTemplate(StrictModel):
    """A prompt the optimizer RUNS ON (`dispatch/llm_call`), never one it produces."""

    RENDER_ORDER: ClassVar[tuple[str, ...]] = (
        *(f for f in PROMPT_FIELDS if f != "problem_description"),
        "problem_description",
    )
    """Prefix-cache order: a value that CHANGES between rounds goes in ``problem_description``, last."""

    persona: str = ""
    task_intent: str = ""
    problem_description: str = ""
    instruction: str = ""
    thinking_style: str = ""
    answer_format: str = ""

    def render(self) -> str:
        return "\n\n".join(self.prompt_fields().values())

    def slots(self) -> frozenset[str]:
        """Each ``{{slot}}`` its render names: the one parse of a template's tokens."""
        return frozenset(TEMPLATE_TOKEN_RE.findall(self.render()))

    def unknown_slots(self, known: Collection[str]) -> set[str]:
        """Its slots *known* does not fill; each reader refuses them in its own type."""
        return set(self.slots()) - set(known)

    def filled(self, values: Mapping[str, str | int]) -> Self:
        """One pass over the template's own text, so a value is data: a ``{{…}}`` inside one is never
        filled; a slot *values* does not name stays LITERAL, the backend's placeholder to fill."""

        def fill(match: re.Match[str]) -> str:
            return str(values[match[1]]) if match[1] in values else match[0]

        return self.model_copy(
            update={f: TEMPLATE_TOKEN_RE.sub(fill, v) for f, v in self.prompt_fields().items()}
        )

    def compile_prompt(self, **kwargs: str | int) -> str:
        return self.filled(kwargs).render()

    def prompt_fields(self) -> dict[str, str]:
        return {f: v for f in self.RENDER_ORDER if (v := getattr(self, f))}


# A field the order omits renders nowhere — silently, in a prompt.
assert tuple(OptimizerPromptTemplate.model_fields) == PROMPT_FIELDS


class OptimizerPromptEdits(
    create_model("_Edits", __base__=StrictModel, **dict.fromkeys(PROMPT_FIELDS, (str, None)))  # type: ignore[misc]
):
    """The fields a node sets over its template: an unsent field stays ``None``, as a sent null
    does (it clears in the fold before it reaches this, `layer_fold.py::_laid`), so its
    ``exclude_none`` dump is the edit."""
