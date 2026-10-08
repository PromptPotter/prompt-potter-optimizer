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

from promptpotter.application.optimizers.potter.dispatch.layout import (
    NODE_LAYOUTS,
    default_l1_layout,
)
from promptpotter.application.optimizers.potter.records import L2L3Memory
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import SCHEMA_TOGGLE_PARAM, PipelineSchema
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.store.io import read_yaml, write_yaml

# 1. Hashes — what a measurement is filed under


def test_an_l4_override_moves_the_prompt_and_hash_of_every_preset() -> None:
    """A layout-only L4 edit changes which evidence a node sees, so it must move
    that node's ``optimizer_prompt_hash`` — otherwise cross-cycle audits joining
    on the hash silently pool layout-differing inner cycles. Prose-hash behavior is
    untouched: no override → identical hashes.

    A paper preset's prompt-field edit must reach the request it sends: an inner cell under a
    mutated CAPO prompt otherwise re-measures the parent under the arm's own id and hash."""
    from promptpotter.application.optimizer_manifest import (
        resolve_optimizer,
        set_optimizer_prompt_overrides,
    )
    from promptpotter.application.optimizers import paper_templates
    from promptpotter.application.runner.inner.spawn import inner_campaign_id
    from promptpotter.application.runner.inner.tasks import InnerTaskSpec

    capo = resolve_optimizer("capo", {})
    cycle = types.SimpleNamespace(optimizer=capo)
    values = {"task_description": "sort the list", "instruction": "Sort it."}
    mutated = {
        "capo_mutate": {"instruction": "Rephrase [{{instruction}}] to {{task_description}}."}
    }
    try:
        set_optimizer_prompt_overrides(None)
        sent, hashes = paper_templates.fill(cycle, "capo_mutate", **values), capo.prompt_hashes()
        set_optimizer_prompt_overrides(mutated)
        assert (
            paper_templates.fill(cycle, "capo_mutate", **values)
            == ("Rephrase [Sort it.] to sort the list.")
            != sent
        ), "the mutated CAPO prompt never reached the request it sends"
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
    """A campaign that PINS its draw and its route must actually run pinned, and two pins must
    not share a banked reply.

    Both halves are silent and both destroy a measurement's identity. `l1_generate` passes
    `temperature=creativity` as a per-call override, so a clamp merged anywhere but last leaves
    the loudest noise source running while every surface reports the campaign as pinned — the
    run is then unreproducible and nothing says so. And hosts of one model disagree
    SYSTEMATICALLY rather than randomly (measured: on a `justlogic-d234` query Groq answered
    FALSE 28/28 where every other host answered Uncertain), so a `route_order` that reached the
    wire without reaching `hash_call` would replay one route's answer under the other's name for
    the life of the cache — unrecoverable, because the row on disk is indistinguishable from one
    the pinned route really produced.
    """
    from promptpotter.application.bench import llm_call as call_mod
    from promptpotter.application.campaign_config import DeterminismClamp
    from promptpotter.application.optimizer_manifest import set_determinism_clamp
    from promptpotter.infrastructure.llm.request import ChatRequest
    from promptpotter.infrastructure.llm.response import LLMResponse
    from promptpotter.infrastructure.store.stores import LLMReuseCache

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

        # Same prompt, same model, a different host: a second measurement, so a second entry.
        await ask(pinned.model_copy(update={"route_order": ["Baidu"]}))
        assert len(sent) == 2, "the second route replayed the first route's banked answer"
        assert len(list((tmp_path / "optimizer_reuse").glob("*.json"))) == 2

        # And an unpinned campaign is left alone rather than handed a `None` for every key.
        await ask(None)
        assert sent[2].temperature == 0.7
        assert sent[2].seed is None
        assert sent[2].route_order is None
    finally:
        set_determinism_clamp(None)


