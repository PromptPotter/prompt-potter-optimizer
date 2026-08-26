"""Server startup banner — printed once by ``main.py``'s ``lifespan()``. Pure formatting, no I/O."""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.presentation.views.display import BOLD, DIM, RESET, YELLOW

if TYPE_CHECKING:
    from pathlib import Path

# A pot-belly glyph: narrow neck, wide gut, one shaded corner for a single implied light
# source. Hand-drawn on purpose — not a perfectly symmetric curve — to match a thrown pot
# rather than a machined one.
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
    """The TLS-warning slot: whether a request has to prove who it is. A posture the operator cannot
    see is one they cannot have chosen, and both silent ends of it bite — an open box on a tunnel
    serves the whole workspace to anyone, and a provider-less production 401s every request."""
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


def render_server_banner(
    *,
    brand_name: str,
    version: str,
    environment: str,
    data_root: Path,
    auth_open: bool,
    providers: tuple[str, ...],
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
    )
    glyph_lines = [_glyph_row(row, shade) for row, shade in zip(_GLYPH_PLAIN, _SHADE, strict=True)]
    blank_glyph_col = " " * _GLYPH_WIDTH
    rows = [
        f"{glyph_lines[i] if i < len(glyph_lines) else blank_glyph_col}{_GLYPH_GUTTER}{text}"
        for i, text in enumerate(info_lines)
    ]
    return "\n".join(rows)
