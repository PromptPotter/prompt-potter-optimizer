"""A wrong identity, or a quiet cross-contamination.

Owns the hashes a measurement is filed under (`sp_hash`, `prompt_hashes`, `hash_call`,
`inner_campaign_id`), `application/pipeline_resolve.py`, `domain/pipeline_overlay.py`, the held-out
partition and what reaches a scored prompt. Two things that differ answer as one, or one thing is
scored on text it was never meant to carry.
"""

from __future__ import annotations

import asyncio
import copy
import io
import json
import random
import re
import types
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import openpyxl
import pytest
from factories import inner_cells, pipeline_schema
from pydantic import ValidationError

from promptpotter.application.bench import llm_call as call_mod
from promptpotter.application.bench.children import admission_failures, overlay_failures
from promptpotter.application.bench.node_context import NodeContext
from promptpotter.application.bench.task_context import campaign_framing
from promptpotter.application.campaign_config import (
    CampaignConfig,
    DeterminismClamp,
    OptimizationConfig,
)
from promptpotter.application.commands.launching import _parse_cycle_seed
from promptpotter.application.commands.payloads import MintCampaignPayload, StartCheckinPayload
from promptpotter.application.datasets import ingest
from promptpotter.application.datasets.draft_campaign import (
    EditDraftPatch,
    load_checkin_draft,
    new_draft,
)
from promptpotter.application.datasets.draft_patch import apply_draft_patch, plan_draft_patch
from promptpotter.application.datasets.loaders import samples_from_dicts
from promptpotter.application.datasets.origin_resolve import build_origin_consultation
from promptpotter.application.datasets.tabular_ingest import IngestError, read_tabular
from promptpotter.application.intelligence.indexes.sample import SampleFoldRow, SampleIndex
from promptpotter.application.intelligence.rasch import Observation, select_round_subset
from promptpotter.application.jobs.launcher.checkin import create_checkin_campaign
from promptpotter.application.optimizer_manifest import (
    resolve_optimizer,
    set_determinism_clamp,
    set_optimizer_prompt_overrides,
)
from promptpotter.application.optimizers.capo.members import cross_shots, mutate_shots
from promptpotter.application.optimizers.potter.dispatch.bundle import (
    CycleSlice,
    InjectionBundle,
    RoundDigest,
)
from promptpotter.application.optimizers.potter.dispatch.facade import DispatchHub
from promptpotter.application.optimizers.potter.dispatch.injections.layer_state import (
    _r_rendered_prompt,
    _r_task_context,
)
from promptpotter.application.optimizers.potter.dispatch.layout import NODE_LAYOUTS
from promptpotter.application.optimizers.potter.dispatch.prompts import (
    base_optimizer_template,
    load_optimizer_prompt,
)
from promptpotter.application.optimizers.potter.l1.population import parse_population
from promptpotter.application.optimizers.potter.records import L2L3Memory, Ladder
from promptpotter.application.optimizers.potter.validators.l1_strict import L1_SHOTS_IN_DEMO_POOL
from promptpotter.application.pipeline_resolve import (
    checkin_campaign_config,
    dataset_pipeline_declaration,
    experiment_outside_run,
    resolve_campaign_config,
    resolve_pipeline_config_params,
    resolved_output_schemas,
)
from promptpotter.application.runner.inner.connector import (
    _extract_experiment,
    _identity_config,
    _resolve_panel,
    promptpotter_wire_adapter,
)
from promptpotter.application.runner.inner.spawn import inner_campaign_id
from promptpotter.application.runner.inner.tasks import InnerTaskSpec
from promptpotter.config.paths import install_datasets_root
from promptpotter.config.settings import settings
from promptpotter.connectors import DEFAULT_CONNECTOR, get
from promptpotter.connectors import harbor as harbor_connector
from promptpotter.connectors.protocol import PROBE_WORKLOAD
from promptpotter.domain.bench import DatasetSplit, partition_bank, restored_partition
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.opt_search_point import (
    PROMPT_STRING_FIELDS,
    TEMPLATE_TOKEN_RE,
    OptSearchPoint,
    Variation,
)
from promptpotter.domain.pipeline_overlay import fold_output_contract
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import (
    ANSWER_AS_TEXT,
    SCHEMA_TOGGLE_PARAM,
    ManifestNodeOverlay,
    NodeConfigParam,
    NodeSearchNarrowing,
    ParamIntent,
    PipelineNode,
    PipelineSchema,
    description_key,
    narrowing_of,
)
from promptpotter.domain.round_diagnostics import RoundDiagnostics
from promptpotter.domain.run_records import ConfigOverrides, CycleSeed, ScoringLockedRecord
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.request import ChatRequest
from promptpotter.infrastructure.llm.response import LLMResponse
from promptpotter.infrastructure.store.dataset_access import (
    DEFINITION_FILE,
    ResolvedDataset,
    read_dataset_pipeline,
    resolve_dataset,
)
from promptpotter.infrastructure.store.dataset_replace import replace_dataset
from promptpotter.infrastructure.store.io import read_yaml, write_yaml
from promptpotter.infrastructure.store.llm_reuse_cache import LLMReuseCache
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.judges.protocol import JudgeSpec, JudgeStage
from promptpotter.shared.errors import PayloadInvalidError
from promptpotter.shared.hashing import source_facts, stable_hash
from promptpotter.shared.identity import default_identity

_L1 = Variation(node="potter:l1_generate", mode="llm")
_DATASETS = install_datasets_root()


def _install(name: str) -> ResolvedDataset:
    return ResolvedDataset(name, _DATASETS / name, "install")


_SPEC = InnerTaskSpec(
    inner_dataset="justlogic/d234",
    optimizer_treatment="o",
    seed=3,
    n_samples=28,
    n_samples_bench=8,
    n_rounds=4,
)


def _frozen_config(**fields: Any) -> dict[str, Any]:
    defaults = dict(get(DEFAULT_CONNECTOR).default_optimization)
    optimization = {**defaults, **fields.pop("optimization", {})}
    return CampaignConfig.model_validate({"optimization": optimization, **fields}).frozen(arm=False)


# 1. Hashes — what a measurement is filed under