def test_inner_campaign_id_separates_two_candidates_and_is_stable() -> None:
    """A cell's inner campaign is addressed by CONTENT, and two candidates must not collide.

    Silent harm, and the reason this key could not simply be the ``cycle_id``: that id is a
    benchmark-CELL hash, so C0, C1.1 and C1.2 measuring seed-3 all derive the same one
    (``cycle_19ab182342b7`` is shared by four campaigns on disk). The optimizer-prompt
    overrides are the only thing that tells the candidates apart, so a key that dropped them
    would file two candidates' inner runs under one campaign — and because
    ``_open_inner_campaign`` CONTINUES an existing campaign, the second candidate would
    inherit the first's banked rounds and be scored on a trajectory it never ran. No error,
    no missing directory: just one candidate's measurement reported as another's.
    """
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
    # The origin (no overrides) is a third distinct arm, not a nameless default.
    assert (
        len({inner_campaign_id(spec, c1), inner_campaign_id(spec, c2), inner_campaign_id(spec, {})})
        == 3
    )
    # A different cell of the SAME candidate is a different campaign too, and so is one whose
    # inner optimizer runs another knob — an eliminator's as much as an llm node's.
    assert inner_campaign_id(spec.model_copy(update={"seed": 6}), c1) != inner_campaign_id(spec, c1)
    cut = {"pobb": ManifestNodeOverlay.model_validate({"config": {"epsilon": 0.3}})}
    as_run, as_cut = (resolve_optimizer("potter", n).treatment().digest for n in ({}, cut))
    assert inner_campaign_id(
        spec.model_copy(update={"optimizer_treatment": as_run}), c1
    ) != inner_campaign_id(spec.model_copy(update={"optimizer_treatment": as_cut}), c1)


def test_judge_identity_moves_the_searchpoint_hash() -> None:
    """Swapping a judge, its models, its rubric, or the TERM it is read under re-cuts the key.

    An archive row is keyed on node configs. A judge that changed without moving the key would
    have every verdict taken under the OLD grader replayed under the new one — silently, and in
    whichever direction the new grader happens to be more lenient. Re-keying is the same fact one
    level up: the same rubric read under a different term banks a different set of observations,
    so a formula naming the old term would raise on rows that look eligible."""
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
        # Same models, different RUBRIC — the fingerprint hashes the prompt text, so this moves
        # even though nothing an operator wrote in the config differs.
        "simpleqa@a": sp_hash({"answer": spec("simpleqa", "a")}),
        # Same judge, same models, read under a different TERM.
        "rekeyed": sp_hash({"correctness": spec("sealqa", "a")}),
        # A step ADDED. The cell now carries two graded observations, not one.
        "two_steps": sp_hash({"answer": spec("sealqa", "a"), "grounded": spec("simpleqa", "a")}),
    }
    assert len(set(hashes.values())) == len(hashes), f"judge identity collides: {hashes}"
    assert sp_hash(one) == hashes["sealqa@a"], "an unchanged judge must not move the key"
    # Declaration order is the STEP order for a reader, never part of what was measured — two
    # campaigns declaring the same graders in a different order graded the same cells identically.
    assert (
        sp_hash({"grounded": spec("simpleqa", "a"), "answer": spec("sealqa", "a")})
        == hashes["two_steps"]
    ), "declaration order must not re-cut the measurement key"


def test_unframed_ablation_renders_no_framing_where_one_is_committed(built_stores: Any) -> None:
    """``task_framing: off`` is the framing ablation, so its arm scores the prompt WITHOUT the
    dataset's committed framing, and its manifest keeps saying so. A leak renders the framing into
    a run declared unframed: both arms then measure one prompt and the ablation reads as no effect,
    with nothing on any surface to say why. A resume that dropped the declaration runs framed."""
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
    """A tenant copy of the inner benchmark is what the L4 fingerprint hashes AND what an inner
    cell runs. Resolved apart, outer rows are keyed on one config and measured on another, and a
    banked cell replays under a benchmark it never ran — every number plausible."""
    from promptpotter.connectors.promptpotter import _identity_config
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


# 2. The config a campaign runs


