"""OptSearchPoint — the optimizer's full working state.

Inherits from ``SearchPoint`` (the shared base for all pipeline search
points). Where ``JobSearchPoint`` is a flat, frozen, content-hashable
target-layer specification (model + temperature + pipeline_params),
OptSearchPoint holds everything the optimizer needs: prompt decomposition
fields, L2/L3 state, and optimization memory.

The rendering bridge: ``render()`` assembles prompt fields into a string;
``to_job_search_point()`` projects into a JobSearchPoint for evaluation
by injecting the rendered prompt into ``pipeline_params``.

Two-layer tracing:
  Target layer:    JobSearchPoint  → content_hash → dataset_runs/
  Optimizer layer: OptSearchPoint  → model_dump() → campaigns/{id}/trial.json

ADR-8: Mutable (not frozen). Updated in-place during the feedback cycle,
serialized via ``model_dump()`` at checkpoint time.
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from api.models.search_point import SearchPoint
from api.shared.constants import PROMPT_STRING_FIELDS

if TYPE_CHECKING:
    from api.models.search_point import JobSearchPoint


# ---------------------------------------------------------------------------
# Supporting models
# ---------------------------------------------------------------------------

class FewShotExample(BaseModel):
    """An input/output pair used as a few-shot demonstration."""

    input: str
    output: str
    explanation: str | None = None


# ---------------------------------------------------------------------------
# OptSearchPoint
# ---------------------------------------------------------------------------

class OptSearchPoint(SearchPoint):
    """Optimizer-level search point — the optimizer's full working state.

    Persisted in trial checkpoints. Enables L4 to correlate optimizer
    configuration with target-pipeline evaluation outcomes.
    """

    # -- Lineage -------------------------------------------------------------
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    parent_id: str | None = None
    changes_description: str = ""
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

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
    degradation_reset_count: int = Field(
        0,
        description="How many times L2/L3 patience exhausted during degradation.",
    )
    backend_warning_emitted: bool = Field(
        False,
        description="One-shot flag — True after backend warning has been emitted.",
    )

    # -- SearchPoint interface ---------------------------------------------

    def render(self) -> str:
        """Assemble prompt decomposition fields into a prompt string.

        Skips empty fields. Sections separated by double newlines.
        Few-shot examples formatted as Input/Output pairs.
        """
        parts = [v for f in PROMPT_STRING_FIELDS if (v := getattr(self, f))]
        block = self._render_few_shot_block()
        if block:
            parts.append(block)
        return "\n\n".join(parts)

    # -- Rendering helpers -------------------------------------------------

    def _render_few_shot_block(self) -> str:
        """Format few-shot examples into a text block (empty string if none)."""
        if not self.few_shot_examples:
            return ""
        lines: list[str] = []
        for ex in self.few_shot_examples:
            lines.append(f"Input: {ex.input}\nOutput: {ex.output}")
            if ex.explanation:
                lines.append(f"Explanation: {ex.explanation}")
        return "\n".join(lines)

    def compile_prompt(self, **kwargs: str | int) -> str:
        """Render and substitute ``{{variable}}`` placeholders.

        Uses Langfuse-compatible double-brace syntax. Raises KeyError if
        template-defined variables remain unsubstituted.
        """
        text = self.render()
        expected = set(re.findall(r"\{\{(\w+)\}\}", text))
        for key, value in kwargs.items():
            text = text.replace("{{" + key + "}}", str(value))
        missing = expected - set(kwargs.keys())
        if missing:
            raise KeyError(f"Unsubstituted template variables: {sorted(missing)}")
        return text

    # -- Projection to target layer ----------------------------------------

    def to_job_search_point(
        self,
        base_pipeline_params: dict | None = None,
        *,
        prompt_node: str = "",
    ) -> JobSearchPoint:
        """Project into a JobSearchPoint for target-layer evaluation.

        Renders prompt fields → injects into pipeline_params → creates
        a frozen JobSearchPoint with ``prompt_fields`` populated for
        variant derivation.
        """
        from api.models.search_point import JobSearchPoint

        pp = dict(base_pipeline_params or {})
        rendered = self.render()
        if rendered and prompt_node:
            pp.setdefault(prompt_node, {})["prompt"] = rendered

        # Build prompt_fields for variant derivation in JobSearchPoint
        pf = {f: v for f, v in self.prompt_field_dict().items() if f != "few_shot_examples"}
        block = self._render_few_shot_block()
        if block:
            pf["few_shot_block"] = block

        return JobSearchPoint(
            pipeline_params=pp,
            prompt_fields=pf or None,
        )

    # -- Candidate derivation ------------------------------------------------

    def derive_candidate(self, **changes: Any) -> OptSearchPoint:
        """Create a child OptSearchPoint with prompt field modifications.

        Sets parent_id to this instance's id. Generates a new id/timestamp.
        Only copies prompt decomposition + L2/L3 state fields — does NOT
        copy optimization memory (critique, escalation_journal, etc.).
        """
        data: dict[str, Any] = {}
        # Copy prompt decomposition fields
        for f in PROMPT_STRING_FIELDS:
            data[f] = changes.pop(f, getattr(self, f))
        # few_shot_examples
        fse = changes.pop("few_shot_examples", None)
        if fse is not None:
            if fse and isinstance(fse[0], dict):
                fse = [FewShotExample(**ex) for ex in fse]
            data["few_shot_examples"] = fse
        else:
            data["few_shot_examples"] = [ex.model_copy() for ex in self.few_shot_examples]
        # L2/L3 state
        data["optimizer_params"] = changes.pop("optimizer_params", dict(self.optimizer_params))
        data["task_context"] = changes.pop("task_context", dict(self.task_context))
        data["plan"] = changes.pop("plan", self.plan)
        # Lineage
        data["parent_id"] = self.id
        data["changes_description"] = changes.pop("changes_description", "")
        # Any remaining changes
        data.update(changes)
        return OptSearchPoint(**data)

    # -- Field extraction --------------------------------------------------

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
        fields = dict(fields)  # don't mutate caller's dict
        fse = fields.pop("few_shot_examples", [])
        if fse and isinstance(fse[0], dict):
            fse = [FewShotExample(**ex) for ex in fse]
        return cls(few_shot_examples=fse, **fields, **kwargs)
