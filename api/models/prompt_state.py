"""
PromptState — versioned, immutable snapshot of a prompt configuration.

Each optimization trial creates a new PromptState linked to its parent,
forming a lineage chain. The diff() function compares any two states.
"""
import difflib
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class FewShotExample(BaseModel):
    """An input/output pair used as a few-shot demonstration."""

    input: str
    output: str
    explanation: Optional[str] = None


class PromptState(BaseModel):
    """Immutable snapshot of prompt text + few-shot examples + parameters."""

    model_config = {"frozen": True}

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    prompt_text: str
    few_shot_examples: List[FewShotExample] = Field(default_factory=list)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    parent_id: Optional[str] = None
    changes_description: Optional[str] = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

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


class PromptStateDiff(BaseModel):
    """Structured diff between two PromptState instances."""

    prompt_text_changed: bool
    prompt_text_diff: Optional[str] = None
    few_shot_added: List[FewShotExample] = Field(default_factory=list)
    few_shot_removed: List[FewShotExample] = Field(default_factory=list)
    parameters_added: Dict[str, Any] = Field(default_factory=dict)
    parameters_removed: Dict[str, str] = Field(default_factory=dict)
    parameters_changed: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


def diff(a: PromptState, b: PromptState) -> PromptStateDiff:
    """Compare two PromptState snapshots and return a structured diff."""
    # Prompt text
    text_changed = a.prompt_text != b.prompt_text
    text_diff = None
    if text_changed:
        text_diff = "\n".join(
            difflib.unified_diff(
                a.prompt_text.splitlines(),
                b.prompt_text.splitlines(),
                fromfile="a",
                tofile="b",
                lineterm="",
            )
        )

    # Few-shot examples
    a_examples = [ex.model_dump() for ex in a.few_shot_examples]
    b_examples = [ex.model_dump() for ex in b.few_shot_examples]
    added = [FewShotExample(**ex) for ex in b_examples if ex not in a_examples]
    removed = [FewShotExample(**ex) for ex in a_examples if ex not in b_examples]

    # Parameters
    a_keys = set(a.parameters)
    b_keys = set(b.parameters)
    params_added = {k: b.parameters[k] for k in b_keys - a_keys}
    params_removed = {k: str(a.parameters[k]) for k in a_keys - b_keys}
    params_changed = {}
    for k in a_keys & b_keys:
        if a.parameters[k] != b.parameters[k]:
            params_changed[k] = {"old": a.parameters[k], "new": b.parameters[k]}

    return PromptStateDiff(
        prompt_text_changed=text_changed,
        prompt_text_diff=text_diff,
        few_shot_added=added,
        few_shot_removed=removed,
        parameters_added=params_added,
        parameters_removed=params_removed,
        parameters_changed=params_changed,
    )
