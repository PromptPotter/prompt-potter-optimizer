"""``httpx`` is imported where a failure is READ, never with this module: callers load it unsent."""

from __future__ import annotations

import ssl

from promptpotter.infrastructure.llm.spend_book import SendOutcome, replied
from promptpotter.shared.errors import ErrorCategory, RequestTooLargeError, SendRefusedError

__all__ = ["failed_send"]


def failed_send(
    exc: BaseException, *, credit_statuses: frozenset[int] | None = None
) -> SendOutcome:
    if isinstance(exc, SendRefusedError):
        return SendOutcome(sent=False, may_have_billed=False, failure=exc.category, detail=str(exc))
    if isinstance(exc, RequestTooLargeError):
        # It carries the provider's 413/429 as its cause, and is terminal: refused ungenerated.
        return SendOutcome(
            sent=True, may_have_billed=False, failure=ErrorCategory.CLIENT, detail=str(exc)
        )
    if (carried := _failed_status(exc)) is not None:
        status, carrier = carried
        resp = getattr(carrier, "response", None)
        body = getattr(resp, "text", None)
        return replied(
            status,
            headers=getattr(resp, "headers", None),
            said=body if isinstance(body, str) else str(exc),
            reported=None,
            credit_statuses=credit_statuses,
        )
    left = not _never_sent(exc)
    broke = _connection_broke(exc)
    transport = not left or broke or _names_no_reply(exc)
    return SendOutcome(
        sent=left,
        may_have_billed=left,
        failure=ErrorCategory.CONNECTION if transport else ErrorCategory.UNKNOWN,
        resendable=not left or broke,
        detail=f"{type(exc).__name__}: {exc}",
    )


def _never_sent(exc: BaseException) -> bool:
    import httpx

    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, httpx.ConnectError | httpx.ConnectTimeout | httpx.PoolTimeout):
            return True
        seen = seen.__cause__ or seen.__context__
    return False


def _connection_broke(exc: BaseException) -> bool:
    """Never a timeout: there the provider may still be generating."""
    import httpx

    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, httpx.TimeoutException | TimeoutError):
            return False
        if isinstance(
            seen,
            httpx.ReadError
            | httpx.WriteError
            | httpx.RemoteProtocolError
            | ssl.SSLError
            | ConnectionError,
        ):
            return True
        seen = seen.__cause__ or seen.__context__
    return False


_NO_REPLY_ERRORS = frozenset({"APIConnectionError", "LMTransportError"})


def _names_no_reply(exc: BaseException) -> bool:
    """Read by class name through the bases: no such client library is a dependency here."""
    import httpx

    return isinstance(exc, httpx.TransportError) or any(
        base.__name__ in _NO_REPLY_ERRORS for base in type(exc).__mro__
    )


def _failed_status(exc: BaseException) -> tuple[int, BaseException] | None:
    """A no-reply error carries none: litellm stamps a failed connection with a 500 nobody sent."""
    import httpx

    seen: BaseException | None = exc
    while seen is not None:
        if _names_no_reply(seen):
            return None
        for name in ("status_code", "status"):
            if isinstance(status := getattr(seen, name, None), int):
                return status, seen
        if isinstance(seen, httpx.HTTPStatusError):
            return seen.response.status_code, seen
        seen = seen.__cause__
    return None
