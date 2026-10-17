import contextlib
import itertools
import json
import os
import shutil
import stat
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import IO, Any, NamedTuple

import yaml
from filelock import FileLock

from promptpotter.config.settings import LOCK_TIMEOUT
from promptpotter.domain.cycle_paths import ALL_DOTS_RE, ID_COMPONENT_RE
from promptpotter.shared.errors import ConflictError


def validate_path_component(name: str) -> str:
    # An all-dots component (`.`/`..`) matches the dot-allowing regex but is a traversal segment.
    if not name or ALL_DOTS_RE.match(name) or not ID_COMPONENT_RE.match(name):
        raise ValueError(
            f"Invalid path component: {name!r}. "
            "Only alphanumerics, hyphens, underscores, and dots are allowed "
            "(and not an all-dots traversal segment)."
        )
    return name


def _long_path(p: str | Path) -> str:
    """The Windows long-path prefix bypasses `MAX_PATH=260`, which nested sandbox dirs exceed."""
    s = str(p)
    if os.name != "nt":
        return s
    if s.startswith(("\\\\?\\", "\\\\.\\")):
        return s
    return "\\\\?\\" + os.path.abspath(s)


def unlink_robust(path: Path) -> None:
    for attempt in range(4):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt == 3:
                raise
            with contextlib.suppress(OSError):
                os.chmod(path, stat.S_IWRITE)
            time.sleep(0.05 * attempt)


def open_text_robust(path: Path) -> IO[str]:
    """Retried: Windows refuses for the instant another process swaps the file (`_atomic_replace`)."""
    for attempt in range(3):
        try:
            return open(_long_path(path), encoding="utf-8")
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
    return open(_long_path(path), encoding="utf-8")


def rmtree_robust(path: Path) -> None:
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


def iter_files(
    root: Path, *, skip: frozenset[str] = frozenset()
) -> Iterator[tuple[Path, os.stat_result]]:
    stack = [root]
    first = True
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if not (first and entry.name in skip):
                                stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            yield Path(entry.path), entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
        except OSError:
            continue
        first = False


def _atomic_replace(tmp: str, path: Path) -> None:
    """Retried: Windows fails with WinError 5 while a reader holds the destination."""
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


def _tmp_beside(path: Path) -> tuple[int, str]:
    parent = _long_path(path.parent)
    os.makedirs(parent, exist_ok=True)
    # Dot-named, which the cycle file listing skips.
    return tempfile.mkstemp(dir=parent, prefix=".", suffix=".tmp")


