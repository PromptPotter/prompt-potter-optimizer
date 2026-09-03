"""Generate the SERVED HTTP surface from the FastAPI app
→ ``docs/specs/openapi.generated.json``. CI re-runs this; non-empty diff fails.

**Two API documents, and they answer different questions — do not merge them.**

``api-openapi.yaml`` is hand-written and **schema-first**: a command kind is
declared there *before* its handler lands, so it legitimately describes operations
that do not exist yet (``x-status: declared-not-wired``). It is the design contract
for the inbound command surface, and its declared/wired partition is pinned against
the code by ``tests/test_integrity.py::test_declared_command_kinds_match_the_wired_set``.

This file is its opposite number: a pure **description of what the running app
actually serves**, generated, never hand-edited. It exists because the hand-written
spec covers the command surface plus six reads, while the app serves ~35 more
endpoints — the whole ``/auth`` surface, ``/backends``, ``/evidence``,
``/optimizer-pipeline``, ``/workspace/storage*``, the SSE stream, the file reads —
that no document mentioned. An integrator reading only the hand-written file would
conclude they do not exist.

So: schema-first for what we *promise*, generated for what we *serve*. Neither is
authority over the other, and a disagreement between them is information, not drift.

The version field is pinned rather than read from the app, because ``APP_VERSION``
tracks ``pyproject.toml`` and would make every release bump look like an API diff.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# ruff: noqa: E402 -- we import from promptpotter after adjusting sys.path
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from promptpotter.main import app

_OUT_PATH = _REPO / "docs" / "specs" / "openapi.generated.json"

_DESCRIPTION = """\
GENERATED — do not edit. Regenerate with `python scripts/build_openapi.py`.

Every operation the running app serves, derived from the FastAPI routers. The
hand-written command contract is `api-openapi.yaml`; it is schema-first and
may declare operations that are not wired yet, so the two documents are expected
to differ. This one describes reality.
"""


def build() -> dict[str, Any]:
    """The app's own OpenAPI, with the release-version field neutralised."""
    schema: dict[str, Any] = json.loads(json.dumps(app.openapi()))
    info = schema.setdefault("info", {})
    info["version"] = "generated"
    info["description"] = _DESCRIPTION
    return schema


def main() -> int:
    # `sort_keys` so a route-registration reorder is not a diff; the schema is a
    # lookup table, not an ordered document.
    content = json.dumps(build(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    _OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    prior = _OUT_PATH.read_text(encoding="utf-8") if _OUT_PATH.is_file() else ""
    if prior == content:
        print(f"{_OUT_PATH.relative_to(_REPO)} — up to date.")
        return 0
    _OUT_PATH.write_text(content, encoding="utf-8")
    print(f"{_OUT_PATH.relative_to(_REPO)} — regenerated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
