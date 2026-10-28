from __future__ import annotations

from typing import Annotated, ClassVar, Literal, Self, TypedDict, Unpack

from pydantic import ConfigDict, Field, SerializeAsAny, model_validator

from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.hashing import shapes_optimizer_prompt

PARSE_FAILURE_MALFORMED: Annotated[str, shapes_optimizer_prompt] = "optimizer_prompt_parse_failure"
PARSE_FAILURE_WRONG_TYPE = "optimizer_prompt_unexpected_type"
# Empty or truncated content is missing data, not a verdict: the round is EXCLUDED, never charged.
PARSE_FAILURE_TOOLING: Annotated[str, shapes_optimizer_prompt] = "l1_provider_empty_response"
# A response whose every variant leaves its parent unchanged: zero candidates, never charged.
PARSE_FAILURE_ALL_NO_OP: Annotated[str, shapes_optimizer_prompt] = "l1_all_no_op_variants"
# Ask this predicate, never `is not None`, which reads TOOLING as a verdict.
PARSE_FAILURE_CHARGED: frozenset[str] = frozenset(
    {PARSE_FAILURE_MALFORMED, PARSE_FAILURE_WRONG_TYPE}
)


class FailureClusterReadout(TypedDict):
    sample_ids: list[int]
    mechanism: str
    claim: str


class CritiqueReadout(TypedDict, total=False):
    """A domain-local mirror, so a round file round-trips without the optimizer's schema in scope."""

    priority_fix: str
    suggested_axes: list[str]
    failure_highlights: list[FailureClusterReadout]


class StatePart(FrozenModel):
    """One kind of optimizer state, registered under ``kind``; ``cls()`` is its value before any member writes it, where every field defaults."""

    kind: ClassVar[str]
    # How many roster members may write it; every part but a pool is one member's.
    writers: ClassVar[Literal["one", "many"]] = "one"
    _registered: ClassVar[dict[str, type[StatePart]]] = {}

    def __init_subclass__(cls, *, kind: str | None, **kwargs: Unpack[ConfigDict]) -> None:
        super().__init_subclass__(**kwargs)
        # `None` is a scope (`StateSlice`, `RoundOutput`), which registers nothing.
        if kind is None:
            return
        if (held := StatePart._registered.setdefault(kind, cls)) is not cls:
            raise TypeError(
                f"state kind {kind!r} is registered twice: {held.__name__}, {cls.__name__}"
            )
        cls.kind = kind

    @staticmethod
    def of_kind(kind: str) -> type[StatePart]:
        """The ONE dispatch by kind: validation and `restamp.py`'s pruning both read a banked part through it."""
        if (held := StatePart._registered.get(kind)) is None:
            raise UnregisteredStatePartError(
                f"no optimizer state kind is registered as {kind!r} (known: "
                f"{sorted(StatePart._registered)}) — the package declaring it is not installed"
            )
        return held

    @classmethod
    def written_first(cls) -> bool:
        """No value stands before its writer runs, so the roster must walk one ahead of every reader."""
        return any(f.is_required() for f in cls.model_fields.values())


class StateSlice(StatePart, kind=None):
    """State a member carries ACROSS rounds; a round's close banks it."""


class RoundOutput(StatePart, kind=None):
    """What ONE round produced, banked on that round; a round whose writer did not run holds ``cls()``, or nothing where it is written first."""

    def lost_to_empty_response(self) -> bool:
        """Asked only of a round with no arm; one with arms answers off them (``l4/proxies.py::_is_evidential``)."""
        return False


class UnregisteredStatePartError(RuntimeError):
    """Not a ``ValueError`` on purpose: a ledger reader skips a line that fails validation."""


class Critique(RoundOutput, kind="critique"):
    """Feedback FOR the next round's proposer, distilled after this round's scoring."""

    readout: CritiqueReadout | None = None


def _by_kind(held: object) -> object:
    if not isinstance(held, dict):
        return held
    return {
        kind: StatePart.of_kind(kind).model_validate(value) if isinstance(value, dict) else value
        for kind, value in held.items()
    }


class OptimizerState(FrozenModel):
    """The one envelope every optimizer's state rides: its parts by kind beside the bench's population."""

    manifest: str
    # Empty where the manifest keeps only the bench's one parent.
    population: list[OptSearchPoint] = Field(default_factory=list)
    # IDENTITY, not a fire record: every llm node is named every round, and a resume diverges on these.
    prompt_hashes: dict[str, str]
    # A written-first kind is absent until its writer ran.
    slices: dict[str, SerializeAsAny[StateSlice]]
    # Every kind the manifest carries, a written-first kind once its writer ran.
    outputs: dict[str, SerializeAsAny[RoundOutput]]

    @model_validator(mode="before")
    @classmethod
    def _read_by_kind(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        return {
            **data,
            **{key: _by_kind(data[key]) for key in ("slices", "outputs") if key in data},
        }

    @model_validator(mode="after")
    def _each_under_its_kind(self) -> Self:
        held: dict[str, StatePart] = {**self.slices, **self.outputs}
        if wrong := sorted(k for k, s in held.items() if type(s) is not StatePart.of_kind(k)):
            raise ValueError(f"optimizer_state holds {wrong} under another kind's name")
        return self

    def slice[S: StateSlice](self, kind: type[S]) -> S:
        """A reader asking a round its manifest never carried is a wiring fault, never a round to read as empty."""
        found = self.slices.get(kind.kind)
        if not isinstance(found, kind):
            raise KeyError(f"{self.manifest!r}'s state carries no {kind.kind!r} slice")
        return found

    def output[O: RoundOutput](self, kind: type[O]) -> O:
        found = self.outputs.get(kind.kind)
        if not isinstance(found, kind):
            raise KeyError(f"{self.manifest!r}'s rounds bank no {kind.kind!r} output")
        return found

    def holding(self, *parts: StatePart) -> OptimizerState:
        slices = {p.kind: p for p in parts if isinstance(p, StateSlice)}
        outputs = {p.kind: p for p in parts if isinstance(p, RoundOutput)}
        return self.model_copy(
            update={"slices": {**self.slices, **slices}, "outputs": {**self.outputs, **outputs}}
        )

    def feedback(self) -> CritiqueReadout | None:
        """``None`` too where the manifest distils no critique: L4 and the bench ask every optimizer."""
        held = self.outputs.get(Critique.kind)
        return held.readout if isinstance(held, Critique) else None

    def lost_to_empty_response(self) -> bool:
        return any(o.lost_to_empty_response() for o in self.outputs.values())
