from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self, TypedDict, get_args

from pydantic import ConfigDict, Field, model_validator

from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.paired_reading import ArmKey, ArmName
from promptpotter.domain.scoring import GradedCell, is_answer_collapsed
from promptpotter.domain.search_point import JobSearchPoint, TaskDecomposition
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.domain.wounds import (
    RuntimeFailure,
    ValidationFailure,
    fatal_validation_failures,
)
from promptpotter.shared.errors import ConflictError
from promptpotter.shared.hashing import shapes_optimizer_prompt, stable_hash
from promptpotter.shared.measurement_context import MeasurementRole

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.sample import Sample


@shapes_optimizer_prompt
def candidate_label(round_num: int, idx: int) -> str:
    if round_num == 0:
        return "C0"
    return f"C{round_num}.{idx + 1}"


#: Why an arm stopped short of its panel AGAINST it: refused before a cell, by the operator, by the
#: bench's run-health rules, or by the eliminator (`Eliminator.stop_disqualifies` says if that rejects).
CutKind = Literal["invalid", "skipped", "broken", "eliminated"]
#: How an arm's walk ended short of its panel: cut against it, or locked in for it.
ArmEnd = Literal[CutKind, "locked_in"]
#: Where an arm's walk stands: each ``ArmState`` member's discriminator.
ArmStateKind = Literal["proposed", "measuring", "cut", "measured"]
#: What an arm IS in its round: the origin, a proposal, or a reference re-read on the round's cells.
ArmRole = Literal[MeasurementRole.ORIGIN, MeasurementRole.PANEL, MeasurementRole.PARENT]


class ArmSlot(FrozenModel):
    """Where an individual was walked as an arm, its latest walk for a re-read: the ``idx``-th of its round's ``total``."""

    round: int
    idx: int
    total: int

    @property
    def label(self) -> str:
        return candidate_label(self.round, self.idx)


def arm_key(
    individual: OptSearchPoint, failures: Iterable[ValidationFailure], round_num: int, idx: int
) -> ArmKey:
    """A refused proposal is keyed by its SLOT: its content is often its parent's or a sibling's."""
    if fatal_validation_failures(failures):
        return ArmKey(stable_hash([round_num, idx, individual.id]))
    return ArmKey(individual.id)


class Proposal(FrozenModel):
    """What a proposer hands the round: an individual, its sparse overlay, and why it is refused."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    individual: OptSearchPoint
    # The sparse fork transport; the individual's own params are the resolved whole.
    overlay: dict[str, dict[str, Any]] = Field(default_factory=dict)
    validation_failures: list[ValidationFailure] = Field(default_factory=list)

    @property
    def refused(self) -> bool:
        return bool(fatal_validation_failures(self.validation_failures))

    def key(self, round_num: int, idx: int) -> ArmKey:
        return arm_key(self.individual, self.validation_failures, round_num, idx)


class _Arm(FrozenModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    role: ArmRole
    # Its walk as a proposal — for a re-read, the latest walk it re-reads; ``None``: a reference no
    # round of this cycle walked as an arm.
    slot: ArmSlot | None
    individual: OptSearchPoint
    overlay: dict[str, dict[str, Any]] = Field(default_factory=dict)
    validation_failures: list[ValidationFailure] = Field(default_factory=list)

    @model_validator(mode="after")
    def _only_a_parent_is_unwalked(self) -> Self:
        if self.slot is None and self.role != MeasurementRole.PARENT:
            raise ValueError(f"a {self.role} arm is walked: it holds its slot")
        return self

    @property
    def label(self) -> str:
        return "parent" if self.slot is None else self.slot.label

    @property
    def refused(self) -> bool:
        return bool(fatal_validation_failures(self.validation_failures))

    @property
    def changes_description(self) -> str:
        return self.individual.lineage.changes_description or ""


class Proposed(_Arm):
    state: Literal["proposed"] = "proposed"


class _Walked(_Arm):
    # Stamped, never re-derived: it covers each node's rendered ``prompt``.
    sp_hash: str
    # Origin floor ⊕ this individual's delta, schema-resolved: what a steered fork seeds from.
    resolved_params: dict[str, Any]
    expected_samples: int


class Measuring(_Walked):
    """What is known as the walk opens; its cells and their fold land on their own records."""

    state: Literal["measuring"] = "measuring"
    sample_order: list[int] = Field(default_factory=list)
    n_priors: int = 0
    # The turn's place in a block race — ``n`` of ``of``, ``size`` cells, ``racing`` arms live.
    block: dict[str, int] | None = None


class Elimination(FrozenModel):
    """The eliminator's reading of a stop it made, for the arm or against it."""

    reason: str
    # Opaque to the bench (potter's: `pobb/checks.py::EliminationContext`).
    context: dict[str, Any]


