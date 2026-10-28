"""Lost or corrupted measurement.

Owns `infrastructure/store/measurement_archive.py` and `archive_queries.py`,
`application/maintenance/`, `application/bench/resume_and_fork/` and `application/origin.py`. A
replay served to the wrong content, a paid row dropped, a fork that inherits the wrong origin.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from factories import (
    PROMPT_INFO,
    arm_state,
    arm_walk,
    close_round,
    election,
    inner_sandbox,
    loop_session,
    measurement,
    measurements,
    minted_campaign,
    optimizer_state,
    pipeline_schema,
    prompted,
    samples,
    score_summary,
    seat,
    set_cycle,
    sheet,
    workspace,
)
from filelock import FileLock

from promptpotter.application import optimizers
from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.bench.difficulty import ruler_rows
from promptpotter.application.bench.node_context import NodeContext
from promptpotter.application.bench.resume_and_fork.fork_siblings import (
    fork_edge,
    mint_operator_fork,
)
from promptpotter.application.bench.resume_and_fork.replayers import replay_decisions
from promptpotter.application.campaign_config import (
    CampaignConfig,
    OptimizationConfig,
    apply_config_overrides,
)
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.cycle_reads import open_family_ray
from promptpotter.application.hierarchy import list_hierarchy
from promptpotter.application.hierarchy_subtree import (
    hierarchy_subtree,
    subtree_course,
    subtree_version,
)
from promptpotter.application.initialization import loop_start
from promptpotter.application.maintenance import restamp
from promptpotter.application.maintenance.archive_maintenance import (
    compact_measurement_archive,
    restore_measurement_archive,
)
from promptpotter.application.mask.divergence import VerdictOutcome, find_divergences
from promptpotter.application.mask.load import load_mask_record
from promptpotter.application.optimizers.nodes import (
    MeasuredRound,
    Panel,
    Proposals,
    ReadArm,
)
from promptpotter.application.optimizers.potter.election import elect_on_theta
from promptpotter.application.optimizers.potter.pobb.checks import EliminationGate
from promptpotter.application.optimizers.potter.race import PoBBRace
from promptpotter.application.origin import (
    resolve_origin_opt_search_point,
    try_inherit_fork_origin,
)
from promptpotter.application.runner import entry
from promptpotter.application.scoring import query_loop, search_point_scorer
from promptpotter.application.scoring.closed_rounds import closed_rounds
from promptpotter.application.scoring.formula.compiler import compile_scorer
from promptpotter.application.scoring.measurement_log import open_cell
from promptpotter.application.scoring.search_point_scorer import replayable_on
from promptpotter.application.served_dashboard import served_dashboard
from promptpotter.config.paths import MEASUREMENTS_DIR
from promptpotter.domain.bench import DatasetSplit, partition_bank
from promptpotter.domain.command_kinds import DeleteCampaignPayload
from promptpotter.domain.cycle_paths import (
    CycleDir,
    CycleHop,
    candidate_address,
    hierarchy_address,
)
from promptpotter.domain.hierarchy import (
    HierarchyCandidate,
    HierarchyRound,
    SubtreeCampaign,
    SubtreeCourse,
    entries_under,
)
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.optimizer_state import UnregisteredStatePartError
from promptpotter.domain.paired_reading import READING_STATE_INFO, ArmKey, ReadingState, RoundArm
from promptpotter.domain.phases import PauseCause, StopReason
from promptpotter.domain.pipeline_schema import PipelineNode
from promptpotter.domain.results import (
    ArmSlot,
    OverlapReading,
    PointContext,
    RoundResult,
    line_reference,
    walked_slot,
)
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.run_records import (
    AnnouncedArms,
    ArmRecord,
    ConfigOverrides,
    CycleSeed,
    ForkSpec,
    ForkTrigger,
    OriginSource,
    RebaseRequest,
    ResumeCheckpointRecord,
    RoundEnteredRecord,
    RunPhaseRecord,
    SpawnedBy,
)
from promptpotter.domain.sample import ArchiveEntry, Bank, Sample, sample_key
from promptpotter.domain.scoring import GRADE_KEYS, MeasuredCell
from promptpotter.domain.search_point import JobSearchPoint, TaskDecomposition
from promptpotter.infrastructure import producer_lock
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.telemetry import reset_cycle_ledger, set_cycle_ledger
from promptpotter.infrastructure.store import archive_queries, measurement_archive
from promptpotter.infrastructure.store.archive_queries import (
    ArmOf,
    CampaignWalks,
    SampleFoldRow,
    bench_reads,
    bind_memory_scope,
    list_populations,
    load_population,
    sample_fold_rows,
    write_sample_fold,
)
from promptpotter.infrastructure.store.dataset_access import DEFINITION_FILE
from promptpotter.infrastructure.store.io import rewrite_lines
from promptpotter.infrastructure.store.layout import CycleLayout, campaigns_root_dir_for
from promptpotter.infrastructure.store.measurement_archive import (
    MeasurementArchive,
    ReplayFeed,
    config_key,
    standing,
)
from promptpotter.infrastructure.store.read_model import append_row
from promptpotter.infrastructure.store.stores import Stores, inner_sandbox_store
from promptpotter.shared.errors import LedgerUnreadableError, PayloadInvalidError
from promptpotter.shared.identity import SYSTEM_PRINCIPAL
from promptpotter.shared.measurement_context import MeasuredPass, MeasurementRole

Configs = list[tuple[str, dict[str, Any]]]
_LLM_ONLY: Configs = [("llm_only", {"model": "X"})]
_CAMPAIGN = "testds__20260101-000000"


def _entry(node_configs: Configs, dataset_hash: str) -> ArchiveEntry:
    return ArchiveEntry(
        config_key=config_key(node_configs),
        prompt_fields_id="pf_x",
        node_configs=node_configs,
        pipeline_params={},
        dataset_hash=dataset_hash,
    )


def _row(
    query: str, *, predicted: str = "p", hit: bool = True, terminal: str = "llm_only"
) -> dict[str, Any]:
    return {
        "query": query,
        "ground_truth": "g",
        "predicted": predicted,
        "hit": hit,
        "fitness": 1.0 if hit else 0.0,
        "pipeline_data": {"terminal_node": terminal},
    }


def _file(
    archive: MeasurementArchive,
    rows: list[dict[str, Any]],
    *,
    dataset_hash: str,
    node_configs: Configs = _LLM_ONLY,
    provenance: str = "A",
    created_at: str = "2026-05-19T00:00:00Z",
) -> list[str]:
    def keyed(r: dict[str, Any]) -> dict[str, Any]:
        if "query" not in r or "ground_truth" not in r:
            return r
        key = sample_key(
            query=r["query"], ground_truth=r["ground_truth"], question=None, source_pin=None
        )
        return {"sample_key": key, **r}

    return archive.file_answers(
        _entry(node_configs, dataset_hash),
        [(MeasuredCell.from_wire(keyed(r)), provenance) for r in rows],
        role="panel",
        source=RunSource.OPTIMIZATION_LOOP.value,
        created_at=created_at,
    )


# 1. Replay eligibility


def _walk_panel(
    root: Path,
    panel: list[Sample],
    per_sample: str,
    per_cell: str | None,
    *,
    bank: list[Sample] | None = None,
    ledger: Path | None = None,
) -> Any:
    """*ledger* given, the walk is an arm's and records what it took there; *bank* given, the
    panel is a part of that bank."""
    schema = pipeline_schema(
        name="claims",
        nodes=[PipelineNode(name="solve", tunes_llm=False, prompt_info=PROMPT_INFO)],
    )
    session = loop_session(
        workspace(root),
        schema,
        panel if bank is None else bank,
        backend_id="b",
        scorer=compile_scorer(per_sample, per_cell, verifier_graded=False),
    )
    sp = PointContext(schema=schema, framing=TaskDecomposition(), demo=[]).job(
        prompted(instruction="Answer.")
    )

    async def _measure(sample: Sample, _session: Any, *, pipeline_params: Any) -> MeasuredCell:
        predicted = "b" if sample.id % 2 else "a"
        return MeasuredCell(
            sample_key=sample.key,
            query=sample.query,
            ground_truth="a",
            predicted=predicted,
        )

    bound = set_cycle_ledger(CycleEventLog(root / "walk.jsonl" if ledger is None else ledger))
    measured = None if ledger is None else MeasuredPass(individual_id="arm")
    try:
        with mock.patch.object(query_loop, "measure_sample", _measure):
            walked = asyncio.run(
                search_point_scorer.score_search_point(
                    sp, panel, session, label="panel", measured=measured
                )
            )
    finally:
        reset_cycle_ledger(bound)
    return session, walked


def test_a_filed_cell_replays_by_its_content_and_reads_as_a_fresh_run_would(tmp_path: Path) -> None:
    archive = MeasurementArchive(tmp_path / "cells")
    # Filed LAST on purpose: wrongly served, it is the cell's most recent and no answer hides it.
    chain: Configs = [(n, {}) for n in ("l1_generate", "l1_critique", "l2_context", "l3_plan")]
    for predicted, terminal in (("short_circuit", "l1_critique"), ("full_chain", "l3_plan")):
        _file(
            archive,
            [_row("q", predicted=predicted, terminal=terminal)],
            dataset_hash="promptpotter-self",
            node_configs=chain,
        )
    later_node_changed: Configs = [
        ("l1_generate", {}),
        ("l1_critique", {}),
        ("l2_context", {"layout": {"problem_description": ["critique"]}}),
        ("l3_plan", {}),
    ]
    served = [b.cell.predicted for b in ReplayFeed(archive, later_node_changed).advance().values()]
    assert served == ["short_circuit"], (
        "a full-chain row replayed across a later-node config change (fake measurement), or a "
        "mid-chain short-circuit inside the trusted prefix stopped reusing"
    )

    for name, grade, terminal, dataset_hash, hit in (
        ("clean", "A", "llm_only", "aime", True),
        ("connector", "C", "token_matching", "aime", True),
        ("aime_14", "A", "llm_only", "aime", True),
        ("justlogic_14", "A", "llm_only", "justlogic", False),
    ):
        row = _row(f"q_{name}", hit=hit, terminal=terminal)
        _file(archive, [row], dataset_hash=dataset_hash, provenance=grade)
    reusable = ReplayFeed(archive, _LLM_ONLY).advance()
    assert {b.cell.query for b in reusable.values()} == {
        "q_clean",
        "q_aime_14",
        "q_justlogic_14",
    }, "a grade-C answer was replayed"
    # A cell replays on its sample's content in whichever bank holds it, at that bank's slot.
    wider = [
        Sample(id=0, query="q_aime_14", ground_truth="g"),
        Sample(id=14, query="q_new", ground_truth="g"),
        Sample(id=1, query="q_aime_14", ground_truth="not_g"),
        Sample(id=2, query="q_aime_14", ground_truth="g", source_pin={"git_commit_id": "moved"}),
    ]
    on_wider = replayable_on(wider, reusable)
    assert set(on_wider) == {0}, "a cell replayed onto a sample whose content it never measured"
    assert on_wider[0].query == "q_aime_14"

    # Two walks never buy one cell: the waiter reads the answer its holder publishes.
    holder, waiter = (
        ReplayFeed(MeasurementArchive(tmp_path / "cells"), _LLM_ONLY) for _ in range(2)
    )
    held = holder.claim("k_held")
    assert held is not None and waiter.claim("k_held") is None, "two walks bought one cell"
    held.publish(MeasuredCell(sample_key="k_held", predicted="p"), grade="B")
    beside = waiter.claim("k_beside")
    assert beside is not None, "one cell's claim held another sample of its configuration"
    assert waiter.claimed_cell("k_beside") is None
    published = waiter.claimed_cell("k_held")
    assert published is not None and published.predicted == "p", "the waiter lost the paid answer"
    held.release()
    beside.release()

    # Rows banked under one formula read under another exactly as a fresh run would.
    wider_panel = samples(10)
    panel = wider_panel[:6]
    banked_under = ("label_match(predicted, ground_truth)", None)
    read_under = ("0.5 * label_match(predicted, ground_truth)", "0.5 * fitness + 0.25")
    shared, banked_walk = _walk_panel(tmp_path / "shared", panel, *banked_under, bank=wider_panel)
    assert all(GRADE_KEYS & r.keys() for r in banked_walk.sheet.wire()), "guard: the walk graded"
    banked = archive_queries.walked_answers(shared.store, banked_walk.cells)
    assert len(banked) == len(panel), "a walked answer was not banked"
    filed = shared.store.archive.cell_signatures()
    _, fresh = _walk_panel(tmp_path / "fresh", panel, *read_under)
    worth = {c.key: c.grade.objective for c in fresh.sheet}
    assert len(set(worth.values())) == 2, "guard: hits and misses are worth different amounts"

    _, replayed = _walk_panel(tmp_path / "shared", panel, *read_under, bank=wider_panel)
    assert all(c.facts.cached for c in replayed.sheet), "guard: the second read bought nothing"
    ruler_cells = {key: y for _, key, y in ruler_rows(shared.store, [("x", replayed.sheet)], None)}
    assert ruler_cells == pytest.approx(worth), "the ruler read the banked formula's grades"
    assert [c[:2] for c in replayed.cells] == [c[:2] for c in banked_walk.cells]
    assert shared.store.archive.cell_signatures() == filed, "a replay filed a second answer"
    assert {c.key: c.grade.objective for c in replayed.sheet} == worth
    assert replayed.scores.composite_fitness == pytest.approx(fresh.scores.composite_fitness)


# 2. The archive on disk


def test_the_archive_on_disk_keeps_every_paid_answer(
    built_stores: Stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = built_stores.archive
    entry = _entry(_LLM_ONLY, "kept")
    other: Configs = [("llm_only", {"model": "Y"})]
    refs: list[str] = []
    for at, said, configs, facts in (
        (1, "first", _LLM_ONLY, {}),
        (2, "second", _LLM_ONLY, {}),
        (3, "ERROR", _LLM_ONLY, {"error_category": "transient"}),
        (4, "elsewhere", other, {}),
    ):
        row: dict[str, Any] = {"sample_key": "k1", "predicted": said, **facts}
        refs += _file(
            archive,
            [row],
            dataset_hash="kept",
            node_configs=configs,
            created_at=f"2026-05-19T00:{at:02}",
        )
    assert len(set(refs)) == 4
    held = archive.population(entry)
    assert [a.cell.predicted for a in held] == ["first", "second", "ERROR"], "an answer was dropped"
    assert standing(held)["k1"].cell.predicted == "second", (
        "an errored answer stood over a graded one"
    )
    described = {e.config_key for e in archive.list_all(dataset_hash="kept")}
    assert described == {config_key(_LLM_ONLY), config_key(other)}
    assert archive.reindex() == {"indexed": 2, "undescribed": 0}
    assert {e.config_key for e in archive.list_all(dataset_hash="kept")} == described
    assert archive.population(entry) == held, "reindex destroyed an answer"
    assert all(archive.answer(ref) is not None for ref in refs), "reindex lost an address"

    # A reader tailing a cell file rewritten under it skips no answer.
    tailed: Configs = [("llm_only", {"model": "T"})]
    reader = MeasurementArchive(archive.base_dir)
    feed = ReplayFeed(reader, tailed)

    def _bank(sid: int, dataset_hash: str = "reidx", via: MeasurementArchive = archive) -> None:
        cell = {
            "sample_key": f"k{sid}",
            "predicted": "p",
            "pipeline_data": {"reasoning_trace": "T" * 400},
        }
        _file(via, [cell], dataset_hash=dataset_hash, node_configs=tailed)

    for sid in (1, 2, 3):
        _bank(sid)
    assert set(feed.advance()) == {"k1", "k2", "k3"}
    file_key = config_key(tailed)
    cells = archive.cell_path(file_key)
    read_to = cells.stat().st_size
    # Rewritten IN PLACE, as an ext4 swap onto a freed inode reads: every line kept, each shorter.
    cells.write_text("".join(ln.replace("T" * 400, "") for ln in archive.detail_lines(file_key)))
    _bank(4)
    assert cells.stat().st_size < read_to
    assert set(feed.advance()) == {"k4"}, "an answer filed after the rewrite was skipped"
    assert reader.entry(file_key, "reidx") is not None
    archive.reindex()
    _bank(5, dataset_hash="reidx-wide")
    assert reader.entry(file_key, "reidx-wide") is not None, "an index entry was skipped"
    # The other process files between this writer's look at the file and its own append.
    own = ReplayFeed(archive, tailed)
    own.advance()

    def raced(path: Path, *rows: dict[str, Any]) -> range:
        monkeypatch.setattr(measurement_archive, "append_row", append_row)
        _bank(6, via=reader)
        return append_row(path, *rows)

    monkeypatch.setattr(measurement_archive, "append_row", raced)
    _bank(7)
    assert set(own.advance()) == {"k6", "k7"}, "an answer another process filed was stepped over"
    _bank(8)
    assert set(own.advance()) == {"k8"}

    # Four writers on one file at once: Windows runs `O_APPEND` as seek-then-write, so what keeps
    # every row is the lock under `append_row`, taken on a fresh handle per append.
    racing = archive.base_dir / "racing.jsonl"
    writers = [
        threading.Thread(
            target=lambda w=w: [append_row(racing, {"w": w, "n": n}) for n in range(50)]
        )
        for w in range(4)
    ]
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join()
    landed = sorted(
        (row["w"], row["n"]) for row in map(json.loads, racing.read_bytes().splitlines())
    )
    assert landed == [(w, n) for w in range(4) for n in range(50)], (
        "a concurrent append overwrote another writer's row"
    )

    # Compaction round-trips every field it moved; TWO filings, so a restore clears each stamp.
    compacted: Configs = [("llm_only", {"model": "C"})]
    compacted_bank = Bank.of(Sample(id=0, query=f"q{sid}", ground_truth="g") for sid in (1, 2))
    compacted_hash = compacted_bank.dataset_hash
    built_stores.tenant_datasets.put_bank(compacted_bank)
    built_stores.tenant_datasets.relabel("compacted", compacted_hash)
    compacted_refs: dict[str, str] = {}
    for sid in (1, 2):
        row = {
            **_row(f"q{sid}"),
            "pipeline_data": {
                "terminal_node": "llm_only",
                "diagnostics": {"warnings": []},
                "step_tokens": {"llm_only": {"input": 10, "output": 5}},
                "step_timings": {"llm_only": 0.5},
                "reasoning_trace": "T" * 3000,
                "result_ranking": [1, 2, 3],
                "final_ranking": [1, 2, 3],
                "total_time": 1.25,
            },
        }
        [compacted_refs[f"q{sid}"]] = _file(
            archive, [row], dataset_hash=compacted_hash, node_configs=compacted
        )
    whole = _entry(compacted, compacted_hash)
    before = archive.population(whole)
    assert len(before) == 2 and not any(answer.compaction for answer in before)
    moved_out = compact_measurement_archive(built_stores, dataset="compacted", apply=True)
    assert (moved_out.files_touched, moved_out.rows_moved) == (1, 2) and moved_out.bytes_freed > 0
    mid = archive.population(whole)
    hot, full = mid[0].cell, before[0].cell
    assert hot.pipeline.reasoning_trace is None and full.pipeline.reasoning_trace
    # The reading campaign's scorer re-grades from these, so compaction leaves them hot.
    assert (hot.predicted, hot.ground_truth) == ("p", "g"), "compaction took what a regrade reads"
    assert hot.pipeline.step_tokens == full.pipeline.step_tokens != {}
    assert hot.pipeline.step_timings == {"llm_only": 0.5}
    assert hot.pipeline.result_ranking == (1, 2, 3)
    assert all(answer.compaction for answer in mid)
    moved_back = restore_measurement_archive(built_stores, dataset="compacted", apply=True)
    assert (moved_back.files_touched, moved_back.conflicts) == (1, 0)
    assert archive.population(whole) == before, "a restore did not return every moved field"

    # A controlled arm remembers only the answers its campaign walked.
    ds = Bank.of(Sample(id=0, query=f"k{i}", ground_truth="g") for i in range(2))

    def individual(name: str) -> ArchiveEntry:
        return ArchiveEntry(
            config_key=config_key([("", {"individual": name})]),
            prompt_fields_id=name,
            node_configs=[],
            pipeline_params={},
            dataset_hash=ds.dataset_hash,
        )

    def bank(name: str, said: list[str], at: int) -> list[str]:
        return archive.file_answers(
            individual(name),
            [
                (MeasuredCell(sample_key=ds.samples[i].key, predicted=p), "A")
                for i, p in enumerate(said)
            ],
            role="panel",
            source="optimization_loop",
            created_at=f"2026-09-28T00:00:{at:02d}Z",
        )

    campaigns = built_stores.campaigns
    arm_root = CycleHop(campaign_id="arm", cycle_id="cycle_root")
    campaigns.mint_cycle(arm_root)

    def walk(hop: CycleHop, answers: list[str]) -> None:
        arm_walk(
            CycleEventLog.open(CycleDir(campaigns.cycle_dir(hop))),
            ((i, (ds.samples[i].key, answer, False)) for i, answer in enumerate(answers)),
            role=MeasurementRole.PANEL,
        )

    def listed() -> set[str]:
        return {e.prompt_fields_id for e in list_populations(built_stores, ds)}

    def remembered(name: str) -> list[str]:
        return [a.cell.predicted for a in load_population(built_stores, individual(name), ds)]

    def fold_row(key: str) -> SampleFoldRow:
        return SampleFoldRow(config_key=key, sp=key, fk="formula", sig=[], graded=[])

    bank("foreign", ["yes", "no"], 1)
    bank("shared", ["no", "no"], 2)
    write_sample_fold(built_stores, dataset_hash=ds.dataset_hash, rows=[fold_row("x")])

    def inside_arm() -> tuple[set[str], list[str], list[SampleFoldRow]]:
        walk(arm_root, bank("origin", ["yes", "no"], 3))
        bind_memory_scope(ArmOf(CampaignWalks(campaigns, "arm"), ruler_version=0))
        assert listed() == {"origin"}, "the arm saw a population its campaign never walked"
        # A cycle the campaign mints after the fence (a rebase, a fork) is its own too, and a
        # replayed answer joins the arm's memory once its walk lands there.
        later = CycleHop(campaign_id="arm", cycle_id="cycle_rebased")
        campaigns.mint_cycle(later)
        walk(later, bank("shared", ["yes", "yes"], 0))
        write_sample_fold(built_stores, dataset_hash=ds.dataset_hash, rows=[fold_row("y")])
        return (
            listed(),
            remembered("shared"),
            sample_fold_rows(built_stores, dataset_hash=ds.dataset_hash),
        )

    arm_listed, arm_shared, arm_fold = contextvars.copy_context().run(inside_arm)
    assert arm_listed == {"origin", "shared"}
    assert arm_shared == ["yes", "yes"], (
        "another campaign's answer of a shared cell steered the arm"
    )
    assert arm_fold == [], "the arm read a fold its fence never admitted"
    assert listed() == {"foreign", "shared", "origin"}, "the fence leaked out of the arm"
    assert remembered("shared") == ["no", "no"]
    assert sample_fold_rows(built_stores, dataset_hash=ds.dataset_hash) == [fold_row("x")]

    # A restamp prunes a record against the union arm it IS: read as the first arm, a walked
    # arm lost its `sp_hash`, and the rewritten ledger refused its cycle to every reader.
    [announced] = [
        rec
        for rec in map(json.loads, campaigns.ledger(arm_root).read_text("utf-8").splitlines())
        if rec["record_type"] == "arm"
    ][:1]
    walked = announced | {
        "state": announced["state"]
        | {"state": "measuring", "sp_hash": "h", "resolved_params": {}, "expected_samples": 2},
        "retired_key": 1,
    }
    pruned, dropped = restamp._prune_record(walked)
    assert dropped == ["retired_key"] and pruned["state"] == walked["state"], (
        "a restamp deleted a key the reader of that record still needs"
    )

    # A relabel moves a name onto a bank: it rewrites no paid row and no campaign, and every cell
    # filed under the bank replays on the samples the new name resolves to.
    def tree_bytes() -> dict[Path, bytes]:
        roots = (archive.base_dir / MEASUREMENTS_DIR, campaigns_root_dir_for(campaigns.workspace))
        return {p: p.read_bytes() for root in roots for p in root.rglob("*") if p.is_file()}

    before_relabel = tree_bytes()
    built_stores.tenant_datasets.relabel("compacted-renamed", compacted_hash)
    assert tree_bytes() == before_relabel, "a relabel rewrote a measurement or a campaign"
    renamed = built_stores.tenant_datasets.labelled("compacted-renamed")
    assert renamed == compacted_bank
    assert set(replayable_on(list(renamed.samples), ReplayFeed(archive, compacted).advance())) == {
        0,
        1,
    }, "a cell filed under the bank stopped replaying once its name moved"

    # A re-cut drops q1 and moves q2: an old answer opens at its own bank's slot, and a holdout
    # read under the old bank stays spent under the new one.
    q1, q2 = compacted_bank.samples
    recut = Bank.of([Sample(id=0, query="q_new", ground_truth="g"), q2])
    built_stores.tenant_datasets.put_bank(recut)
    built_stores.tenant_datasets.relabel("compacted-renamed", recut.dataset_hash)
    definition = built_stores.tenant_datasets.dataset_dir("compacted-renamed")
    definition.mkdir(parents=True)
    (definition / DEFINITION_FILE).write_text(
        "pipeline:\n  nodes:\n    llm_only: {type: llm}\n  pipelines:\n    default: [llm_only]\n"
        "campaign:\n  scoring: label_match(predicted, ground_truth)\n"
        "  optimization:\n    degradation_threshold: 0.4\n",
        encoding="utf-8",
    )
    opened = open_cell(built_stores, "compacted-renamed", compacted_refs[q1.query])
    assert opened.position == compacted_bank.slot_of[q1.key], (
        "an old answer opened at another bank's slot"
    )
    assert bench_reads(built_stores, bank=recut, sample_keys=frozenset({q2.key})) == 0, "guard"
    archive.file_answers(
        _entry(compacted, compacted_hash),
        [(MeasuredCell(sample_key=q2.key, predicted="g"), "A")],
        role=MeasurementRole.BENCH.value,
        source=RunSource.OPTIMIZATION_LOOP.value,
        created_at="2026-05-19T00:00:00Z",
    )
    assert bench_reads(built_stores, bank=recut, sample_keys=frozenset({q2.key})) == 1, (
        "a holdout read under the bank before a re-cut was not counted on the re-cut"
    )


# 3. Replayed decisions, and forks


def _round(**kw: Any) -> RoundResult:
    return RoundResult(
        **{
            "folds": {},
            "election": None,
            "overlap": OverlapReading.unpaired(ReadingState.NOT_HELD, 0, False),
            "optimizer_state": optimizer_state(),
        }
        | kw
    )


def _minted(stores: Stores, cycle_id: str) -> tuple[CycleHop, CycleEventLog]:
    hop = CycleHop(campaign_id=_CAMPAIGN, cycle_id=cycle_id)
    stores.campaigns.mint_cycle(hop)
    return hop, CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(hop)))


def _mint_sibling(
    store: Any, parent: CycleHop, cycle_id: str, spec: ForkSpec, *, cut_round: int
) -> CycleHop:
    """The mint record alone, cut at the parent's head: what `mint_fork` writes for a sibling."""
    child = parent.model_copy(update={"cycle_id": cycle_id})
    parent_dir = store.cycle_dir(parent)
    store.mint_fork_cycle(
        child,
        parent_dir,
        spec,
        cut=CycleEventLog.open(CycleDir(parent_dir)).next_offset,
        cut_round=cut_round,
    )
    return child


