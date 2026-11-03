"""Keeps each request that reached this app from outside without a session (`application/app_visits.py`)."""

from __future__ import annotations

import asyncio
import ipaddress

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from promptpotter.application.app_visits import keep_visit
from promptpotter.shared.errors import graceful

_SITE_RECORDS = "/api/v1/site-records/"
_CONTENT_NAMED = "/_next/static/"


def _outside(host: str) -> bool:
    """A public address: loopback, the LAN and a test client's name are this side."""
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return False


def _kept(scope: Scope, status: int) -> bool:
    path: str = scope.get("path", "")
    if scope.get("state", {}).get("identity_ctx") is not None:
        # A signed-in person's requests are their own trail's.
        return False
    if path.startswith(_SITE_RECORDS) and status < 400:
        # The website's own sender, holding its token.
        return False
    # A page's own build files, fetched by every visit that loads it.
    return not (path.startswith(_CONTENT_NAMED) and status in (200, 206, 304))


class OutsideVisitsMiddleware:
    """Pure ASGI, outside the OIDC seam, so the session it resolved is on the scope when this reads."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        client = scope.get("client")
        if scope["type"] != "http" or client is None or not _outside(client[0]):
            await self.app(scope, receive, send)
            return
        answered: list[int] = []

        async def send_noting_status(message: Message) -> None:
            if message["type"] == "http.response.start":
                answered.append(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_noting_status)
        finally:
            if answered and _kept(scope, answered[0]):
                headers = Headers(scope=scope)
                # After the answer: a visit that could not be kept costs the record, never the response.
                with graceful("could not keep an outside visit"):
                    await asyncio.to_thread(
                        keep_visit,
                        ip=client[0],
                        country=headers.get("cf-ipcountry", ""),
                        method=scope.get("method", ""),
                        path=scope.get("path", ""),
                        query=scope.get("query_string", b"").decode("latin-1"),
                        status=answered[0],
                        ua=headers.get("user-agent", ""),
                        referrer=headers.get("referer", ""),
                    )
