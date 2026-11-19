"""Probe: a first write whose dir chain passes MAX_PATH, on whichever tree PYTHONPATH names."""

from __future__ import annotations

import tempfile
from pathlib import Path

from promptpotter.infrastructure.store import io

root = Path(tempfile.mkdtemp(prefix="rvs2-lp-"))
deep = root.joinpath(*(["d" * 40] * 7)) / "f.json"
print("path length", len(str(deep)))
try:
    target = io.with_parents(deep) if hasattr(io, "with_parents") else deep
    io.write_json(target, {"a": 1})
    print("OK", io.read_json(deep))
except Exception as exc:  # noqa: BLE001
    print("FAIL", type(exc).__name__, str(exc)[:160])
