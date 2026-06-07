"""C5 — Frozen model shape.

The objects every service passes around: ``JobSearchPoint``, ``OptSearchPoint``,
``PipelineSchema``. Guards their immutability, identity (content-hash), projection
(``to_job_search_point`` / ``to_pipeline_params`` / ``render``), and deep-copy
semantics — never the modules that consume them.
"""

import pydantic
import pytest

from promptpotter.connectors.termnorm import termnorm_wire_adapter
from promptpotter.domain.l1_layout import L1Layout
from promptpotter.domain.opt_search_point import (
    FewShotExample,
    L2L3Memory,
    OptSearchPoint,
)
from promptpotter.domain.pipeline_schema import (
    NodeOutputSchema,
    NodePromptInfo,
    ObservationMapping,
    PipelineNode,
    PipelineSchema,
    stable_hash,
)
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.shared.hashing import content_hash


def _prompt_node_schema(name: str = "llm_ranking") -> PipelineSchema:
    return PipelineSchema(nodes=[PipelineNode(name=name, prompt_info=NodePromptInfo())])


# --- JobSearchPoint: frozen, render, identity, derive -----------------------


def test_jsp_is_frozen_and_render_reads_pipeline_params() -> None:
    sp = JobSearchPoint(pipeline_params={"llm_ranking": {"prompt": "Rank by relevance."}})
    assert sp.render() == "Rank by relevance."
    with pytest.raises(pydantic.ValidationError):
        sp.pipeline_params = {"new": "val"}


def test_jsp_content_hash_distinguishes_pipeline_params() -> None:
    dataset = [Sample(id=1, query="q", ground_truth="a")]
    sp_a = JobSearchPoint(pipeline_params={"steps": ["llm_ranking"]})
    sp_b = JobSearchPoint(pipeline_params={"steps": ["fuzzy_matching"]})
    assert sp_a.content_hash(dataset) != sp_b.content_hash(dataset)
    assert sp_a.content_hash(dataset) == content_hash(sp_a.render(), dataset, sp_a.pipeline_params)


def test_jsp_derive_returns_new_frozen_instance() -> None:
    sp = JobSearchPoint(pipeline_params={"steps": ["llm_ranking"]})
    sp2 = sp.derive(pipeline_params={"steps": ["fuzzy_matching"]})
    assert sp2.pipeline_params == {"steps": ["fuzzy_matching"]}
    assert sp.pipeline_params == {"steps": ["llm_ranking"]}
    with pytest.raises(pydantic.ValidationError):
        sp2.pipeline_params = {"steps": ["c"]}


def test_jsp_pipeline_params_rejects_flat_param_map() -> None:
    """``pipeline_params`` is nested-by-node ⇒ a flat ``{param: value}`` map is rejected."""
    JobSearchPoint(pipeline_params={"llm_only": {"model": "x", "temperature": 0.1}})
    JobSearchPoint(pipeline_params={"steps": ["llm_ranking"], "llm_ranking": {"prompt": "x"}})
    JobSearchPoint(pipeline_params={})
    JobSearchPoint(pipeline_params=None)
    with pytest.raises(pydantic.ValidationError):
        JobSearchPoint(pipeline_params={"model": "x", "temperature": 0.1})


# --- OptSearchPoint: projection, render isolation, memory deep-copy ---------


def test_to_job_search_point_projects_prompt_fields_and_few_shot_block() -> None:
    osp = OptSearchPoint(
        persona="Expert",
        instruction="Rank items.",
        few_shot_examples=[FewShotExample(input="a", output="b")],
    )
    sp = osp.to_job_search_point(schema=_prompt_node_schema())
    assert sp.prompt_fields["persona"] == "Expert"
    assert sp.prompt_fields["instruction"] == "Rank items."
    assert "Input: a" in sp.prompt_fields["few_shot_block"]
    assert OptSearchPoint(instruction="X").to_job_search_point().prompt_fields is None


def test_render_does_not_leak_l3_plan_into_target_prompt() -> None:
    """L3's plan reaches L1/L2/L3 prompts via ``_r_plan`` only — never via ``render``."""
    sentinel = "REVISED_OPTIMIZATION_FRAMEWORK_PLAN_SENTINEL"
    osp = OptSearchPoint(persona="Expert", instruction="Solve.", plan=sentinel)
    assert sentinel not in osp.render()


