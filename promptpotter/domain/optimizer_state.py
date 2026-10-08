"""An optimizer's own working state, which rides the round document and never the individual.

The bench persists it as ``RoundResult.optimizer_state``, restores it from there on resume and
fork, and reads nothing inside ``payload`` beyond what :class:`RoundPayload` asks of it. Each
optimizer declares its payload in its own package, registered under its manifest's name."""

from __future__ import annotations

from typing import Annotated, ClassVar, Self, TypedDict, Unpack

from pydantic import ConfigDict, SerializeAsAny, model_validator

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
    "round_payload_type",
]

# The reasons a proposer's output can be unreadable. Opposite kinds of evidence, so no reader may
# treat one as a bool:
#   MALFORMED  — schema-noncompliant output. The optimizer prompt's fault; charge it.
#   WRONG_TYPE — decoded cleanly but as another model, so the fault is the schema it asked
#                for, not the transport. Charged like MALFORMED.
#   TOOLING    — empty/truncated content. Missing data, not a verdict: charging it scores
#                provider flakiness as a bad mutation, so the round must be EXCLUDED.
PARSE_FAILURE_MALFORMED: Annotated[str, shapes_optimizer_prompt] = "optimizer_prompt_parse_failure"
PARSE_FAILURE_WRONG_TYPE = "optimizer_prompt_unexpected_type"
PARSE_FAILURE_TOOLING: Annotated[str, shapes_optimizer_prompt] = "l1_provider_empty_response"
# The reasons a CHARGING reader may hold against the optimizer prompt. Asked as this predicate,
# never as `is not None` — that is the bool the block above forbids, and it reads TOOLING as a
# verdict the round never reached. A ROUTING reader is a different question and may ask either.
PARSE_FAILURE_CHARGED: frozenset[str] = frozenset(
    {PARSE_FAILURE_MALFORMED, PARSE_FAILURE_WRONG_TYPE}
)


class CritiqueReadout(TypedDict, total=False):
    """A domain-local mirror, so a round file round-trips without the optimization layer's
    schema in scope; the optimizer node's full Pydantic shape stays in ``dispatch/schemas.py``."""

    priority_fix: str
    suggested_axes: list[str]
    failure_highlights: list[str]


class RoundPayload(StrictModel):
    """An optimizer's payload: ``class P(RoundPayload, manifest="name")`` registers it, and the
    envelope reads a banked one back as that type. It answers the two questions a harness reader
    asks of any optimizer's payload; one that keeps no such readout answers with absence."""

    _registered: ClassVar[dict[str, type[RoundPayload]]] = {}

    def __init_subclass__(cls, *, manifest: str, **kwargs: Unpack[ConfigDict]) -> None:
        super().__init_subclass__(**kwargs)
        if (held := RoundPayload._registered.setdefault(manifest, cls)) is not cls:
            raise TypeError(f"{manifest!r} registers two payloads: {held.__name__}, {cls.__name__}")

    def feedback(self) -> CritiqueReadout | None:
        return None

    def lost_to_empty_response(self) -> bool:
        """A round that proposed no arm at all: whether its generation came back empty. A round
        with arms answers off them instead (``domain/l4/proxies.py::_is_evidential``)."""
        return False


def round_payload_type(manifest: str) -> type[RoundPayload]:
    if (held := RoundPayload._registered.get(manifest)) is None:
        raise ValueError(
            f"no payload is registered for optimizer {manifest!r} (known: "
            f"{sorted(RoundPayload._registered)}) — complete the registries before reading a round"
        )
    return held


class OptimizerState(StrictModel):
    """``{manifest, prompt_hashes, payload}`` — the one envelope every optimizer's state rides."""

    manifest: str
    # Which prompts of the manifest produced this round, per llm node — the only thing that can
    # answer "was this round produced by the optimizer I am holding now?" once the process exited.
    # Resume diverges at the FIRST round that disagrees.
    # IDENTITY, NOT A FIRE RECORD — every node is named on every round, including ones that never
    # run. Which node RAN, and what each panel cost it, is the ledger's `llm_call`.
    prompt_hashes: dict[str, str]
    payload: SerializeAsAny[RoundPayload]

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
        """The payload as *kind*, raising on any other optimizer's — a reader handed a peer's round
        is a wiring fault, never a round to read as empty."""
        if not isinstance(self.payload, kind):
            raise TypeError(f"a {kind.__name__} reader was handed {self.manifest!r}'s state")
        return self.payload
