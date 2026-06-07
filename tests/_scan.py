"""Source-scan engine for ``structure.py`` — the declarative lint layer.

One place walks ``promptpotter/`` and finds offenders; the bans themselves are
tables in ``structure.py``. Adding a lint is adding a row, not copy-pasting an
``rglob``/``ast.walk``/offender-list block.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "promptpotter"


def iter_py(under: str | None = None) -> Iterator[tuple[str, Path]]:
    """Yield ``(rel_posix, path)`` for every ``.py`` under ``promptpotter/``.

    ``rel_posix`` is relative to ``promptpotter/`` (e.g. ``"infrastructure/store/base.py"``).
    Pass ``under`` to restrict to a subtree (e.g. ``"presentation/api"``).
    """
    base = ROOT / under if under else ROOT
    for path in base.rglob("*.py"):
        yield path.relative_to(ROOT).as_posix(), path


def regex_offenders(
    pattern: re.Pattern[str],
    text: str,
    *,
    skip_comment_lines: bool = False,
) -> list[int]:
    """Line numbers where ``pattern`` matches ``text``.

    ``skip_comment_lines`` drops matches whose line begins with ``#``/quote/``*``
    (comment or docstring) — mirrors the archive-facade scan's filtering.
    """
    hits: list[int] = []
    for match in pattern.finditer(text):
        if skip_comment_lines:
            line_start = text.rfind("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.start())
            line = text[line_start : line_end if line_end != -1 else None].lstrip()
            if line.startswith(("#", '"', "'", "*")):
                continue
        hits.append(text[: match.start()].count("\n") + 1)
    return hits


def parse(path: Path) -> ast.Module:
    """Parse ``path`` once — the single read+``ast.parse`` site for source-scans."""
    return ast.parse(path.read_text(encoding="utf-8"))


def _called_name(node: ast.Call, match: str) -> str | None:
    """Name of a call target under a match mode.

    ``"attr"`` → only ``x.foo()`` (Attribute); ``"name"`` → only ``foo()`` (Name);
    ``"any"`` → either (Attribute's ``.attr``, else Name's ``.id``).
    """
    fn = node.func
    if match in ("any", "attr") and isinstance(fn, ast.Attribute):
        return fn.attr
    if match in ("any", "name") and isinstance(fn, ast.Name):
        return fn.id
    return None


def call_offenders(path: Path, names: frozenset[str], match: str = "any") -> list[int]:
    """Line numbers of calls to any of ``names`` in ``path`` under ``match`` mode."""
    return [
        node.lineno
        for node in ast.walk(parse(path))
        if isinstance(node, ast.Call) and _called_name(node, match) in names
    ]


def calls_missing_kwarg(path: Path, name: str, kwarg: str) -> list[int]:
    """Line numbers of calls to ``name`` in ``path`` that omit keyword ``kwarg``.

    Locks "every call to this gateway must wire <kwarg> explicitly" (the
    score_search_point ``on_sample_scored`` visibility rule).
    """
    offenders: list[int] = []
    for node in ast.walk(parse(path)):
        if not isinstance(node, ast.Call) or _called_name(node, "any") != name:
            continue
        if kwarg not in {kw.arg for kw in node.keywords if kw.arg is not None}:
            offenders.append(node.lineno)
    return offenders


def raise_offenders(path: Path, exc_name: str) -> list[int]:
    """Line numbers of ``raise <exc_name>(...)`` in ``path``."""
    tree = parse(path)
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Raise)
        and isinstance(node.exc, ast.Call)
        and isinstance(node.exc.func, ast.Name)
        and node.exc.func.id == exc_name
    ]


class _RuntimeImports(ast.NodeVisitor):
    """Collect runtime import module names; skips ``if TYPE_CHECKING:`` blocks."""

    def __init__(self) -> None:
        self.modules: list[str] = []

    def visit_If(self, node: ast.If) -> None:
        if "TYPE_CHECKING" in ast.unparse(node.test):
            for n in node.orelse:
                self.visit(n)
            return
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.modules.append(node.module)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.modules.append(alias.name)


def runtime_imports(path: Path) -> list[str]:
    """Module names imported at runtime by ``path`` (TYPE_CHECKING-only excluded)."""
    visitor = _RuntimeImports()
    visitor.visit(parse(path))
    return visitor.modules
