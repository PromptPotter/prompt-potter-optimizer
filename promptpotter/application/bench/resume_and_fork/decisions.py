"""``resume_checkpoint_gating`` is the sole source for ``REPLAYED`` vs ``ARCHIVAL``: the bench's kinds
here, each optimizer's through its runtime; every declared ``CheckpointKind`` must appear exactly
once or the registries fail to complete. **Adding a kind is two edits in one commit**, plus a
replayer if it is ``REPLAYED``."""

from __future__ import annotations

import enum
import functools
from collections.abc import Mapping
from typing import Any, Protocol

from promptpotter.application import optimizers
from promptpotter.domain.run_records import (
    BenchCheckpointKind,
    CheckpointKind,
    ResumeCheckpointRecord,
)

__all__ = [
    "BENCH_CHECKPOINT_GATING",
    "GatingMode",
    "ResumeCheckpointRecord",
    "record_decision",
    "resume_checkpoint_gating",
]


class GatingMode(enum.StrEnum):
    """Whether a decision kind drives resume-divergence checking. ``REPLAYED`` is re-derived under the active scorer and a
    mismatch halts or forks; ``ARCHIVAL`` is never compared."""

    REPLAYED = "replayed"
    ARCHIVAL = "archival"


# The bench's own kinds. ``REPLAYED`` kinds need a replayer (see :mod:`.replayers`); ``ARCHIVAL``
# kinds must NOT have one.
BENCH_CHECKPOINT_GATING: dict[CheckpointKind, GatingMode] = {
    # Panel coverage re-derives INVARIANTLY under everything replay varies, so replaying it
    # could only ever confirm itself. Replay re-runs the SCORER over stored rows, and
    # rescoring never turns an errored row into a measured one — the hole count is a fact
    # about which rows exist, not about how they score. Two further facts make it
    # unreachable as a REPLAYED kind even in principle: the gate halts before
    # ``persist_round``, so on the round that matters the record only ever reaches the
    # ledger and never ``round_data.decisions`` — the only thing ``replay_decisions``
    # walks. Registering a replayer here would install a guard that cannot fire. What
    # recovers a holed round is ``repair_incomplete_rounds``, which re-measures the cells
    # and then forces this walk so the kinds that CAN move are re-derived against the
    # repaired rows.
    BenchCheckpointKind.PANEL_COVERAGE: GatingMode.ARCHIVAL,
    # Fork is observable from the parent's history (the FORK_CUT record in
    # the parent ledger names the new cycle id and the offset that the
    # fork inherits from). It's archival because the fork's identity is
    # downstream of the divergence-checked decisions, not part of the
    # gating itself — replaying it can't re-derive a different fork.
    BenchCheckpointKind.FORK_CUT: GatingMode.ARCHIVAL,
}


@functools.cache
def resume_checkpoint_gating() -> Mapping[CheckpointKind, GatingMode]:
    """The bench's gating, then every optimizer runtime's for its own kinds. A declared kind nobody
    gates is a programming error, raised where the registries complete rather than at a first
    replay — every optimizer package is imported by then, so every enum is declared."""
    table: dict[CheckpointKind, GatingMode] = dict(BENCH_CHECKPOINT_GATING)
    for runtime in optimizers.runtimes().values():
        table.update(runtime.checkpoint_gating)
    declared = [kind for enum_ in CheckpointKind.__subclasses__() for kind in enum_]
    if unmapped := [k for k in declared if k not in table]:
        raise RuntimeError(f"Checkpoint kinds no gating table maps: {unmapped}")
    return table


class _DecisionSink(Protocol):
    """Anything with ``append(ResumeCheckpointRecord) -> Any`` — list[ResumeCheckpointRecord] or CycleEventLog."""

    def append(self, decision: ResumeCheckpointRecord, /) -> Any: ...


def record_decision(
    sink: _DecisionSink,
    kind: CheckpointKind,
    inputs_ref: dict[str, Any],
    outcome: Any,
    *,
    node: str | None,
    data: dict[str, Any] | None = None,
    round: int | None = None,
) -> Any:
    sink.append(
        ResumeCheckpointRecord(
            kind=kind,
            node=node,
            inputs_ref=dict(inputs_ref),
            outcome=outcome,
            data=dict(data or {}),
            round=round,
        )
    )
    return outcome