def test_the_drafts_CHAIN_reaches_the_mint_on_a_reused_dataset(tmp_path: Path) -> None:
    """The operator picks LLM-only and the campaign measures the full pipeline. A fresh upload
    commits its own `pipeline.yaml`, so `pipelines.default` IS the chosen chain; a REUSED dataset
    writes no file, and nothing else carried `draft.pipeline_steps` to the run — nor the optimizer
    the picker chose, so the shared file's ran under the draft's name."""
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
    # A draft that never touched the toggle leaves the dataset's answer alone — excluding the
    # complement of "nothing chosen" would close the whole pipeline.
    untouched = build_cycle_config(cast(Any, session), root, optimization={}, pipeline_steps=[])
    assert untouched.exclude_nodes == []


def _pipeline_schema(dataset: str) -> PipelineSchema:
    """The committed `datasets/{dataset}/pipeline.yaml`, parsed — a plain dataset's whole graph."""
    path = Path(__file__).resolve().parents[1] / "datasets" / dataset / "pipeline.yaml"
    return parse_pipeline_response(yaml.safe_load(path.read_text(encoding="utf-8")))


def test_answering_in_TEXT_sends_no_contract_to_answer_INTO() -> None:
    """The toggle is spent at the wire seam, and it must take `answer_field` with it: a backend
    destructuring a slot the response never had reads "" for every sample and grades the run
    NO_RESULT — a mechanical zero the loop would attribute to the idea under test.

    And an UNMOVED node must stay byte-identical. The fold runs before the content hash, so
    writing a resolved default here would re-key every banked measurement in the archive to say
    nothing new.
    """
    from promptpotter.application.pipeline_resolve import resolved_output_schemas
    from promptpotter.domain.pipeline_overlay import fold_output_contract
    from promptpotter.domain.pipeline_schema import ANSWER_AS_TEXT, description_key

    schema = _pipeline_schema("justlogic-d234")
    base = {n.name: dict(n.current_config) for n in schema.declared_nodes}
    assert "output_schema" in base["llm_only"] and "answer_field" in base["llm_only"]

    untouched = copy.deepcopy(base)
    fold_output_contract(untouched, schema)
    assert untouched == base, "an unmoved point must hash as it always did"

    chose_text = copy.deepcopy(base)
    chose_text["llm_only"][SCHEMA_TOGGLE_PARAM] = ANSWER_AS_TEXT
    chose_text["llm_only"][description_key("answer")] = "IGNORED"
    fold_output_contract(chose_text, schema)
    assert "output_schema" not in chose_text["llm_only"]
    assert "answer_field" not in chose_text["llm_only"]
    # The description lever reaches nothing under text and must not resolve a registry schema
    # back onto a node that just said it wants none.
    assert description_key("answer") not in chose_text["llm_only"]

    # The served contract follows the same fold, so the panel says "free text" instead of showing
    # a schema the searchpoint is not answering under.
    assert resolved_output_schemas(schema, base)["llm_only"] is not None
    text_point = {**base, "llm_only": {**base["llm_only"], SCHEMA_TOGGLE_PARAM: ANSWER_AS_TEXT}}
    assert resolved_output_schemas(schema, text_point)["llm_only"] is None

    # Under a schema the description lever is its own param, and the fold writes it onto the
    # NESTED field it names — left at the top level it reaches no request, and the round scores
    # an edit the backend never saw.
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
    pp = copy.deepcopy({"llm_only": {**cast(Any, nested.get_node("llm_only")).current_config}})
    pp["llm_only"][amount] = "Net, in CHF."
    fold_output_contract(pp, nested)
    line = pp["llm_only"]["output_schema"]["properties"]["lines"]["items"]["properties"]["amount"]
    assert line["description"] == "Net, in CHF." and amount not in pp["llm_only"]


