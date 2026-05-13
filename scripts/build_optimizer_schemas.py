"""Regenerate ``optimizer_pipeline.json::resolved_schemas`` from the Pydantic
models in ``promptpotter.application.optimization.optimizer_schemas``.

The Pydantic models are the SoT for what each optimizer LLM call may
return. The JSON-Schema export in ``optimizer_pipeline.json`` exists for
non-Python consumers (Langfuse trace metadata, future webapp). Re-run
this after editing a model:

    python scripts/build_optimizer_schemas.py

Idempotent. Writes a single ``resolved_schemas`` block keyed by
``"{node}/1"`` — matches the existing manifest convention.
"""

from __future__ import annotations

import json
from pathlib import Path

from promptpotter.application.optimization.optimizer_schemas import (
    OPTIMIZER_RESPONSE_MODELS,
)


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    manifest_path = (
        repo_root / "promptpotter" / "application" / "optimization" / "optimizer_pipeline.json"
    )
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
