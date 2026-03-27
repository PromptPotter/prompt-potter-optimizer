"""Canonical field definitions shared across models and services.

This is the single source of truth for prompt field lists and layer
mappings.  All modules that need these lists import from here.
"""

# Fields that render() assembles into the prompt string.
PROMPT_STRING_FIELDS: list[str] = [
    "persona",
    "task_intent",
    "problem_description",
    "instruction",
    "thinking_style",
    "answer_format",
]

# Layer → field mapping for the optimization hierarchy.
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
    "refine_context": ["optimizer_params"],
    "modify_plan": ["plan"],
}

# Layer 1 string fields (all generate fields except few_shot_examples).
LAYER1_STRING_FIELDS = [f for f in LAYER_FIELDS["generate"] if f != "few_shot_examples"]
