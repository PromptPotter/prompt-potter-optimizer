"""``dspy`` is not metered here: its sends are admitted at its LM call (``connectors/dspy_module.py``)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from promptpotter.infrastructure.llm.base import hold_ceiling, send_bound
from promptpotter.infrastructure.llm.openai_compat import reply_bill
from promptpotter.infrastructure.llm.send_failure import failed_send
from promptpotter.infrastructure.llm.spend_book import (
    Admission,
    CallLabel,
    SendOutcome,
    admitted,
    answered,
)


@dataclass
class ReportedCost:
    _usd: float = 0.0
    _priced: int = 0
    _silent: bool = False

    def count(self, outcome: SendOutcome) -> None:
        if outcome.reported is None:
            self._silent = self._silent or outcome.may_have_billed
            return
        for part in outcome.reported.parts:
            if part.cost_usd is None:
                self._silent = True
            else:
                self._usd += part.cost_usd
                self._priced += 1

    @property
    def usd(self) -> float | None:
        """``None`` once any send that may have billed was silent: unknown, never a smaller price."""
        return self._usd if self._priced and not self._silent else None


@dataclass(frozen=True)
class _Block:
    label: CallLabel
    model: str | None
    provider: str | None
    cost: ReportedCost = field(default_factory=ReportedCost)

    def settle(self, admission: Admission, outcome: SendOutcome) -> None:
        admission.close(outcome)
        self.cost.count(outcome)


_BLOCK: ContextVar[_Block | None] = ContextVar("litellm_sends_billed", default=None)
_UNMETERED: dict[str, Callable[..., Awaitable[Any]]] = {}


@contextmanager
def litellm_sends_billed_as(
    label: CallLabel, *, model: str | None, provider: str | None
) -> Iterator[ReportedCost]:
    """Priced as the owner's ``(model, provider)`` pair: litellm's model string is never split back."""
    _meter()
    block = _Block(label, model, provider)
    token = _BLOCK.set(block)
    try:
        yield block.cost
    finally:
        _BLOCK.reset(token)


def _meter() -> None:
    import litellm

    if _UNMETERED:
        return
    _UNMETERED["acompletion"] = litellm.acompletion
    _UNMETERED["aresponses"] = litellm.aresponses
    litellm.acompletion = _billed_acompletion
    litellm.aresponses = _refused_aresponses


async def _billed_acompletion(*args: Any, **kwargs: Any) -> Any:
    send = _UNMETERED["acompletion"]
    if (block := _BLOCK.get()) is None:
        return await send(*args, **kwargs)
    label = block.label
    if kwargs.get("stream"):
        raise RuntimeError(f"{label.node}: a streamed litellm send reports no bill at its send")
    model, provider = block.model, block.provider
    messages = kwargs.get("messages", args[1] if len(args) > 1 else None)
    bound = send_bound(
        await hold_ceiling(model, provider) if model and provider else None,
        sent=messages,
        max_tokens=kwargs.get("max_tokens"),
    )
    with admitted(label, bound, model=model, provider=provider) as admission:
        try:
            reply = await send(*args, **kwargs)
        except BaseException as exc:
            # Cancelled with the send out, it left and nothing said what it cost.
            left = SendOutcome(sent=True, may_have_billed=True)
            block.settle(admission, failed_send(exc) if isinstance(exc, Exception) else left)
            raise
        block.settle(admission, answered(reply_bill(reply, model=None)))
        return reply


async def _refused_aresponses(*args: Any, **kwargs: Any) -> Any:
    if (block := _BLOCK.get()) is not None:
        raise RuntimeError(
            f"{block.label.node}: litellm's Responses API is not metered here, so a send through "
            "it would reach the provider unbilled — run the agent on chat completions"
        )
    return await _UNMETERED["aresponses"](*args, **kwargs)


__all__ = ["ReportedCost", "litellm_sends_billed_as"]
