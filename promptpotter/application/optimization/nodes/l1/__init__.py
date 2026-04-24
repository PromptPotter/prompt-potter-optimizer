"""L1 round — the inner pipeline: generate candidates → score+select → critique results.

Either produces output (winner advances) or triggers escalation (L2/L3 mutate state,
then L1 runs again). L2 and L3 are escalation handlers, not peer layers.
"""

from promptpotter.application.optimization.nodes.l1.execute import (
    PauseForReviewError,
    execute_round,
)

__all__ = ["PauseForReviewError", "execute_round"]
