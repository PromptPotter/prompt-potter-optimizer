from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Any, Union

from processes import replace_changed
from pydantic import Field, TypeAdapter

# ruff: noqa: E402 -- we import from promptpotter after adjusting sys.path
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from promptpotter.domain.projection_envelope import ProjectionEnvelope
from promptpotter.domain.record_kinds import RECORDS
from promptpotter.infrastructure.store.data_root import pinned_schemas
from promptpotter.main import app

_SPECS = _REPO / "docs" / "specs"
_OPENAPI_PATH = _SPECS / "openapi.generated.json"
_ASYNCAPI_PATH = _SPECS / "events-asyncapi.generated.json"
_DATA_ROOT_PATH = _SPECS / "data-root.generated.json"

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
is `docs/developer/stable-api.md` § The event stream.
"""


_DATA_ROOT_DESCRIPTION = f"""\
{_REGENERATE}

Every SURVIVOR and CACHE row of `promptpotter/infrastructure/store/data_root.py`, by its patterns
under the data root: a diff in a SURVIVOR's shape owes a migration
(`promptpotter/application/maintenance/migrate.py::MIGRATIONS`) the landing runs before the code
that reads it does; a CACHE file that stops loading is dropped by the landing, which says so.
"""


def data_root() -> dict[str, Any]:
    return {"description": _DATA_ROOT_DESCRIPTION, "kinds": pinned_schemas()}


def openapi() -> dict[str, Any]:
    schema: dict[str, Any] = json.loads(json.dumps(app.openapi()))
    # Pinned: `APP_VERSION` tracks `pyproject.toml`, so every release bump would read as an API diff.
    schema["info"]["version"] = "generated"
    schema["info"]["description"] = _OPENAPI_DESCRIPTION
    return schema


def asyncapi(paths: dict[str, Any]) -> dict[str, Any]:
    # A value, not an alias: the arms are the registry's, known once its package is walked.
    records: Any = Annotated[
        Union[*RECORDS.registered().values()], Field(discriminator="record_type")
    ]
    roots, defs = TypeAdapter.json_schemas(
        [
            ("frame", "serialization", TypeAdapter(ProjectionEnvelope)),
            ("record", "serialization", TypeAdapter(records)),
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
        (_DATA_ROOT_PATH, data_root()),
    ):
        # `sort_keys` so a registration reorder is not a diff.
        content = json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        verb = "wrote" if replace_changed(path, content) else "unchanged"
        print(f"{verb} {path.relative_to(_REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