def _frozen_config(**fields: Any) -> dict[str, Any]:
    """A manifest's `config` as a mint freezes it, over the default connector's loop knobs."""
    from promptpotter.application.campaign_config import load_campaign_config
    from promptpotter.connectors import DEFAULT_CONNECTOR, get

    defaults = dict(get(DEFAULT_CONNECTOR).default_optimization)
    optimization = {**defaults, **fields.pop("optimization", {})}
    return load_campaign_config({"optimization": optimization, **fields}).frozen(arm=False)


def test_a_campaign_runs_the_config_it_froze_whatever_its_dataset_file_says_later(
    built_stores: Any,
) -> None:
    """The dataset file seeds the NEXT campaign. Read at resume, it switched a potter campaign to
    whatever optimizer a later edit named, and a mint's `--config` reached `campaign.json` but not
    the loop — every surface showing one configuration while another ran."""
    from promptpotter.application.pipeline_resolve import resolve_campaign_config
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.run_records import ConfigOverrides, CycleSeed

    stores = built_stores
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
        ),
    )
    stores.campaigns.create_campaign(campaign)
    stores.campaigns.create(campaign.root_hop, {})
    ran = resolve_campaign_config(stores, campaign, campaign.root_hop)
    assert (ran.optimization.optimizer, ran.optimization.max_rounds) == ("potter", 12)

    seed = CycleSeed(
        optimizer_narrowing={"a": {"param_keys": []}},
        config_overrides=ConfigOverrides(max_rounds=4),
    )
    stores.campaigns.write_cycle_seed(campaign.root_hop, seed)
    forked = resolve_campaign_config(stores, campaign, campaign.root_hop)
    assert forked.optimization.max_rounds == 4
    assert [forked.optimizer_narrowing[n].param_keys for n in "ab"] == [[], ["k"]]


def test_backend_row_names_the_endpoint_the_run_actually_reached(built_stores: Any) -> None:
    """Every measurement a run banks is attributed to the row `init_services` resolved, and nothing
    downstream re-reads the URL to check. So the id a caller asks for is a PREFERENCE the endpoint
    outranks in both directions — an endpoint already registered answers under its own id, and an id
    held by a DIFFERENT endpoint never absorbs this one."""
    from promptpotter.application.initialization.wiring import _resolve_backend_id
    from promptpotter.domain.backend import BackendConnection

    built_stores.backends.register(
        BackendConnection(
            id="box", name="n", backend_type="termnorm", base_url="http://10.0.0.5:8000"
        )
    )

    # Same endpoint under another name: one physical endpoint keeps one row.
    resolved = _resolve_backend_id(built_stores, "local", "http://10.0.0.5:8000/", "termnorm", "n")
    assert resolved == "box"
    assert len(built_stores.backends.list_all()) == 1

    # A new endpoint whose requested id is taken gets its own row, naming its own URL — the
    # arm that used to pass an existence check and hand the run someone else's base_url.
    minted = _resolve_backend_id(built_stores, "box", "http://127.0.0.1:8000", "termnorm", "n")
    assert minted != "box"
    row = built_stores.backends.get(minted)
    assert row is not None
    assert row.base_url == "http://127.0.0.1:8000"


# Bare scalars YAML 1.1 resolves to a non-string: the write-side hazard `write_yaml` must quote.
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
    """A config value that survives the write as a *different type* is silent harm.

    YAML 1.1 — which PyYAML implements — resolves bare ``off``/``no``/``TRUE`` to
    booleans and ``0755`` to an int. Two live values sit on that edge: the JustLogic
    label enum is ``["TRUE", "FALSE"]`` and ``promptpotter-self`` sets
    ``prompt_block_catalogue: "off"``. If the emitter ever stopped quoting them, a
    written config would come back with a boolean where a label belongs and the
    pipeline would grade every sample against it — no error, wrong numbers.
    """
    path = tmp_path / "hazards.yaml"
    payload = {k: k for k in _YAML_1_1_HAZARDS} | {"nested": {"labels": list(_YAML_1_1_HAZARDS)}}
    write_yaml(path, payload)
    assert read_yaml(path) == payload


