"""Regenerate every ``resolved_schemas.json`` beside a manifest under ``promptpotter/assets/`` —
one per optimizer (``optimizers/{name}/``) and the bench's check-in (``checkin/``) — from
``promptpotter.application.optimizers.potter.dispatch.schemas``. Idempotent.

Each file holds the schemas of the nodes its own manifest DECLARES, so a node's schema ships
beside the manifest that runs it. It reads the manifests' node names and writes nothing else:
the authored half is YAML, and re-emitting it would reformat the operator's blocks and comments
on every run, which CI would read as schema drift.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from promptpotter.application.bench.task_context import CheckinOutput
from promptpotter.application.optimizers.potter.dispatch.schemas import (
    OPTIMIZER_RESPONSE_MODELS,
)
from promptpotter.config.paths import checkin_assets_root, optimizers_root
from promptpotter.infrastructure.store.io import read_yaml

RESPONSE_MODELS: dict[str, type[BaseModel]] = {
    **OPTIMIZER_RESPONSE_MODELS,
    "checkin": CheckinOutput,
}


def _manifest_dirs() -> list[Path]:
    return [checkin_assets_root(), *sorted(p for p in optimizers_root().iterdir() if p.is_dir())]


def _entry(node: str) -> dict[str, Any]:
    schema = RESPONSE_MODELS[node].model_json_schema()
    return {
        # DECLARATION order, never sorted. `fields` IS the order declaration
        # (`NodeOutputSchema`), and field order is generation order — alphabetizing
        # it makes the manifest disagree with the schema the wire actually carries.
        "fields": list(schema.get("properties", {})),
        "json_schema": {
            "name": node,
            # The wire ships `strict: False` (`openai_compat.py`); claiming True here
            # made the manifest describe a constraint no provider was ever given.
            "strict": False,
            "schema": schema,
        },
    }


def main() -> int:
    placed: set[str] = set()
    for directory in _manifest_dirs():
        declared = read_yaml(directory / "pipeline.yaml").get("nodes") or {}
        nodes = [n for n in RESPONSE_MODELS if n in declared]
        placed.update(nodes)
        resolved = {f"{node}/1": _entry(node) for node in nodes}
        out_path = directory / "resolved_schemas.json"
        # `ensure_ascii=False`: the schemas carry hand-written prose in their `description`
        # strings. Escaping them to \uXXXX makes the generator unable to reproduce its own
        # committed output, so the contract check fails on punctuation instead of schema drift.
        out_path.write_text(
            json.dumps(resolved, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"wrote {len(resolved)} schemas to {out_path}")
    if orphans := sorted(set(RESPONSE_MODELS) - placed):
        raise SystemExit(f"response models no manifest declares a node for: {orphans}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
