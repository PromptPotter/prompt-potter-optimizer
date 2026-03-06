"""
PromptState — versioned, immutable snapshot of a prompt configuration.

Each optimization trial creates a new PromptState linked to its parent,
forming a lineage chain. The diff() function compares any two states.

Fields are organized into three optimization layers:
  Layer 1 (Generate)        — prompt components that vary every pass
  Layer 2 (Refine Context)  — context and parameters, adjusted when Layer 1 stalls
  Layer 3 (Modify Plan)     — optimization strategy, ideally left at defaults
"""
import difflib
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Layer field mapping
# ---------------------------------------------------------------------------

LAYER_FIELDS: dict[str, list[str]] = {
    "generate": [
        "persona",
        "task_intent",
        "problem_description",
        "instruction",
        "thinking_style",
        "answer_format",
        "few_shot_examples",
    ],
    "refine_context": ["context", "parameters"],
    "modify_plan": ["plan"],
}

# Layer 1 string fields (all generate fields except few_shot_examples).
# render() and diff() iterate this instead of hardcoded tuples.
LAYER1_STRING_FIELDS = [f for f in LAYER_FIELDS["generate"] if f != "few_shot_examples"]


# ---------------------------------------------------------------------------
# Supporting models
# ---------------------------------------------------------------------------

class FewShotExample(BaseModel):
    """An input/output pair used as a few-shot demonstration."""

    input: str
    output: str
    explanation: str | None = None


# ---------------------------------------------------------------------------
# PromptState
# ---------------------------------------------------------------------------

class PromptState(BaseModel):
    """Immutable snapshot of structured prompt components organized into
    three optimization layers, plus metadata for lineage tracking."""

    model_config = {"frozen": True}

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    parent_id: str | None = None
    changes_description: str | None = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # Layer 1: Generate (prompt components, vary every pass)
    persona: str = ""
    task_intent: str = ""
    problem_description: str = ""
    instruction: str = ""
    thinking_style: str = ""
    answer_format: str = ""
    few_shot_examples: list[FewShotExample] = Field(default_factory=list)

    # Layer 2: Refine Context (adjust when generation stalls)
    context: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)

    # Layer 3: Modify Plan (outermost, ideally at defaults)
    plan: str = ""

    def render(self) -> str:
        """Assemble Layer 1 string fields into a prompt string.

        Skips empty fields. Sections are separated by double newlines.
        Few-shot examples are formatted as Input/Output pairs.
        """
        parts: list[str] = []
        for field_name in LAYER1_STRING_FIELDS:
            value = getattr(self, field_name)
            if value:
                parts.append(value)

        if self.few_shot_examples:
            examples_lines: list[str] = []
            for ex in self.few_shot_examples:
                examples_lines.append(f"Input: {ex.input}\nOutput: {ex.output}")
                if ex.explanation:
                    examples_lines.append(f"Explanation: {ex.explanation}")
            parts.append("\n".join(examples_lines))

        return "\n\n".join(parts)

    def derive(self, **changes: Any) -> "PromptState":
        """Create a child state with modifications.

        Accepts keyword arguments for any PromptState field to override.
        Automatically sets parent_id and generates a new id/timestamp.
        """
        data = self.model_dump()
        data.pop("id")
        data.pop("created_at")
        data["parent_id"] = self.id
        data.update(changes)
        return PromptState(**data)


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

class FieldChange(BaseModel):
    """A single field-level change between two PromptState instances."""

    field: str
    layer: str
    old: Any = None
    new: Any = None


class PromptStateDiff(BaseModel):
    """Structured diff between two PromptState instances."""

    field_changes: list[FieldChange] = Field(default_factory=list)

    # Layer-level flags
    generate_changed: bool = False
    context_changed: bool = False
    plan_changed: bool = False

    # Rendered diff (comparing render() output)
    rendered_diff: str | None = None

    # Preserved detailed sub-diffs
    few_shot_added: list[FewShotExample] = Field(default_factory=list)
    few_shot_removed: list[FewShotExample] = Field(default_factory=list)
    parameters_added: dict[str, Any] = Field(default_factory=dict)
    parameters_removed: dict[str, str] = Field(default_factory=dict)
    parameters_changed: dict[str, dict[str, Any]] = Field(default_factory=dict)


def diff(a: PromptState, b: PromptState) -> PromptStateDiff:
    """Compare two PromptState snapshots and return a structured diff."""
    field_changes: list[FieldChange] = []

    # Compare Layer 1 string fields
    for field_name in LAYER1_STRING_FIELDS:
        old_val = getattr(a, field_name)
        new_val = getattr(b, field_name)
        if old_val != new_val:
            field_changes.append(FieldChange(
                field=field_name,
                layer="generate",
                old=old_val,
                new=new_val,
            ))

    # Compare Layer 2 context
    if a.context != b.context:
        field_changes.append(FieldChange(
            field="context",
            layer="refine_context",
            old=a.context,
            new=b.context,
        ))

    # Compare Layer 3 plan
    if a.plan != b.plan:
        field_changes.append(FieldChange(
            field="plan",
            layer="modify_plan",
            old=a.plan,
            new=b.plan,
        ))

    # Few-shot examples (dedicated fields only, no redundant FieldChange)
    a_examples = [ex.model_dump() for ex in a.few_shot_examples]
    b_examples = [ex.model_dump() for ex in b.few_shot_examples]
    fs_added = [FewShotExample(**ex) for ex in b_examples if ex not in a_examples]
    fs_removed = [FewShotExample(**ex) for ex in a_examples if ex not in b_examples]

    # Parameters (dedicated fields only, no redundant FieldChange)
    a_keys = set(a.parameters)
    b_keys = set(b.parameters)
    params_added = {k: b.parameters[k] for k in b_keys - a_keys}
    params_removed = {k: str(a.parameters[k]) for k in a_keys - b_keys}
    params_changed = {}
    for k in a_keys & b_keys:
        if a.parameters[k] != b.parameters[k]:
            params_changed[k] = {"old": a.parameters[k], "new": b.parameters[k]}

    # Layer flags — check both field_changes and dedicated sub-diffs
    generate_changed = (
        any(fc.layer == "generate" for fc in field_changes)
        or bool(fs_added) or bool(fs_removed)
    )
    context_changed = (
        any(fc.layer == "refine_context" for fc in field_changes)
        or bool(params_added) or bool(params_removed) or bool(params_changed)
    )
    plan_changed = any(fc.layer == "modify_plan" for fc in field_changes)

    # Rendered diff
    rendered_a = a.render()
    rendered_b = b.render()
    rendered_diff = None
    if rendered_a != rendered_b:
        rendered_diff = "\n".join(
            difflib.unified_diff(
                rendered_a.splitlines(),
                rendered_b.splitlines(),
                fromfile="a",
                tofile="b",
                lineterm="",
            )
        )

    return PromptStateDiff(
        field_changes=field_changes,
        generate_changed=generate_changed,
        context_changed=context_changed,
        plan_changed=plan_changed,
        rendered_diff=rendered_diff,
        few_shot_added=fs_added,
        few_shot_removed=fs_removed,
        parameters_added=params_added,
        parameters_removed=params_removed,
        parameters_changed=params_changed,
    )
