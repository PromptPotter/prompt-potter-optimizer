"""A wrong identity, or a quiet cross-contamination.

Owns the hashes a measurement is filed under (`sp_hash`, `prompt_hashes`, `hash_call`,
`inner_campaign_id`), `application/pipeline_resolve.py`, `domain/pipeline_overlay.py`, the held-out
partition and what reaches a scored prompt. Two things that differ answer as one, or one thing is
scored on text it was never meant to carry.
"""

from __future__ import annotations

import asyncio
import copy
import random
import types
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from factories import pipeline_schema

from promptpotter.application.bench.children import admission_failures, overlay_failures
from promptpotter.application.optimizers.potter.dispatch.layout import NODE_LAYOUTS
from promptpotter.application.optimizers.potter.records import L2L3Memory, Ladder
from promptpotter.domain.opt_search_point import OptSearchPoint, Variation
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import SCHEMA_TOGGLE_PARAM, PipelineSchema
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.store.io import read_yaml, write_yaml

_L1 = Variation(node="potter:l1_generate", mode="llm")

# 1. Hashes — what a measurement is filed under


def test_an_l4_override_moves_the_prompt_and_hash_of_every_preset() -> None:
    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.optimizer_manifest import (
        resolve_optimizer,
        set_optimizer_prompt_overrides,
    )
    from promptpotter.application.runner.inner.spawn import inner_campaign_id
    from promptpotter.application.runner.inner.tasks import InnerTaskSpec

    capo = resolve_optimizer("capo", {})
    round_ = types.SimpleNamespace(cycle=types.SimpleNamespace(optimizer=capo))
    ctx: NodeContext[Any] = NodeContext(cast(Any, round_), "capo_mutate")
    values = {"task_description": "sort the list", "instruction": "Sort it."}
    mutated = {
        "capo_mutate": {"instruction": "Rephrase [{{instruction}}] to {{task_description}}."}
    }
    try:
        set_optimizer_prompt_overrides(None)
        sent, hashes = ctx.fill(**values), capo.prompt_hashes()
        set_optimizer_prompt_overrides(mutated)
        assert ctx.fill(**values) == ("Rephrase [Sort it.] to sort the list.") != sent, (
            "the mutated CAPO prompt never reached the request it sends"
        )
        moved = capo.prompt_hashes()
        assert moved["capo_mutate"] != hashes["capo_mutate"]
        assert {k: v for k, v in moved.items() if k != "capo_mutate"} == {
            k: v for k, v in hashes.items() if k != "capo_mutate"
        }
    finally:
        set_optimizer_prompt_overrides(None)
    spec = InnerTaskSpec(
        inner_dataset="justlogic-d234", optimizer_treatment="o", seed=3, n_samples=28, n_rounds=4
    )
    assert inner_campaign_id(spec, mutated) != inner_campaign_id(spec, {})

    potter = resolve_optimizer("potter", {})
    try:
        set_optimizer_prompt_overrides(None)
        baseline = potter.prompt_hashes()
        assert potter.prompt_hashes() == baseline

        # Valid layout edit (`diagnostics` stays placed) on one node.
        set_optimizer_prompt_overrides(
            {"l1_critique": {"layout": {"axis_memory": "thinking_style"}}}
        )
        edited = potter.prompt_hashes()
        assert edited["l1_critique"] != baseline["l1_critique"], (
            "layout-only override left the node hash unchanged — audits would pool "
            "layout-differing cycles"
        )
        assert {k: v for k, v in edited.items() if k != "l1_critique"} == {
            k: v for k, v in baseline.items() if k != "l1_critique"
        }, "layout edit on one node must not move other nodes' hashes"
    finally:
        set_optimizer_prompt_overrides(None)


async def test_the_determinism_clamp_outranks_every_other_layer_and_keys_the_bank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from promptpotter.application.bench import llm_call as call_mod
    from promptpotter.application.campaign_config import DeterminismClamp
    from promptpotter.application.optimizer_manifest import set_determinism_clamp
    from promptpotter.infrastructure.llm.request import ChatRequest
    from promptpotter.infrastructure.llm.response import LLMResponse
    from promptpotter.infrastructure.store.llm_reuse_cache import LLMReuseCache

    sent: list[ChatRequest] = []

    class _Recorder:
        async def chat(self, request: ChatRequest, **_: Any) -> LLMResponse:
            sent.append(request)
            return LLMResponse(content="ok", model="m")

    monkeypatch.setattr(call_mod, "get_llm_client", lambda _provider: _Recorder())
    cache = LLMReuseCache(tmp_path, "optimizer_reuse")
    ctx = call_mod.LLMCallContext(cache=cache)

    async def ask(clamp: DeterminismClamp | None) -> None:
        set_determinism_clamp(clamp)
        await call_mod.llm_call(
            [{"role": "user", "content": "q"}],
            config={"provider": "openrouter", "model": "m", "temperature": 0.9},
            context=ctx,
            temperature=0.7,
        )

    try:
        pinned = DeterminismClamp(temperature=0.0, seed=7, route_order=["Alibaba"])
        await ask(pinned)
        assert sent[0].temperature == 0.0, (
            "the node's file value or the per-call override beat the clamp — the campaign "
            "reports itself pinned and runs unpinned"
        )
        assert sent[0].seed == 7
        assert sent[0].route_order == ["Alibaba"]

        await ask(pinned.model_copy(update={"route_order": ["Baidu"]}))
        assert len(sent) == 2, "the second route replayed the first route's banked answer"
        assert len(list((tmp_path / "optimizer_reuse").glob("*.json"))) == 2

        await ask(None)
        assert sent[2].temperature == 0.7
        assert sent[2].seed is None
        assert sent[2].route_order is None
    finally:
        set_determinism_clamp(None)


