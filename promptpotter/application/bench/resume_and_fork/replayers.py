"""Decision replayers — re-derive each ``REPLAYED`` decision under the active scorer. A replayer is
PURE over :class:`ReplayContext` and must never touch the live ``Cycle`` or ledger; each optimizer's
runtime declares its own kinds' replayers."""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable, Iterator, Mapping
from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter.application import optimizers
from promptpotter.application.bench.resume_and_fork.decisions import (
    GatingMode,
    resume_checkpoint_gating,
)
from promptpotter.domain.results import RoundResult

if TYPE_CHECKING:
    from promptpotter.domain.ruler import DeltaRuler

__all__ = [
    "ReplayContext",
    "ReplayMismatch",
    "replay_all_mismatches",
    "replay_decisions",
    "replayers",
]

logger = logging.getLogger(__name__)


class ReplayMismatch(NamedTuple):
    """A recorded value that no longer re-derives. Not "a decision" — three producers, none of them the
    loop. **Evidence, not a departure point**: where a branch stops carrying over is a *divergence*."""

    round_num: int
    kind: str
    recorded_outcome: Any
    current_outcome: Any
    inputs_ref: dict[str, Any]


class ReplayContext(NamedTuple):
    """One round's measurements, rescored — a replayer re-derives from its own round and nothing
    else, so a decision needing the cycle's HISTORY is ``ARCHIVAL``, never ``REPLAYED``.

    ``decisions`` arrives from the LEDGER (``scan_ledger_decisions``), which is where a decision is
    written and stamped with the round that made it — never a second copy off the round document,
    which is assembled from whatever was pending when it happened to be written.

    Deliberately carries NO comparison anchor: a caller-supplied one is a caller-private one, so
    the anchor rides the decision that used it."""

    round_data: RoundResult
    decisions: list[dict[str, Any]]
    ruler: DeltaRuler | None


Replayer = Callable[[ReplayContext, dict[str, Any], dict[str, Any]], Any]


def _iter_mismatches(ctx: ReplayContext) -> Iterator[ReplayMismatch]:
    round_data = ctx.round_data
    for rec in ctx.decisions:
        # An ARCHIVAL kind, or a kind no replayer knows, is recorded and never replayed.
        kind = str(rec["kind"])
        fn = replayers().get(kind)
        if fn is None:
            continue

        try:
            current = fn(ctx, rec["inputs_ref"], rec["data"])
        except Exception as exc:
            # A replayer that cannot ANSWER is a third state, never a match: they raise on a record
            # that does not re-derive (`RulerCoverageError` on an uncarried cell, a `ROUND_WINNER`
            # missing its `parent_cells` anchor), so counting that as agreement reports a clean pass
            # for exactly the rounds nothing verified and `--fork-on-divergence` never fires. Its own
            # kind, so the operator reads WHICH check went blind.
            # `.get` here only: the raise may BE the missing key, and an error path may not raise.
            logger.warning(
                "replayer for decision kind %r could not re-derive its record", kind, exc_info=True
            )
            yield ReplayMismatch(
                round_num=round_data.round,
                kind=f"replay_error:{kind}",
                recorded_outcome=rec.get("outcome"),
                current_outcome=f"{type(exc).__name__}: {exc}",
                inputs_ref=dict(rec.get("inputs_ref") or {}),
            )
            continue
        recorded = rec["outcome"]
        if current != recorded:
            yield ReplayMismatch(
                round_num=round_data.round,
                kind=kind,
                recorded_outcome=recorded,
                current_outcome=current,
                inputs_ref=dict(rec["inputs_ref"]),
            )


def _replay_context(
    round_data: RoundResult,
    decisions: list[dict[str, Any]] | None,
    ruler: DeltaRuler | None,
) -> ReplayContext:
    return ReplayContext(
        round_data=round_data,
        decisions=list(decisions or []),
        ruler=ruler,
    )


def replay_decisions(
    round_data: RoundResult,
    decisions: list[dict[str, Any]] | None = None,
    ruler: DeltaRuler | None = None,
) -> ReplayMismatch | None:
    """Walk this round's ledger decisions in order; return the FIRST mismatch (resume's halt seam)."""
    ctx = _replay_context(round_data, decisions, ruler)
    return next(_iter_mismatches(ctx), None)


def replay_all_mismatches(
    round_data: RoundResult,
    decisions: list[dict[str, Any]] | None = None,
    ruler: DeltaRuler | None = None,
) -> list[ReplayMismatch]:
    """Every decision in this round that re-derives differently — the A/B engine's per-round diff, where
    ``replay_decisions`` short-circuits at the first."""
    ctx = _replay_context(round_data, decisions, ruler)
    return list(_iter_mismatches(ctx))


@functools.cache
def replayers() -> Mapping[str, Replayer]:
    """Every optimizer runtime's replayers, which must cover exactly the REPLAYED kinds — both
    directions raise: a REPLAYED kind with none is silently never replayed, an ARCHIVAL kind with
    one re-derives what must never be. Completes with the registries."""
    table: dict[str, Replayer] = {}
    for runtime in optimizers.runtimes().values():
        table.update(runtime.replayers)
    gating = resume_checkpoint_gating()
    replayed = {str(k) for k, mode in gating.items() if mode is GatingMode.REPLAYED}
    if replayed != set(table):
        raise RuntimeError(
            f"REPLAYED kinds {sorted(replayed)} and registered replayers {sorted(table)} differ."
        )
    return table
