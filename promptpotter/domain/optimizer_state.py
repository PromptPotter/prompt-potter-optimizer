from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, ClassVar, Self, TypedDict, Unpack

from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny, model_validator

from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.hashing import shapes_optimizer_prompt

__all__ = [
    "PARSE_FAILURE_CHARGED",
    "PARSE_FAILURE_MALFORMED",
    "PARSE_FAILURE_TOOLING",
    "PARSE_FAILURE_WRONG_TYPE",
    "CritiqueReadout",
    "OptimizerState",
    "RoundPayload",
    "UnregisteredPayloadError",
    "round_payload_type",
]

PARSE_FAILURE_MALFORMED: Annotated[str, shapes_optimizer_prompt] = "optimizer_prompt_parse_failure"
PARSE_FAILURE_WRONG_TYPE = "optimizer_prompt_unexpected_type"
# Empty or truncated content is missing data, not a verdict: the round is EXCLUDED, never charged.
PARSE_FAILURE_TOOLING: Annotated[str, shapes_optimizer_prompt] = "l1_provider_empty_response"
# Ask this predicate, never `is not None`, which reads TOOLING as a verdict.
PARSE_FAILURE_CHARGED: frozenset[str] = frozenset(
    {PARSE_FAILURE_MALFORMED, PARSE_FAILURE_WRONG_TYPE}
)


class CritiqueReadout(TypedDict, total=False):
    """A domain-local mirror, so a round file round-trips without the optimizer's schema in scope."""

    priority_fix: str
    suggested_axes: list[str]
    failure_highlights: list[str]


class RoundPayload(StrictModel):
    """An optimizer's own payload, banked with every round and read back as its registered type."""

    model_config = ConfigDict(frozen=True)

    _registered: ClassVar[dict[str, type[RoundPayload]]] = {}

    def __init_subclass__(cls, *, manifest: str, **kwargs: Unpack[ConfigDict]) -> None:
        super().__init_subclass__(**kwargs)
        if (held := RoundPayload._registered.setdefault(manifest, cls)) is not cls:
            raise TypeError(f"{manifest!r} registers two payloads: {held.__name__}, {cls.__name__}")

    def feedback(self) -> CritiqueReadout | None:
        return None

    def lost_to_empty_response(self) -> bool:
        """Asked only of a round with no arm; one with arms answers off them (``l4/proxies.py::_is_evidential``)."""
        return False


class UnregisteredPayloadError(RuntimeError):
    """Not a ``ValueError`` on purpose: a ledger reader skips a line that fails validation."""


def round_payload_type(manifest: str) -> type[RoundPayload]:
    if (held := RoundPayload._registered.get(manifest)) is None:
        raise UnregisteredPayloadError(
            f"no payload is registered for optimizer {manifest!r} (known: "
            f"{sorted(RoundPayload._registered)}) — the package declaring it is not installed"
        )
    return held


class OptimizerState(StrictModel):
    """The one envelope every optimizer's state rides: its own payload beside the bench's population."""

    model_config = ConfigDict(frozen=True)

    manifest: str
    # Empty where the manifest keeps only the bench's one parent.
    population: list[OptSearchPoint] = Field(default_factory=list)
    # IDENTITY, not a fire record: every llm node is named every round, and a resume diverges on these.
    prompt_hashes: dict[str, str]
    payload: SerializeAsAny[RoundPayload]

    @classmethod
    def stored_field_model(cls, key: str, raw: Mapping[str, object]) -> type[BaseModel] | None:
        return round_payload_type(str(raw["manifest"])) if key == "payload" else None

    @model_validator(mode="before")
    @classmethod
    def _read_as_the_manifests(cls, data: object) -> object:
        if isinstance(data, dict) and isinstance(data.get("payload"), dict):
            payload = round_payload_type(data["manifest"]).model_validate(data["payload"])
            return {**data, "payload": payload}
        return data

    @model_validator(mode="after")
    def _payload_is_the_manifests(self) -> Self:
        if type(self.payload) is not round_payload_type(self.manifest):
            raise ValueError(
                f"optimizer_state names {self.manifest!r} but carries a "
                f"{type(self.payload).__name__} payload"
            )
        return self

    def payload_as[P: RoundPayload](self, kind: type[P]) -> P:
        """A reader handed a peer's round is a wiring fault, never a round to read as empty."""
        if not isinstance(self.payload, kind):
            raise TypeError(f"a {kind.__name__} reader was handed {self.manifest!r}'s state")
        return self.payload