def test_inner_campaign_id_separates_two_candidates_and_is_stable() -> None:
    from promptpotter.application.optimizer_manifest import resolve_optimizer
    from promptpotter.application.runner.inner.spawn import inner_campaign_id
    from promptpotter.application.runner.inner.tasks import InnerTaskSpec
    from promptpotter.domain.pipeline_schema import ManifestNodeOverlay

    spec = InnerTaskSpec(
        inner_dataset="justlogic-d234", optimizer_treatment="o", seed=3, n_samples=28, n_rounds=4
    )
    c1 = {"l1_generate": {"instruction": "widen the axes"}}
    c2 = {"l1_generate": {"instruction": "narrow the axes"}}

    assert inner_campaign_id(spec, c1) != inner_campaign_id(spec, c2), (
        "two candidates differing only in one override field share a campaign — "
        "their measurements merge under one id with no error"
    )
    assert (
        len({inner_campaign_id(spec, c1), inner_campaign_id(spec, c2), inner_campaign_id(spec, {})})
        == 3
    )
    assert inner_campaign_id(spec.model_copy(update={"seed": 6}), c1) != inner_campaign_id(spec, c1)
    cut = {"pobb": ManifestNodeOverlay.model_validate({"config": {"epsilon": 0.3}})}
    as_run, as_cut = (resolve_optimizer("potter", n).treatment().digest for n in ({}, cut))
    assert inner_campaign_id(
        spec.model_copy(update={"optimizer_treatment": as_run}), c1
    ) != inner_campaign_id(spec.model_copy(update={"optimizer_treatment": as_cut}), c1)


def test_judge_identity_moves_the_searchpoint_hash() -> None:
    from promptpotter.application.pipeline_resolve import resolve_pipeline_config_params
    from promptpotter.domain.pipeline_parsing import parse_pipeline_response
    from promptpotter.judges.protocol import JudgeSpec, JudgeStage

    schema = parse_pipeline_response(
        {
            "nodes": {"llm_only": {"type": "llm", "config": {"model": "m", "provider": "p"}}},
            "pipelines": {"default": ["llm_only"]},
        }
    )
    active = schema.active_steps_excluding([])

    def sp_hash(judges: dict[str, JudgeSpec]) -> str:
        return schema.sp_hash(
            resolve_pipeline_config_params(
                active,
                {},
                None,
                schema,
                judges=judges,
                experiment=None,
                stores=None,
                workspace=None,
            )
        )

    def spec(name: str, model: str) -> JudgeSpec:
        return JudgeSpec(name=name, stages=[JudgeStage(model=model, provider="p")])

    one = {"answer": spec("sealqa", "a")}
    hashes = {
        "none": sp_hash({}),
        "sealqa@a": sp_hash(one),
        "sealqa@b": sp_hash({"answer": spec("sealqa", "b")}),
        # Same models, different rubric: the fingerprint hashes the judge's prompt text.
        "simpleqa@a": sp_hash({"answer": spec("simpleqa", "a")}),
        "rekeyed": sp_hash({"correctness": spec("sealqa", "a")}),
        "two_steps": sp_hash({"answer": spec("sealqa", "a"), "grounded": spec("simpleqa", "a")}),
    }
    assert len(set(hashes.values())) == len(hashes), f"judge identity collides: {hashes}"
    assert sp_hash(one) == hashes["sealqa@a"], "an unchanged judge must not move the key"
    assert (
        sp_hash({"grounded": spec("simpleqa", "a"), "answer": spec("sealqa", "a")})
        == hashes["two_steps"]
    ), "declaration order must not re-cut the measurement key"


def test_unframed_ablation_renders_no_framing_where_one_is_committed(built_stores: Any) -> None:
    from promptpotter.application.bench.task_context import campaign_framing
    from promptpotter.application.campaign_config import (
        CampaignConfig,
        OptimizationConfig,
        load_campaign_config,
    )

    built_stores.tenant_datasets.save_task_context(
        "gsm8k", TaskDecomposition(domain="grade-school arithmetic")
    )
    framed = CampaignConfig(optimization=OptimizationConfig(degradation_threshold=0.05))
    unframed = framed.model_copy(update={"task_framing": "off"})

    assert campaign_framing(built_stores, framed, "gsm8k").domain == "grade-school arithmetic"
    assert not campaign_framing(built_stores, unframed, "gsm8k"), (
        "the ablation arm rendered the committed framing it declared off"
    )
    frozen = load_campaign_config(unframed.frozen(arm=False))
    assert not campaign_framing(built_stores, frozen, "gsm8k"), (
        "the campaign manifest lost the declaration, so a resume of the ablation runs framed"
    )


def test_the_inner_benchmark_is_one_directory_to_identity_and_run(built_stores: Any) -> None:
    from promptpotter.application.runner.inner.connector import _identity_config
    from promptpotter.infrastructure.store.dataset_access import readable_dataset_dir
    from promptpotter.infrastructure.store.stores import build_stores

    outer = Path(__file__).resolve().parents[1] / "datasets" / "promptpotter-self"
    panel = {
        "inner_benchmark": "innerbench",
        "inner_benchmark_config": {"n_samples_per_inner_round": 4, "max_inner_rounds": 2},
        "tasks": [{"id": "seed-0"}],
    }
    install = built_stores.benchmarks_root / "innerbench"
    write_yaml(install / "pipeline.yaml", {"nodes": {"llm_only": {"config": {"model": "a"}}}})
    before = _identity_config(built_stores, outer, panel)

    tenant_copy = built_stores.tenant_datasets.dataset_dir("innerbench")
    write_yaml(tenant_copy / "pipeline.yaml", {"nodes": {"llm_only": {"config": {"model": "b"}}}})
    assert _identity_config(built_stores, outer, panel) != before, "identity hashed the wrong tier"

    sandbox = build_stores(
        built_stores.identity,
        projects_root=built_stores.base_dir / ".inner" / "cell",
        benchmarks_root=built_stores.benchmarks_root,
        shared_root=built_stores.shared_root,
    )
    assert readable_dataset_dir(sandbox, "innerbench") == tenant_copy, "the cell ran another tier"


