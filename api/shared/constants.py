"""Canonical constants shared across models and services.

This is the single source of truth for prompt field lists, layer
mappings, and service-level constants.  All modules that need these
import from here.
"""

# ---------------------------------------------------------------------------
# Shared application constants
# ---------------------------------------------------------------------------

DATASET_NAME: str = "termnorm_ground_truth"
NO_RESULT: str = "NO_RESULT"
DISPLAY_TRUNCATE: int = 60
DEFAULT_DIAGNOSTIC_QUERIES: int = 6

# backend_client — HTTP timeout for /matches endpoint
MATCH_TIMEOUT: float = 120.0

# llm_client — provider defaults
OPENAI_MAX_RETRIES: int = 5
GROQ_MAX_RETRIES: int = 3
GROQ_TIMEOUT: float = 60.0
GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"

# llm_client — app-level retry (beyond SDK's own retry)
LLM_RETRY_STATUSES: frozenset[int] = frozenset({429, 502, 503})
LLM_MAX_APP_RETRIES: int = 3
LLM_BASE_DELAY: float = 1.0  # seconds

# pipeline_discovery — TTL for cached GET /pipeline responses
PIPELINE_CACHE_TTL: float = 30.0

# search/smart_search — diagnostic set thresholds
MIN_DIAGNOSTIC_QUERIES: int = 3
DIAGNOSTIC_HIT_RATIO: float = 0.75

# stores/dataset_run_store — file lock parameters
LOCK_RETRY_INTERVAL: float = 0.05  # seconds between retries
LOCK_TIMEOUT: float = 5.0  # seconds before treating lock as stale

# ---------------------------------------------------------------------------
# Prompt field definitions
# ---------------------------------------------------------------------------

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
DEFAULT_CONNECTOR_TYPE = "termnorm"
