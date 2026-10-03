"""Every paid send litellm makes in THIS process, billed where it is made.

Harbor's in-process agents reach their model through ``litellm.acompletion``, not through our
clients. Inside :func:`litellm_sends_billed_as` each such send goes the way our own do
(``base.py::_admitted_send``): held at its worst case before it leaves, closed with the bill the
provider reported when it lands, released when the provider provably billed nothing, and
unreported when nothing came back. So a cell's spend lands send by send as it is made, and a
cancelled cell loses only the send it had out — never the bills it already paid.

A send made outside that block is not ours to meter and passes through untouched: a connector
that pays through litellm without it (``dspy``) is billed per cell, off its reply."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from promptpotter.infrastructure.llm.base import send_bound
from promptpotter.infrastructure.llm.openai_compat import reply_cost, reply_served_by, reply_usage
from promptpotter.infrastructure.llm.pricing import rate_ceiling
from promptpotter.infrastructure.llm.spend_book import (
    Billed,
    CallLabel,
    admitted,
    may_have_billed,
    never_sent,
)

_LABEL: ContextVar[CallLabel | None] = ContextVar("litellm_send_label", default=None)
_UNMETERED: dict[str, Callable[..., Awaitable[Any]]] = {}


def litellm_route(model: str) -> tuple[str, str | None]:
    """``(model, provider)`` for a litellm model string — the split litellm itself routes by, so
    ``openrouter/inception/mercury-2.5`` bills as OpenRouter's ``inception/mercury-2.5``."""
    import litellm

    try:
        name, provider, _key, _base = litellm.get_llm_provider(model)
    except Exception:
        return model, None
    return name, provider


@contextmanager
def litellm_sends_billed_as(label: CallLabel) -> Iterator[None]:
    """Bill every litellm send made inside the block — in this task or any task it starts — as
    ``label``'s, one send at a time."""
    _meter()
    token = _LABEL.set(label)
    try:
        yield
    finally:
        _LABEL.reset(token)


def _meter() -> None:
    """Route litellm's async entry points through here, once per process."""
    import litellm

    if _UNMETERED:
        return
    _UNMETERED["acompletion"] = litellm.acompletion
    _UNMETERED["aresponses"] = litellm.aresponses
    litellm.acompletion = _billed_acompletion
    litellm.aresponses = _refused_aresponses


async def _billed_acompletion(*args: Any, **kwargs: Any) -> Any:
    send = _UNMETERED["acompletion"]
    if (label := _LABEL.get()) is None:
        return await send(*args, **kwargs)
    if kwargs.get("stream"):
        raise RuntimeError(f"{label.node}: a streamed litellm send reports no bill at its send")
    model, provider = litellm_route(str(kwargs.get("model") or args[0]))
    messages = kwargs.get("messages", args[1] if len(args) > 1 else None)
    bound = send_bound(
        await rate_ceiling(model, provider) if provider else None,
        sent=messages,
        max_tokens=kwargs.get("max_tokens"),
    )
    with admitted(label, bound, model=model, provider=provider) as admission:
        try:
            reply = await send(*args, **kwargs)
        except Exception as exc:
            if never_sent(exc) or not may_have_billed(getattr(exc, "status_code", None), exc):
                admission.release()
            raise
        admission.settle(
            Billed(reply_usage(reply), reply_cost(reply), served_by=reply_served_by(reply))
        )
        return reply


async def _refused_aresponses(*args: Any, **kwargs: Any) -> Any:
    if (label := _LABEL.get()) is not None:
        raise RuntimeError(
            f"{label.node}: litellm's Responses API is not metered here, so a send through it "
            "would reach the provider unbilled — run the agent on chat completions"
        )
    return await _UNMETERED["aresponses"](*args, **kwargs)


__all__ = ["litellm_route", "litellm_sends_billed_as"]
