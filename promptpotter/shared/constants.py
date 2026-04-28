"""Canonical constants shared across models and services.

This is the single source of truth for prompt field lists, layer
mappings, and service-level constants.  All modules that need these
import from here.
"""

DATASET_NAME: str = "ground_truth"
NO_RESULT: str = "NO_RESULT"

# stores/measurement_archive — file lock parameters
LOCK_TIMEOUT: float = 5.0  # seconds before treating lock as stale


# Fields that render() assembles into the prompt string.
PROMPT_STRING_FIELDS: list[str] = [
    "persona",
    "task_intent",
    "problem_description",
    "instruction",
    "thinking_style",
    "answer_format",
]

# task_context sub-fields that L1 may emit alongside prompt/node overrides.
TASK_CONTEXT_OVERRIDES: frozenset[str] = frozenset({"upstream_context", "downstream_context"})


def classify_axis(axis_key: str) -> str:
    """Classify a pipeline_params override key as 'prompt_field', 'task_context', or 'pipeline_param'."""
    if axis_key in PROMPT_STRING_FIELDS:
        return "prompt_field"
    if axis_key in TASK_CONTEXT_OVERRIDES:
        return "task_context"
    return "pipeline_param"


# Persistence versioning
MEASUREMENTS_SCHEMA_VERSION = 1
DEFAULT_CONNECTOR_TYPE = "default"