def test_the_canonical_form_every_stored_key_is_cut_from_does_not_move() -> None:
    from promptpotter.infrastructure.store.measurement_archive import config_key
    from promptpotter.shared.hashing import ADDRESS_HEX, stable_hash

    value = {"b": 0.1, "a": {"ü": [1.5, None], "A": "Zürich 東京"}}
    reordered = {"a": {"A": "Zürich 東京", "ü": [1.5, None]}, "b": 0.1}

    assert stable_hash(value) == stable_hash(reordered) == "bff2239dc003d203"
    assert stable_hash(value, length=ADDRESS_HEX) == "bff2239dc003d203bdf1154c1e7222c6"
    assert config_key([("solve", value)]) == "f50c75d3792a48e09028a5e79dacd742"
    assert stable_hash({**value, "b": 0.10000000000000002}) != stable_hash(value)
    with pytest.raises(TypeError):
        stable_hash({"when": Path(".")})


def test_prose_moves_no_source_digest_and_a_code_token_does() -> None:
    from promptpotter.shared.hashing import source_facts

    def digest(text: str) -> str:
        return "".join(unit["source"] for unit in source_facts(text)["units"])

    marked = "from promptpotter.shared.hashing import shapes_optimizer_prompt\n"
    bare = marked + (
        "shapes_optimizer_prompt(None)\n"
        "LIMIT = 3\n"
        "class Arm:\n"
        "    width: int = 2\n"
        "    def cut(self, n):\n"
        "        if n > LIMIT:\n"
        "            return n - self.width\n"
        "        return n\n"
    )
    documented = marked + (
        '"""Module docstring."""\n'
        "shapes_optimizer_prompt(None)\n"
        "LIMIT = 3  # a comment\n"
        '"""Attribute docstring."""\n'
        "class Arm:\n"
        '    """Class docstring."""\n'
        "    width: int = 2\n"
        '    """Attribute docstring."""\n'
        "    def cut(self, n):\n"
        '        """Function docstring."""\n'
        "        if n > LIMIT:\n"
        '            "a bare string in a branch"\n'
        "            return n - self.width\n"
        "        return n\n"
    )
    assert digest(bare) == digest(documented), "prose re-keyed a treatment"
    assert digest(bare.replace("n > LIMIT", "n >= LIMIT")) != digest(bare)
    assert digest(bare.replace("LIMIT = 3", "LIMIT = 4")) != digest(bare)
    only_prose = marked + 'shapes_optimizer_prompt(None)\ndef f():\n    """Doc."""\n'
    assert digest(only_prose) == digest(only_prose.replace('"""Doc."""', "pass"))


# 2. The config a campaign runs


def test_the_drafts_CHAIN_reaches_the_mint_on_a_reused_dataset(tmp_path: Path) -> None:
    from promptpotter.application.jobs.launcher.mint_and_start import build_cycle_config
    from promptpotter.connectors import DEFAULT_CONNECTOR, get
    from promptpotter.infrastructure.store.io import write_yaml

    root = tmp_path / "ds"
    root.mkdir()
    write_yaml(
        root / "campaign.yaml",
        {"campaign_config": {"optimization": dict(get(DEFAULT_CONNECTOR).default_optimization)}},
    )
    schema = parse_pipeline_response(
        {
            "nodes": {n: {"type": "llm"} for n in ("llm_only", "web_search", "rerank")},
            "pipelines": {"default": ["web_search", "rerank", "llm_only"]},
        }
    )
    session = types.SimpleNamespace(pipeline_schema=schema)

    chosen = build_cycle_config(
        cast(Any, session), root, optimization={"optimizer": "capo"}, pipeline_steps=["llm_only"]
    )
    assert sorted(chosen.exclude_nodes) == ["rerank", "web_search"]
    assert schema.active_steps_excluding(chosen.exclude_nodes) == ["llm_only"]
    assert chosen.optimization.optimizer == "capo"
    # `pipeline_steps=[]` is "untouched": excluding its complement would close the whole pipeline.
    untouched = build_cycle_config(cast(Any, session), root, optimization={}, pipeline_steps=[])
    assert untouched.exclude_nodes == []


def _pipeline_schema(dataset: str) -> PipelineSchema:
    path = Path(__file__).resolve().parents[1] / "datasets" / dataset / "pipeline.yaml"
    return parse_pipeline_response(yaml.safe_load(path.read_text(encoding="utf-8")))


def test_answering_in_TEXT_sends_no_contract_to_answer_INTO() -> None:
    from promptpotter.application.pipeline_resolve import resolved_output_schemas
    from promptpotter.domain.pipeline_overlay import fold_output_contract
    from promptpotter.domain.pipeline_schema import ANSWER_AS_TEXT, description_key

    schema = _pipeline_schema("justlogic-d234")
    base = {n.name: dict(n.current_config) for n in schema.declared_nodes}
    assert "output_schema" in base["llm_only"] and "answer_field" in base["llm_only"]

    assert fold_output_contract(base, schema) == base, "an unmoved point must hash as it always did"

    steered = copy.deepcopy(base)
    steered["llm_only"][SCHEMA_TOGGLE_PARAM] = ANSWER_AS_TEXT
    steered["llm_only"][description_key("answer")] = "IGNORED"
    chose_text = fold_output_contract(steered, schema)
    assert "output_schema" not in chose_text["llm_only"]
    assert "answer_field" not in chose_text["llm_only"]
    assert description_key("answer") not in chose_text["llm_only"]

    assert resolved_output_schemas(schema, base)["llm_only"] is not None
    text_point = {**base, "llm_only": {**base["llm_only"], SCHEMA_TOGGLE_PARAM: ANSWER_AS_TEXT}}
    assert resolved_output_schemas(schema, text_point)["llm_only"] is None

    items = {"type": "object", "properties": {"amount": {"type": "string"}}}
    out = {"lines": {"type": "array", "items": items}, "total": {"type": "number"}}
    nested = parse_pipeline_response(
        {
            "nodes": {
                "llm_only": {
                    "type": "llm",
                    "config": {
                        "model": "m",
                        "output_schema": {"type": "object", "properties": out},
                    },
                    "optimizer": {},
                }
            },
            "pipelines": {"default": ["llm_only"]},
        }
    )
    amount = description_key("lines.amount")
    declared = cast(Any, nested.get_node("llm_only")).current_config
    pp = fold_output_contract({"llm_only": {**declared, amount: "Net, in CHF."}}, nested)
    line = pp["llm_only"]["output_schema"]["properties"]["lines"]["items"]["properties"]["amount"]
    assert line["description"] == "Net, in CHF." and amount not in pp["llm_only"]
    # The fold leaves the declared schema it read unmutated: the parent individual still holds it.
    assert "description" not in items["properties"]["amount"]


