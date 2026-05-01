"""Shared I/O helpers for file-based stores."""

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
    """Validate that *name* is safe for use as a filesystem path component."""
    if not name or not _SAFE_PATH_RE.match(name):
        raise ValueError(
            f"Invalid path component: {name!r}. "
            "Only alphanumerics, hyphens, underscores, and dots are allowed."
        )
    return name


def _long_path(p: str | Path) -> str:
    r"""Return *p* with the Windows ``\\?\`` long-path prefix applied.

    Windows ``MAX_PATH`` is 260 chars. Sweep-fork audit dirs nest deep
    (``campaigns/{root}/sweeps/{batch}/forks/{cycle}_sweep_{batch}_{hash}/langfuse/traces/{32hex}.json``)
    and the trailing 32-hex filename routinely pushes the full path past
    260, which makes ``CreateFileW`` / ``CreateDirectoryW`` / ``MoveFileExW``
    fail with ``ERROR_PATH_NOT_FOUND`` (WinError 3) unless ``LongPathsEnabled``
    is set in the registry. Prefixing an absolute path with ``\\?\`` bypasses
    the limit without touching the registry. No-op on POSIX.
    """
    s = str(p)
    if os.name != "nt":
        return s
    if s.startswith(("\\\\?\\", "\\\\.\\")):
        return s
    return "\\\\?\\" + os.path.abspath(s)


def _ensure_parent(path: Path) -> None:
    """``mkdir(parents=True, exist_ok=True)`` for *path*'s parent, long-path safe."""
    os.makedirs(_long_path(path.parent), exist_ok=True)


def write_json(
    path: Path,
    data: Any,
    *,
    default: Callable[[Any], Any] | None = None,
) -> None:
    """Write *data* as pretty-printed JSON atomically.

    Writes to a temp file in the same directory, then uses ``os.replace()``
    to atomically swap it into place. This prevents partial/corrupt files
    if the process crashes mid-write. Both args of ``os.replace`` are
    routed through :func:`_long_path` so destinations past Windows
    ``MAX_PATH=260`` succeed.

    On Windows, ``os.replace()`` can also fail with ``PermissionError``
    (WinError 5) when the destination is briefly held by another process —
    OneDrive sync, antivirus, a stale reader. Retry once with 100 ms
    back-off; POSIX never hits the retry branch.

    ``default`` is forwarded to ``json.dump`` for non-native types (e.g.
    pass ``str`` to coerce enums / datetimes to their ``str`` form).
    """
    import time

    _ensure_parent(path)
    fd, tmp = tempfile.mkstemp(dir=_long_path(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=default)
        last_exc: OSError | None = None
        for attempt in range(3):
            try:
                os.replace(_long_path(tmp), _long_path(path))
                return
            except PermissionError as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(0.1)
        if last_exc is not None:
            raise last_exc
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def write_text(path: Path, content: str) -> None:
    """Write *content* to *path*, creating parent directories if needed."""
    _ensure_parent(path)
    with open(_long_path(path), "w", encoding="utf-8") as f:
        f.write(content)


def append_text(path: Path, line: str) -> None:
    """Append *line* to *path*, creating parent directories if needed."""
    _ensure_parent(path)
    with open(_long_path(path), "a", encoding="utf-8") as f:
        f.write(line)


def write_yaml_kv(path: Path, data: dict) -> None:
    """Write *data* as YAML-compatible ``key: value`` lines.

    Handles None → ``null``, bools → lowercase, lists → JSON arrays.
    Used for MLflow meta.yaml files.
    """
    _ensure_parent(path)
    with open(_long_path(path), "w", encoding="utf-8") as f:
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
    with open(_long_path(path), encoding="utf-8") as f:
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
    _ensure_parent(path)
    with open(_long_path(path), "a", encoding="utf-8") as f:
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
