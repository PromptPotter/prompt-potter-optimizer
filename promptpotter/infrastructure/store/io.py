import contextlib
import itertools
import json
import os
import shutil
import stat
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import IO, Any, NamedTuple

import yaml

from promptpotter.config.settings import LOCK_TIMEOUT
from promptpotter.domain.cycle_paths import ALL_DOTS_RE, ID_COMPONENT_RE
from promptpotter.shared.errors import ConflictError

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except PermissionError:
            return False
        return True

    def _unlock(fd: int) -> None:
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

    def _excluded(fd: int) -> bool:
        # The holder's byte-0 lock refuses a read of it, and a read takes no lock.
        try:
            os.read(fd, 1)
        except PermissionError:
            return True
        return False

    def _retire(fd: int, name: str) -> None:
        _unlock(fd)
        os.close(fd)
        # Refused while another taker has it open, which keeps it for that taker.
        with contextlib.suppress(OSError):
            os.unlink(name)

else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)

    def _excluded(fd: int) -> bool:
        # Shared, so probes never exclude each other; it lasts until the probe closes the file.
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False

    def _retire(fd: int, name: str) -> None:
        # Unlinked while held, so a waiter on this inode finds it unlinked and reopens the name.
        with contextlib.suppress(FileNotFoundError):
            os.unlink(name)
        _unlock(fd)
        os.close(fd)


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
    """Every file under *root*, past `MAX_PATH` too; one removed mid-walk is passed over.

    No link is entered: a junction reads as a directory, and its target's files are not *root*'s
    to count or delete."""
    stack = [root]
    first = True
    while stack:
        current = stack.pop()
        try:
            with os.scandir(_long_path(current)) as it:
                for entry in it:
                    path = current / entry.name
                    try:
                        if entry.is_junction():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if not (first and entry.name in skip):
                                stack.append(path)
                        elif entry.is_file(follow_symlinks=False):
                            yield path, entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
        except FileNotFoundError:
            continue
        first = False


def prune_empty_dirs(root: Path) -> None:
    """Every empty directory under *root*, deepest first and past `MAX_PATH` too; *root* stays.

    Walked top-down so a junction is cut before it is entered: `os.walk` follows one."""
    top = _long_path(root)
    below: list[str] = []
    for current, dirs, _files in os.walk(top):
        dirs[:] = [d for d in dirs if not os.path.isjunction(os.path.join(current, d))]
        if current != top:
            below.append(current)
    for current in reversed(below):
        with contextlib.suppress(OSError):
            os.rmdir(current)


def stat_optional(path: Path) -> os.stat_result | None:
    """A file's stat past `MAX_PATH` too; ``None`` where no file is."""
    try:
        held = os.stat(_long_path(path))
    except FileNotFoundError:
        return None
    return held if stat.S_ISREG(held.st_mode) else None


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


def _read_bytes(path: Path) -> bytes:
    """Retried: Windows refuses for the instant another process swaps the file (`_atomic_replace`)."""
    for attempt in range(2):
        try:
            with open(_long_path(path), "rb") as f:
                return f.read()
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
    with open(_long_path(path), "rb") as f:
        return f.read()


def read_bytes_optional(path: Path) -> bytes | None:
    try:
        return _read_bytes(path)
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
        del indentless
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


def _yaml_text(data: object) -> str:
    return yaml.dump(
        data,
        Dumper=_YamlDumper,
        # Load-bearing: declaration order is meaning (node order, schema field order).
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=100,
        indent=2,
    )


def write_yaml(path: Path, data: Any) -> None:
    write_text(path, _yaml_text(data))


_YAML_LOADER: type = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

# Strings a YAML 1.1 loader reads as another type: written, each must read back as the string it was.
_YAML_1_1_HAZARDS = (
    *("TRUE", "FALSE", "True", "no", "No", "yes", "on", "off", "OFF", "y", "n", "null", "~"),
    *("1", "1.5", "0755", "1e5", "2026-07-26", "v1.0"),
)
_hazards = {h: h for h in _YAML_1_1_HAZARDS} | {"nested": {"labels": list(_YAML_1_1_HAZARDS)}}
if yaml.load(_yaml_text(_hazards), Loader=_YAML_LOADER) != _hazards:
    raise RuntimeError("write_yaml leaves a YAML 1.1 scalar bare, so read_yaml reads another type")
del _hazards


def parse_yaml(text: str | bytes) -> Any:
    """`yaml.YAMLError` is no `ValueError`, which every guard in this tree catches, so it is wrapped here."""
    try:
        return yaml.load(text, Loader=_YAML_LOADER)
    except yaml.YAMLError as exc:
        raise ValueError(f"not valid YAML — {exc}") from exc