def _frozen_config(**fields: Any) -> dict[str, Any]:
    from promptpotter.application.campaign_config import load_campaign_config
    from promptpotter.connectors import DEFAULT_CONNECTOR, get

    defaults = dict(get(DEFAULT_CONNECTOR).default_optimization)
    optimization = {**defaults, **fields.pop("optimization", {})}
    return load_campaign_config({"optimization": optimization, **fields}).frozen(arm=False)


def test_a_campaign_runs_the_config_it_froze_whatever_its_dataset_file_says_later(
    built_stores: Any,
) -> None:
    from promptpotter.application.pipeline_resolve import resolve_campaign_config
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.cycle_paths import CycleDir
    from promptpotter.domain.run_records import ConfigOverrides, CycleSeed, ScoringLockedRecord
    from promptpotter.infrastructure.ledger import CycleEventLog

    stores = built_stores
    dials = {"per_sample": "exact", "dials": "cost: 0.1"}
    write_yaml(
        stores.tenant_datasets.dataset_dir("ds") / "campaign.yaml",
        {"campaign_config": _frozen_config(optimization={"optimizer": "capo", "max_rounds": 3})},
    )
    campaign = Campaign(
        campaign_id="ds__000001",
        dataset_name="ds",
        created_at="2026-09-27T00:00:00Z",
        root_cycle_id="cycle_root",
        owner_user_id=str(stores.identity.user_id),
        config=_frozen_config(
            optimization={"max_rounds": 12},
            optimizer_narrowing={"a": {"param_keys": ["temperature"]}, "b": {"param_keys": ["k"]}},
            scoring=dials,
        ),
    )
    stores.campaigns.create_campaign(campaign)
    stores.campaigns.mint_cycle(campaign.root_hop)
    ran = resolve_campaign_config(stores, campaign, campaign.root_hop)
    assert (ran.optimization.optimizer, ran.optimization.max_rounds) == ("potter", 12)

    # The criterion its origin locked rides the root's ledger, and every cycle runs under it.
    ledger = CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(campaign.root_hop)))
    ledger.append(ScoringLockedRecord(declared={"dials": "other"}, locked={"per_cell": "0"}))
    assert resolve_campaign_config(stores, campaign, campaign.root_hop).scoring == dials
    locked = {"per_sample": "exact", "per_cell": "correct - 0.1 * cost"}
    ledger.append(ScoringLockedRecord(declared=dials, locked=locked))
    assert resolve_campaign_config(stores, campaign, None).scoring == locked
    assert resolve_campaign_config(stores, campaign, campaign.root_hop).scoring == locked

    seed = CycleSeed(
        optimizer_narrowing={"a": {"param_keys": []}},
        config_overrides=ConfigOverrides(max_rounds=4),
    )
    stores.campaigns.write_cycle_seed(campaign.root_hop, seed)
    forked = resolve_campaign_config(stores, campaign, campaign.root_hop)
    assert forked.optimization.max_rounds == 4
    assert [forked.optimizer_narrowing[n].param_keys for n in "ab"] == [[], ["k"]]


def test_a_row_reads_as_the_search_space_its_ticks_and_padlock_drew() -> None:
    from promptpotter.domain.pipeline_schema import NodeConfigParam, ParamIntent, narrowing_of

    rows = [
        NodeConfigParam(key="model", kind="model", options=["a", "b"], permitted=["a"]),
        NodeConfigParam(key="effort", kind="enum", options=["low", "mid", "high"]),
        NodeConfigParam(key="temperature", kind="number"),
    ]

    def declared(
        key: str, ticks: list[str], *, unlocked: bool = True
    ) -> tuple[bool, list[str] | None]:
        intent = ParamIntent(key=key, open=unlocked, allowed=ticks)
        out = narrowing_of(rows, [intent])
        return key in (out.param_keys or []), out.param_allowed_values.get(key)

    # Every tick on declares nothing: an absent entry IS the menu.
    assert declared("effort", ["low", "mid", "high"]) == (True, None)
    assert declared("effort", ["low", "high"]) == (True, ["low", "high"])
    # One tick is a pin: shut, yet still stated, because a human fork may steer to it.
    assert declared("model", ["a"]) == (False, ["a"])
    assert declared("effort", ["low", "max"]) == (True, ["low", "max"])
    # Nothing ticked is an axis with nothing legal, never one with no bound.
    assert declared("effort", []) == (False, None)
    assert declared("temperature", []) == (True, None)
    assert declared("temperature", ["0.2"], unlocked=False) == (False, None)
    # A full menu over a server-stated `permitted` is stated: the declaration replaces the node's.
    assert declared("model", ["a", "b"]) == (True, ["a", "b"])


