"""The ONE header seam: what a page may load, what a browser may keep, and which build is being served."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_API = "/api/v1/"
# The build (`webapp/vite.config.ts`) names these files by their content, so one that was served never changes.
_CONTENT_NAMED = "/assets/"
_API_CSP = "default-src 'none'; frame-ancestors 'none'"
# No inline script or style: the theme stamp is a file, and React's `style=` props set the CSSOM,
# which `style-src` does not govern. The browser walk fails on any violation (`e2e/harness.ts`).
_WEBAPP_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; "
    "form-action 'self'"
)
# The API reference (`main.py::scalar_docs`) is Scalar's page: its script and fonts come off its CDN.
_DOCS = "/docs"
_DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline'; font-src 'self' data: https://fonts.scalar.com; "
    "img-src 'self' data: https:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
)


def content_policy(path: str) -> str:
    if path.startswith(_API):
        return _API_CSP
    return _DOCS_CSP if path == _DOCS else _WEBAPP_CSP


def served_build_file(path: str, status: int) -> bool:
    """A content-named file actually served: a 404 for a chunk asked mid-build is not one."""
    return path.startswith(_CONTENT_NAMED) and status in (200, 206, 304)


def cache_policy(*, api: bool, path: str, status: int) -> str:
    """What a browser may answer from its own copy without asking.

    Every page is the CURRENT build's entry and is re-asked each time: one kept past a deploy runs
    the old build against the new API. A content-named file is kept only once it was actually
    served, since a 404 for a chunk asked mid-build would otherwise be kept as long.
    """
    if api:
        return "no-store"
    if served_build_file(path, status):
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
                headers.setdefault("content-security-policy", content_policy(path))
                headers["cache-control"] = cache_policy(
                    api=api, path=path, status=message["status"]
                )
                if api:
                    elapsed_ms = (time.perf_counter() - began) * 1000
                    headers["server-timing"] = f"app;dur={elapsed_ms:.1f}"
                    # The webapp compares it with the id it was built with (`lib/api/client.ts::send`).
                    build = await asyncio.to_thread(served_build, self._webapp_dir)
                    if build:
                        headers["x-webapp-build"] = build
            await send(message)

        await self.app(scope, receive, send_with_headers)