def test_copy_memory_deep_copies_l1_layout_into_adoption_target() -> None:
    custom = L1Layout(
        persona=["plan"],
        task_intent=["task_context"],
        problem_description=["rendered_prompt", "pipeline_param_catalogue"],
    )
    parent = OptSearchPoint(memory=L2L3Memory(l1_layout=custom))
    child = OptSearchPoint()
    parent.copy_memory_to(child)
    assert child.memory.l1_layout.persona == ["plan"]
    assert child.memory.l1_layout.task_intent == ["task_context"]
    assert child.memory.l1_layout.problem_description == [
        "rendered_prompt",
        "pipeline_param_catalogue",
    ]
    child.memory.l1_layout.persona.append("diagnostics")
    assert "diagnostics" not in parent.memory.l1_layout.persona


# --- PipelineSchema: sparse projection --------------------------------------


def test_pipeline_schema_to_params_is_sparse() -> None:
    """``to_pipeline_params`` emits ``{steps}`` only — per-node defaults stay backend-owned."""
    schema = PipelineSchema(
        name="t",
        version="v",
        nodes=[
            PipelineNode(
                name="llm_only",
                runtime="backend",
                node_role="ranker",
                param_keys=("provider", "model", "temperature"),
                output_schema=NodeOutputSchema(),
                current_config={"provider": "groq", "model": "x", "temperature": 0.0},
            )
        ],
    )
    pp = schema.to_pipeline_params()
    assert pp == {"steps": ["llm_only"]}, (
        f"to_pipeline_params must be sparse; per-node defaults are backend-owned. Got {pp!r}"
    )
    payload = termnorm_wire_adapter("q", {**pp, "llm_only": {"temperature": 0.7}})
    assert payload["node_config"] == {"llm_only": {"temperature": 0.7}}, (
        "Wire payload carries only operator/optimizer overrides — not backend defaults. "
        f"Got {payload['node_config']!r}"
    )


# --- PipelineSchema: derivation + config-identity hash ----------------------


def test_pipeline_schema_node_param_keys_drops_unparametrized_nodes() -> None:
    schema = PipelineSchema(
        name="test",
        nodes=[
            PipelineNode(
                name="search",
                param_keys={"max_results"},
                observation_name="search",
                observation_mappings=[
                    ObservationMapping(pipeline_key="results", output_field="items")
                ],
                langfuse_type="tool",
            ),
            PipelineNode(
                name="rank",
                param_keys={"temperature"},
                observation_name="rank",
                observation_mappings=[ObservationMapping(pipeline_key="ranked", is_llm=True)],
                langfuse_type="generation",
            ),
            PipelineNode(name="cache", langfuse_type="span"),
        ],
    )
    assert schema.node_param_keys() == {"search": {"max_results"}, "rank": {"temperature"}}
    assert "cache" not in schema.node_param_keys()


def _three_node_schema() -> PipelineSchema:
    """a → b → c, with param_keys on a and b."""
    return PipelineSchema(
        name="test",
        nodes=[
            PipelineNode(name="a", param_keys={"max_results"}),
            PipelineNode(
                name="b", param_keys={"temperature"}, prompt_info=NodePromptInfo(family="p")
            ),
            PipelineNode(name="c"),
        ],
    )


def test_pipeline_schema_exclude_drops_named_nodes_and_returns_self_for_empty() -> None:
    schema = _three_node_schema()
    assert [n.name for n in schema.exclude({"b"}).nodes] == ["a", "c"]
    assert schema.exclude(None) is schema
    assert schema.exclude(set()) is schema


def test_pipeline_schema_node_configs_preserves_order_and_fills_empty() -> None:
    configs = _three_node_schema().node_configs(
        {"a": {"max_results": 5}, "b": {"temperature": 0.7}}
    )
    assert [name for name, _ in configs] == ["a", "b", "c"]
    assert configs[0][1] == {"max_results": 5}
    assert configs[2][1] == {}  # missing node → empty dict


def test_pipeline_schema_sp_hash_distinguishes_configs_and_handles_empty() -> None:
    schema = _three_node_schema()
    pp = {"a": {"max_results": 5}}
    assert schema.sp_hash(pp) == stable_hash(schema.node_configs(pp))
    assert schema.sp_hash(pp) != schema.sp_hash({"a": {"max_results": 10}})
    assert PipelineSchema(nodes=[]).sp_hash({}) == ""
