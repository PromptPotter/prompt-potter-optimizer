"""Canonical constants shared across models and services.

This is the single source of truth for prompt field lists, layer
mappings, and service-level constants.  All modules that need these
import from here.
"""

DATASET_NAME: str = "ground_truth"
NO_RESULT: str = "NO_RESULT"
DEFAULT_DIAGNOSTIC_QUERIES: int = 6

# search/smart_search — diagnostic set thresholds
MIN_DIAGNOSTIC_QUERIES: int = 3
DIAGNOSTIC_HIT_RATIO: float = 0.75
SCAN_TARGET_MDE: float = 0.15  # 15% minimum detectable effect for scan sizing

# stores/dataset_run_store — file lock parameters
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

# Persistence versioning
DATASET_RUNS_SCHEMA_VERSION = 1
DEFAULT_CONNECTOR_TYPE = "default"
