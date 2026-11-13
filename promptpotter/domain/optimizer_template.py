import re
from collections.abc import Collection
from typing import Annotated, ClassVar

from pydantic import create_model

from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.hashing import shapes_optimizer_prompt

TEMPLATE_TOKEN_RE: Annotated[re.Pattern[str], shapes_optimizer_prompt] = re.compile(
    r"\{\{(\w+)\}\}"
)
PROMPT_FIELDS: Annotated[tuple[str, ...], shapes_optimizer_prompt] = (
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

    RENDER_ORDER: ClassVar[Annotated[tuple[str, ...], shapes_optimizer_prompt]] = (
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

    @shapes_optimizer_prompt
    def render(self) -> str:
        return "\n\n".join(self.prompt_fields().values())

    @shapes_optimizer_prompt
    def slots(self) -> frozenset[str]:
        """Each ``{{slot}}`` its render names: the one parse of a template's tokens."""
        return frozenset(TEMPLATE_TOKEN_RE.findall(self.render()))

    def unknown_slots(self, known: Collection[str]) -> set[str]:
        """Its slots *known* does not fill; each reader refuses them in its own type."""
        return set(self.slots()) - set(known)

    @shapes_optimizer_prompt
    def compile_prompt(self, **kwargs: str | int) -> str:
        """Any ``{{…}}`` left after substitution stays LITERAL: it is the backend's placeholder to fill."""
        text = self.render()
        for key, value in kwargs.items():
            text = text.replace("{{" + key + "}}", str(value))
        return text

    @shapes_optimizer_prompt
    def prompt_fields(self) -> dict[str, str]:
        return {f: v for f in self.RENDER_ORDER if (v := getattr(self, f))}


# A field the order omits renders nowhere — silently, in a prompt.
assert tuple(OptimizerPromptTemplate.model_fields) == PROMPT_FIELDS


@shapes_optimizer_prompt
class OptimizerPromptEdits(
    create_model("_Edits", __base__=StrictModel, **dict.fromkeys(PROMPT_FIELDS, (str, None)))  # type: ignore[misc]
):
    """The fields a node sets over its template: an unsent field stays ``None`` and a sent null
    refuses (a patch's null clears before the fold reaches this, `layer_fold.py::wire_patch`), so
    its ``exclude_none`` dump is the edit."""