def test_a_measurement_is_filed_under_every_input_that_moves_its_number_and_no_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, built_stores: Stores
) -> None:
    # A value the canonical form cannot spell is refused, never stringified into a stored key.
    with pytest.raises(TypeError):
        stable_hash({"when": Path(".")})

    # Prose moves no source digest, and a code token does.
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
    for old, new in (("n > LIMIT", "n >= LIMIT"), ("LIMIT = 3", "LIMIT = 4")):
        assert digest(bare.replace(old, new)) != digest(bare), f"`{new}` moved no treatment"
    only_prose = marked + 'shapes_optimizer_prompt(None)\ndef f():\n    """Doc."""\n'
    assert digest(only_prose) == digest(only_prose.replace('"""Doc."""', "pass")), (
        "a body of nothing but a docstring keyed apart from an empty one"
    )

    # The judge a cell is graded by moves its searchpoint hash.
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

    def judge(name: str, model: str) -> JudgeSpec:
        return JudgeSpec(name=name, stages=[JudgeStage(model=model, provider="p")])

    hashes = {
        "none": sp_hash({}),
        "sealqa@a": sp_hash({"answer": judge("sealqa", "a")}),
        "sealqa@b": sp_hash({"answer": judge("sealqa", "b")}),
        # Same models, different rubric: the fingerprint hashes the judge's prompt text.
        "simpleqa@a": sp_hash({"answer": judge("simpleqa", "a")}),
        "rekeyed": sp_hash({"correctness": judge("sealqa", "a")}),
        "two_steps": sp_hash({"answer": judge("sealqa", "a"), "grounded": judge("simpleqa", "a")}),
    }
    assert len(set(hashes.values())) == len(hashes), f"judge identity collides: {hashes}"
    assert sp_hash({"answer": judge("sealqa", "a")}) == hashes["sealqa@a"], (
        "an unchanged judge must not move the key"
    )
    assert (
        sp_hash({"grounded": judge("simpleqa", "a"), "answer": judge("sealqa", "a")})
        == hashes["two_steps"]
    ), "declaration order must not re-cut the measurement key"

    # An L4 override moves the prompt and the hash of every preset, and the inner campaign it runs.
    capo = resolve_optimizer("capo", {})
    round_ = types.SimpleNamespace(cycle=types.SimpleNamespace(optimizer=capo))
    ctx: NodeContext[Any] = NodeContext(cast(Any, round_), "capo_mutate")
    values = {"task_description": "sort the list", "instruction": "Sort it."}
    mutated = {
        "capo_mutate": {"instruction": "Rephrase [{{instruction}}] to {{task_description}}."}
    }
    potter = resolve_optimizer("potter", {})
    try:
        set_optimizer_prompt_overrides(None)
        sent, capo_hashes, treated = (
            ctx.fill(**values),
            capo.prompt_hashes(),
            capo.treatment().digest,
        )
        baseline = potter.prompt_hashes()
        assert potter.prompt_hashes() == baseline, "an unmoved preset re-hashed"
        set_optimizer_prompt_overrides(mutated)
        assert ctx.fill(**values) == "Rephrase [Sort it.] to sort the list." != sent, (
            "the mutated CAPO prompt never reached the request it sends"
        )
        assert capo.treatment().digest != treated, (
            "two inner cells under two arms stamp ONE treatment — a bank reads them as one arm"
        )
        # A valid layout edit on one node: `diagnostics` stays placed.
        layout = {"l1_critique": {"layout": {"thinking_style": ["axis_memory"]}}}
        for optimizer, before, override, node in (
            (capo, capo_hashes, mutated, "capo_mutate"),
            (potter, baseline, layout, "l1_critique"),
        ):
            set_optimizer_prompt_overrides(override)
            moved = optimizer.prompt_hashes()
            assert moved[node] != before[node], (
                f"{node}: an override left the node hash unchanged — audits would pool the cycles"
            )
            assert {k: v for k, v in moved.items() if k != node} == {
                k: v for k, v in before.items() if k != node
            }, f"{node}: an edit on one node moved other nodes' hashes"
    finally:
        set_optimizer_prompt_overrides(None)

    # The inner campaign id separates two candidates, two seeds and two treatments.
    c1 = {"l1_generate": {"instruction": "widen the axes"}}
    c2 = {"l1_generate": {"instruction": "narrow the axes"}}
    ids = {inner_campaign_id(_SPEC, o) for o in (c1, c2, {}, mutated)}
    assert len(ids) == 4, (
        "two candidates differing only in one override field share a campaign — "
        "their measurements merge under one id with no error"
    )
    assert inner_campaign_id(_SPEC.model_copy(update={"seed": 6}), c1) != inner_campaign_id(
        _SPEC, c1
    ), "two seeds share one inner campaign"
    cut = {"pobb": ManifestNodeOverlay.model_validate({"config": {"epsilon": 0.3}})}
    as_run, as_cut = (resolve_optimizer("potter", n).treatment().digest for n in ({}, cut))
    assert inner_campaign_id(
        _SPEC.model_copy(update={"optimizer_treatment": as_run}), c1
    ) != inner_campaign_id(_SPEC.model_copy(update={"optimizer_treatment": as_cut}), c1), (
        "a moved optimizer knob ran under the inner campaign of the unmoved one"
    )

    # The determinism clamp outranks every other layer and keys the bank.
    asked: list[ChatRequest] = []

    class _Recorder:
        async def chat(self, request: ChatRequest, **_: Any) -> LLMResponse:
            asked.append(request)
            return LLMResponse(content="ok", model="m")

    monkeypatch.setattr(call_mod, "get_llm_client", lambda _provider: _Recorder())
    reuse = LLMReuseCache(tmp_path / "reuse", "optimizer_reuse")

    async def ask(clamp: DeterminismClamp | None) -> None:
        set_determinism_clamp(clamp)
        await call_mod.llm_call(
            [{"role": "user", "content": "q"}],
            config={"provider": "openrouter", "model": "m", "temperature": 0.9},
            context=call_mod.LLMCallContext(cache=reuse),
            temperature=0.7,
        )

    pinned = DeterminismClamp(temperature=0.0, seed=7, route_order=["Alibaba"])
    try:
        for clamp, (temperature, seed, route) in (
            (pinned, (0.0, 7, ["Alibaba"])),
            (pinned.model_copy(update={"route_order": ["Baidu"]}), (0.0, 7, ["Baidu"])),
            (None, (0.7, None, None)),
        ):
            asyncio.run(ask(clamp))
            assert (asked[-1].temperature, asked[-1].seed, asked[-1].route_order) == (
                temperature,
                seed,
                route,
            ), (
                f"{clamp}: the node's file value or the per-call override beat the clamp — the "
                "campaign reports itself pinned and runs unpinned"
            )
    finally:
        set_determinism_clamp(None)
    assert len(list((tmp_path / "reuse" / "optimizer_reuse").glob("*.json"))) == 3, (
        "the second route replayed the first route's banked answer"
    )

    # The unframed ablation renders no framing where one is committed, resumed or not.
    write_yaml(built_stores.install_datasets_root / "gsm8k" / DEFINITION_FILE, {"pipeline": {}})
    built_stores.tenant_datasets.save_task_context(
        "gsm8k", TaskDecomposition(domain="grade-school arithmetic")
    )
    gsm8k = resolve_dataset(built_stores, "gsm8k")
    framed = CampaignConfig(optimization=OptimizationConfig(degradation_threshold=0.05))
    unframed = framed.model_copy(update={"task_framing": "off"})
    assert campaign_framing(built_stores, framed, gsm8k).domain == "grade-school arithmetic", (
        "the framed arm lost the framing committed for it"
    )
    assert not campaign_framing(built_stores, unframed, gsm8k), (
        "the ablation arm rendered the committed framing it declared off"
    )
    frozen = CampaignConfig.model_validate(unframed.frozen(arm=False))
    assert not campaign_framing(built_stores, frozen, gsm8k), (
        "the campaign manifest lost the declaration, so a resume of the ablation runs framed"
    )

    # A cell is keyed by its own instrument and no other cell's: adding an inner dataset re-keys no
    # banked cell, and an edit to a seat's dataset re-keys that seat alone, off the tier it runs.
    outer = _install("promptpotter-self/panel")
    panel: dict[str, Any] = {
        "inner_benchmark": "innerbench",
        "inner_benchmark_config": {
            "n_samples_per_inner_round": 4,
            "n_samples_bench": 2,
            "max_inner_rounds": 2,
        },
        "tasks": [{"kind": "seat", "id": "seed-0"}],
    }
    campaign = {"optimization": {"max_rounds": 2, "degradation_threshold": 0.4}}
    for name in ("innerbench", "otherbench"):
        write_yaml(
            built_stores.install_datasets_root / name / DEFINITION_FILE,
            {"pipeline": {"nodes": {"llm_only": {"config": {"model": "a"}}}}, "campaign": campaign},
        )

    def keyed(declared: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
        resolved = _resolve_panel(built_stores, declared)
        rows = samples_from_dicts(_extract_experiment(resolved))
        return {s.query: s.key for s in rows}, _identity_config(built_stores, outer, resolved)

    before, fingerprint = keyed(panel)
    other = {"kind": "seat", "id": "other", "inner_dataset": "otherbench"}
    grown = {**panel, "tasks": [*panel["tasks"], other]}
    after, grown_fingerprint = keyed(grown)
    assert after["seed-0"] == before["seed-0"] and grown_fingerprint == fingerprint, (
        "adding an inner dataset re-keyed a banked cell"
    )
    tenant_copy = built_stores.tenant_datasets.dataset_dir("innerbench")
    write_yaml(
        tenant_copy / DEFINITION_FILE,
        {"pipeline": {"nodes": {"llm_only": {"config": {"model": "b"}}}}, "campaign": campaign},
    )
    edited, _ = keyed(grown)
    assert edited["seed-0"] != before["seed-0"], "identity hashed the wrong tier"
    assert edited["other"] == after["other"], "one seat's edit re-keyed another seat"
    sandbox = build_stores(
        built_stores.identity,
        projects_root=built_stores.base_dir / ".inner" / "cell",
        install_datasets_root=built_stores.install_datasets_root,
        shared_root=built_stores.shared_root,
    )
    assert resolve_dataset(sandbox, "innerbench").directory == tenant_copy, (
        "the cell ran another tier"
    )

    # A Harbor panel reads its pinned roster and never dials out; upstream repinning re-cuts nothing.
    def no_fetch(*_a: object, **_k: object) -> None:
        raise AssertionError("a panel read fetched Harbor's registry")

    monkeypatch.setattr(harbor_connector.httpx, "get", no_fetch)
    for harbor_dataset, version, include in (
        ("spreadsheetbench-verified", "1.0", ["10452", "105-24"]),
        ("terminal-bench-sample", "2.0", ["regex-log"]),
    ):
        write_yaml(
            built_stores.tenant_datasets.dataset_dir(harbor_dataset) / DEFINITION_FILE,
            {
                "pipeline": {
                    "backend_type": "harbor",
                    "nodes": {"agent": {"type": "agent", "config": {"model": "m"}}},
                },
                "panel": {
                    "harbor_dataset": harbor_dataset,
                    "harbor_dataset_version": version,
                    "tasks_include": include,
                    "agent": {"name": "terminus-2", "environment": "docker"},
                },
            },
        )
        resolved = experiment_outside_run(
            built_stores, resolve_dataset(built_stores, harbor_dataset)
        )
        assert resolved is not None and [t["id"] for t in resolved["tasks"]] == include, (
            f"{harbor_dataset} resolved another panel than its include list"
        )
    with pytest.raises(ValueError):
        harbor_connector._resolve_experiment(
            built_stores, {"harbor_dataset": "unpinned", "harbor_dataset_version": "9"}
        )
    roster = [{"id": "a", "git_url": "u", "git_commit_id": "c1", "path": "a"}]
    published = [roster]
    monkeypatch.setattr(harbor_connector, "_ROSTER_DIR", tmp_path)
    monkeypatch.setattr(harbor_connector, "_registry_roster", lambda *_: published[0])
    pin = harbor_connector.pin_roster("bench", "1.0")
    assert harbor_connector.pin_roster("bench", "1.0") == pin, "an unchanged registry re-pinned"
    published[0] = [{**roster[0], "git_commit_id": "c2"}]
    with pytest.raises(ValueError):
        harbor_connector.pin_roster("bench", "1.0")
    assert harbor_connector._pinned_roster("bench", "1.0") == roster, (
        "a repinned registry moved the panel under the name it had"
    )


# 2. The config a campaign runs


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


def test_a_campaign_runs_the_config_its_check_in_froze_and_its_forks_narrowed(
    tmp_path: Path, built_stores: Stores
) -> None:
    # The draft's CHAIN reaches the mint on a reused dataset.
    pinned = {"per_sample": "label_match(predicted, ground_truth)", "per_cell": "correct"}
    root = built_stores.tenant_datasets.dataset_dir("ds")
    optimization = dict(get(DEFAULT_CONNECTOR).default_optimization)
    schema = parse_pipeline_response(
        {
            "nodes": {n: {"type": "llm"} for n in ("llm_only", "web_search", "rerank")},
            "pipelines": {"default": ["web_search", "rerank", "llm_only"]},
        }
    )
    write_yaml(
        root / DEFINITION_FILE,
        {
            "pipeline": {"pipelines": dict(schema.pipelines)},
            "campaign": {"optimization": optimization, "scoring": pinned},
        },
    )
    draft = new_draft(
        tenant_id=built_stores.identity.tenant_id,
        slug="ds",
        n_samples=1,
        sample_preview=[],
        headers=[],
        source_file="dataset:ds",
    ).patch(optimization_overrides={"optimizer": "capo"})
    live = [n.name for n in schema.nodes]
    picked = draft.patch(pipeline_steps=["llm_only"])
    chosen = checkin_campaign_config(built_stores, picked, session_chain=live)
    assert sorted(chosen.exclude_nodes) == ["rerank", "web_search"], "a picked chain ran whole"
    assert schema.active_steps_excluding(chosen.exclude_nodes) == ["llm_only"], (
        "the excluded complement runs another chain than was picked"
    )
    assert chosen.optimization.optimizer == "capo", "the draft's optimizer did not reach the mint"
    assert checkin_campaign_config(built_stores, picked) == chosen, (
        "what a read serves before Start is not what Start freezes"
    )
    assert chosen.scoring == pinned, "the pinned criterion did not reach the mint"
    dialled = checkin_campaign_config(built_stores, picked.patch(scoring_dials="cost=0.1"))
    assert dialled.scoring == {"per_sample": pinned["per_sample"], "dials": "cost=0.1"}, (
        "dials froze another criterion than they spell"
    )
    untouched = checkin_campaign_config(built_stores, draft, session_chain=live)
    assert untouched.exclude_nodes == [], (
        "`pipeline_steps=[]` is untouched: excluding its complement would close the whole pipeline"
    )
    campaign_id, _, _ = create_checkin_campaign(built_stores, draft=picked, bank_items=[])
    replace_dataset(stores=built_stores, slug="ds")
    write_yaml(
        root / DEFINITION_FILE,
        {
            "pipeline": {"pipelines": dict(schema.pipelines)},
            "campaign": {"optimization": optimization},
        },
    )
    carried = load_checkin_draft(built_stores, campaign_id)
    assert (
        carried is not None and checkin_campaign_config(built_stores, carried).scoring == pinned
    ), "a replace frees the slug for other rows: the check-in follows the ones it was cut from"

    # A draft edit sent as rows freezes the search space the overlay spelled. A REUSED dataset:
    # its file is not the check-in's to write, so the overlay rides the config.
    reused = built_stores.tenant_datasets.dataset_dir("d")
    write_yaml(
        reused / DEFINITION_FILE,
        {"pipeline": {"backend_type": "termnorm"}, "campaign": _frozen_config()},
    )
    declared_node = {
        "type": "llm",
        "config": {"model": "m", "reasoning_effort": "low", "temperature": 0.2},
        "optimizer": {
            "param_keys": ["reasoning_effort", "temperature"],
            "param_allowed_values": {"reasoning_effort": ["low", "mid", "high"]},
        },
    }
    edited = new_draft(
        tenant_id=built_stores.identity.tenant_id,
        slug="d",
        n_samples=1,
        sample_preview=[],
        headers=[],
        source_file="dataset:d",
    ).patch(backend_nodes={"llm_only": declared_node})

    def frozen(**patch: Any) -> Any:
        plan = plan_draft_patch(built_stores, edited, EditDraftPatch.model_validate(patch))
        return checkin_campaign_config(built_stores, apply_draft_patch(edited, plan))

    rows = [
        {"key": "reasoning_effort", "open": True, "allowed": ["low", "high"]},
        {"key": "temperature", "open": False, "allowed": []},
    ]
    spelled = {
        "param_keys": ["reasoning_effort"],
        "param_allowed_values": {"reasoning_effort": ["low", "high"]},
    }
    as_rows = frozen(node_narrowing={"llm_only": rows}).optimizer_narrowing
    assert as_rows == {"llm_only": NodeSearchNarrowing(**spelled)}, "rows froze another space"
    as_overlay = frozen(pipeline_overlay={"llm_only": {"optimizer": spelled}})
    assert as_rows == as_overlay.optimizer_narrowing, "rows and the overlay froze two spaces"
    # No field named `answer`, none named by the operator: the slot is the LAST, reasoning first.
    answer_schema = {"type": "object", "properties": {"reasoning": {}, "code": {}}}
    authored = frozen(
        node_output={"node": "llm_only", "output_schema": answer_schema}
    ).pipeline_overlay
    contract = {"output_schema": answer_schema, "answer_field": "code", "response_format": "json"}
    assert (
        authored == frozen(pipeline_overlay={"llm_only": {"config": contract}}).pipeline_overlay
    ), "an authored output froze another contract than the overlay spells"
    assert authored["llm_only"]["answer_field"] == "code", "the answer slot is not the last field"
    # A check-in names only a connector that runs its upload; a container one is never chosen there.
    for foreign in ("dbllmbench", "harbor", "dspy", "no-such"):
        with pytest.raises(PayloadInvalidError):
            frozen(connector=foreign)

    # A row reads as the search space its ticks and padlock drew.
    params = [
        NodeConfigParam(
            key="model", kind="model", options=["a", "b"], permitted=["a"], permitted_stated=True
        ),
        NodeConfigParam(key="effort", kind="enum", options=["low", "mid", "high"]),
        NodeConfigParam(key="temperature", kind="number"),
    ]
    for key, ticks, unlocked, drawn in (
        # Every tick on declares nothing: an absent entry IS the menu.
        ("effort", ["low", "mid", "high"], True, (True, None)),
        ("effort", ["low", "high"], True, (True, ["low", "high"])),
        # One tick is a pin: shut, yet still stated, because a human fork may steer to it.
        ("model", ["a"], True, (False, ["a"])),
        ("effort", ["low", "max"], True, (True, ["low", "max"])),
        # Nothing ticked is an axis with nothing legal, never one with no bound.
        ("effort", [], True, (False, None)),
        ("temperature", [], True, (True, None)),
        ("temperature", ["0.2"], False, (False, None)),
        # A full menu over a server-stated `permitted` is stated: it replaces the node's.
        ("model", ["a", "b"], True, (True, ["a", "b"])),
    ):
        out = narrowing_of(params, [ParamIntent(key=key, open=unlocked, allowed=ticks)])
        assert (key in (out.param_keys or []), out.param_allowed_values.get(key)) == drawn, (
            f"{key} {ticks} {'open' if unlocked else 'locked'}: a row drew another search space"
        )

    # A campaign runs the config it froze, whatever its dataset file says later.
    stores = built_stores
    dials = {"per_sample": "exact", "dials": "cost: 0.1"}
    write_yaml(
        stores.tenant_datasets.dataset_dir("fz") / DEFINITION_FILE,
        {"campaign": _frozen_config(optimization={"optimizer": "capo", "max_rounds": 3})},
    )
    campaign = Campaign(
        campaign_id="fz__000001",
        dataset_name="fz",
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
    assert (ran.optimization.optimizer, ran.optimization.max_rounds) == ("potter", 12), (
        "a campaign ran its dataset file's later config instead of the one it froze"
    )
    ledger = CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(campaign.root_hop)))
    ledger.append(ScoringLockedRecord(declared={"dials": "other"}, locked={"per_cell": "0"}))
    assert resolve_campaign_config(stores, campaign, campaign.root_hop).scoring == dials, (
        "a lock declared off other dials governed this campaign"
    )
    locked = {"per_sample": "exact", "per_cell": "correct - 0.1 * cost"}
    ledger.append(ScoringLockedRecord(declared=dials, locked=locked))
    for hop in (None, campaign.root_hop):
        assert resolve_campaign_config(stores, campaign, hop).scoring == locked, (
            f"{hop}: the criterion its origin locked rides the root's ledger, and every cycle "
            "runs under it"
        )
    seed = CycleSeed(
        optimizer_narrowing={"a": {"param_keys": []}},
        config_overrides=ConfigOverrides(max_rounds=4),
    )
    stores.campaigns.write_cycle_seed(campaign.root_hop, seed)
    forked = resolve_campaign_config(stores, campaign, campaign.root_hop)
    assert forked.optimization.max_rounds == 4, "a seed's override did not reach its cycle"
    assert [forked.optimizer_narrowing[n].param_keys for n in "ab"] == [[], ["k"]], (
        "a seed's narrowing replaced the nodes it did not name"
    )

    # A fork closes the axis its operator closed on the forked cycle's own menu: ticks equal to
    # the ROOT's whole menu still close the rung only the fork's menu carries.
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

    steered = Campaign(
        campaign_id="fk__000001",
        dataset_name="fk",
        created_at="2026-09-27T00:00:00Z",
        root_cycle_id="cycle_root",
        owner_user_id=str(stores.identity.user_id),
        config=_frozen_config(),
    )
    stores.campaigns.create_campaign(steered)
    fork = CycleHop(campaign_id=steered.campaign_id, cycle_id="cycle_fork")
    for hop, rungs in ((steered.root_hop, ["low", "high"]), (fork, ["low", "high", "xhigh"])):
        stores.campaigns.mint_cycle(hop)
        stores.campaigns.write_resolved_pipeline(hop, declaring(rungs))
    fork_seed = _parse_cycle_seed(
        {"node_narrowing": {"a": rows}},
        resolve_campaign_config(stores, steered, None),
        stores,
        steered,
        fork,
    )
    assert fork_seed.optimizer_narrowing == {
        "a": NodeSearchNarrowing(
            param_keys=["reasoning_effort"],
            param_allowed_values={"reasoning_effort": ["low", "high"]},
        )
    }, "the fork left open a rung its operator closed"

    # Answering in TEXT sends no contract to answer INTO.
    text_schema = parse_pipeline_response(read_dataset_pipeline(_DATASETS / "justlogic" / "d234"))
    base = {n.name: dict(n.current_config) for n in text_schema.declared_nodes}
    assert {"output_schema", "answer_field"} <= base["llm_only"].keys(), (
        "the instrument declares no contract — the rest proves nothing"
    )
    assert fold_output_contract(base, text_schema) == base, (
        "an unmoved point must hash as it always did"
    )
    chose_text = copy.deepcopy(base)
    chose_text["llm_only"][SCHEMA_TOGGLE_PARAM] = ANSWER_AS_TEXT
    chose_text["llm_only"][description_key("answer")] = "IGNORED"
    folded = fold_output_contract(chose_text, text_schema)["llm_only"]
    assert not {"output_schema", "answer_field", description_key("answer")} & folded.keys(), (
        "a point answering in text still carries the contract it left"
    )
    assert resolved_output_schemas(text_schema, base)["llm_only"] is not None, (
        "a point answering in the contract was sent none"
    )
    text_point = {**base, "llm_only": {**base["llm_only"], SCHEMA_TOGGLE_PARAM: ANSWER_AS_TEXT}}
    assert resolved_output_schemas(text_schema, text_point)["llm_only"] is None, (
        "a point answering in TEXT was sent a contract to answer INTO"
    )
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
    assert line["description"] == "Net, in CHF." and amount not in pp["llm_only"], (
        "a nested field's description did not fold into the contract it names"
    )
    assert "description" not in items["properties"]["amount"], (
        "the fold mutated the declared schema it read: the parent individual still holds it"
    )

    # An invented param is rejected, and so is the individual a variation wrote it on.
    outer = _install("promptpotter-self/panel")
    outer_stores = build_stores(default_identity(), projects_root=tmp_path / "outer")
    declaration = dataset_pipeline_declaration(
        outer_stores, outer, experiment_outside_run(outer_stores, outer)
    )
    assert declaration is not None, "the outer dataset declared no pipeline"
    outer_schema = parse_pipeline_response(declaration)
    for overlay, reasons in (
        ({"l1_generate": {"invented_knob": 1}}, [("l1_generate.invented_knob", "unknown_param")]),
        ({"l1_critique": {"layout": "hdr"}}, [("l1_critique.layout", "type_mismatch")]),
        ({"l1_critique": {"layout": {"instruction": ["plan"]}}}, []),
    ):
        found = [(f.axis, f.reason) for f in overlay_failures(overlay, outer_schema)]
        assert found == reasons, f"{overlay}: admitted a param the schema does not declare"
    parent = OptSearchPoint(persona="You grade.", instruction="Answer {{combined_text}}.")
    wiped = OptSearchPoint.derive([parent], variation=_L1, persona="")
    assert wiped.lineage.variations[0].loci == ["persona"], (
        "a deletion is a move like any other: an emptied field is a locus the variation wrote"
    )
    assert wiped.lineage.parent_ids == [parent.id], "a wiped field lost its parent"
    found = admission_failures(wiped, {"l1_generate": {"invented_knob": 1}}, outer_schema)
    assert [f.reason for f in found] == ["unknown_param"], "an invented knob was admitted"

    # The endpoint is the operator's config: a request that names one is refused, not followed.
    termnorm = BackendClient(get("termnorm"), workload=PROBE_WORKLOAD)
    assert termnorm.base_url == settings.TERMNORM_URL.rstrip("/"), "the client left its config"
    for wire in (StartCheckinPayload, MintCampaignPayload):
        with pytest.raises(ValidationError):
            wire.model_validate(
                {"campaign_id": "c" * 8, "dataset_name": "d", "backend_url": "http://evil"}
            )

    # The YAML a config is written to never reinterprets a string it wrote.
    path = tmp_path / "hazards.yaml"
    payload = {k: k for k in _YAML_1_1_HAZARDS} | {"nested": {"labels": list(_YAML_1_1_HAZARDS)}}
    write_yaml(path, payload)
    assert read_yaml(path) == payload, "a YAML 1.1 scalar read back as another type than written"