def _emit_round(
    closing: Cycle,
    ledger: CycleEventLog,
    closed: list[RoundResult],
    name: str,
    *,
    crowned: bool = True,
) -> str:
    n = len(closed)
    pick = prompted(instruction=name)
    key = ArmKey(pick.id)
    arm = arm_state(name, slot=ArmSlot(round=n, idx=0, total=1), individual=pick)
    rr = _round(round=n, arms={key: arm}, election=election(key if crowned else None, ended_on=key))
    closed.append(rr)
    close_round(ledger, closed, cycle=closing)
    return pick.id


def test_a_closed_round_replays_its_decisions_a_rewind_retracts_them_and_a_fork_inherits_them(
    built_stores: Stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = built_stores
    # A replayer that cannot re-derive its decision is a named divergence, never a clean match.
    blind = replay_decisions(
        _round(
            round=0,
            cells={
                ArmKey("c1"): sheet(
                    [measurement(0, 1.0, objective=1.0, query="q", predicted="p", ground_truth="g")]
                )
            },
        ),
        [
            ResumeCheckpointRecord.model_validate(
                {
                    "kind": "round_winner",
                    "inputs_ref": {"arms": ["c1"], "round_num": 0, "coverage_floor": 1},
                    "outcome": "c1",
                }
            )
        ],
    )
    assert blind is not None, "an unreproducible decision was reported as a clean replay"
    assert blind.kind == "replay_error:round_winner", (
        "the mismatch must name WHICH check went blind"
    )

    # A cut the live race made past an errored cell replays on the cells it was fit on.
    parent = prompted(instruction="parent")
    hits = sheet(measurements([1.0] * 10))
    racing = seat(
        stores,
        round_num=3,
        optimization={"elimination_n_min": 4, "nodes": {"pobb": {"config": {"epsilon": 0.30}}}},
    )
    on_parent = {
        "arms": {ArmKey(parent.id): arm_state("parent", individual=parent)},
        "election": election(ended_on=parent.id),
    }
    # The frontier holds every cell, answered by an individual that is not the parent.
    line = [_round(round=0, cells={ArmKey("origin"): hits}, **on_parent)]
    set_cycle(racing.cycle, line=line)
    ten = samples(10, "g")

    def race_of() -> PoBBRace:
        return PoBBRace(
            NodeContext(racing, optimizers.member("pobb")),
            Panel(cells=ten, order=ten, block_size=len(ten)),
            lambda sp, sample, prior_id: None,  # type: ignore[arg-type, return-value]
            measured_unit="sample",
        )

    walked = sheet(
        measurement(i, None, error="boom", error_category="transient")
        if i == 2
        else measurement(i, 1.0 if i == 4 else 0.0)
        for i in range(8)
    )
    assert race_of().rule([]).check(walked.cells) is None, (
        "the parent's prior borrowed cells another individual answered"
    )
    set_cycle(
        racing.cycle, line=[*line, _round(round=1, cells={ArmKey(parent.id): hits}, **on_parent)]
    )
    race = race_of()
    reading = race.judge(
        race.rule([]).check(walked.cells),
        key=ArmKey("c1"),
        results=walked.cells,
        labels={ArmKey("c1"): "C3.1"},
    )
    assert reading is not None and reading.context["gate"] == EliminationGate.EPSILON
    [cut] = racing.cycle.pending_decisions
    assert cut.data["candidate_cells"] == [str(i) for i in range(8) if i != 2]
    assert replay_decisions(_round(round=3, cells={ArmKey("c1"): walked}), [cut]) is None, (
        "the cut the live rule made is not the cut its own record replays"
    )

    # A parent's charged error replays at the grade the live election read.
    scorer = compile_scorer("label_match(predicted, ground_truth)", None, verifier_graded=False)

    def filed(model: str, answers: dict[int, str | None]) -> Any:
        rows = [
            {"query": f"q{sid}", "ground_truth": "g"}
            | (
                {"predicted": said}
                if said is not None
                else {"error": "boom", "error_category": "PIPELINE"}
            )
            for sid, said in answers.items()
        ]
        refs = _file(
            stores.archive,
            rows,
            dataset_hash="replayds",
            node_configs=[("llm_only", {"model": model})],
        )
        return scorer.sheet(a.cell for ref in refs if (a := stores.archive.answer(ref)) is not None)

    # Two cells won outright; the third, which clears the bar, only the parent's charged error gives.
    answered = {sid: "g" if sid < 5 else "x" for sid in range(9)}
    parent, child = prompted(instruction="parent"), prompted(instruction="child")
    bar = filed("P", {**answered, 9: None})
    arm = filed("C", {**answered, 5: "g", 6: "g", 9: "g"})
    assert bar.cells[9].scored and bar.cells[9].grade.fitness == 0.0, "guard"
    electing = seat(stores)
    set_cycle(electing.cycle, line=[_round(round=0, **on_parent)], population=[parent])
    walked_child = ArmKey(child.id)
    child_arm = arm_state("child", slot=ArmSlot(round=1, idx=0, total=1), individual=child)
    read_parent = ReadArm(ArmKey(parent.id), arm_state("parent", individual=parent), bar)
    selection = elect_on_theta(
        NodeContext(electing, optimizers.member("theta_election")),
        MeasuredRound(
            arms={walked_child: child_arm},
            rows={walked_child: arm},
            parent=read_parent,
            filed={
                walked_child: ReadArm(walked_child, child_arm, arm),
                read_parent.key: read_parent,
            },
            folds={},
            fit={},
            electable=[walked_child],
            coverage_floor=1,
            cut=None,
            origin_panel_slots=[],
            on_panel={},
            crowned=frozenset(),
        ),
        Proposals([]),
    )
    assert selection.selected == walked_child, "guard: the live election charges the error"
    charged, ledger = _minted(stores, "cycle_charged_parent")
    origin_slot = ArmSlot(round=0, idx=0, total=1)
    adopted = _round(
        round=0,
        arms={ArmKey("origin"): arm_state("origin", slot=origin_slot)},
        election=election("origin"),
    )
    close_round(
        ledger,
        [
            adopted,
            _round(
                round=1,
                arms={walked_child: child_arm, read_parent.key: read_parent.state},
                election=election(walked_child),
                cells={walked_child: arm, read_parent.key: bar},
            ),
        ],
        cycle=seat(stores, bank=samples(10, "g")).cycle,
    )
    [closed_on_disk] = closed_rounds(stores, charged, scorer, moment=None)
    [decided] = electing.cycle.pending_decisions
    assert replay_decisions(closed_on_disk, [decided]) is None, (
        "the replay read the parent's charged error as a cell nobody scored"
    )

    # Rounds stand; a rewind retracts every election, close and crown past it, for every reader.
    closing = seat(stores).cycle
    hop = CycleHop(campaign_id="rewound__20260101-000000", cycle_id="cycle_rewound")
    stores.campaigns.create_campaign(
        minted_campaign(hop, owner_user_id=str(stores.identity.user_id))
    )
    stores.campaigns.mint_cycle(hop)
    ledger = CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(hop)))
    closed: list[RoundResult] = []
    ids = [_emit_round(closing, ledger, closed, name) for name in ("c0", "c1", "c2")]

    def on_course() -> list[HierarchyCandidate]:
        course = subtree_course(stores, (hop,))
        return [c for r in course.children for c in r.children if isinstance(c, HierarchyCandidate)]

    def served() -> list[tuple[str, bool, bool]]:
        return [
            (c.facts.individual, c.facts.reading.election.held, c.facts.reading.election.selected)
            for c in on_course()
        ]

    def selected() -> list[str]:
        index = stores.campaigns.load(hop, moment=None)
        assert index is not None
        readers = (
            stores.campaigns.standing_rounds(hop, moment=None).standing,
            subtree_course(stores, (hop,)).facts.standing,
            index.standing,
        )
        return [reader.selection.label for reader in readers]  # type: ignore[union-attr]

    assert served() == [(i, True, True) for i in ids] and selected() == ["C2.1"] * 3, "guard"
    # A `--from` retracts rounds only once preflight admits the launch.
    refused = loop_session(stores, pipeline_schema([]), [], hop=hop)

    def refuse(*_: Any, **__: Any) -> None:
        raise PayloadInvalidError("refused", code="spend_cap_below_round")

    with monkeypatch.context() as patched, pytest.raises(PayloadInvalidError):
        patched.setattr(loop_start, "run_preflight_checks", refuse)
        asyncio.run(
            loop_start.init_optimization_loop(
                mock.Mock(),
                [],
                mock.Mock(),
                cb=mock.Mock(),
                no_divergence_check=True,
                fork_on_divergence=False,
                resume_from_round_override=1,
                session=refused,
            )
        )
    assert refused.state.resumed_from_round == 2, "a refused launch reported another next round"
    assert list(stores.campaigns.standing_rounds(hop, moment=None).rounds) == [0, 1, 2], (
        "a refused launch's `--from` retracted rounds on the ledger"
    )
    stores.campaigns.rewind_to_round(hop, 1)
    assert selected() == ["C1.1"] * 3, (
        "the ledger, the subtree and the index disagree on the selection"
    )
    stores.campaigns.rewind_to_round(hop, 0)
    stood = stores.campaigns.standing_rounds(hop, moment=None)
    assert list(stood.rounds) == list(stood.elections) == [0] and stood.crowns == {0: ids[0]}, (
        "an election, close or crown stood past the rewind"
    )
    assert served() == [(ids[0], True, True)]
    # Every live fold reads the rewind off its record: a re-entered round's arm folds its new walk.
    folds = AnnouncedArms()
    walk = ArmRecord(round=1, arm=ArmKey(ids[1]), state=closed[1].arms[ArmKey(ids[1])])
    [cell] = arm_walk(
        CycleEventLog(stores.projects_root / "rewalk.jsonl"),
        [(0, ("k0", "a", False))],
        rnd=1,
        individual=ids[1],
        role=MeasurementRole.PANEL,
        running=score_summary(),
    )
    [rewind] = [
        rec
        for _, rec in ledger.iter()
        if isinstance(rec, RoundEnteredRecord) and rec.rewound and rec.round == 1
    ]
    folds.announce(walk)
    folds.take(cell)
    assert folds.fold(1, walk.arm) is not None, "guard"
    folds.enter(rewind)
    with pytest.raises(LookupError):
        folds.take(cell)
    folds.announce(walk)
    assert folds.fold(1, walk.arm) is None, (
        "a re-announced arm folded the cells of the pass a rewind retracted"
    )
    again = _emit_round(closing, ledger, closed[:1], "c1_again", crowned=False)
    assert served() == [(ids[0], True, True), (again, True, False)]
    origin, held = on_course()
    assert (origin.facts.stands, held.facts.stands) == (True, False), (
        "an uncrowned re-run took the stand"
    )

    # A close holding an unregistered slice stops the read. `rewrite_lines` writes a fresh file: a
    # ledger index takes a same-inode growth for an append, and no writer here grows one in place.
    peer, ledger = _minted(stores, "cycle_of_a_peer")
    _emit_round(closing, ledger, [], "c0")

    def _uninstall(lines: list[str]) -> tuple[list[str], None]:
        records = [json.loads(line) for line in lines]
        close = max(i for i, rec in enumerate(records) if "optimizer_state" in rec)
        records[close]["optimizer_state"]["slices"]["uninstalled"] = {}
        return [json.dumps(rec) + "\n" for rec in records], None

    rewrite_lines(ledger.path, _uninstall)
    with pytest.raises(UnregisteredStatePartError):
        list(CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(peer))).iter())
    with pytest.raises(UnregisteredStatePartError):
        stores.campaigns.standing_rounds(peer, moment=None)

    def _reinstall(lines: list[str]) -> tuple[list[str], None]:
        records = [json.loads(line) for line in lines]
        for rec in records:
            rec.get("optimizer_state", {}).get("slices", {}).pop("uninstalled", None)
        return [json.dumps(rec) + "\n" for rec in records], None

    # Every family read lists the whole store, so the peer is read again by the steps below.
    rewrite_lines(ledger.path, _reinstall)

    # A close this build cannot read refuses its cycle to every reader of rounds, never half-read;
    # the remedy it names deletes the campaign, paused as it is, and keeps the paid measurement.
    stranded = CycleHop(campaign_id="stranded__20260101-000000", cycle_id="cycle_stranded")
    stores.campaigns.create_campaign(
        minted_campaign(stranded, owner_user_id=str(stores.identity.user_id))
    )
    stores.campaigns.mint_cycle(stranded)
    ledger = CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(stranded)))
    _emit_round(closing, ledger, [], "c0")
    ledger.append(RunPhaseRecord.stop(StopReason.PAUSED, cause=PauseCause.INTERRUPT))
    retired: list[int] = []

    def _retire(lines: list[str]) -> tuple[list[str], None]:
        records = [json.loads(line) for line in lines]
        retired.append(max(i for i, rec in enumerate(records) if "optimizer_state" in rec))
        records[retired[0]]["a_field_no_build_declares"] = True
        return [json.dumps(rec) + "\n" for rec in records], None

    rewrite_lines(ledger.path, _retire)
    paid = _file(stores.archive, [_row("q_stranded")], dataset_hash="strandedds")
    readers: dict[str, Callable[[], object]] = {
        "iter": lambda: list(CycleEventLog(ledger.path).iter()),
        "resume": lambda: stores.campaigns.standing_rounds(stranded, moment=None),
        "export": lambda: closed_rounds(stores, stranded, scorer, moment=None),
        "index": lambda: stores.campaigns.load(stranded, moment=None),
        "subtree": lambda: subtree_course(stores, (stranded,)),
        "dashboard": lambda: served_dashboard(stores, stranded),
    }
    for name, read in readers.items():
        with pytest.raises(LedgerUnreadableError) as unreadable:
            read()
        named = (unreadable.value.details["offset"], unreadable.value.details["campaign_id"])
        assert named == (retired[0], stranded.campaign_id), (
            f"the {name} read refused another line, or named another campaign to delete"
        )
    asyncio.run(
        CommandDispatcher(stores).dispatch(
            CommandCall(DeleteCampaignPayload(campaign_id=stranded.campaign_id))
        )
    )
    assert not stores.campaigns.campaign_root_dir(stranded.campaign_id).exists(), (
        "the remedy left the refused campaign standing"
    )
    assert all(stores.archive.answer(ref) is not None for ref in paid), (
        "the remedy took a paid measurement with the campaign"
    )

    # A fork: an unmodified origin inherits the branch-point candidate's reading; an edited one
    # rescores.
    store = stores.campaigns
    prompt = {"instruction": "do the thing", "persona": "you are precise"}
    solving = pipeline_schema(
        [PipelineNode(name="solve", tunes_llm=False, prompt_info=PROMPT_INFO)]
    )
    inheriting, ledger = _minted(stores, "cycle_inherit_parent")
    close_round(
        ledger,
        [
            adopted,
            _round(
                round=1,
                arms={
                    ArmKey("c1"): arm_state(
                        "c1",
                        slot=ArmSlot(round=1, idx=0, total=1),
                        individual=prompted(**prompt),
                        total=10,
                    ),
                    ArmKey("parent"): arm_state("parent", total=10),
                },
                folds={ArmKey("c1"): score_summary(total=10)},
                election=election(ended_on="parent"),
            ),
        ],
        cycle=closing,
    )
    fork = "cycle_inherit_parent_fork_abc123"
    spec = ForkSpec(
        trigger=ForkTrigger.OPERATOR_STEERED,
        reason="",
        issued_by=SYSTEM_PRINCIPAL,
        from_candidate=RoundArm(round=1, arm=ArmKey("c1")),
    )
    _mint_sibling(store, inheriting, fork, spec, cut_round=0)
    session = loop_session(
        stores,
        solving,
        [],
        hop=CycleHop(campaign_id=_CAMPAIGN, cycle_id=fork),
        # The branch-point round banked no row here, so no cell is ever graded.
        scorer=compile_scorer("label_match(predicted, ground_truth)", None, verifier_graded=False),
    )

    def inherit(fields: dict[str, str]) -> Any:
        # Resolved as `establish_campaign_origin` does: the fork seed wins.
        seed = CycleSeed(
            origin_prompts=prompted(**fields).prompts, origin_source=OriginSource.FORK_SEED
        )
        resolved = resolve_origin_opt_search_point(seed=seed, pipeline_params=None, schema=solving)
        return try_inherit_fork_origin(
            session,
            seed,
            resolved_origin=resolved,
            sp=JobSearchPoint(),
            framing=TaskDecomposition(),
        )

    inherited = inherit(dict(prompt))
    assert inherited is not None, "an unmodified fork origin rescored what its parent paid for"
    assert inherited.origin.individual.lineage.source == "origin"
    assert inherit({**prompt, "instruction": "do it differently"}) is None, (
        "an edited fork origin inherited a measurement it never earned"
    )

    # An applied scenario forks at its round, keeps the parent's holdout and carries its criterion.
    scenario, scenario_log = _minted(stores, "cycle_applyscenario")
    stores.campaigns.create_campaign(
        minted_campaign(scenario, owner_user_id=str(stores.identity.user_id))
    )
    scenario_rounds: list[RoundResult] = []
    scenario_pick = ArmKey(
        [_emit_round(closing, scenario_log, scenario_rounds, f"s{n}") for n in range(2)][-1]
    )
    bank = samples(4, "g")
    drawn = partition_bank(bank, DatasetSplit(bench=0), seed=0).record(dataset_hash="bank")
    store.write_bank_partition(scenario, drawn)
    criterion = "0.9 * accuracy + 0.1 * (1 - mean_latency_s)"
    fork_id = mint_operator_fork(
        stores=stores,
        hop=scenario,
        from_round=2,
        from_candidate_id="",
        seed=CycleSeed(config_overrides=ConfigOverrides(scoring=criterion)),
        keep_rounds=True,
    )
    applied = scenario.model_copy(update={"cycle_id": fork_id})
    index = store.load(applied, moment=None)
    assert index is not None and index.fork is not None
    assert index.fork.issued_by.id == str(stores.identity.user_id), "the fork names no issuer"
    assert store.read_bank_partition(applied) == drawn, (
        "a redrawn holdout reads the fork's bench lift on rows its parent never held out"
    )
    # OPERATOR_REWIND is the rebase branch, the one that lifts rounds 0..N-1.
    assert (index.fork.trigger, index.cut_round, index.fork.from_candidate) == (
        ForkTrigger.OPERATOR_REWIND,
        2,
        RoundArm(round=1, arm=scenario_pick),
    ), "a rewind's record names another arm than the pick its parent's line stood on"
    seed = store.read_cycle_seed(applied)
    assert seed is not None
    base = CampaignConfig(optimization=OptimizationConfig(degradation_threshold=0.05))
    assert apply_config_overrides(base, seed.config_overrides).scoring == criterion
    assert base.scoring is None, "applying a scenario wrote through to its base config"

    # A fork's own ledger displaces its parent's prefix once.
    displaced = CycleHop(campaign_id="displaced__20260101-000000", cycle_id="cycle_displaced")
    stores.campaigns.create_campaign(
        minted_campaign(displaced, owner_user_id=str(stores.identity.user_id))
    )
    stores.campaigns.mint_cycle(displaced)
    ledger = CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(displaced)))
    closed = []
    prefix = [_emit_round(closing, ledger, closed, f"p{n}") for n in range(3)]
    fork_hop = _mint_sibling(
        store,
        displaced,
        "cycle_displaced_fork_r1",
        ForkSpec(
            trigger=ForkTrigger.OPERATOR_REWIND,
            reason="",
            issued_by=SYSTEM_PRINCIPAL,
            from_candidate=RoundArm(round=2, arm=ArmKey(prefix[2])),
        ),
        cut_round=3,
    )

    def candidates(hop: CycleHop) -> list[str]:
        return [key for _, key in store.standing_rounds(hop, moment=None).arms]

    assert candidates(fork_hop) == prefix, "the fork does not continue its parent's rounds"
    own = CycleEventLog.open(CycleDir(store.cycle_dir(fork_hop)))
    forked = closed[:1]
    mine = [
        _emit_round(closing, own, forked, "f1"),
        _emit_round(closing, own, forked, "f2", crowned=False),
    ]
    stood = store.standing_rounds(fork_hop, moment=None)
    assert candidates(fork_hop) == [prefix[0], *mine], "a parent round stood beside the fork's own"
    assert [next(iter(r.close.arms)) for r in stood.rounds.values()] == [prefix[0], *mine]
    assert stood.crowns == {0: prefix[0], 1: mine[0]}
    assert candidates(displaced) == prefix, "the fork's rounds displaced its parent's own"

    # A step's silence is the family's, wherever a window happens to cut the ray.
    def gaps(limit: int) -> dict[str, float]:
        ray = open_family_ray(stores, (fork_hop,), limit=limit, before=None).window()
        return {step.address: step.facts.gap_before_s for step in ray.steps}

    whole = gaps(10_000)
    assert len(whole) > 2, "guard"
    for limit in (1, 2, len(whole) - 1):
        windowed = gaps(limit)
        assert len(windowed) == limit and {s: whole[s] for s in windowed} == windowed, (
            f"a {limit}-step window re-measured the silence before its first step"
        )

    # A supersede ends its parent only where no producer holds it: a holder declares its own stop.
    def ended(hop: CycleHop) -> StopReason | None:
        index = store.load(hop, moment=None)
        assert index is not None
        return index.stop_reason

    # Held as a server-launched run holds it, from a lock of its own process.
    lock = CycleLayout(store.cycle_dir(displaced)).producer_lock
    producer = FileLock(str(lock))
    producer.acquire()
    with producer_lock.probe(lock) as seen:
        assert seen, "a reader's probe missed the producer holding the cycle"
    asked = time.monotonic()
    store.mark_superseded(displaced, fork_hop.cycle_id)
    waited = time.monotonic() - asked
    producer.release()
    assert ended(displaced) is None, "a supersede ended its parent under the producer running it"
    assert waited < 0.5, f"a supersede waited {waited:.2f}s out a live producer's hold"
    lock.touch()
    with producer_lock.probe(lock):
        assert not producer_lock.held(lock), (
            "a reader's probe in flight read as a producer, so a supersede would skip its stop"
        )
    store.mark_superseded(displaced, fork_hop.cycle_id)
    assert ended(displaced) is StopReason.REBASED, "a superseded parent no producer holds runs on"
    [listed] = list_hierarchy(
        stores,
        inside=(),
        dataset=None,
        lifecycle="all",
        campaign_id=displaced.campaign_id,
        attached_only=False,
        depth=1,
    ).entries
    assert listed.facts.line == hierarchy_address((), fork_hop.campaign_id, fork_hop.cycle_id), (
        "the campaign's line stayed on the cycle a supersede retired"
    )
    [line_course] = [c for c in listed.children if c.address == listed.facts.line]
    assert line_course.facts.standing is not None, "guard"
    held_label = READING_STATE_INFO[ReadingState.HELD_ELSEWHERE].label
    for course in listed.children:
        graded = [s.value for s in course.stats if s.key == "bench"]
        assert (graded == [held_label]) is (course is not line_course), (
            "the bench graded a course that does not hold the line, or the line's read held elsewhere"
        )
    assert listed.stats[1:] == line_course.stats[1:], "the campaign's bench is not its line's"
    [lift] = [s for s in line_course.stats if s.key == "lift"]
    assert ("θ" in (lift.sub or "")) is (line_course.facts.elects_on == "ability"), (
        "a lift column read beside a round won on θ does not say so, or says it where none was"
    )
    # The bench chip grades archive rows, so a regrade at rest must answer the next ask with a body.
    validator = subtree_version(stores, listed.address)
    _file(stores.archive, [_row("q_regraded")], dataset_hash="regradeds")
    assert subtree_version(stores, listed.address) != validator, (
        "an archive write left the subtree's validator standing: its BENCH chip goes stale behind a 304"
    )
    picked = line_course.facts.standing.selection
    assert picked is not None, "guard"
    [line_drawn] = [
        e
        for e in entries_under(hierarchy_subtree(stores, listed.address, None, None, moment=None))
        if isinstance(e, SubtreeCourse) and e.address == listed.facts.line
    ]
    line_stood = [
        e
        for rnd in line_drawn.children
        if isinstance(rnd, HierarchyRound)
        for e in rnd.children
        if isinstance(e, HierarchyCandidate) and e.facts.stands
    ]
    assert [(e.facts.reading.arm.round, e.facts.reading.arm.key) for e in line_stood] == [
        (picked.round, picked.key)
    ], "the subtree stands a candidate on the line other than the one its index selected"
    assert line_course.facts.standing.changes == line_stood[0].facts.reading.changes_description, (
        "the course's standing names another pick's change than the candidate it stands"
    )

    # ONE line resolver over a fork-of-a-fork-of-a-fork: every (course, round) reads off the course
    # owning it, as the mint records say. The trunk crowns o, a, z; the kid cuts it at 2 and holds;
    # the grand cuts the kid at 3 and crowns x; the great cuts the grand at 4; a steer takes the kid
    # back to C0, another the grand to b, an arm of the trunk's round 1 never crowned; rewinds re-run
    # the grand's round 1 and its round 3.
    family = CycleHop(campaign_id="family__20260101-000000", cycle_id="cycle_family")
    stores.campaigns.create_campaign(
        minted_campaign(
            family,
            config=CampaignConfig(
                optimization=OptimizationConfig(degradation_threshold=0.4),
                scoring="label_match(predicted, ground_truth)",
            ),
            owner_user_id=str(stores.identity.user_id),
        )
    )
    stores.campaigns.mint_cycle(family)

    def ident(name: str) -> str:
        return prompted(instruction=name).id

    # One row per round closed, in ledger order: a course's first row mints it off `parent`, cut at
    # `cut` (a steer names the candidate it starts from); every row closes one round on it.
    rows: list[tuple[str, str, int, tuple[int, str] | None, list[str], str | None, str | None]] = [
        # course, parent, cut, steered from, arms, crown, reference
        ("family", "", 0, None, ["o"], "o", None),
        ("family", "", 0, None, ["a", "b"], "a", "o"),
        ("kid", "family", 2, None, ["y"], None, "a"),
        ("family", "", 0, None, ["a", "z"], "z", None),
        ("grand", "kid", 3, None, ["x"], "x", "a"),
        ("great", "grand", 4, None, ["v"], "v", "x"),
        ("steered", "kid", 0, (0, "o"), ["s"], "s", None),
        ("rewound", "grand", 1, None, ["w"], "w", "o"),
        ("redone", "grand", 3, None, ["q"], "q", "a"),
        ("branched", "grand", 0, (1, "b"), ["p"], "p", None),
        ("rerun", "grand", 0, (3, "a"), ["r"], "r", None),
        ("relift", "grand", 0, (1, "o"), ["l"], "l", None),
        ("mended", "grand", 3, None, ["m"], "m", "a"),
        # A supersede fork of a supersede fork, cut below where every course above it was cut.
        ("carried", "rewound", 2, None, ["d"], "d", "w"),
        # A course already minted, cut again: `--from` in place, re-running round 1 after the fork.
        ("rewound", "", 1, None, ["k"], "k", "o"),
        # Two supersede forks of one course, the second cut above where the first branched, then the
        # course re-run in place under both: the first still retires what the second stands on.
        ("rival", "kid", 0, (0, "o"), ["c"], "c", None),
        ("rival", "", 0, None, ["e", "f"], "e", "c"),
        ("first", "rival", 1, None, ["i"], "i", "c"),
        ("first", "", 0, None, ["n"], "n", "i"),
        ("rival", "", 0, None, ["g"], "g", "e"),
        ("second", "rival", 3, None, ["h"], "h", "g"),
        ("rival", "", 2, None, ["j"], "j", "e"),
        # An arm re-run away that only an offshoot's read holds, and no line stands on.
        ("steered", "", 0, None, ["t"], "t", "s"),
        ("glance", "steered", 0, (0, "s"), ["u"], "u", None),
        ("steered", "", 1, None, ["t2"], "t2", "s"),
        # Re-run in place BELOW an arm a cut still holds: that arm reads the line it ran on.
        ("steered", "", 0, None, ["t3"], "t3", "t2"),
        ("peek", "steered", 0, (2, "t3"), ["u2"], "u2", None),
        ("steered", "", 1, None, ["t4"], "t4", "s"),
    ]
    restated_at_mint = {"mended": [1]}
    hops, line_of = {"family": family}, {"family": list[RoundResult]()}
    for row_course, row_parent, row_cut, row_named, row_picks, row_crown, row_reference in rows:
        if row_course in hops and row_cut:
            store.rewind_to_round(hops[row_course], row_cut - 1)
            del line_of[row_course][row_cut:]
        if row_course not in hops:
            spec = ForkSpec(
                trigger=ForkTrigger.OPERATOR_STEERED if row_named else ForkTrigger.OPERATOR_REWIND,
                reason="",
                issued_by=SYSTEM_PRINCIPAL,
                # The one resolution `mint_fork` writes: the named arm, else the line's pick.
                from_candidate=fork_edge(
                    store.standing_rounds(hops[row_parent], moment=None),
                    row_cut,
                    None
                    if row_named is None
                    else RoundArm(round=row_named[0], arm=ArmKey(ident(row_named[1]))),
                ),
            )
            hops[row_course] = _mint_sibling(
                store, hops[row_parent], f"cycle_family_{row_course}", spec, cut_round=row_cut
            )
            line_of[row_course] = line_of[row_parent][:row_cut]
            # A repair restates a lifted round WHOLE at its mint, so the round is the fork's own.
            for r in restated_at_mint.get(row_course, ()):
                lifted = store.standing_rounds(hops[row_course], moment=None).rounds[r].close
                store.restate_round(hops[row_course], lifted.restated(lifted.optimizer_state))
        line, n = line_of[row_course], len(line_of[row_course])
        arms = {
            ArmKey(ident(p)): arm_state(p, slot=ArmSlot(round=n, idx=i, total=len(row_picks)))
            for i, p in enumerate(row_picks)
        }
        reference = None
        if row_reference is not None:
            arms[ArmKey(ident(row_reference))] = arm_state(
                row_reference,
                slot=walked_slot(line, ArmKey(ident(row_reference))),
                role=MeasurementRole.PARENT,
            )
            reference = line_reference(line)
            assert reference.arm == ident(row_reference), (
                f"guard: a row re-reads off {row_course}'s line"
            )
        row_ended = row_crown or row_reference or row_picks[0]
        line.append(
            _round(
                round=n,
                arms=arms,
                election=election(
                    None if row_crown is None else ident(row_crown),
                    ended_on=ident(row_ended),
                    reference=reference,
                ),
            )
        )
        close_round(
            CycleEventLog.open(CycleDir(store.cycle_dir(hops[row_course]))), line, cycle=closing
        )
    restamp = walked_slot(line_of["family"], ArmKey(ident("a")))
    assert restamp is not None and restamp.round == 2, (
        "a re-read stamps a walk other than its latest"
    )
    # Each course by its first row, in mint order.
    (
        kid,
        grand,
        great,
        steered,
        rewound,
        redone,
        branched,
        rerun,
        relift,
        mended,
        carried,
        rival,
        first,
        second,
        glance,
        peek,
    ) = (hops[n] for n in list(dict.fromkeys(row[0] for row in rows))[1:])
    # A round still measuring: its re-read is announced, and nothing has elected or closed.
    live = len(line_of["peek"])
    CycleEventLog.open(CycleDir(store.cycle_dir(peek))).append(
        ArmRecord(
            round=live,
            arm=ArmKey(ident("u2")),
            state=arm_state(
                "u2", slot=ArmSlot(round=0, idx=0, total=1), role=MeasurementRole.PARENT
            ),
        )
    )
    (inner_sandbox(stores, family.campaign_id, family.cycle_id) / stores.tenant_id).mkdir()
    sandbox = inner_sandbox_store(stores, family.campaign_id, family.cycle_id)
    assert sandbox is not None, "guard"
    asks = {
        "reread": (grand, 3, "a", MeasurementRole.PARENT),
        "again": (family, 2, "a", MeasurementRole.PANEL),
        "own": (grand, 3, "x", MeasurementRole.PANEL),
        # A pass beside the draw, of the very individual round 3 re-read: no arm asked it.
        "overlap": (grand, 3, "a", MeasurementRole.OVERLAP),
        # Asked by an arm its course has since re-run away: it is still the candidate that ran it.
        "unrun": (rewound, 1, "w", MeasurementRole.PANEL),
        # Asked by a re-read whose round has not elected yet.
        "live": (peek, live, "u2", MeasurementRole.PARENT),
    }
    for name, (asker, rnd, pick, role) in asks.items():
        spawned = CycleHop(campaign_id=f"{name}__20260101-000000", cycle_id=f"cycle_{name}")
        sandbox.campaigns.create_campaign(minted_campaign(spawned))
        sandbox.campaigns.mint_cycle(spawned)
        sandbox.campaigns.record_spawned(
            spawned,
            SpawnedBy(
                round=rnd,
                individual_id=ident(pick),
                role=role,
                cycle_id=asker.cycle_id,
                task=f"t/{name}",
            ),
        )
    tree = hierarchy_subtree(
        stores, hierarchy_address((), family.campaign_id), None, None, moment=None
    )

    def course_of(hop: CycleHop) -> str:
        return hierarchy_address((), hop.campaign_id, hop.cycle_id)

    hung = {
        name: e.parent
        for e in entries_under(tree)
        if isinstance(e, SubtreeCampaign)
        for name in asks
        if f"{name}__" in e.address
    }

    def at(hop: CycleHop, rnd: int, name: str) -> str:
        return candidate_address(course_of(hop), rnd, ArmKey(ident(name)))

    o, first_pick, b, x = (
        at(family, 0, "o"),
        at(family, 1, "a"),
        at(family, 1, "b"),
        at(grand, 3, "x"),
    )
    assert hung == {
        "reread": first_pick,
        "again": at(family, 2, "a"),
        "own": x,
        "overlap": course_of(grand),
        "unrun": at(rewound, 1, "w"),
        "live": at(peek, 0, "u2"),
    }, "an inner run hung on a candidate other than the one its spawn record names"

    # The bugs reviews found are rows here: the grand's cut round drawn twice, the steer's
    # inherited anchor lost, the rewind's round 1 re-read off the wrong course's picks, and a
    # retirement that is anything but "off the fork's line, within its reach".
    head = [(0, [o]), (1, [first_pick])]
    crownless: tuple[int, list[str]] = (2, [])
    lines = {
        o: [(0, [o])],
        first_pick: head,
        b: [(0, [o]), (1, [b])],
        **{c: [*head, (2, [c])] for c in (at(family, 2, "a"), at(family, 2, "z"), at(kid, 2, "y"))},
        x: [*head, crownless, (3, [x])],
        at(great, 4, "v"): [*head, crownless, (3, [x]), (4, [at(great, 4, "v")])],
        at(steered, 0, "s"): [(0, [at(steered, 0, "s")])],
        at(rewound, 1, "w"): [(0, [o]), (1, [at(rewound, 1, "w")])],
        at(rewound, 1, "k"): [(0, [o]), (1, [at(rewound, 1, "k")])],
        # Its parent re-ran round 1 in place after the cut; the fork's line still stands on w.
        at(carried, 2, "d"): [(0, [o]), (1, [at(rewound, 1, "w")]), (2, [at(carried, 2, "d")])],
        at(redone, 3, "q"): [*head, crownless, (3, [at(redone, 3, "q")])],
        at(branched, 0, "p"): [(0, [at(branched, 0, "p")])],
        at(rerun, 0, "r"): [(0, [at(rerun, 0, "r")])],
        at(relift, 0, "l"): [(0, [at(relift, 0, "l")])],
        # The restated round 1 is the repair's own, whole: its arms are copies on its own line.
        **{c: [(0, [o]), (1, [c]), crownless] for c in (at(mended, 1, "a"), at(mended, 1, "b"))},
        at(mended, 3, "m"): [
            (0, [o]),
            (1, [at(mended, 1, "a")]),
            crownless,
            (3, [at(mended, 3, "m")]),
        ],
        at(rival, 0, "c"): [(0, [at(rival, 0, "c")])],
        **{n: [(0, [at(rival, 0, "c")]), (1, [n])] for n in (at(rival, 1, "e"), at(rival, 1, "f"))},
        at(first, 1, "i"): [(0, [at(rival, 0, "c")]), (1, [at(first, 1, "i")])],
        at(first, 2, "n"): [
            (0, [at(rival, 0, "c")]),
            (1, [at(first, 1, "i")]),
            (2, [at(first, 2, "n")]),
        ],
        # Each read on the line it ran on: g on the rival's round 1 as the second's cut found it.
        **{
            n: [(0, [at(rival, 0, "c")]), (1, [at(rival, 1, "e")]), (2, [n])]
            for n in (at(rival, 2, "g"), at(rival, 2, "j"))
        },
        at(second, 3, "h"): [
            (0, [at(rival, 0, "c")]),
            (1, [at(rival, 1, "e")]),
            (2, [at(rival, 2, "g")]),
            (3, [at(second, 3, "h")]),
        ],
        **{
            n: [(0, [at(steered, 0, "s")]), (1, [n])]
            for n in (at(steered, 1, "t"), at(steered, 1, "t2"), at(steered, 1, "t4"))
        },
        at(steered, 2, "t3"): [
            (0, [at(steered, 0, "s")]),
            (1, [at(steered, 1, "t2")]),
            (2, [at(steered, 2, "t3")]),
        ],
        at(glance, 0, "u"): [(0, [at(glance, 0, "u")])],
        at(peek, 0, "u2"): [(0, [at(peek, 0, "u2")])],
    }
    assert {
        e.address: [(s.round, s.candidates) for s in e.facts.main_line]
        for e in entries_under(tree)
        if isinstance(e, HierarchyCandidate)
    } == lines, "a candidate's line reads a round off a course that does not own it"
    courses = {e.address: e for e in entries_under(tree) if isinstance(e, SubtreeCourse)}
    hangs = {
        family: (None, o, {0: None, 1: o, 2: None}),
        kid: (first_pick, o, {2: first_pick}),
        grand: (first_pick, o, {3: first_pick}),
        great: (x, o, {4: x}),
        # Round 2 lists only t3, re-run away: it re-read nothing its head still holds.
        steered: (o, at(steered, 0, "s"), {0: None, 1: at(steered, 0, "s"), 2: None}),
        rewound: (o, o, {1: o}),
        redone: (first_pick, o, {3: first_pick}),
        branched: (b, at(branched, 0, "p"), {0: None}),
        # A steer naming a round's re-read hangs from the candidate that round re-read, lifted or not.
        rerun: (first_pick, at(rerun, 0, "r"), {0: None}),
        relift: (o, at(relift, 0, "l"), {0: None}),
        # It hangs from the parent line's arm; each re-read is its own round's, off its own crowns.
        mended: (first_pick, o, {1: o, 3: at(mended, 1, "a")}),
        carried: (at(rewound, 1, "w"), o, {2: at(rewound, 1, "w")}),
        rival: (o, at(rival, 0, "c"), {0: None, 1: at(rival, 0, "c"), 2: at(rival, 1, "e")}),
        first: (at(rival, 0, "c"), at(rival, 0, "c"), {1: at(rival, 0, "c"), 2: at(first, 1, "i")}),
        # Cut on g, which its parent has since re-run away: the cut still names g.
        second: (at(rival, 2, "g"), at(rival, 0, "c"), {3: at(rival, 2, "g")}),
        glance: (at(steered, 0, "s"), at(glance, 0, "u"), {0: None}),
        # The live round names its re-read only once it elects.
        peek: (at(steered, 2, "t3"), at(peek, 0, "u2"), {0: None, live: None}),
    }
    for hop, (cut_from, starts, references) in hangs.items():
        course = courses[course_of(hop)]
        assert (course.facts.from_candidate, course.facts.origin) == (cut_from, starts), (
            f"{hop.cycle_id} hangs from, or starts at, a candidate its mint record does not name"
        )
        assert {
            rnd.facts.round: rnd.facts.reference
            for rnd in course.children
            if isinstance(rnd, HierarchyRound)
        } == references, f"{hop.cycle_id} names a re-read its line did not stand on"
    by_addr = {e.address: e for e in entries_under(tree) if isinstance(e, HierarchyCandidate)}
    assert {
        address: tag.value
        for address, e in by_addr.items()
        if (tag := e.tag("superseded")) is not None
    } == {
        # Off the line of ANY supersede fork the line passed through that reached it, named by the
        # lowest cut, the nearest link of a tie: the rewind takes the trunk's round 1 off its line,
        # `kid` its round 2 though `carried`, cut from `kid` at the same round, reached it too.
        **dict.fromkeys((at(family, 1, n) for n in "ab"), course_of(rewound)),
        **dict.fromkeys((at(family, 2, n) for n in "az"), course_of(kid)),
        at(kid, 2, "y"): course_of(carried),
        x: course_of(mended),
        at(rewound, 1, "k"): course_of(carried),
        # The second's line stands on g, and the first still retires it.
        **dict.fromkeys(
            (at(rival, r, n) for r, n in ((1, "e"), (1, "f"), (2, "g"), (2, "j"))), course_of(first)
        ),
        # Re-run away by their own course, whether or not a line stands on them.
        at(rewound, 1, "w"): course_of(rewound),
        **dict.fromkeys(
            (at(steered, r, n) for r, n in ((1, "t"), (1, "t2"), (2, "t3"))), course_of(steered)
        ),
    }, "a cut retires an arm on its line, one past the reach of its branch, or names another cut"
    on_a_line, on_none = (
        "Rewound: its course re-ran this round in place after a fork was cut from it. It stays as "
        "the record of what ran" + tail
        for tail in (", and a fork's line stands on it.", ".")
    )
    assert {addr: by_addr[addr].lede for addr in (at(rewound, 1, "w"), at(steered, 1, "t"))} == {
        at(rewound, 1, "w"): on_a_line,
        at(steered, 1, "t"): on_none,
    }, "a rewound arm says a fork's line stands on it where none does, or hides one that does"
    # The mask reads the same lines: a lifted round keeps the crown its parent elected, and a
    # parting dims every fork whose start was proposed past it, a steer from a later arm included.
    mask = load_mask_record(stores, family.campaign_id, lens=None)
    crowned = {
        c.cycle_id: {
            r.round: next((m.key for m in r.candidates if m.is_selected), None) for r in c.rounds
        }
        for c in mask.cycles
    }
    assert crowned[great.cycle_id] == {
        0: ident("o"),
        1: ident("a"),
        2: None,
        3: ident("x"),
        4: ident("v"),
    }, "the mask crowns a lifted round other than where its parent elected it"
    parted = find_divergences(
        mask,
        lambda rnd: VerdictOutcome(diverged=(rnd.cycle_id, rnd.round) == (grand.cycle_id, 0)),
    )
    # `relift` named round 1's re-read of o, which its mint records as o: proposed AT the parting.
    assert {cid for cid, _ in parted.divergent} == {
        h.cycle_id for h in (grand, great, rewound, redone, branched, rerun, mended, carried)
    }, "a parting dims a fork rooted before it, or keeps one proposed past it"

    # The operator's `--from` rewinds the line's first cycle; a rebase's fork stands on its own cut.
    rewinds: list[int | None] = []
    rebase = RebaseRequest(
        fork_from_round=5,
        trigger=ForkTrigger.OPTIMIZER_REBASE,
        reason="",
        issued_by=SYSTEM_PRINCIPAL,
    )

    async def one_cycle(prep: Any, *, mode: RunMode, rebases: int, **_: Any) -> Any:
        rewinds.append(mode.from_round)
        return mock.Mock(fork=None if rebases else rebase)

    monkeypatch.setattr(entry, "_run_single_cycle", one_cycle)
    monkeypatch.setattr(
        entry, "_mint_and_rebase_fork", lambda prep, **kw: (prep, kw["session"], kw["observers"])
    )
    asyncio.run(
        entry._run_line(
            mock.Mock(),
            dataset=[],
            session=mock.Mock(),
            observers=mock.Mock(),
            mode=RunMode(from_round=3),
            step_rounds=None,
        )
    )
    assert rewinds == [3, None], "a rebase's fork re-applied the rewind to rounds it inherited"
