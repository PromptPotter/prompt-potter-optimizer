"""
Shared I/O helpers for file-based stores.
"""
import json
import re
from pathlib import Path
from typing import Any

_SAFE_PATH_RE = re.compile(r"^[a-zA-Z0-9_\-\.]+$")


def validate_path_component(name: str) -> str:
    """Validate that *name* is safe for use as a filesystem path component.

    Raises ValueError on traversal attempts or disallowed characters.
    """
    if not name or not _SAFE_PATH_RE.match(name):
        raise ValueError(
            f"Invalid path component: {name!r}. "
            "Only alphanumerics, hyphens, underscores, and dots are allowed."
        )
    return name


def write_json(path: Path, data: Any) -> None:
    """Write *data* as pretty-printed JSON, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def read_json(path: Path) -> Any:
    """Read and parse JSON from *path*."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)
