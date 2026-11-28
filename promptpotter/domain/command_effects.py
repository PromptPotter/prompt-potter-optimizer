"""What a command's ack says it did, beyond having happened: each effect, by kind."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.job import Job
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.worded import Worded


class NoEffect(FrozenModel):
    """An ack whose command names no effect beyond having happened."""

    kind: Literal["none"] = "none"


class StandingEffect(FrozenModel):
    """A loop command accepted for the hold it was admitted against: the job holding its cycle,
    ``None`` where the cycle lock alone holds it."""

    kind: Literal["standing"] = "standing"
    hold: str | None


class DraftOpenedEffect(FrozenModel):
    """A check-in draft opened by upload, dataset or origin: the draft a retry under its key answers."""

    kind: Literal["draft_opened"] = "draft_opened"
    draft_id: str


class LaunchedJob(FrozenModel):
    """What a launch answers of one job it launched: the job, the cycle it holds or will (``None``
    where a queued mint names none yet) and the words every surface names it by."""

    job_id: str
    hop: CycleHop | None
    what: str

    @classmethod
    def of(cls, job: Job, hop: CycleHop | None = None) -> LaunchedJob:
        """*hop* the cycle its hold took; unsaid, the one the job already knows."""
        hop = hop or job.hop
        return cls(job_id=job.job_id, hop=hop, what=replace(job, hop=hop).what)


class LaunchedEffect(FrozenModel):
    """A launch, of any kind: each job it launched, in launch order, which a retry under its key
    answers — after a job's record is reaped too."""

    kind: Literal["launched"] = "launched"
    launches: list[LaunchedJob]


class LaunchAnswer(FrozenModel):
    """What every door answers of a launch, a retry under its key included: the command it filed,
    which its task and its `cancel` name, and each job it launched, in launch order (a
    head-to-head's in its arms' order)."""

    command_id: str
    launches: list[LaunchedJob]


class VerifySaturated(FrozenModel):
    """A verify that bought nothing: every sample of the candidate's search split was already
    measured for its config: its answer, kept on its end as a graded one is."""

    kind: Literal["verify_saturated"] = "verify_saturated"
    dataset_name: str
    already_measured: int


VerifyWord = Literal["saturated"]


# What a `VerifySaturated` says, filled from its own fields: every surface reads it here.
VERIFY_WORDS: Worded[VerifyWord, str] = Worded(
    {
        "saturated": "Every sample in the {dataset_name} search split is already measured for "
        "this config ({already_measured} total). Nothing to add."
    }
)
