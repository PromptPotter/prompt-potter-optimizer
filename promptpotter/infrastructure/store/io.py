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
    """The Windows long-path prefix bypasses ``MAX_PATH=260`` without the registry's
    ``LongPathsEnabled`` — sweep-fork audit dirs nest past it. No-op on POSIX."""
    s = str(p)
    if os.name != "nt":
        return s
    if s.startswith(("\\\\?\\", "\\\\.\\")):
        return s
    return "\\\\?\\" + os.path.abspath(s)


def ensure_parent_dir(path: Path) -> None:
    os.makedirs(_long_path(path.parent), exist_ok=True)


def unlink_robust(path: Path) -> None:
    """Delete one FILE — the same read-only chmod dance :func:`rmtree_robust` does, split from it
    only by arity. Missing is success; anything else the caller must see."""
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)
        path.unlink()


def rmtree_robust(path: Path) -> None:
    """Delete a tree — long-path safe, read-only tolerant, retried. **The one deleter.**
    Raises on genuine failure: a sandbox that could not be reclaimed is a fact its caller needs."""

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
    """Atomically swap *tmp* onto *path*, long-path safe. Windows can fail with WinError 5 while a
    reader holds the destination, so it retries; POSIX never reaches that branch."""
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
    _atomic_write(path, lambda f: json.dump(data, f, indent=2, ensure_ascii=False, default=default))


def write_text(path: Path, content: str) -> None:
    _atomic_write(path, lambda f: f.write(content))


def read_json(path: Path) -> Any:
    with open(_long_path(path), encoding="utf-8") as f:
        return json.load(f)


def read_json_optional(path: Path) -> Any | None:
    try:
        return read_json(path)
    except FileNotFoundError:
        return None


def read_json_tolerant(path: Path, default: Any = None) -> Any:
    try:
        return read_json(path)
    except (OSError, json.JSONDecodeError):
        return default


def read_text_optional(path: Path, default: str = "") -> str:
    try:
        with open(_long_path(path), encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return default


_YAML_FOLD_OVER = 90


class _YamlDumper(yaml.SafeDumper):
    """``SafeDumper``, so an enum / datetime / arbitrary object raises instead of serialising to
    something no editor can read back."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        # Indent sequence items under their key. PyYAML hangs them at the parent's
        # column by default, which puts a list outside the block an editor folds.
        super().increase_indent(flow, False)


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    """Picks literal / folded / plain so prose wraps instead of running off the edge. It NEVER
    rewrites the value — these strings hash into measurement identity, so ugly output stays."""
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
    """Write *data* as block-scalar YAML atomically. ``sort_keys=False`` because declaration order
    is meaning here — a pipeline's node order, a schema's field order."""
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
    try:
        return read_yaml(path)
    except FileNotFoundError:
        return None


def append_jsonl(path: Path, item: dict[str, Any]) -> Path:
    ensure_parent_dir(path)
    with open(_long_path(path), "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
        f.flush()
    return path


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """The whole-file peer of :func:`append_jsonl` — compaction / reindex replace a log with its
    live rows through the temp-file + atomic-replace path."""
    _atomic_write(
        path,
        lambda f: f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
    )


def newest_mtime_ns(*paths: Path) -> int | None:
    """Newest ``st_mtime_ns`` across *paths*; missing skipped, all missing → ``None``. Nanoseconds,
    not float seconds: the float collides on a same-tick append and serves a spurious 304."""
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
    "read_yaml",
    "read_yaml_optional",
    "rmtree_robust",
    "unlink_robust",
    "validate_path_component",
    "write_json",
    "write_jsonl",
    "write_text",
    "write_yaml",
]
