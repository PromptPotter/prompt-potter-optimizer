"""
Shared string constants used across services.

Centralises scattered literals so that each value is defined exactly once.
"""

from typing import Literal

# -- Dataset ------------------------------------------------------------------

DATASET_NAME: str = "termnorm_ground_truth"

# -- Evaluation ---------------------------------------------------------------

NO_RESULT: str = "NO_RESULT"

# -- Feedback cycle actions ---------------------------------------------------

NextAction = Literal["generate", "refine_context", "modify_plan", "stop"]
NEXT_ACTIONS: frozenset[str] = frozenset(
    {"generate", "refine_context", "modify_plan", "stop"}
)

# -- Smart search axis types -------------------------------------------------

AxisType = Literal["prompt_field", "pipeline_param"]
