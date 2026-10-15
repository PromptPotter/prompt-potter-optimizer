"""The ONE header seam: what a page may load, what a browser may keep, and which build is being served."""

from __future__ import annotations

import time
from pathlib import Path

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_API = "/api/v1/"
# A build names these files by their content, so one that was served never changes.
_CONTENT_NAMED = "/_next/static/"
_SERVED = (200, 206, 304)
_API_CSP = "default-src 'none'; frame-ancestors 'none'"
# 'unsafe-inline': the export inlines per-page RSC scripts, and a 304 revalidation carries no body to hash.
_WEBAPP_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; "
    "form-action 'self'"
)


def cache_policy(*, api: bool, path: str, status: int) -> str:
    """What a browser may answer from its own copy without asking.

    Every page is the CURRENT build's entry and is re-asked each time: one kept past a deploy runs
    the old build against the new API. A content-named file is kept only once it was actually
    served, since a 404 for a chunk asked mid-build would otherwise be kept as long.
    """
    if api:
        return "no-store"
    if path.startswith(_CONTENT_NAMED) and status in _SERVED:
        return "public, max-age=31536000, immutable"
    return "no-cache"


def served_build(webapp_dir: Path) -> str:
    """Read per answer, not at startup: a rebuild under a running server is the local loop."""
    try:
        return (webapp_dir / "build-id.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


class ResponseHeadersMiddleware:
    """Pure ASGI: ``BaseHTTPMiddleware`` buffers the body and breaks SSE teardown."""

    def __init__(self, app: ASGIApp, *, webapp_dir: Path) -> None:
        self.app = app
        self._webapp_dir = webapp_dir

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        api = path.startswith(_API)
        is_https = scope.get("scheme") == "https"
        began = time.perf_counter()

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("x-content-type-options", "nosniff")
                headers.setdefault("x-frame-options", "DENY")
                headers.setdefault("referrer-policy", "strict-origin-when-cross-origin")
                if is_https:
                    headers.setdefault(
                        "strict-transport-security", "max-age=63072000; includeSubDomains"
                    )
                headers.setdefault("content-security-policy", _API_CSP if api else _WEBAPP_CSP)
                headers["cache-control"] = cache_policy(
                    api=api, path=path, status=message["status"]
                )
                if api:
                    elapsed_ms = (time.perf_counter() - began) * 1000
                    headers["server-timing"] = f"app;dur={elapsed_ms:.1f}"
                    # The webapp compares it with the id it was built with (`lib/api/client.ts::send`).
                    build = served_build(self._webapp_dir)
                    if build:
                        headers["x-webapp-build"] = build
            await send(message)

        await self.app(scope, receive, send_with_headers)


__all__ = ["ResponseHeadersMiddleware", "cache_policy", "served_build"]
