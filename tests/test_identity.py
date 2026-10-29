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
import zipfile
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any, cast

import openpyxl
import pytest
from factories import (
    PARENT,
    PROMPT_INFO,
    arm_state,
    inner_cells,
    loop_session,
    minted_campaign,
    pipeline_schema,
    prompted,
    round_result,
    samples,
    seat,
    set_cycle,
)
from offline_world import Router

from promptpotter.application import optimizers
from promptpotter.application.bench import llm_call as call_mod
from promptpotter.application.bench.children import (
    admission_failures,
    carried_writes,
    overlay_failures,
)
from promptpotter.application.bench.node_context import Calls, Children, NodeContext
from promptpotter.application.bench.task_context import campaign_framing
from promptpotter.application.commands.dispatcher import CommandCall
from promptpotter.application.commands.launching import dispatch_start_checkin, parse_cycle_seed
from promptpotter.application.datasets import ingest, loaders, tabular_ingest
from promptpotter.application.datasets.draft_campaign import new_draft
from promptpotter.application.datasets.draft_patch import apply_draft_patch, plan_draft_patch
from promptpotter.application.datasets.loaders import archive_entry, samples_from_dicts
from promptpotter.application.datasets.origin_resolve import build_origin_consultation
from promptpotter.application.datasets.tabular_ingest import IngestError, read_tabular
from promptpotter.application.initialization.loop_start import verb_ledger
from promptpotter.application.intelligence.indexes.sample import SampleIndex
from promptpotter.application.jobs.launcher import checkin as checkin_launch
from promptpotter.application.jobs.launcher.launch import Inline
from promptpotter.application.jobs.mint import (
    ConfigDriftError,
    CyclePlan,
    arm_plans,
    declare,
    mint_arms,
    resolve_cycle_plan,
    resolve_resume,
)
from promptpotter.application.knobs import bench_defaults_pin
from promptpotter.application.optimizer_manifest import (
    SelectedOptimizer,
    binding_optimizer,
    binding_prompt_overrides,
    resolve_optimizer,
    running_prompt,
    select_optimizer,
)
from promptpotter.application.optimizers.capo.members import cross_shots, mutate_shots
from promptpotter.application.optimizers.gepa.members import GepaReflect
from promptpotter.application.optimizers.gepa.state import ParetoScores
from promptpotter.application.optimizers.nodes import MintFacts, Panel, Proposals, admits
from promptpotter.application.optimizers.potter.dispatch.bundle import (
    CycleSlice,
    InjectionBundle,
    RoundDigest,
)
from promptpotter.application.optimizers.potter.dispatch.facade import DispatchHub
from promptpotter.application.optimizers.potter.dispatch.l1_wire_schema import (
    build_l1_response_schema,
)
from promptpotter.application.optimizers.potter.dispatch.layout import NODE_LAYOUTS
from promptpotter.application.optimizers.potter.dispatch.prompts import (
    base_optimizer_template,
    load_optimizer_prompt,
)
from promptpotter.application.optimizers.potter.l1.population import parse_population
from promptpotter.application.optimizers.potter.records import L2Memory, L3Memory, Ladder
from promptpotter.application.optimizers.potter.validators.l1_strict import l1_shots_in_demo_pool
from promptpotter.application.pipeline_resolve import (
    campaign_admits,
    dataset_pipeline_declaration,
    defaults_drift,
    draft_config,
    experiment_outside_run,
    resolve_campaign_config,
    resolve_pipeline_config_params,
    resolved_output_schemas,
)
from promptpotter.application.run_observers import declare_run_wiring
from promptpotter.application.runner.arm_gate import author_arm
from promptpotter.application.runner.inner.connector import CONNECTOR, promptpotter_wire_adapter
from promptpotter.application.runner.inner.spawn import inner_campaign_id
from promptpotter.application.runner.inner.tasks import (
    InnerBenchmarkConfig,
    InnerTasks,
    InnerTaskSpec,
    inner_instrument_config,
)
from promptpotter.config.paths import block_packs_root, install_datasets_root
from promptpotter.connectors import CONNECTORS, DEFAULT_CONNECTOR
from promptpotter.connectors import harbor as harbor_connector
from promptpotter.domain.arms import PointContext
from promptpotter.domain.bench import DatasetSplit, partition_bank, restored_partition
from promptpotter.domain.campaign import Arm, Compare, DeterminismClamp, Optimize
from promptpotter.domain.campaign_config import (
    CampaignConfig,
    OptimizationConfig,
    declare_run,
    effective_config,
)
from promptpotter.domain.cells import Seat
from promptpotter.domain.command_kinds import (
    ArmRequest,
    ForkSeed,
    HeadToHeadRequest,
    StartCheckinPayload,
    SubmittedArm,
)
from promptpotter.domain.cycle_paths import Cut, CycleDir, CycleHop
from promptpotter.domain.draft_edit import EditDraftPatch, OptimizationOverrides
from promptpotter.domain.judges import JudgeSpec, JudgeStage
from promptpotter.domain.opt_search_point import (
    TEMPLATE_TOKEN_RE,
    Locus,
    NodePrompt,
    OptimizerPromptTemplate,
    OptSearchPoint,
    Variation,
)
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.pipeline_overlay import (
    fold_output_contract,
    normalize_model_id,
    permitted_models_from_narrowing,
    steers_disallowed_model,
)
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import (
    ANSWER_AS_TEXT,
    SCHEMA_TOGGLE_PARAM,
    ManifestNodeOverlay,
    ModelCapability,
    NodeConfigParam,
    NodeKind,
    NodePromptInfo,
    NodeRole,
    NodeSearchNarrowing,
    ObservationMapping,
    ParamIntent,
    PipelineNode,
    PipelineSchema,
    description_key,
    narrowing_of,
)
from promptpotter.domain.pool import EARNED_SOURCE, entry_id
from promptpotter.domain.prompt_block import Authored, BlockPack, Presumption, PromptPart, Snippet
from promptpotter.domain.results import RoundResult
from promptpotter.domain.round_diagnostics import RoundDiagnostics
from promptpotter.domain.run_records import ConfigOverrides, CycleSeed, ScoringLockedRecord
from promptpotter.domain.sample import Bank, Sample
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.domain.spend import Budget
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.registry import default_routes
from promptpotter.infrastructure.llm.spend_book import spending_under, unbounded_spend_book
from promptpotter.infrastructure.llm.telemetry import set_cycle_ledger
from promptpotter.infrastructure.projections.live_dashboard.projection import fold_at
from promptpotter.infrastructure.store.archive_queries import SampleFoldRow
from promptpotter.infrastructure.store.dataset_access import (
    DEFINITION_FILE,
    ResolvedDataset,
    read_dataset_pipeline,
    resolve_dataset,
)
from promptpotter.infrastructure.store.io import write_json, write_yaml
from promptpotter.infrastructure.store.llm_reuse_cache import LLMReuseCache
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.shared.errors import ConflictError, PayloadInvalidError
from promptpotter.shared.hashing import dataset_hash, source_facts, stable_hash
from promptpotter.shared.identity import default_identity

_L1 = Variation(node="potter:l1_generate", mode="llm")
_DATASETS = install_datasets_root()


def _install(name: str) -> ResolvedDataset:
    return ResolvedDataset(name, _DATASETS / name, "install")


_SPEC = InnerTaskSpec(
    inner_dataset="justlogic/d234",
    optimizer_treatment="o",
    run_seed=3,
    n_samples=28,
    n_samples_bench=8,
    n_rounds=4,
)


def _connector_config(**fields: Any) -> CampaignConfig:
    defaults = dict(CONNECTORS.get(DEFAULT_CONNECTOR).default_optimization)
    optimization = {**defaults, **fields.pop("optimization", {})}
    return CampaignConfig.model_validate({"optimization": optimization, **fields})


# 1. Hashes — what a measurement is filed under


