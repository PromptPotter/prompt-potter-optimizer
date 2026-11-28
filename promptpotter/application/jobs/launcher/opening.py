"""ONE run's opening, from its admission to its one release: every launch, arm, slotless run and
paid pass is one. It is entered once, where its job is created (`Opening.of`); from there its exit
rides the stack that spans its next stretch (`push`, `push_async_exit`), and `pop_all` hands it on,
so no start site spells a release."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import TracebackType
from typing import TYPE_CHECKING, Self

from promptpotter.application.jobs.registry import JobRegistry, launch_interrupted
from promptpotter.application.jobs.shares import hold_moved, release_run
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.job import Job, LaunchMove
from promptpotter.domain.phases import PauseCause, StopOutcome, StopReason, stop_reason_outcome
from promptpotter.domain.records.error import ErrorRecord
from promptpotter.domain.records.money import Hold
from promptpotter.domain.records.run_phase import RunPhaseRecord
from promptpotter.domain.spend import Budget, CeilingMeter, HeldBudget
from promptpotter.infrastructure.llm.spend_book import LiveCycle, SpendBook
from promptpotter.infrastructure.store.stores import Stores

if TYPE_CHECKING:
    from promptpotter.application.bench.resume_and_fork.fork_siblings import ForkCut


@dataclass(eq=False, slots=True)
class Opening:
    """A job's slot, from its admission to its one release. Its exit releases the job with what
    ended it: the raise, or nothing (a run that declared its own stop, a pass that returned), unless the run's
    own process took the job over (`hand_off`). Every hold the job holds closes with it: its hold on
    the account by a `Release`, filed first so a crash before the job's stamp leaves an unreleased
    job the reaper still ends; its claim of a cycle as it reads released. A raise stamps the cycle
    the job writes (`owns`), never an interrupt past the run's boundary (`running_on`), whose pause
    the runner declared; a fork's cut not yet taken is withdrawn, once the job no longer holds it."""

    stores: Stores
    job_registry: JobRegistry
    job: Job
    cut: ForkCut | None = None
    # What its admission shared it of its campaign's ceiling: its LAND files it on its cycle.
    share: Budget | None = None
    _stamps: CycleHop | None = None
    _running: bool = False
    _handed: bool = False
    _entered: bool = False

    @classmethod
    def of(cls, stores: Stores, job_registry: JobRegistry, job: Job) -> Opening:
        """*job*'s opening, entered: from here its exit is the job's one release."""
        return cls(stores, job_registry, job).__enter__()

    @property
    def job_id(self) -> str:
        return self.job.job_id

    def __enter__(self) -> Self:
        if self._entered:
            raise RuntimeError(f"job {self.job_id}'s opening is entered once")
        self._entered = True
        return self

    def __exit__(
        self,
        _type: type[BaseException] | None,
        exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        if self._handed:
            return
        held = self.job_registry.get(self.job_id)
        if held is not None and held.admitted and not held.released:
            release_run(self.stores, self.job_id)
        stop_reason = self.job_registry.release(self.job_id, exc)
        # An interrupt past the boundary is the pause flag's SYNTHETIC one, its pause declared.
        if (
            exc is not None
            and self._stamps is not None
            and not (self._running and launch_interrupted(exc))
        ):
            self._stamp(stop_reason or StopReason.NOT_ADMITTED, exc, self._stamps)
        if exc is not None and self.cut is not None:
            self.cut.withdraw(self.stores, self.job_registry, exc)

    async def __aenter__(self) -> Self:
        return self.__enter__()

    async def __aexit__(
        self,
        _type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Off the loop: the release waits on the admission gate."""
        await asyncio.to_thread(self.__exit__, _type, exc, tb)

    async def abandon(self, exc: BaseException) -> None:
        """Its exit on *exc*, where the act that admitted it raised before any run held it."""
        await self.__aexit__(type(exc), exc, exc.__traceback__)

    def owns(self, hop: CycleHop | None) -> None:
        """The cycle its job writes from here, which a raise stamps; ``None`` stamps none."""
        self._stamps = hop

    def land(self, hop: CycleHop) -> None:
        """Its job onto the cycle a mint wrote, once its ledger exists and before its campaign is
        listed, or onto a fork's cut, with the share its admission granted (`land_job`)."""
        if self.share is None:
            raise RuntimeError(f"job {self.job_id} lands before its admission shared it")
        self.owns(hop)
        land_job(self.stores, self.job_registry, self.job_id, hop, self.share)

    def land_cut(self, cut: ForkCut) -> None:
        """The job onto the child a fork's admission cut, which its exit withdraws until taken."""
        self.cut = cut
        self.land(cut.child)

    def take_cut(self) -> None:
        """The fork's act on its parent: from here its cut is the parent's line, never withdrawn."""
        if self.cut is None:
            raise RuntimeError(f"job {self.job_id} cut no fork to take")
        self.cut.take(self.stores)
        self.cut = None

    def running_on(self, hop: CycleHop) -> None:
        """Past the run's boundary on *hop*: the runner declares its own stops from here."""
        self.owns(hop)
        self._running = True

    def hand_off(self) -> None:
        """The run's own process holds the job: it releases it there."""
        self._handed = True

    def _stamp(self, stop_reason: StopReason, exc: BaseException, stamps: CycleHop) -> None:
        # Best-effort — it must never mask *exc*.
        try:  # noqa: SIM105
            self.stores.declare_stop(
                stamps,
                RunPhaseRecord.stop(
                    stop_reason,
                    cause=(
                        PauseCause.CANCELLED
                        if isinstance(exc, asyncio.CancelledError)
                        else PauseCause.INTERRUPT
                    ),
                    detail="the launch was interrupted before it held a run",
                ),
                error=(
                    None
                    if stop_reason_outcome(stop_reason) is StopOutcome.PAUSED
                    else ErrorRecord.raised(exc, kind="launch_failed", stop_reason=stop_reason)
                ),
            )
        except Exception:  # noqa: BLE001
            pass


def claim_cycle(stores: Stores, hop: CycleHop, job_id: str) -> None:
    """The job's hold on *hop*, on the cycle's ledger, filed where the job first names the cycle:
    it lives and dies with the job (`holders.py::holder_of`)."""
    stores.campaigns.append(hop, Hold.of_job(job_id, Budget(), parent=None))


def land_job(
    stores: Stores, job_registry: JobRegistry, job_id: str, hop: CycleHop, allotment: Budget
) -> None:
    """THE LAND of a launch onto a cycle it did not open on (a mint's, a fork's cut, a running one
    onto its fork): its job's hop, its share and its claim of the cycle in one write under the
    gate, so no sibling reads the job on its campaign unshared."""
    with job_registry.admission_gate():
        job_registry.move(job_id, LaunchMove.LAND, hop=hop)
        hold_moved(stores, hop, allotment)
        claim_cycle(stores, hop, job_id)


def open_spend_book(
    held: HeldBudget,
    *,
    stores: Stores,
    hop: CycleHop | None,
    meters: CeilingMeter,
    opened: float,
    live: bool,
) -> SpendBook:
    """On a cycle its share is read live off the cycle's record, which a reshare moves, and meters
    the cycle's ledger; off one, as admitted, metering its job's hold. Its reserve is its hold's on
    the account, which a set-limits restates. A *live* book reads the cycle's standing axes too."""
    ledger = None if hop is None else stores.campaigns.ledger(hop)
    return SpendBook(
        declared=held.admitted,
        share=held.share if hop is None else LiveCycle(stores.campaigns, hop),
        charges=stores.charges,
        hold_id=held.job_id,
        headroom=Budget(),
        metered_on=ledger,
        meters=meters,
        opened=opened,
        cycle=LiveCycle(stores.campaigns, hop) if live and hop is not None else None,
    )