def read_yaml(path: Path) -> Any:
    with open(_long_path(path), encoding="utf-8") as f:
        text = f.read()
    try:
        return parse_yaml(text)
    except ValueError as exc:
        raise ValueError(f"{path} is {exc}") from exc


def read_yaml_optional(path: Path) -> Any | None:
    try:
        return read_yaml(path)
    except FileNotFoundError:
        return None


_LOCK_POLL_S = 0.005


class HeldLock:
    """An OS lock on a file: the kernel drops it with its holder, and it shuts out every other handle,
    this process's included. Released from any thread; never reentrant."""

    def __init__(self, name: str, fd: int) -> None:
        self._name = name
        self._fd: int | None = fd

    @property
    def held(self) -> bool:
        return self._fd is not None

    def release(self) -> None:
        if self._fd is not None:
            fd, self._fd = self._fd, None
            _unlock(fd)
            os.close(fd)

    def retire(self) -> None:
        """`release`, and the file goes too: for a name taken once, whose files would pile up."""
        if self._fd is not None:
            fd, self._fd = self._fd, None
            _retire(fd, self._name)


def _open_lock(name: str) -> int:
    try:
        return _private(name, os.O_RDWR | os.O_CREAT)
    except FileNotFoundError:
        os.makedirs(os.path.dirname(name), exist_ok=True)
        return _private(name, os.O_RDWR | os.O_CREAT)


def take_lock(path: Path, *, wait_s: float) -> HeldLock | None:
    """Polls for up to *wait_s*, so ``0`` is one try; ``None`` is another holder's."""
    name = _long_path(path)
    deadline = time.monotonic() + wait_s
    while True:
        fd = _open_lock(name)
        if _try_lock(fd):
            # A retired file's waiter holds an unlinked inode: the name is reopened instead.
            if os.fstat(fd).st_nlink:
                return HeldLock(name, fd)
            _unlock(fd)
        os.close(fd)
        if time.monotonic() >= deadline:
            return None
        time.sleep(_LOCK_POLL_S)


@contextlib.contextmanager
def held_lock(path: Path, *, wait_s: float) -> Iterator[None]:
    lock = take_lock(path, wait_s=wait_s)
    if lock is None:
        raise TimeoutError(f"{path} stayed locked past {wait_s} s")
    try:
        yield
    finally:
        lock.release()


@contextlib.contextmanager
def probe_lock(path: Path) -> Iterator[bool]:
    """Whether *path* is held, asked without an exclusive hold another probe would read as one."""
    try:
        fd = os.open(_long_path(path), os.O_RDONLY)
    except FileNotFoundError:
        yield False
        return
    try:
        yield _excluded(fd)
    finally:
        os.close(fd)


def write_lock_name(name: str) -> str:
    """Dot-named, which the cycle file listing skips; never retired, since making it per line is the cost."""
    return f".{name}.lock"


def _write_lock(path: Path) -> AbstractContextManager[None]:
    """Windows runs `O_APPEND` as seek-then-write, so two unlocked appends overwrite one record."""
    return held_lock(path.with_name(write_lock_name(path.name)), wait_s=LOCK_TIMEOUT)


# Name → (inode, end, mtime) after this process's last append there, which landed whole: an inode
# number outlives its file, the write time of a re-created one does not. Only a speed-up: a miss
# costs one read-access open, which Windows' scanner answers by reading the whole file.
_APPENDED: dict[str, tuple[int, int, int]] = {}
_APPENDED_HELD = 4096


def append_line(path: Path, line: bytes) -> range:
    """Reopened per call, never held: a held handle blocks a stub fork's delete on Windows.

    Under the lock no append is in flight, so an unterminated last line is a crashed one: it is cut
    back to the last newline before this line lands. No reader counted those bytes (`complete_lines`,
    `FileMark.end`), and the append that wrote them never returned. A file still ending where this
    process's last append left it is opened append-only: it ends on a line written whole."""
    data = line + b"\n"
    name = _long_path(path)
    with _write_lock(path):
        with open(name, "ab", opener=_private) as f:
            st = os.fstat(f.fileno())
            if not st.st_size or _APPENDED.get(name) == (st.st_ino, st.st_size, st.st_mtime_ns):
                return _landed(name, f, data)
        _cut_unterminated(name)
        with open(name, "ab", opener=_private) as f:
            return _landed(name, f, data)


