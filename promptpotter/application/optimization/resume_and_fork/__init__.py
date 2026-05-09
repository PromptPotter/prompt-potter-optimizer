"""Resume + fork-on-divergence machinery — one module, one public surface.

This file participates in the **central loop** as the resume + fork
machinery. Bundles every piece of the ``--from N`` and
``--fork-on-divergence`` mechanism: the per-round resume-checkpoint
records (``ResumeCheckpointRecord`` / ``ResumeCheckpointKind`` /
``RESUME_CHECKPOINT_GATING``), the post-resume rescore-and-replay
walker (``replay_decisions``, ``REPLAYERS``), the divergence-or-fork
entry point (``resume_with_divergence_check``), and the sibling-mint
helpers (``fork_for_diag_sibling``, ``fork_for_sweep_sibling``).

When fork-on-divergence breaks, this is the one module to open. No
sidecar dispatch path; no replayer registered outside ``REPLAYERS``.

Out-of-bounds: replayers MUST NOT touch the live ``Cycle`` or write
to the ledger; a new ``REPLAYED`` kind requires a registered
replayer or import-time fails (see :mod:`.replayers`). Fork
semantics are the data-vs-policy contract this module owns; no
second fork-mint path elsewhere.
"""

from __future__ import annotations

from promptpotter.application.optimization.resume_and_fork.decisions import (
    RESUME_CHECKPOINT_GATING,
    GatingMode,
    ResumeCheckpointKind,
    ResumeCheckpointRecord,
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
    "REPLAYERS",
    "RESUME_CHECKPOINT_GATING",
    "Divergence",
    "ForkResult",
    "GatingMode",
    "ReplayContext",
    "Replayer",
    "ResumeCheckpointKind",
    "ResumeCheckpointRecord",
    "fork_at_divergence",
    "fork_for_diag_sibling",
    "fork_for_sweep_sibling",
    "record_decision",
    "replay_decisions",
    "resume_with_divergence_check",
]
