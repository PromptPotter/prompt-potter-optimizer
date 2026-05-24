"""Regenerate ``datasets/_optimizer/pipeline.json::resolved_schemas`` from
``promptpotter.application.optimization.dispatch.schemas``. Idempotent.
"""

from __future__ import annotations

import json
from pathlib import Path

from promptpotter.application.optimization.dispatch.schemas import (
    OPTIMIZER_RESPONSE_MODELS,
)


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    manifest_path = repo_root / "datasets" / "_optimizer" / "pipeline.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    resolved: dict[str, dict] = {}
    for node, model_cls in OPTIMIZER_RESPONSE_MODELS.items():
        schema = model_cls.model_json_schema()
        resolved[f"{node}/1"] = {
            "fields": sorted(schema.get("properties", {}).keys()),
            "json_schema": {
                "name": node,
                "strict": True,
                "schema": schema,
            },
        }

    manifest["resolved_schemas"] = resolved
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(resolved)} schemas to {manifest_path.relative_to(repo_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
