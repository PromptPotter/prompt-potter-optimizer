"""OptSearchPoint — the optimizer's full working state.

The more capable twin of ``SearchPoint``. Where SearchPoint is a flat,
frozen, content-hashable target-layer specification (model + temperature +
pipeline_params), OptSearchPoint holds everything the optimizer needs:
prompt decomposition fields, L2/L3 state, and optimization memory.

The rendering bridge: ``render_prompt()`` assembles prompt fields into a
string; ``to_search_point()`` projects into a SearchPoint for evaluation
by injecting the rendered prompt into ``pipeline_params``.

Two-layer tracing:
  Target layer:    SearchPoint  → content_hash → dataset_runs/
  Optimizer layer: OptSearchPoint → model_dump() → campaigns/{id}/trial.json

L4 meta-optimization searches over OptSearchPoints, just as L1-L3 produce
SearchPoints for evaluation.

ADR-8: Mutable (not frozen). Updated in-place during the feedback cycle,
serialized via ``model_dump()`` at checkpoint time.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.models.search_point import SearchPoint

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Supporting models
# ---------------------------------------------------------------------------

class FewShotExample(BaseModel):
    """An input/output pair used as a few-shot demonstration."""

    input: str
    output: str
    explanation: str | None = None


# Fields that render_prompt() assembles into the prompt string.
PROMPT_STRING_FIELDS: list[str] = [
    "persona",
    "task_intent",
    "problem_description",
    "instruction",
    "thinking_style",
    "answer_format",
]


# ---------------------------------------------------------------------------
# OptSearchPoint
# ---------------------------------------------------------------------------

class OptSearchPoint(BaseModel):
    """Optimizer-level search point — the optimizer's full working state.

    Persisted in trial checkpoints. Enables L4 to correlate optimizer
    configuration with target-pipeline evaluation outcomes.
    """

    # -- Prompt decomposition (L1 working representation) ------------------
    persona: str = ""
    task_intent: str = ""
    problem_description: str = ""
    instruction: str = ""
    thinking_style: str = ""
    answer_format: str = ""
    few_shot_examples: list[FewShotExample] = Field(default_factory=list)

    # -- L2 state ----------------------------------------------------------
    optimizer_params: dict[str, Any] = Field(default_factory=dict)
    task_context: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured domain context (domain, pipeline_purpose, "
        "data_characteristics, optimization_goals, key_challenges). "
        "Set from TASK_DESCRIPTION decomposition, refinable by L2.",
    )

    # -- L3 state ----------------------------------------------------------
    plan: str = ""

    # -- Optimization memory -----------------------------------------------
    critique_text: str = ""
    critique: dict[str, Any] = Field(
        default_factory=dict,
        description="Full 5-field critique dict (positive_critique, negative_critique, "
        "priority_fix, suggested_axes, summary).",
    )
    thinking_styles: list[str] = Field(default_factory=list)
    escalation_journal: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Cross-round degradation investigation memory.",
    )
    warning_inventory: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Per-query warning inventory across rounds.",
    )
    l2_directive: str = Field(
        default="",
        description="L2's diagnostic reasoning + action guidance for L1.",
    )
    content_hashes: list[str] = Field(
        default_factory=list,
        description="Content hashes of dataset_runs produced under this config.",
    )
    degradation_reset_count: int = Field(
        0,
        description="How many times L2/L3 patience exhausted during degradation.",
    )
    backend_warning_emitted: bool = Field(
        False,
        description="One-shot flag — True after backend warning has been emitted.",
    )

    # -- Rendering ---------------------------------------------------------

    def render_prompt(self) -> str:
        """Assemble prompt decomposition fields into a prompt string.

        Skips empty fields. Sections separated by double newlines.
        Few-shot examples formatted as Input/Output pairs.
        """
        parts: list[str] = []
        for field_name in PROMPT_STRING_FIELDS:
            value = getattr(self, field_name)
            if value:
                parts.append(value)

        if self.few_shot_examples:
            lines: list[str] = []
            for ex in self.few_shot_examples:
                lines.append(f"Input: {ex.input}\nOutput: {ex.output}")
                if ex.explanation:
                    lines.append(f"Explanation: {ex.explanation}")
            parts.append("\n".join(lines))

        return "\n\n".join(parts)

    def compile_prompt(self, **kwargs: str) -> str:
        """Render and substitute ``{{variable}}`` placeholders.

        Uses Langfuse-compatible double-brace syntax. Raises KeyError if
        template-defined variables remain unsubstituted.
        """
        text = self.render_prompt()
        expected = set(re.findall(r"\{\{(\w+)\}\}", text))
        for key, value in kwargs.items():
            text = text.replace("{{" + key + "}}", str(value))
        missing = expected - set(kwargs.keys())
        if missing:
            raise KeyError(f"Unsubstituted template variables: {sorted(missing)}")
        return text

    def to_search_point(
        self,
        model: str = "",
        temperature: float = 0.0,
        base_pipeline_params: dict | None = None,
        *,
        prompt_node: str = "llm_ranking",
    ) -> "SearchPoint":
        """Project into a SearchPoint for target-layer evaluation.

        Renders prompt fields → injects into pipeline_params → creates
        a frozen SearchPoint. The ``prompt_node`` param controls which
        node receives the rendered prompt (default: llm_ranking).
        """
        from api.models.search_point import SearchPoint

        pp = dict(base_pipeline_params or {})
        rendered = self.render_prompt()
        if rendered:
            pp.setdefault(prompt_node, {})["prompt"] = rendered
        return SearchPoint(
            model=model,
            temperature=temperature,
            pipeline_params=pp,
        )

    def prompt_field_dict(self) -> dict[str, Any]:
        """Return prompt decomposition fields as a dict (for L1 candidate generation)."""
        d: dict[str, Any] = {}
        for f in PROMPT_STRING_FIELDS:
            v = getattr(self, f)
            if v:
                d[f] = v
        if self.few_shot_examples:
            d["few_shot_examples"] = [ex.model_dump() for ex in self.few_shot_examples]
        return d

    @classmethod
    def from_prompt_fields(cls, fields: dict[str, Any], **kwargs: Any) -> OptSearchPoint:
        """Create an OptSearchPoint from a dict of prompt fields + optional extra state."""
        fse = fields.pop("few_shot_examples", [])
        if fse and isinstance(fse[0], dict):
            fse = [FewShotExample(**ex) for ex in fse]
        return cls(few_shot_examples=fse, **fields, **kwargs)
