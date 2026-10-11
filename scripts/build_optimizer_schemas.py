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
    stated = optimizers.llm_nodes()
    out: dict[Path, Mapping[str, type[BaseModel]]] = {
        checkin_assets_root(): {"checkin": CheckinOutput}
    }
    for runtime in optimizers.runtimes().values():
        declared = read_yaml(runtime.manifest_dir / "pipeline.yaml").get("nodes") or {}
        # Manifest order: the file's key order is the committed artifact's.
        out[runtime.manifest_dir] = {
            node: model
            for node in declared
            if node in stated and (model := stated[node].response_model) is not None
        }
    return out


def _entry(node: str, model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    return {
        # DECLARATION order, never sorted: field order is generation order (`NodeOutputSchema`).
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
        # `ensure_ascii=False`: escaped prose cannot reproduce the committed output byte for byte.
        out_path.write_text(
            json.dumps(resolved, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"wrote {len(resolved)} schemas to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