def _outer_schema(root: Path) -> PipelineSchema:
    """`promptpotter-self`'s graph as a run declares it: served off the inner manifest, with the
    schema levers the outer L4 campaign mutates."""
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
    """`node_param_keys` is the single emittable surface — and every reader must read it.

    An invented PARAM is not dropped the way a hallucinated NODE is: absent a membership
    check it merges into `pipeline_params` and rides to the wire. The round completes and
    the candidate's fitness is attributed to an axis that does not exist. Same set, two
    readers: a graft on one side alone is either an unhonoured edit or an unguarded one.
    """
    from promptpotter.application.optimizer_manifest import resolve_optimizer
    from promptpotter.application.optimizers.potter.dispatch.l1_wire_schema import (
        build_l1_response_schema,
    )
    from promptpotter.application.optimizers.potter.validators.l1_strict import validate_overrides

    schema = _outer_schema(tmp_path)
    emitted = build_l1_response_schema(
        schema, citable_fields=(), inner_optimizer=resolve_optimizer("potter", {})
    )["properties"]["variants"]["items"]["properties"]["pipeline_overlay"]["properties"]
    for node, keys in schema.node_param_keys().items():
        assert set(emitted[node]["properties"]) <= keys, (
            f"{node}: the schema declares a key `validate_overrides` rejects as unknown_param"
        )

    # A param no node advertises is rejected, not silently merged.
    reasons = [
        (f.axis, f.reason)
        for f in validate_overrides({"l1_generate": {"invented_knob": 1}}, schema)
    ]
    assert reasons == [("l1_generate.invented_knob", "unknown_param")]
    # A nested param is declared `object`, so a scalar in its slot is caught rather than
    # coerced — depth comes from the declaration, never from sniffing the value.
    assert [f.reason for f in validate_overrides({"l1_critique": {"layout": "hdr"}}, schema)] == [
        "type_mismatch"
    ]
    assert validate_overrides({"l1_critique": {"layout": {"instruction": ["plan"]}}}, schema) == []


# 3. Contamination of a scored prompt