# 3. Contamination of a scored prompt


_EXPORTED_ROWS = [
    {"query": "Café au lait?", "label": "Getränk"},
    {"query": "Smørrebrød", "label": "Essen"},
]


def test_no_held_out_row_or_foreign_text_reaches_a_scored_prompt(
    monkeypatch: pytest.MonkeyPatch, built_stores: Stores
) -> None:
    # A mis-decoded label grades every cell against text the person never wrote, with no error.
    for filename, blob in (
        (
            "excel.csv",
            "query;label\r\nCafé au lait?;Getränk\r\nSmørrebrød;Essen\r\n".encode("cp1252"),
        ),
        (
            "unicode.txt",
            "query\tlabel\nCafé au lait?\tGetränk\nSmørrebrød\tEssen\n".encode("utf-16"),
        ),
        (
            "bare.txt",
            "query\tlabel\nCafé au lait?\tGetränk\nSmørrebrød\tEssen\n".encode("utf-16-le"),
        ),
        ("bom.csv", "query,label\nCafé au lait?,Getränk\nSmørrebrød,Essen\n".encode("utf-8-sig")),
        (
            "lines.json",
            "\n".join(json.dumps(r, ensure_ascii=False) for r in _EXPORTED_ROWS).encode(),
        ),
    ):
        table = read_tabular(blob, filename)
        assert (table.headers, list(table.rows)) == (("query", "label"), _EXPORTED_ROWS), (
            f"{filename}: an exported table read to labels it does not hold"
        )

    # An OpenDocument repeat count is the file's claim: blanks keep their columns, tails stay
    # unexpanded, and a valued repeat stops at the cap instead of allocating what it declares.
    def ods(*rows: str) -> bytes:
        ns = " ".join(
            f'xmlns:{p}="urn:oasis:names:tc:opendocument:xmlns:{p}:1.0"'
            for p in ("office", "table", "text")
        )
        body = f"<table:table>{''.join(rows)}</table:table>"
        content = f"<office:document-content {ns}>{body}</office:document-content>"
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as zf:
            zf.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
            zf.writestr("content.xml", content)
        return out.getvalue()

    def row(*cells: str, repeat: int = 1) -> str:
        tag = f'table:table-row table:number-rows-repeated="{repeat}"'
        return f"<{tag}>{''.join(cells)}</table:table-row>"

    def cell(value: str, repeat: int = 1) -> str:
        tag = f'table:table-cell table:number-columns-repeated="{repeat}"'
        return f"<{tag}>{f'<text:p>{value}</text:p>' if value else ''}</table:table-cell>"

    tail = cell("", 16_384)
    sheet = ods(
        row(cell("query"), cell("", 2), cell("label"), tail),
        row(cell("Café"), cell("", 2), cell("Getränk", 1_000_000_000), tail),
        row(tail, repeat=1_048_000),
    )
    assert read_tabular(sheet, "s.ods").rows == (
        {"query": "Café", "column_2": "", "column_3": "", "label": "Getränk"},
    ), "an OpenDocument repeat shifted a column or expanded a blank tail"
    for refused in (
        ods(row(cell("query"), cell("label", 1_000_000_000)), row(cell("x", 1_000_000_000))),
        ods(row(cell("query")), row(cell("q"), repeat=1_000_000_000)),
    ):
        with pytest.raises(IngestError) as caught:
            read_tabular(refused, "s.ods")
        assert caught.value.reason == "too_large", "a declared repeat read past the caps"
    header_row = row(cell("query"), cell("label"))
    plain = ods(header_row, *(row(*map(cell, r.values())) for r in _EXPORTED_ROWS))
    assert list(read_tabular(plain, "s.ods").rows) == _EXPORTED_ROWS, "an OpenDocument misread"

    # An Excel <dimension> and row number are claims too: the caps bound the walk, not the sheet.
    def xlsx(dimension: str, last_row: int) -> bytes:
        wb = openpyxl.Workbook()
        ws = wb.active
        assert ws is not None
        for r in ({"query": "query", "label": "label"}, *_EXPORTED_ROWS):
            ws.append(list(r.values()))
        written = io.BytesIO()
        wb.save(written)
        out = io.BytesIO()
        with zipfile.ZipFile(written) as src, zipfile.ZipFile(out, "w") as dst:
            for info in src.infolist():
                data = src.read(info)
                if info.filename == "xl/worksheets/sheet1.xml":
                    text = re.sub(
                        r'<dimension ref="[^"]*"', f'<dimension ref="{dimension}"', data.decode()
                    )
                    data = re.sub(r'r="([AB]?)3"', rf'r="\g<1>{last_row}"', text).encode()
                dst.writestr(info, data)
        return out.getvalue()

    assert list(read_tabular(xlsx("A1:B3", 3), "s.xlsx").rows) == _EXPORTED_ROWS
    assert list(read_tabular(xlsx("A1:XFD1048576", 3), "s.xlsx").rows) == _EXPORTED_ROWS, (
        "a declared Excel dimension padded the walk"
    )
    with pytest.raises(IngestError) as caught:
        read_tabular(xlsx("A1:B3", 1_048_576), "s.xlsx")
    assert caught.value.reason == "too_large", "a run of blank Excel rows went uncounted"

    # No held-out row reaches a round panel or an archive view.
    bank = [Sample(id=i, query=f"claim {i}", ground_truth="TRUE") for i in range(60)]
    split = DatasetSplit(bench=12, demo=6)
    part = partition_bank(bank, split)
    held = {s.id for s in (*part.bench, *part.demo)}
    assert (len(part.bench), len(part.demo), len(part.search)) == (12, 6, 42), (
        "the partition held out another count than its split declares"
    )
    assert held.isdisjoint(s.id for s in part.search), "a held-out row sits on the search side"
    assert {s.id for s in part.bench}.isdisjoint(s.id for s in part.demo), (
        "a bench row is also a shot"
    )
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
        assert set(out) <= demo_ids and len(set(out)) == len(out) <= 3, (
            f"seed {seed}: a mutated shot list left the demo pool, repeated a shot or overgrew"
        )
        moves.add(len(out) - 2)
    assert moves == {-1, 0, 1}, "a shot mutation never adds, drops or keeps"
    assert all(
        len(mutate_shots(pool[:3], pool, k_max=3, rng=random.Random(s))) <= 3 for s in range(30)
    ), "a full shot list grew past its k_max"
    child = cross_shots([pool[:2], pool[2:6]], rng=random.Random(0))
    assert len(child) == 3 and set(child) <= demo_ids, "a crossed shot list left the demo pool"
    assert {s.id for s in partition_bank(bank[::-1], split).bench} == {s.id for s in part.bench}, (
        "the bench moved with the bank's row order"
    )
    twins = [*bank, *(s.model_copy(update={"id": 60 + s.id}) for s in bank)]
    doubled = partition_bank(twins, split)
    assert {s.key for s in doubled.bench}.isdisjoint(s.key for s in doubled.search), (
        "a copy of a held-out sample sits on the search side"
    )
    assert len(doubled.bench) == 2 * split.bench, "a held-out sample's copy was searched"
    # A declared bench row moves no other row, so widening a bench keeps every paid cell's pool.
    extra = [
        Sample(id=60 + i, query=f"held {i}", ground_truth="TRUE", bench_only=True) for i in range(9)
    ]
    assert extra[0].key == extra[0].model_copy(update={"bench_only": False}).key, (
        "declaring a row bench-only re-keyed it"
    )
    wide = partition_bank([*bank, *extra], split)
    assert (wide.search, wide.demo) == (part.search, part.demo), "a wider bench moved a paid cell"
    assert wide.bench == (*part.bench, *extra), "a declared bench row was not held out"
    for refused in (
        ([*bank, *extra], None),
        ([*bank, bank[0].model_copy(update={"id": 60, "bench_only": True})], split),
    ):
        with pytest.raises(ValueError):
            partition_bank(*refused)
    # An L4 pool seats a held-out ENVIRONMENT the same way: the seat is out of the pin, so the
    # cell replays once searched, and a pool declaring no split cannot draw it by mistake.
    task = {"kind": "seat", "id": "gsm_hard/8b", "inner_dataset": "gsm_hard", "inner_model": "m"}
    other = {**task, "id": "hotpotqa/8b", "inner_dataset": "hotpotqa"}
    seats = {
        "inner_benchmark": "gsm_hard",
        "inner_benchmark_config": {
            "n_samples_per_inner_round": 1,
            "n_samples_bench": 1,
            "max_inner_rounds": 2,
        },
    }

    def resolved(*tasks: dict[str, Any]) -> dict[str, Any]:
        return {
            "panel": {**seats, "tasks": list(tasks)},
            "instruments": {task["id"]: "i", other["id"]: "j"},
            "datasets": {task["id"]: "gsm_hard", other["id"]: "hotpotqa"},
        }

    seated, drawn = samples_from_dicts(
        _extract_experiment(resolved({**task, "held_out": True}, other))
    )
    searched, _ = samples_from_dicts(_extract_experiment(resolved(task, other)))
    assert seated.bench_only and not searched.bench_only, "a held-out environment was searched"
    assert seated.source_pin == {**task, "instrument": "i"} and seated.key == searched.key, (
        "the seat entered the pin, so the held-out cell never replays once searched"
    )
    with pytest.raises(ValueError):
        partition_bank([seated, drawn], None)
    environments = partition_bank([seated, drawn], DatasetSplit(bench=0))
    assert (environments.search, environments.bench) == ((drawn,), (seated,)), (
        "a held-out environment was drawn for the search"
    )
    # A cycle's diagnostics read the partition its run init drew: an L4 cell's held-out rows ride
    # its bank as bench-only copies, so re-drawing off its config alone searches them.
    cell = [*bank[:30], *(s.model_copy(update={"bench_only": True}) for s in bank[30:50])]
    record = partition_bank(cell, DatasetSplit(bench=0)).record(dataset_hash="d")
    back = restored_partition(bank, record)
    assert (back.search, back.bench) == (tuple(bank[:30]), tuple(bank[30:50])), (
        "the restored partition is not the one the run drew"
    )
    assert partition_bank(bank, DatasetSplit(bench=0)).bench == (), "a re-draw held rows out"
    moved = [*bank[:40], bank[40].model_copy(update={"query": "moved"}), *bank[41:]]
    with pytest.raises(ValueError):
        restored_partition(moved, record)
    contaminating = [Observation("bench_arm", s.key, 0.0) for s in (*part.bench, *part.demo)]
    panel = select_round_subset(list(part.search), contaminating, 20, worth={})
    assert held.isdisjoint(s.id for s in panel), "a held-out row reached a round panel"
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

    # The check-in model reads no held-out row.
    checkin_bank = [Sample(id=i, query=f"claim {i}", ground_truth="TRUE") for i in range(40)]
    built_stores.tenant_datasets.save_materialized_rows("heldout", checkin_bank)
    dataset_dir = built_stores.tenant_datasets.dataset_dir("heldout")
    dataset_dir.mkdir(parents=True)
    (dataset_dir / DEFINITION_FILE).write_text(
        "pipeline: {}\ncampaign:\n  scoring: label_match(predicted, ground_truth)\n"
        "  dataset_split:\n    bench: 30\n  optimization:\n    degradation_threshold: 0.4\n",
        encoding="utf-8",
    )

    async def _offline(*_: Any, **__: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(ingest, "refresh_capabilities", _offline)
    monkeypatch.setattr(ingest, "fetch_backend_nodes", _offline)
    draft = asyncio.run(ingest.draft_from_dataset(stores=built_stores, dataset_name="heldout"))
    content, _ = build_origin_consultation(draft)
    bench_rows = partition_bank(checkin_bank, DatasetSplit(bench=30)).bench
    assert not [s.id for s in bench_rows if f'"{s.query}"' in content], (
        "a bench row reached the resolver"
    )

    # Rewriting the prompt panel cannot accumulate the operator framing.
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
    rendered = "\n\n".join(i.text for i in _r_rendered_prompt(bundle))
    shown = {
        label.removeprefix("[").removesuffix("]"): body
        for label, _, body in (s.partition("\n") for s in rendered.split("\n\n"))
        if label.startswith("[")
    }
    rewritten = OptSearchPoint.derive([opt_sp], variation=_L1, **shown)
    assert rewritten.render_target(framing, demo=()) == opt_sp.render_target(framing, demo=()), (
        "rewriting the shown panel accumulated the operator framing into the scored prompt"
    )
    assert upstream in opt_sp.render_target(framing, demo=()), (
        "dropping the framing from the target render trades this bug for a blinder one"
    )
    assert upstream in "".join(i.text for i in _r_task_context(bundle)), (
        "dropping the framing from the task context trades this bug for a blinder one"
    )

    # The L4 generator is shown the optimizer prompts it rewrites: the INNER campaign's
    # manifest's, never the outer's own.
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

    inner = replace(
        bundle,
        opt_sp=OptSearchPoint(),
        framing=TaskDecomposition(),
        pipeline_schema=outer_schema(tuple(NODE_LAYOUTS)),
        inner_cells=inner_cells("potter"),
    )
    subject_chars = sum(len(i.text) for i in DispatchHub.render_items("rendered_prompt", inner))
    allowance = NODE_LAYOUTS["l1_generate"].discretionary_chars
    assert subject_chars > allowance, (
        f"vacuous — the inner optimizer prompts ({subject_chars}c) fit inside the "
        f"discretionary allowance ({allowance}c), so nothing is being kept against a budget"
    )
    filled = DispatchHub.fill(load_optimizer_prompt("l1_generate"), inner, node="l1_generate")
    assert len(filled.rendered["rendered_prompt"]) >= subject_chars, (
        "the generator was handed no subject — it is rewriting text it cannot see"
    )
    starved = set(filled.breakdown.dropped) & NODE_LAYOUTS["l1_generate"].mandatory
    assert not starved, f"mandatory panel(s) refused by the budget: {sorted(starved)}"
    # A panel mixing two manifests: each outer node is shown, and checked, as its own manifest's.
    capo = resolve_optimizer("capo", {})
    potter = resolve_optimizer("potter", {})
    mixed = inner_cells("potter", "capo")
    mixed_schema = outer_schema((*NODE_LAYOUTS, *capo.llm_nodes))
    mixed_bundle = replace(inner, pipeline_schema=mixed_schema, inner_cells=mixed)
    mixed_shown = "".join(i.text for i in DispatchHub.render_items("rendered_prompt", mixed_bundle))
    base_text = base_optimizer_template(capo, "capo_mutate").instruction
    assert f"[capo_mutate.instruction]\n{base_text}" in mixed_shown, (
        "a CAPO inner's prompt was never shown"
    )
    potter_base = base_optimizer_template(potter, "l1_generate")
    assert f"[l1_generate.instruction]\n{potter_base.instruction}" in mixed_shown, (
        "the potter cell's prompt was never shown beside the CAPO cell's"
    )
    parent = OptSearchPoint()
    edit = {"capo_mutate": {"instruction": base_text + " Keep the rewrite under 80 words."}}
    severed = {"capo_mutate": {"instruction": "Rewrite the prompt."}}
    severed_potter = {
        "l1_generate": {
            f: "Propose variants."
            for f in fields
            if TEMPLATE_TOKEN_RE.findall(str(getattr(potter_base, f) or ""))
        }
    }
    assert severed_potter["l1_generate"], "vacuous — no potter field carries a placeholder"

    def proposed(overlay: dict[str, Any]) -> Any:
        node = next(iter(overlay))
        proposing = types.SimpleNamespace(
            cycle=types.SimpleNamespace(
                optimizer=mixed.owners[node],
                session=types.SimpleNamespace(pipeline_schema=mixed_schema),
            )
        )
        return NodeContext(cast(Any, proposing), node).child([parent], overlay=overlay)

    proposals = [proposed(overlay) for overlay in (edit, severed, severed_potter)]
    parse_population(
        proposals,
        parent,
        None,
        mixed_schema,
        runtime_failures=[],
        demo_ids=frozenset(),
        shot_k_max=0,
        inner_cells=mixed,
        prompt_block_catalogue="guidance",
    )
    assert proposals[0].validation_failures == [], "a valid edit of CAPO's prompt was refused"
    for severing in proposals[1:]:
        assert "dropped_mandatory_placeholder" in [
            f.reason for f in severing.validation_failures
        ], "an edit severing a mixed panel cell's port was measured"
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
