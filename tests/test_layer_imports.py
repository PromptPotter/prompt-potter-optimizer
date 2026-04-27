"""Hexagonal layer-import rule guard.

Named invariant from root CLAUDE.md: ``Hexagonal layout: domain/ ... ->
application/ ... -> infrastructure/ -> presentation/`` and the strict
directionality rule ``intelligence/ MUST NOT import from optimization/``.

This test walks the source AST and asserts the rules at runtime-import
level only — ``if TYPE_CHECKING:`` blocks are skipped because those imports
never execute and don't create module-level coupling.

When a violation is intentional (e.g. pending a structural rework that will
land in a later phase), add it to ``KNOWN_VIOLATIONS`` with a TODO pointer.
The test fails both on un-listed violations AND on listed-but-disappeared
entries, so the allowlist stays accurate.
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).parent.parent / "promptpotter"


# Documented runtime cross-layer imports. The codebase is currently clean
# at runtime; this allowlist exists so that any *intentional* future
# violation can be tracked here with a TODO pointer rather than slipping
# in silently. Stale entries fail the test, so the list cannot drift.
KNOWN_VIOLATIONS: frozenset[tuple[str, str]] = frozenset()


def _layer(rel_posix: str) -> str | None:
    """Map a source file path to its hexagonal layer."""
    if "/domain/" in rel_posix:
        return "domain"
    if "/application/intelligence/" in rel_posix:
        return "intelligence"
    if "/application/optimization/" in rel_posix:
        return "optimization"
    if "/application/" in rel_posix:
        return "application"
    if "/infrastructure/" in rel_posix:
        return "infrastructure"
    if "/presentation/" in rel_posix:
        return "presentation"
    return None


def _target_layer(module: str) -> str | None:
    """Map an imported promptpotter module to its hexagonal layer."""
    if module.startswith("promptpotter.domain"):
        return "domain"
    if module.startswith("promptpotter.application.intelligence"):
        return "intelligence"
    if module.startswith("promptpotter.application.optimization"):
        return "optimization"
    if module.startswith("promptpotter.application"):
        return "application"
    if module.startswith("promptpotter.infrastructure"):
        return "infrastructure"
    if module.startswith("promptpotter.presentation"):
        return "presentation"
    return None


def _is_violation(src: str, tgt: str) -> bool:
    """The runtime-import rules. Tightest at the bottom (domain), loosest at the top."""
    if src == "domain" and tgt != "domain":
        return True
    if src == "intelligence" and tgt == "optimization":
        return True
    return src == "infrastructure" and tgt in {"application", "intelligence", "optimization"}


class _RuntimeImports(ast.NodeVisitor):
    """Collect imports, skipping ``if TYPE_CHECKING:`` blocks."""

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


def _scan_violations() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT.parent).as_posix()
        src_layer = _layer(rel)
        if src_layer is None:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        visitor = _RuntimeImports()
        visitor.visit(tree)
        for module in visitor.modules:
            tgt_layer = _target_layer(module)
            if tgt_layer is None:
                continue
            if _is_violation(src_layer, tgt_layer):
                found.add((rel, module))
    return found


def test_no_unexpected_runtime_layer_violations() -> None:
    found = _scan_violations()
    new = found - KNOWN_VIOLATIONS
    stale = KNOWN_VIOLATIONS - found
    assert not new, (
        "New runtime layer-import violations detected. "
        "Either fix the import, or — if intentional and pending a rework — add it "
        "to KNOWN_VIOLATIONS with a TODO pointer.\nNew violations:\n  "
        + "\n  ".join(f"{src}: {tgt}" for src, tgt in sorted(new))
    )
    assert not stale, (
        "KNOWN_VIOLATIONS contains entries that no longer occur in the source. "
        "Remove them to keep the allowlist accurate.\nStale entries:\n  "
        + "\n  ".join(f"{src}: {tgt}" for src, tgt in sorted(stale))
    )
