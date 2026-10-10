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
    schema: dict[str, Any] = json.loads(json.dumps(app.openapi()))
    info = schema.setdefault("info", {})
    # Pinned: `APP_VERSION` tracks `pyproject.toml`, so every release bump would read as an API diff.
    info["version"] = "generated"
    info["description"] = _DESCRIPTION
    return schema


def main() -> int:
    # `sort_keys` so a route-registration reorder is not a diff.
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
