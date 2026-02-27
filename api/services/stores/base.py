"""
Shared I/O helpers for file-based stores.
"""
import json
import os
import re
import tempfile
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
    """Write *data* as pretty-printed JSON atomically.

    Writes to a temp file in the same directory, then uses ``os.replace()``
    to atomically swap it into place.  This prevents partial/corrupt files
    if the process crashes mid-write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        # Clean up temp file on failure (fd already closed by os.fdopen)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path) -> Any:
    """Read and parse JSON from *path*."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)
