"""Regenerate ``datasets/_optimizer/resolved_schemas.json`` from
``promptpotter.application.optimization.dispatch.schemas``. Idempotent.

**A pure writer** — it reads nothing from ``datasets/_optimizer/`` and owns this file
outright. It used to read the whole ``pipeline.json``, graft ``resolved_schemas`` into
it, and rewrite the lot, which put generated output and hand-written meta-prompt prose
in one file under one ``git diff --exit-code`` gate. That is unworkable now the authored
half is YAML: re-emitting it would reformat the operator's blocks and comments on every
run, and CI would read that as schema drift.
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
    out_path = repo_root / "datasets" / "_optimizer" / "resolved_schemas.json"

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

    # `ensure_ascii=False`: the schemas carry hand-written prose in their `description`
    # strings. Escaping them to \uXXXX makes the generator unable to reproduce its own
    # committed output, so the contract check fails on punctuation instead of schema drift.
    out_path.write_text(json.dumps(resolved, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(resolved)} schemas to {out_path.relative_to(repo_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
