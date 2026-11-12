"""A wrong identity, or a quiet cross-contamination.

Owns the hashes a measurement is filed under (`sp_hash`, `prompt_hashes`, `hash_call`,
`inner_campaign_id`), `application/pipeline_resolve.py`, `domain/pipeline_overlay.py`, the held-out
partition and what reaches a scored prompt. Two things that differ answer as one, or one thing is
scored on text it was never meant to carry.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import io
import json
import random
import re
import sys
import time
import zipfile
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, cast
from unittest.mock import Mock

import openpyxl
import pytest
from factories import (
    PARENT,
    PROMPT_INFO,
    arm_state,
    backend_client,
    inner_cells,
    inner_sandbox,
    jumping_clock,
    loop_session,
    minted_campaign,
    pipeline_schema,
    prompted,
    round_result,
    run_init_round,
    samples,
    seat,
    set_cycle,
    spend_book,
    stored_campaign,
    workspace,
)
from offline_world import LLM_ONLY_NODE, Router
from pydantic import ValidationError

from promptpotter.application import optimizer_manifest, optimizers
from promptpotter.application.bench import llm_call as call_mod
from promptpotter.application.bench.children import (
    admission_failures,
    carried_writes,
    overlay_failures,
)
from promptpotter.application.bench.node_context import Calls, Children, NodeContext
from promptpotter.application.bench.resume_and_fork.fork_siblings import declare_steered_values
from promptpotter.application.bench.task_context import (
    CheckinOutput,
    OriginFinding,
    campaign_framing,
    checkin_template,
)
from promptpotter.application.commands import launching, origin_resolving
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.commands.launching import dispatch_start_checkin, parse_cycle_seed
from promptpotter.application.datasets import (
    ingest,
    loaders,
    origin_resolve,
    startability,
    tabular_ingest,
)
from promptpotter.application.datasets.authored import (
    ConfigLayers,
    campaign_file_block,
    dataset_campaign_block,
    validated_campaign_config,
)
from promptpotter.application.datasets.draft_campaign import (
    DraftCriterion,
    draft_criterion,
    new_draft,
    owned_draft,
    upload_pipeline_json,
)
from promptpotter.application.datasets.draft_patch import apply_draft_patch, plan_draft_patch
from promptpotter.application.datasets.loaders import archive_entry, samples_from_dicts
from promptpotter.application.datasets.origin_resolve import (
    build_origin_consultation,
    raised_commands,
)
from promptpotter.application.datasets.startability import upload_source
from promptpotter.application.datasets.tabular_ingest import IngestError, read_tabular
from promptpotter.application.embedded_run import run_campaign
from promptpotter.application.hierarchy import entries_version, list_hierarchy
from promptpotter.application.initialization.scoring_scope import verb_ledger
from promptpotter.application.intelligence.indexes.sample import SampleIndex
from promptpotter.application.jobs.launcher import checkin as checkin_launch
from promptpotter.application.jobs.launcher.launch import Inline
from promptpotter.application.jobs.launcher.mint_and_start import (
    OriginIncompleteError,
    commit_upload_definition,
)
from promptpotter.application.jobs.mint import (
    ConfigDriftError,
    CyclePlan,
    arm_plans,
    declare,
    mint_arms,
    resolve_cycle_plan,
    resolve_resume,
)
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.knobs import bench_defaults_pin
from promptpotter.application.optimizer_manifest import (
    SelectedOptimizer,
    checkin_manifest,
    running_prompt,
    select_optimizer,
)
from promptpotter.application.optimizers import nodes
from promptpotter.application.optimizers.capo.members import cross_shots, mutate_shots
from promptpotter.application.optimizers.gepa.members import GepaReflect, ParetoScores
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
)
from promptpotter.application.optimizers.potter.l1 import parse_population
from promptpotter.application.optimizers.potter.records import L2Memory, L3Memory, Ladder
from promptpotter.application.optimizers.potter.validators import l1_shots_in_demo_pool
from promptpotter.application.origin_listing import list_datasets, list_origins
from promptpotter.application.pipeline_resolve import (
    ParamLayers,
    ServedPipeline,
    campaign_admits,
    campaign_config_map,
    cycle_config,
    dataset_pipeline,
    defaults_drift,
    draft_config,
    draft_pipeline,
    draft_served,
    experiment_outside_run,
    fork_preview,
    identity_layer,
    laid_config,
    permitted_narrowing,
    resolve_pipeline_at,
    resolved_output_schemas,
)
from promptpotter.application.pipeline_responses import ForkPreviewResponse
from promptpotter.application.run_observers import (
    RunObservers,
    build_run_observers,
    declare_run_stop,
    declare_run_wiring,
)
from promptpotter.application.runner.bench import cycle_bench
from promptpotter.application.runner.entry import run_optimization
from promptpotter.application.runner.inner import spawn
from promptpotter.application.runner.inner.spawn import (
    CONNECTOR,
    inner_campaign_id,
    promptpotter_wire_adapter,
)
from promptpotter.application.runner.inner.tasks import (
    InnerBenchmarkConfig,
    InnerSpawnContext,
    InnerTasks,
    InnerTaskSpec,
    inner_instrument_config,
)
from promptpotter.application.runner.round import author_arm
from promptpotter.application.scoring.formula.compiler import campaign_terms
from promptpotter.application.scoring.measurement_log import cells_scope, measurement_log
from promptpotter.config import paths
from promptpotter.config.paths import block_packs_root, install_datasets_root
from promptpotter.connectors import harbor as harbor_connector
from promptpotter.connectors import upload_connector
from promptpotter.domain.arms import PointContext
from promptpotter.domain.bench import (
    BenchRefusal,
    DatasetSplit,
    PartitionRecord,
    partition_bank,
    restored_partition,
)
from promptpotter.domain.campaign import (
    Arm,
    CampaignRefusal,
    CampaignRefusedError,
    Compare,
    DeterminismClamp,
    Optimize,
    run_clamp,
)
from promptpotter.domain.campaign_config import (
    CampaignConfig,
    CycleConfig,
    LaidSpan,
    OptimizationConfig,
    OptimizerChoice,
    declare_run,
    effective_config,
)
from promptpotter.domain.cells import PinnedCell, Seat
from promptpotter.domain.checkin_draft import DatasetBasis, DraftFile, UploadBasis
from promptpotter.domain.command_kinds import (
    ArmRequest,
    ForkSeed,
    HeadToHeadRequest,
    PauseCyclePayload,
    ResolveOriginPayload,
    StartCheckinPayload,
    SubmittedArm,
)
from promptpotter.domain.cycle_paths import Cut, CycleDir, CycleHop
from promptpotter.domain.dataset_name import validate_dataset_name
from promptpotter.domain.dataset_start import DatasetUnstartableError, StartBlock
from promptpotter.domain.draft_edit import EditDraftPatch, OptimizationOverrides
from promptpotter.domain.judges import JudgeSpec, JudgeStage
from promptpotter.domain.opt_search_point import Locus, NodePrompt, OptSearchPoint, Variation
from promptpotter.domain.optimizer_template import TEMPLATE_TOKEN_RE, OptimizerPromptTemplate
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.phases import PauseCause, RunPhase, StopReason
from promptpotter.domain.pipeline_overlay import (
    fold_output_contract,
    normalize_model_id,
    permitted_models_from_narrowing,
    steers_disallowed_model,
)
from promptpotter.domain.pipeline_params import NodeCall, PipelineParams
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import (
    ANSWER_AS_JSON,
    ANSWER_AS_TEXT,
    SCHEMA_TOGGLE_PARAM,
    KnobLayer,
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
    pickable,
    running_only,
)
from promptpotter.domain.pool import EARNED_SOURCE, entry_id
from promptpotter.domain.prompt_block import Authored, BlockPack, Presumption, PromptPart, Snippet
from promptpotter.domain.results import RoundResult
from promptpotter.domain.round_diagnostics import RoundDiagnostics
from promptpotter.domain.ruler import RulerReach
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.run_records import (
    ConfigOverrides,
    CycleSeed,
    CycleSeedRecord,
    LaunchClaimRecord,
    PipelineRead,
    RoundWarningRecord,
    ScoringLockedRecord,
)
from promptpotter.domain.sample import Bank, Sample
from promptpotter.domain.scoring import ScoringFormulaError
from promptpotter.domain.search_point import NodeConfig, TaskDecomposition
from promptpotter.domain.spend import Budget, HeldBudget
from promptpotter.infrastructure.identity import blocklist, grants
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm import pricing
from promptpotter.infrastructure.llm.registry import default_routes
from promptpotter.infrastructure.producer_lock import cycle_held, release, take
from promptpotter.infrastructure.projections.base import Projection
from promptpotter.infrastructure.projections.live_dashboard.projection import (
    DashboardCheckout,
    fold_at,
)
from promptpotter.infrastructure.store import source_scan
from promptpotter.infrastructure.store.archive_queries import SampleFoldRow
from promptpotter.infrastructure.store.dataset_access import (
    DEFINITION_FILE,
    DatasetCard,
    ResolvedDataset,
    campaign_defaults,
    install_dataset,
    list_readable_datasets,
    read_dataset_definition,
    read_dataset_pipeline,
    resolve_dataset,
    runnable_definition,
    with_node_templates,
)
from promptpotter.infrastructure.store.draft_store import DRAFT_FILE
from promptpotter.infrastructure.store.io import rmtree_robust, write_json, write_yaml
from promptpotter.infrastructure.store.layout import CampaignLayout, CycleLayout
from promptpotter.infrastructure.store.llm_reuse_cache import LLMReuseCache
from promptpotter.infrastructure.store.read_model import LedgerIndex, derived, file_sig
from promptpotter.infrastructure.store.session_pointer import read_active_pointer
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.presentation.cli import campaign_runner
from promptpotter.presentation.cli.commands import new as cli_new
from promptpotter.shared.clock import iso_z
from promptpotter.shared.errors import (
    LANDING_REMEDY,
    CellUnscoreableError,
    ConflictError,
    DatasetAccessError,
    FileKind,
    FileUnreadableError,
    PayloadInvalidError,
    PotterError,
    SlugTakenError,
)
from promptpotter.shared.hashing import SourceFacts, dataset_hash, source_facts, stable_hash
from promptpotter.shared.identity import CAMPAIGN_BABYSIT_CAP, default_identity
from promptpotter.shared.run_scope import scoped

_L1 = Variation(node="potter:l1_generate", mode="llm")
_DATASETS = install_datasets_root()
_RESEARCH = Path(__file__).resolve().parents[1] / "research" / "datasets"


def _install(name: str) -> ResolvedDataset:
    return install_dataset(_DATASETS, name)


_SPEC = InnerTaskSpec(
    inner_dataset="justlogic/d234",
    optimizer_treatment="o",
    run_seed=3,
    n_samples=28,
    n_samples_bench=8,
    n_rounds=4,
)


def _l4_arm(optimizer: str, overrides: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """An L4 cell's `optimization`, the arm laid as a spawn lays it (`spawn.py::_open_seat`)."""
    return _l4_laid(optimizer, overrides).model_dump(mode="json", exclude_unset=True)


def _l4_laid(optimizer: str, overrides: dict[str, dict[str, Any]]) -> OptimizerChoice:
    bare = CampaignConfig.model_validate(
        {"optimization": {"optimizer": optimizer, "degradation_threshold": 0.0}}
    )
    return spawn._laid_arm(bare, overrides).optimization


def _l4_selected(optimizer: str, overrides: dict[str, dict[str, Any]]) -> SelectedOptimizer:
    return select_optimizer(_l4_laid(optimizer, overrides))


def _connector_config(**fields: Any) -> CampaignConfig:
    threshold = campaign_defaults()["optimization"]["degradation_threshold"]
    defaults = {"degradation_threshold": threshold}
    optimization = {**defaults, **fields.pop("optimization", {})}
    return CampaignConfig.model_validate({"optimization": optimization, **fields})


# 1. Hashes — what a measurement is filed under