def test_a_draft_edit_sent_as_rows_freezes_the_search_space_the_overlay_spelled(
    built_stores: Any,
) -> None:
    from promptpotter.application.datasets.draft_campaign import (
        EditDraftPatch,
        draft_campaign_config,
        new_draft,
    )
    from promptpotter.application.datasets.draft_patch import apply_draft_patch, plan_draft_patch
    from promptpotter.domain.pipeline_schema import NodeSearchNarrowing

    declared = {
        "type": "llm",
        "config": {"model": "m", "reasoning_effort": "low", "temperature": 0.2},
        "optimizer": {
            "param_keys": ["reasoning_effort", "temperature"],
            "param_allowed_values": {"reasoning_effort": ["low", "mid", "high"]},
        },
    }
    draft = new_draft(
        tenant_id=built_stores.identity.tenant_id,
        slug="d",
        n_samples=1,
        sample_preview=[],
        headers=[],
    ).patch(backend_nodes={"llm_only": declared})

    def frozen(**patch: Any) -> Any:
        plan = plan_draft_patch(built_stores, draft, EditDraftPatch.model_validate(patch))
        return draft_campaign_config(apply_draft_patch(draft, plan))

    rows = [
        {"key": "reasoning_effort", "open": True, "allowed": ["low", "high"]},
        {"key": "temperature", "open": False, "allowed": []},
    ]
    spelled = {
        "param_keys": ["reasoning_effort"],
        "param_allowed_values": {"reasoning_effort": ["low", "high"]},
    }
    as_rows = frozen(node_narrowing={"llm_only": rows}).optimizer_narrowing
    assert as_rows == {"llm_only": NodeSearchNarrowing(**spelled)}
    as_overlay = frozen(pipeline_overlay={"llm_only": {"optimizer": spelled}})
    assert as_rows == as_overlay.optimizer_narrowing

    # No field named `answer`, none named by the operator: the slot is the LAST, reasoning first.
    schema = {"type": "object", "properties": {"reasoning": {}, "code": {}}}
    authored = frozen(node_output={"node": "llm_only", "output_schema": schema}).pipeline_overlay
    contract = {"output_schema": schema, "answer_field": "code", "response_format": "json"}
    assert authored == frozen(pipeline_overlay={"llm_only": {"config": contract}}).pipeline_overlay
    assert authored["llm_only"]["answer_field"] == "code"


def test_a_fork_closes_the_axis_its_operator_closed_on_the_forked_cycles_own_menu(
    built_stores: Any,
) -> None:
    """Ticks equal to the ROOT's whole menu still close the rung only the fork's menu carries."""
    from promptpotter.application.commands.launching import _parse_cycle_seed
    from promptpotter.application.pipeline_resolve import resolve_campaign_config
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.pipeline_schema import NodeSearchNarrowing

    def declaring(rungs: list[str]) -> dict[str, Any]:
        node = {
            "type": "llm",
            "config": {"model": "m", "reasoning_effort": "low", "temperature": 0.0},
            "optimizer": {
                "param_keys": ["reasoning_effort", "temperature"],
                "param_allowed_values": {"reasoning_effort": rungs},
            },
        }
        return {"nodes": {"a": node}, "pipelines": {"default": ["a"]}}

    stores = built_stores
    campaign = Campaign(
        campaign_id="ds__000001",
        dataset_name="ds",
        created_at="2026-09-27T00:00:00Z",
        root_cycle_id="cycle_root",
        owner_user_id=str(stores.identity.user_id),
        config=_frozen_config(),
    )
    stores.campaigns.create_campaign(campaign)
    forked = CycleHop(campaign_id=campaign.campaign_id, cycle_id="cycle_fork")
    for hop, rungs in ((campaign.root_hop, ["low", "high"]), (forked, ["low", "high", "xhigh"])):
        stores.campaigns.mint_cycle(hop)
        stores.campaigns.write_resolved_pipeline(hop, declaring(rungs))

    rows = [
        {"key": "reasoning_effort", "open": True, "allowed": ["low", "high"]},
        {"key": "temperature", "open": False, "allowed": []},
    ]
    seed = _parse_cycle_seed(
        {"node_narrowing": {"a": rows}},
        resolve_campaign_config(stores, campaign, None),
        stores,
        campaign,
        forked,
    )
    assert seed.optimizer_narrowing == {
        "a": NodeSearchNarrowing(
            param_keys=["reasoning_effort"],
            param_allowed_values={"reasoning_effort": ["low", "high"]},
        )
    }


def test_backend_row_names_the_endpoint_the_run_actually_reached(built_stores: Any) -> None:
    from promptpotter.application.initialization.wiring import _resolve_backend_id
    from promptpotter.domain.backend import BackendConnection

    built_stores.backends.register(
        BackendConnection(
            id="box", name="n", backend_type="termnorm", base_url="http://10.0.0.5:8000"
        )
    )

    resolved = _resolve_backend_id(built_stores, "local", "http://10.0.0.5:8000/", "termnorm", "n")
    assert resolved == "box"
    assert len(built_stores.backends.list_all()) == 1

    minted = _resolve_backend_id(built_stores, "box", "http://127.0.0.1:8000", "termnorm", "n")
    assert minted != "box"
    row = built_stores.backends.get(minted)
    assert row is not None
    assert row.base_url == "http://127.0.0.1:8000"


_YAML_1_1_HAZARDS = (
    "TRUE",
    "FALSE",
    "True",
    "no",
    "No",
    "yes",
    "on",
    "off",
    "OFF",
    "y",
    "n",
    "null",
    "~",
    "1",
    "1.5",
    "0755",
    "1e5",
    "2026-07-26",
    "v1.0",
)


def test_yaml_emitter_never_reinterprets_a_string_it_wrote(tmp_path: Path) -> None:
    path = tmp_path / "hazards.yaml"
    payload = {k: k for k in _YAML_1_1_HAZARDS} | {"nested": {"labels": list(_YAML_1_1_HAZARDS)}}
    write_yaml(path, payload)
    assert read_yaml(path) == payload


def _outer_schema(root: Path) -> PipelineSchema:
    from promptpotter.application.pipeline_resolve import (
        dataset_pipeline_declaration,
        experiment_outside_run,
    )
    from promptpotter.infrastructure.store.stores import build_stores
    from promptpotter.shared.identity import default_identity

    outer = Path(__file__).resolve().parents[1] / "datasets" / "promptpotter-self"
    stores = build_stores(default_identity(), projects_root=root)
    declared = dataset_pipeline_declaration(stores, outer, experiment_outside_run(outer))
    assert declared is not None
    return parse_pipeline_response(declared)


