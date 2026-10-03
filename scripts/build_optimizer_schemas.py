"""Regenerate every ``resolved_schemas.json`` beside a manifest — one per registered optimizer
runtime, from the ``response_models`` it declares, and the bench's check-in (``checkin/``).
Idempotent.

Each file holds the schemas of the nodes its own manifest DECLARES, so a node's schema ships
beside the manifest that runs it. It reads the manifests' node names and writes nothing else:
the authored half is YAML, and re-emitting it would reformat the operator's blocks and comments
on every run, which CI would read as schema drift.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from promptpotter.application import optimizers
from promptpotter.application.bench.task_context import CheckinOutput
from promptpotter.config.paths import checkin_assets_root
from promptpotter.infrastructure.store.io import read_yaml


def _manifests() -> dict[Path, Mapping[str, type[BaseModel]]]:
    return {
        checkin_assets_root(): {"checkin": CheckinOutput},
        **{rt.manifest_dir: rt.response_models for rt in optimizers.runtimes().values()},
    }


def _entry(node: str, model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    return {
        # DECLARATION order, never sorted. `fields` IS the order declaration
        # (`NodeOutputSchema`), and field order is generation order — alphabetizing
        # it makes the manifest disagree with the schema the wire actually carries.
        "fields": list(schema.get("properties", {})),
        "json_schema": {
            "name": node,
            # The wire ships `strict: False` (`openai_compat.py`), so the manifest says so too.
            "strict": False,
            "schema": schema,
        },
    }


def main() -> int:
    for directory, models in sorted(_manifests().items()):
        declared = read_yaml(directory / "pipeline.yaml").get("nodes") or {}
        if orphans := sorted(set(models) - set(declared)):
            raise SystemExit(
                f"{directory}: response models its manifest declares no node for: {orphans}"
            )
        resolved = {f"{node}/1": _entry(node, model) for node, model in models.items()}
        out_path = directory / "resolved_schemas.json"
        # `ensure_ascii=False`: the schemas carry hand-written prose in their `description`
        # strings. Escaping them to \uXXXX makes the generator unable to reproduce its own
        # committed output, so the contract check fails on punctuation instead of schema drift.
        out_path.write_text(
            json.dumps(resolved, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"wrote {len(resolved)} schemas to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