@pytest.mark.usefixtures("optimizing_memory")
def test_a_measurement_is_filed_under_every_input_that_moves_its_number_and_no_other(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    built_stores: Stores,
    offline: Router,
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
    # A refusal shapes no prompt: its words, and what only a raise reads, are free.
    refusing = marked + (
        "HINT = 1\n"
        "@shapes_optimizer_prompt\n"
        "def cut(n):\n"
        "    if n < 0:\n"
        "        raise ValueError(f'no {n}')\n"
        "    return n\n"
    )
    reworded = refusing.replace("f'no {n}'", "HINT, n")
    assert digest(reworded) == digest(refusing), "a reworded refusal re-keyed a treatment"
    cut_reads = [u["reads"] for u in source_facts(reworded)["units"] if u["name"] == "cut"]
    assert cut_reads and "HINT" not in cut_reads[0]["names"], "a raise-only read was walked"
    # An annotation is free, but `Annotated` metadata is a value its name carries, so it is walked.
    annotated = marked + "@shapes_optimizer_prompt\ndef pick(n: Annotated[Arm, BOUND]): pass\n"
    (pick,) = source_facts(annotated)["units"]
    assert "BOUND" in pick["reads"]["names"], "an annotation's metadata went unwalked"
    assert "Arm" not in pick["reads"]["names"], "an annotation was walked"
    # The walk stops at plumbing: editing what it reads re-keys nothing.
    package = (tmp_path / "pkg" / "promptpotter").resolve()
    for module, text in {
        "__init__": "",
        "config/__init__": "",
        "config/settings": "WIDTH = 2\n",
        "config/shaped": "WIDTH = 2\n",
        "unit": marked + "from promptpotter.config import settings, shaped\n"
        "@shapes_optimizer_prompt\n"
        "def width():\n    return settings.WIDTH + shaped.WIDTH\n",
    }.items():
        (package / f"{module}.py").parent.mkdir(parents=True, exist_ok=True)
        (package / f"{module}.py").write_text(text, encoding="utf-8")

    unit = ModuleType("promptpotter.unit")
    unit.__file__ = str(package / "unit.py")

    def process() -> Callable[[], str]:
        """A new process on the scratch package: nothing held in memory, one cache file."""
        stores = {
            "_FACTS": source_scan._ContentCache[SourceFacts](source_scan.SOURCE_FACTS_FILE),
            "_DIGESTS": source_scan._ContentCache[str](source_scan.TREATMENT_DIGESTS_FILE),
        }

        def treatment() -> str:
            with monkeypatch.context() as patch:
                patch.setattr(source_scan, "_PACKAGE_ROOT", package)
                patch.setattr(source_scan, "install_cache_root", lambda: tmp_path / "cache")
                for name, store in stores.items():
                    patch.setattr(source_scan, name, store)
                return source_scan.prompt_source_digest((unit,), covered=(), foreign=())

        return treatment

    def with_edit(module: str, text: str) -> str:
        path = package / f"{module}.py"
        kept = path.read_text(encoding="utf-8")
        path.write_text(text, encoding="utf-8")
        try:
            return process()()
        finally:
            path.write_text(kept, encoding="utf-8")

    assert with_edit("config/settings", "WIDTH = 3\n") == process()(), (
        "a plumbing module's edit re-keyed a treatment"
    )
    assert with_edit("config/shaped", "WIDTH = 3\n") != process()(), (
        "the walk stopped short of a shaping module"
    )
    # A digest is derived from the tree its key hashes: a module edited after a process's first
    # digest is answered by the edited text, whether the cache file holds it or not.
    running = process()
    first_digest = running()
    (package / "unit.py").write_text(
        (package / "unit.py").read_text(encoding="utf-8") + "EXTRA = 1\n", encoding="utf-8"
    )
    edited_digest = running()
    assert edited_digest != first_digest, "an edit after a first digest kept the old text's digest"
    digests_file = tmp_path / "cache" / source_scan.TREATMENT_DIGESTS_FILE
    assert digests_file.is_file(), "the process kept no digest"
    assert process()() == edited_digest, "a new process read a cached digest of another text"
    digests_file.unlink()
    assert process()() == edited_digest, "the cached digest differs from a fresh derivation"

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

    def sp_hash(judges: dict[str, JudgeSpec]) -> str:
        identity = identity_layer(None, None, None, judges, schema.active_steps)
        return schema.sp_hash(
            ParamLayers(schema=schema, workspace=None, identity=identity).fold().params
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
    capo = select_optimizer(OptimizerChoice(optimizer="capo"))
    capo_seat = jumping_clock(
        run_init_round(
            workspace(tmp_path / "capo"),
            offline,
            samples(40, "TRUE"),
            optimization={"optimizer": "capo"},
        )
    )
    calls = NodeContext(capo_seat, optimizers.NODE_MEMBERS.get("capo_mutate")).read(Calls)
    values = {"task_description": "sort the list", "instruction": "Sort it."}
    mutated = {
        "capo_mutate": {"instruction": "Rephrase [{{instruction}}] to {{task_description}}."}
    }
    potter = select_optimizer(OptimizerChoice(optimizer="potter"))
    every = frozenset(Presumption)
    sent, capo_hashes, treated = (
        calls.prompt().compile_prompt(**values),
        capo.prompt_hashes(),
        capo.treatment(every, built_stores.block_packs).digest,
    )
    baseline = potter.prompt_hashes()
    assert potter.prompt_hashes() == baseline, "an unmoved preset re-hashed"
    capo_calls = NodeContext(
        jumping_clock(
            run_init_round(
                workspace(tmp_path / "mutated"),
                offline,
                samples(40, "TRUE"),
                optimization=_l4_arm("capo", mutated),
            )
        ),
        optimizers.NODE_MEMBERS.get("capo_mutate"),
    ).read(Calls)
    assert (
        capo_calls.prompt().compile_prompt(**values)
        == "Rephrase [Sort it.] to sort the list."
        != sent
    ), "the mutated CAPO prompt never reached the request it sends"
    assert _l4_selected("capo", mutated).treatment(every, built_stores.block_packs).digest != (
        treated
    ), "two inner cells under two arms stamp ONE treatment — a bank reads them as one arm"
    # A valid layout edit on one node: `diagnostics` stays placed.
    layout = {"l1_critique": {"layout": {"thinking_style": ["axis_memory"]}}}
    for optimizer, hashes, override, edited_node in (
        (capo, capo_hashes, mutated, "capo_mutate"),
        (potter, baseline, layout, "l1_critique"),
    ):
        moved = _l4_selected(optimizer.name, override).prompt_hashes()
        assert moved[edited_node] != hashes[edited_node], (
            f"{edited_node}: an override left the node hash unchanged — audits would pool the cycles"
        )
        assert {k: v for k, v in moved.items() if k != edited_node} == {
            k: v for k, v in hashes.items() if k != edited_node
        }, f"{edited_node}: an edit on one node moved other nodes' hashes"
    # A lever no render can apply is REFUSED at selection, as an unknown key is, never dropped to
    # run the floor under the arm's name; a padded name is one name, stripped once.
    for unusable in (
        {"l1_critique": {"layout": {"thinking_style": ["no_such_panel"]}}},
        {"l1_generate": {"output_schema_field_names": {"rationale": "not a name"}}},
        {"l1_generate": {"output_schema_field_names": {"rationale": "why", "evidence": "why"}}},
    ):
        with pytest.raises(PayloadInvalidError) as overlay_refusal:
            _l4_selected("potter", unusable)
        assert overlay_refusal.value.code == "optimizer_overlay", (
            f"{unusable}: an unusable L4 mutation ran as the floor under its arm's name"
        )
    # An arm's model answers every inner node, whichever node its edit names it on.
    answered = _l4_selected("potter", {"l1_critique": {"model": "qwen/qwen3.7-flash"}})
    assert {answered.node_config(n).model for n in answered.llm_nodes} == {"qwen/qwen3.7-flash"}, (
        "an L4 arm's model answered one inner node, the rest the manifest's"
    )
    # A drafted prompt edit rides the overlay: the node runs it, its hash reads it, L4's still wins.
    drafted = select_optimizer(
        OptimizerChoice(
            optimizer="capo",
            nodes={
                "capo_mutate": ManifestNodeOverlay(config={"instruction": "Reword {{instruction}}"})
            },
        )
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
    # A reset is the draft's path list; a null laid over an authored field would reset it unseen.
    with pytest.raises(PayloadInvalidError, match="instruction"):
        select_optimizer(
            OptimizerChoice(
                optimizer="capo",
                nodes={"capo_mutate": ManifestNodeOverlay(config={"instruction": None})},
            )
        )
    both = select_optimizer(
        spawn._laid_arm(
            CampaignConfig.model_validate(
                {
                    "optimization": {
                        "optimizer": "capo",
                        "degradation_threshold": 0.0,
                        "nodes": {
                            "capo_mutate": {"config": {"instruction": "Reword {{instruction}}"}}
                        },
                    }
                }
            ),
            mutated,
        ).optimization
    )
    assert runs(both) == "Rephrase [Sort it.] to sort the list.", "a draft edit beat L4's"
    assert both.prompt_hashes() == _l4_selected("capo", mutated).prompt_hashes(), (
        "one text under two declarations hashed apart: the hash reads a second door"
    )

    # The inner campaign id separates two candidates, two seeds, two treatments and two estimators.
    c1 = {"l1_generate": {"instruction": "widen the axes"}}
    c2 = {"l1_generate": {"instruction": "narrow the axes"}}
    ids = {inner_campaign_id(_SPEC, o, "e") for o in (c1, c2, {}, mutated)}
    assert len(ids) == 4, (
        "two candidates differing only in one override field share a campaign — "
        "their measurements merge under one id with no error"
    )
    breaching = {"l1_critique": {"layout": {"thinking_style": ["no_such_panel"]}}}
    assert inner_campaign_id(_SPEC, breaching, "e") != inner_campaign_id(_SPEC, {}, "e"), (
        "a refused layout arm continued the floor arm's inner campaign"
    )
    assert inner_campaign_id(
        _SPEC.model_copy(update={"run_seed": 6}), c1, "e"
    ) != inner_campaign_id(_SPEC, c1, "e"), "two seeds share one inner campaign"
    assert inner_campaign_id(_SPEC, c1, "e") != inner_campaign_id(_SPEC, c1, "f"), (
        "a moved estimator continued the campaign the old one banked"
    )
    cut = {"pobb": ManifestNodeOverlay.model_validate({"config": {"epsilon": 0.3}})}
    as_run, as_cut = (
        select_optimizer(OptimizerChoice(optimizer="potter", nodes=n))
        .treatment(every, built_stores.block_packs)
        .digest
        for n in ({}, cut)
    )
    assert inner_campaign_id(
        _SPEC.model_copy(update={"optimizer_treatment": as_run}), c1, "e"
    ) != inner_campaign_id(_SPEC.model_copy(update={"optimizer_treatment": as_cut}), c1, "e"), (
        "a moved optimizer knob ran under the inner campaign of the unmoved one"
    )
    rewalked = capo.manifest.model_copy(update={"initial_population_walks": "alone"})
    assert replace(capo, manifest=rewalked).treatment(every, built_stores.block_packs).digest != (
        capo.treatment(every, built_stores.block_packs).digest
    ), "a manifest walking its initial population apart ran under the treatment of one racing it"
    # A prompt-shaping class's method re-keys the run though it is only ever read through an instance.
    manifest_file, read_text = Path(optimizer_manifest.__file__), Path.read_text
    as_shipped = read_text(manifest_file, "utf-8")
    drawing_on = as_shipped.replace(
        'self.access(member, pool) != "off"', 'self.access(member, pool) == "on"'
    )
    assert drawing_on != as_shipped, "the probe's edit no longer lands in `SelectedOptimizer.draws`"
    as_run_source = potter.treatment(every, built_stores.block_packs).source
    with monkeypatch.context() as patch:
        patch.setattr(
            Path,
            "read_text",
            lambda p, *a, **k: drawing_on if p == manifest_file else read_text(p, *a, **k),
        )
        nodes._source_digest.cache_clear()
        assert potter.treatment(every, built_stores.block_packs).source != as_run_source, (
            "an edit to which pools a member draws ran under the unedited treatment"
        )
    nodes._source_digest.cache_clear()
    # A pool its readers could draw nothing from never runs: the silent outcome is a pool-less search.
    earned_only = ManifestNodeOverlay(config={"active": [f"persona/{EARNED_SOURCE}"]})
    closed = ManifestNodeOverlay(config={"block_access": "closed"})
    select_optimizer(OptimizerChoice(optimizer="potter", nodes={"block_pool": earned_only}))
    with pytest.raises(PayloadInvalidError):
        select_optimizer(
            OptimizerChoice(
                optimizer="potter", nodes={"block_pool": earned_only, "l1_generate": closed}
            )
        )
    stray_shot = ManifestNodeOverlay(config={"active": ["not-a-sample-key"]})
    named = select_optimizer(OptimizerChoice(optimizer="capo", nodes={"demo_pool": stray_shot}))
    unranked = pipeline_schema(
        [
            PipelineNode(
                name="solve", kind=NodeKind.LLM, current_config={"model": "m"}, tunes_llm=False
            )
        ]
    )
    assert named.pool_refusals(
        mint=MintFacts(
            [Sample(id=1, query="q", ground_truth="a")],
            admits(unranked, None),
            built_stores.block_packs,
        )
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
            return select_optimizer(
                OptimizerChoice(optimizer="potter", nodes={"block_pool": overlay})
            )

        assert drawing(["one"], ["*"]).treatment(every, built_stores.block_packs).source != (
            drawing(["two"], ["*"]).treatment(every, built_stores.block_packs).source
        ), "campaigns seeded from two different block packs share one treatment"
        # Only what `active` keeps reaches a prompt, so only its edit re-keys the run.
        intent_only = (
            drawing(["two"], ["task_intent"]).treatment(every, built_stores.block_packs).digest
        )
        install("two", ranked[0], snippet("answer_format", "Close on the answer."))
        assert (
            drawing(["two"], ["task_intent"]).treatment(every, built_stores.block_packs).digest
            == intent_only
        ), "an edit to a snippet `active` excludes split one treatment in two"
        install("two", snippet("task_intent", "Pick the best match."), ranked[1])
        assert (
            drawing(["two"], ["task_intent"]).treatment(every, built_stores.block_packs).digest
            != intent_only
        ), "an edit to a drawable snippet ran under the treatment of the unedited one"
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
            k: drawing(["two"], ["*"]).treatment(a, built_stores.block_packs).digest
            for k, a in admitted_by.items()
        }
        install(
            "two", snippet("task_intent", "Pick the best match.", Presumption.RANKING), ranked[1]
        )
        edited = {
            k: drawing(["two"], ["*"]).treatment(a, built_stores.block_packs).digest
            for k, a in admitted_by.items()
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
                mint=MintFacts((), admits(unranked, matcher), built_stores.block_packs)
            )
            assert (not refused) == admitted, (
                f"{path} under {matcher}: admission missed a presumption"
            )

    # The determinism clamp outranks every other layer and keys the bank.
    reuse = LLMReuseCache(tmp_path / "reuse", "optimizer_reuse")

    def ask(
        content: str,
        clamp: DeterminismClamp | None = None,
        *,
        temperature: float | None = None,
        seed: int | None = None,
        **laid: Any,
    ) -> Any:
        """One optimizer call as an entry sends it; the body that reached the provider."""

        async def entered() -> None:
            with (
                scoped(
                    ledger=CycleEventLog(tmp_path / "asked.jsonl"),
                    optimizer=select_optimizer(OptimizerChoice()),
                    determinism=clamp,
                ),
                scoped(book=spend_book()),
            ):
                await call_mod.llm_call(
                    [{"role": "user", "content": content}],
                    context=call_mod.LLMCallContext(
                        node="checkin",
                        config=NodeConfig.model_validate(
                            checkin_manifest().config.model_dump(exclude_unset=True) | laid
                        ),
                        template=checkin_template(),
                        cache=reuse,
                        seed=seed,
                    ),
                    temperature=temperature,
                )

        asyncio.run(entered())
        return offline.llm.sent[-1]

    pinned = DeterminismClamp(temperature=0.0, seed=7, route_order=["Alibaba"])
    for clamp, (temperature, seed, route) in (
        (pinned, (0.0, 7, ["Alibaba"])),
        (pinned.model_copy(update={"route_order": ["Baidu"]}), (0.0, 7, ["Baidu"])),
        (None, (0.7, None, None)),
    ):
        sent = ask("q", clamp, provider="openrouter", model="m", temperature=0.7)
        assert (sent["temperature"], sent.get("seed"), sent["provider"].get("order")) == (
            temperature,
            seed,
            route,
        ), (
            f"{clamp}: the node's file value or the per-call override beat the clamp — the "
            "campaign reports itself pinned and runs unpinned"
        )
    banked = tmp_path / "reuse" / "optimizer_reuse"
    assert len(list(banked.glob("*.json"))) == 3, (
        "the second route replayed the first route's banked answer"
    )

    # A head-to-head pins one seed on every arm: an arm's replicate (numbered at the mint, § 2)
    # enters the seed, and arms of one replicate share it, so a pair differs by its treatment alone
    # (common random numbers).
    def armed(key: str, replicate: int) -> Compare:
        return Compare(arm=Arm(head_to_head_id="h", arm_key=key, replicate=replicate))

    drawn = [ask("q", run_clamp(pinned, armed("potter", n))).get("seed") for n in (1, 2)]
    assert drawn[0] != drawn[1], (
        "two replicates under a pinned seed sent one request, so the second replayed the first's "
        "answer and N replicates read as one draw"
    )
    assert run_clamp(pinned, armed("gepa", 1)) == run_clamp(pinned, armed("potter", 1)), (
        "two arms of one replicate drew apart, so their difference carries draw noise"
    )
    assert run_clamp(pinned, Optimize()) == pinned, "a run outside a head-to-head moved its pin"

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
        point = prompted("llm_only", instruction="x").configured(
            PipelineParams().with_nodes({"llm_only": cfg}), routed
        )
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
    ).configured(PipelineParams(), chain)
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
        return job.pipeline_params.laid_nodes, key

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
        opts: tuple[tuple[dict[str, Any], Any], ...] = (({}, pin), ({"route_order": []}, None))
        for opted, route in opts:
            sent = ask(model, provider=gateway, model=model, **opted)
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
    tenant_copy = built_stores.tenant_datasets.dataset_dir("innerbench")
    for root in (tenant_copy, built_stores.install_datasets_root / "otherbench"):
        write_yaml(
            root / DEFINITION_FILE,
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
        return {s.query: s.key for s in rows}, identity_of(built_stores, resolved)

    before, fingerprint = keyed(panel)
    other = {"kind": "seat", "id": "other", "inner_dataset": "otherbench"}
    grown = {**panel, "tasks": [*panel["tasks"], other]}
    after, grown_fingerprint = keyed(grown)
    assert after["seed-0"] == before["seed-0"] and grown_fingerprint == fingerprint, (
        "adding an inner dataset re-keyed a banked cell"
    )
    write_yaml(
        tenant_copy / DEFINITION_FILE,
        {"pipeline": {"nodes": {"llm_only": {"config": {"model": "b"}}}}, "campaign": campaign},
    )
    edited, _ = keyed(grown)
    assert edited["seed-0"] != before["seed-0"], "an edit to a seat's dataset re-keyed nothing"
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
    hub_source = loaders.definition_source(hubcut)
    assert hub_source is not None
    hub_bank = Bank.of([Sample(id=0, query="q", ground_truth="g")])
    built_stores.tenant_datasets.record_cut(hub_source.identity, hub_bank)
    held_cut = loaders.held_bank(built_stores, hubcut)
    assert held_cut is not None and held_cut.dataset_hash == hub_bank.dataset_hash, (
        "a recorded cut was not held"
    )
    # A held bank opens without its source's extra, which only a fetch needs; one not held is
    # refused for that extra before a fetch is tried.
    write_yaml(
        built_stores.tenant_datasets.dataset_dir("hubfresh") / DEFINITION_FILE,
        {
            "source": {
                "recipe": "columns",
                "hub": "org/repo",
                "rev": "c2",
                "split": "test",
                "query": "q",
                "truth": "t",
            },
            "pipeline": {"backend_type": "termnorm"},
        },
    )
    with monkeypatch.context() as uninstalled:
        uninstalled.setattr(startability, "installed", lambda _extra: False)
        assert startability.dataset_reading(built_stores, "hubcut") == (
            startability.DatasetReading(1, None)
        ), "a held bank was refused for the extra only its fetch needs"
        assert startability.checkin_bank(built_stores, "hubcut").dataset_hash == (
            hub_bank.dataset_hash
        ), "a check-in on a held bank did not open on it"
        fresh = startability.dataset_reading(built_stores, "hubfresh")
        assert fresh.block is StartBlock.MISSING_EXTRA, "an unheld bank listed as startable"
        with pytest.raises(DatasetUnstartableError):
            startability.checkin_bank(built_stores, "hubfresh")
    assert not offline.unrouted, f"a refused fetch reached the hub: {offline.unrouted}"

    # A name reads from the first tier holding it, an account's own, then the install's, then the
    # research tier's, and is refused nowhere for a second holder; a card that is not plain title
    # and summary refuses a run and lists the dataset without one.
    install_root, research_root = tmp_path / "install", tmp_path / "research"
    termnorm = {"pipeline": {"backend_type": "termnorm"}}
    for root, name, definition in (
        (install_root, "twin", termnorm),
        (research_root, "twin", {"pipeline": {"backend_type": "promptpotter"}}),
        (research_root, "solo", termnorm),
        (install_root, "carded", {**termnorm, "card": {"title": "T", "summary": "Two  words."}}),
        (install_root, "miscarded", {**termnorm, "card": {"title": "T"}}),
    ):
        write_yaml(root / name / DEFINITION_FILE, definition)
    tiered = build_stores(
        default_identity(),
        projects_root=tmp_path / "tiered",
        install_datasets_root=install_root,
        research_datasets_root=research_root,
    )
    # An account's own written before the install shipped its name: a survivor, read as its own.
    write_yaml(tiered.tenant_datasets.dataset_dir("carded") / DEFINITION_FILE, termnorm)
    assert {n: resolve_dataset(tiered, n).tier for n in ("twin", "carded", "solo")} == {
        "twin": "install",
        "carded": "yours",
        "solo": "research",
    }, "a name was read from another tier than the first holding it"
    assert runnable_definition(resolve_dataset(tiered, "twin")).backend_type == "termnorm", (
        "a shadowed tier answered"
    )
    # Nothing a name's second holder blocks: every campaign lists, and so does every origin.
    for campaign_id, name in (("camp-twice", "carded"), ("camp-beside", "miscarded")):
        hop = CycleHop(campaign_id=campaign_id, cycle_id=f"cycle_{name}0000")
        stored_campaign(
            tiered, minted_campaign(hop, dataset_name=name, root_content_hash=f"origin-{name}")
        )
        tiered.campaigns.mint_cycle(hop)
    shadowed = entries_version([tiered])
    sidebar = list_hierarchy(
        tiered,
        inside=(),
        dataset=None,
        lifecycle="all",
        campaign_id=None,
        attached_only=False,
        depth=0,
    )
    assert sorted(entry.label for entry in sidebar.entries) == ["carded", "miscarded"], (
        "a campaign on a shadowing name failed the sidebar, or hid its neighbour"
    )
    # A root holding no resolved run serves the refusal it raised on its row, never one sentence
    # for every refusal, so the reader learns which record to repair.
    unresolved = build_stores(default_identity(), projects_root=tmp_path / "unresolved")
    refused_root = CycleHop(campaign_id="camp-bare", cycle_id="cycle_bare00000000")
    unresolved.campaigns.create_campaign(minted_campaign(refused_root, dataset_name="carded"))
    unresolved.campaigns.mint_cycle(refused_root)
    for depth in (0, 1):
        [entry] = list_hierarchy(
            unresolved,
            inside=(),
            dataset=None,
            lifecycle="all",
            campaign_id=None,
            attached_only=False,
            depth=depth,
        ).entries
        [caveat] = [line for line in entry.lines if line.key == "unresolved"]
        assert "holds no resolved run" in caveat.label, (
            f"depth {depth}: a root's refusal was served as {caveat.label!r}, not its own reason"
        )
    assert sorted(origin.dataset_name for origin in list_origins(tiered)) == [
        "carded",
        "miscarded",
    ], "the origins survey dropped an origin whose name another tier also holds"

    # The one writer (a compile's too) refuses a name another tier holds, and lands nothing; an
    # account re-writes a name it holds in place, its survivor over the install's included.
    def write_own(name: str) -> Path:
        return tiered.write_own_definition(
            name,
            source={"recipe": "rows", "items": []},
            pipeline=termnorm["pipeline"],
            campaign={},
            description="d",
            framing=TaskDecomposition(),
            origin={},
        )

    for name in ("twin", "solo", "miscarded"):
        with pytest.raises(SlugTakenError):
            write_own(name)
        assert not tiered.tenant_datasets.dataset_dir(name).exists(), f"a write landed over {name}"
    for name in ("mine", "mine", "carded"):
        write_own(name)
        assert resolve_dataset(tiered, name).tier == "yours", f"a re-write of {name} was refused"
    for name in ("carded", "mine"):
        rmtree_robust(tiered.tenant_datasets.dataset_dir(name))
    assert resolve_dataset(tiered, "carded").tier == "install", "a name lost its next holder"
    assert entries_version([tiered]) != shadowed, (
        "the sidebar's validator missed the holder a name resolves to: a 304 serves the old one on"
    )
    # A draft names a dataset no tier holds, an account's own included, and is told a free name.
    write_yaml(research_root / "solo-2" / DEFINITION_FILE, termnorm)
    write_yaml(tiered.tenant_datasets.dataset_dir("own") / DEFINITION_FILE, termnorm)
    for slug, free in (("miscarded", "miscarded-2"), ("solo", "solo-3"), ("own", "own-2")):
        with pytest.raises(SlugTakenError) as taken:
            asyncio.run(
                ingest.ingest_draft(stores=tiered, blob=b"a,b\n1,2\n", filename="t.csv", slug=slug)
            )
        assert taken.value.suggested == free, f"an upload over {slug} was offered a held name"
    # One torn definition file, one answer, whichever door reads it.
    for read_miscarded in (
        lambda: runnable_definition(resolve_dataset(tiered, "miscarded")),
        lambda: dataset_campaign_block(resolve_dataset(tiered, "miscarded")),
    ):
        with pytest.raises(FileUnreadableError) as miscarded:
            read_miscarded()
        assert (miscarded.value.kind, miscarded.value.details["path"]) == (
            FileKind.STORED_CONFIG,
            str(install_root / "miscarded" / DEFINITION_FILE),
        ), "a malformed card ran, or one file answered two ways"
    cards = {ref.dataset.name: ref.card for ref in list_readable_datasets(tiered)}
    assert cards == {
        "own": None,
        "twin": None,
        "carded": DatasetCard("T", "Two words."),
        "miscarded": None,
    }, "the listing misread a card, or listed the research tier"
    # What the checkout ships: its install and research tiers hold no name in common, so no
    # install name is shadowed by research and no research name by the install.
    shipped = [
        {p.parent.relative_to(root).as_posix() for p in root.rglob(DEFINITION_FILE)}
        for root in (paths.install_datasets_root(), paths.research_datasets_root())
        if root is not None
    ]
    assert len(shipped) == 2 and all(shipped), "this checkout lost a tier"
    assert not set.intersection(*shipped), f"both shipped tiers hold {set.intersection(*shipped)}"
    # An unoffered name's 404 reads as an absent one's, so the answer never says which it is.
    with monkeypatch.context() as unoffered:
        unoffered.setattr(startability, "offered", lambda _stores, _name: False)
        with pytest.raises(DatasetAccessError) as hidden:
            startability.offered_dataset(tiered, "twin")
    with pytest.raises(DatasetAccessError) as absent:
        resolve_dataset(tiered, "nowhere")
    assert str(hidden.value).replace("twin", "nowhere") == str(absent.value), (
        "an unoffered name's 404 said more, or less, than an absent one's"
    )
    # `new` says the refusal's own words: on a wheel the research-tier sentence is all that tells
    # an operator where L4's panel lives.
    with monkeypatch.context() as wheel:
        wheel.setattr(paths, "research_datasets_root", lambda: None)
        wheel.setenv("OPENROUTER_API_KEY", "not-a-key")
        wheel.setattr(sys, "argv", ["promptpotter", "new", "promptpotter-self/panel"])
        capsys.readouterr()
        with pytest.raises(SystemExit):
            campaign_runner.main()
    assert "no research tier" in capsys.readouterr().err, (
        "`new` on a wheel dropped why the L4 panel is not found"
    )
    # So does an upload `new` refuses: the slug's own reason, never a parse failure in its place.
    upload = tmp_path / "rows.csv"
    upload.write_text("q,a\nx,1\n", encoding="utf-8")
    with pytest.raises(ValueError) as bad_slug:
        validate_dataset_name("bad slug!")
    with monkeypatch.context() as cli:
        cli.setenv("OPENROUTER_API_KEY", "not-a-key")
        cli.setattr(sys, "argv", ["promptpotter", "new", str(upload), "--slug", "Bad Slug!"])
        capsys.readouterr()
        with pytest.raises(SystemExit):
            campaign_runner.main()
    assert f"Error: {bad_slug.value}" in capsys.readouterr().err.splitlines(), (
        "`new` reworded an upload's refusal"
    )
    # A check-in renamed onto a name the install holds is refused, in the claim's own words, and
    # told its draft is kept.
    with monkeypatch.context() as cli:
        cli.setenv("OPENROUTER_API_KEY", "not-a-key")
        cli.setattr(
            sys, "argv", ["promptpotter", "new", str(upload), "--set", "slug=email-tagging"]
        )
        capsys.readouterr()
        with pytest.raises(SystemExit):
            campaign_runner.main()
    said = capsys.readouterr().err
    assert said.startswith(
        "Error: A dataset named 'email-tagging' already exists. Suggested slug: email-tagging-2."
    ), f"a check-in took an install name, or `new` reworded the claim: {said!r}"
    assert "discard-draft" in said, "`new` dropped that the refused check-in is kept"

    # Every file the store refuses whole is refused as what it IS, by its path, through the door
    # that serves it: its code, its status and the remedy that fits it.
    beside = CycleHop(campaign_id="camp-beside", cycle_id="cycle_miscarded0000")
    held_bank = Bank.of([Sample(id=0, query="q", ground_truth="g")])
    tiered.tenant_datasets.put_bank(held_bank)
    write_yaml(install_root / "badconfig" / DEFINITION_FILE, termnorm)
    config_file = install_root / "badconfig" / DEFINITION_FILE
    grants_file, blocks_file = tmp_path / "grants.json", tmp_path / "blocklist.json"
    draft_file = tiered.drafts.draft_dir("torn") / DRAFT_FILE
    draft_file.parent.mkdir(parents=True)
    store = ("file_unreadable", 409, "No reader repairs it.")
    stored_config = ("stored_config_invalid", 500, "Fix the file.")
    manifest_file = CampaignLayout(tiered.campaigns.campaign_root_dir("camp-beside")).manifest
    manifest = json.loads(manifest_file.read_bytes())
    manifest["declaration"]["loop"]["no_such_knob"] = 1
    checkin_file = paths.user_data_root() / "checkin" / "pipeline.yaml"

    def beside_config() -> CycleConfig:
        campaign = tiered.campaigns.load_campaign("camp-beside")
        assert campaign is not None
        return cycle_config(tiered, campaign, None)

    shipped_checkin = (paths.checkin_assets_root() / "pipeline.yaml").read_text(encoding="utf-8")
    for torn_file, torn, door, (code, status, remedy) in (
        (
            tiered.campaigns.layout(beside).export,
            b"{not an export",
            lambda: tiered.campaigns.read_export(beside),
            ("export_unreadable", 409, "An export is disposable: re-run the cycle to export it."),
        ),
        (
            tiered.tenant_datasets._bank_path(held_bank.dataset_hash),
            b'{"items": 3}',
            lambda: tiered.tenant_datasets.bank(held_bank.dataset_hash),
            (
                "bank_unreadable",
                409,
                "No reader repairs it: delete it, and a dataset with a source re-fetches it on "
                "its next start.",
            ),
        ),
        (
            CampaignLayout(tiered.campaigns.campaign_root_dir("camp-beside")).manifest,
            b"{not a manifest",
            lambda: tiered.campaigns.load_campaign("camp-beside"),
            ("manifest_unreadable", 409, LANDING_REMEDY),
        ),
        (
            manifest_file,
            json.dumps(manifest).encode(),
            beside_config,
            ("manifest_unreadable", 409, LANDING_REMEDY),
        ),
        (
            checkin_file,
            shipped_checkin.replace(
                "{{consultation_instruction}}", "{{consultation_instruction}} {{no_such_slot}}"
            ).encode(),
            checkin_template,
            (
                "optimizer_prompt_invalid",
                500,
                "Fix its template: it fills only the slots its node declares.",
            ),
        ),
        (
            tiered.jobs_dir / "torn.json",
            b'{"job_id": 3}',
            lambda: JobRegistry.attach(tiered.jobs_dir).get("torn"),
            ("job_unreadable", 409, LANDING_REMEDY),
        ),
        (
            config_file,
            b"campaign: [unclosed",
            lambda: dataset_campaign_block(resolve_dataset(tiered, "badconfig")),
            stored_config,
        ),
        (
            config_file,
            b"campaign: [unclosed",
            lambda: campaign_file_block(config_file),
            stored_config,
        ),
        (
            config_file,
            b"campaign: 3\n",
            lambda: campaign_file_block(config_file),
            stored_config,
        ),
        (
            config_file,
            b"campaign:\n  optimization: 3\n",
            lambda: validated_campaign_config(campaign_file_block(config_file), path=config_file),
            stored_config,
        ),
        (
            blocks_file,
            b'{"emails": "ada@x.com"}',
            lambda: blocklist.block_email(
                blocks_file, "eve@x.com", actor="o", audit_path=tmp_path / "audit.jsonl"
            ),
            stored_config,
        ),
        (grants_file, b'{"grants": 3}', lambda: grants.list_grants(grants_file), stored_config),
        (draft_file, b"{not a draft", lambda: tiered.drafts.read_draft("torn"), store),
    ):
        kept = torn_file.read_bytes() if torn_file.is_file() else None
        torn_file.parent.mkdir(parents=True, exist_ok=True)
        torn_file.write_bytes(torn)
        with pytest.raises(FileUnreadableError) as unread_file:
            door()
        said_of = unread_file.value
        assert (said_of.code, said_of.http_status, said_of.details["path"]) == (
            code,
            status,
            str(torn_file),
        ), f"{torn_file.name} was refused as another file, or unnamed"
        assert str(said_of).endswith(f"). {remedy}"), f"{torn_file.name} was told another remedy"
        if kept is None:
            torn_file.unlink()
        else:
            torn_file.write_bytes(kept)
    # A potter template filling a slot its node never declares is refused at selection, by address.
    typo_overlay = {"l1_generate": {"config": {"instruction": "{{typo}}"}}}
    with pytest.raises(PotterError) as typo:
        select_optimizer(
            OptimizerChoice.model_validate({"optimizer": "potter", "nodes": typo_overlay})
        )
    assert (typo.value.code, typo.value.http_status) == (
        FileKind.OPTIMIZER_PROMPT.code,
        FileKind.OPTIMIZER_PROMPT.http_status,
    ), "a typo'd slot in a potter template was served as another refusal"
    assert "optimization.nodes.l1_generate.config.instruction" in str(typo.value), (
        "the refusal named no config address"
    )

    for recipe, imported, why in (
        ("table", {loaders, tabular_ingest}, "a recipe's identity missed code it imports"),
        ("columns", {loaders}, "a recipe's identity took code it does not run"),
    ):
        assert set(loaders.recipe_modules(recipe)) == imported, why

    # A derived read is keyed by its file's signature, and an absent file's `None` is one like any
    # other: its compute's absent answer, then the written file's.
    probe = tmp_path / "derived_probe.json"

    def sized() -> int:
        return len(probe.read_bytes()) if probe.exists() else 0

    assert derived(("probe", probe), sig=file_sig(probe), compute=sized) == 0, (
        "an absent file answered no value"
    )
    probe.write_bytes(b"abc")
    assert derived(("probe", probe), sig=file_sig(probe), compute=sized) == 3, (
        "a written file kept its absent answer"
    )


# 2. The config a campaign runs


@pytest.mark.usefixtures("optimizing_memory")
def test_a_campaign_runs_the_config_its_check_in_froze_and_its_forks_narrowed(
    tmp_path: Path,
    built_stores: Stores,
    offline: Router,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The draft's CHAIN reaches the mint on a reused dataset.
    pinned = {
        "per_sample": "label_match(predicted, ground_truth)",
        "per_cell": "fitness * (0.92 + 0.08 * 120.0 / max(120.0, tokens))",
    }
    root = built_stores.tenant_datasets.dataset_dir("ds")
    optimization = dict(campaign_defaults()["optimization"])
    schema = pipeline_schema(
        [
            PipelineNode(name=n, kind=NodeKind.LLM, tunes_llm=False)
            for n in ("web_search", "rerank", "llm_only")
        ]
    )
    write_yaml(
        root / DEFINITION_FILE,
        {
            "pipeline": {
                "backend_type": upload_connector().name,
                "pipelines": dict(schema.pipelines),
            },
            "campaign": {"optimization": optimization, "scoring": pinned},
        },
    )
    draft = new_draft(
        slug="ds",
        basis=DatasetBasis(dataset="ds"),
        n_samples=1,
        sample_preview=[],
        headers=[],
    ).patch(config={"optimization": {"optimizer": "capo"}})
    picked = draft.patch(config={**draft.config, "exclude_nodes": ["rerank", "web_search"]})
    chosen = draft_config(built_stores, picked)
    assert running_only(schema, exclude=chosen.exclude_nodes).running == ("llm_only",), (
        "the excluded complement runs another chain than was picked"
    )
    off_chain = schema.model_copy(update={"pipelines": {"default": ["web_search", "llm_only"]}})
    assert running_only(off_chain, exclude=["web_search"]).running == ("llm_only", "rerank"), (
        "an exclusion dropped a node the graph fires off every walk"
    )
    alternative = off_chain.model_copy(
        update={"pipelines": {**off_chain.pipelines, "rerank_only": ["rerank"]}}
    )
    assert running_only(alternative, exclude=[]).running == ("web_search", "llm_only"), (
        "a node only on an alternative walk ran beside the chain"
    )
    # L4's outer graph: the escalation nodes walk nowhere, LEVI's seed walks its phase.
    l4_graph = running_only(
        parse_pipeline_response(inner_cells("potter", "levi").pipeline()), exclude=[]
    )
    escalations = {"l2_context", "l3_plan", "levi_seed"}
    assert escalations <= set(l4_graph.node_config_schema(steer_bound=False)), (
        "an outer node the inner run fires is no lever"
    )
    # An upload searches the `model` axis TermNorm serves, bounded by its connector's menu.
    uploads = upload_connector().uploads
    assert uploads is not None
    served_termnorm = ServedPipeline(
        "termnorm", {"nodes": {"llm_only": LLM_ONLY_NODE}, "pipelines": {"llm_only": ["llm_only"]}}
    )
    upload_graph = parse_pipeline_response(
        served_termnorm.over(
            with_node_templates(
                upload_pipeline_json(UploadBasis(connector="termnorm", kept="x.csv"), {})
            )
        )
    )
    answering = upload_graph.get_node("llm_only")
    assert answering is not None and "model" in answering.param_keys, (
        "an upload closed the model axis its backend serves"
    )
    assert upload_graph.model_options(answering) == list(uploads.available_models), (
        "the upload's menu does not bound the model axis L1 searches"
    )
    assert chosen.optimization.optimizer == "capo", "the draft's optimizer did not reach the mint"
    assert chosen.scoring == pinned, "a pinned criterion lost its anchors under another edit"
    assert draft_config(built_stores, draft).exclude_nodes == [], (
        "an untouched chain excluded a node"
    )

    def planned(on: DraftFile, **patch: Any) -> CampaignConfig:
        plan = asyncio.run(plan_draft_patch(built_stores, on, EditDraftPatch.model_validate(patch)))
        return draft_config(built_stores, apply_draft_patch(on, plan))

    # A check-in picks the criterion as the server reads it; sent back, a pinned formula's own
    # dials move nothing, and a matcher pick keeps the composite.
    assert draft_criterion(
        chosen.scoring, campaign_terms(upload_connector().name)
    ) == DraftCriterion(matcher="label_match", dials="tokens=0.08"), (
        "the served criterion is not the pinned formula's"
    )
    assert planned(draft, config={"scoring": {"dials": "tokens=0.08"}}).scoring == pinned, (
        "a pinned formula's anchors did not survive a round trip"
    )
    assert planned(draft, config={"scoring": {"matcher": "em_match"}}).scoring == {
        **pinned,
        "per_sample": "em_match(predicted, ground_truth)",
    }, "a matcher pick dropped the composite"
    dials = {"per_sample": pinned["per_sample"], "dials": "cost=0.1"}
    assert planned(draft, config={"scoring": {"dials": "cost=0.1"}}).scoring == dials, (
        "dials froze another criterion than they spell"
    )
    # A knob edit on the basis's optimizer, then a switch: the knob addressed the old one.
    write_yaml(
        built_stores.tenant_datasets.dataset_dir("cap") / DEFINITION_FILE,
        {
            "pipeline": {
                "backend_type": upload_connector().name,
                "pipelines": dict(schema.pipelines),
            },
            "campaign": {"optimization": {**optimization, "optimizer": "capo"}, "scoring": pinned},
        },
    )
    on_capo = new_draft(
        slug="cap",
        basis=DatasetBasis(dataset="cap"),
        n_samples=1,
        sample_preview=[],
        headers=[],
    )
    capo_knob = EditDraftPatch.model_validate(
        {"config": {"optimization": {"nodes": {"blocks": {"config": {"block_size": 20}}}}}}
    )
    knobbed = apply_draft_patch(
        on_capo, asyncio.run(plan_draft_patch(built_stores, on_capo, capo_knob))
    )
    switched = planned(knobbed, config={"optimization": {"optimizer": "potter"}}).optimization
    assert switched.optimizer == "potter" and "blocks" not in switched.nodes, (
        "a switch kept the old optimizer's knobs"
    )
    # A draft edit sent as rows freezes the search space the overlay spelled. A REUSED dataset:
    # its file is not the check-in's to write, so the overlay rides the config.
    reused = built_stores.tenant_datasets.dataset_dir("d")
    declared_node = {
        "type": "llm",
        "config": {"model": "m", "reasoning_effort": "low", "temperature": 0.2},
        "optimizer": {
            "param_keys": ["reasoning_effort", "temperature"],
            "param_allowed_values": {"reasoning_effort": ["low", "mid", "high"]},
        },
    }
    write_yaml(
        reused / DEFINITION_FILE,
        {
            "pipeline": {"backend_type": "termnorm", "nodes": {"llm_only": declared_node}},
            "campaign": _connector_config().model_dump(mode="json"),
        },
    )
    edited = new_draft(
        slug="d",
        basis=DatasetBasis(dataset="d"),
        n_samples=1,
        sample_preview=[],
        headers=[],
    )

    def frozen(**patch: Any) -> Any:
        plan = asyncio.run(
            plan_draft_patch(built_stores, edited, EditDraftPatch.model_validate(patch))
        )
        return draft_pipeline(built_stores, apply_draft_patch(edited, plan), plan.served).config

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
    as_overlay = frozen(config={"optimizer_narrowing": {"llm_only": spelled}})
    assert as_rows == as_overlay.optimizer_narrowing, "rows and the overlay froze two spaces"
    # No field named `answer`, none named by the operator: the slot is the LAST, reasoning first.
    answer_schema = {"type": "object", "properties": {"reasoning": {}, "code": {}}}
    authored = frozen(
        node_output={"node": "llm_only", "output_schema": answer_schema}
    ).pipeline_overlay
    contract = {
        "output_schema": answer_schema,
        "answer_fields": ["code"],
        "response_format": "json",
    }
    assert (
        authored == frozen(config={"pipeline_overlay": {"llm_only": contract}}).pipeline_overlay
    ), "an authored output froze another contract than the overlay spells"
    assert authored["llm_only"].laid_keys()["answer_fields"] == ["code"], (
        "the answer slot is not the last field"
    )
    # A reused dataset's own contract is authored already: editing it reopens no axis.
    contracted = {"model": "m", "output_schema": answer_schema, "answer_fields": ["reasoning"]}
    write_yaml(
        built_stores.tenant_datasets.dataset_dir("dj") / DEFINITION_FILE,
        {
            "pipeline": {
                "backend_type": "termnorm",
                "nodes": {"llm_only": {**declared_node, "config": contracted}},
            },
            "campaign": _connector_config().model_dump(mode="json"),
        },
    )
    reused_contract = new_draft(
        slug="dj",
        basis=DatasetBasis(dataset="dj"),
        n_samples=1,
        sample_preview=[],
        headers=[],
    )
    contract_edit = EditDraftPatch.model_validate(
        {"node_output": {"node": "llm_only", "output_schema": answer_schema}}
    )
    edited_node = asyncio.run(
        plan_draft_patch(built_stores, reused_contract, contract_edit)
    ).changes["config"]["pipeline_overlay"]["llm_only"]
    assert "response_format" not in edited_node and edited_node["answer_fields"] == ["reasoning"], (
        "a reused dataset's declared contract read as unauthored"
    )
    # A check-in names only a connector that runs its upload; a container one is never chosen there.
    for foreign in ("dbllmbench", "harbor", "dspy", "no-such"):
        with pytest.raises(PayloadInvalidError):
            frozen(connector=foreign)

    # An upload's draft splits once: its node edit is the definition's, which its Start writes, so
    # the campaign it mints declares none, as one named off that definition does not.
    landed = asyncio.run(
        ingest.ingest_draft(stores=built_stores, blob=b"q,a\nx,1\ny,2\nz,1\n", filename="up.csv")
    )
    upload = owned_draft(built_stores, landed.draft_id).patch(
        raw_task_description="Map q to a.", column_query="q", column_ground_truth="a"
    )
    # The node edit is the definition's half; the halt edit is the campaign's, which a later
    # campaign on the uploaded dataset starts from, as a shipped dataset's `campaign` block.
    upload_knob = {
        "config": {
            "pipeline_overlay": {"llm_only": {"temperature": 0.3}},
            "optimization": {"halt_at_fitness": 0.9},
        }
    }
    knob_plan = asyncio.run(
        plan_draft_patch(built_stores, upload, EditDraftPatch.model_validate(upload_knob))
    )
    knobbed = apply_draft_patch(upload, knob_plan)
    upload_laid = draft_pipeline(built_stores, knobbed, knob_plan.served)
    assert "pipeline_overlay" not in upload_laid.config.model_dump(exclude_defaults=True), (
        "an upload's check-in declares its definition's half into the campaign"
    )
    temperature = next(r for r in upload_laid.rows["llm_only"] if r.key == "temperature")
    assert (temperature.value, temperature.source) == (0.3, "dataset"), (
        "an upload's node edit reads as another layer than the definition its Start writes"
    )
    assert isinstance(knobbed.basis, UploadBasis)
    commit_upload_definition(
        built_stores,
        knobbed,
        source=upload_source(knobbed, knobbed.basis),
        pipeline=upload_laid,
    )
    named = asyncio.run(ingest.draft_from_dataset(stores=built_stores, dataset_name=knobbed.slug))
    by_name = owned_draft(built_stores, named.draft_id)
    again = draft_pipeline(built_stores, by_name, asyncio.run(draft_served(built_stores, by_name)))
    assert (again.config, again.params) == (upload_laid.config, upload_laid.params), (
        "a dataset named off an upload's Start declares or runs another config than its check-in"
    )
    # A resolver's model proposal is the one edit at its node's config path, applied as any edit.
    menu = next(r for r in upload_laid.rows["llm_only"] if r.key == "model").options
    model_found = OriginFinding(
        field="node.llm_only.model", proposed_value=menu[-1], confidence="high", evidence="row 1"
    )
    (proposal,) = raised_commands(
        knobbed, CheckinOutput(task_intent="map", answer_format="a label", findings=[model_found])
    )
    proposed = asyncio.run(plan_draft_patch(built_stores, knobbed, proposal.patch))
    assert (
        draft_pipeline(built_stores, apply_draft_patch(knobbed, proposed), proposed.served)
        .params.nodes["llm_only"]
        .model
        == menu[-1]
    ), "a resolver's model proposal was dropped"

    # A menu offers no model its Start refuses, and offers it again once a host prices it: the
    # API's sweep re-asks an unpriced route, whichever process answered it.
    def menu_now() -> list[str]:
        laid = draft_pipeline(built_stores, knobbed, knob_plan.served)
        return next(r for r in laid.rows["llm_only"] if r.key == "model").options

    unhosted = menu[-1]
    with monkeypatch.context() as aged:
        # Every answer reads as past its time, so each read is the sweep's next tick.
        aged.setattr(pricing, "_TTL_SECONDS", 0.0)
        aged.setattr(pricing, "_OUTAGE_MEMO_S", 0.0)
        pricing.refresh_route_answers()
        offline.hosts[unhosted] = {"data": {"endpoints": []}}
        assert pricing.route_priced(unhosted, "openrouter") is False
        assert unhosted not in menu_now(), "a menu offered a model its Start refuses"
        del offline.hosts[unhosted]
        pricing.refresh_route_answers()
        assert unhosted in menu_now(), "a model a host prices again stayed off every menu"
    # ...and a route no host ever prices, a misspelt model, is re-asked for one day of its answer,
    # never for good: a re-fetch answering the same keeps when it first answered.
    misspelt = "vendor/misspelt-model"
    offline.hosts[misspelt] = {"data": {"endpoints": []}}
    clock = [time.time()]
    with monkeypatch.context() as days:
        days.setattr(time, "time", lambda: clock[0])
        assert pricing.route_priced(misspelt, "openrouter") is False
        for _ in range(2):
            clock[0] += pricing._TTL_SECONDS / 2
            pricing.refresh_route_answers()
        assert pricing.remembered_priced(misspelt, "openrouter") is None, (
            "the sweep kept re-asking a route no host prices past a day of its answer"
        )
    # ...and only as that edit: a node's model moves spend, so the turn raises it and applies none.
    answer = CheckinOutput(task_intent="map", answer_format="a label", findings=[model_found])

    async def answered(**_: object) -> tuple[CheckinOutput, int]:
        return answer, 0

    monkeypatch.setattr(origin_resolve, "run_checkin", answered)
    monkeypatch.setattr(origin_resolve, "paid_call", lambda **_: contextlib.nullcontext())
    turn = asyncio.run(
        origin_resolve.resolve_origin_turn(stores=built_stores, draft=knobbed, pipeline=upload_laid)
    )
    assert turn.draft.config == knobbed.config, "a resolver turn applied a node's model unpriced"
    assert [c.payload.patch for c in turn.resolution.raised] == [proposal.patch], (
        "a resolver turn dropped the model edit it may not apply"
    )
    # An empty answer buys a turn only where one is owed, decided by the server at every door: a
    # draft holding no task and authored prompts buys none from the browser's empty Send or from a
    # `new <file>` naming no task, and only the operator's re-run buys one over it.
    turns: list[object] = []

    async def counted(**_: object) -> tuple[CheckinOutput, int]:
        turns.append(None)
        return answer, 0

    monkeypatch.setattr(origin_resolve, "run_checkin", counted)
    authored = owned_draft(built_stores, landed.draft_id).patch(
        column_query="q", column_ground_truth="a", origin_prompts={"llm_only": {"persona": "Map."}}
    )
    built_stores.drafts.write_draft(authored)
    with pytest.raises(ConflictError) as unowed:
        asyncio.run(
            CommandDispatcher(built_stores).dispatch(
                CommandCall(ResolveOriginPayload(draft_id=authored.draft_id))
            )
        )
    assert unowed.value.code == origin_resolving.RESOLVER_NOT_OWED
    with pytest.raises(OriginIncompleteError):
        asyncio.run(
            cli_new._hold_checkin(argparse.Namespace(), built_stores, authored.draft_id, None)
        )
    assert not turns, "an empty answer bought a turn the draft does not owe"
    asyncio.run(
        CommandDispatcher(built_stores).dispatch(
            CommandCall(ResolveOriginPayload(draft_id=authored.draft_id, rerun=True))
        )
    )
    assert len(turns) == 1, "the operator's re-run bought no turn"
    # An edit lays over the draft's config depth first, keeping the node's other knobs; a scoring
    # edit names only what one takes, and its criterion compiles before it is kept.
    capped = asyncio.run(
        plan_draft_patch(
            built_stores,
            knobbed,
            EditDraftPatch.model_validate(
                {"config": {"pipeline_overlay": {"llm_only": {"max_tokens": 64}}}}
            ),
        )
    )
    assert capped.changes["config"]["pipeline_overlay"]["llm_only"] == {
        "temperature": 0.3,
        "max_tokens": 64,
    }, "an edit to one knob clobbered the node's other"
    for malformed in ({"weights": "tokens=1"}, {"per_cell": "correct +"}):
        with pytest.raises((PayloadInvalidError, ScoringFormulaError)):
            asyncio.run(
                plan_draft_patch(
                    built_stores,
                    knobbed,
                    EditDraftPatch.model_validate({"config": {"scoring": malformed}}),
                )
            )

    # A row reads as the search space its ticks and padlock drew.
    params = [
        NodeConfigParam(
            key="model",
            kind="model",
            options=["a", "b"],
            permitted=["a"],
            permitted_stated=True,
            pickable="any",
        ),
        NodeConfigParam(key="effort", kind="enum", options=["low", "mid", "high"], pickable="any"),
        NodeConfigParam(key="temperature", kind="number", pickable="any"),
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
        [NodeConfigParam(key="persona", kind="prompt", pickable="any")],
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
    )
    stores.campaigns.create_campaign(campaign)
    stores.campaigns.mint_cycle(campaign.root_hop)
    ran = cycle_config(stores, campaign, campaign.root_hop).config
    assert (ran.optimization.optimizer, ran.optimization.budget.rounds) == ("potter", 12), (
        "a campaign ran its dataset file's later config instead of the one it froze"
    )
    ledger = CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(campaign.root_hop)))
    ledger.append(ScoringLockedRecord(declared={"dials": "other"}, locked={"per_cell": "0"}))
    assert cycle_config(stores, campaign, campaign.root_hop).config.scoring == dials, (
        "a lock declared off other dials governed this campaign"
    )
    locked = {"per_sample": "exact", "per_cell": "correct - 0.1 * cost"}
    ledger.append(ScoringLockedRecord(declared=dials, locked=locked))
    for hop in (None, campaign.root_hop):
        assert cycle_config(stores, campaign, hop).config.scoring == locked, (
            f"{hop}: the criterion its origin locked rides the root's ledger, and every cycle "
            "runs under it"
        )
    seed = CycleSeed(
        optimizer_narrowing={"a": NodeSearchNarrowing(param_keys=[])},
        config_overrides=ConfigOverrides(budget=Budget(rounds=4)),
    )
    stores.campaigns.append(campaign.root_hop, CycleSeedRecord(seed=seed))
    forked = cycle_config(stores, campaign, campaign.root_hop).config
    assert forked.optimization.budget.rounds == 4, "a seed's override did not reach its cycle"
    assert [forked.optimizer_narrowing[n].param_keys for n in "ab"] == [[], ["k"]], (
        "a seed's narrowing replaced the nodes it did not name"
    )
    # A steer lays over the campaign's narrowing by part: its closed axes stay closed, its held
    # fields held, and the steered model joins the menu without sanctioning the steer.
    probe = _connector_config(
        optimizer_narrowing={
            "solve": {
                "param_keys": [],
                "prompt_fields_held": ["instruction"],
                "param_allowed_values": {"model": ["a", "b"]},
            }
        }
    )
    steering = declare_steered_values(
        CycleSeed(pipeline_overlay={"solve": NodeCall(model="x")}), probe.optimizer_narrowing
    )
    laid = CycleConfig.laid(probe, [LaidSpan(seed=steering, scoring_lock=None)])
    assert laid.config.optimizer_narrowing["solve"] == NodeSearchNarrowing(
        param_keys=[],
        prompt_fields_held=["instruction"],
        param_allowed_values={"model": ["x", "a", "b"]},
    ), "a steer reopened an axis, released a held field or dropped a permitted model"
    assert laid.steers_outside(probe.optimizer_narrowing), (
        "a seed widening its own menu sanctioned the steer the campaign never did"
    )
    # An onward seed lays by part over its parent's laid narrowing: a subset of a closed axis
    # stays closed, and a held field joins the ones its line held.
    onward = CycleSeed(
        config_overrides=ConfigOverrides(budget=Budget(rounds=2)),
        optimizer_narrowing={
            "solve": NodeSearchNarrowing(param_keys=["model"], prompt_fields_held=["persona"])
        },
    )
    grand = CycleConfig.laid(
        probe, [LaidSpan(seed=s, scoring_lock=None) for s in (steering, onward)]
    )
    assert (grand.seed_overlay, grand.steers_outside(probe.optimizer_narrowing)) == (
        laid.seed_overlay,
        True,
    ), "a fork of a steered fork ran without its parent's steer"
    onward_laid = grand.config.optimizer_narrowing["solve"]
    assert (onward_laid.param_keys, onward_laid.prompt_fields_held) == (
        [],
        ["instruction", "persona"],
    ), "an onward seed reopened its parent's closed axis or released a field its line held"

    # A fork closes the axis its operator closed on the forked cycle's own menu: ticks equal to
    # the ROOT's whole menu still close the rung only the fork's menu carries.
    def declaring(rungs: list[str]) -> dict[str, Any]:
        node = {
            "type": "llm",
            "config": {"model": "m", "reasoning_effort": "low", "temperature": 0.0},
            "optimizer": {
                "param_keys": ["reasoning_effort", "temperature"],
                "param_allowed_values": {"reasoning_effort": rungs, "model": ["m", "n"]},
            },
        }
        return {"nodes": {"a": node}, "pipelines": {"default": ["a"]}}

    write_yaml(
        stores.tenant_datasets.dataset_dir("fk") / DEFINITION_FILE,
        {"pipeline": {"backend_type": "termnorm"}},
    )
    steered = minted_campaign(
        CycleHop(campaign_id="fk__000001", cycle_id="cycle_root"),
        config=_connector_config(),
        dataset_name="fk",
    )
    stores.campaigns.create_campaign(steered)
    fork = CycleHop(campaign_id=steered.campaign_id, cycle_id="cycle_fork")
    for hop, rungs in ((steered.root_hop, ["low", "high"]), (fork, ["low", "high", "xhigh"])):
        stores.campaigns.mint_cycle(hop)
        stores.campaigns.write_resolved_run(
            hop,
            PipelineRead(declaration=declaring(rungs), dataset_overlay={}),
            experiment=None,
            model_capabilities={},
        )
    fork_seed = parse_cycle_seed(
        ForkSeed.model_validate({"node_narrowing": {"a": rows}}), stores, steered, fork
    )
    assert fork_seed.optimizer_narrowing == {
        "a": NodeSearchNarrowing(
            param_keys=["reasoning_effort"],
            param_allowed_values={"reasoning_effort": ["low", "high"]},
        )
    }, "the fork left open a rung its operator closed"
    # A seed never steers outside the menu it states itself.
    with pytest.raises(PayloadInvalidError):
        parse_cycle_seed(
            ForkSeed.model_validate(
                {
                    "node_narrowing": {"a": rows},
                    "pipeline_overlay": {"a": {"reasoning_effort": "xhigh"}},
                }
            ),
            stores,
            steered,
            fork,
        )
    # A steer off its engine key's type is refused typed at the door, never minted.
    with pytest.raises(PayloadInvalidError, match="temperature"):
        parse_cycle_seed(
            ForkSeed.model_validate({"pipeline_overlay": {"a": {"temperature": '"warm"'}}}),
            stores,
            steered,
            fork,
        )
    # A node permits what its recorded schema serves under the line, so the dataset's optimizer
    # block sanctions a model the campaign's own (empty) layer never names.
    assert fork_preview(stores, steered.campaign_id, {"a": {"model": "n"}}) == ForkPreviewResponse(
        steer="clean", permitted={"a": ["m", "n"]}
    ), "a steer inside the dataset's model list was graded the babysit act"
    assert fork_preview(stores, steered.campaign_id, {"a": {"model": "z"}}).steer != "clean", (
        "a model no layer permits forked clean"
    )
    at_fork = permitted_narrowing(stores, steered, fork)["a"].param_allowed_values
    assert (at_fork["reasoning_effort"], at_fork["model"]) == (
        ["low", "high", "xhigh"],
        ["m", "n"],
    ), "a fork's steer was checked against its root's menu, not its own"
    # A served row says whether its reader may steer it past `permitted`: an account without
    # `campaign.babysit` is held there on the model, an axis stays free, and an account that may
    # babysit is held nowhere.
    unbabysat = replace(
        stores,
        identity=replace(
            stores.identity, capabilities=stores.identity.capabilities - {CAMPAIGN_BABYSIT_CAP}
        ),
    )
    for reader, held_keys in ((unbabysat, {"model"}), (stores, set())):
        served_rows = resolve_pipeline_at(reader, steered.campaign_id, "").node_config_schema
        row_at = {row.key: row for row in served_rows["a"]}
        assert {k for k, row in row_at.items() if row.pickable != "any"} == held_keys, (
            "a served row let its reader steer past what babysit binds, or held one it does not"
        )
        assert row_at["model"].permitted == ["m", "n"] and row_at["temperature"].pickable == "any"
    # An optimizer knob names the layer its mint recorded behind it, never the tier the account is
    # on now; an edit restating the dataset's value is the edit's.
    restated = {"optimization": {"nodes": {"l1_generate": {"config": {"n_variants": 3}}}}}
    on_dataset = {"optimization": {**restated["optimization"], "degradation_threshold": 0.4}}
    assert laid_config(
        stores, ConfigLayers(on_dataset, restated), connector="termnorm", schema=None
    ).knob_layers == {"l1_generate": {"n_variants": "campaign"}}, (
        "an edit restating the dataset's value read as the dataset's"
    )
    knob_config = CampaignConfig.model_validate(
        {
            "optimization": {
                "degradation_threshold": 0.05,
                "nodes": {
                    "l1_generate": {"config": {"n_variants": 3, "k_max": 4, "pool_window": 6}}
                },
            }
        }
    )
    layered = minted_campaign(
        CycleHop(campaign_id="fk__000002", cycle_id="cycle_root"),
        config=knob_config,
        dataset_name="fk",
    )
    stores.campaigns.create_campaign(layered)
    stores.campaigns.mint_cycle(layered.root_hop)
    recorded: dict[str, dict[str, KnobLayer]] = {
        "l1_generate": {"n_variants": "dataset", "k_max": "free_tier", "pool_window": "campaign"}
    }
    stores.campaigns.write_resolved_run(
        layered.root_hop,
        PipelineRead(
            declaration=declaring(["low"]),
            dataset_overlay={},
            knob_layers=recorded,
        ),
        experiment=None,
        model_capabilities={},
    )
    sources = {
        knob.path.rsplit(".", 1)[-1]: knob.source
        for group in campaign_config_map(stores, layered.campaign_id).groups
        for knob in group.knobs
        if ".l1_generate." in knob.path
    }
    assert {k: sources[k] for k in ("n_variants", "k_max", "pool_window")} == {
        "n_variants": "dataset",
        "k_max": "free_tier",
        "pool_window": "campaign",
    }, "a knob named another layer than its mint recorded behind it"
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
    # A served row is bound by the one predicate the verdict reads.
    assert [pickable(k, steer_bound=True) for k in ("model", "provider", "temperature")] == [
        "permitted",
        "none",
        "any",
    ], "a bound cost lever offered its permitted values, which the verdict refuses"

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
        pipeline_schema([solve]),
        rows_30,
        # Bound as run init binds it: to the connector the definition names.
        client=backend_client(
            stores.jobs_dir,
            name="termnorm",
            max_cells_in_flight=1,
            cancel_stops_billing=True,
            holds_own_sends=True,
        ),
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
    armed = declare(
        opened, door, purpose=Compare(arm=Arm(head_to_head_id="h", arm_key="a", replicate=1))
    )
    twin = declare(
        opened, door, purpose=Compare(arm=Arm(head_to_head_id="h", arm_key="b", replicate=1))
    )
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
    door_layers = ConfigLayers({}, door.model_dump(mode="json", exclude_unset=True))

    def arm_of(key: str, budget: Budget, *, h2h: str = "h") -> list[CyclePlan]:
        request = HeadToHeadRequest(
            head_to_head_id=h2h,
            arms=[
                ArmRequest(
                    arm_key=key,
                    optimization=OptimizationOverrides(optimizer=door.optimization.optimizer),
                )
            ],
            budget=budget,
        )
        return asyncio.run(arm_plans(arming, door_layers, request, rows_30))

    # No dollar cap: a backend holding its own sends prices no cell before a run.
    mint_arms(arm_of("a", Budget(usd=None, rounds=2)), adopting=None)
    # An arm declared over another budget never joins its head-to-head, and takes no campaign.
    standing = stores.campaigns.list_campaigns(lifecycle="all")
    with pytest.raises(ConflictError):
        arm_of("b", Budget(usd=None, rounds=3))
    assert stores.campaigns.list_campaigns(lifecycle="all") == standing, (
        "an arm refused off its head-to-head's record minted a campaign"
    )
    # Two acts on one fresh id each plan off no record: the second to mint is held to the first's
    # record, so one comparison never holds two shared parts.
    early = arm_of("x", Budget(usd=None, rounds=3), h2h="race")
    mint_arms(arm_of("y", Budget(usd=None, rounds=2), h2h="race"), adopting=None)
    declared_race = stores.campaigns.load_head_to_head("race")
    assert declared_race is not None, "a fresh id's first mint declared no record"
    with pytest.raises(ConflictError):
        mint_arms(early, adopting=None)
    assert stores.campaigns.load_head_to_head("race") == declared_race, (
        "an act that planned off no record rewrote the record another act minted under"
    )
    assert [
        campaign.declaration.shared_part()
        for campaign in stores.campaigns.list_campaigns(lifecycle="all")
        if isinstance(p := campaign.declaration.purpose, Compare)
        and p.arm.head_to_head_id == "race"
    ] == [declared_race.shared], "one comparison holds arms of two shared parts"
    # An act on an id another act holds is refused before it pays or mints, never queued behind it.
    standing = stores.campaigns.list_campaigns(lifecycle="all")
    with stores.campaigns.head_to_head_held("race"), pytest.raises(ConflictError):
        mint_arms(arm_of("z", Budget(usd=None, rounds=2), h2h="race"), adopting=None)
    assert stores.campaigns.list_campaigns(lifecycle="all") == standing, (
        "an act on a held head-to-head minted a campaign"
    )

    # A replicate is numbered at the mint by the key the evidence groups on, the treatment, across
    # every act on the id: a later act's arm on a standing treatment is its next replicate, on its
    # own seed, and arms of one replicate share theirs (common random numbers).
    def act(h2h: str, *arms: tuple[str, OptimizationOverrides]) -> dict[str, Compare]:
        request = HeadToHeadRequest(
            head_to_head_id=h2h,
            arms=[ArmRequest(arm_key=key, optimization=sent) for key, sent in arms],
            budget=Budget(usd=None, rounds=2),
        )
        minted = mint_arms(
            asyncio.run(arm_plans(arming, door_layers, request, rows_30)), adopting=None
        )
        purposes: dict[str, Compare] = {}
        for cycle in minted:
            campaign = stores.campaigns.load_campaign(cycle.hop.campaign_id)
            assert campaign is not None and isinstance(campaign.declaration.purpose, Compare)
            purposes[campaign.declaration.purpose.arm.arm_key] = campaign.declaration.purpose
        return purposes

    on_potter = OptimizationOverrides(optimizer="potter")
    reps = act("reps", ("p1", on_potter), ("c1", OptimizationOverrides(optimizer="capo")))
    reps |= act("reps", ("p2", on_potter), ("p3", on_potter))
    assert {key: purpose.arm.replicate for key, purpose in reps.items()} == {
        "p1": 1,
        "c1": 1,
        "p2": 2,
        "p3": 3,
    }, "a later act's arm on a standing treatment was numbered as its first replicate"
    seeded = DeterminismClamp(temperature=0.0, seed=7)
    seeds = {key: run_clamp(seeded, purpose) for key, purpose in reps.items()}
    assert seeds["p1"] != seeds["p2"] != seeds["p3"] != seeds["p1"], (
        "two replicates of one treatment drew one seed, so the evidence reads one draw as two"
    )
    assert seeds["p1"] == seeds["c1"], "two arms of one replicate drew apart"
    # A knob set to its default is no other treatment, so the evidence pools the two arms.
    stated = OptimizationOverrides(
        optimizer="potter", arm_gate=OptimizationConfig.model_fields["arm_gate"].default
    )
    knob = act("knob", ("k1", on_potter), ("k2", stated))
    assert [knob[key].arm.replicate for key in ("k1", "k2")] == [1, 2], (
        "an arm stating a default knob ran as replicate 1 of a treatment it shares"
    )
    # An arm sets an operator's knobs only: a dataset knob of its own is a difference no arm names.
    with pytest.raises(ValueError):
        ArmRequest.model_validate(
            {"arm_key": "d1", "optimization": {"optimizer": "potter", "degradation_threshold": 0.5}}
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
    # The seat's inner run opens that ACCOUNT definition inside its sandbox: a sandbox isolates
    # campaign state, never the account's datasets, or a tenant seat refuses every arm.
    sandboxed = spawn.sandbox_stores(
        InnerSpawnContext(
            inner_sandbox_root=tmp_path / "sandbox",
            identity=stores.identity,
            shared_root=stores.shared_root,
            install_datasets_root=stores.install_datasets_root,
            research_datasets_root=stores.research_datasets_root,
            inside=(CycleHop(campaign_id="outer__000000", cycle_id="cycle_outer"),),
            enclosing=(),
            acting=(),
            purpose=Optimize(),
            ruler=RulerReach(version=0, own=None),
        )
    )
    assert resolve_dataset(sandboxed, "decl") == resolve_dataset(stores, "decl"), (
        "an inner run resolved its seat's account dataset elsewhere than the panel that seated it"
    )
    assert sandboxed.campaigns.campaign_root_dir("c").is_relative_to(tmp_path / "sandbox"), (
        "an inner run's campaign state left its sandbox for the account's"
    )
    l4_arm = declare(
        opened,
        seat_config,
        purpose=Compare(arm=Arm(head_to_head_id="h4", arm_key="a", replicate=1)),
    )
    as_campaign = minted_campaign(
        CycleHop(campaign_id="decl__0000a4", cycle_id="cycle_l4arm"),
        config=effective_config(l4_arm),
        purpose=l4_arm.purpose,
        dataset_name="decl",
        dataset_hash=opened.bank.dataset_hash,
    )
    stores.campaigns.create_campaign(as_campaign)
    stores.campaigns.mint_cycle(as_campaign.root_hop)
    assert (
        cycle_config(stores, as_campaign, as_campaign.root_hop).config.model_dump()
        == effective_config(l4_arm).model_dump()
    ), "an arm runs another config than a campaign declared that way"
    held_campaign = minted_campaign(
        CycleHop(campaign_id="decl__000001", cycle_id=plan.cycle_id),
        config=door,
        dataset_name="decl",
        root_content_hash=plan.origin_hash,
        dataset_hash=opened.bank.dataset_hash,
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
    stores.campaigns.append(
        held_campaign.root_hop,
        partition_bank(rows_30, door.dataset_split, seed=0).record(
            dataset_hash=opened.bank.dataset_hash
        ),
    )
    opened = opened.bound(held_campaign.root_hop, purpose=Optimize())
    opened.record_resolved_run()
    resolve_resume(opened, held_campaign, rows_30)
    assert read_active_pointer(stores.base_dir) != held_campaign.root_hop, (
        "a resume moved the latest launch before its run held the cycle"
    )
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
        declare_run_wiring(
            opened, effective_config(declared), wired_ledger, campaign=on_disk, selected=running
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
        client=opened.backend_client,
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
    # A resume runs on the connector its campaign was minted on: its dataset re-pointed since is
    # refused at the terminal's door, the embedded one and the inner one alike, before any probes
    # or binds a connector.
    decl_definition = stores.tenant_datasets.dataset_dir("decl") / DEFINITION_FILE
    as_minted = {
        "pipeline": {"backend_type": "termnorm"},
        "campaign": {"optimization": {"degradation_threshold": 0.4}},
    }
    write_yaml(decl_definition, {**as_minted, "pipeline": {"backend_type": "n8n"}})
    asked = (len(offline.backend.asked), len(offline.harness.asked))
    with monkeypatch.context() as cli:
        cli.setenv("PROMPTPOTTER_HOME", str(tmp_path))
        cli.setattr(
            sys,
            "argv",
            ["promptpotter", "--json", "resume", "--campaign", held_campaign.campaign_id],
        )
        capsys.readouterr()
        with pytest.raises(SystemExit):
            campaign_runner.main()
    assert json.loads(capsys.readouterr().out)["code"] == "connector_moved", (
        "the terminal's resume ran a campaign on a connector its dataset no longer names"
    )
    with pytest.raises(CampaignRefusedError) as embedded:
        asyncio.run(
            run_campaign(held_campaign.root_hop, stores=stores, budget=Budget(), mode=RunMode())
        )
    assert embedded.value.refusal is CampaignRefusal.CONNECTOR_MOVED, (
        "the embedded resume ran a campaign on a connector its dataset no longer names"
    )
    # The inner door asks it of a continuing seat before its session opens: the cell's fault.
    seats = inner_cells("potter", dataset="decl")
    (inner_seat,) = seats.cells
    owner = held_campaign.root_hop
    spawning = InnerSpawnContext(
        inner_sandbox_root=inner_sandbox(stores, owner.campaign_id, owner.cycle_id),
        identity=stores.identity,
        shared_root=stores.shared_root,
        install_datasets_root=stores.install_datasets_root,
        research_datasets_root=None,
        inside=(owner,),
        enclosing=(),
        acting=(),
        cells=seats,
        purpose=Optimize(),
        ruler=RulerReach(version=0, own=None),
    )
    inner_hop = CycleHop(
        campaign_id=inner_campaign_id(
            seats.spec(inner_seat), seats.own(inner_seat, {}), seats.estimator
        ),
        cycle_id="cycle-0",
    )
    stored_campaign(spawn.sandbox_stores(spawning), minted_campaign(inner_hop, dataset_name="decl"))
    pin = PinnedCell(cell=inner_seat, instrument="i").pin()
    with (
        scoped(ledger=CycleEventLog(tmp_path / "outer.jsonl"), spawn=spawning),
        pytest.raises(CellUnscoreableError) as inner,
    ):
        asyncio.run(
            spawn.run_inner_cycle(
                Sample(id=0, query=inner_seat.id, ground_truth=None, source_pin=pin), {}
            )
        )
    refused_inner = inner.value.__cause__
    assert isinstance(refused_inner, CampaignRefusedError), "the inner door refused no campaign"
    assert refused_inner.refusal is CampaignRefusal.CONNECTOR_MOVED, (
        "the inner seat ran a campaign on a connector its dataset no longer names"
    )
    assert (len(offline.backend.asked), len(offline.harness.asked)) == asked, (
        "a door probed or bound a connector before it refused the moved one"
    )
    # The served bench reads the one refusal its pass's admission raises: no grade is offered.
    holder = stores.campaigns.line_holder(held_campaign.root_hop)
    bench_config = cycle_config(stores, held_campaign, holder).config.model_copy(
        update={"scoring": {"per_sample": "label_match(predicted, ground_truth)"}}
    )
    bench_read = cycle_bench(stores, held_campaign, holder, bench_config).status
    assert (bench_read.can_run, bench_read.refusal) == (False, BenchRefusal.CONNECTOR_MOVED), (
        "the served bench offers a grade on a connector its dataset no longer names"
    )
    write_yaml(decl_definition, as_minted)
    # A steering seed is its fork's declared delta, never a waiver of the root's own drift.
    steered_hop = CycleHop(campaign_id=held_campaign.campaign_id, cycle_id="cycle_steered")
    stores.campaigns.mint_cycle(steered_hop)
    partition = stores.campaigns.last(held_campaign.root_hop, PartitionRecord)
    assert partition is not None, "the held campaign banked no partition"
    stores.campaigns.append(steered_hop, partition)
    stores.campaigns.append(
        steered_hop,
        CycleSeedRecord(seed=CycleSeed(pipeline_overlay={"solve": NodeCall(temperature=0.3)})),
    )
    steered_fork = opened.bound(steered_hop, purpose=Optimize())
    steered_fork.record_resolved_run()
    resolve_resume(steered_fork, held_campaign, rows_30)
    with pytest.raises(ConfigDriftError):
        resolve_resume(steered_fork, held_campaign, rows_30[:-1])
    # The capability menu the origin hashed under is on record: a moved one is the cause named.
    floored = ModelCapability(
        model="m", provider="", reasoning_efforts=["high"], reasoning_note="", source="test"
    )
    stores.campaigns.write_resolved_run(
        held_campaign.root_hop,
        opened.pipeline_read,
        experiment=None,
        model_capabilities={"": {"m": floored}},
    )
    with pytest.raises(ConfigDriftError) as refused:
        resolve_resume(recut, held_campaign, rows_30[:-1])
    assert refused.value.details["capabilities_moved"] == ["/m"], (
        "a resume past a moved capability snapshot blamed only the dataset"
    )
    # A launch's observers hold its ledger's projections, and another holder joins the very ones
    # they hold; only while the run lives: one that drained, or whose exit unwound through
    # `run_optimization`'s `finally`, leaves neither its ledger's index nor its cycle held.
    root_dir = CycleDir(stores.campaigns.cycle_dir(held_campaign.root_hop))
    launched = effective_config(declared)
    address = {"campaign_id": opened.hop.campaign_id, "cycle_id": opened.hop.cycle_id}

    def pause(key: str) -> str:
        call = CommandCall(PauseCyclePayload(**address), key)
        return asyncio.run(CommandDispatcher(stores).dispatch(call)).accepted.command_id

    def acked(command_id: str) -> str | None:
        return stores.commands.book().status(command_id)

    # A pause filed while a live launch claims the cycle (bl1.B11), QUEUED before its STARTING
    # re-claim, or while the launch folds its ledger (bl1.B10), is that launch's to take.
    claimant = tmp_path / "claimant.lock"
    assert take(claimant)
    stores.campaigns.claim_launch(
        opened.hop, stage=RunPhase.QUEUED, job_id="claimed", claimant_lock=claimant
    )
    claimed = pause("claimed")
    stores.campaigns.append(
        opened.hop,
        LaunchClaimRecord(
            stage=RunPhase.STARTING,
            job_id="claimed",
            claimant_lock=str(claimant),
            timestamp=iso_z(datetime.now(UTC) + timedelta(minutes=1)),
        ),
    )
    folding: list[str] = []
    voids = Stores.void_inbox

    def void_after_a_pause(held: Stores, hop: CycleHop, *, before: float | None = None) -> None:
        folding.append(pause("fold"))
        voids(held, hop, before=before)

    with monkeypatch.context() as filing:
        filing.setattr(Stores, "void_inbox", void_after_a_pause)
        observers = build_run_observers(session=opened, campaign_config=launched)
    assert read_active_pointer(stores.base_dir) == opened.hop, (
        "a run held its cycle, and the latest launch names another"
    )
    release(claimant)
    assert [acked(claimed), acked(folding[0])] == ["accepted"] * 2, (
        "the launch voided a pause filed under its own claim or hold"
    )
    with LedgerIndex.of(wired_ledger.path).projecting() as joiner:
        assert joiner.add(DashboardCheckout(root_dir)) is observers.dashboard, (
            "a second holder folded a dashboard beside the run's, each writing its own file"
        )
    # A store-door append during the run lands through the run's own `index.json` writer, at once.
    stores.campaigns.record_intervention(held_campaign.root_hop, kind="steered")
    manifest = CycleLayout(root_dir).manifest
    assert LedgerIndex.of(wired_ledger.path).pinned and "steered" in manifest.read_text(), (
        "a store-door append let go of the run's projections, or left its index.json stale"
    )
    # A pause filed between the stop's declaration and the drain dies at the release (B16): only a
    # producer that stops on a pause takes it, so none reads "answered" on a run that never paused.
    declare_run_stop(
        opened, observers, StopReason.ROUNDS_BUDGET, interrupted_by=PauseCause.INTERRUPT
    )
    late = pause("late")
    observers.drain_all()
    assert not LedgerIndex.of(wired_ledger.path).pinned and not cycle_held(root_dir), (
        "a released run kept its ledger's projections or its cycle"
    )
    assert acked(late) == "voided", "a release took a pause no producer stopped on"
    # Outside a run, the store door holds its one-shot only while its line lands.
    stores.campaigns.record_intervention(held_campaign.root_hop, kind="steered")
    assert not LedgerIndex.of(wired_ledger.path).pinned, (
        "a store-door append pinned its ledger's index past its own line"
    )
    # A build that raises past its projections' checkout lets go of them too.
    with monkeypatch.context() as broken, pytest.raises(OSError, match="no observers"):
        broken.setattr(RunObservers, "__init__", Mock(side_effect=OSError("no observers")))
        build_run_observers(session=opened, campaign_config=launched)
    assert not LedgerIndex.of(wired_ledger.path).pinned and not cycle_held(root_dir), (
        "a build that raised kept its ledger's projections or its cycle"
    )
    observers = build_run_observers(session=opened, campaign_config=launched)
    # An exit on an error drains nothing: a drain's own fault would replace the error in flight.
    drained = Mock()
    monkeypatch.setattr(DashboardCheckout, "drain", drained)
    with pytest.raises(PayloadInvalidError):
        asyncio.run(
            run_optimization(
                [],
                CycleConfig.laid(launched, []),
                session=opened,
                observers=observers,
                mode=RunMode(diag=True),
                budget=HeldBudget(
                    admitted=Budget(),
                    standing=Budget(),
                    job_id="",
                    step_rounds=None,
                    share=Budget(),
                ),
                # No controller, so `--diag` refuses inside the run, past its observers' build.
                optimizer=select_optimizer(
                    OptimizationConfig(degradation_threshold=0.0, optimizer="gepa")
                ),
            )
        )
    assert not LedgerIndex.of(wired_ledger.path).pinned and not cycle_held(root_dir), (
        "a run whose exit unwound kept its ledger's projections or its cycle"
    )
    assert not drained.called, "a run that raised drained its dashboard on the way out"
    # A projection that cannot write is one stale-file warning on its ledger, never a raise into
    # the appends whose lines landed, and nothing it raised is held for a later append.

    class _Unwritable(Projection):
        def _handle_round_warning(self, record: RoundWarningRecord) -> None:  # noqa: ARG002 -- routed by its annotation
            raise OSError("disk full")

    faulty = CycleEventLog.open(CycleDir(tmp_path / "campaigns" / "c" / "cycles" / "faulty"))
    with LedgerIndex.of(faulty.path).projecting() as held:
        held.add(_Unwritable())
        offsets = [
            faulty.append(RoundWarningRecord(kind="layer_parse_failure", message=str(n)))
            for n in range(2)
        ]
    kinds = [record.kind for _, record in faulty.iter() if isinstance(record, RoundWarningRecord)]
    assert offsets == [0, 2] and kinds.count("derived_file_stale") == 1 and len(kinds) == 3, (
        "a projection's write fault unwound a landed append, or went unreported, or stormed"
    )

    # Answering in TEXT sends no contract to answer INTO.
    d234 = read_dataset_pipeline(_install("justlogic/d234"))
    assert d234 is not None
    # TermNorm serves its prompt's fields and no slot: its node template declares the slot.
    served_llm = {"type": "llm", "prompt_info": {"fields": list(PROMPT_INFO.fields)}}
    text_pipeline = ServedPipeline("termnorm", {"nodes": {"llm_only": served_llm}}).over(d234)
    text_schema = parse_pipeline_response(text_pipeline)
    base = {n.name: dict(n.current_config) for n in text_schema.declared_nodes}
    assert {"output_schema", "answer_fields"} <= base["llm_only"].keys(), (
        "the instrument declares no contract — the rest proves nothing"
    )
    assert (
        fold_output_contract(PipelineParams().with_nodes(base), text_schema).laid_nodes == base
    ), "an unmoved point must hash as it always did"
    # One door: whatever the carrier builds reads back off its dump, each node's keys in laid order.
    as_laid: dict[str, object] = {"prompt": "p", "temperature": 0.2, "model": "m"}
    for built in (
        PipelineParams().with_nodes({"solve": as_laid}).with_steps(["solve"]),
        PipelineParams.model_validate({"steps": ["solve"], "solve": as_laid}),
    ):
        assert PipelineParams.model_validate(built.dump()).dump() == built.dump()
        assert [list(cfg) for cfg in built.laid_nodes.values()] == [list(as_laid)], (
            "the wire sent a node's keys out of their laid order"
        )
    # The laid order is the wire's, never the call's identity: one call laid in two orders is one.
    relaid = dict(reversed(as_laid.items()))
    assert (NodeCall.model_validate(relaid), PipelineParams.model_validate({"solve": relaid})) == (
        NodeCall.model_validate(as_laid),
        PipelineParams.model_validate({"solve": as_laid}),
    ), "two calls with the same keys compared unequal for their laid order"
    # Equality reads the fields alone, however a call was built: a copy validates nothing.
    seeded_call = NodeCall.model_validate({"model": "m", "seed": 3})
    for one_call in (
        NodeCall.model_validate({"model": "m"}).model_copy(update={"seed": 3}),
        NodeCall(model="m", seed=3),
        NodeCall.model_validate({"seed": 3, "model": "m"}),
    ):
        assert one_call == seeded_call and one_call.model_copy(update={"seed": 4}) != seeded_call, (
            "two calls with the same fields compared unequal, or a moved field compared equal"
        )
    nulled = PipelineParams.model_validate({"solve": {"model": "m", "seed": None}})
    assert (
        nulled.laid_nodes == PipelineParams.model_validate({"solve": {"model": "m"}}).laid_nodes
    ), "a call laid `seed: null` hashed apart from one laid without it"
    for build in (PipelineParams.model_validate, PipelineParams().with_nodes):
        with pytest.raises(ValidationError):
            build({"solve": {"max_tokens": 1024.0}})
    with pytest.raises(ValidationError, match="may not be named"):
        pipeline_schema(nodes=[PipelineNode(name="steps", tunes_llm=False)])
    # No door builds a node named for a carrier field, the keyword `nodes=` included.
    reserving: tuple[Callable[[], PipelineParams], ...] = (
        lambda: PipelineParams(nodes={"solve": NodeCall.model_validate(as_laid)}),
        lambda: PipelineParams().with_nodes({"nodes": as_laid}),
        lambda: PipelineParams().with_nodes({"steps": as_laid}),
        lambda: PipelineParams.model_validate({"nodes": as_laid}),
    )
    for build_reserved in reserving:
        with pytest.raises(ValidationError):
            build_reserved()
    chose_text = copy.deepcopy(base)
    chose_text["llm_only"][SCHEMA_TOGGLE_PARAM] = ANSWER_AS_TEXT
    chose_text["llm_only"][description_key("answer")] = "IGNORED"
    folded = fold_output_contract(PipelineParams().with_nodes(chose_text), text_schema).laid_nodes[
        "llm_only"
    ]
    assert not {"output_schema", "answer_fields", description_key("answer")} & folded.keys(), (
        "a point answering in text still carries the contract it left"
    )
    assert (
        resolved_output_schemas(text_schema, PipelineParams().with_nodes(base))["llm_only"]
        is not None
    ), "a point answering in the contract was sent none"
    text_point = {**base, "llm_only": {**base["llm_only"], SCHEMA_TOGGLE_PARAM: ANSWER_AS_TEXT}}
    assert (
        resolved_output_schemas(text_schema, PipelineParams().with_nodes(text_point))["llm_only"]
        is None
    ), "a point answering in TEXT was sent a contract to answer INTO"
    joint_pipeline = copy.deepcopy(text_pipeline)
    joint_pipeline["nodes"]["llm_only"]["config"]["answer_fields"] = ["reasoning", "answer"]
    joint_schema = parse_pipeline_response(joint_pipeline)
    joint_node = joint_schema.get_node("llm_only")
    assert joint_node is not None
    assert joint_schema.param_options(joint_node, SCHEMA_TOGGLE_PARAM) == [ANSWER_AS_JSON], (
        "a node graded on several slots was offered prose, where every field misses"
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
    pp = fold_output_contract(
        PipelineParams().with_nodes({"llm_only": {**declared, amount: "Net, in CHF."}}), nested
    ).laid_nodes
    line = pp["llm_only"]["output_schema"]["properties"]["lines"]["items"]["properties"]["amount"]
    assert line["description"] == "Net, in CHF." and amount not in pp["llm_only"], (
        "a nested field's description did not fold into the contract it names"
    )
    assert "description" not in amount_items["properties"]["amount"], (
        "the fold mutated the declared schema it read: the parent individual still holds it"
    )

    # An invented param is rejected, and so is the individual a variation wrote it on.
    # A checkout resolves its research tier with no setting; a seat no committed tier holds is the
    # account's: a panel the account cannot resolve refuses at init, naming the seat.
    outer_stores = build_stores(default_identity(), projects_root=tmp_path / "outer")
    outer = resolve_dataset(outer_stores, "promptpotter-self/panel")
    assert (outer.tier, outer.tier_root) == ("research", _RESEARCH), (
        "a checkout did not resolve the L4 panel from its own research tier"
    )
    declared_panel = read_dataset_definition(outer)
    assert declared_panel is not None and declared_panel.panel is not None, "guard"
    unheld = "unheld/seed-0"
    unheld_seat = {"kind": "seat", "id": unheld, "inner_dataset": "unheld", "run_seed": 0}
    with pytest.raises(PayloadInvalidError) as unseated:
        InnerTasks.model_validate({**declared_panel.panel, "tasks": [unheld_seat]}).resolve_cells(
            outer_stores
        )
    assert unheld in str(unseated.value), (
        "a panel refused a seat its account does not hold without naming the seat"
    )
    for seated in InnerTasks.model_validate(declared_panel.panel).datasets:
        try:
            resolve_dataset(outer_stores, seated)
        except DatasetAccessError:
            write_yaml(
                outer_stores.tenant_datasets.dataset_dir(seated) / DEFINITION_FILE,
                {
                    "pipeline": {"backend_type": "termnorm"},
                    "campaign": {"optimization": {"degradation_threshold": 0.4}},
                },
            )
    outer_schema = parse_pipeline_response(
        asyncio.run(
            dataset_pipeline(
                outer_stores, outer, experiment=experiment_outside_run(outer_stores, outer)
            )
        )
    )
    for overlay, reasons in (
        ({"l1_generate": {"invented_knob": 1}}, [("l1_generate.invented_knob", "unknown_param")]),
        ({"l1_critique": {"layout": "hdr"}}, [("l1_critique.layout", "type_mismatch")]),
        ({"l1_critique": {"layout": {"instruction": ["plan"]}}}, []),
    ):
        judged = overlay_failures(overlay, outer_schema, running=PipelineParams())
        assert [(f.axis, f.reason) for f in judged] == reasons, (
            f"{overlay}: admitted a param the schema does not declare, or off its declared type"
        )
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
        writer="llm",
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
                prompt_info=NodePromptInfo(
                    fields=("instruction", "rubric"), rewrite_slot="instruction"
                ),
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
    gepa = select_optimizer(OptimizerChoice(optimizer="gepa"))
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
    capo = select_optimizer(OptimizerChoice(optimizer="capo"))
    assert optimizers.unfit_pipeline(capo, two, "termnorm") is not None, (
        "a member rewriting the one prompt node was admitted on a chain of two"
    )
    assert optimizers.unfit_pipeline(gepa, two, "termnorm") is None, (
        "GEPA was refused a chain it can walk"
    )
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
    # The child door refuses an optimizer's child the held field; the operator who held it writes it.
    both = {Locus("review", "rubric"): "x", Locus("review", "instruction"): "y"}
    held_writes, _, held_refusals = carried_writes(
        one_held, writer="llm", prompt_loci=both, overlay={}, demo=None
    )
    assert list(held_writes) == [Locus("review", "instruction")], "a held field was written"
    assert [(f.axis, f.reason) for f in held_refusals] == [("review.rubric", "forbidden_axis")], (
        "a held field's write carried no refusal"
    )
    authored, _, authored_refusals = carried_writes(
        one_held, writer="operator", prompt_loci=both, overlay={}, demo=None
    )
    assert (list(authored), authored_refusals) == (list(both), []), (
        "the operator's own arm was refused the field they held"
    )
    all_held = two.narrow(
        {
            n.name: NodeSearchNarrowing(param_keys=[], prompt_fields_held=list(n.prompt_fields))
            for n in two.declared_nodes
        }
    )
    assert optimizers.unfit_pipeline(gepa, all_held, "termnorm") is not None, (
        "a campaign leaving its optimizer nothing to edit was admitted"
    )

    # Round 0 measured each parent a reflection re-reads; the bench resolves it by id.
    def measured(*held: OptSearchPoint) -> list[RoundResult]:
        points, schema = reflected_on.cycle.points, reflected_on.cycle.session.pipeline_schema
        arms = {
            ArmKey(ind.id): arm_state(
                "parent",
                individual=ind,
                resolved_params=points.job(ind).config_params.dump(),
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
            scoped(
                optimizer=reflected_on.cycle.optimizer,
                determinism=reflected_on.cycle.config.optimization.determinism,
            ),
            scoped(book=spend_book()),
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
    # A whole-prompt rewrite lands in the slot the node declares: none, or a held one, refuses.
    unslotted = optimizers.unfit_pipeline(gepa, rubric_only, "termnorm")
    assert unslotted is not None and "'review'" in unslotted and "'termnorm'" in unslotted, (
        "a peer was admitted on a node declaring no slot, or its refusal named no node or connector"
    )
    slot_held = two.narrow({"review": NodeSearchNarrowing(prompt_fields_held=["instruction"])})
    assert optimizers.unfit_pipeline(gepa, slot_held, "termnorm") is not None, (
        "a peer was admitted to rewrite a slot its campaign holds"
    )
    with pytest.raises(ValidationError, match="rewrite_slot"):
        NodePromptInfo(fields=("rubric",), rewrite_slot="instruction")


# 3. Contamination of a scored prompt


_EXPORTED_ROWS = [
    {"query": "Café au lait?", "label": "Getränk"},
    {"query": "Smørrebrød", "label": "Essen"},
]


@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_no_held_out_row_or_foreign_text_reaches_a_scored_prompt(
    monkeypatch: pytest.MonkeyPatch, built_stores: Stores, tmp_path: Path, offline: Router
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
        Sample(id=60 + i, query=f"held {i}", ground_truth="TRUE", held_out=True) for i in range(9)
    ]
    assert extra[0].key == extra[0].model_copy(update={"held_out": False}).key, (
        "declaring a row bench-only re-keyed it"
    )
    wide = partition_bank([*bank, *extra], split, seed=0)
    assert (wide.search, wide.demo) == (part.search, part.demo), "a wider bench moved a paid cell"
    assert wide.bench == (*part.bench, *extra), "a declared bench row was not held out"
    for unsplittable, declared_split in (
        ([*bank, *extra], None),
        ([*bank, bank[0].model_copy(update={"id": 60, "held_out": True})], split),
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
    assert seated.held_out and not searched.held_out, "a held-out environment was searched"
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
    l4_bank = [*bank[:30], *(s.model_copy(update={"held_out": True}) for s in bank[30:50])]
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
    built_stores.campaigns.append(
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
    # A bank whose rows declare another field is refused by its path: read as unflagged rows, its
    # held tail is searched. The listing, a survey, lists its one dataset UNREADABLE beside the rest.
    stale = [
        {"bench_only": True, **{k: v for k, v in row.items() if k != "held_out"}}
        if "held_out" in row
        else row
        for row in drawn_bank.banked()
    ]
    stale_bank = Bank.of(Sample(id=i, query=f"stale {i}", ground_truth="T") for i in range(3))
    write_json(built_stores.tenant_datasets._bank_path(stale_bank.dataset_hash), {"items": stale})
    stale_path = str(built_stores.tenant_datasets._bank_path(stale_bank.dataset_hash))
    for read in (
        lambda: built_stores.tenant_datasets.bank(stale_bank.dataset_hash),
        lambda: built_stores.tenant_datasets.rows(stale_bank.dataset_hash, [35]),
    ):
        with pytest.raises(FileUnreadableError) as unread:
            read()
        assert (unread.value.kind, unread.value.details["path"]) == (FileKind.BANK, stale_path), (
            "a stale bank was refused unnamed"
        )
    for name, held_hash in (
        ("stalebank", stale_bank.dataset_hash),
        ("goodbank", recut.dataset_hash),
    ):
        write_yaml(
            built_stores.tenant_datasets.dataset_dir(name) / DEFINITION_FILE,
            {"pipeline": {"backend_type": "termnorm"}},
        )
        built_stores.tenant_datasets.relabel(name, held_hash)
    # A materialized panel this build cannot read is the store's one refusal too.
    write_yaml(
        built_stores.tenant_datasets.dataset_dir("brokenpanel") / DEFINITION_FILE,
        {"pipeline": {"backend_type": "promptpotter"}},
    )
    panel_file = built_stores.tenant_datasets.materialized_panel_dir("brokenpanel") / "panel.yaml"
    panel_file.parent.mkdir(parents=True)
    panel_file.write_text("cells: [unclosed", encoding="utf-8")
    listed = {entry.name: entry for entry in list_datasets(built_stores)}
    for unreadable in ("stalebank", "brokenpanel"):
        assert listed[unreadable].start_block is StartBlock.UNREADABLE, (
            f"{unreadable} listed readable"
        )
    assert (listed["goodbank"].n_samples, listed["goodbank"].start_block) == (41, None), (
        "an unreadable dataset beside it hid a readable one"
    )
    # Every other door answers the refusal as itself, by its path: never a 422 that drops it.
    stale_scope = cells_scope(
        built_stores,
        "stalebank",
        scope="dataset",
        campaign_id=None,
        cycle_id=None,
        descend=(),
        at=None,
    )
    with pytest.raises(FileUnreadableError) as unread_log:
        measurement_log(
            stale_scope,
            limit=5,
            max_unmeasured=None,
            order=None,
            candidate_id=None,
            round=None,
            status=None,
        )
    assert (unread_log.value.code, unread_log.value.details["path"]) == (
        "bank_unreadable",
        stale_path,
    ), "the dataset-scope leaderboard answered a stale bank as another refusal"
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

    cut = asyncio.run(ingest.draft_from_dataset(stores=built_stores, dataset_name="heldout"))
    draft = owned_draft(built_stores, cut.draft_id)
    served = asyncio.run(draft_served(built_stores, draft))
    content = build_origin_consultation(
        draft, draft_pipeline(built_stores, draft, served)
    ).user_content
    bench_rows = partition_bank(checkin_bank, DatasetSplit(bench=30), seed=0).bench
    assert not [s.id for s in bench_rows if f'"{s.query}"' in content], (
        "a bench row reached the resolver"
    )

    # An upload's Start asks the mint's refusals of the draft BEFORE its dataset lands, so a refused
    # Start takes no slug and its retry is refused the same way, never as a collision.
    landed = asyncio.run(
        ingest.ingest_draft(stores=built_stores, blob=b"q,a\nx,1\ny,2\nz,1\n", filename="tiny.csv")
    )
    uploaded = owned_draft(built_stores, landed.draft_id)
    starved = {"l1_generate": {"config": {"model": "deepseek/deepseek-v4-flash", "max_tokens": 64}}}
    built_stores.drafts.write_draft(
        uploaded.patch(
            column_query="q",
            column_ground_truth="a",
            config={"optimization": {"optimizer": "potter", "nodes": starved}},
        ),
    )
    monkeypatch.setattr(checkin_launch, "assert_origin_ready", lambda _draft, _pipeline: None)
    for attempt in ("first", "retry"):
        with pytest.raises(PayloadInvalidError):
            asyncio.run(
                dispatch_start_checkin(
                    built_stores,
                    CommandCall(StartCheckinPayload(draft_id=uploaded.draft_id), attempt),
                    inline=Inline(),
                )
            )
        assert not built_stores.tenant_datasets.dataset_dir(uploaded.slug).exists(), (
            f"the {attempt} refused Start committed its dataset"
        )
    # A name the install gained since the draft claimed it is refused at Start, leaving nothing
    # behind: no cut, no bank, no definition.
    write_yaml(
        built_stores.install_datasets_root / "email-tagging" / DEFINITION_FILE,
        {"pipeline": {"backend_type": "termnorm"}},
    )
    built_stores.drafts.write_draft(
        owned_draft(built_stores, uploaded.draft_id).patch(slug="email-tagging")
    )
    monkeypatch.setattr(launching, "assert_origin_ready", lambda _draft, _pipeline: None)
    held_before = built_stores.tenant_datasets.held_version("email-tagging")
    with pytest.raises(SlugTakenError):
        asyncio.run(
            dispatch_start_checkin(
                built_stores,
                CommandCall(StartCheckinPayload(draft_id=uploaded.draft_id), "onto-install"),
                inline=Inline(),
            )
        )
    assert not built_stores.tenant_datasets.dataset_dir("email-tagging").exists(), (
        "a Start landed an account's definition over the install's"
    )
    assert built_stores.tenant_datasets.held_version("email-tagging") == held_before, (
        "a refused Start recorded a cut"
    )
    opened = asyncio.run(
        checkin_launch.open_checkin(built_stores, draft.draft_id, served, overlays=[])
    )
    assert opened.session.pipeline_schema == draft_pipeline(built_stores, draft, served).declared, (
        "the run inits on another pipeline than the one its Start was checked against"
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
            admission=0.85,
        ),
        digest=RoundDigest(diagnostics=RoundDiagnostics(n_valid=0, samples=[]), critique=None),
        axes=None,
        block_access="open",
        rebase_capability=True,
        reelect_capability=True,
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
    potter = select_optimizer(OptimizerChoice(optimizer="potter"))
    generator = running_prompt("l1_generate", potter.node_config("l1_generate"), potter.manifest)
    filled = DispatchHub.fill(generator, inner, node="l1_generate")
    assert len(filled.rendered["rendered_prompt"]) >= subject_chars, (
        "the generator was handed no subject — it is rewriting text it cannot see"
    )
    refused_panels = set(filled.injections.dropped) & NODE_LAYOUTS["l1_generate"].mandatory
    assert not refused_panels, f"mandatory panel(s) refused by the budget: {sorted(refused_panels)}"
    # A panel mixing two manifests: each outer node is shown, and checked, as its own manifest's.
    capo = select_optimizer(OptimizerChoice(optimizer="capo"))
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
        PipelineParams(),
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
        Sample(id=0, query="justlogic-d234/seed-0", ground_truth=None),
        proposals[0].individual.pipeline_params,
    )
    capo_seat = jumping_clock(
        run_init_round(
            workspace(tmp_path / "inner"),
            offline,
            samples(40, "TRUE"),
            optimization=_l4_arm("capo", payload["optimizer_prompt_overrides"]),
        )
    )
    sent = (
        NodeContext(capo_seat, optimizers.NODE_MEMBERS.get("capo_mutate"))
        .read(Calls)
        .prompt()
        .compile_prompt(task_description="sort the list", instruction="Sort it.")
    )
    assert sent.endswith("Keep the rewrite under 80 words."), "the inner cell ran the parent"
