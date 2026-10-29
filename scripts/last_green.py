"""Stdlib only: the gate imports this before it has chosen its interpreter."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import sys
import sysconfig
from collections.abc import Mapping, Sequence
from pathlib import Path
from stat import S_ISDIR, S_ISREG

from processes import REPO, git

# No prose is among them, so a docs edit reruns nothing that only RUNS the engine.
ENGINE_RUNTIME = ("promptpotter/", "examples/", "pyproject.toml", "uv.lock")
OFFLINE_RUN = "offline-run"
OFFLINE_RUN_READS = (
    *ENGINE_RUNTIME,
    "scripts/offline_run.py",
    "scripts/offline_world.py",
    "scripts/last_green.py",
    "scripts/processes.py",
    # Its import graph is what scopes each optimizer's stamp.
    "scripts/tests_owning.py",
)
# Stamped by size and mtime, so a late reader is keyed on the file as it is when IT asks.
_DIGESTS: dict[Path, tuple[tuple[int, int], bytes]] = {}


def digest(*chunks: bytes) -> bytes:
    return hashlib.sha256(b"\0".join(chunks)).digest()


def file_digest(path: Path) -> bytes:
    try:
        stat = path.stat()
    except OSError:  # listed and since deleted: its absence is the content
        return b""
    if S_ISDIR(stat.st_mode) and (path / ".git").exists():  # a gitlink: its content is its commit
        code, head = git("-C", str(path), "rev-parse", "HEAD")
        if code:
            raise RuntimeError(f"{path}: a gitlink whose commit git cannot read: {head}")
        return digest(head.encode())
    if not S_ISREG(stat.st_mode):
        raise RuntimeError(f"{path}: git lists it, and it is neither a file nor a gitlink")
    stamp = (stat.st_size, stat.st_mtime_ns)
    held = _DIGESTS.get(path)
    if held is None or held[0] != stamp:
        held = _DIGESTS[path] = stamp, digest(path.read_bytes())
    return held[1]


def reads(rel: str, prefixes: Sequence[str]) -> bool:
    inside = tuple(p for p in prefixes if not p.startswith("!"))
    outside = tuple(p[1:] for p in prefixes if p.startswith("!"))
    return rel.startswith(inside) or bool(outside and not rel.startswith(outside))


class LastGreen:
    _STORE = REPO / ".gate_cache" / "last_green.json"
    # What `npm ci` leaves naming the tree it installed: whether the install still matches the lock.
    _INSTALLED = ("webapp/node_modules/.package-lock.json",)

    def __init__(self, listed: str) -> None:
        self._files = sorted(filter(None, listed.split("\0")))
        # The interpreter's own site dirs: a build product in the tree is on `sys.path`, never a tool.
        site = sorted({sysconfig.get_path("purelib"), sysconfig.get_path("platlib")})
        tools = sorted(f"{d.name}={d.version}" for d in importlib.metadata.distributions(path=site))
        tools.append(sys.version)  # the interpreter is a tool no distribution names
        self._tools = digest(*(tool.encode() for tool in tools))

    @classmethod
    def open(cls) -> LastGreen | None:
        """``None`` under ``CI``, and where git cannot list the tree: a key over no files matches every tree."""
        if os.environ.get("CI"):
            return None
        code, listed = git("ls-files", "-co", "--exclude-standard", "-z")
        return None if code else cls(listed)

    def key(
        self, prefixes: Sequence[str], products: Sequence[str] = (), without: Sequence[str] = ()
    ) -> str:
        # git ignores a built product, so it is walked.
        built = sorted(
            Path(root, name).relative_to(REPO).as_posix()
            for product in products
            for root, _, names in os.walk(REPO / product)
            for name in names
        )
        skipped = tuple(without)
        sources = (
            rel for rel in self._files if reads(rel, prefixes) and not rel.startswith(skipped)
        )
        return digest(
            self._tools,
            *(
                rel.encode() + b"\0" + file_digest(REPO / rel)
                for rel in (*self._INSTALLED, *sources, *built)
            ),
        ).hex()

    def _load(self) -> dict[str, str]:
        try:
            return dict(json.loads(self._STORE.read_text(encoding="utf-8"))["green"])
        except (OSError, ValueError, KeyError, TypeError):
            return {}

    def holds(self, name: str, key: str) -> bool:
        return self._load().get(name) == key

    def record(self, verdicts: Mapping[str, str | None]) -> None:
        green = {n: k for n, k in {**self._load(), **verdicts}.items() if k is not None}
        self._STORE.parent.mkdir(exist_ok=True)
        self._STORE.write_text(
            json.dumps({"green": green}, indent=1, sort_keys=True), encoding="utf-8"
        )
