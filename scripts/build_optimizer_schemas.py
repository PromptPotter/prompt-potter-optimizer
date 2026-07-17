"""Regenerate ``datasets/_optimizer/pipeline.json::resolved_schemas`` from
``promptpotter.application.optimization.dispatch.schemas``. Idempotent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from promptpotter.application.optimization.dispatch.schemas import (
    OPTIMIZER_RESPONSE_MODELS,
)


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    manifest_path = repo_root / "datasets" / "_optimizer" / "pipeline.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    resolved: dict[str, dict[str, Any]] = {}
    for node, model_cls in OPTIMIZER_RESPONSE_MODELS.items():
        schema = model_cls.model_json_schema()
        resolved[f"{node}/1"] = {
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

    manifest["resolved_schemas"] = resolved
    # `ensure_ascii=False`: the manifest is UTF-8 and its descriptions are hand-written
    # prose. Escaping them to \uXXXX makes the generator unable to reproduce its own
    # committed output, so the contract check fails on punctuation instead of on schema drift.
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(resolved)} schemas to {manifest_path.relative_to(repo_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