def _private(name: str, flags: int) -> int:
    """Owner-only, as `mkstemp` makes every atomic write's file: whichever process creates a file
    sets its mode, never that process's umask."""
    return os.open(name, flags, 0o600)


def _landed(name: str, f: IO[bytes], data: bytes) -> range:
    f.write(data)
    f.flush()
    end = f.tell()
    if len(_APPENDED) >= _APPENDED_HELD:
        _APPENDED.clear()
    st = os.fstat(f.fileno())
    _APPENDED[name] = (st.st_ino, end, st.st_mtime_ns)
    return range(end - len(data), end)


def _cut_unterminated(name: str) -> None:
    with open(name, "r+b") as f:
        size = f.seek(0, os.SEEK_END)
        if size:
            f.seek(size - 1)
            if f.read(1) != b"\n":
                f.truncate(size - len(_last_line(f, size)))


def append_jsonl(path: Path, item: dict[str, Any]) -> Path:
    append_line(path, json.dumps(item, ensure_ascii=False).encode("utf-8"))
    return path


class FileMark(NamedTuple):
    """How far a line file was read, and what says a later handle is still that file."""

    end: int
    # `None`: the file was absent, so whatever an append has put there since is all tail.
    inode: int | None
    # The last line read, ending at `end`: an inode outlives its file, so a delete and a re-mint
    # pass every stat test, and only the bytes say the file is the one read.
    last: bytes = b""


ABSENT = FileMark(0, None)


def holds_mark(fh: IO[bytes], mark: FileMark) -> bool:
    """The one identity rule: *fh* is the file *mark* read, grown by appends alone."""
    if mark.inode is None:
        return True
    st = os.fstat(fh.fileno())
    if st.st_ino != mark.inode or st.st_size < mark.end:
        return False
    fh.seek(mark.end - len(mark.last))
    return fh.read(len(mark.last)) == mark.last


def complete_lines(fh: IO[bytes]) -> Iterator[bytes]:
    """Every line reader's one rule: an unterminated last line is an append in flight, and is not read."""
    for raw in fh:
        if not raw.endswith(b"\n"):
            return
        yield raw


class Tail(NamedTuple):
    lines: list[bytes]
    mark: FileMark
    # Read from the start: nothing was read before, or the file read is gone or was rewritten.
    whole: bool


def read_tail(path: Path, mark: FileMark) -> Tail:
    """Every line past *mark* while the file is the one *mark* read; every line otherwise."""
    try:
        with open(_long_path(path), "rb") as f:
            whole = mark.inode is None or not holds_mark(f, mark)
            at = FileMark(0, os.fstat(f.fileno()).st_ino) if whole else mark
            f.seek(at.end)
            lines: list[bytes] = []
            for raw in complete_lines(f):
                lines.append(raw)
                at = FileMark(at.end + len(raw), at.inode, raw)
    except FileNotFoundError:
        return Tail([], ABSENT, whole=True)
    return Tail(lines, at, whole)


def _read_marked(path: Path) -> tuple[list[str], FileMark]:
    tail = read_tail(path, ABSENT)
    return [raw.decode("utf-8") for raw in tail.lines], tail.mark


def read_lines(path: Path) -> list[str]:
    return _read_marked(path)[0]


_TAIL_STEP = 4096


def _last_line(f: IO[bytes], end: int) -> bytes:
    start = max(0, end - _TAIL_STEP)
    while True:
        f.seek(start)
        held = f.read(end - start)
        cut = held.rfind(b"\n", 0, len(held) - 1)
        if cut != -1 or start == 0:
            return held[cut + 1 :]
        start = max(0, start - _TAIL_STEP)


def _end_mark(path: Path) -> FileMark:
    # Locked, so no append is in flight. A torn fragment a crashed append left is counted in the
    # mark, so `replace_lines` drops it: only what lands past the mark is carried.
    with _write_lock(path):
        try:
            with open(_long_path(path), "rb") as f:
                st = os.fstat(f.fileno())
                return FileMark(st.st_size, st.st_ino, _last_line(f, st.st_size))
        except FileNotFoundError:
            return ABSENT


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


def _swap(path: Path, mark: FileMark, lines: Iterable[str], companion: _Companion | None) -> bool:
    data = "".join(lines).encode("utf-8")
    # Only the swap is locked: a big rewrite outlasts `LOCK_TIMEOUT`, where an appender gives up.
    with _write_lock(path):
        try:
            with open(_long_path(path), "rb") as f:
                if not holds_mark(f, mark):
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
    mark: FileMark
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
