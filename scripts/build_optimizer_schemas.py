from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from processes import replace_changed
from pydantic import BaseModel

from promptpotter.application import optimizers
from promptpotter.application.bench.task_context import CheckinOutput
from promptpotter.application.optimizer_manifest import authored_manifest
from promptpotter.config.paths import checkin_assets_root
from promptpotter.domain.pipeline_manifest import ManifestSwitch
from promptpotter.infrastructure.store.io import read_yaml


def _manifests() -> dict[Path, Mapping[str, type[BaseModel]]]:
    stated = optimizers.llm_nodes()
    out: dict[Path, Mapping[str, type[BaseModel]]] = {
        checkin_assets_root(): {"checkin": CheckinOutput}
    }
    for manifest in optimizers.MANIFESTS.table.values():
        authored = authored_manifest(manifest.directory / "pipeline.yaml")
        # A switch writes none: its schemas are those of the manifests it reads its nodes from.
        if isinstance(authored, ManifestSwitch):
            continue
        declared = authored.nodes
        # Manifest order: the file's key order is the committed artifact's.
        out[manifest.directory] = {
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
            # The wire ships `strict: True` (`openai_compat.py`), so the manifest says so too.
            "strict": True,
            "schema": schema,
        },
    }


def main() -> int:
    # One line per file it writes, `wrote` where the content moved: the gate's probe reads them.
    for directory, models in sorted(_manifests().items()):
        declared = read_yaml(directory / "pipeline.yaml").get("nodes") or {}
        if orphans := sorted(set(models) - set(declared)):
            raise SystemExit(
                f"{directory}: response models its manifest declares no node for: {orphans}"
            )
        resolved = {f"{node}/1": _entry(node, model) for node, model in models.items()}
        out_path = directory / "resolved_schemas.json"
        # `ensure_ascii=False`: escaped prose cannot reproduce the committed output byte for byte.
        content = json.dumps(resolved, indent=2, ensure_ascii=False) + "\n"
        print(f"{'wrote' if replace_changed(out_path, content) else 'unchanged'} {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
