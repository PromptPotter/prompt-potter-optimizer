from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

# ruff: noqa: E402 -- we import from promptpotter after adjusting sys.path
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from promptpotter.domain.projection_envelope import ProjectionEnvelope
from promptpotter.domain.prompt_block import BlockPack
from promptpotter.domain.run_records import CycleRecord
from promptpotter.main import app

_SPECS = _REPO / "docs" / "specs"
_OPENAPI_PATH = _SPECS / "openapi.generated.json"
_ASYNCAPI_PATH = _SPECS / "events-asyncapi.generated.json"
_STORED_PATH = _SPECS / "stored-shapes.generated.json"

# Each on-disk document `restamp` prunes, by the type its reader validates it as.
_STORED_ROOTS: dict[str, object] = {"CycleRecord": CycleRecord, "BlockPack": BlockPack}

_REGENERATE = "GENERATED — do not edit. Regenerate with `python scripts/build_specs.py`."

_OPENAPI_DESCRIPTION = f"""\
{_REGENERATE}

Every operation the running app serves, derived from the FastAPI routers and the
payload and response models they declare: a command kind is its payload model, so
this document IS the command contract.
"""

_ASYNCAPI_DESCRIPTION = f"""\
{_REGENERATE}

The outbound Server-Sent Events channel: every frame is a `ProjectionEnvelope`, and a
record-derived frame's `payload` is the `CycleRecord` its `kind` names. The
subscription contract (snapshot first, then the live tail; gap recovery; no replay)
is `docs/developer/event-stream.md`.
"""


_STORED_DESCRIPTION = f"""\
{_REGENERATE}

What each on-disk document `python -m promptpotter restamp` prunes must hold to be read, under
`roots` by the type its reader validates: a ledger line, a block pack's `pack.json`. The gate's
`stored-shapes` commit check compares it across a commit.
"""


def stored_shapes() -> dict[str, Any]:
    roots, defs = TypeAdapter.json_schemas(
        [(name, "validation", TypeAdapter(root)) for name, root in _STORED_ROOTS.items()],
        ref_template="#/components/schemas/{model}",
    )
    return {
        "title": "Stored shapes",
        "description": _STORED_DESCRIPTION,
        "roots": {name: roots[(name, "validation")] for name in _STORED_ROOTS},
        "components": {"schemas": defs["$defs"]},
    }


def openapi() -> dict[str, Any]:
    schema: dict[str, Any] = json.loads(json.dumps(app.openapi()))
    # Pinned: `APP_VERSION` tracks `pyproject.toml`, so every release bump would read as an API diff.
    schema["info"]["version"] = "generated"
    schema["info"]["description"] = _OPENAPI_DESCRIPTION
    return schema


def asyncapi(paths: dict[str, Any]) -> dict[str, Any]:
    roots, defs = TypeAdapter.json_schemas(
        [
            ("frame", "serialization", TypeAdapter(ProjectionEnvelope)),
            ("record", "serialization", TypeAdapter(CycleRecord)),
        ],
        ref_template="#/components/schemas/{model}",
    )
    schemas: dict[str, Any] = {**defs["$defs"], "CycleRecord": roots[("record", "serialization")]}
    return {
        "asyncapi": "3.0.0",
        "info": {
            "title": "Cycle event stream",
            "version": "generated",
            "description": _ASYNCAPI_DESCRIPTION,
        },
        "channels": {
            "cycleEvents": {
                "address": next(path for path in paths if path.endswith("/events:subscribe")),
                "messages": {"projectionFrame": {"$ref": "#/components/messages/ProjectionFrame"}},
            }
        },
        "operations": {
            "subscribeCycleEvents": {
                "action": "receive",
                "channel": {"$ref": "#/channels/cycleEvents"},
                "messages": [{"$ref": "#/channels/cycleEvents/messages/projectionFrame"}],
            }
        },
        "components": {
            "messages": {
                "ProjectionFrame": {
                    "contentType": "application/json",
                    "payload": roots[("frame", "serialization")],
                }
            },
            "schemas": schemas,
        },
    }


def main() -> int:
    contract = openapi()
    for path, document in (
        (_OPENAPI_PATH, contract),
        (_ASYNCAPI_PATH, asyncapi(contract["paths"])),
        (_STORED_PATH, stored_shapes()),
    ):
        # `sort_keys` so a registration reorder is not a diff.
        content = json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        path.write_text(content, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(_REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