def test_emittable_params_are_declared_and_an_invented_one_is_rejected(tmp_path: Path) -> None:
    from promptpotter.application.optimizer_manifest import resolve_optimizer
    from promptpotter.application.optimizers.potter.dispatch.l1_wire_schema import (
        build_l1_response_schema,
    )

    schema = _outer_schema(tmp_path)
    emitted = build_l1_response_schema(
        schema, citable_fields=(), inner_optimizer=resolve_optimizer("potter", {})
    )["properties"]["variants"]["items"]["properties"]["pipeline_overlay"]["properties"]
    for node, keys in schema.node_param_keys().items():
        assert set(emitted[node]["properties"]) <= keys, (
            f"{node}: the schema declares a key `overlay_failures` rejects as unknown_param"
        )

    reasons = [
        (f.axis, f.reason) for f in overlay_failures({"l1_generate": {"invented_knob": 1}}, schema)
    ]
    assert reasons == [("l1_generate.invented_knob", "unknown_param")]
    assert [f.reason for f in overlay_failures({"l1_critique": {"layout": "hdr"}}, schema)] == [
        "type_mismatch"
    ]
    assert overlay_failures({"l1_critique": {"layout": {"instruction": ["plan"]}}}, schema) == []

    # A deletion is a move like any other: an emptied field is a locus the variation wrote.
    parent = OptSearchPoint(persona="You grade.", instruction="Answer {{combined_text}}.")
    wiped = OptSearchPoint.derive([parent], variation=_L1, persona="")
    assert wiped.lineage.variations[0].loci == ["persona"] and wiped.lineage.parent_ids == [
        parent.id
    ]
    found = admission_failures(wiped, {"l1_generate": {"invented_knob": 1}}, schema)
    assert [f.reason for f in found] == ["unknown_param"]


# 3. Contamination of a scored prompt


def test_rewriting_the_prompt_panel_cannot_accumulate_the_operator_framing() -> None:
    from promptpotter.application.optimizers.potter.dispatch.bundle import (
        CycleSlice,
        InjectionBundle,
        RoundDigest,
    )
    from promptpotter.application.optimizers.potter.dispatch.injections.layer_state import (
        _r_rendered_prompt,
        _r_task_context,
    )
    from promptpotter.domain.round_diagnostics import RoundDiagnostics

    upstream = "Raw invoice text is provided directly as the input column."
    framing = TaskDecomposition(
        upstream_context=upstream,
        downstream_context="The assigned code books a ledger entry.",
    )
    opt_sp = OptSearchPoint(
        persona="You assign Swiss account codes.",
        problem_description="Assign the four-digit account code for the invoice.",
    )
    bundle = InjectionBundle(
        opt_sp=opt_sp,
        memory=L2L3Memory(),
        framing=framing,
        pipeline_schema=None,
        cycle_slice=CycleSlice(
            round_num=1,
            l1_stall_depth=0,
            ladder=Ladder(),
            exploration_budget="tight",
        ),
        digest=RoundDigest(diagnostics=RoundDiagnostics(n_valid=0, samples=[]), critique=None),
        axes=None,
        prompt_block_catalogue="guidance",
        rebase_capability=True,
        terminate_capability=True,
        schema_field_rename=False,
        shot_k_max=0,
    )
    panel = "\n\n".join(i.text for i in _r_rendered_prompt(bundle))
    shown = {
        label.removeprefix("[").removesuffix("]"): body
        for label, _, body in (s.partition("\n") for s in panel.split("\n\n"))
        if label.startswith("[")
    }
    rewritten = OptSearchPoint.derive([opt_sp], variation=_L1, **shown)
    assert rewritten.render_target(framing, demo=()) == opt_sp.render_target(framing, demo=())
    # Dropping the framing from either render trades this bug for a blinder one.
    assert upstream in opt_sp.render_target(framing, demo=())
    assert upstream in "".join(i.text for i in _r_task_context(bundle))


def test_a_controlled_arm_remembers_only_the_answers_its_own_line_walked(built_stores: Any) -> None:
    import contextvars

    from promptpotter.domain.sample import ArchiveEntry
    from promptpotter.domain.scoring import MeasuredCell
    from promptpotter.infrastructure.store.archive_queries import (
        SampleFoldRow,
        list_populations,
        load_population,
        note_walked,
        sample_fold_rows,
        scope_memory_to_own_answers,
        write_sample_fold,
    )
    from promptpotter.infrastructure.store.measurement_archive import config_key

    def entry(name: str) -> ArchiveEntry:
        return ArchiveEntry(
            config_key=config_key([("", {"individual": name})]),
            prompt_fields_id=name,
            rendered_prompt_hash="",
            node_configs=[],
            pipeline_params={},
            dataset_name="ds",
        )

    def bank(name: str, said: list[str], at: int) -> list[str]:
        return built_stores.archive.file_answers(
            entry(name),
            [(MeasuredCell(sample_id=i, predicted=p), "A") for i, p in enumerate(said)],
            role="panel",
            source="optimization_loop",
            created_at=f"2026-09-28T00:00:{at:02d}Z",
        )

    def listed() -> set[str]:
        return {e.prompt_fields_id for e in list_populations(built_stores, dataset_name="ds")}

    def remembered(name: str) -> list[str]:
        return [answer.cell.predicted for answer in load_population(built_stores, entry(name))]

    bank("foreign", ["yes", "no"], 1)
    bank("shared", ["no", "no"], 2)

    def fold_row(key: str) -> SampleFoldRow:
        return SampleFoldRow(config_key=key, sp=key, fk="formula", sig=[], graded=[])

    write_sample_fold(built_stores, dataset_name="ds", rows=[fold_row("x")])

    def inside_arm() -> tuple[set[str], list[str], list[SampleFoldRow]]:
        scope_memory_to_own_answers(set(bank("origin", ["yes", "no"], 3)))
        assert listed() == {"origin"}
        # A replayed answer joins the arm's memory exactly as a filed one does.
        for answer in bank("shared", ["yes", "yes"], 0):
            note_walked(answer)
        write_sample_fold(built_stores, dataset_name="ds", rows=[fold_row("y")])
        return listed(), remembered("shared"), sample_fold_rows(built_stores, dataset_name="ds")

    own, shared, fold = contextvars.copy_context().run(inside_arm)
    assert own == {"origin", "shared"}
    assert shared == ["yes", "yes"], "another campaign's answer of a shared cell steered the arm"
    assert fold == []
    assert listed() == {"foreign", "shared", "origin"}
    assert remembered("shared") == ["no", "no"]
    assert sample_fold_rows(built_stores, dataset_name="ds") == [fold_row("x")]


