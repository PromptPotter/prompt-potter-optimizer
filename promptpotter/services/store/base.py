"""
Shared I/O helpers for file-based stores.
"""

import contextlib
import json
import os
import re
import tempfile
from collections.abc import Callable
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


def write_json(
    path: Path,
    data: Any,
    *,
    default: Callable[[Any], Any] | None = None,
) -> None:
    """Write *data* as pretty-printed JSON atomically.

    Writes to a temp file in the same directory, then uses ``os.replace()``
    to atomically swap it into place.  This prevents partial/corrupt files
    if the process crashes mid-write.

    ``default`` is forwarded to ``json.dump`` for non-native types (e.g.
    pass ``str`` to coerce enums / datetimes to their ``str`` form).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=default)
        os.replace(tmp, path)
    except Exception:
        # Clean up temp file on failure (fd already closed by os.fdopen)
        with contextlib.suppress(OSError):
            os.unlink(tmp)
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


class EntityStore:
    """Generic JSON entity store: ``{backend_id}/{subdir}/{entity_id}.json``.

    Subclasses set *subdir* and may add domain-specific methods.
    """

    def __init__(self, base_dir: Path, subdir: str):
        self._base_dir = base_dir
        self._subdir = subdir

    def _entity_dir(self, backend_id: str) -> Path:
        validate_path_component(backend_id)
        return self._base_dir / backend_id / self._subdir

    def _entity_path(self, backend_id: str, entity_id: str) -> Path:
        validate_path_component(entity_id)
        return self._entity_dir(backend_id) / f"{entity_id}.json"

    def save(self, backend_id: str, entity_id: str, data: dict[str, Any]) -> Path:
        path = self._entity_path(backend_id, entity_id)
        write_json(path, data)
        return path

    def load(self, backend_id: str, entity_id: str) -> dict[str, Any] | None:
        return read_json_optional(self._entity_path(backend_id, entity_id))

    def update(self, backend_id: str, entity_id: str, updates: dict[str, Any]) -> None:
        path = self._entity_path(backend_id, entity_id)
        data = read_json(path)
        data.update(updates)
        write_json(path, data)