def test_rewriting_the_prompt_panel_cannot_accumulate_the_operator_framing() -> None:
    """The panel IS the text L1 replaces, so whatever it shows comes back as the raw field. Showing
    a SPLICED ``problem_description`` therefore returns the operator's context inside it, and the
    next render splices the context around that copy — a strict accumulator, no error, and every
    candidate after it scored on the grown prompt. Ten banked rounds of one campaign carried the
    same 58-char ``upstream_context`` while ``problem_description`` ran 906 → 5,024 chars."""
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
        memory=L2L3Memory(l1_layout=default_l1_layout()),
        framing=framing,
        pipeline_schema=None,
        cycle_slice=CycleSlice(
            round_num=1,
            l1_stall_depth=0,
            l2_round=0,
            l2_stall_count=0,
            l3_round=0,
            l3_stall_count=0,
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
    # A generator that replaces every field with exactly what it was shown changes nothing.
    rewritten = OptSearchPoint.derive([opt_sp], source="potter:l1_generate", **shown)
    assert rewritten.render_target(framing, demo=()) == opt_sp.render_target(framing, demo=())
    # The framing still reaches the target prompt, and L1 still sees it — as context, not as the
    # field it is being asked to rewrite. Dropping either half trades this bug for a blinder one.
    assert upstream in opt_sp.render_target(framing, demo=())
    assert upstream in "".join(i.text for i in _r_task_context(bundle))


def test_a_controlled_arm_remembers_only_the_runs_its_own_line_filed(built_stores: Any) -> None:
    """An arm of a head-to-head is compared on what its OWN search found: a δ ruler, an axis
    digest or a sample fold drawn from another campaign's runs on the dataset steers it with
    measurements its rival never had, and nothing on screen says so. The archive stays a CACHE
    either way; MEMORY is fenced to the runs the arm filed, and grows as it files more."""
    import contextvars

    from factories import measurements

    from promptpotter.infrastructure.store.archive_queries import (
        list_runs,
        record_measurement_run,
        runs_since,
        sample_fold_rows,
        scope_memory_to_own_runs,
        write_sample_fold,
    )

    def bank(run_id: str) -> None:
        record_measurement_run(
            built_stores,
            run_id,
            {
                "run_id": run_id,
                "dataset_name": "ds",
                "prompt_fields_id": run_id,
                "item_count": 2,
                "content_hash": run_id,
                "created_at": "2026-09-28T00:00:00Z",
            },
            measurements([1.0, 0.0]),
        )

    bank("panel_foreign")
    write_sample_fold(built_stores, dataset_name="ds", rows=[{"run_id": "x"}], append=False)

    def inside_arm() -> tuple[set[str], set[str], list[dict[str, Any]]]:
        scope_memory_to_own_runs({"origin_own"})
        bank("origin_own")
        bank("panel_own")
        write_sample_fold(built_stores, dataset_name="ds", rows=[{"run_id": "y"}], append=True)
        return (
            {e["run_id"] for e in list_runs(built_stores, dataset_name="ds")},
            {run_id for run_id, _ in runs_since(built_stores, set(), dataset_name="ds")},
            sample_fold_rows(built_stores, dataset_name="ds"),
        )

    listed, since, fold = contextvars.copy_context().run(inside_arm)
    assert listed == since == {"origin_own", "panel_own"}
    assert fold == []
    # Outside the arm the tenant's whole archive is memory again, and the fold was not touched.
    assert {e["run_id"] for e in list_runs(built_stores, dataset_name="ds")} == {
        "panel_foreign",
        "origin_own",
        "panel_own",
    }
    assert sample_fold_rows(built_stores, dataset_name="ds") == [{"run_id": "x"}]


def test_no_held_out_row_reaches_a_round_panel_or_an_archive_view() -> None:
    """The headline is read on the bench set, so a bench or demo row the optimizer ever sees makes
    it grade its own exam — and nothing says so: every number renders, only higher.

    Three roads in. The round's panel is drawn from the search pool, so the partition must never
    hand a held-out row to it. The archive is filed by DATASET: once the bench measures its rows they
    sit beside the search's, so an archive view the optimizer reads must drop them — the sample
    index is the one that puts a row's QUERY TEXT into the generator's prompt. And a shot pastes a
    row into the prompt with its answer, so only a demo row may be one — never a bench row, and
    never a search row the panel then scores against its own worked answer.
    """
    from promptpotter.application.intelligence.exploration import (
        Observation,
        select_round_subset,
    )
    from promptpotter.application.intelligence.indexes.sample import SampleIndex
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
    # CAPO's shot operators draw from the demo pool alone, and within `k_max`. The support, never
    # the stream: add, drop and keep each occur, and a crossover samples the union at the mean.
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
    # A row's CONTENT holds it out, not its slot: the same bank in another order agrees.
    assert {s.id for s in partition_bank(bank[::-1], split).bench} == {s.id for s in part.bench}
    # A sample the bank repeats is ONE sample, so no copy of a bench row sits in the search pool.
    twins = [*bank, *(s.model_copy(update={"id": 60 + s.id}) for s in bank)]
    doubled = partition_bank(twins, split)
    assert {s.key for s in doubled.bench}.isdisjoint(s.key for s in doubled.search)
    assert len(doubled.bench) == 2 * split.bench
    # A row a bank DECLARES bench is bench beside the ranked ones and moves no other row: widening
    # a dataset's bench leaves its search and demo pools, so its paid cells, exactly where they were.
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

    # Archive evidence naming every held-out cell cannot pull one into the panel.
    contaminating = [Observation("bench_arm", sid, 0.0) for sid in held]
    panel = select_round_subset(list(part.search), contaminating, 20)
    assert held.isdisjoint(s.id for s in panel)

    index = SampleIndex(sample_ids=part.admitted_ids)
    for n in range(12):
        index.ingest_run(
            {
                "run_id": f"bench_{n}",
                "measurements": [
                    {"sample_id": sid, "query": f"claim {sid}", "fitness": 0.0} for sid in held
                ],
            }
        )
    assert not index.rare_hit_samples() and not index.records(), (
        "a held-out row reached the index the optimizer's panels read"
    )


def test_the_check_in_model_reads_no_held_out_row(
    built_stores: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The origin resolver is an LLM handed sample rows WITH their labels, and it writes the prompt
    every optimizer starts from. A bench row in its preview is the headline's exam read by the one
    authoring the answer sheet: every number renders, only higher, and no rerun unreads it."""
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
    """The generator's instruction says "CURRENT INNER OPTIMIZER PROMPTS below is the text you are
    rewriting… carry every contract forward", so a dropped ``rendered_prompt`` leaves it rewriting
    text it was never shown — and ``guts_inherited_contract`` rejecting it for shortening a field
    it could not see.

    Composes the real floor layout against an L4-shaped schema, so it fails if the mandatory floor
    ever stops being admitted whatever it costs.

    The subject is the manifest the INNER campaign selects, never the outer's own: a CAPO inner is
    shown CAPO's prompts, an edit is checked against CAPO's ports, and the cell renders the edit.
    Read off the outer's own manifest, a CAPO inner is shown nothing and no edit of it is checked.
    """
    from promptpotter.application.optimizer_manifest import (
        resolve_optimizer,
        set_optimizer_prompt_overrides,
    )
    from promptpotter.application.optimizers import paper_templates
    from promptpotter.application.optimizers.potter.dispatch.bundle import (
        OPTIMIZER_DISCRETIONARY_CHARS,
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
    from promptpotter.connectors.promptpotter import promptpotter_wire_adapter
    from promptpotter.domain.opt_search_point import PROMPT_STRING_FIELDS, OptSearchPoint
    from promptpotter.domain.pipeline_schema import PipelineNode
    from promptpotter.domain.results import CandidateProposal
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
        memory=L2L3Memory(l1_layout=default_l1_layout()),
        framing=TaskDecomposition(),
        pipeline_schema=schema,
        cycle_slice=CycleSlice(
            round_num=1,
            l1_stall_depth=0,
            l2_round=0,
            l2_stall_count=0,
            l3_round=0,
            l3_stall_count=0,
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
    allowance = OPTIMIZER_DISCRETIONARY_CHARS["l1_generate"]
    # Non-vacuous: the whole defect is that the node's own subject outweighs the budget the
    # discretionary panels share. If it ever fits, this test proves nothing.
    assert subject_chars > allowance, (
        f"vacuous — the inner optimizer prompts ({subject_chars}c) now fit inside the "
        f"discretionary allowance ({allowance}c), so nothing is being kept against a budget"
    )

    filled = DispatchHub.fill(load_optimizer_prompt("l1_generate"), bundle, node="l1_generate")
    assert "CURRENT INNER OPTIMIZER PROMPTS" in filled.template.render(), (
        "the generator was handed no subject — it is rewriting text it cannot see"
    )
    assert len(filled.rendered["rendered_prompt"]) >= subject_chars
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
    proposals = [
        CandidateProposal(
            opt_sp=OptSearchPoint.derive([parent], source="potter:l1_generate"),
            pipeline_overlay=overlay,
        )
        for overlay in (edit, severed)
    ]
    _, merged = parse_population(
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

    payload = promptpotter_wire_adapter("justlogic-d234/seed-0", merged[0])
    try:
        set_optimizer_prompt_overrides(payload["optimizer_prompt_overrides"])
        sent = paper_templates.fill(
            types.SimpleNamespace(optimizer=capo),
            "capo_mutate",
            task_description="sort the list",
            instruction="Sort it.",
        )
    finally:
        set_optimizer_prompt_overrides(None)
    assert sent.endswith("Keep the rewrite under 80 words."), "the inner cell ran the parent"
