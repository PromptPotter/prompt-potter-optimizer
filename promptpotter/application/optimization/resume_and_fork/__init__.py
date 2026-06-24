"""Resume + fork-on-divergence machinery — one module, one public surface.

This file participates in the **central loop** as the resume + fork
machinery. Bundles every piece of the ``--from N`` and
``--fork-on-divergence`` mechanism: the per-round resume-checkpoint
records (``ResumeCheckpointRecord`` / ``ResumeCheckpointKind`` /
``RESUME_CHECKPOINT_GATING``), the post-resume rescore-and-replay
walker (``replay_decisions``, ``REPLAYERS``), the divergence-or-fork
entry point (``resume_with_divergence_check``), and the unified
fork-mint primitive (``_mint_fork``).

When fork-on-divergence breaks, this is the one module to open. No
sidecar dispatch path; no replayer registered outside ``REPLAYERS``;
no fork-mint path outside ``_mint_fork``.

CONCEPT MAP (by module):
* **decisions** (:mod:`.decisions`) — resume-checkpoint policy:
  ``RESUME_CHECKPOINT_GATING`` (the single ``REPLAYED`` vs ``ARCHIVAL``
  source of truth), ``ResumeCheckpointKind`` / ``ResumeCheckpointRecord``,
  ``record_decision``.
* **resume** (:mod:`.resume`) — :func:`resume_with_divergence_check`, the
  ``--from N`` / ``--fork-on-divergence`` entry point.
* **replayers** (:mod:`.replayers`) — ``replay_decisions`` + the
  ``REPLAYERS`` registry; re-derive each ``REPLAYED`` decision under the
  active scorer, returning the first :class:`Divergence`. Pure over
  :class:`ReplayContext`; MUST NOT touch the live ``Cycle`` or ledger.
* **fork_siblings** (:mod:`.fork_siblings`) — :func:`_mint_fork`, the unified
  fork-mint primitive dispatching on :class:`ForkTrigger` (``ForkResult``), plus
  :func:`mint_operator_fork`, the control-plane seam that builds a ``ForkSpec``
  and delegates to it.

Out-of-bounds: replayers MUST NOT touch the live ``Cycle`` or write
to the ledger; a new ``REPLAYED`` kind requires a registered
replayer or import-time fails (see :mod:`.replayers`). Fork
semantics are the data-vs-policy contract this module owns; new
fork triggers are :class:`ForkTrigger` enum additions, never
parallel mint paths.
"""

from __future__ import annotations

from promptpotter.application.optimization.resume_and_fork.ab_replay import (
    AbReport,
    ab_replay_cycle,
)
from promptpotter.application.optimization.resume_and_fork.decisions import (
    RESUME_CHECKPOINT_GATING,
    GatingMode,
    ResumeCheckpointKind,
    ResumeCheckpointRecord,
    record_decision,
)
from promptpotter.application.optimization.resume_and_fork.fork_siblings import (
    ForkResult,
    _mint_fork,
    mint_operator_fork,
)
from promptpotter.application.optimization.resume_and_fork.replayers import (
    REPLAYERS,
    Divergence,
    ReplayContext,
    Replayer,
    replay_all_divergences,
    replay_decisions,
)
from promptpotter.application.optimization.resume_and_fork.resume import (
    resume_with_divergence_check,
)

__all__ = [
    "REPLAYERS",
    "RESUME_CHECKPOINT_GATING",
    "AbReport",
    "Divergence",
    "ForkResult",
    "GatingMode",
    "ReplayContext",
    "Replayer",
    "ResumeCheckpointKind",
    "ResumeCheckpointRecord",
    "_mint_fork",
    "ab_replay_cycle",
    "mint_operator_fork",
    "record_decision",
    "replay_all_divergences",
    "replay_decisions",
    "resume_with_divergence_check",
]