def test_no_held_out_row_reaches_a_round_panel_or_an_archive_view() -> None:
    from promptpotter.application.intelligence.indexes.sample import SampleFoldRow, SampleIndex
    from promptpotter.application.intelligence.rasch import (
        Observation,
        select_round_subset,
    )
    from promptpotter.application.optimizers.capo.members import cross_shots, mutate_shots
    from promptpotter.application.optimizers.potter.validators.l1_strict import (
        L1_SHOTS_IN_DEMO_POOL,
    )
    from promptpotter.domain.bench import DatasetSplit, partition_bank

    bank = [Sample(id=i, query=f"claim {i}", ground_truth="TRUE") for i in range(60)]
    split = DatasetSplit(bench=12, demo=6)
    part = partition_bank(bank, split)
    held = {s.id for s in (*part.bench, *part.demo)}
    assert (len(part.bench), len(part.demo), len(part.search)) == (12, 6, 42)
    assert held.isdisjoint(s.id for s in part.search)
    assert {s.id for s in part.bench}.isdisjoint(s.id for s in part.demo)

    demo_ids = frozenset(s.id for s in part.demo)
    for leak in (part.search[0].id, part.bench[0].id):
        outcome = L1_SHOTS_IN_DEMO_POOL.check(
            {"shot_ids": [part.demo[0].id, leak]}, demo_ids=demo_ids, k_max=len(demo_ids)
        )
        rejected = [f.value for f in outcome.evidence["failures"]] if outcome else []
        assert rejected == [str(leak)], f"a shot naming scored row {leak} was accepted"
    # Asserts the SUPPORT, never the stream: add, drop and keep each occur over the seeds.
    pool = sorted(demo_ids)
    moves = set()
    for seed in range(60):
        out = mutate_shots(pool[:2], pool, k_max=3, rng=random.Random(seed))
        assert set(out) <= demo_ids and len(set(out)) == len(out) <= 3
        moves.add(len(out) - 2)
    assert moves == {-1, 0, 1}
    assert all(
        len(mutate_shots(pool[:3], pool, k_max=3, rng=random.Random(s))) <= 3 for s in range(30)
    )
    child = cross_shots([pool[:2], pool[2:6]], rng=random.Random(0))
    assert len(child) == 3 and set(child) <= demo_ids
    assert {s.id for s in partition_bank(bank[::-1], split).bench} == {s.id for s in part.bench}
    twins = [*bank, *(s.model_copy(update={"id": 60 + s.id}) for s in bank)]
    doubled = partition_bank(twins, split)
    assert {s.key for s in doubled.bench}.isdisjoint(s.key for s in doubled.search)
    assert len(doubled.bench) == 2 * split.bench
    # A declared bench row moves no other row, so widening a bench keeps every paid cell's pool.
    extra = [
        Sample(id=60 + i, query=f"held {i}", ground_truth="TRUE", bench_only=True) for i in range(9)
    ]
    assert extra[0].key == extra[0].model_copy(update={"bench_only": False}).key
    wide = partition_bank([*bank, *extra], split)
    assert (wide.search, wide.demo) == (part.search, part.demo)
    assert wide.bench == (*part.bench, *extra)
    with pytest.raises(ValueError):
        partition_bank([*bank, *extra], None)
    with pytest.raises(ValueError):
        partition_bank([*bank, bank[0].model_copy(update={"id": 60, "bench_only": True})], split)

    contaminating = [Observation("bench_arm", sid, 0.0) for sid in held]
    panel = select_round_subset(list(part.search), contaminating, 20)
    assert held.isdisjoint(s.id for s in panel)

    index = SampleIndex(sample_ids=part.admitted_ids)
    for n in range(12):
        index.replay_row(
            SampleFoldRow(
                config_key=f"bench_{n}",
                sp=f"bench_{n}",
                fk="formula",
                sig=[],
                graded=[],
                cells=[(sid, True, False, None) for sid in held],
                new_samples=[(sid, f"claim {sid}", "TRUE") for sid in held],
            )
        )
    assert not index.rare_hit_samples() and not index.records(), (
        "a held-out row reached the index the optimizer's panels read"
    )


