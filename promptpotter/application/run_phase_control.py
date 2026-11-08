from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from promptpotter.domain.phases import (
    REFUSAL_STOPS,
    GateDecision,
    PauseCause,
    StopReason,
)
from promptpotter.infrastructure.runtime_flags import requested_lookahead
from promptpotter.infrastructure.store.command_log import CommandLog, Controls

if TYPE_CHECKING:
    from promptpotter.domain.command_kinds import SubmitArmsPayload
    from promptpotter.domain.cycle_paths import Subject
    from promptpotter.infrastructure.llm.spend_book import SpendBook


@dataclass(frozen=True)
class Flight:
    """``waiting`` is the ``(position, launched_at)`` call a DECISION is held on under in-order absorption."""

    out: int = 0
    allowed: int = 0
    most: int = 0
    waiting: tuple[int, float] | None = None
    # ``None`` where no book bounds them; against a reserve it is the WORST case.
    affordable: int | None = None
    cell_usd: float | None = None


class FlightGauge:
    """Read at most once per ``every`` seconds: a full horizon is a bounds sweep, too dear per launch."""

    def __init__(self, emit: Callable[[Flight], None], *, every: float = 0.5) -> None:
        self._emit = emit
        self._every = every
        self._reader: Callable[[], Flight] | None = None
        self._published = Flight()
        self._due: asyncio.TimerHandle | None = None

    def open(self, reader: Callable[[], Flight]) -> None:
        if self._reader is not None:
            raise RuntimeError("a scoring phase is already publishing on this gauge")
        self._reader = reader
        self.touch()

    def close(self) -> None:
        self._reader = None
        self._publish()

    def touch(self) -> None:
        if self._due is None and self._reader is not None:
            self._due = asyncio.get_running_loop().call_later(self._every, self._publish)

    def _publish(self) -> None:
        if self._due is not None:
            self._due.cancel()
            self._due = None
        reading = self._reader() if self._reader is not None else Flight()
        if reading != self._published:
            self._published = reading
            self._emit(reading)


@dataclass(frozen=True)
class RunControl:
    """Every ``take_*`` / ``spend_*`` acks ``applied`` on the account's log, on *cycle*'s commands.
    Unarmed, it reads an empty inbox."""

    commands: CommandLog | None = None
    cycle: Subject | None = None
    # Outermost first. Their pause stops this run; their look-ahead never reaches it — inherited,
    # one arming would multiply concurrency at every nested level.
    enclosing: tuple[Subject, ...] = ()
    # Held, never spent, where no round spends a press (a screen, a bench pass); ``None`` reads the inbox.
    held_lookahead: int | None = None

    def __post_init__(self) -> None:
        if self.commands is None and (self.cycle is not None or self.enclosing):
            raise ValueError("a control over a cycle reads that cycle's commands off the log")

    def _log(self) -> CommandLog:
        assert self.commands is not None, "an armed control holds the log (`__post_init__`)"
        return self.commands

    def _inbox(self) -> Controls:
        return Controls() if self.cycle is None else self._log().book().controls(self.cycle)

    def _outer_paused(self) -> Subject | None:
        """The innermost enclosing run a pause, or a pass's cancel, stands on."""
        if not self.enclosing:
            return None
        book = self._log().book()
        return next((o for o in reversed(self.enclosing) if _asked(book.controls(o))), None)

    def pause_requested(self) -> bool:
        """A pause is a CLEAN EXIT: poll it only where work already done is on disk. A pass's
        ``cancel`` is its pause, read off the subject it is filed on."""
        if _asked(self._inbox()):
            return True
        return self._outer_paused() is not None

    def paused(self, stop: KeyboardInterrupt | asyncio.CancelledError) -> bool:
        """*stop* is a pause this control was asked, never a cancel of the task itself: the one stop
        a pass answers with the reading over what it banked."""
        task = asyncio.current_task()
        cancelled = (
            isinstance(stop, asyncio.CancelledError) and task is not None and task.cancelling()
        )
        return not cancelled and self.pause_requested()

    def take_pause(self) -> tuple[PauseCause, str] | None:
        """``None`` where nothing on a ledger asked: a signal or a cancellation, which the caller names."""
        inbox = self._inbox()
        if (asked := inbox.pause) is not None:
            self._log().ack(asked.command_id, "applied")
            return PauseCause.COMMAND, f"pause by {asked.issued_by.display}"
        # Its pass's own end drops it from the inbox: nothing to ack.
        if (stop := inbox.stop) is not None:
            return PauseCause.COMMAND, f"cancel by {stop.issued_by.display}"
        if (outer := self._outer_paused()) is not None:
            return PauseCause.ENCLOSING, f"enclosing run {outer.cycle_id} was paused"
        return None

    def skip_requested(self) -> bool:
        return bool(self._inbox().skips)

    def spend_skip(self) -> bool:
        skips = self._inbox().skips
        if not skips:
            return False
        self._log().ack(skips[0], "applied")
        return True

    def take_gate_decision(self) -> GateDecision | None:
        standing = self._inbox().gate_decisions
        if not standing:
            return None
        command_id, asked = standing[0]
        self._log().ack(command_id, "applied")
        return cast("GateDecision", asked)

    def standing_submission(self) -> tuple[str, SubmitArmsPayload] | None:
        """Read, not taken: the round's arms are banked before `take_submission` acks them."""
        standing = self._inbox().submissions
        return standing[0] if standing else None

    def take_submission(self, command_id: str) -> None:
        self._log().ack(command_id, "applied")

    def sample_lookahead(self) -> int:
        if self.held_lookahead is not None:
            return self.held_lookahead
        return requested_lookahead(self._inbox())

    def spend_sample_lookahead(self) -> None:
        """One ack per round removes a plain press and leaves an ``auto`` one standing."""
        if self.held_lookahead is not None:
            return
        asked = self._inbox().lookahead
        if asked is not None and not asked.taken:
            self._log().ack(asked.command_id, "applied")


def _asked(inbox: Controls) -> bool:
    return inbox.pause is not None or inbox.stop is not None


def budget_stop(book: SpendBook) -> StopReason | None:
    """Read between samples and at a round boundary; whether a call may be SENT is the book's."""
    refused = book.exhausted()
    return None if refused is None else REFUSAL_STOPS[refused]