class Eliminated(FrozenModel):
    kind: Literal["eliminated"] = "eliminated"
    elimination: Elimination


class DegradationContext(TypedDict, total=False):
    """The bench's reading of a ``BROKEN`` arm, empty on every other."""

    degraded_rate: float
    degraded_count: int
    total_scored: int
    dominant_warning: str
    fatal: bool
    warning_types: dict[str, int]
    source: str


def degradation_reading(
    *,
    source: str,
    degraded_count: int,
    total_scored: int,
    warning_types: Mapping[str, int],
    dominant_warning: str,
    fatal: bool = False,
) -> DegradationContext:
    return {
        "degraded_rate": degraded_count / total_scored,
        "degraded_count": degraded_count,
        "total_scored": total_scored,
        "dominant_warning": dominant_warning,
        "fatal": fatal,
        "warning_types": dict(warning_types),
        "source": source,
    }


class Broken(FrozenModel):
    """The bench's run-health rules stopped it."""

    kind: Literal["broken"] = "broken"
    failure: RuntimeFailure
    degradation: DegradationContext


class Invalid(FrozenModel):
    """Refused before a cell: the reasons are the arm's own fatal ``validation_failures``."""

    kind: Literal["invalid"] = "invalid"


class Skipped(FrozenModel):
    """The operator stopped it."""

    kind: Literal["skipped"] = "skipped"


CutReason = Annotated[Eliminated | Broken | Invalid | Skipped, Field(discriminator="kind")]


class Cut(_Walked):
    """Stopped short of its panel against it."""

    state: Literal["cut"] = "cut"
    reason: CutReason

    @property
    def kind(self) -> CutKind:
        return self.reason.kind


class Measured(_Walked):
    state: Literal["measured"] = "measured"
    # Stopped FOR it: the eliminator settled it before its panel ran out.
    locked_in: Elimination | None


Concluded = Cut | Measured
ArmState = Annotated[Proposed | Measuring | Cut | Measured, Field(discriminator="state")]

assert {r.model_fields["kind"].default for r in get_args(get_args(CutReason)[0])} == set(
    get_args(CutKind)
), "CutKind is the cut reasons' discriminators"
assert {a.model_fields["state"].default for a in get_args(get_args(ArmState)[0])} == set(
    get_args(ArmStateKind)
), "ArmStateKind is the arm states' discriminators"


def arm_end(arm: ArmState) -> ArmEnd | None:
    match arm:
        case Cut(reason=reason):
            return reason.kind
        case Measured(locked_in=Elimination()):
            return "locked_in"
        case _:
            return None


def walked_short(arm: ArmState) -> bool:
    """Stopped before its panel ran out, for it or against it; an ``invalid`` arm never started one."""
    return arm_end(arm) not in (None, "invalid")


@shapes_optimizer_prompt
def arm_elimination(arm: ArmState) -> Elimination | None:
    """The eliminator's verdict on an arm it settled, cut or locked in."""
    match arm:
        case Cut(reason=Eliminated(elimination=elimination)) | Measured(locked_in=elimination):
            return elimination
        case _:
            return None


def is_leader_eligible(arm: ArmState) -> bool:
    """Measured or eliminated: the bench's own stops and a refusal leave no reading."""
    return isinstance(arm, Measured) or (isinstance(arm, Cut) and arm.kind == "eliminated")


def is_electable(arm: ArmState, rows: Sequence[GradedCell]) -> bool:
    """NOT the whole election rule: the coverage floor is `scoring/selection.py::readable_lifts`."""
    return is_leader_eligible(arm) and bool(rows) and not is_answer_collapsed(rows)


def arm_name(key: ArmKey, arm: ArmState, round_num: int) -> ArmName:
    """An arm by its slot; a parent no round of the cycle walked is the one *round_num* read."""
    if arm.slot is None:
        return ArmName(key=key, round=round_num, label=arm.label)
    return ArmName(key=key, round=arm.slot.round, label=arm.slot.label)


class BankedSearchPointError(ConflictError):
    code = "banked_searchpoint_moved"


@dataclass(frozen=True)
class PointContext:
    schema: PipelineSchema
    framing: TaskDecomposition
    demo: Sequence[Sample]

    def job(self, individual: OptSearchPoint) -> JobSearchPoint:
        return individual.to_job_search_point(
            schema=self.schema, framing=self.framing, demo=self.demo
        )

    def measured(self, arm: Concluded) -> JobSearchPoint:
        sp = self.job(arm.individual)
        if sp.sp_hash(self.schema) != arm.sp_hash:
            raise BankedSearchPointError(
                f"{arm.label} rebuilds as searchpoint {sp.sp_hash(self.schema)[:12]}, not the "
                f"{arm.sp_hash[:12]} its rows were measured under: the pipeline schema, framing "
                "or demo pool has moved under it."
            )
        return sp
