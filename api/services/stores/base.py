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
    except Exception:
        # Clean up temp file on failure (fd already closed by os.fdopen)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_text(path: Path, content: str) -> None:
    """Write *content* to *path*, creating parent directories if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def append_text(path: Path, line: str) -> None:
    """Append *line* to *path*, creating parent directories if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


def write_yaml_kv(path: Path, data: dict) -> None:
    """Write *data* as YAML-compatible ``key: value`` lines.

    Handles None → ``null``, bools → lowercase, lists → JSON arrays.
    Used for MLflow meta.yaml files.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for key, value in data.items():
            f.write(f"{key}: {_yaml_value(value)}\n")


def _yaml_value(v: Any) -> str:
    """Format a Python value for YAML key-value output."""
    if isinstance(v, str):
        return f'"{v}"'
    if v is None:
        return "null"
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, list):
        return json.dumps(v)
    return str(v)


def read_json(path: Path) -> Any:
    """Read and parse JSON from *path*."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_json_optional(path: Path) -> Any | None:
    """Read JSON from *path*, returning ``None`` if it does not exist."""
    if not path.exists():
        return None
    return read_json(path)


def append_jsonl(path: Path, item: dict) -> Path:
    """Append one JSON object as a line to a JSONL file.

    Creates parent directories if needed.  Returns *path*.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
        f.flush()
    return path


def read_jsonl(path: Path) -> list[dict]:
    """Read all JSON objects from a JSONL file.

    Returns an empty list if *path* does not exist.
    """
    if not path.exists():
        return []
    items: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