def _atomic_write(path: Path, write_fn: Callable[[IO[str]], object]) -> None:
    fd, tmp = _tmp_beside(path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            write_fn(f)
        _atomic_replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def write_bytes(path: Path, data: bytes) -> None:
    fd, tmp = _tmp_beside(path)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
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
    text = json.dumps(data, indent=2, ensure_ascii=False, default=default)
    _atomic_write(path, lambda f: f.write(text))


def write_text(path: Path, content: str) -> None:
    _atomic_write(path, lambda f: f.write(content))


def read_bytes_optional(path: Path) -> bytes | None:
    try:
        with open(_long_path(path), "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


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
    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        super().increase_indent(flow, False)


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    """Picks the scalar STYLE only and never rewrites the value: these strings hash into measurement identity."""
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
    _atomic_write(
        path,
        lambda f: yaml.dump(
            data,
            f,
            Dumper=_YamlDumper,
            # Load-bearing: declaration order is meaning (node order, schema field order).
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
            width=100,
            indent=2,
        ),
    )


_YAML_LOADER: type = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def read_yaml(path: Path) -> Any:
    """`yaml.YAMLError` is no `ValueError`, which every guard in this tree catches, so it is wrapped here."""
    try:
        with open(_long_path(path), encoding="utf-8") as f:
            return yaml.load(f, Loader=_YAML_LOADER)
    except yaml.YAMLError as exc:
        raise ValueError(f"{path} is not valid YAML — {exc}") from exc


def read_yaml_optional(path: Path) -> Any | None:
    try:
        return read_yaml(path)
    except FileNotFoundError:
        return None


def _write_lock(path: Path) -> FileLock:
    """Windows runs `O_APPEND` as seek-then-write, so two unlocked appends overwrite one record."""
    # Dot-named, which the cycle file listing skips; acquiring it makes the directory.
    return FileLock(_long_path(path.with_name(f".{path.name}.lock")), timeout=LOCK_TIMEOUT)


def append_line(path: Path, line: bytes) -> range:
    """Reopened per call, never held: a held handle blocks a stub fork's delete on Windows."""
    data = line + b"\n"
    with _write_lock(path), open(_long_path(path), "ab") as f:
        f.write(data)
        f.flush()
        end = f.tell()
    return range(end - len(data), end)


def append_jsonl(path: Path, item: dict[str, Any]) -> Path:
    append_line(path, json.dumps(item, ensure_ascii=False).encode("utf-8"))
    return path


class _FileMark(NamedTuple):
    end: int
    # `None`: the file was absent, so whatever an append has put there since is all tail.
    inode: int | None


def _read_marked(path: Path) -> tuple[list[str], _FileMark]:
    lines: list[str] = []
    end, inode = 0, None
    with contextlib.suppress(FileNotFoundError), open(_long_path(path), "rb") as f:
        inode = os.fstat(f.fileno()).st_ino
        for raw in f:
            # An unterminated line is an append in flight: the swap carries it past `end`.
            if not raw.endswith(b"\n"):
                break
            lines.append(raw.decode("utf-8"))
            end += len(raw)
    return lines, _FileMark(end, inode)


def read_lines(path: Path) -> list[str]:
    return _read_marked(path)[0]


def _end_mark(path: Path) -> _FileMark:
    # Locked, so no append is in flight and the whole file is whole lines.
    with _write_lock(path):
        try:
            st = os.stat(_long_path(path))
        except FileNotFoundError:
            return _FileMark(0, None)
    return _FileMark(st.st_size, st.st_ino)


class PairWrite(NamedTuple):
    """A line file and its companion rewritten together; ``None`` is an absent companion."""

    lines: Iterable[str]
    # What the companion holds while the lines swap, so it must carry every fact either line state leans on.
    bridge: bytes | None
    companion: bytes | None


class _Companion(NamedTuple):
    path: Path
    held: bytes | None
    write: PairWrite


def _put(path: Path, data: bytes | None, *, held: bytes | None) -> None:
    if data == held:
        return
    if data is None:
        unlink_robust(path)
    else:
        write_bytes(path, data)


def _swap(path: Path, mark: _FileMark, lines: Iterable[str], companion: _Companion | None) -> bool:
    data = "".join(lines).encode("utf-8")
    # Only the swap is locked: a big rewrite outlasts `LOCK_TIMEOUT`, where an appender gives up.
    with _write_lock(path):
        try:
            with open(_long_path(path), "rb") as f:
                st = os.fstat(f.fileno())
                if mark.inode not in (None, st.st_ino) or st.st_size < mark.end:
                    return False
                f.seek(mark.end)
                tail = f.read()
        except FileNotFoundError:
            if mark.inode is not None:
                return False
            tail = b""
        if companion is None:
            write_bytes(path, data + tail)
            return True
        if read_bytes_optional(companion.path) != companion.held:
            return False
        _put(companion.path, companion.write.bridge, held=companion.held)
        write_bytes(path, data + tail)
        _put(companion.path, companion.write.companion, held=companion.write.bridge)
    return True


_REWRITE_ATTEMPTS = 3


class _Attempt[T](NamedTuple):
    mark: _FileMark
    lines: Iterable[str] | None
    companion: _Companion | None
    result: T


def _retry_swap[T](path: Path, attempt: Callable[[], _Attempt[T]]) -> T:
    for _ in range(_REWRITE_ATTEMPTS):
        mark, lines, companion, result = attempt()
        if lines is None or _swap(path, mark, lines, companion):
            return result
    raise ConflictError(
        f"{path.name} was rewritten by another writer during each of {_REWRITE_ATTEMPTS} "
        "attempts, so this rewrite was not applied. Try again."
    )


def rewrite_lines[T](
    path: Path, transform: Callable[[list[str]], tuple[Iterable[str] | None, T]]
) -> T:
    """*transform* reruns on a lost race, so it returns its result and never accumulates one;
    ``None`` lines write nothing."""

    def attempt() -> _Attempt[T]:
        held, mark = _read_marked(path)
        lines, result = transform(held)
        return _Attempt(mark, lines, None, result)

    return _retry_swap(path, attempt)


def rewrite_pair[T](
    path: Path,
    companion: Path,
    transform: Callable[[list[str], bytes | None], tuple[PairWrite | None, T]],
) -> T:
    """`rewrite_lines` with a companion no appender touches, swapped under the same lock and refused if
    it changed since read: bridge, then lines, then its final bytes, so no instant holds a fact in neither."""

    def attempt() -> _Attempt[T]:
        held, mark = _read_marked(path)
        beside = read_bytes_optional(companion)
        write, result = transform(held, beside)
        if write is None:
            return _Attempt(mark, None, None, result)
        return _Attempt(mark, write.lines, _Companion(companion, beside, write), result)

    return _retry_swap(path, attempt)


def replace_lines[T](path: Path, build: Callable[[], tuple[Iterable[str], T]]) -> T:
    """`rewrite_lines` for lines built elsewhere: only what lands past the end mark is read, and carried."""

    def attempt() -> _Attempt[T]:
        mark = _end_mark(path)
        lines, result = build()
        return _Attempt(mark, lines, None, result)

    return _retry_swap(path, attempt)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    _atomic_write(
        path,
        lambda f: f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
    )


def newest_mtime_ns(*paths: Path) -> int | None:
    """Nanoseconds, not float seconds: the float collides on a same-tick append and serves a spurious 304."""
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
    "PairWrite",
    "append_jsonl",
    "append_line",
    "iter_files",
    "newest_mtime_ns",
    "open_text_robust",
    "read_bytes_optional",
    "read_json",
    "read_json_optional",
    "read_json_tolerant",
    "read_lines",
    "read_yaml",
    "read_yaml_optional",
    "replace_lines",
    "rewrite_lines",
    "rewrite_pair",
    "rmtree_robust",
    "unlink_robust",
    "validate_path_component",
    "write_bytes",
    "write_json",
    "write_jsonl",
    "write_text",
    "write_yaml",
]
