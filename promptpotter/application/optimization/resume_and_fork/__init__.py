"""Resume + fork-on-divergence machinery — one module, one public surface.

Bundles every piece of the ``--from N`` and ``--fork-on-divergence``
mechanism: the per-round resume-checkpoint records (``DecisionRecord`` /
``DecisionKind`` / ``DECISION_GATING``), the post-resume rescore-and-replay
walker (``replay_decisions``, ``REPLAYERS``), the divergence-or-fork
entry point (``resume_with_divergence_check``), and the sibling-mint
helpers (``fork_for_diag_sibling``, ``fork_for_sweep_sibling``).

When fork-on-divergence breaks, this is the one module to open. No
sidecar dispatch path; no replayer registered outside ``REPLAYERS``.
"""

from __future__ import annotations

from promptpotter.application.optimization.resume_and_fork.decisions import (
    DECISION_GATING,
    DecisionKind,
    DecisionRecord,
    GatingMode,
    record_decision,
)
from promptpotter.application.optimization.resume_and_fork.fork_siblings import (
    ForkResult,
    fork_at_divergence,
    fork_for_diag_sibling,
    fork_for_sweep_sibling,
)
from promptpotter.application.optimization.resume_and_fork.replayers import (
    REPLAYERS,
    Divergence,
    ReplayContext,
    Replayer,
    replay_decisions,
)
from promptpotter.application.optimization.resume_and_fork.resume import (
    resume_with_divergence_check,
)

__all__ = [
    "DECISION_GATING",
    "REPLAYERS",
    "DecisionKind",
    "DecisionRecord",
    "Divergence",
    "ForkResult",
    "GatingMode",
    "ReplayContext",
    "Replayer",
    "fork_at_divergence",
    "fork_for_diag_sibling",
    "fork_for_sweep_sibling",
    "record_decision",
    "replay_decisions",
    "resume_with_divergence_check",
]
