"""Shared I/O helpers for file-based stores."""

import contextlib
import itertools
import json
import os
import re
import shutil
import stat
import tempfile
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import IO, Any

import yaml

_SAFE_PATH_RE = re.compile(r"^[a-zA-Z0-9_\-\.]+$")


def validate_path_component(name: str) -> str:
    # An all-dots component (``.``/``..``/``...``) matches the dot-allowing regex
    # but is a traversal segment — reject it so a user-supplied id/slug/filename
    # can never climb out of the dir the caller rooted it under.
    if not name or set(name) == {"."} or not _SAFE_PATH_RE.match(name):
        raise ValueError(
            f"Invalid path component: {name!r}. "
            "Only alphanumerics, hyphens, underscores, and dots are allowed "
            "(and not an all-dots traversal segment)."
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


def rmtree_robust(path: Path) -> None:
    r"""Delete a tree — long-path safe, read-only tolerant, retried. **The one deleter.**

    Every recursive delete in the package routes here, because a bare
    ``shutil.rmtree`` cannot remove the trees this package actually writes. An L4
    inner sandbox nests langfuse observation dirs past ``MAX_PATH=260`` — measured
    at 668 chars — and a plain ``rmtree`` fails on them; with ``ignore_errors=True``
    it fails *silently* and leaves the tree on disk, which is exactly how
    ``.inner/`` reached 343 MB (60% of the store) with no code path able to reclaim
    it. The ``\\?\`` prefix (``_long_path``) removes the same tree without a
    registry change; the chmod dance handles Windows read-only bits, and the
    backoff handles a virus scanner or an editor still holding a handle.

    Raises on genuine failure rather than swallowing — a sandbox that could not be
    reclaimed is a fact its caller has to be able to see.
    """

    def _onexc(func: Callable[[str], object], target: str, exc: BaseException) -> None:
        if isinstance(exc, PermissionError):
            try:
                os.chmod(target, stat.S_IWRITE)
                func(target)
                return
            except OSError:
                pass
        raise exc

    target = _long_path(path)
    for attempt in range(4):
        try:
            shutil.rmtree(target, onexc=_onexc)
            return
        except OSError:
            if attempt == 3:
                raise
            time.sleep(0.1 * (attempt + 1))


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


_YAML_FOLD_OVER = 90


class _YamlDumper(yaml.SafeDumper):
    """Emitter for the operator-authored config tier.

    ``SafeDumper``, so an enum / datetime / arbitrary object raises instead of
    serialising to something no editor can read back.
    """

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        # Indent sequence items under their key. PyYAML hangs them at the parent's
        # column by default, which puts a list outside the block an editor folds.
        super().increase_indent(flow, False)


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    """Pick the scalar style that makes this string readable.

    The whole point of the config tier being YAML: prose wraps and folds instead of
    running off the right edge as one unbreakable line.

    - stacked non-blank lines (bullet lists, an embedded JSON template) → literal
      ``|``, because wrapping them would destroy the shape that carries the meaning;
    - long one-liners and blank-line-separated paragraphs → folded ``>``, which wraps
      at the emitter width and keeps the blank lines;
    - everything short → plain.

    **Never rewrites the value.** A line with trailing whitespace or a tab makes PyYAML
    abandon block style and emit a double-quoted blob — losslessly, so nothing breaks,
    it just reads badly. Normalising it here would be a silent edit to strings that are
    hashed into measurement identity (`combined_optimizer_prompt_hash`), so the ugly
    output is left visible and `test_integrity` fails on it instead.
    """
    lines = data.split("\n")
    if len([ln for ln in lines if ln.strip()]) <= 1:
        style = ">" if len(data) > _YAML_FOLD_OVER else None
    elif any(a.strip() and b.strip() for a, b in itertools.pairwise(lines)):
        style = "|"
    elif any(len(ln) > _YAML_FOLD_OVER for ln in lines):
        style = ">"
    else:
        style = "|"
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_YamlDumper.add_representer(str, _represent_str)


def write_yaml(path: Path, data: Any) -> None:
    """Write *data* as block-scalar YAML atomically — the operator-authored tier.

    Format policy lives here, not at the call sites, exactly as :func:`write_json`
    owns ``indent=2``. ``sort_keys=False`` because declaration order is meaning in
    these files (a pipeline's node order, a schema's field order).
    """
    _atomic_write(
        path,
        lambda f: yaml.dump(
            data,
            f,
            Dumper=_YamlDumper,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
            width=100,
            indent=2,
        ),
    )


def read_yaml(path: Path) -> Any:
    with open(_long_path(path), encoding="utf-8") as f:
        return yaml.safe_load(f)


def read_yaml_optional(path: Path) -> Any | None:
    """Read YAML from *path*, returning ``None`` if it does not exist.

    There is deliberately no ``read_yaml_tolerant``. A corrupt config is not a cold
    cache: degrading it to "not there" attributes a measurement to the wrong
    fingerprint, silently. Let it raise.
    """
    try:
        return read_yaml(path)
    except FileNotFoundError:
        return None


def append_jsonl(path: Path, item: dict[str, Any]) -> Path:
    """Creates parent directories if needed; returns *path*."""
    ensure_parent_dir(path)
    with open(_long_path(path), "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
        f.flush()
    return path


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """Atomically (re)write a JSONL file — one compact object per line.

    The whole-file peer of :func:`append_jsonl`, used by compaction / reindex to
    replace a log with its live rows via the temp-file + atomic-replace path.
    """
    _atomic_write(
        path,
        lambda f: f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
    )


def newest_mtime_ns(*paths: Path) -> int | None:
    """Newest ``st_mtime_ns`` across *paths*; missing files skipped, all missing → None.

    Nanoseconds, not float seconds: a conditional-GET validator built on the float can
    collide for a same-tick append and serve a spurious 304.
    """
    newest: int | None = None
    for p in paths:
        try:
            m = p.stat().st_mtime_ns
        except OSError:
            continue
        if newest is None or m > newest:
            newest = m
    return newest


__all__ = [
    "append_jsonl",
    "ensure_parent_dir",
    "newest_mtime_ns",
    "read_json",
    "read_json_optional",
    "read_json_tolerant",
    "read_text_optional",
    "read_yaml",
    "read_yaml_optional",
    "rmtree_robust",
    "validate_path_component",
    "write_json",
    "write_jsonl",
    "write_text",
    "write_yaml",
]
