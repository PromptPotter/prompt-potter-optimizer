"""Regenerate ``.ai/SYMBOLS.txt`` — flat ``symbol\\tfile:line`` over top-level
``class``/``def`` in ``promptpotter/`` + ``tests/``. Idempotent; re-run after large refactors."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOTS = ("promptpotter", "tests")
OUTPUT = Path(".ai") / "SYMBOLS.txt"


def collect(path: Path) -> list[tuple[str, str, int]]:
    rows: list[tuple[str, str, int]] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as exc:
        print(f"skip {path}: {exc}", file=sys.stderr)
        return rows
    rel = path.as_posix()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_"):
                continue
            rows.append((node.name, rel, node.lineno))
    return rows


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    rows: list[tuple[str, str, int]] = []
    for root_name in ROOTS:
        root = repo_root / root_name
        if not root.is_dir():
            print(f"warning: {root_name}/ not found", file=sys.stderr)
            continue
        for py in sorted(root.rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            rows.extend(collect(py.relative_to(repo_root)))

    rows.sort(key=lambda r: (r[0].lower(), r[1], r[2]))

    out_path = repo_root / OUTPUT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{name}\t{rel}:{lineno}" for name, rel, lineno in rows]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out_path} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
