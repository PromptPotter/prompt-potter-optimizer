"""Fail loudly when a `.ai/CODEMAP.md` `symbol → file:line` citation goes stale.

`build_ai_index.py` regenerates `.ai/SYMBOLS.txt` but never touches the
hand-written CODEMAP tables, so a refactor that moves a symbol silently
re-rots the AI-navigation map. This check parses every full-path
`symbol → file:line` row from the CODEMAP backbone tables and asserts the
symbol still resolves at (or near) the cited line.

Run from the repo root:

    python scripts/check_codemap.py

Exit 0 = every checked citation resolves. Exit 1 = at least one is stale.
Rows with abbreviated paths, dotted/decorated symbols, or multiple
symbols / citations are skipped — this check trades coverage for zero
false positives. The backbone symbol-index tables are full-path and
carry the bulk of the citations an AI agent navigates by.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

CODEMAP = Path(".ai") / "CODEMAP.md"

# A symbol may legitimately drift a few lines under an unrelated edit; only
# a wrong file, an absent symbol, or a gross line jump should fail.
LINE_TOLERANCE = 5

# Only full-path citations rooted at a real package dir are resolvable
# unambiguously; abbreviated paths (`dispatch/hub/facade.py`) are skipped.
_PY_CITATION = re.compile(r"((?:promptpotter|tests)/[\w./]+\.py):(\d+)")
_BACKTICK = re.compile(r"`([^`]+)`")


def _toplevel_symbols(path: Path) -> dict[str, list[int]]:
    """Map every top-level def / class / assignment name to its line number(s)."""
    out: dict[str, list[int]] = {}
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def add(name: str, lineno: int) -> None:
        out.setdefault(name, []).append(lineno)

    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            add(node.name, node.lineno)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    add(tgt.id, node.lineno)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            add(node.target.id, node.lineno)
    return out


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    codemap = repo_root / CODEMAP
    if not codemap.is_file():
        print(f"error: {CODEMAP} not found", file=sys.stderr)
        return 1

    symbol_cache: dict[Path, dict[str, list[int]]] = {}
    checked = 0
    stale: list[str] = []

    for raw in codemap.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        symbol_cell, citation_cell = cells[0], cells[-1]

        symbols = _BACKTICK.findall(symbol_cell)
        citations = _PY_CITATION.findall(citation_cell)
        # One clean symbol, one full-path citation — skip the messy rows.
        if len(symbols) != 1 or len(citations) != 1:
            continue
        symbol = symbols[0]
        if not symbol.isidentifier():
            continue
        rel_path, line_str = citations[0]
        cited_line = int(line_str)

        target = repo_root / rel_path
        if not target.is_file():
            stale.append(f"{symbol}: cited file {rel_path} does not exist")
            continue
        if target not in symbol_cache:
            symbol_cache[target] = _toplevel_symbols(target)
        locs = symbol_cache[target].get(symbol)
        checked += 1
        if not locs:
            stale.append(f"{symbol}: not a top-level symbol in {rel_path}")
        elif not any(abs(loc - cited_line) <= LINE_TOLERANCE for loc in locs):
            found = ", ".join(f"{rel_path}:{x}" for x in locs)
            stale.append(f"{symbol}: cited {rel_path}:{cited_line} but found at {found}")

    if stale:
        print("CODEMAP staleness - fix the rows in .ai/CODEMAP.md:", file=sys.stderr)
        for entry in stale:
            print(f"  {entry}", file=sys.stderr)
        print(f"\n{len(stale)} stale / {checked} checked", file=sys.stderr)
        return 1
    print(f"CODEMAP ok - {checked} symbol citations resolve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
