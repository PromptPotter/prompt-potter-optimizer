"""Shared I/O helpers for file-based stores."""

import contextlib
import json
import os
import re
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

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
    r"""Apply the Windows ``\\?\`` long-path prefix.

    Sweep-fork audit dirs nest past Windows ``MAX_PATH=260``, breaking
    ``CreateFileW``/``CreateDirectoryW``/``MoveFileExW`` with WinError 3
    unless ``LongPathsEnabled`` is set in the registry. The ``\\?\`` prefix
    bypasses the limit without the registry change. No-op on POSIX.
    """
    s = str(p)
    if os.name != "nt":
        return s
    if s.startswith(("\\\\?\\", "\\\\.\\")):
        return s
    return "\\\\?\\" + os.path.abspath(s)


def ensure_parent_dir(path: Path) -> None:
    """``mkdir(parents=True, exist_ok=True)`` for *path*'s parent, long-path safe."""
    os.makedirs(_long_path(path.parent), exist_ok=True)


def _atomic_replace(tmp: str, path: Path) -> None:
    """Atomically swap *tmp* onto *path*, long-path safe.

    On Windows ``os.replace`` can fail with WinError 5 when the destination is
    held briefly (OneDrive / antivirus / stale reader); retry twice with 100ms
    back-off. POSIX never hits the retry branch.
    """
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


def _atomic_write(path: Path, write_fn: Callable[[IO[str]], object]) -> None:
    """Atomic text write: create parent dirs, write via a temp file, then
    :func:`_atomic_replace`. ``write_fn`` receives the open file and does the
    format-specific write (json.dump / f.write). On any error the temp file is
    removed and the original is left untouched.
    """
    ensure_parent_dir(path)
    fd, tmp = tempfile.mkstemp(dir=_long_path(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            write_fn(f)
        _atomic_replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def write_json(
    path: Path,
    data: Any,
    *,
    default: Callable[[Any], Any] | None = None,
) -> None:
    """Write *data* as pretty JSON atomically.

    ``default`` forwards to ``json.dump`` for non-native types (e.g. ``str`` to coerce enums/datetimes).
    """
    _atomic_write(path, lambda f: json.dump(data, f, indent=2, ensure_ascii=False, default=default))


def write_text(path: Path, content: str) -> None:
    """Write *content* atomically; creates parent dirs."""
    _atomic_write(path, lambda f: f.write(content))


def read_json(path: Path) -> Any:
    """Read and parse JSON from *path*."""
    with open(_long_path(path), encoding="utf-8") as f:
        return json.load(f)


def read_json_optional(path: Path) -> Any | None:
    """Read JSON from *path*, returning ``None`` if it does not exist."""
    try:
        return read_json(path)
    except FileNotFoundError:
        return None


def read_json_tolerant(path: Path, default: Any = None) -> Any:
    """Read JSON from *path*, returning *default* if it is missing OR corrupt.

    The corruption-tolerant peer of :func:`read_json_optional` (which catches only a
    missing file): a truncated / half-written JSON file returns *default* instead of
    raising. Use for best-effort reads of caches, pointers, and prior-state snapshots
    where a damaged file should degrade to "not there", not crash the caller.
    """
    try:
        return read_json(path)
    except (OSError, json.JSONDecodeError):
        return default


def read_text_optional(path: Path, default: str = "") -> str:
    """Read text from *path*, returning ``default`` if it does not exist."""
    try:
        with open(_long_path(path), encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return default


def append_jsonl(path: Path, item: dict[str, Any]) -> Path:
    """Append one JSON object as a line to a JSONL file.

    Creates parent directories if needed.  Returns *path*.
    """
    ensure_parent_dir(path)
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


__all__ = [
    "EntityStore",
    "append_jsonl",
    "ensure_parent_dir",
    "read_json",
    "read_json_optional",
    "read_text_optional",
    "validate_path_component",
    "write_json",
    "write_text",
]
