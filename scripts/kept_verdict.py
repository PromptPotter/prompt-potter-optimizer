"""Stdlib only: the gate imports this before it has chosen its interpreter."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# No prose is among them, so a docs edit reruns nothing that only RUNS the engine.
ENGINE_RUNTIME = ("promptpotter/", "datasets/", "examples/", "pyproject.toml", "uv.lock")
OFFLINE_RUN = "offline-run"
OFFLINE_RUN_READS = (*ENGINE_RUNTIME, "scripts/offline_run.py", "scripts/kept_verdict.py")


def reads(rel: str, prefixes: Sequence[str]) -> bool:
    inside = tuple(p for p in prefixes if not p.startswith("!"))
    outside = tuple(p[1:] for p in prefixes if p.startswith("!"))
    return rel.startswith(inside) or bool(outside and not rel.startswith(outside))


class KeptVerdicts:
    _STORE = REPO / ".gate_cache" / "green.json"
    # What `npm ci` leaves naming the tree it installed: whether the install still matches the lock.
    _INSTALLED = ("webapp/node_modules/.package-lock.json",)

    def __init__(self, listed: str) -> None:
        self._files = sorted(filter(None, listed.split("\0")))
        # Stamped by size and mtime, so a late check is keyed on the files as they are when IT starts.
        self._hashes: dict[str, tuple[tuple[int, int], bytes]] = {}
        tools = sorted(f"{d.name}={d.version}" for d in importlib.metadata.distributions())
        tools.append(sys.version)  # the interpreter is a tool no distribution names
        self._tools = hashlib.sha256("\0".join(tools).encode()).digest()

    @classmethod
    def open(cls) -> KeptVerdicts | None:
        """``None`` under ``CI``, and where git cannot list the tree: a key over no files matches every tree."""
        if os.environ.get("CI"):
            return None
        listing = subprocess.run(
            ["git", "ls-files", "-co", "--exclude-standard", "-z"],
            cwd=REPO,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return None if listing.returncode else cls(listing.stdout)

    def _hash(self, rel: str) -> bytes:
        path = REPO / rel
        try:
            stat = path.stat()
        except OSError:  # listed and since deleted: its absence is the content
            return b""
        stamp = (stat.st_size, stat.st_mtime_ns)
        held = self._hashes.get(rel)
        if held is None or held[0] != stamp:
            held = self._hashes[rel] = stamp, hashlib.sha256(path.read_bytes()).digest()
        return held[1]

    def key(self, prefixes: Sequence[str], products: Sequence[str] = ()) -> str:
        # git ignores a built product, so it is walked.
        built = sorted(
            Path(root, name).relative_to(REPO).as_posix()
            for product in products
            for root, _, names in os.walk(REPO / product)
            for name in names
        )
        digest = hashlib.sha256(self._tools)
        sources = (rel for rel in self._files if reads(rel, prefixes))
        for rel in (*self._INSTALLED, *sources, *built):
            digest.update(rel.encode() + b"\0" + self._hash(rel))
        return digest.hexdigest()

    def _kept(self) -> dict[str, str]:
        try:
            kept: dict[str, str] = json.loads(self._STORE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return kept

    def holds(self, name: str, key: str) -> bool:
        return self._kept().get(name) == key

    def record(self, verdicts: Mapping[str, str | None]) -> None:
        kept = self._kept()
        for name, key in verdicts.items():
            if key is None:
                kept.pop(name, None)
            else:
                kept[name] = key
        self._STORE.parent.mkdir(exist_ok=True)
        self._STORE.write_text(json.dumps(kept, indent=1, sort_keys=True), encoding="utf-8")
