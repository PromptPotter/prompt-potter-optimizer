"""``JobRegistry`` — per-job audit, in-memory asyncio task tracking, and the machine's run-admission
gate. The jobs dir is machine-global and several processes attach to it at once, so both facts a
slot count rests on — who may admit, and whether a job's producer is alive — are carried across
processes by ``interlock.py`` rather than by anything this process happens to remember."""

from __future__ import annotations

import asyncio
import logging
import secrets
import threading
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from json import JSONDecodeError
from pathlib import Path
from typing import Literal, cast, get_args

from filelock import Timeout

from promptpotter.application.jobs.interlock import (
    admission_lock,
    producer_alive,
    this_producer,
)
from promptpotter.config.paths import user_data_root
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.infrastructure.store.io import read_json, write_json
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import ServiceUnavailableError

logger = logging.getLogger(__name__)

# A reservation admitted BEFORE the mint resolves its ids — the slot is held, the cycle it
# will name does not exist yet. `update_target` fills it in once the mint returns.
UNRESOLVED_HOP = CycleHop(campaign_id="", cycle_id="")

JobStatus = Literal["queued", "pending", "running", "completed", "failed", "stopped"]
_JOB_STATUSES: frozenset[str] = frozenset(get_args(JobStatus))
# The two that HOLD the machine slot — ``pending`` is the reserve→attach window, ``running`` the
# task itself. One name, because three sites spelled the pair: a fourth reader that forgot
# ``pending`` would hand the slot out twice across that window and see nothing wrong.
LIVE_JOB_STATUSES: frozenset[JobStatus] = frozenset({"pending", "running"})
# The three that still need a live PRODUCER — the two above plus a launch waiting in line. A
# queued job holds no slot and no wallet reservation, but it is somebody's pending intent, so an
# abandoned one must still be cleared or it sits in the queue forever, counted and undrainable.
UNFINISHED_JOB_STATUSES: frozenset[JobStatus] = LIVE_JOB_STATUSES | {"queued"}
# A typo here matches nothing and reads as "not live", so the slot is handed out twice or never
# released — no error either way. The Literal is the source; fail at import instead.
assert UNFINISHED_JOB_STATUSES <= _JOB_STATUSES, (
    f"job-status set names unknown JobStatuses: {sorted(UNFINISHED_JOB_STATUSES - _JOB_STATUSES)}"
)
# The load-bearing separation, and the plausible future edit that breaks it is "it's live, the
# user is waiting". A queued job admitted into LIVE would hold a machine slot AND be counted as an
# outstanding wallet reservation, so the box would fill with launches that are only waiting to run.
assert "queued" not in LIVE_JOB_STATUSES, "a queued launch holds no machine slot"


@dataclass(frozen=False)
class Job:
    """Persisted job record. Lives at ``jobs/{job_id}.json``."""

    job_id: str
    user_id: str
    campaign_id: str
    cycle_id: str
    dataset_name: str
    status: JobStatus
    created_at: str
    started_at: str | None
    finished_at: str | None
    stop_reason: str | None
    # The ceilings this run was admitted at — until it finishes they are the account's outstanding
    # reservation, which is what stops two concurrent launches sharing one remainder.
    cap_usd: float | None = None
    cap_tokens: int | None = None
    # Which process admitted this job (`interlock.this_producer`). It is what makes the slot
    # releasable by whoever notices: any process reading the dir can ask whether that one is still
    # alive, so a killed producer's slot comes back without waiting for the server that lost it.
    producer_id: str = ""

    @property
    def hop(self) -> CycleHop:
        """The cycle this job runs, as the pair that addresses it. Reads :data:`UNRESOLVED_HOP`
        between admission and mint — the slot is held before the cycle it will name exists."""
        return CycleHop(campaign_id=self.campaign_id, cycle_id=self.cycle_id)


def default_jobs_dir() -> Path:
    """Jobs dir beside `projects/` in the user-data tree."""
    return user_data_root() / "jobs"