def test_a_measurement_is_filed_under_every_input_that_moves_its_number_and_no_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, built_stores: Stores, offline: Router
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
    schema = pipeline_schema(
        [
            PipelineNode(
                name="llm_only",
                kind=NodeKind.LLM,
                current_config={"model": "m", "provider": "p"},
                tunes_llm=False,
            )
        ]
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
    capo = resolve_optimizer("capo", {}, gate="off")
    capo_seat = seat(built_stores, optimization={"optimizer": "capo"})
    calls = NodeContext(capo_seat, optimizers.NODE_MEMBERS.get("capo_mutate")).read(Calls)
    values = {"task_description": "sort the list", "instruction": "Sort it."}
    mutated = {
        "capo_mutate": {"instruction": "Rephrase [{{instruction}}] to {{task_description}}."}
    }
    potter = resolve_optimizer("potter", {}, gate="off")
    every, shelf = frozenset(Presumption), built_stores.block_packs
    sent, capo_hashes, treated = (
        calls.fill(**values),
        capo.prompt_hashes(),
        capo.treatment(every, shelf).digest,
    )
    baseline = potter.prompt_hashes()
    assert potter.prompt_hashes() == baseline, "an unmoved preset re-hashed"
    with binding_prompt_overrides(mutated):
        assert calls.fill(**values) == "Rephrase [Sort it.] to sort the list." != sent, (
            "the mutated CAPO prompt never reached the request it sends"
        )
        assert capo.treatment(every, shelf).digest != treated, (
            "two inner cells under two arms stamp ONE treatment — a bank reads them as one arm"
        )
    # A valid layout edit on one node: `diagnostics` stays placed.
    layout = {"l1_critique": {"layout": {"thinking_style": ["axis_memory"]}}}
    for optimizer, hashes, override, edited_node in (
        (capo, capo_hashes, mutated, "capo_mutate"),
        (potter, baseline, layout, "l1_critique"),
    ):
        with binding_prompt_overrides(override):
            moved = optimizer.prompt_hashes()
        assert moved[edited_node] != hashes[edited_node], (
            f"{edited_node}: an override left the node hash unchanged — audits would pool the cycles"
        )
        assert {k: v for k, v in moved.items() if k != edited_node} == {
            k: v for k, v in hashes.items() if k != edited_node
        }, f"{edited_node}: an edit on one node moved other nodes' hashes"
    assert capo.prompt_hashes() == capo_hashes, "an override outlived the run that bound it"
    # A drafted prompt edit rides the overlay: the node runs it, its hash reads it, L4's still wins.
    drafted = resolve_optimizer(
        "capo",
        {"capo_mutate": ManifestNodeOverlay(config={"instruction": "Reword {{instruction}}"})},
        gate="off",
    )
    drafted_hashes = drafted.prompt_hashes()

    def runs(selected: SelectedOptimizer) -> str:
        config = selected.node_config("capo_mutate")
        return running_prompt("capo_mutate", config, selected.manifest).compile_prompt(**values)

    assert runs(drafted) == "Reword Sort it.", "a drafted prompt edit never reached its node"
    assert drafted_hashes["capo_mutate"] != capo_hashes["capo_mutate"], (
        "a drafted prompt edit ran under the undrafted node's hash"
    )
    assert {k: v for k, v in drafted_hashes.items() if k != "capo_mutate"} == {
        k: v for k, v in capo_hashes.items() if k != "capo_mutate"
    }, "a prompt edit on one node moved other nodes' hashes"
    with binding_prompt_overrides(mutated):
        assert runs(drafted) == "Rephrase [Sort it.] to sort the list.", "a draft edit beat L4's"
        assert drafted.prompt_hashes() == capo.prompt_hashes(), (
            "one text under two declarations hashed apart: the hash reads a second door"
        )

    # The inner campaign id separates two candidates, two seeds and two treatments.
    c1 = {"l1_generate": {"instruction": "widen the axes"}}
    c2 = {"l1_generate": {"instruction": "narrow the axes"}}
    ids = {inner_campaign_id(_SPEC, o) for o in (c1, c2, {}, mutated)}
    assert len(ids) == 4, (
        "two candidates differing only in one override field share a campaign — "
        "their measurements merge under one id with no error"
    )
    assert inner_campaign_id(_SPEC.model_copy(update={"run_seed": 6}), c1) != inner_campaign_id(
        _SPEC, c1
    ), "two seeds share one inner campaign"
    cut = {"pobb": ManifestNodeOverlay.model_validate({"config": {"epsilon": 0.3}})}
    as_run, as_cut = (
        resolve_optimizer("potter", n, gate="off").treatment(every, shelf).digest for n in ({}, cut)
    )
    assert inner_campaign_id(
        _SPEC.model_copy(update={"optimizer_treatment": as_run}), c1
    ) != inner_campaign_id(_SPEC.model_copy(update={"optimizer_treatment": as_cut}), c1), (
        "a moved optimizer knob ran under the inner campaign of the unmoved one"
    )
    rewalked = capo.manifest.model_copy(update={"initial_population_walks": "alone"})
    assert replace(capo, manifest=rewalked).treatment(every, shelf).digest != (
        capo.treatment(every, shelf).digest
    ), "a manifest walking its initial population apart ran under the treatment of one racing it"
    # A pool its readers could draw nothing from never runs: the silent outcome is a pool-less search.
    earned_only = ManifestNodeOverlay(config={"active": [f"persona/{EARNED_SOURCE}"]})
    closed = ManifestNodeOverlay(config={"block_access": "closed"})
    resolve_optimizer("potter", {"block_pool": earned_only}, gate="off")
    with pytest.raises(PayloadInvalidError):
        resolve_optimizer("potter", {"block_pool": earned_only, "l1_generate": closed}, gate="off")
    stray_shot = ManifestNodeOverlay(config={"active": ["not-a-sample-key"]})
    named = resolve_optimizer("capo", {"demo_pool": stray_shot}, gate="off")
    unranked = pipeline_schema(
        [
            PipelineNode(
                name="solve", kind=NodeKind.LLM, current_config={"model": "m"}, tunes_llm=False
            )
        ]
    )
    assert named.pool_refusals(
        mint=MintFacts([Sample(id=1, query="q", ground_truth="a")], admits(unranked, None), shelf)
    ), "a demo pool naming a key its partition lacks reached a mint"
    # A pack's drawable snippets are starting content: two packs merging under one treatment is silent.
    made = Authored(
        kind="authored",
        author="t",
        date=date(2026, 1, 1),
        experiment="t",
        version=1,
        supersedes=None,
        notes=None,
    )
    ranking = {"task_intent": "Pick the single best match.", "answer_format": "End on the answer."}

    def snippet(part: str, text: str, *presumes: Presumption) -> Snippet:
        return Snippet(
            text=text,
            suits=frozenset({PromptPart(part)}),
            tags=(),
            presumes=frozenset(presumes),
            provenance=made,
        )

    def install(name: str, *snippets: Snippet) -> None:
        file = block_packs_root() / name / "pack.json"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(BlockPack(works={}, snippets=snippets).model_dump_json(), "utf-8")

    ranked = (
        snippet("task_intent", ranking["task_intent"], Presumption.RANKING),
        snippet("answer_format", ranking["answer_format"], Presumption.LAST_LINE_ANSWER),
    )
    with monkeypatch.context() as home:
        home.setenv("PROMPTPOTTER_HOME", str(tmp_path / "packs-home"))
        install("one", snippet("thinking_style", "Think it through."))
        install("two", *ranked)

        def drawing(names: list[str], active: list[str]) -> SelectedOptimizer:
            overlay = ManifestNodeOverlay(config={"packs": names, "active": active})
            return resolve_optimizer("potter", {"block_pool": overlay}, gate="off")

        assert drawing(["one"], ["*"]).treatment(every, shelf).source != (
            drawing(["two"], ["*"]).treatment(every, shelf).source
        ), "campaigns seeded from two different block packs share one treatment"
        # Only what `active` keeps reaches a prompt, so only its edit re-keys the run.
        intent_only = drawing(["two"], ["task_intent"]).treatment(every, shelf).digest
        install("two", ranked[0], snippet("answer_format", "Close on the answer."))
        assert drawing(["two"], ["task_intent"]).treatment(every, shelf).digest == intent_only, (
            "an edit to a snippet `active` excludes split one treatment in two"
        )
        install("two", snippet("task_intent", "Pick the best match."), ranked[1])
        assert drawing(["two"], ["task_intent"]).treatment(every, shelf).digest != intent_only, (
            "an edit to a drawable snippet ran under the treatment of the unedited one"
        )
        # Only what the campaign's chain admits is drawn, so only its edit re-keys that campaign.
        ranker = PipelineNode(
            name="rank",
            kind=NodeKind.LLM,
            role=NodeRole.RANKER,
            observation_mappings=[ObservationMapping(pipeline_key="ranking")],
            current_config={"model": "m"},
            tunes_llm=False,
        )
        admitted_by = {
            "unranked": admits(unranked, "label_match"),
            "ranked": admits(pipeline_schema([ranker]), "label_match"),
        }
        install("two", *ranked)
        unedited = {
            k: drawing(["two"], ["*"]).treatment(a, shelf).digest for k, a in admitted_by.items()
        }
        install(
            "two", snippet("task_intent", "Pick the best match.", Presumption.RANKING), ranked[1]
        )
        edited = {
            k: drawing(["two"], ["*"]).treatment(a, shelf).digest for k, a in admitted_by.items()
        }
        assert edited["unranked"] == unedited["unranked"], (
            "an edit to a ranking snippet a non-ranking campaign can never draw re-keyed its treatment"
        )
        assert edited["ranked"] != unedited["ranked"], (
            "an edit to a ranking snippet a ranking campaign draws ran under the unedited treatment"
        )
        install("two", *ranked)
        # A block presuming what the run lacks is noise in its prompt: naming one refuses the mint.
        ranks = entry_id("task_intent", ranking["task_intent"])
        last_line = entry_id("answer_format", ranking["answer_format"])
        for path, matcher, admitted in (
            (ranks, "label_match", False),
            (last_line, "list_rr", False),
            (last_line, "label_match", True),
        ):
            refused = drawing(["two"], [path]).pool_refusals(
                mint=MintFacts((), admits(unranked, matcher), shelf)
            )
            assert (not refused) == admitted, (
                f"{path} under {matcher}: admission missed a presumption"
            )

    # The determinism clamp outranks every other layer and keys the bank.
    reuse = LLMReuseCache(tmp_path / "reuse", "optimizer_reuse")

    def ask(
        content: str,
        config: dict[str, Any],
        clamp: DeterminismClamp | None = None,
        **override: Any,
    ) -> Any:
        """One optimizer call as an entry sends it; the body that reached the provider."""

        async def entered() -> None:
            set_cycle_ledger(CycleEventLog(tmp_path / "asked.jsonl"))
            with (
                binding_optimizer(
                    select_optimizer(OptimizationConfig(degradation_threshold=0.0)),
                    determinism=clamp,
                ),
                spending_under(unbounded_spend_book()),
            ):
                await call_mod.llm_call(
                    [{"role": "user", "content": content}],
                    config=config,
                    context=call_mod.LLMCallContext(cache=reuse),
                    **override,
                )

        asyncio.run(entered())
        return offline.llm.sent[-1]

    pinned = DeterminismClamp(temperature=0.0, seed=7, route_order=["Alibaba"])
    clamped = {"provider": "openrouter", "model": "m", "temperature": 0.9}
    for clamp, (temperature, seed, route) in (
        (pinned, (0.0, 7, ["Alibaba"])),
        (pinned.model_copy(update={"route_order": ["Baidu"]}), (0.0, 7, ["Baidu"])),
        (None, (0.7, None, None)),
    ):
        sent = ask("q", clamped, clamp, temperature=0.7)
        assert (sent["temperature"], sent.get("seed"), sent["provider"].get("order")) == (
            temperature,
            seed,
            route,
        ), (
            f"{clamp}: the node's file value or the per-call override beat the clamp — the "
            "campaign reports itself pinned and runs unpinned"
        )
    assert len(list((tmp_path / "reuse" / "optimizer_reuse").glob("*.json"))) == 3, (
        "the second route replayed the first route's banked answer"
    )

    # A node left to the gateway's choice of host is filed as, and sent to, the host it is pinned to.
    routes = default_routes()

    def filed_under(cfg: dict[str, Any]) -> str:
        routed = pipeline_schema(
            [
                PipelineNode(
                    name="llm_only",
                    kind=NodeKind.LLM,
                    current_config={"model": cfg["model"]},
                    prompt_info=PROMPT_INFO,
                    tunes_llm=True,
                )
            ],
            default_routes=routes,
        )
        point = prompted("llm_only", instruction="x").configured({"llm_only": cfg}, routed)
        job = PointContext(schema=routed, framing=TaskDecomposition(), demo=[]).job(point)
        return archive_entry(job, dataset_hash="d", pipeline_schema=routed).config_key

    # The individual is {locus: value} over the roster: a child written on the SECOND prompt
    # node's field is scored with it and filed apart, each node rendering its own declared order.
    chain = pipeline_schema(
        [
            PipelineNode(
                name=name,
                kind=NodeKind.LLM,
                current_config={"model": "m"},
                prompt_info=NodePromptInfo(fields=fields),
                tunes_llm=True,
            )
            for name, fields in (
                ("extract", ("instruction", "answer_format")),
                ("classify", ("persona", "instruction")),
            )
        ]
    )
    two = OptSearchPoint(
        prompts={
            "extract": NodePrompt(fields={"instruction": "Pull the amount.", "answer_format": "N"}),
            "classify": NodePrompt(fields={"persona": "You book.", "instruction": "Pick a code."}),
        }
    ).configured({}, chain)
    second = OptSearchPoint.derive(
        [two],
        schema=chain,
        variation=_L1,
        changes={Locus("classify", "instruction"): "Pick the four-digit code."},
    )
    assert second.lineage.variations[0].loci == ["classify.instruction"], (
        "the variation wrote another locus than the second node's instruction"
    )
    assert second.prompt("extract") == two.prompt("extract"), "an edit on one node moved another"

    def scored(point: OptSearchPoint) -> tuple[dict[str, Any], str]:
        job = PointContext(schema=chain, framing=TaskDecomposition(), demo=[]).job(point)
        key = archive_entry(job, dataset_hash="d", pipeline_schema=chain).config_key
        return job.pipeline_params, key

    (before, before_key), (after, after_key) = scored(two), scored(second)
    assert after["classify"]["prompt"] == "You book.\n\nPick the four-digit code.", (
        "the second node's edited instruction never reached the prompt it scores"
    )
    assert after["extract"]["prompt"] == before["extract"]["prompt"] == "Pull the amount.\n\nN", (
        "the first node's prompt moved, or rendered in another order than it declares"
    )
    assert after_key != before_key, "an edit on the second prompt node was filed with its parent"

    for model in ("openai/gpt-oss-120b", "openai/gpt-oss-20b:nitro"):
        bare = normalize_model_id(model)
        gateway = next(name for name, pins in routes.items() if bare in pins)
        pin = routes[gateway][bare]
        routing = {"provider": gateway, "model": model}
        assert filed_under(routing) == filed_under({**routing, "route_order": pin}), (
            f"{model}: an unpinned node was filed apart from the host it is served by"
        )
        assert filed_under(routing) != filed_under({**routing, "route_order": ["Elsewhere"]}), (
            f"{model}: two hosts answered under one key"
        )
        for opted, route in (({}, pin), ({"route_order": []}, None)):
            sent = ask(model, {"provider": gateway, "model": model, **opted})
            assert sent.get("provider", {}).get("order") == route, (
                f"{model} {opted}: an optimizer call went to another host than the one it is "
                "filed under"
            )

    # A bank is its rows' content: a question alone tells two banks apart.
    asked_as = [
        [Sample(id=0, query="q", ground_truth="g", question=question)] for question in ("a", "b")
    ]
    assert dataset_hash(asked_as[0]) != dataset_hash(asked_as[1]), (
        "two banks differing only in their question share one identity"
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
    declared = effective_config(
        declare_run(
            unframed,
            purpose=Optimize(),
            dataset_hash="bank",
            connector="termnorm",
            bench_defaults_pin=bench_defaults_pin(),
        )
    )
    assert not campaign_framing(built_stores, declared, gsm8k), (
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
    campaign = {"optimization": {"budget": {"rounds": 2}, "degradation_threshold": 0.4}}
    for name in ("innerbench", "otherbench"):
        write_yaml(
            built_stores.install_datasets_root / name / DEFINITION_FILE,
            {"pipeline": {"nodes": {"llm_only": {"config": {"model": "a"}}}}, "campaign": campaign},
        )

    resolve, extract, identity_of = (
        CONNECTOR.resolve_experiment,
        CONNECTOR.extract_experiment,
        CONNECTOR.identity_config,
    )
    assert resolve is not None and extract is not None and identity_of is not None

    def keyed(declared: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
        resolved = resolve(built_stores, declared)
        rows = samples_from_dicts(extract(resolved))
        return {s.query: s.key for s in rows}, identity_of(built_stores, outer, resolved)

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
    def panel_tasks(harbor_dataset: str, version: str, include: list[str]) -> list[Any]:
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
        assert resolved is not None, f"{harbor_dataset} resolved no panel"
        return list(resolved["tasks"])

    for harbor_dataset, version, include in (
        ("spreadsheetbench-verified", "1.0", ["10452", "105-24"]),
        ("terminal-bench-sample", "2.0", ["regex-log"]),
    ):
        assert [t["id"] for t in panel_tasks(harbor_dataset, version, include)] == include, (
            f"{harbor_dataset} resolved another panel than its include list"
        )
    assert not offline.unrouted, f"a panel read fetched Harbor's registry: {offline.unrouted}"
    with pytest.raises(ValueError):
        panel_tasks("unpinned", "9", ["a"])

    def published(commit: str) -> list[dict[str, Any]]:
        task = {"name": "a", "git_url": "u", "git_commit_id": commit, "path": "a"}
        return [{"name": "bench", "version": "1.0", "description": "", "tasks": [task]}]

    monkeypatch.setattr(harbor_connector, "_ROSTER_DIR", tmp_path)
    offline.registry = published("c1")
    roster = harbor_connector.pin_roster("bench", "1.0")
    assert harbor_connector.pin_roster("bench", "1.0") == roster, "an unchanged registry re-pinned"
    offline.registry = published("c2")
    with pytest.raises(ValueError):
        harbor_connector.pin_roster("bench", "1.0")
    assert [t["git_commit_id"] for t in panel_tasks("bench", "1.0", ["a"])] == ["c1"], (
        "a repinned registry moved the panel under the name it had"
    )

    # A held cut is keyed on its recipe's code too, so an edited recipe re-cuts its bank.
    write_yaml(
        built_stores.tenant_datasets.dataset_dir("hubcut") / DEFINITION_FILE,
        {
            "source": {
                "recipe": "columns",
                "hub": "org/repo",
                "rev": "c1",
                "split": "test",
                "query": "q",
                "truth": "t",
            },
            "pipeline": {"backend_type": "termnorm"},
        },
    )
    hubcut = resolve_dataset(built_stores, "hubcut")
    hub_source = loaders.definition_source(hubcut.directory)
    assert hub_source is not None
    hub_bank = Bank.of([Sample(id=0, query="q", ground_truth="g")])
    built_stores.tenant_datasets.record_cut(hub_source.identity, hub_bank)
    held_cut = loaders.held_bank(built_stores, hubcut)
    assert held_cut is not None and held_cut.dataset_hash == hub_bank.dataset_hash, (
        "a recorded cut was not held"
    )

    for recipe, imported, why in (
        ("table", {loaders, tabular_ingest}, "a recipe's identity missed code it imports"),
        ("columns", {loaders}, "a recipe's identity took code it does not run"),
    ):
        assert set(loaders.recipe_modules(recipe)) == imported, why


# 2. The config a campaign runs


def test_a_campaign_runs_the_config_its_check_in_froze_and_its_forks_narrowed(
    tmp_path: Path, built_stores: Stores, offline: Router
) -> None:
    # The draft's CHAIN reaches the mint on a reused dataset.
    pinned = {"per_sample": "label_match(predicted, ground_truth)", "per_cell": "correct"}
    root = built_stores.tenant_datasets.dataset_dir("ds")
    optimization = dict(CONNECTORS.get(DEFAULT_CONNECTOR).default_optimization)
    schema = pipeline_schema(
        [
            PipelineNode(name=n, kind=NodeKind.LLM, tunes_llm=False)
            for n in ("web_search", "rerank", "llm_only")
        ]
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
        owner_user_id=str(built_stores.identity.user_id),
        slug="ds",
        n_samples=1,
        sample_preview=[],
        headers=[],
        source_dataset="ds",
    ).patch(optimization_overrides={"optimizer": "capo"})
    live = [n.name for n in schema.nodes]
    picked = draft.patch(pipeline_steps=["llm_only"])
    chosen = draft_config(built_stores, picked, session_chain=live)
    assert sorted(chosen.exclude_nodes) == ["rerank", "web_search"], "a picked chain ran whole"
    assert schema.active_steps_excluding(chosen.exclude_nodes) == ["llm_only"], (
        "the excluded complement runs another chain than was picked"
    )
    assert chosen.optimization.optimizer == "capo", "the draft's optimizer did not reach the mint"
    assert draft_config(built_stores, picked) == chosen, (
        "what a read serves before Start is not what Start declares"
    )
    assert chosen.scoring == pinned, "the pinned criterion did not reach the mint"
    dialled = draft_config(built_stores, picked.patch(scoring_dials="cost=0.1"))
    assert dialled.scoring == {"per_sample": pinned["per_sample"], "dials": "cost=0.1"}, (
        "dials froze another criterion than they spell"
    )
    untouched = draft_config(built_stores, draft, session_chain=live)
    assert untouched.exclude_nodes == [], (
        "`pipeline_steps=[]` is untouched: excluding its complement would close the whole pipeline"
    )
    # A draft edit sent as rows freezes the search space the overlay spelled. A REUSED dataset:
    # its file is not the check-in's to write, so the overlay rides the config.
    reused = built_stores.tenant_datasets.dataset_dir("d")
    write_yaml(
        reused / DEFINITION_FILE,
        {
            "pipeline": {"backend_type": "termnorm"},
            "campaign": _connector_config().model_dump(mode="json"),
        },
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
        owner_user_id=str(built_stores.identity.user_id),
        slug="d",
        n_samples=1,
        sample_preview=[],
        headers=[],
        source_dataset="d",
    ).patch(backend_nodes={"llm_only": declared_node})

    def frozen(**patch: Any) -> Any:
        plan = plan_draft_patch(built_stores, edited, EditDraftPatch.model_validate(patch))
        return draft_config(built_stores, apply_draft_patch(edited, plan))

    rows = [
        {"key": "reasoning_effort", "open": True, "allowed": ["low", "high"]},
        {"key": "temperature", "open": False, "allowed": []},
    ]
    spelled = {
        "param_keys": ["reasoning_effort"],
        "param_allowed_values": {"reasoning_effort": ["low", "high"]},
    }
    as_rows = frozen(node_narrowing={"llm_only": rows}).optimizer_narrowing
    assert as_rows == {"llm_only": NodeSearchNarrowing.model_validate(spelled)}, (
        "rows froze another space"
    )
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
    # A padlocked prompt row holds that field alone and opens no config axis.
    prompt_row = narrowing_of(
        [NodeConfigParam(key="persona", kind="prompt")],
        [ParamIntent(key="persona", open=False, allowed=[])],
    )
    assert (prompt_row.param_keys, prompt_row.prompt_fields_held) == ([], ["persona"]), (
        "a padlocked prompt row drew another search space"
    )

    # A campaign runs the config it froze, whatever its dataset file says later.
    stores = built_stores
    dials = {"per_sample": "exact", "dials": "cost: 0.1"}
    write_yaml(
        stores.tenant_datasets.dataset_dir("fz") / DEFINITION_FILE,
        {
            "campaign": _connector_config(
                optimization={"optimizer": "capo", "budget": {"rounds": 3}}
            ).model_dump(mode="json")
        },
    )
    campaign = minted_campaign(
        CycleHop(campaign_id="fz__000001", cycle_id="cycle_root"),
        config=_connector_config(
            optimization={"budget": {"rounds": 12}},
            optimizer_narrowing={"a": {"param_keys": ["temperature"]}, "b": {"param_keys": ["k"]}},
            scoring=dials,
        ),
        dataset_name="fz",
        owner_user_id=str(stores.identity.user_id),
    )
    stores.campaigns.create_campaign(campaign)
    stores.campaigns.mint_cycle(campaign.root_hop)
    ran = resolve_campaign_config(stores, campaign, campaign.root_hop)
    assert (ran.optimization.optimizer, ran.optimization.budget.rounds) == ("potter", 12), (
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
        optimizer_narrowing={"a": NodeSearchNarrowing(param_keys=[])},
        config_overrides=ConfigOverrides(budget=Budget(rounds=4)),
    )
    stores.campaigns.write_cycle_seed(campaign.root_hop, seed)
    forked = resolve_campaign_config(stores, campaign, campaign.root_hop)
    assert forked.optimization.budget.rounds == 4, "a seed's override did not reach its cycle"
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

    steered = minted_campaign(
        CycleHop(campaign_id="fk__000001", cycle_id="cycle_root"),
        config=_connector_config(),
        dataset_name="fk",
        owner_user_id=str(stores.identity.user_id),
    )
    stores.campaigns.create_campaign(steered)
    fork = CycleHop(campaign_id=steered.campaign_id, cycle_id="cycle_fork")
    for hop, rungs in ((steered.root_hop, ["low", "high"]), (fork, ["low", "high", "xhigh"])):
        stores.campaigns.mint_cycle(hop)
        stores.campaigns.write_resolved_run(
            hop,
            declaration=declaring(rungs),
            experiment=None,
            model_capabilities={},
            backend_revision=None,
        )
    fork_seed = parse_cycle_seed(
        ForkSeed.model_validate({"node_narrowing": {"a": rows}}),
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
    # A steer the campaign never sanctioned cannot pass as a clean fork.
    sanctioning = {
        "l1_generate": NodeSearchNarrowing(param_allowed_values={"model": ["openai/gpt-oss-120b"]})
    }
    assert permitted_models_from_narrowing(sanctioning) == {
        "l1_generate": ["openai/gpt-oss-120b"]
    }, "the set served beside the verdict is not the set the verdict compares against"
    sanctioned = {"model": "openai/gpt-oss-120b"}
    for steer, babysat, why in (
        ({"l1_generate": sanctioned}, False, "a sanctioned responder was graded a babysit act"),
        (
            {"l1_generate": {"temperature": 0.9}},
            False,
            "an axis edit read as a steer of WHO ANSWERS",
        ),
        (
            {"l1_generate": {"model": "deepseek/deepseek-v4"}},
            True,
            "an unsanctioned responder passed",
        ),
        ({"l2_context": sanctioned}, True, "a node never narrowed sanctioned a model"),
        ({"l1_generate": {"route_order": ["a", "b"]}}, True, "a cost lever passed as clean"),
    ):
        assert steers_disallowed_model(sanctioning, steer) is babysat, why
    assert not steers_disallowed_model({}, {}), "an empty steer is not a babysit act"

    # Every door declares through ONE seam, and a declaration is a delta over today's defaults: a
    # moved code default leaves the minted manifest alone and is named when it moves the origin.
    write_yaml(
        stores.tenant_datasets.dataset_dir("decl") / DEFINITION_FILE,
        {
            "pipeline": {"backend_type": "termnorm"},
            "campaign": {"optimization": {"degradation_threshold": 0.4}},
        },
    )
    rows_30 = samples(30, "TRUE", query="claim ")
    solve = PipelineNode(name="solve", tunes_llm=False, prompt_info=PROMPT_INFO)
    opened = loop_session(
        stores,
        pipeline_schema([solve], name="decl"),
        rows_30,
        dataset=resolve_dataset(stores, "decl"),
    )
    door = _connector_config()
    declared = declare(opened, door, purpose=Optimize())
    assert effective_config(declared).model_dump() == door.model_dump(), (
        "a seat runs another config than the one it declared"
    )
    plan = resolve_cycle_plan(opened, declared, rows_30)
    for unmeasured in (
        {"optimization": {"budget": {"usd": 9.0, "rounds": 3}}},
        {"display_metric": "composite"},
    ):
        moved = declare(opened, _connector_config(**copy.deepcopy(unmeasured)), purpose=Optimize())
        assert (
            moved != declared
            and resolve_cycle_plan(opened, moved, rows_30).cycle_id == plan.cycle_id
        ), f"{unmeasured} — a key that bounds spend or display only — moved the root id"
    assert not declared.budget.named and declared.run_seed is None, (
        "a declaration restated a budget axis or a seed no layer set"
    )
    armed = declare(opened, door, purpose=Compare(arm=Arm(head_to_head_id="h", arm_key="a")))
    twin = declare(opened, door, purpose=Compare(arm=Arm(head_to_head_id="h", arm_key="b")))
    assert armed.shared_part() == twin.shared_part(), (
        "two arms of one declaration hold different shared parts"
    )
    arm_plan = resolve_cycle_plan(opened, armed, rows_30)
    assert arm_plan.origin_hash == plan.origin_hash, (
        "an arm and a seat over one dataset, partition and environment read different origins"
    )
    twin_plan = resolve_cycle_plan(opened, twin, rows_30)
    run_ids = {plan.cycle_id, arm_plan.cycle_id, twin_plan.cycle_id}
    assert len(run_ids) == 3, (
        "two arms over one content, or an arm and a seat, share a run id every surface keys on"
    )
    arming = loop_session(stores, opened.pipeline_schema, rows_30, dataset=opened.dataset)

    def arm_of(key: str, budget: Budget) -> list[CyclePlan]:
        request = HeadToHeadRequest(
            head_to_head_id="h",
            arms=[
                ArmRequest(
                    arm_key=key,
                    optimization=OptimizationOverrides(optimizer=door.optimization.optimizer),
                )
            ],
            budget=budget,
        )
        return asyncio.run(arm_plans(arming, door, request, rows_30))

    # No dollar cap: a backend holding its own sends prices no cell before a run.
    mint_arms(arm_of("a", Budget(usd=None, rounds=2)), billed=None)
    # An arm declared over another budget never joins its head-to-head, and takes no campaign.
    standing = stores.campaigns.list_campaigns(lifecycle="all")
    with pytest.raises(ConflictError):
        arm_of("b", Budget(usd=None, rounds=3))
    assert stores.campaigns.list_campaigns(lifecycle="all") == standing, (
        "an arm refused off its head-to-head's record minted a campaign"
    )
    # An L4 seat declares through the door every campaign takes, and an arm so declared runs what a
    # campaign minted on that declaration resolves to.
    panel = InnerTasks(
        inner_benchmark="decl",
        inner_benchmark_config=InnerBenchmarkConfig(
            n_samples_per_inner_round=4, n_samples_bench=6, max_inner_rounds=2
        ),
        tasks=[Seat(kind="seat", id="decl/seed-3", run_seed=3)],
    )
    cells = panel.resolve_cells(stores)
    (l4_seat,) = cells.cells
    seat_config = inner_instrument_config(
        cells.spec(l4_seat),
        CampaignConfig.model_validate(dict(cells.held(l4_seat).campaign_config)),
        solver=[solve],
        n_search=24,
    )
    l4 = declare(opened, seat_config, purpose=Optimize())
    assert effective_config(l4).model_dump() == seat_config.model_dump(), (
        "an L4 seat runs another config than the one its seat declared"
    )
    assert l4.run_seed == 3, "an L4 seat's declaration lost the seed its seat names"
    l4_arm = declare(
        opened, seat_config, purpose=Compare(arm=Arm(head_to_head_id="h4", arm_key="a"))
    )
    as_campaign = minted_campaign(
        CycleHop(campaign_id="decl__0000a4", cycle_id="cycle_l4arm"),
        config=effective_config(l4_arm),
        purpose=l4_arm.purpose,
        dataset_name="decl",
        dataset_hash=opened.bank.dataset_hash,
        owner_user_id=str(stores.identity.user_id),
    )
    stores.campaigns.create_campaign(as_campaign)
    stores.campaigns.mint_cycle(as_campaign.root_hop)
    assert (
        resolve_campaign_config(stores, as_campaign, as_campaign.root_hop).model_dump()
        == effective_config(l4_arm).model_dump()
    ), "an arm runs another config than a campaign declared that way"
    held_campaign = minted_campaign(
        CycleHop(campaign_id="decl__000001", cycle_id=plan.cycle_id),
        config=door,
        dataset_name="decl",
        root_content_hash=plan.origin_hash,
        dataset_hash=opened.bank.dataset_hash,
        owner_user_id=str(stores.identity.user_id),
    )
    assert held_campaign.declaration == declared, (
        "the manifest holds another declaration than the door made"
    )
    manifest_path = stores.campaigns.create_campaign(held_campaign)
    manifest = json.loads(manifest_path.read_bytes())
    with pytest.raises(ConflictError):
        stores.campaigns.create_campaign(
            held_campaign.model_copy(update={"root_content_hash": "drift"})
        )
    assert json.loads(manifest_path.read_bytes()) == manifest, (
        "a drifted origin re-minted over the campaign its banked rounds stand on"
    )
    stores.campaigns.mint_cycle(held_campaign.root_hop)
    stores.campaigns.write_bank_partition(
        held_campaign.root_hop,
        partition_bank(rows_30, door.dataset_split, seed=0).record(
            dataset_hash=opened.bank.dataset_hash
        ),
    )
    opened = opened.bound(held_campaign.root_hop, purpose=Optimize())
    resolve_resume(opened, held_campaign, rows_30)
    wired_ledger = CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(held_campaign.root_hop)))
    root_cut = Cut(
        cycle=CycleDir(stores.campaigns.cycle_dir(held_campaign.root_hop)),
        hop=held_campaign.root_hop,
    )
    # A manifest pinned over bench defaults this build no longer ships runs on, and what it runs
    # under says so on the record every launch writes.
    stale = held_campaign.model_copy(
        update={"declaration": declared.model_copy(update={"bench_defaults_pin": "older"})}
    )
    running = select_optimizer(effective_config(declared).optimization)
    runs_under = running.treatment(
        campaign_admits(effective_config(declared), opened.pipeline_schema), stores.block_packs
    )
    drift = defaults_drift(stale, running=runs_under)
    assert drift is not None and drift.moved() == ["bench"], (
        "a moved bench defaults pin drifted nothing, or blamed another part"
    )
    for on_disk, served_drift in ((stale, drift), (held_campaign, None)):
        write_json(manifest_path, on_disk.model_dump(mode="json"))
        declare_run_wiring(
            opened, effective_config(declared), wired_ledger, selected=running, tracing=None
        )
        served = fold_at(root_cut)
        assert served is not None and served.defaults_drift == served_drift, (
            "a launch past moved bench defaults served no drift, or one on its own served one"
        )
    # The optimizer's defaults are its whole treatment: a part moved under one declaration is named.
    reshipped = held_campaign.treatment.model_copy(update={"manifest": "older"})
    reshipped_drift = defaults_drift(
        held_campaign.model_copy(update={"treatment": reshipped}), running=runs_under
    )
    assert reshipped_drift is not None and reshipped_drift.moved() == ["manifest"], (
        "an optimizer whose manifest moved under one declaration drifted nothing, or blamed "
        "another part"
    )
    recut = loop_session(
        stores,
        opened.pipeline_schema,
        rows_30[:-1],
        dataset=opened.dataset,
        hop=held_campaign.root_hop,
    )
    # A resume over an edited bank never runs on an origin other than its rounds stand on.
    with pytest.raises(ConfigDriftError):
        resolve_resume(recut, held_campaign, rows_30[:-1])
    # The candidates a ranker reads beside the bank's answers are origin content too.
    with pytest.raises(ConfigDriftError):
        resolve_resume(
            replace(opened, candidate_library=("an unlisted label",)), held_campaign, rows_30
        )
    # A steering seed is its fork's declared delta, never a waiver of the root's own drift.
    steered_hop = CycleHop(campaign_id=held_campaign.campaign_id, cycle_id="cycle_steered")
    stores.campaigns.mint_cycle(steered_hop)
    partition = stores.campaigns.read_bank_partition(held_campaign.root_hop)
    assert partition is not None, "the held campaign banked no partition"
    stores.campaigns.write_bank_partition(steered_hop, partition)
    stores.campaigns.write_cycle_seed(
        steered_hop, CycleSeed(pipeline_overlay={"solve": {"temperature": 0.3}})
    )
    steered_fork = opened.bound(steered_hop, purpose=Optimize())
    resolve_resume(steered_fork, held_campaign, rows_30)
    with pytest.raises(ConfigDriftError):
        resolve_resume(steered_fork, held_campaign, rows_30[:-1])
    # The capability menu the origin hashed under is on record: a moved one is the cause named.
    floored = ModelCapability(
        model="m", provider="", reasoning_efforts=["high"], reasoning_note="", source="test"
    )
    stores.campaigns.write_resolved_run(
        held_campaign.root_hop,
        declaration={},
        experiment=None,
        model_capabilities={"": {"m": floored}},
        backend_revision=None,
    )
    with pytest.raises(ConfigDriftError) as refused:
        resolve_resume(recut, held_campaign, rows_30[:-1])
    assert refused.value.details["capabilities_moved"] == ["/m"], (
        "a resume past a moved capability snapshot blamed only the dataset"
    )

    # Answering in TEXT sends no contract to answer INTO.
    text_pipeline = read_dataset_pipeline(_DATASETS / "justlogic" / "d234")
    assert text_pipeline is not None
    text_schema = parse_pipeline_response(text_pipeline)
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
    amount_items: dict[str, Any] = {"type": "object", "properties": {"amount": {"type": "string"}}}
    invoice = {"lines": {"type": "array", "items": amount_items}, "total": {"type": "number"}}
    nested = pipeline_schema(
        [
            PipelineNode(
                name="llm_only",
                kind=NodeKind.LLM,
                current_config={
                    "model": "m",
                    "output_schema": {"type": "object", "properties": invoice},
                },
                tunes_llm=False,
            )
        ]
    )
    amount = description_key("lines.amount")
    declared = cast(Any, nested.get_node("llm_only")).current_config
    pp = fold_output_contract({"llm_only": {**declared, amount: "Net, in CHF."}}, nested)
    line = pp["llm_only"]["output_schema"]["properties"]["lines"]["items"]["properties"]["amount"]
    assert line["description"] == "Net, in CHF." and amount not in pp["llm_only"], (
        "a nested field's description did not fold into the contract it names"
    )
    assert "description" not in amount_items["properties"]["amount"], (
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
    for overlay, refused_axes in (
        ({"l1_generate": {"invented_knob": 1}}, ["l1_generate.invented_knob"]),
        ({"l1_critique": {"layout": "hdr"}}, ["l1_critique.layout"]),
        ({"l1_critique": {"layout": {"instruction": ["plan"]}}}, []),
    ):
        found = [f.axis for f in overlay_failures(overlay, outer_schema, running={})]
        assert found == refused_axes, f"{overlay}: admitted a param the schema does not declare"
    grade = PipelineNode(
        name="grade",
        kind=NodeKind.LLM,
        current_config={"model": "m"},
        prompt_info=PROMPT_INFO,
        tunes_llm=True,
    )
    graded = pipeline_schema([*outer_schema.declared_nodes, grade])
    parent = prompted("grade", persona="You grade.", instruction="Answer {{combined_text}}.")
    wiped = OptSearchPoint.derive(
        [parent], schema=graded, variation=_L1, changes={Locus("grade", "persona"): ""}
    )
    assert wiped.lineage.variations[0].loci == ["grade.persona"], (
        "a deletion is a move like any other: an emptied field is a locus the variation wrote"
    )
    assert wiped.lineage.parent_ids == [parent.id], "a wiped field lost its parent"
    refusals = admission_failures(wiped, {"l1_generate": {"invented_knob": 1}}, graded, carried=[])
    assert [f.axis for f in refusals] == ["l1_generate.invented_knob"], (
        "an invented knob was admitted"
    )
    # Each carrier writes its own kind: a prompt locus the node does not declare, a config key
    # aimed at a prompt field, and a node the pipeline lacks are all refused on the child.
    _, config, carried = carried_writes(
        graded,
        prompt_loci={
            Locus("grade", "invented_field"): "x",
            Locus("grade", "persona"): "You grade strictly.",
            Locus("ghost", "persona"): "x",
        },
        overlay={"grade": {"instruction": "Answer."}},
        demo=(),
    )
    assert sorted(f.axis for f in carried) == [
        "ghost",
        "grade.instruction",
        "grade.invented_field",
    ], "a write off its declared carrier reached the child"
    assert config == {}, "a prompt field rode the config overlay"

    # A SECOND prompt node, on a roster of its own, is written as the first is — by L1's wire and
    # an operator's arm — and a member knowing one prompt is refused pre-spend.
    two = pipeline_schema(
        [
            PipelineNode(name="draft", kind=NodeKind.LLM, prompt_info=PROMPT_INFO, tunes_llm=True),
            PipelineNode(
                name="review",
                kind=NodeKind.LLM,
                prompt_info=NodePromptInfo(fields=("instruction", "rubric")),
                tunes_llm=True,
            ),
        ]
    )
    wire = build_l1_response_schema(two, citable_fields=["critique"], inner_cells=None)
    offered = wire["properties"]["variants"]["items"]["properties"]["prompt_fields_updates"]
    assert set(offered["properties"]["review"]["properties"]) == {"instruction", "rubric"}, (
        "L1 was offered another roster than the second prompt node declares"
    )
    origin = OptSearchPoint(
        prompts={
            "draft": NodePrompt(fields={"instruction": "Draft it."}),
            "review": NodePrompt(fields={"instruction": "Review it.", "rubric": "Correctness."}),
        }
    )
    gepa = resolve_optimizer("gepa", {}, gate="off")
    reflected_on = seat(
        built_stores,
        schema=two,
        bank=[Sample(id=7, query="q", ground_truth="a"), Sample(id=8, query="r", ground_truth="b")],
        demo=1,
        optimization={"optimizer": "gepa"},
    )
    population = [origin]
    set_cycle(
        reflected_on.cycle,
        line=[
            round_result(0, arms={PARENT: arm_state("parent", individual=origin)}, improved=False)
        ],
        population=population,
    )
    arm = SubmittedArm(
        prompt_fields={"review": {"rubric": "Cite the clause."}},
        changes_description="tighten the review",
    )
    authored = author_arm(reflected_on, origin, arm)
    assert authored.validation_failures == [], "an operator arm on the second node was refused"
    assert authored.individual.prompts["review"].fields["rubric"] == "Cite the clause.", (
        "the arm's second-node write did not land"
    )
    assert authored.individual.prompts["draft"] == origin.prompts["draft"], "the arm moved node one"
    stray = SubmittedArm(
        prompt_fields={"review": {"persona": "x"}, "ghost": {"rubric": "x"}},
        changes_description="off roster",
    )
    off_roster = author_arm(reflected_on, origin, stray)
    assert len(off_roster.validation_failures) == 2, "an arm writing off the roster was measured"
    capo = resolve_optimizer("capo", {}, gate="off")
    assert optimizers.unfit_pipeline(capo, two) is not None, (
        "a member rewriting the one prompt node was admitted on a chain of two"
    )
    assert optimizers.unfit_pipeline(gepa, two) is None, "GEPA was refused a chain it can walk"
    # The shipped defaults' config-axis narrowing leaves every prompt field to L1; only
    # `prompt_fields_held` withdraws one, and a surface with nothing open is refused pre-spend.
    axes_only = two.narrow(
        {
            "review": NodeSearchNarrowing(
                param_keys=["temperature", "max_tokens", "reasoning_effort"]
            )
        }
    )
    axes_review = axes_only.get_node("review")
    assert axes_review is not None and axes_review.open_prompt_fields == [
        "instruction",
        "rubric",
    ], "a config-axis narrowing withdrew a prompt field from L1"
    one_held = two.narrow({"review": NodeSearchNarrowing(prompt_fields_held=["rubric"])})
    review = one_held.get_node("review")
    assert review is not None and review.open_prompt_fields == ["instruction"], (
        "a held prompt field withdrew another than itself"
    )
    all_held = two.narrow(
        {
            n.name: NodeSearchNarrowing(param_keys=[], prompt_fields_held=list(n.prompt_fields))
            for n in two.declared_nodes
        }
    )
    assert optimizers.unfit_pipeline(gepa, all_held) is not None, (
        "a campaign leaving its optimizer nothing to edit was admitted"
    )

    # Round 0 measured each parent a reflection re-reads; the bench resolves it by id.
    def measured(*held: OptSearchPoint) -> list[RoundResult]:
        points, schema = reflected_on.cycle.points, reflected_on.cycle.session.pipeline_schema
        arms = {
            ArmKey(ind.id): arm_state(
                "parent",
                individual=ind,
                resolved_params=points.job(ind).config_params or {},
                sp_hash=points.job(ind).sp_hash(schema),
            )
            for ind in held
        }
        arms[PARENT] = arm_state("parent", individual=origin)
        return [round_result(0, arms=arms, improved=False)]

    offline.llm.says = lambda _body: f"```\nRewritten in round {len(population)}.\n```"
    set_cycle(reflected_on.cycle, line=measured(reflected_on.cycle.parent))
    reflected: list[set[str]] = []
    for _ in range(3):
        reflecting = NodeContext(reflected_on, optimizers.NODE_MEMBERS.get("gepa_reflect"))
        no_cells = Panel(cells=[], order=[], block_size=0)
        with (
            binding_optimizer(
                reflected_on.cycle.optimizer,
                determinism=reflected_on.cycle.config.optimization.determinism,
            ),
            spending_under(unbounded_spend_book()),
            verb_ledger(built_stores, reflected_on.cycle.session.hop),
        ):
            reflection = asyncio.run(GepaReflect().propose(reflecting, no_cells, Proposals([])))
        (child,) = reflection.proposals
        reflected.append(
            {locus.split(".")[0] for locus in child.individual.lineage.variations[0].loci}
        )
        population.append(child.individual)
        set_cycle(
            reflected_on.cycle,
            line=measured(reflected_on.cycle.parent, *population),
            population=population,
            slices=[ParetoScores(scores={child.individual.id: {}}, parent_id=child.individual.id)],
        )
    assert reflected == [{"draft"}, {"review"}, {"draft"}], (
        "GEPA's reflection did not round-robin a lineage across the prompt nodes"
    )
    rubric_only = pipeline_schema(
        [
            PipelineNode(
                name="review",
                kind=NodeKind.LLM,
                prompt_info=NodePromptInfo(fields=("rubric",)),
                tunes_llm=True,
            )
        ]
    )
    assert optimizers.unfit_pipeline(gepa, rubric_only) is not None, (
        "a peer was admitted to rewrite a field its prompt node does not declare"
    )


# 3. Contamination of a scored prompt


_EXPORTED_ROWS = [
    {"query": "Café au lait?", "label": "Getränk"},
    {"query": "Smørrebrød", "label": "Essen"},
]


@pytest.mark.usefixtures("offline")
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
        with pytest.raises(IngestError):
            read_tabular(refused, "s.ods")
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
    with pytest.raises(IngestError):
        read_tabular(xlsx("A1:B3", 1_048_576), "s.xlsx")

    # No held-out row reaches a round panel or an archive view.
    bank = samples(60, "TRUE", query="claim ")
    split = DatasetSplit(bench=12, demo=6)
    part = partition_bank(bank, split, seed=0)
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
        rejected = [
            f.value
            for f in l1_shots_in_demo_pool(
                {"classify": [part.demo[0].id, leak]}, demo_ids=demo_ids, k_max=len(demo_ids)
            )
        ]
        assert rejected == [str(leak)], f"a shot naming scored row {leak} was accepted"
    # Asserts the SUPPORT, never the stream: add, drop and keep each occur over the seeds.
    demo_keys = frozenset(s.key for s in part.demo)
    pool = sorted(demo_keys)
    moves = set()
    for seed in range(60):
        out = mutate_shots(pool[:2], pool, k_max=3, rng=random.Random(seed))
        assert set(out) <= demo_keys and len(set(out)) == len(out) <= 3, (
            f"seed {seed}: a mutated shot list left the demo pool, repeated a shot or overgrew"
        )
        moves.add(len(out) - 2)
    assert moves == {-1, 0, 1}, "a shot mutation never adds, drops or keeps"
    assert all(
        len(mutate_shots(pool[:3], pool, k_max=3, rng=random.Random(s))) <= 3 for s in range(30)
    ), "a full shot list grew past its k_max"
    child = cross_shots([pool[:2], pool[2:6]], rng=random.Random(0))
    assert len(child) == 3 and set(child) <= demo_keys, "a crossed shot list left the demo pool"
    reversed_bench = partition_bank(bank[::-1], split, seed=0).bench
    assert {s.id for s in reversed_bench} == {s.id for s in part.bench}, (
        "the bench moved with the bank's row order"
    )
    assert {s.id for s in partition_bank(bank, split, seed=1).bench} != {
        s.id for s in part.bench
    }, "the partition seed drew no other bench"
    twins = [*bank, *(s.model_copy(update={"id": 60 + s.id}) for s in bank)]
    doubled = partition_bank(twins, split, seed=0)
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
    wide = partition_bank([*bank, *extra], split, seed=0)
    assert (wide.search, wide.demo) == (part.search, part.demo), "a wider bench moved a paid cell"
    assert wide.bench == (*part.bench, *extra), "a declared bench row was not held out"
    for unsplittable, declared_split in (
        ([*bank, *extra], None),
        ([*bank, bank[0].model_copy(update={"id": 60, "bench_only": True})], split),
    ):
        with pytest.raises(ValueError):
            partition_bank(unsplittable, declared_split, seed=0)
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

    extract = CONNECTOR.extract_experiment
    assert extract is not None
    seated, drawn = samples_from_dicts(extract(resolved({**task, "held_out": True}, other)))
    searched, _ = samples_from_dicts(extract(resolved(task, other)))
    assert seated.bench_only and not searched.bench_only, "a held-out environment was searched"
    assert seated.source_pin == {**task, "instrument": "i"} and seated.key == searched.key, (
        "the seat entered the pin, so the held-out cell never replays once searched"
    )
    with pytest.raises(ValueError):
        partition_bank([seated, drawn], None, seed=0)
    environments = partition_bank([seated, drawn], DatasetSplit(bench=0), seed=0)
    assert (environments.search, environments.bench) == ((drawn,), (seated,)), (
        "a held-out environment was drawn for the search"
    )
    # A cycle's diagnostics read the partition its run init drew: an L4 cell's held-out rows ride
    # its bank as bench-only copies, so re-drawing off its config alone searches them.
    l4_bank = [*bank[:30], *(s.model_copy(update={"bench_only": True}) for s in bank[30:50])]
    cell_drawn = partition_bank(l4_bank, DatasetSplit(bench=0), seed=0)
    back = restored_partition(bank, cell_drawn.record(dataset_hash="d"))
    assert (back.search, back.bench) == (cell_drawn.search, tuple(bank[30:50])), (
        "the restored partition is not the one the run drew"
    )
    assert partition_bank(bank, DatasetSplit(bench=0), seed=0).bench == (), (
        "a re-draw held rows out"
    )
    # A resume after its label was re-cut reads the bank its cycle drew, never the newer one.
    drawn_bank = Bank.of(l4_bank)
    recut = Bank.of([*l4_bank[:40], l4_bank[40].model_copy(update={"query": "x"})])
    for held_bank in (drawn_bank, recut):
        built_stores.tenant_datasets.put_bank(held_bank)
    built_stores.tenant_datasets.relabel("recut", recut.dataset_hash)
    hop = CycleHop(campaign_id="camp-recut", cycle_id="cycle_recut")
    built_stores.campaigns.write_bank_partition(
        hop,
        partition_bank(l4_bank, DatasetSplit(bench=0), seed=0).record(
            dataset_hash=drawn_bank.dataset_hash
        ),
    )
    relabelled = built_stores.tenant_datasets.labelled("recut")
    assert relabelled is not None
    resumed = loop_session(built_stores, pipeline_schema([]), list(relabelled.samples))
    resumed = resumed.bound(hop, purpose=Optimize())
    assert resumed.bank == drawn_bank, (
        "a resume after a re-cut switched its cycle onto the new bank"
    )
    index = SampleIndex(bank=Bank.of(bank), sample_ids=part.admitted_ids)
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
    assert not index.records(), "a held-out row reached the index the optimizer's panels read"

    # The check-in model reads no held-out row.
    checkin_bank = samples(40, "TRUE", query="claim ")
    built_stores.tenant_datasets.put_bank(heldout := Bank.of(checkin_bank))
    built_stores.tenant_datasets.relabel("heldout", heldout.dataset_hash)
    dataset_dir = built_stores.tenant_datasets.dataset_dir("heldout")
    dataset_dir.mkdir(parents=True)
    (dataset_dir / DEFINITION_FILE).write_text(
        "pipeline:\n  backend_type: termnorm\ncampaign:\n  scoring: label_match(predicted, ground_truth)\n"
        "  dataset_split:\n    bench: 30\n  optimization:\n    degradation_threshold: 0.4\n",
        encoding="utf-8",
    )

    draft = asyncio.run(ingest.draft_from_dataset(stores=built_stores, dataset_name="heldout"))
    content, _ = build_origin_consultation(draft)
    bench_rows = partition_bank(checkin_bank, DatasetSplit(bench=30), seed=0).bench
    assert not [s.id for s in bench_rows if f'"{s.query}"' in content], (
        "a bench row reached the resolver"
    )

    # An upload's Start asks the mint's refusals of the draft BEFORE its dataset lands, so a refused
    # Start takes no slug and its retry is refused the same way, never as a collision.
    uploaded = asyncio.run(
        ingest.ingest_draft(stores=built_stores, blob=b"q,a\nx,1\ny,2\nz,1\n", filename="tiny.csv")
    )
    starved = {"l1_generate": {"config": {"model": "deepseek/deepseek-v4-flash", "max_tokens": 64}}}
    built_stores.drafts.write_draft(
        uploaded.draft_id,
        uploaded.patch(
            column_query="q",
            column_ground_truth="a",
            optimization_overrides={**uploaded.optimization_overrides, "nodes": starved},
        ).to_disk(),
    )
    monkeypatch.setattr(checkin_launch, "assert_origin_ready", lambda _draft: None)
    for attempt in ("first", "retry"):
        with pytest.raises(PayloadInvalidError):
            asyncio.run(
                dispatch_start_checkin(
                    built_stores,
                    CommandCall(StartCheckinPayload(draft_id=uploaded.draft_id), attempt),
                    inline=Inline(),
                )
            )
        assert not built_stores.tenant_datasets.slug_exists(uploaded.slug), (
            f"the {attempt} refused Start committed its dataset"
        )

    # Rewriting the prompt panel cannot accumulate the operator framing.
    upstream = "Raw invoice text is provided directly as the input column."
    framing = TaskDecomposition(
        upstream_context=upstream,
        downstream_context="The assigned code books a ledger entry.",
    )
    opt_sp = prompted(
        "classify",
        persona="You assign Swiss account codes.",
        problem_description="Assign the four-digit account code for the invoice.",
    )
    booking = pipeline_schema(
        [PipelineNode(name="classify", kind=NodeKind.LLM, prompt_info=PROMPT_INFO, tunes_llm=True)]
    )
    classify = booking.prompt_nodes()[0]
    bundle = InjectionBundle(
        parent=opt_sp,
        l2=L2Memory(),
        l3=L3Memory(),
        framing=framing,
        pipeline_schema=booking,
        cycle_slice=CycleSlice(
            round_num=1,
            l1_stall_depth=0,
            ladder=Ladder(),
            exploration_budget="tight",
        ),
        digest=RoundDigest(diagnostics=RoundDiagnostics(n_valid=0, samples=[]), critique=None),
        axes=None,
        block_access="open",
        rebase_capability=True,
        terminate_capability=True,
        schema_field_rename=False,
        shot_k_max=0,
        slot_of={},
        pool_window=8,
    )
    rendered = "\n\n".join(i.text for i in DispatchHub.render_items("rendered_prompt", bundle))
    shown = {
        label.removeprefix("[").removesuffix("]"): body
        for label, _, body in (s.partition("\n") for s in rendered.split("\n\n"))
        if label.startswith("[")
    }
    rewritten = OptSearchPoint.derive(
        [opt_sp],
        schema=booking,
        variation=_L1,
        changes={Locus(*label.split(".", 1)): body for label, body in shown.items()},
    )
    assert rewritten.render_target(classify, framing, demo=()) == opt_sp.render_target(
        classify, framing, demo=()
    ), "rewriting the shown panel accumulated the operator framing into the scored prompt"
    assert upstream in opt_sp.render_target(classify, framing, demo=()), (
        "dropping the framing from the target render trades this bug for a blinder one"
    )
    assert upstream in DispatchHub.render("task_context", bundle), (
        "dropping the framing from the task context trades this bug for a blinder one"
    )

    # The L4 generator is shown the optimizer prompts it rewrites: the INNER campaign's
    # manifest's, never the outer's own.
    fields = list(OptimizerPromptTemplate.model_fields)

    def outer_schema(nodes: tuple[str, ...]) -> PipelineSchema:
        return pipeline_schema(
            name="promptpotter-self",
            version="1",
            nodes=[
                PipelineNode(
                    name=name,
                    kind=NodeKind.LLM,
                    param_keys=set(fields),
                    param_types=dict.fromkeys(fields, "string"),
                    tunes_llm=False,
                )
                for name in nodes
            ],
        )

    inner = replace(
        bundle,
        parent=OptSearchPoint(),
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
    refused_panels = set(filled.breakdown.dropped) & NODE_LAYOUTS["l1_generate"].mandatory
    assert not refused_panels, f"mandatory panel(s) refused by the budget: {sorted(refused_panels)}"
    # A panel mixing two manifests: each outer node is shown, and checked, as its own manifest's.
    capo = resolve_optimizer("capo", {}, gate="off")
    potter = resolve_optimizer("potter", {}, gate="off")
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
        proposing = seat(
            built_stores,
            schema=mixed_schema,
            optimization={"optimizer": mixed.owners[node].name},
        )
        return (
            NodeContext(proposing, optimizers.NODE_MEMBERS.get(node))
            .read(Children)
            .child([parent], overlay=overlay)
        )

    proposals = parse_population(
        [proposed(overlay) for overlay in (edit, severed, severed_potter)],
        parent,
        None,
        mixed_schema,
        runtime_failures=[],
        inner_cells=mixed,
    )
    assert proposals[0].validation_failures == [], "a valid edit of CAPO's prompt was refused"
    for severing in proposals[1:]:
        assert severing.validation_failures, (
            "an edit severing a mixed panel cell's port was measured"
        )
    assert proposals[0].individual.lineage.variations[0].loci == ["capo_mutate.instruction"], (
        "the overlay's locus is missing from the variation that proposed it"
    )
    payload = promptpotter_wire_adapter(
        "justlogic-d234/seed-0", proposals[0].individual.pipeline_params
    )
    with binding_prompt_overrides(payload["optimizer_prompt_overrides"]):
        capo_seat = seat(built_stores, optimization={"optimizer": "capo"})
        sent = (
            NodeContext(capo_seat, optimizers.NODE_MEMBERS.get("capo_mutate"))
            .read(Calls)
            .fill(task_description="sort the list", instruction="Sort it.")
        )
    assert sent.endswith("Keep the rewrite under 80 words."), "the inner cell ran the parent"
