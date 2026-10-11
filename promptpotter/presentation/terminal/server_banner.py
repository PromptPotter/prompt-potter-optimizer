from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.views.render.primitives import BOLD, DIM, RESET, YELLOW

if TYPE_CHECKING:
    from pathlib import Path

_GLYPH_PLAIN = (
    "      .-.",
    "      | |",
    "    _/   \\_",
    "   (  .:@@ )",
    "   | .:@@@ |",
    "    \\_____/",
)
_SHADE = (None, None, None, ".:@@", ".:@@@", None)
_GLYPH_WIDTH = max(len(row) for row in _GLYPH_PLAIN)
_GLYPH_GUTTER = "   "

__all__ = ["render_server_banner"]


def _glyph_row(row: str, shade: str | None) -> str:
    """Pad on the PLAIN row first, then wrap in color — padding must never count escape bytes."""
    if shade is None:
        return f"{DIM}{row.ljust(_GLYPH_WIDTH)}{RESET}"
    before, _, after = row.partition(shade)
    after = after.ljust(_GLYPH_WIDTH - len(before) - len(shade))
    return f"{DIM}{before}{RESET}{YELLOW}{shade}{RESET}{DIM}{after}{RESET}"


def _auth_lines(*, auth_open: bool, providers: tuple[str, ...]) -> tuple[str, ...]:
    if auth_open:
        return (
            f"{DIM}Auth: {RESET}{YELLOW}open — no sign-in enforced{RESET}",
            f"{YELLOW}  WARNING: every request runs as the local operator.{RESET}",
            f"{YELLOW}  Do not expose this port beyond localhost.{RESET}",
        )
    if not providers:
        return (
            f"{DIM}Auth: {RESET}{YELLOW}no providers configured{RESET}",
            f"{YELLOW}  WARNING: no sign-in can complete — every request is 401.{RESET}",
        )
    return (f"{DIM}Auth: {', '.join(providers)}{RESET}",)


def _encoding_lines(non_utf8: str | None) -> tuple[str, ...]:
    """ASCII only: this prints to the very console whose encoding it reports."""
    if non_utf8 is None:
        return ()
    return (
        f"{DIM}Text: {RESET}{YELLOW}{non_utf8} — not UTF-8{RESET}",
        f"{YELLOW}  WARNING: container-backed campaigns (harbor) refuse to start here.{RESET}",
        f"{YELLOW}  Relaunch with PYTHONUTF8=1 to run them.{RESET}",
    )


def render_server_banner(
    *,
    brand_name: str,
    version: str,
    environment: str,
    data_root: Path,
    auth_open: bool,
    providers: tuple[str, ...],
    non_utf8: str | None,
) -> str:
    header_plain = f"Running {brand_name} {version} in {environment} mode."
    info_lines = (
        f"{BOLD}{header_plain}{RESET}",
        f"{DIM}{'-' * len(header_plain)}{RESET}",
        f"{DIM}Serving:{RESET}",
        "  Webapp   /",
        "  API      /api/v1",
        "  Docs     /docs",
        "",
        f"{DIM}Data: {data_root}{RESET}",
        *_auth_lines(auth_open=auth_open, providers=providers),
        *_encoding_lines(non_utf8),
    )
    glyph_lines = [_glyph_row(row, shade) for row, shade in zip(_GLYPH_PLAIN, _SHADE, strict=True)]
    blank_glyph_col = " " * _GLYPH_WIDTH
    rows = [
        f"{glyph_lines[i] if i < len(glyph_lines) else blank_glyph_col}{_GLYPH_GUTTER}{text}"
        for i, text in enumerate(info_lines)
    ]
    return "\n".join(rows)