class JobRegistry:
    """Process-wide job tracker + run-admission gate, thread-safe.

    ``capacity`` is asked, not stored: a callable taking how many runs are live and returning how
    many are admissible right now (``application/jobs/capacity.py``). It is resolved per admission
    rather than bound at startup so a box under provider back-pressure stops admitting without a
    restart. Deliberately no default — a registry that silently admitted one run would be a policy
    decision hidden in a signature.

    **Constructing one changes nothing on disk, and no process sweeps another's jobs.** A job is
    cleared by proving its producer gone (:meth:`_reap_if_orphaned`), never by one process deciding
    that everything it did not start must be stale — which is what a boot-time sweep amounts to
    once a terminal run holds a slot in the same dir."""

    def __init__(
        self,
        jobs_dir: Path,
        *,
        capacity: Callable[[int], int],
        on_reap: Callable[[Job], None] | None = None,
    ) -> None:
        self._dir = jobs_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        # Reentrant on purpose: `reserve` holds it across `list_running` →
        # `_reap_if_orphaned` → `mark_finished`, each of which takes it again.
        # A plain Lock deadlocks the event-loop thread on the SECOND launch.
        self._lock = threading.RLock()
        # Its cross-process peer, and ONE instance so nesting is reentrant the same way.
        self._gate = admission_lock(jobs_dir)
        self._capacity = capacity
        self._tasks: dict[str, asyncio.Task[None]] = {}
        # Fired whenever a job is proven dead so the same liveness owner can stamp the cycle
        # terminal — the second half of reconciling the two owners this class's docstring names.
        # Store-free: the wiring in main.py resolves the cycle and writes it.
        self._on_reap = on_reap

    def _fire_reap(self, job: Job) -> None:
        """Invoke the reap callback, never letting its failure break job tracking."""
        if self._on_reap is None:
            return
        try:
            self._on_reap(job)
        except Exception:
            logger.exception("on_reap callback failed for job %s", job.job_id)

    def create(
        self,
        *,
        user_id: str,
        hop: CycleHop,
        dataset_name: str,
        status: JobStatus = "pending",
    ) -> Job:
        job_id = secrets.token_urlsafe(12)
        now = utcnow_iso()
        job = Job(
            job_id=job_id,
            user_id=user_id,
            campaign_id=hop.campaign_id,
            cycle_id=hop.cycle_id,
            dataset_name=dataset_name,
            status=status,
            created_at=now,
            started_at=None,
            finished_at=None,
            stop_reason=None,
            # Asked for HERE rather than at construction: minting the token takes a lock held for
            # the process's life, and a registry that only reads occupancy is not a producer.
            producer_id=this_producer(self._dir),
        )
        self._persist(job)
        return job

    @contextmanager
    def admission_gate(self) -> Iterator[None]:
        """Hold the machine's admission window — every ceiling read that a reservation is then
        written against belongs inside one.

        Both the per-user ceiling and the machine's own are counted from the same on-disk jobs, so
        checking either outside the gate lets two admissions read one free slot and both take it.
        Reentrant, so :meth:`reserve` may be called alone or nested inside a caller that also gates
        its quota checks.

        Timing out means a process is wedged mid-admission: the window is a dir glob and one write,
        so nothing legitimate approaches ``LOCK_TIMEOUT``. There is no honest occupancy answer to
        give, and guessing would be the over-admission this gate exists to stop."""
        try:
            with self._lock, self._gate:
                yield
        except Timeout as exc:
            raise ServiceUnavailableError(
                "The machine's admission gate is held by another process that has not released "
                "it. Nothing was started; retry shortly.",
                code="admission_gate_stuck",
            ) from exc

    def request_slot(
        self,
        *,
        user_id: str,
        dataset_name: str,
        hop: CycleHop = UNRESOLVED_HOP,
    ) -> Job:
        """Take a machine slot, or join the queue for one — the answer rides the job's ``status``.

        A full box does not refuse. One admission act with two outcomes, so nothing downstream
        holds a second vocabulary for "busy", and the launch is a fact on disk either way: counted,
        positioned and cancellable from the moment the operator presses.

        The count read, the capacity resolution and the write happen under :meth:`admission_gate`
        with **no ``await`` between them**, which closes the race in this process and across
        processes."""
        with self.admission_gate():
            running = self.list_running()
            live = len(running)
            status: JobStatus = "pending" if live < self._capacity(live) else "queued"
            return self.create(
                user_id=user_id,
                hop=hop,
                dataset_name=dataset_name,
                status=status,
            )

    def queue_order(self) -> list[Job]:
        """The queue in the order it will drain: **least-served first**.

        The oldest waiting launch belonging to whoever has the FEWEST runs going, ties on age then
        job id. Derived fresh from what is already on disk rather than kept as a cursor, so it
        needs no reconciliation when a user stops running or a process dies, and every reader — the
        drain, the served position — sees one order.

        Starvation-free by arithmetic, not by promise: a user's own concurrency ceiling bounds how
        many entries they can hold, so a quiet account's launch overtakes a busy one's every time.
        Plain FIFO would let one account's burst push everyone else behind it."""
        held = Counter(j.user_id for j in self.list_running())
        return sorted(self.list_queued(), key=lambda j: (held[j.user_id], j.created_at, j.job_id))

    def claim_next(self, job_id: str) -> bool:
        """Promote *job_id* to ``pending`` iff a slot is free and it is first in
        :meth:`queue_order`. Whoever is waiting on a queued job asks this; the answer is the same
        for all of them because the order is derived, so no two claimants can both be next.

        Deliberately NOT "claim whatever is drainable by me": a waiter that skipped past the head
        of the line because it could not run that one would starve it, and the head is exactly the
        launch fairness just decided is owed a slot."""
        with self.admission_gate():
            running = self.list_running()
            live = len(running)
            if live >= self._capacity(live):
                return False
            first = next(iter(self.queue_order()), None)
            if first is None or first.job_id != job_id:
                return False
            first.status = "pending"
            self._persist(first)
            return True

    def cancel_queued(self, job_id: str, *, user_id: str) -> bool:
        """Withdraw a launch that has not started. **Owner-only, and enforced here** rather than at
        one route: a queue every entry point can join is one every entry point can leave, and the
        capability that let someone queue says nothing about whose launch this is."""
        with self.admission_gate():
            job = self.get(job_id)
            if job is None or job.status != "queued" or job.user_id != user_id:
                return False
            self.mark_finished(job_id, status="stopped", stop_reason="queue_cancelled")
            return True

    def holder(self) -> Job | None:
        """The oldest live run — who the box is busy WITH. One definition, because the banner, the
        terminal's refusal and the queue readout all name it and disagreeing would be worse than
        any of them being silent."""
        running = self.list_running()
        return min(running, key=lambda j: j.created_at) if running else None

    def set_caps(self, job_id: str, *, cap_usd: float | None, cap_tokens: int | None) -> None:
        """Stamp what the launch was admitted at, once composed — the reservation the NEXT admission
        counts against, so it must land before the run reaches an LLM."""
        job = self.get(job_id)
        if job is None:
            return
        job.cap_usd = cap_usd
        job.cap_tokens = cap_tokens
        self._persist(job)

    def update_target(self, job_id: str, *, hop: CycleHop) -> None:
        """Bind a reservation admitted at :data:`UNRESOLVED_HOP` to the cycle the mint resolved.
        Until it lands the job answers no hop-keyed query, so a held cap is unreachable."""
        job = self.get(job_id)
        if job is None:
            return
        job.campaign_id = hop.campaign_id
        job.cycle_id = hop.cycle_id
        self._persist(job)

    def attach_task(self, job_id: str, task: asyncio.Task[None]) -> None:
        with self._lock:
            self._tasks[job_id] = task

    def mark_started(self, job_id: str) -> None:
        job = self.get(job_id)
        if job is None:
            return
        job.status = "running"
        job.started_at = utcnow_iso()
        self._persist(job)

    def mark_finished(
        self, job_id: str, *, status: JobStatus, stop_reason: str | None = None
    ) -> None:
        job = self.get(job_id)
        if job is None:
            return
        job.status = status
        job.finished_at = utcnow_iso()
        job.stop_reason = stop_reason
        self._persist(job)
        with self._lock:
            self._tasks.pop(job_id, None)

    def get(self, job_id: str) -> Job | None:
        path = self._path(job_id)
        if not path.is_file():
            return None
        try:
            raw = read_json(path)
        except (OSError, JSONDecodeError):
            logger.warning("job %s file unreadable", job_id)
            return None
        return self._job_from_dict(raw)

    def list_all(self, *, user_id: str | None = None) -> list[Job]:
        out: list[Job] = []
        for path in self._dir.glob("*.json"):
            try:
                raw = read_json(path)
            except (OSError, JSONDecodeError):
                continue
            job = self._job_from_dict(raw)
            if user_id is not None and job.user_id != user_id:
                continue
            out.append(job)
        out.sort(key=lambda j: j.created_at, reverse=True)
        return out

    def _reap_if_orphaned(self, job: Job) -> Job:
        """Self-heal a live job whose producer is gone — a zombie holding a machine slot.

        Two facts, and conflating them is catastrophic. ``self._tasks`` answers only for jobs THIS
        process started, and the jobs dir is machine-global: read as "no task, therefore dead", a
        terminal verb attaching to the dir would stamp the server's live campaign ``stopped`` and
        reap its cycle merely by counting slots. So an unattached job is judged by the OS lock its
        producer holds for its own lifetime, which answers for a process this one never started and
        goes false the moment that process dies — including by ``SIGKILL``.

        It covers ``pending`` and ``queued`` as well as ``running``, and only a durable producer
        fact can: neither has a task or a cycle to judge. Without it a launch killed inside the
        reserve→attach window holds its slot until the owning server restarts, and an abandoned
        queued one sits in the order forever — counted, first in line, drainable by nobody, since
        the launch intent lives in the process that took the request."""
        if job.status not in UNFINISHED_JOB_STATUSES:
            return job
        with self._lock:
            task = self._tasks.get(job.job_id)
        if task is not None:
            if not task.done():
                return job
        elif producer_alive(self._dir, job.producer_id):
            return job
        logger.warning(
            "job %s claims %s but its producer is gone — reaping", job.job_id, job.status
        )
        self.mark_finished(job.job_id, status="stopped", stop_reason="producer_vanished")
        self._fire_reap(job)
        return self.get(job.job_id) or job

    def list_running(self, *, user_id: str | None = None) -> list[Job]:
        """The live jobs — a reconciling READ, deliberately: a job whose producer is gone is stamped
        ``stopped`` here and its cycle reaped, so no caller is answered a zombie. The write is
        bounded to once per zombie (the next call filters it out above the check). This is the ONLY
        place a job is cleared, boot included — a torn task and a dead process are both visible from
        here, and nothing else has to decide which jobs were "ours"."""
        return self._reconciled(LIVE_JOB_STATUSES, user_id=user_id)

    def list_queued(self, *, user_id: str | None = None) -> list[Job]:
        """The launches waiting for a slot, oldest first — the same reconciling read as
        :meth:`list_running`, over the status that holds no slot. Age order, not drain order:
        :meth:`queue_order` owns which one goes next."""
        out = self._reconciled(frozenset({"queued"}), user_id=user_id)
        out.sort(key=lambda j: (j.created_at, j.job_id))
        return out

    def _reconciled(self, statuses: frozenset[JobStatus], *, user_id: str | None) -> list[Job]:
        """Jobs in *statuses*, each proven to still have a producer. One walk, because a job that
        survives the check in one caller's read and not another's is a slot count that disagrees
        with the queue beside it."""
        out: list[Job] = []
        for j in self.list_all(user_id=user_id):
            if j.status not in statuses:
                continue
            j = self._reap_if_orphaned(j)
            if j.status in statuses:
                out.append(j)
        return out

    def running_job_for(self, hop: CycleHop) -> Job | None:
        """The in-flight job running this cycle — how a mid-flight ceiling change reaches the
        reservation that job holds, which would otherwise keep quoting the launch's number."""
        return next((j for j in self.list_running() if j.hop == hop), None)

    def list_created_today(self, *, user_id: str | None = None) -> list[Job]:
        """Daily-campaigns quota probe. The prefix is SLICED off ``utcnow_iso`` rather than composed,
        because ``created_at`` was minted by it and a second spelling of "today" once disagreed."""
        today = utcnow_iso()[:10]
        return [j for j in self.list_all(user_id=user_id) if j.created_at.startswith(today)]

    def _persist(self, job: Job) -> None:
        write_json(self._path(job.job_id), asdict(job))

    def _path(self, job_id: str) -> Path:
        if not job_id or "/" in job_id or "\\" in job_id or job_id.startswith("."):
            raise ValueError(f"invalid job_id: {job_id!r}")
        return self._dir / f"{job_id}.json"

    @staticmethod
    def _job_from_dict(raw: dict[str, object]) -> Job:
        return Job(
            job_id=str(raw.get("job_id", "")),
            user_id=str(raw.get("user_id", "")),
            campaign_id=str(raw.get("campaign_id", "")),
            cycle_id=str(raw.get("cycle_id", "")),
            dataset_name=str(raw.get("dataset_name", "")),
            status=_coerce_status(raw.get("status")),
            created_at=str(raw.get("created_at", "")),
            started_at=_optional_str(raw.get("started_at")),
            finished_at=_optional_str(raw.get("finished_at")),
            stop_reason=_optional_str(raw.get("stop_reason")),
            cap_usd=_optional_float(raw.get("cap_usd")),
            cap_tokens=_optional_int(raw.get("cap_tokens")),
            producer_id=str(raw.get("producer_id", "")),
        )


def _coerce_status(raw: object) -> JobStatus:
    """A status that did not survive its file reads ``stopped`` — an unreadable job is not one to
    resume. Membership derives from the Literal, so a new status needs no arm here."""
    if isinstance(raw, str) and raw in _JOB_STATUSES:
        return cast(JobStatus, raw)
    return "stopped"


def _optional_str(raw: object) -> str | None:
    if raw is None:
        return None
    return str(raw)


def _optional_float(raw: object) -> float | None:
    return float(raw) if isinstance(raw, int | float) and not isinstance(raw, bool) else None


def _optional_int(raw: object) -> int | None:
    return int(raw) if isinstance(raw, int | float) and not isinstance(raw, bool) else None


__all__ = [
    "LIVE_JOB_STATUSES",
    "UNRESOLVED_HOP",
    "Job",
    "JobRegistry",
    "JobStatus",
    "default_jobs_dir",
]
