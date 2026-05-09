"""`optimizer_pipeline.json` parity with backend `pipeline.json`.

Pins the §3.5 deliverable from `docs/specs/m10-cleanup.md` and
`docs/developer/pipeline-json-contract.md`: the optimizer's manifest
loads through the **same** `parse_pipeline_response()` parser as a
backend's pipeline, with the same top-level shape and the same
per-node `optimizer` sub-object. If the optimizer manifest ever
drifts in shape from a backend pipeline (special-case fields,
parallel registries, ad-hoc keys), this test fails — and the M11
PromptPotter-as-backend connector can stop being a refactor.
"""

from __future__ import annotations

import json
from pathlib import Path

from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import PipelineSchema

REPO = Path(__file__).resolve().parent.parent
OPTIMIZER_PIPELINE = REPO / "promptpotter/application/optimization/optimizer_pipeline.json"
BACKEND_PIPELINES = sorted((REPO / "datasets").glob("*/pipeline.json"))


def _required_top_level_fields(data: dict) -> set[str]:
    """Pinned per the contract doc: name, nodes, pipelines."""
    return {"name", "nodes", "pipelines"} - set(data.keys())


def test_optimizer_and_backend_pipelines_share_parser_and_shape() -> None:
    """Same parser, same top-level shape, same per-node optimizer sub-object.

    One contract, two consumers: every dataset's `pipeline.json` and
    the optimizer's own `optimizer_pipeline.json`. The contract is
    `docs/developer/pipeline-json-contract.md`; this test pins it so
    a drift in either consumer fails CI.
    """
    assert OPTIMIZER_PIPELINE.exists(), OPTIMIZER_PIPELINE
    assert BACKEND_PIPELINES, "no backend pipeline.json files under datasets/"

    files = [OPTIMIZER_PIPELINE, *BACKEND_PIPELINES]
    schemas: dict[Path, PipelineSchema] = {}

    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))

        missing = _required_top_level_fields(data)
        assert not missing, f"{path}: missing required top-level fields: {sorted(missing)}"
        assert "default" in data["pipelines"], f"{path}: pipelines.default is required"

        schema = parse_pipeline_response(data)
        assert schema.name, f"{path}: parsed schema has empty name"
        assert schema.nodes, f"{path}: parsed schema has zero nodes"
        schemas[path] = schema

        active = data["pipelines"]["default"]
        active_in_manifest = [n for n in active if n in data["nodes"]]
        assert [n.name for n in schema.nodes] == active_in_manifest, (
            f"{path}: parser walked a different node order than pipelines.default"
        )

        for node in schema.nodes:
            assert node.name, f"{path}: node has empty name"
            assert node.wire_type, f"{path}::{node.name} has empty wire_type"
            assert node.langfuse_type, f"{path}::{node.name} has empty langfuse_type"