def test_the_check_in_model_reads_no_held_out_row(
    built_stores: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from promptpotter.application.datasets import ingest
    from promptpotter.application.datasets.origin_resolve import build_origin_consultation
    from promptpotter.domain.bench import DatasetSplit, partition_bank
    from promptpotter.domain.sample import Sample

    bank = [Sample(id=i, query=f"claim {i}", ground_truth="TRUE") for i in range(40)]
    built_stores.tenant_datasets.save_benchmark_rows("heldout", bank)
    dataset_dir = tmp_path / "heldout"
    dataset_dir.mkdir()
    (dataset_dir / "campaign.yaml").write_text(
        "campaign_config:\n  scoring: label_match(predicted, ground_truth)\n"
        "  dataset_split:\n    bench: 30\n  optimization:\n    degradation_threshold: 0.4\n",
        encoding="utf-8",
    )

    async def _offline(*_: Any, **__: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(ingest, "refresh_capabilities", _offline)
    monkeypatch.setattr(ingest, "fetch_backend_nodes", _offline)
    draft = asyncio.run(
        ingest.draft_from_dataset(
            stores=built_stores, dataset_dir=dataset_dir, dataset_name="heldout"
        )
    )
    content, _ = build_origin_consultation(draft)
    held = partition_bank(bank, DatasetSplit(bench=30)).bench
    assert not [s.id for s in held if f'"{s.query}"' in content], "a bench row reached the resolver"


def test_the_l4_generator_is_shown_the_optimizer_prompts_it_rewrites() -> None:
    """The prompts shown are the INNER campaign's manifest's, never the outer's own."""
    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.optimizer_manifest import (
        resolve_optimizer,
        set_optimizer_prompt_overrides,
    )
    from promptpotter.application.optimizers.potter.dispatch.bundle import (
        CycleSlice,
        InjectionBundle,
        RoundDigest,
    )
    from promptpotter.application.optimizers.potter.dispatch.facade import DispatchHub
    from promptpotter.application.optimizers.potter.dispatch.prompts import (
        base_optimizer_template,
        load_optimizer_prompt,
    )
    from promptpotter.application.optimizers.potter.l1.population import parse_population
    from promptpotter.application.runner.inner.connector import promptpotter_wire_adapter
    from promptpotter.domain.opt_search_point import PROMPT_STRING_FIELDS
    from promptpotter.domain.pipeline_schema import PipelineNode
    from promptpotter.domain.round_diagnostics import RoundDiagnostics

    fields = list(PROMPT_STRING_FIELDS)

    def outer_schema(nodes: tuple[str, ...]) -> PipelineSchema:
        return pipeline_schema(
            name="promptpotter-self",
            version="1",
            nodes=[
                PipelineNode(
                    name=name,
                    kind="llm",
                    param_keys=fields,
                    param_types=dict.fromkeys(fields, "string"),
                    tunes_llm=False,
                )
                for name in nodes
            ],
        )

    schema = outer_schema(tuple(NODE_LAYOUTS))
    bundle = InjectionBundle(
        opt_sp=OptSearchPoint(),
        memory=L2L3Memory(),
        framing=TaskDecomposition(),
        pipeline_schema=schema,
        cycle_slice=CycleSlice(
            round_num=1,
            l1_stall_depth=0,
            ladder=Ladder(),
            exploration_budget="tight",
        ),
        digest=RoundDigest(diagnostics=RoundDiagnostics(n_valid=0, samples=[]), critique=None),
        axes=None,
        prompt_block_catalogue="guidance",
        rebase_capability=True,
        terminate_capability=True,
        schema_field_rename=False,
        shot_k_max=0,
        inner_optimizer=resolve_optimizer("potter", {}),
    )

    subject = DispatchHub.render_items("rendered_prompt", bundle)
    subject_chars = sum(len(i.text) for i in subject)
    allowance = NODE_LAYOUTS["l1_generate"].discretionary_chars
    assert subject_chars > allowance, (
        f"vacuous — the inner optimizer prompts ({subject_chars}c) now fit inside the "
        f"discretionary allowance ({allowance}c), so nothing is being kept against a budget"
    )

    filled = DispatchHub.fill(load_optimizer_prompt("l1_generate"), bundle, node="l1_generate")
    assert len(filled.rendered["rendered_prompt"]) >= subject_chars, (
        "the generator was handed no subject — it is rewriting text it cannot see"
    )
    starved = set(filled.breakdown.dropped) & NODE_LAYOUTS["l1_generate"].mandatory
    assert not starved, f"mandatory panel(s) refused by the budget: {sorted(starved)}"

    capo = resolve_optimizer("capo", {})
    capo_schema = outer_schema(capo.llm_nodes)
    capo_bundle = replace(bundle, pipeline_schema=capo_schema, inner_optimizer=capo)
    shown = "".join(i.text for i in DispatchHub.render_items("rendered_prompt", capo_bundle))
    base = base_optimizer_template(capo, "capo_mutate").instruction
    assert f"[capo_mutate.instruction]\n{base}" in shown, "a CAPO inner's prompt was never shown"

    parent = OptSearchPoint()
    edit = {"capo_mutate": {"instruction": base + " Keep the rewrite under 80 words."}}
    severed = {"capo_mutate": {"instruction": "Rewrite the prompt."}}
    proposing = types.SimpleNamespace(
        cycle=types.SimpleNamespace(
            optimizer=capo, session=types.SimpleNamespace(pipeline_schema=capo_schema)
        )
    )
    proposals = [
        NodeContext(cast(Any, proposing), "capo_mutate").child([parent], overlay=overlay)
        for overlay in (edit, severed)
    ]
    parse_population(
        proposals,
        parent,
        None,
        capo_schema,
        runtime_failures=[],
        demo_ids=frozenset(),
        shot_k_max=0,
        inner_optimizer=capo,
        prompt_block_catalogue="guidance",
    )
    assert proposals[0].validation_failures == []
    assert [f.reason for f in proposals[1].validation_failures] == [
        "dropped_mandatory_placeholder"
    ], "an edit severing CAPO's {{instruction}} port was measured"
    assert proposals[0].opt_sp.lineage.variations[0].loci == ["capo_mutate.instruction"], (
        "the overlay's locus is missing from the variation that proposed it"
    )

    payload = promptpotter_wire_adapter(
        "justlogic-d234/seed-0", proposals[0].opt_sp.pipeline_params
    )
    try:
        set_optimizer_prompt_overrides(payload["optimizer_prompt_overrides"])
        round_ = types.SimpleNamespace(cycle=types.SimpleNamespace(optimizer=capo))
        sent = NodeContext(cast(Any, round_), "capo_mutate").fill(
            task_description="sort the list", instruction="Sort it."
        )
    finally:
        set_optimizer_prompt_overrides(None)
    assert sent.endswith("Keep the rewrite under 80 words."), "the inner cell ran the parent"
