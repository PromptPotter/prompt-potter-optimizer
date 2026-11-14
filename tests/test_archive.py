"""Lost or corrupted measurement.

Owns `infrastructure/store/measurement_archive.py` and `archive_queries.py`,
`application/maintenance/`, `application/bench/resume_and_fork/`, `application/origin.py` and
`application/runner/loop.py`. A
replay served to the wrong content, a paid row dropped, a fork that inherits the wrong origin.
"""

from __future__ import annotations

import asyncio
import contextvars
import dataclasses
import json
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NamedTuple
from unittest import mock

import pytest
from factories import (
    LLM_ONLY,
    PROMPT_INFO,
    Configs,
    archive_row,
    arm_state,
    arm_walk,
    close_round,
    defaulted_config,
    degradation_health,
    election,
    filed,
    filed_under,
    inner_sandbox,
    loop_session,
    measurement,
    measurements,
    mint_fork,
    minted_campaign,
    minted_dataset,
    optimizer_state,
    pipeline_schema,
    prompted,
    samples,
    score_summary,
    scorer_of,
    seat,
    set_cycle,
    sheet,
    spend_book,
    stored_campaign,
    workspace,
)

from promptpotter.application import optimizers
from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.bench.difficulty import ruler_rows
from promptpotter.application.bench.node_context import NodeContext
from promptpotter.application.bench.resume_and_fork.fork_siblings import (
    cut_operator_fork,
    fork_edge,
)
from promptpotter.application.bench.resume_and_fork.repair import apply_correction
from promptpotter.application.bench.resume_and_fork.replayers import replay_all_mismatches
from promptpotter.application.bench.resume_and_fork.resume import resume_with_divergence_check
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.commands.launching import parse_cycle_seed
from promptpotter.application.cycle_reads import feed_versions, open_family_ray
from promptpotter.application.hierarchy import list_hierarchy
from promptpotter.application.hierarchy_subtree import (
    hierarchy_subtree,
    subtree_course,
    subtree_version,
)
from promptpotter.application.initialization.session import LaunchClock
from promptpotter.application.jobs.launcher.admission import request_jobs
from promptpotter.application.jobs.launcher.launch import Admitted, Inline
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.maintenance.archive_maintenance import (
    compact_measurement_archive,
    reindex_measurement_archive,
    restore_measurement_archive,
)
from promptpotter.application.mask.divergence import VerdictOutcome, find_divergences
from promptpotter.application.mask.load import load_mask_record
from promptpotter.application.optimizers.capo.members import (
    CapoObjective,
    PairedTCut,
    PopulationKept,
)
from promptpotter.application.optimizers.gepa.members import MinibatchTested
from promptpotter.application.optimizers.nodes import MeasuredRound, Panel, ReadArm
from promptpotter.application.optimizers.potter.election import RoundWinner, elect_on_theta
from promptpotter.application.optimizers.potter.pobb import EliminationGate
from promptpotter.application.optimizers.potter.race import EliminationCut, PoBBRace
from promptpotter.application.origin import (
    resolve_origin_opt_search_point,
    try_inherit_fork_origin,
)
from promptpotter.application.pipeline_resolve import (
    cycle_config,
    permitted_narrowing,
    steer_verdict,
)
from promptpotter.application.runner.inner.tasks import inner_spawn_of
from promptpotter.application.runner.loop import LoopEnd, open_origin_round, run_round_loop
from promptpotter.application.runner.round import run_init
from promptpotter.application.scoring import query_loop, search_point_scorer
from promptpotter.application.scoring.closed_rounds import closed_rounds
from promptpotter.application.scoring.measurement_log import open_cell
from promptpotter.application.scoring.search_point_scorer import replayable_on
from promptpotter.application.served_dashboard import served_dashboard
from promptpotter.config.paths import MEASUREMENTS_DIR
from promptpotter.domain.arms import ArmSlot, PointContext
from promptpotter.domain.bench import DatasetSplit, PartitionRecord, partition_bank
from promptpotter.domain.campaign_config import (
    seed_layer,
)
from promptpotter.domain.command_kinds import (
    DeleteCampaignPayload,
    Door,
    ForkCyclePayload,
    ForkSeed,
)
from promptpotter.domain.crowning_bar import CrowningBar
from promptpotter.domain.cycle_paths import (
    CycleDir,
    CycleHop,
    Subject,
)
from promptpotter.domain.hierarchy import (
    HierarchyCampaign,
    HierarchyCandidate,
    HierarchyEntry,
    HierarchyRound,
    SubtreeCampaign,
    SubtreeCourse,
    entries_under,
)
from promptpotter.domain.job import CampaignLaunch
from promptpotter.domain.optimizer_state import Critique
from promptpotter.domain.origin_panel import OverlapReading
from promptpotter.domain.paired_reading import ArmKey, ReadingState, RoundArm
from promptpotter.domain.phases import PauseCause, RunPhase, StopReason
from promptpotter.domain.pipeline_params import PipelineParams
from promptpotter.domain.pipeline_schema import PipelineNode
from promptpotter.domain.results import RoundResult
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.run_records import (
    AnnouncedArms,
    ArmRecord,
    ConfigOverrides,
    CycleSeed,
    CycleShareRecord,
    CycleSupersededRecord,
    ForkDirection,
    ForkSpec,
    ForkTrigger,
    OriginGatePass,
    OriginSource,
    ResumeCheckpointRecord,
    RoundEnteredRecord,
    RoundProposedRecord,
    RunPhaseRecord,
    SpawnedBy,
)
from promptpotter.domain.run_standing import line_reference, walked_slot
from promptpotter.domain.sample import ArchiveEntry, Bank, Sample
from promptpotter.domain.scoring import GRADE_KEYS, MeasuredCell
from promptpotter.domain.search_point import JobSearchPoint, TaskDecomposition
from promptpotter.domain.spend import Budget, SpendRollup
from promptpotter.infrastructure import producer_lock
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store import archive_queries, measurement_archive
from promptpotter.infrastructure.store.archive_queries import (
    ArmOf,
    CampaignWalks,
    OpenAt,
    SampleFoldRow,
    bench_reads,
    list_populations,
    load_population,
    sample_fold_rows,
    write_sample_fold,
)
from promptpotter.infrastructure.store.dataset_access import DEFINITION_FILE
from promptpotter.infrastructure.store.io import (
    append_jsonl,
    probe_lock,
    rewrite_lines,
    take_lock,
)
from promptpotter.infrastructure.store.layout import CycleLayout, campaigns_root_dir_for
from promptpotter.infrastructure.store.measurement_archive import (
    ArchiveFile,
    MeasurementArchive,
    ReplayFeed,
    config_key,
    standing,
)
from promptpotter.infrastructure.store.session_pointer import read_active_pointer
from promptpotter.infrastructure.store.stores import Stores, inner_sandbox_store, owned_campaign
from promptpotter.shared.errors import (
    ConflictError,
    FileKind,
    FileUnreadableError,
    LedgerUnreadableError,
    PayloadInvalidError,
    PotterError,
    ResumeDivergenceError,
)
from promptpotter.shared.identity import SYSTEM_PRINCIPAL
from promptpotter.shared.measurement_context import MeasurementRole
from promptpotter.shared.run_scope import rescope, scoped

_CAMPAIGN = "testds__20260101-000000"


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
        nodes=[PipelineNode(name="solve", tunes_llm=False, prompt_info=PROMPT_INFO)],
    )
    session = loop_session(
        workspace(root),
        schema,
        panel if bank is None else bank,
        scorer=scorer_of(per_sample, per_cell),
    )
    sp = PointContext(schema=schema, framing=TaskDecomposition(), demo=[]).job(
        prompted(instruction="Answer.")
    )

    async def _measure(sample: Sample, _session: Any, _pipeline_params: Any) -> MeasuredCell:
        predicted = "b" if sample.id % 2 else "a"
        return MeasuredCell(
            sample_key=sample.key,
            query=sample.query,
            ground_truth="a",
            predicted=predicted,
        )

    measured = None if ledger is None else "arm"
    with (
        scoped(ledger=CycleEventLog(root / "walk.jsonl" if ledger is None else ledger)),
        mock.patch.object(query_loop, "measure_sample", _measure),
    ):
        walked = asyncio.run(
            search_point_scorer.score_search_point(
                sp, panel, session, kind=MeasurementRole.PANEL, measured=measured
            )
        )
    return session, walked


def test_a_filed_cell_replays_by_its_content_and_reads_as_a_fresh_run_would(tmp_path: Path) -> None:
    archive = MeasurementArchive(tmp_path / "cells")
    # Filed LAST on purpose: wrongly served, it is the cell's most recent and no answer hides it.
    chain: Configs = [(n, {}) for n in ("l1_generate", "l1_critique", "l2_context", "l3_plan")]
    for predicted, terminal in (("short_circuit", "l1_critique"), ("full_chain", "l3_plan")):
        filed(
            archive,
            [archive_row("q", predicted=predicted, pipeline_data={"terminal_node": terminal})],
            filed_under(chain, "promptpotter-self"),
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

    for name, grade, terminal, dataset_hash in (
        ("clean", "A", "llm_only", "aime"),
        ("connector", "C", "token_matching", "aime"),
        ("aime_14", "A", "llm_only", "aime"),
        ("justlogic_14", "A", "llm_only", "justlogic"),
    ):
        row = archive_row(f"q_{name}", predicted="p", pipeline_data={"terminal_node": terminal})
        filed(archive, [row], filed_under(dataset_hash=dataset_hash), grade=grade)
    reusable = ReplayFeed(archive, LLM_ONLY).advance()
    assert {b.cell.query for b in reusable.values()} == {
        "q_clean",
        "q_aime_14",
        "q_justlogic_14",
    }, "a grade-C answer was replayed"
    # A later diagnostic rescore (B) or remeasure never displaces the answer a run measured first.
    feed = ReplayFeed(archive, LLM_ONLY)
    for at, said, grade in ((1, "measured", "A"), (2, "rescored", "B"), (3, "again", "A")):
        row = archive_row("q_rescored", predicted=said, pipeline_data={"terminal_node": "llm_only"})
        filed(archive, [row], filed_under(), grade=grade, created_at=f"2026-05-19T00:0{at}")
        moved = [b.cell.predicted for b in feed.advance().values() if b.cell.query == "q_rescored"]
        assert moved == (["measured"] if at == 1 else []), (
            f"the replay served {moved} once {said!r} was filed: a later row displaced the first"
        )
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

    # Two walks never buy one cell: the holder files the answer before its claim retires, and the
    # waiter that takes the claim next replays it; a second release frees nobody's claim.
    claims = MeasurementArchive(tmp_path / "cells")
    holder, waiter = (ReplayFeed(claims, LLM_ONLY) for _ in range(2))
    row = archive_row("q_held")
    held = holder.claim(row.sample_key)
    assert held is not None and waiter.claim(row.sample_key) is None, "two walks bought one cell"
    beside = waiter.claim(archive_row("q_beside").sample_key)
    assert beside is not None, "one cell's claim held another sample of its configuration"
    filed(claims, [row])
    held.retire()
    taken = waiter.claim(row.sample_key)
    assert taken is not None and row.sample_key in waiter.advance(), "the waiter lost the answer"
    held.retire()
    assert holder.claim(row.sample_key) is None, "a second release freed the next holder's claim"
    taken.retire()
    beside.retire()

    # Rows banked under one formula read under another exactly as a fresh run would.
    wider_panel = samples(10)
    panel = wider_panel[:6]
    banked_under = ("label_match(predicted, ground_truth)", None)
    read_under = ("0.5 * label_match(predicted, ground_truth)", "0.5 * fitness + 0.25")
    shared, banked_walk = _walk_panel(tmp_path / "shared", panel, *banked_under, bank=wider_panel)
    assert all(GRADE_KEYS & r.keys() for r in banked_walk.sheet.wire()), "guard: the walk graded"
    banked = archive_queries.walked_answers(shared.store, banked_walk.cells)
    assert len(banked) == len(panel), "a walked answer was not banked"
    signatures = shared.store.archive.cell_signatures()
    _, fresh = _walk_panel(tmp_path / "fresh", panel, *read_under)
    worth = {c.key: c.grade.objective for c in fresh.sheet}
    assert len(set(worth.values())) == 2, "guard: hits and misses are worth different amounts"

    _, replayed = _walk_panel(tmp_path / "shared", panel, *read_under, bank=wider_panel)
    assert all(c.facts.cached for c in replayed.sheet), "guard: the second read bought nothing"
    ruler_cells = {key: y for _, key, y in ruler_rows(shared.store, [("x", replayed.sheet)], None)}
    assert ruler_cells == pytest.approx(worth), "the ruler read the banked formula's grades"
    assert [c[:2] for c in replayed.cells] == [c[:2] for c in banked_walk.cells]
    assert shared.store.archive.cell_signatures() == signatures, "a replay filed a second answer"
    assert {c.key: c.grade.objective for c in replayed.sheet} == worth
    assert replayed.scores.composite_fitness == pytest.approx(fresh.scores.composite_fitness)


# 2. The archive on disk


@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_the_archive_on_disk_keeps_every_paid_answer(built_stores: Stores) -> None:
    archive = built_stores.archive
    entry = filed_under(LLM_ONLY, "kept")
    other: Configs = [("llm_only", {"model": "Y"})]
    refs: list[str] = []
    for at, said, configs, facts in (
        (1, "first", LLM_ONLY, {}),
        (2, "second", LLM_ONLY, {}),
        (3, "ERROR", LLM_ONLY, {"error_category": "transient"}),
        (4, "elsewhere", other, {}),
    ):
        row = MeasuredCell.from_wire({"sample_key": "k1", "predicted": said, **facts})
        refs += [
            ref
            for _, ref, _ in filed(
                archive, [row], filed_under(configs, "kept"), created_at=f"2026-05-19T00:{at:02}"
            )
        ]
    assert len(set(refs)) == 4
    held = archive.population(entry)
    assert [a.cell.predicted for a in held] == ["first", "second", "ERROR"], "an answer was dropped"
    assert standing(held)["k1"].cell.predicted == "second", (
        "an errored answer stood over a graded one"
    )
    described = {e.config_key for e in archive.list_all(dataset_hash="kept")}
    assert described == {config_key(LLM_ONLY), config_key(other)}
    index = archive.path(ArchiveFile.INDEX)
    index.write_bytes(b"")
    assert archive.reindex(apply=False) == {"indexed": 2, "undescribed": 0}
    assert index.read_bytes() == b"", "a reindex that was not asked to apply wrote the index"
    assert archive.reindex(apply=True) == {"indexed": 2, "undescribed": 0}
    assert {e.config_key for e in archive.list_all(dataset_hash="kept")} == described
    assert archive.population(entry) == held, "reindex destroyed an answer"
    assert all(archive.answer(ref) is not None for ref in refs), "reindex lost an address"


_HOLD_CYCLE = """
import sys
from pathlib import Path
from promptpotter.infrastructure.store.io import take_lock
assert take_lock(Path(sys.argv[1]), wait_s=0) is not None
print("held", flush=True)
sys.stdin.read()
"""


def test_a_reindex_beside_a_run_in_another_process_refuses_naming_its_cycle(
    built_stores: Stores,
) -> None:
    """A row the run lands mid-rewrite lands in no index: its writer is asked, never raced."""
    cycle_dir = built_stores.campaigns.cycle_dir(CycleHop(campaign_id="camp", cycle_id="cyc"))
    layout = CycleLayout(cycle_dir)
    layout.ledger.parent.mkdir(parents=True)
    layout.ledger.touch()
    run = subprocess.Popen(
        [sys.executable, "-c", _HOLD_CYCLE, str(layout.producer_lock)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert run.stdin is not None and run.stdout is not None
    try:
        assert run.stdout.readline().strip() == "held"
        with pytest.raises(ConflictError) as refused:
            reindex_measurement_archive(built_stores)
        assert refused.value.code == "archive_writers"
        assert "cyc, held by its run" in str(refused.value), "the refusal named no writer"
    finally:
        run.stdin.close()
        run.wait()
    assert reindex_measurement_archive(built_stores, apply=True) == {"indexed": 0, "undescribed": 0}


# Each step reads on from where the feed's last advance left it.
@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_a_reader_tailing_a_cell_file_rewritten_under_it_skips_no_answer(
    built_stores: Stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = built_stores.archive
    tailed: Configs = [("llm_only", {"model": "T"})]
    reader = MeasurementArchive(archive.base_dir)
    feed = ReplayFeed(reader, tailed)

    def _bank(sid: int, dataset_hash: str = "reidx", via: MeasurementArchive = archive) -> None:
        cell = MeasuredCell.from_wire(
            {
                "sample_key": f"k{sid}",
                "predicted": "p",
                "pipeline_data": {"reasoning_trace": "T" * 400},
            }
        )
        filed(via, [cell], filed_under(tailed, dataset_hash))

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
    archive.reindex(apply=True)
    _bank(5, dataset_hash="reidx-wide")
    assert reader.entry(file_key, "reidx-wide") is not None, "an index entry was skipped"
    # The other process files between this writer's look at the file and its own append.
    own = ReplayFeed(archive, tailed)
    own.advance()

    def raced(path: Path, *rows: dict[str, Any]) -> range:
        monkeypatch.setattr(measurement_archive, "append_jsonl", append_jsonl)
        _bank(6, via=reader)
        return append_jsonl(path, *rows)

    monkeypatch.setattr(measurement_archive, "append_jsonl", raced)
    _bank(7)
    assert set(own.advance()) == {"k6", "k7"}, "an answer another process filed was stepped over"
    _bank(8)
    assert set(own.advance()) == {"k8"}
    # A writer that died mid-append leaves a torn last line: the next append cuts it and lands whole.
    with cells.open("ab") as torn:
        torn.write(b'{"sample_key": "k_torn", "predi')
    _bank(9)
    assert set(own.advance()) == {"k9"}, "an answer filed past a crashed append was lost"
    assert cells.read_bytes().endswith(b"}\n"), "the torn bytes merged into the next answer"
    assert archive.detail_lines(file_key), "the cell file stopped reading after a crash"


@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_four_writers_on_one_file_at_once_keep_every_row(built_stores: Stores) -> None:
    # Windows runs `O_APPEND` as seek-then-write, so what keeps every row is the lock under
    # `append_jsonl`, taken on a fresh handle per append.
    racing = built_stores.archive.base_dir / "racing.jsonl"
    writers = [
        threading.Thread(
            target=lambda w=w: [append_jsonl(racing, {"w": w, "n": n}) for n in range(50)]
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


class _Compacted(NamedTuple):
    configs: Configs
    bank: Bank
    refs: dict[str, str]
    whole: ArchiveEntry


@pytest.fixture
def compacted_rows(built_stores: Stores) -> _Compacted:
    """The bank named `compacted` and its two rows, each holding every field compaction moves;
    TWO filings, so a restore clears each stamp."""
    archive = built_stores.archive
    compacted: Configs = [("llm_only", {"model": "C"})]
    compacted_bank = Bank.of(Sample(id=0, query=f"q{sid}", ground_truth="g") for sid in (1, 2))
    compacted_hash = compacted_bank.dataset_hash
    built_stores.tenant_datasets.put_bank(compacted_bank)
    built_stores.tenant_datasets.relabel("compacted", compacted_hash)
    compacted_refs: dict[str, str] = {}
    whole = filed_under(compacted, compacted_hash)
    for sid in (1, 2):
        row = archive_row(
            f"q{sid}",
            predicted="p",
            pipeline_data={
                "terminal_node": "llm_only",
                "diagnostics": {"warnings": []},
                "step_tokens": {"llm_only": {"input": 10, "output": 5}},
                "step_timings": {"llm_only": 0.5},
                "reasoning_trace": "T" * 3000,
                "result_ranking": [1, 2, 3],
                "final_ranking": [1, 2, 3],
                "total_time": 1.25,
            },
        )
        [(_, compacted_refs[f"q{sid}"], _)] = filed(archive, [row], whole)
    return _Compacted(compacted, compacted_bank, compacted_refs, whole)


@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_compaction_round_trips_every_field_it_moved(
    built_stores: Stores, compacted_rows: _Compacted
) -> None:
    archive, whole = built_stores.archive, compacted_rows.whole
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


@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_a_controlled_arm_remembers_only_the_answers_its_campaign_walked(
    built_stores: Stores,
) -> None:
    archive = built_stores.archive
    ds = Bank.of(Sample(id=0, query=f"k{i}", ground_truth="g") for i in range(2))

    def individual(name: str) -> ArchiveEntry:
        return filed_under([("", {"individual": name})], ds.dataset_hash, prompt_fields_id=name)

    def bank(name: str, said: list[str], at: int) -> list[str]:
        cells = [
            MeasuredCell(sample_key=ds.samples[i].key, predicted=p) for i, p in enumerate(said)
        ]
        return [
            ref
            for _, ref, _ in filed(
                archive, cells, individual(name), created_at=f"2026-09-28T00:00:{at:02d}Z"
            )
        ]

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
        rescope(memory=ArmOf(CampaignWalks(campaigns, "arm"), ruler_version=0))
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


# The re-cut moves the name the relabel placed on the bank.
@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_a_relabel_or_a_recut_moves_a_name_and_every_paid_answer_reads_on(
    built_stores: Stores, compacted_rows: _Compacted
) -> None:
    archive, campaigns = built_stores.archive, built_stores.campaigns
    compacted, compacted_bank, compacted_refs, whole = compacted_rows
    compacted_hash = compacted_bank.dataset_hash

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
        "pipeline:\n  backend_type: termnorm\n"
        "campaign:\n  scoring: label_match(predicted, ground_truth)\n"
        "  optimization:\n    degradation_threshold: 0.4\n",
        encoding="utf-8",
    )
    opened = asyncio.run(open_cell(built_stores, "compacted-renamed", compacted_refs[q1.query]))
    assert opened.position == compacted_bank.slot_of[q1.key], (
        "an old answer opened at another bank's slot"
    )
    assert bench_reads(built_stores, bank=recut, sample_keys=frozenset({q2.key})) == 0, "guard"
    filed(
        archive,
        [MeasuredCell(sample_key=q2.key, predicted="g")],
        whole,
        role=MeasurementRole.BENCH,
        created_at="2026-05-19T00:00:00Z",
    )
    assert bench_reads(built_stores, bank=recut, sample_keys=frozenset({q2.key})) == 1, (
        "a holdout read under the bank before a re-cut was not counted on the re-cut"
    )


# 3. Replayed decisions, and forks


def _round(**kw: Any) -> RoundResult:
    return RoundResult(
        **{
            "reference": None,
            "folds": {},
            "election": None,
            "overlap": OverlapReading.unpaired(ReadingState.NOT_HELD, 0, False),
            "health": degradation_health(),
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
    """The mint record alone, cut at the parent's head: what `cut_fork` writes for a sibling."""
    child = parent.model_copy(update={"cycle_id": cycle_id})
    parent_dir = store.cycle_dir(parent)
    mint_fork(
        store,
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
    resolved_params: dict[str, Any] | None = None,
) -> str:
    n = len(closed)
    pick = prompted(instruction=name)
    key = ArmKey(pick.id)
    arm = arm_state(
        name,
        slot=ArmSlot(round=n, idx=0, total=1),
        individual=pick,
        resolved_params=resolved_params or {},
    )
    rr = _round(round=n, arms={key: arm}, election=election(key if crowned else None, ended_on=key))
    closed.append(rr)
    close_round(ledger, closed, cycle=closing)
    return pick.id


def _on_parent() -> dict[str, Any]:
    """A round whose arm and election end on the individual named `parent`."""
    parent = prompted(instruction="parent")
    return {
        "arms": {ArmKey(parent.id): arm_state("parent", individual=parent)},
        "election": election(ended_on=parent.id),
    }


def _adopted() -> RoundResult:
    """Round 0, electing the origin."""
    origin_slot = ArmSlot(round=0, idx=0, total=1)
    return _round(
        round=0,
        arms={ArmKey("origin"): arm_state("origin", slot=origin_slot)},
        election=election("origin"),
    )


# The absent-arm replays read the cut this race records.
@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_a_cut_the_live_race_made_past_an_errored_cell_replays_on_the_cells_it_was_fit_on(
    built_stores: Stores,
) -> None:
    stores = built_stores
    parent = prompted(instruction="parent")
    hits = sheet(measurements([1.0] * 10))
    racing = seat(
        stores,
        round_num=3,
        optimization={"elimination_n_min": 4, "nodes": {"pobb": {"config": {"epsilon": 0.30}}}},
    )
    on_parent = _on_parent()
    # The frontier holds every cell, answered by an individual that is not the parent.
    line = [_round(round=0, cells={ArmKey("origin"): hits}, **on_parent)]
    set_cycle(racing.cycle, line=line)
    ten = samples(10, "g")

    def race_of() -> PoBBRace:
        return PoBBRace(
            NodeContext(racing, optimizers.NODE_MEMBERS.get("pobb")),
            Panel(cells=ten, order=ten, block_size=len(ten)),
        )

    walked = sheet(
        measurement(i, None, error="boom", error_category="transient")
        if i == 2
        else measurement(i, 1.0 if i == 4 else 0.0)
        for i in range(8)
    )
    assert not race_of().close({ArmKey("c1"): walked.cells}), (
        "the parent's prior borrowed cells another individual answered"
    )
    set_cycle(
        racing.cycle, line=[*line, _round(round=1, cells={ArmKey(parent.id): hits}, **on_parent)]
    )
    race = race_of()
    stop = race.close({ArmKey("c1"): walked.cells}).get(ArmKey("c1"))
    reading = race.judge(stop, ArmKey("c1"), {ArmKey("c1"): "C3.1"})
    assert reading is not None and reading.context["gate"] == EliminationGate.EPSILON
    [cut] = racing.cycle.pending_decisions
    assert isinstance(cut.inputs_ref, EliminationCut)
    assert cut.inputs_ref.candidate_cells == [str(i) for i in range(8) if i != 2]
    assert replay_all_mismatches(_round(round=3, cells={ArmKey("c1"): walked}), [cut]) == [], (
        "the cut the live rule made is not the cut its own record replays"
    )
    # Every replayer that reads an arm replays an absent one as no cut, no gain and no winner.
    unwalked, bar_arm = ArmKey("c1"), ArmKey(parent.id)
    paired = PairedTCut(
        objective=CapoObjective(length_penalty=0.0, length_norm=1),
        arm=unwalked,
        queries_scored=8,
        alpha=0.05,
        survivors=1,
        raced_against=[bar_arm],
        outscored_by=[bar_arm],
    )
    minibatch = MinibatchTested(
        arm=unwalked, parent_id=parent.id, parent_scores={"g0": 0.0}, sigma=0.0, sigma_prime=1.0
    )
    winner = RoundWinner(
        arms=[unwalked],
        coverage_floor=1,
        bar=CrowningBar(statistic="posterior", admission=0.5),
        parent=bar_arm,
        crowned=[],
    )
    kept = PopulationKept(objective=paired.objective, survivors=[unwalked])
    for inputs, recorded in (
        (cut.inputs_ref, True),
        (paired, True),
        (minibatch, True),
        (winner, unwalked),
        (kept, [unwalked]),
    ):
        [gone] = replay_all_mismatches(
            _round(round=3, cells={}),
            [ResumeCheckpointRecord(kind=inputs.kind, inputs_ref=inputs, outcome=recorded)],
        )
        assert gone.current_outcome in (False, None, []), (
            f"{inputs.kind}: an absent arm's decision replayed"
        )
    # Two survivors walked on disjoint cells share no reading: neither is ranked above the other.
    chars = {"target_prompt_chars": 10}
    apart = sheet(measurements([1.0] * 4, sample_ids=[10, 11, 12, 13], pipeline_data=chars))
    bar = sheet(measurements([0.0] * 4, pipeline_data=chars))
    survived = PopulationKept(objective=paired.objective, survivors=[unwalked, bar_arm])
    [disjoint] = replay_all_mismatches(
        _round(round=3, cells={unwalked: apart, bar_arm: bar}),
        [ResumeCheckpointRecord(kind=survived.kind, inputs_ref=survived, outcome=[unwalked])],
    )
    assert disjoint.current_outcome == [], (
        "population_kept ranked arms that share no cell, reading each as a 0.0 mean"
    )


@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_a_parents_charged_error_replays_at_the_grade_the_live_election_read(
    built_stores: Stores,
) -> None:
    stores = built_stores
    on_parent = _on_parent()
    scorer = scorer_of()

    def answered_by(model: str, answers: dict[int, str | None]) -> Any:
        rows = [
            archive_row(f"q{sid}", predicted=said)
            if said is not None
            else archive_row(f"q{sid}", error="boom", error_category="PIPELINE")
            for sid, said in answers.items()
        ]
        walked = filed(
            stores.archive, rows, filed_under([("llm_only", {"model": model})], "replayds")
        )
        return scorer.sheet(
            a.cell for _, ref, _ in walked if (a := stores.archive.answer(ref)) is not None
        )

    # Two cells won outright; the third, which clears the bar, only the parent's charged error gives.
    answered = {sid: "g" if sid < 5 else "x" for sid in range(9)}
    parent, child = prompted(instruction="parent"), prompted(instruction="child")
    bar = answered_by("P", {**answered, 9: None})
    arm = answered_by("C", {**answered, 5: "g", 6: "g", 9: "g"})
    assert bar.cells[9].scored and bar.cells[9].grade.fitness == 0.0, "guard"
    electing = seat(stores)
    set_cycle(electing.cycle, line=[_round(round=0, **on_parent)], population=[parent])
    walked_child = ArmKey(child.id)
    child_arm = arm_state("child", slot=ArmSlot(round=1, idx=0, total=1), individual=child)
    read_parent = ReadArm(ArmKey(parent.id), arm_state("parent", individual=parent), bar)
    selection = elect_on_theta(
        NodeContext(electing, optimizers.NODE_MEMBERS.get("theta_election")),
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
    )
    assert selection.selected == walked_child, "guard: the live election charges the error"
    charged, ledger = _minted(stores, "cycle_charged_parent")
    close_round(
        ledger,
        [
            _adopted(),
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
    assert replay_all_mismatches(closed_on_disk, [decided]) == [], (
        "the replay read the parent's charged error as a cell nobody scored"
    )


class _ThreeRounds(NamedTuple):
    hop: CycleHop
    ledger: CycleEventLog
    closed: list[RoundResult]
    ids: list[str]
    closing: Cycle
    on_course: Callable[[], list[HierarchyCandidate]]
    served: Callable[[], list[tuple[str, bool, bool]]]
    selected: Callable[[], list[str]]


@pytest.fixture
def three_rounds(built_stores: Stores) -> _ThreeRounds:
    """A campaign's line closed through c0..c2, and its readers of what stands."""
    stores = built_stores
    closing = seat(stores).cycle
    hop = CycleHop(campaign_id="rewound__20260101-000000", cycle_id="cycle_rewound")
    stored_campaign(stores, minted_campaign(hop))
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

    return _ThreeRounds(hop, ledger, closed, ids, closing, on_course, served, selected)


@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_a_rewind_retracts_every_election_close_and_crown_past_it_for_every_reader(
    built_stores: Stores, three_rounds: _ThreeRounds
) -> None:
    stores = built_stores
    hop, _, _, ids, _, _, served, selected = three_rounds
    assert served() == [(i, True, True) for i in ids] and selected() == ["C2.1"] * 3, "guard"
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


@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_a_resume_from_3_that_halts_on_a_divergence_below_it_displaces_no_round_past_it(
    built_stores: Stores,
) -> None:
    stores = built_stores
    closing = seat(stores).cycle
    halted = CycleHop(campaign_id="halted__20260101-000000", cycle_id="cycle_halted")
    stored_campaign(stores, minted_campaign(halted))
    stores.campaigns.mint_cycle(halted)
    halted_ledger = CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(halted)))
    run: list[RoundResult] = []
    for name in ("h0", "h1", "h2"):
        _emit_round(closing, halted_ledger, run, name)
    edited = optimizer_state().model_copy(update={"prompt_hashes": {"l1_generate": "edited"}})
    for n in (3, 4):
        walked_h = prompted(instruction=f"h{n}")
        run.append(
            _round(
                round=n,
                arms={ArmKey(walked_h.id): arm_state(f"h{n}", individual=walked_h)},
                election=election(walked_h.id),
                optimizer_state=edited if n == 3 else optimizer_state(),
            )
        )
        close_round(halted_ledger, run, cycle=closing)
    resuming = seat(stores, hop=halted)
    resuming.cycle.session.state.resumed_from_round = stores.campaigns.rewind_enters(halted, 3)
    with (
        scoped(ledger=resuming.ledger, spawn=inner_spawn_of(resuming.cycle.session)),
        scoped(book=spend_book()),
        pytest.raises(ResumeDivergenceError),
    ):
        asyncio.run(resume_with_divergence_check(resuming.cycle, [], RunMode(from_round=3)))
    assert stores.campaigns.standing_rounds(halted, moment=None).last_closed == 4, (
        "a resume its divergence halted had already rewound the line"
    )


@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_a_reentered_rounds_arm_folds_only_its_new_walk(
    built_stores: Stores, three_rounds: _ThreeRounds
) -> None:
    stores = built_stores
    hop, ledger, closed, ids, *_ = three_rounds
    stores.campaigns.rewind_to_round(hop, 1)
    stores.campaigns.rewind_to_round(hop, 0)
    # Every live fold reads the rewind off its record.
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
    folds.enter(rewind.round)
    with pytest.raises(LookupError):
        folds.take(cell)
    folds.announce(walk)
    assert folds.fold(1, walk.arm) is None, (
        "a re-announced arm folded the cells of the pass a rewind retracted"
    )


@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_an_uncrowned_rerun_of_a_rewound_round_never_takes_the_stand(
    built_stores: Stores, three_rounds: _ThreeRounds
) -> None:
    stores = built_stores
    hop, ledger, closed, ids, closing, on_course, served, _ = three_rounds
    stores.campaigns.rewind_to_round(hop, 1)
    stores.campaigns.rewind_to_round(hop, 0)
    again = _emit_round(closing, ledger, closed[:1], "c1_again", crowned=False)
    assert served() == [(ids[0], True, True), (again, True, False)]
    origin, held = on_course()
    assert (origin.facts.stands, held.facts.stands) == (True, False), (
        "an uncrowned re-run took the stand"
    )


# An inode number outlives its file, so only the bytes tell the old file's tail from a new file.
@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_a_ledger_reminted_onto_its_freed_inode_grown_past_the_old_one_is_read_as_itself(
    built_stores: Stores, three_rounds: _ThreeRounds
) -> None:
    stores = built_stores
    hop, ledger, _, _, closing, *_ = three_rounds
    # A reader indexed the old file before it was replaced.
    stores.campaigns.standing_rounds(hop, moment=None)
    _, regrown = _minted(stores, "cycle_regrown")
    regrown_rounds: list[RoundResult] = []
    grown = [_emit_round(closing, regrown, regrown_rounds, f"d{n}") for n in range(6)]
    assert regrown.path.stat().st_size > ledger.path.stat().st_size, "guard"
    ledger.path.write_bytes(regrown.path.read_bytes())
    assert stores.campaigns.standing_rounds(hop, moment=None).crowns == dict(enumerate(grown)), (
        "a re-minted ledger was read as the tail of the file it replaced"
    )


# Disposable, so a re-run remedies it.
@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_an_export_this_build_cannot_read_is_refused_as_an_export(
    built_stores: Stores, three_rounds: _ThreeRounds
) -> None:
    stores, hop = built_stores, three_rounds.hop
    export_file = stores.campaigns.layout(hop).export
    export_file.write_text("{not an export", encoding="utf-8")
    with pytest.raises(FileUnreadableError) as unread_export:
        stores.campaigns.read_export(hop)
    assert unread_export.value.kind is FileKind.EXPORT, "an export was refused as another file"
    export_file.unlink()


# Never half-read; the remedy it names deletes the campaign, paused as it is.
@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_an_unreadable_close_refuses_its_cycle_to_every_reader_and_the_remedy_keeps_paid_rows(
    built_stores: Stores,
) -> None:
    stores = built_stores
    closing, scorer = seat(stores).cycle, scorer_of()
    # `rewrite_lines` writes a fresh file: a ledger index takes a same-inode growth for an append.
    stranded = CycleHop(campaign_id="stranded__20260101-000000", cycle_id="cycle_stranded")
    stored_campaign(stores, minted_campaign(stranded))
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
    paid = filed(
        stores.archive,
        [archive_row("q_stranded", predicted="p")],
        filed_under(dataset_hash="strandedds"),
    )
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
            CommandCall(DeleteCampaignPayload(campaign_id=stranded.campaign_id), door=Door.HOST)
        )
    )
    assert not stores.campaigns.campaign_root_dir(stranded.campaign_id).exists(), (
        "the remedy left the refused campaign standing"
    )
    assert all(stores.archive.answer(ref) is not None for _, ref, _ in paid), (
        "the remedy took a paid measurement with the campaign"
    )


@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_an_unmodified_fork_origin_inherits_the_branch_points_reading_and_an_edited_one_rescores(
    built_stores: Stores,
) -> None:
    stores = built_stores
    closing = seat(stores).cycle
    store = stores.campaigns
    prompt = {"instruction": "do the thing", "persona": "you are precise"}
    solving = pipeline_schema(
        [PipelineNode(name="solve", tunes_llm=False, prompt_info=PROMPT_INFO)]
    )
    inheriting, ledger = _minted(stores, "cycle_inherit_parent")
    close_round(
        ledger,
        [
            _adopted(),
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
        scorer=scorer_of(),
    )

    def inherit(fields: dict[str, str]) -> Any:
        # Resolved as `seat_campaign_origin` does: the fork seed wins.
        seed = CycleSeed(
            origin_prompts=prompted(**fields).prompts,
            origin_source=OriginSource.FORK_SEED,
        )
        resolved = resolve_origin_opt_search_point(
            seed=seed, pipeline_params=PipelineParams(), schema=solving
        )
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


class _Scenario(NamedTuple):
    hop: CycleHop
    log: CycleEventLog
    rounds: list[RoundResult]
    pick: ArmKey
    drawn: PartitionRecord
    unheld: tuple[RunPhaseRecord, CycleShareRecord]
    closing: Cycle


@pytest.fixture
def scenario_line(built_stores: Stores) -> _Scenario:
    """A paused line closed through s0, s1 on a drawn holdout, with a share the parent holds; its
    campaign permits model `m` on node `a`."""
    stores = built_stores
    closing = seat(stores).cycle
    store = stores.campaigns
    scenario, scenario_log = _minted(stores, "cycle_applyscenario")
    permits_m = defaulted_config(
        {
            "optimization": {"degradation_threshold": 0.4},
            "optimizer_narrowing": {"a": {"param_allowed_values": {"model": ["m"]}}},
        }
    )
    scenario_campaign = minted_campaign(scenario, config=permits_m)
    stored_campaign(stores, scenario_campaign)
    minted_dataset(stores, scenario_campaign)
    scenario_rounds: list[RoundResult] = []
    scenario_pick = ArmKey(
        [_emit_round(closing, scenario_log, scenario_rounds, f"s{n}") for n in range(2)][-1]
    )
    bank = samples(4, "g")
    drawn = partition_bank(bank, DatasetSplit(bench=0), seed=0).record(dataset_hash="bank")
    store.append(scenario, drawn)
    unheld = (
        RunPhaseRecord.stop(StopReason.PAUSED, cause=PauseCause.INTERRUPT),
        CycleShareRecord(allotment=Budget(usd=1.0)),
    )
    for record in unheld:
        store.append(scenario, record)
    return _Scenario(scenario, scenario_log, scenario_rounds, scenario_pick, drawn, unheld, closing)


# No supersede, no inbox void, no moved pointer, no stub; nor does a cut past the last close.
@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_a_rewind_its_launch_refuses_has_written_nothing_on_its_parent(
    built_stores: Stores, scenario_line: _Scenario
) -> None:
    stores, store, scenario = built_stores, built_stores.campaigns, scenario_line.hop

    # No backend answers this dataset, so the launch refuses.
    def parent_standing() -> tuple[object, ...]:
        index = store.load(scenario, moment=None)
        assert index is not None
        cycles = sorted(d.name for d in store.campaign_cycle_dirs(scenario.campaign_id))
        return cycles, read_active_pointer(store.workspace), index.final

    standing = parent_standing()
    for refused_round in (2, 99):
        with pytest.raises(PotterError):
            asyncio.run(
                CommandDispatcher(stores, inline=Inline()).dispatch(
                    CommandCall(
                        ForkCyclePayload(
                            campaign_id=scenario.campaign_id,
                            cycle_id=scenario.cycle_id,
                            round=refused_round,
                            keep_rounds=True,
                        ),
                        door=Door.HOST,
                    )
                )
            )
        assert store.last(scenario, CycleSupersededRecord) is None, (
            f"a refused rewind at round {refused_round} superseded its parent"
        )
        assert parent_standing() == standing, (
            f"a refused rewind at round {refused_round} left a stub or moved its parent"
        )


def _operator_fork(stores: Stores, hop: CycleHop, parsed: CycleSeed, **asked: Any) -> str:
    """An operator's fork of *hop* cut and taken, its seed *parsed*."""
    payload = ForkCyclePayload(campaign_id=hop.campaign_id, cycle_id=hop.cycle_id, **asked)
    return cut_operator_fork(stores, payload, parsed).take(stores)


# The steered forks cut from the applied scenario's fork, and from each other.
@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_an_applied_scenario_forks_at_its_round_keeps_the_holdout_and_carries_its_criterion(
    built_stores: Stores, scenario_line: _Scenario
) -> None:
    stores, store = built_stores, built_stores.campaigns
    scenario, _, _, scenario_pick, drawn, unheld, _ = scenario_line
    criterion = "0.9 * accuracy + 0.1 * (1 - mean_latency_s)"
    fork_id = _operator_fork(
        stores,
        scenario,
        CycleSeed(config_overrides=ConfigOverrides(scoring=criterion)),
        round=2,
        keep_rounds=True,
    )
    applied = scenario.model_copy(update={"cycle_id": fork_id})
    index = store.load(applied, moment=None)
    assert index is not None and index.fork is not None
    assert index.fork.issued_by.id == str(stores.identity.user_id), "the fork names no issuer"
    assert store.last(applied, PartitionRecord) == drawn, (
        "a redrawn holdout reads the fork's bench lift on rows its parent never held out"
    )
    for record in unheld:
        assert store.last(applied, type(record)) is None, (
            f"a fork read its parent's {type(record).__name__}: its phase or its share is the parent's"
        )
    # OPERATOR_REWIND is the rebase branch, the one that lifts rounds 0..N-1.
    assert (index.fork.trigger, index.cut_round, index.fork.from_candidate) == (
        ForkTrigger.OPERATOR_REWIND,
        2,
        RoundArm(round=1, arm=scenario_pick),
    ), "a rewind's record names another arm than the pick its parent's line stood on"
    seed = store.read_laid_spans(applied)[-1].seed
    assert seed is not None, "a rewind's seed rode no record of its own"
    base = defaulted_config({"optimization": {"degradation_threshold": 0.05}})
    assert base.overlaid(seed_layer(seed.config_overrides)).scoring == criterion
    assert base.scoring != criterion, "applying a scenario wrote through to its base config"
    # A steer outside what the campaign permits is the babysit act, and a fork of that fork lays
    # its seed over its parent's: a steer back inside the menu still runs babysat, and its mint
    # stamps the intervention every answer it files is graded by.
    campaign = owned_campaign(stores, scenario.campaign_id)

    def steered_fork(hop: CycleHop, model: str) -> CycleHop:
        seed = parse_cycle_seed(
            ForkSeed(pipeline_overlay={"a": {"model": model}}), stores, campaign, hop
        )
        cycle_id = _operator_fork(stores, hop, seed, round=2, keep_rounds=True)
        return hop.model_copy(update={"cycle_id": cycle_id})

    permitted = permitted_narrowing(stores, campaign)
    babysat = steered_fork(applied, "x")
    assert cycle_config(stores, campaign, babysat).steers_outside(permitted), (
        "an unpermitted steer forked clean"
    )
    back = steered_fork(babysat, "m")
    onward = cycle_config(stores, campaign, back)
    assert (onward.seed_overlay, onward.steers_outside(permitted), onward.config.scoring) == (
        {"a": {"model": "m"}},
        True,
        criterion,
    ), "a fork steering back inside the menu dropped what its line laid, or ran clean"
    for hop in (babysat, back):
        index = store.load(hop, moment=None)
        assert index is not None and index.human_intervened, (
            f"{hop.cycle_id} runs babysat and its mint stamped no intervention"
        )
    clean = store.load(applied, moment=None)
    assert clean is not None and not clean.human_intervened, "a clean fork was stamped babysat"


# With no edit it steers nothing and stamps no intervention.
@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_a_panel_fork_off_a_candidate_runs_its_record_and_sends_only_the_operators_edit(
    built_stores: Stores, scenario_line: _Scenario
) -> None:
    stores, store = built_stores, built_stores.campaigns
    scenario, scenario_log, scenario_rounds, *_, closing = scenario_line
    campaign = owned_campaign(stores, scenario.campaign_id)
    permitted = permitted_narrowing(stores, campaign)
    # Provider and route included.
    candidate_record = {"a": {"model": "m", "provider": "p", "route_order": ["r"]}}
    candidate_key = _emit_round(
        closing, scenario_log, scenario_rounds, "s2", resolved_params=candidate_record
    )
    no_edit = ForkSeed()
    assert steer_verdict(stores, permitted, no_edit.pipeline_overlay) == "clean"
    panel_fork = scenario.model_copy(
        update={
            "cycle_id": _operator_fork(
                stores,
                scenario,
                parse_cycle_seed(no_edit, stores, campaign, scenario),
                round=2,
                candidate_id=candidate_key,
            )
        }
    )
    panel_laid = cycle_config(stores, campaign, panel_fork)
    panel_index = store.load(panel_fork, moment=None)
    assert (panel_laid.seed_overlay, panel_laid.steers_outside(permitted)) == (
        candidate_record,
        False,
    ), (
        "a panel fork candidate_record other params than its candidate's record, or steered by running them"
    )
    assert panel_index is not None and not panel_index.human_intervened, (
        "a panel fork with no edit was stamped babysat"
    )


@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_a_field_cleared_in_the_panel_drops_the_candidates_value_and_the_layer_below_shows(
    built_stores: Stores, scenario_line: _Scenario
) -> None:
    stores = built_stores
    scenario, scenario_log, scenario_rounds, *_, closing = scenario_line
    campaign = owned_campaign(stores, scenario.campaign_id)
    candidate_record = {"a": {"model": "m", "provider": "p", "route_order": ["r"]}}
    candidate_key = _emit_round(
        closing, scenario_log, scenario_rounds, "s2", resolved_params=candidate_record
    )
    cleared = ForkSeed(pipeline_overlay={"a": {"route_order": None}})
    reset_fork = scenario.model_copy(
        update={
            "cycle_id": _operator_fork(
                stores,
                scenario,
                parse_cycle_seed(cleared, stores, campaign, scenario),
                round=2,
                candidate_id=candidate_key,
                seed=cleared,
            )
        }
    )
    assert cycle_config(stores, campaign, reset_fork).seed_overlay == {
        "a": {"model": "m", "provider": "p"}
    }, "a cleared field forked with the candidate's value"


@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_a_rewind_fork_asked_to_rewind_its_parent_too_refuses_before_any_cut(
    built_stores: Stores, scenario_line: _Scenario
) -> None:
    stores, scenario = built_stores, scenario_line.hop
    before = [hop.cycle_id for hop, _ in stores.campaigns.campaign_cycles(scenario.campaign_id)]
    # `resume --rewind 2 --from 1`, as the terminal lays it.
    with pytest.raises(PayloadInvalidError):
        PayloadInvalidError.validating(
            ForkCyclePayload,
            {
                "campaign_id": scenario.campaign_id,
                "cycle_id": scenario.cycle_id,
                "round": 2,
                "keep_rounds": True,
                "mode": RunMode(from_round=1),
            },
        )
    after = [hop.cycle_id for hop, _ in stores.campaigns.campaign_cycles(scenario.campaign_id)]
    assert after == before, "a fork whose run would rewind its parent in place was cut"


# It goes ahead on its own authority over the parent (bl1.B5).
@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_a_fork_by_another_issuer_withdraws_the_launch_queued_on_its_parent(
    built_stores: Stores,
    scenario_line: _Scenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stores, store, scenario = built_stores, built_stores.campaigns, scenario_line.hop
    room = [0]
    queue = JobRegistry(stores.jobs_dir, capacity=lambda _live: room[0])
    (parents_launch,) = request_jobs(
        stores=stores, job_registry=queue, holder=CampaignLaunch("ds1"), hop=scenario
    )
    assert parents_launch.queued, "guard: the parent's launch holds a place in line"
    room[0] = 1
    other = dataclasses.replace(
        stores, identity=dataclasses.replace(stores.identity, claims={"principal": "sub-1"})
    )

    async def past_its_refusals(admitted: Admitted) -> Any:
        # The journey asks what the held fork does to its parent, not what holds it.
        return SimpleNamespace(start=admitted.start, session=admitted.request)

    monkeypatch.setattr(Admitted, "held", past_its_refusals)
    asyncio.run(
        CommandDispatcher(other, job_registry=queue, inline=Inline()).dispatch(
            CommandCall(
                ForkCyclePayload(
                    campaign_id=scenario.campaign_id,
                    cycle_id=scenario.cycle_id,
                    round=2,
                    keep_rounds=True,
                ),
                door=Door.HOST,
            )
        )
    )
    withdrawn = queue.get(parents_launch.job_id)
    assert withdrawn is not None and withdrawn.lapse == "withdrawn", (
        "a fork left its parent's queued launch to run beside it"
    )
    assert store.holder(scenario) is None, "a withdrawn launch still claims the parent"
    assert store.last(scenario, CycleSupersededRecord) is not None, "the fork never took"


class _Displaced(NamedTuple):
    parent: CycleHop
    fork: CycleHop
    prefix: list[str]
    closed: list[RoundResult]
    closing: Cycle


@pytest.fixture
def displaced_line(built_stores: Stores) -> _Displaced:
    """A line closed through p0..p2 and a fork minted off it at round 3, holding no round of its
    own yet."""
    stores, store = built_stores, built_stores.campaigns
    closing = seat(stores).cycle
    displaced = CycleHop(campaign_id="displaced__20260101-000000", cycle_id="cycle_displaced")
    stored_campaign(stores, minted_campaign(displaced))
    stores.campaigns.mint_cycle(displaced)
    ledger = CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(displaced)))
    closed: list[RoundResult] = []
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
    return _Displaced(displaced, fork_hop, prefix, closed, closing)


def _fork_own_rounds(stores: Stores, line: _Displaced) -> list[str]:
    """The fork's own rounds over its parent's round 0: f1 crowned, f2 not."""
    own = CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(line.fork)))
    forked = line.closed[:1]
    return [
        _emit_round(line.closing, own, forked, "f1"),
        _emit_round(line.closing, own, forked, "f2", crowned=False),
    ]


@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_a_forks_own_ledger_displaces_its_parents_prefix_once(
    built_stores: Stores, displaced_line: _Displaced
) -> None:
    store = built_stores.campaigns
    displaced, fork_hop, prefix, *_ = displaced_line

    def candidates(hop: CycleHop) -> list[str]:
        return [key for _, key in store.standing_rounds(hop, moment=None).arms]

    assert candidates(fork_hop) == prefix, "the fork does not continue its parent's rounds"
    mine = _fork_own_rounds(built_stores, displaced_line)
    stood = store.standing_rounds(fork_hop, moment=None)
    assert candidates(fork_hop) == [prefix[0], *mine], "a parent round stood beside the fork's own"
    assert [next(iter(r.close.arms)) for r in stood.rounds.values()] == [prefix[0], *mine]
    assert stood.crowns == {0: prefix[0], 1: mine[0]}
    assert candidates(displaced) == prefix, "the fork's rounds displaced its parent's own"


# A line whose origin walk never closed names its origin by the arm it announced.
@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_a_line_stands_on_its_origin_until_an_election_crowns_another(
    built_stores: Stores,
) -> None:
    stores, store = built_stores, built_stores.campaigns
    closing = seat(stores).cycle
    walked_only, origin_log = _minted(stores, "cycle_origin_only")
    walked_origin = ArmKey(prompted(instruction="w_origin").id)
    origin_slot = ArmSlot(round=0, idx=0, total=1)
    origin_log.append(
        ArmRecord(round=0, arm=walked_origin, state=arm_state("w_origin", slot=origin_slot))
    )
    on_origin = RoundArm(round=0, arm=walked_origin)
    for cut_round in (0, 1):
        assert fork_edge(store.standing_rounds(walked_only, moment=None), cut_round, None) == (
            on_origin
        ), "a fork of a line no election crowned hangs from no candidate, or from another"
    crown = ArmKey(prompted(instruction="w_crown").id)
    close_round(
        origin_log,
        [
            # `close_round` closes the line's last round alone: round 0 stays the announced arm.
            _round(
                round=0,
                arms={walked_origin: arm_state("w_origin", slot=origin_slot)},
                election=election(walked_origin),
            ),
            _round(
                round=1,
                arms={crown: arm_state("w_crown", slot=ArmSlot(round=1, idx=0, total=1))},
                election=election(crown),
            ),
        ],
        cycle=closing,
    )
    standing_now = store.standing_rounds(walked_only, moment=None)
    assert (fork_edge(standing_now, 0, None), fork_edge(standing_now, 2, None)) == (
        on_origin,
        RoundArm(round=1, arm=crown),
    ), "a cut stands on a candidate other than the line's newest crown below it"


@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_a_steps_silence_is_the_familys_wherever_a_window_cuts_the_ray(
    built_stores: Stores, displaced_line: _Displaced
) -> None:
    stores, fork_hop = built_stores, displaced_line.fork
    _fork_own_rounds(stores, displaced_line)

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


# A holder declares its own stop; the hierarchy then reads the line the supersede moved.
@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_a_supersede_ends_its_parent_only_where_no_producer_holds_it(
    built_stores: Stores, displaced_line: _Displaced, tmp_path: Path
) -> None:
    stores, store = built_stores, built_stores.campaigns
    displaced, fork_hop, *_ = displaced_line
    _fork_own_rounds(stores, displaced_line)

    def ended(hop: CycleHop) -> StopReason | None:
        index = store.load(hop, moment=None)
        assert index is not None
        return index.stop_reason

    # Held as a server-launched run holds it, from a lock of its own process.
    lock = CycleLayout(store.cycle_dir(displaced)).producer_lock
    producer = take_lock(lock, wait_s=0)
    assert producer is not None
    with probe_lock(lock) as seen:
        assert seen, "a reader's probe missed the producer holding the cycle"
    stores.mark_superseded(displaced, fork_hop.cycle_id)
    producer.release()
    assert ended(displaced) is None, "a supersede ended its parent under the producer running it"
    lock.touch()
    with probe_lock(lock):
        assert not producer_lock.held(lock), (
            "a reader's probe in flight read as a producer, so a supersede would skip its stop"
        )
    stores.mark_superseded(displaced, fork_hop.cycle_id)
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
    assert isinstance(listed, HierarchyCampaign), "guard"
    assert listed.facts.line == Subject.of((fork_hop,)).address, (
        "the campaign's line stayed on the cycle a supersede retired"
    )
    [line_course] = [c for c in listed.children if c.address == listed.facts.line]
    assert line_course.facts.standing is not None, "guard"
    assert all(c.facts.bench is None for c in listed.children if c is not line_course), (
        "the bench graded a course that does not hold the line"
    )
    assert [r for r in listed.figures if r.key == "bench"] == [
        r for r in line_course.figures if r.key == "bench"
    ], "the campaign's bench is not its line's"
    # The bench chip grades archive rows, so a regrade at rest must answer the next ask with a body.
    validator = subtree_version(stores, listed.address)
    feed_jobs = JobRegistry(tmp_path / "feed_jobs", capacity=lambda _live: 1)
    fed = feed_versions(stores, feed_jobs)["store:"]
    filed(
        stores.archive,
        [archive_row("q_regraded", predicted="p")],
        filed_under(dataset_hash="regradeds"),
    )
    assert subtree_version(stores, listed.address) != validator, (
        "an archive write left the subtree's validator standing: its BENCH chip goes stale behind a 304"
    )
    assert feed_versions(stores, feed_jobs)["store:"] != fed, (
        "the feed left the store standing on an archive write: no browser re-asks the BENCH chip"
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
        if e.facts.stands
    ]
    assert [(e.facts.reading.arm.round, e.facts.reading.arm.key) for e in line_stood] == [
        (picked.round, picked.key)
    ], "the subtree stands a candidate on the line other than the one its index selected"
    assert line_course.facts.standing.changes == line_stood[0].facts.reading.changes_description, (
        "the course's standing names another pick's change than the candidate it stands"
    )


def _ident(name: str) -> str:
    return prompted(instruction=name).id


def _course_of(hop: CycleHop) -> str:
    return Subject.of((hop,)).address


def _at(hop: CycleHop, rnd: int, name: str) -> str:
    return Subject.of((hop,))._replace(round=rnd, candidate_id=ArmKey(_ident(name))).address


def _trunk_picks(hops: dict[str, CycleHop]) -> tuple[str, str, str, str]:
    """The trunk's origin o, its round-1 arms a and b, and the grand's x."""
    family, grand = hops["family"], hops["grand"]
    return (
        _at(family, 0, "o"),
        _at(family, 1, "a"),
        _at(family, 1, "b"),
        _at(grand, 3, "x"),
    )


# ONE line resolver over a fork-of-a-fork-of-a-fork: every (course, round) reads off the course
# owning it, as the mint records say. The trunk crowns o, a, z; the kid cuts it at 2 and holds;
# the grand cuts the kid at 3 and crowns x; the great cuts the grand at 4; a steer takes the kid
# back to C0, another the grand to b, an arm of the trunk's round 1 never crowned; rewinds re-run
# the grand's round 1 and its round 3.
# One row per round closed, in ledger order: a course's first row mints it off `parent`, cut at
# `cut` (a steer names the candidate it starts from); every row closes one round on it.
_FAMILY_ROWS: list[
    tuple[str, str, int, tuple[int, str] | None, list[str], str | None, str | None]
] = [
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


class _Family(NamedTuple):
    stores: Stores
    hops: dict[str, CycleHop]
    line_of: dict[str, list[RoundResult]]
    asks: dict[str, tuple[CycleHop, int, str, MeasurementRole]]
    seed_key: ArmKey
    tree: HierarchyEntry


@pytest.fixture(scope="module")
def family_tree(tmp_path_factory: pytest.TempPathFactory) -> _Family:
    """Built once: every fact on it only reads."""
    with scoped(memory=OpenAt()):
        return _family(workspace(tmp_path_factory.mktemp("family")))


def _family(stores: Stores) -> _Family:
    """Every row of `_FAMILY_ROWS` closed, inner runs spawned by each ask, and the subtree read."""
    store = stores.campaigns
    closing = seat(stores).cycle
    family = CycleHop(campaign_id="family__20260101-000000", cycle_id="cycle_family")
    stored_campaign(
        stores,
        minted_campaign(
            family,
            config=defaulted_config({"optimization": {"degradation_threshold": 0.4}}),
        ),
    )
    stores.campaigns.mint_cycle(family)
    restated_at_mint = {"mended": [1]}
    hops, line_of = {"family": family}, {"family": list[RoundResult]()}
    for (
        row_course,
        row_parent,
        row_cut,
        row_named,
        row_picks,
        row_crown,
        row_reference,
    ) in _FAMILY_ROWS:
        if row_course in hops and row_cut:
            store.rewind_to_round(hops[row_course], row_cut - 1)
            del line_of[row_course][row_cut:]
        if row_course not in hops:
            spec = ForkSpec(
                trigger=ForkTrigger.OPERATOR_STEERED if row_named else ForkTrigger.OPERATOR_REWIND,
                reason="",
                issued_by=SYSTEM_PRINCIPAL,
                # The one resolution `cut_fork` writes: the named arm, else the line's pick.
                from_candidate=fork_edge(
                    store.standing_rounds(hops[row_parent], moment=None),
                    row_cut,
                    None
                    if row_named is None
                    else RoundArm(round=row_named[0], arm=ArmKey(_ident(row_named[1]))),
                ),
            )
            hops[row_course] = _mint_sibling(
                store, hops[row_parent], f"cycle_family_{row_course}", spec, cut_round=row_cut
            )
            line_of[row_course] = line_of[row_parent][:row_cut]
            # A repair restates a lifted round WHOLE at its mint, so the round is the fork's own.
            for r in restated_at_mint.get(row_course, ()):
                lifted = store.standing_rounds(hops[row_course], moment=None).rounds[r].close
                store.rebank_round(hops[row_course], lifted.restated(lifted.optimizer_state))
        line, n = line_of[row_course], len(line_of[row_course])
        arms = {
            ArmKey(_ident(p)): arm_state(p, slot=ArmSlot(round=n, idx=i, total=len(row_picks)))
            for i, p in enumerate(row_picks)
        }
        if row_reference is not None:
            arms[ArmKey(_ident(row_reference))] = arm_state(
                row_reference,
                slot=walked_slot(line, ArmKey(_ident(row_reference))),
                role=MeasurementRole.PARENT,
            )
            root = next(iter(line[0].arms))
            assert line_reference(line, origin=root).arm == _ident(row_reference), (
                f"guard: a row re-reads off {row_course}'s line"
            )
        row_ended = row_crown or row_reference or row_picks[0]
        line.append(
            _round(
                round=n,
                arms=arms,
                reference=None if row_reference is None else line_reference(line, origin=root),
                election=election(
                    None if row_crown is None else _ident(row_crown),
                    ended_on=_ident(row_ended),
                ),
            )
        )
        close_round(
            CycleEventLog.open(CycleDir(store.cycle_dir(hops[row_course]))), line, cycle=closing
        )
    grand, rewound, peek = hops["grand"], hops["rewound"], hops["peek"]
    # A round still measuring: its re-read is announced, and nothing has elected or closed.
    live = len(line_of["peek"])
    CycleEventLog.open(CycleDir(store.cycle_dir(peek))).append(
        ArmRecord(
            round=live,
            arm=ArmKey(_ident("u2")),
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
        stored_campaign(sandbox, minted_campaign(spawned))
        sandbox.campaigns.mint_cycle(spawned)
        sandbox.campaigns.record_spawned(
            spawned,
            SpawnedBy(
                round=rnd,
                individual_id=_ident(pick),
                role=role,
                cycle_id=asker.cycle_id,
                task=f"t/{name}",
            ),
        )
    # The run x asked closes its origin: its line continues the line of the candidate that asked.
    seeded = CycleHop(campaign_id="own__20260101-000000", cycle_id="cycle_own")
    seed_key = ArmKey(_ident("w0"))
    close_round(
        CycleEventLog.open(CycleDir(sandbox.campaigns.cycle_dir(seeded))),
        [
            _round(
                round=0,
                arms={seed_key: arm_state("w0", slot=ArmSlot(round=0, idx=0, total=1))},
                election=election(seed_key, ended_on=seed_key),
            )
        ],
        cycle=closing,
    )
    tree = hierarchy_subtree(stores, Subject(family.campaign_id).address, None, None, moment=None)
    return _Family(stores, hops, line_of, asks, seed_key, tree)


@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_a_reread_stamps_the_latest_walk_of_its_individual(family_tree: _Family) -> None:
    restamp = walked_slot(family_tree.line_of["family"], ArmKey(_ident("a")))
    assert restamp is not None and restamp.round == 2, (
        "a re-read stamps a walk other than its latest"
    )


@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_an_inner_run_hangs_on_the_candidate_its_spawn_record_names(
    family_tree: _Family,
) -> None:
    family, grand, rewound, peek = (
        family_tree.hops[n] for n in ("family", "grand", "rewound", "peek")
    )
    asks, tree = family_tree.asks, family_tree.tree
    hung = {
        name: e.parent
        for e in entries_under(tree)
        if isinstance(e, SubtreeCampaign)
        for name in asks
        if f"{name}__" in e.address
    }
    _, first_pick, _, x = _trunk_picks(family_tree.hops)
    assert hung == {
        "reread": first_pick,
        "again": _at(family, 2, "a"),
        "own": x,
        "overlap": _course_of(grand),
        "unrun": _at(rewound, 1, "w"),
        "live": _at(peek, 0, "u2"),
    }, "an inner run hung on a candidate other than the one its spawn record names"


# The bugs reviews found are rows here: the grand's cut round drawn twice, the steer's
# inherited anchor lost, the rewind's round 1 re-read off the wrong course's picks.
@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_every_candidates_line_reads_each_round_off_the_course_owning_it(
    family_tree: _Family,
) -> None:
    (
        family,
        kid,
        _grand,
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
    ) = family_tree.hops.values()
    tree, seed_key = family_tree.tree, family_tree.seed_key
    o, first_pick, b, x = _trunk_picks(family_tree.hops)
    head = [(0, [o]), (1, [first_pick])]
    crownless: tuple[int, list[str]] = (2, [])
    # A course lifting no round continues the line of the candidate it hangs from, through it.
    steer = [(0, [o]), (0, [_at(steered, 0, "s")])]
    rivals = [(0, [o]), (0, [_at(rival, 0, "c")])]
    [own_seed] = [
        e.address
        for e in entries_under(tree)
        if isinstance(e, HierarchyCandidate) and e.facts.individual == seed_key
    ]
    lines = {
        o: [(0, [o])],
        first_pick: head,
        b: [(0, [o]), (1, [b])],
        **{
            c: [*head, (2, [c])]
            for c in (_at(family, 2, "a"), _at(family, 2, "z"), _at(kid, 2, "y"))
        },
        x: [*head, crownless, (3, [x])],
        _at(great, 4, "v"): [*head, crownless, (3, [x]), (4, [_at(great, 4, "v")])],
        own_seed: [*head, crownless, (3, [x]), (0, [own_seed])],
        _at(steered, 0, "s"): steer,
        _at(rewound, 1, "w"): [(0, [o]), (1, [_at(rewound, 1, "w")])],
        _at(rewound, 1, "k"): [(0, [o]), (1, [_at(rewound, 1, "k")])],
        # Its parent re-ran round 1 in place after the cut; the fork's line still stands on w.
        _at(carried, 2, "d"): [(0, [o]), (1, [_at(rewound, 1, "w")]), (2, [_at(carried, 2, "d")])],
        _at(redone, 3, "q"): [*head, crownless, (3, [_at(redone, 3, "q")])],
        _at(branched, 0, "p"): [(0, [o]), (1, [b]), (0, [_at(branched, 0, "p")])],
        _at(rerun, 0, "r"): [*head, (0, [_at(rerun, 0, "r")])],
        _at(relift, 0, "l"): [(0, [o]), (0, [_at(relift, 0, "l")])],
        # The restated round 1 is the repair's own, whole: its arms are copies on its own line.
        **{c: [(0, [o]), (1, [c]), crownless] for c in (_at(mended, 1, "a"), _at(mended, 1, "b"))},
        _at(mended, 3, "m"): [
            (0, [o]),
            (1, [_at(mended, 1, "a")]),
            crownless,
            (3, [_at(mended, 3, "m")]),
        ],
        _at(rival, 0, "c"): rivals,
        **{n: [*rivals, (1, [n])] for n in (_at(rival, 1, "e"), _at(rival, 1, "f"))},
        # A cut lifting the rival's origin continues the line that origin hangs from.
        _at(first, 1, "i"): [*rivals, (1, [_at(first, 1, "i")])],
        _at(first, 2, "n"): [*rivals, (1, [_at(first, 1, "i")]), (2, [_at(first, 2, "n")])],
        # Each read on the line it ran on: g on the rival's round 1 as the second's cut found it.
        **{
            n: [*rivals, (1, [_at(rival, 1, "e")]), (2, [n])]
            for n in (_at(rival, 2, "g"), _at(rival, 2, "j"))
        },
        _at(second, 3, "h"): [
            *rivals,
            (1, [_at(rival, 1, "e")]),
            (2, [_at(rival, 2, "g")]),
            (3, [_at(second, 3, "h")]),
        ],
        **{
            n: [*steer, (1, [n])]
            for n in (_at(steered, 1, "t"), _at(steered, 1, "t2"), _at(steered, 1, "t4"))
        },
        _at(steered, 2, "t3"): [*steer, (1, [_at(steered, 1, "t2")]), (2, [_at(steered, 2, "t3")])],
        _at(glance, 0, "u"): [*steer, (0, [_at(glance, 0, "u")])],
        _at(peek, 0, "u2"): [
            *steer,
            (1, [_at(steered, 1, "t2")]),
            (2, [_at(steered, 2, "t3")]),
            (0, [_at(peek, 0, "u2")]),
        ],
    }
    assert {
        e.address: [(s.round, s.candidates) for s in e.facts.main_line]
        for e in entries_under(tree)
        if isinstance(e, HierarchyCandidate)
    } == lines, "a candidate's line reads a round off a course that does not own it"


@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_every_course_hangs_from_and_starts_at_the_candidates_its_mint_record_names(
    family_tree: _Family,
) -> None:
    (
        family,
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
    ) = family_tree.hops.values()
    tree = family_tree.tree
    o, first_pick, b, x = _trunk_picks(family_tree.hops)
    courses = {e.address: e for e in entries_under(tree) if isinstance(e, SubtreeCourse)}
    hangs = {
        family: (None, o),
        kid: (first_pick, o),
        grand: (first_pick, o),
        great: (x, o),
        steered: (o, _at(steered, 0, "s")),
        rewound: (o, o),
        redone: (first_pick, o),
        branched: (b, _at(branched, 0, "p")),
        # A steer naming a round's re-read hangs from the candidate that round re-read, lifted or not.
        rerun: (first_pick, _at(rerun, 0, "r")),
        relift: (o, _at(relift, 0, "l")),
        # It hangs from the parent line's arm.
        mended: (first_pick, o),
        carried: (_at(rewound, 1, "w"), o),
        rival: (o, _at(rival, 0, "c")),
        first: (_at(rival, 0, "c"), _at(rival, 0, "c")),
        # Cut on g, which its parent has since re-run away: the cut still names g.
        second: (_at(rival, 2, "g"), _at(rival, 0, "c")),
        glance: (_at(steered, 0, "s"), _at(glance, 0, "u")),
        peek: (_at(steered, 2, "t3"), _at(peek, 0, "u2")),
    }
    for hop, hangs_from in hangs.items():
        course = courses[_course_of(hop)]
        assert (course.facts.from_candidate, course.facts.origin) == hangs_from, (
            f"{hop.cycle_id} hangs from, or starts at, a candidate its mint record does not name"
        )


# A retirement is "off the fork's line, within its reach", and nothing else.
@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_a_cut_retires_exactly_the_arms_off_its_line_within_its_reach(
    family_tree: _Family,
) -> None:
    family, kid, steered, rewound, mended, carried, rival, first = (
        family_tree.hops[n]
        for n in ("family", "kid", "steered", "rewound", "mended", "carried", "rival", "first")
    )
    tree = family_tree.tree
    *_, x = _trunk_picks(family_tree.hops)
    by_addr = {e.address: e for e in entries_under(tree) if isinstance(e, HierarchyCandidate)}
    assert {
        address: tag.value
        for address, e in by_addr.items()
        if (tag := e.tag("superseded")) is not None
    } == {
        # Off the line of ANY supersede fork the line passed through that reached it, named by the
        # lowest cut, the nearest link of a tie: the rewind takes the trunk's round 1 off its line,
        # `kid` its round 2 though `carried`, cut from `kid` at the same round, reached it too.
        **dict.fromkeys((_at(family, 1, n) for n in "ab"), _course_of(rewound)),
        **dict.fromkeys((_at(family, 2, n) for n in "az"), _course_of(kid)),
        _at(kid, 2, "y"): _course_of(carried),
        x: _course_of(mended),
        _at(rewound, 1, "k"): _course_of(carried),
        # The second's line stands on g, and the first still retires it.
        **dict.fromkeys(
            (_at(rival, r, n) for r, n in ((1, "e"), (1, "f"), (2, "g"), (2, "j"))),
            _course_of(first),
        ),
        # Re-run away by their own course, whether or not a line stands on them.
        _at(rewound, 1, "w"): _course_of(rewound),
        **dict.fromkeys(
            (_at(steered, r, n) for r, n in ((1, "t"), (1, "t2"), (2, "t3"))), _course_of(steered)
        ),
    }, "a cut retires an arm on its line, one past the reach of its branch, or names another cut"


# A lifted round keeps the crown its parent elected, and a parting dims every fork whose start
# was proposed past it, a steer from a later arm included.
@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_the_mask_reads_the_lines_the_subtree_reads(family_tree: _Family) -> None:
    stores = family_tree.stores
    family, grand, great, rewound, redone, branched, rerun, mended, carried = (
        family_tree.hops[n]
        for n in (
            "family",
            "grand",
            "great",
            "rewound",
            "redone",
            "branched",
            "rerun",
            "mended",
            "carried",
        )
    )
    mask = load_mask_record(stores, family.campaign_id, lens=None)
    crowned = {
        c.cycle_id: {
            r.round: next((m.key for m in r.candidates if m.is_selected), None) for r in c.rounds
        }
        for c in mask.cycles
    }
    assert crowned[great.cycle_id] == {
        0: _ident("o"),
        1: _ident("a"),
        2: None,
        3: _ident("x"),
        4: _ident("v"),
    }, "the mask crowns a lifted round other than where its parent elected it"
    parted = find_divergences(
        mask,
        lambda rnd: VerdictOutcome(diverged=(rnd.cycle_id, rnd.round) == (grand.cycle_id, 0)),
    )
    # `relift` named round 1's re-read of o, which its mint records as o: proposed AT the parting.
    assert {cid for cid, _ in parted.divergent} == {
        h.cycle_id for h in (grand, great, rewound, redone, branched, rerun, mended, carried)
    }, "a parting dims a fork rooted before it, or keeps one proposed past it"


class _Repair(NamedTuple):
    repaired: Callable[..., tuple[CycleHop, CycleHop]]
    fork_direction: Callable[[CycleHop], ForkDirection]
    critique_of: Callable[[CycleHop], str]


@pytest.fixture
def repair(built_stores: Stores) -> _Repair:
    """A repair re-measures a holed round onto a branch, graded by whether any later round read the
    hole: the one validity check, and a round banked off the hole, decide EQUIVALENT against
    SUPERSEDE."""
    stores = built_stores
    repair_bank = samples(4, "g")
    repair_origin, holed = prompted(instruction="origin"), ArmKey(prompted(instruction="c1").id)

    async def _answered(sample: Sample, _session: Any, _pipeline_params: Any) -> MeasuredCell:
        return MeasuredCell(
            sample_key=sample.key, query=sample.query, ground_truth="g", predicted="g"
        )

    def repaired(cycle_id: str, *, consumed: bool) -> tuple[CycleHop, CycleHop]:
        hop = CycleHop(campaign_id=f"{cycle_id}__20260101-000000", cycle_id=cycle_id)
        stored_campaign(stores, minted_campaign(hop))
        stores.campaigns.mint_cycle(hop)
        drawn = partition_bank(repair_bank, DatasetSplit(bench=0), seed=0)
        stores.campaigns.append(hop, drawn.record(dataset_hash="bank"))
        rows = [
            measurement(i, 1.0, sample_key=s.key, predicted="g") for i, s in enumerate(repair_bank)
        ]
        running = seat(
            stores,
            hop=hop,
            bank=repair_bank,
            round_num=2,
            origin=(repair_origin, lambda _s: sheet(rows)),
        )
        cycle, ledger = running.cycle, running.ledger
        rescope(spawn=inner_spawn_of(cycle.session))
        run_init(cycle, ledger)
        # Round 1's arm ran to its end on cell 3 and came back with no measurement.
        hole = measurement(
            3, None, sample_key=repair_bank[3].key, error="boom", error_category="transient"
        )
        critiqued = Critique(readout={"priority_fix": "framing: as recorded"})
        child = prompted(instruction="c1")
        sp = cycle.session.points(cycle.framing).job(child)
        c1 = arm_state(
            "c1",
            slot=ArmSlot(round=1, idx=0, total=1),
            individual=child,
            resolved_params=sp.config_params.dump(),
            sp_hash=sp.sp_hash(cycle.session.pipeline_schema),
        )
        line = [
            cycle.origin_round.model_copy(update={"optimizer_state": cycle.close_state(0)}),
            _round(
                round=1,
                arms={holed: c1},
                cells={holed: sheet([*rows[:3], hole])},
                election=election(holed),
                optimizer_state=cycle.close_state(1).holding(critiqued),
            ),
        ]
        for n in range(len(line)):
            close_round(ledger, line[: n + 1], cycle=cycle)
        if consumed:
            ledger.append(
                RoundProposedRecord(
                    round=2,
                    consumed="",
                    proposals=[],
                    submitted=False,
                    walked=cycle.round_state(2),
                    reads=[],
                    opened_on=cycle.difficulty.stamp.reach,
                )
            )
        set_cycle(cycle, line=line)
        with (
            scoped(ledger=ledger),
            mock.patch.object(query_loop, "measure_sample", _answered),
            scoped(book=spend_book()),
        ):
            branch = asyncio.run(apply_correction(cycle, line, repair_bank))
        assert branch is not None, "a holed round was carried as it ran"
        return hop, branch.child

    def fork_direction(hop: CycleHop) -> ForkDirection:
        index = stores.campaigns.load(hop, moment=None)
        assert index is not None and index.fork is not None
        return index.fork.resolved_direction

    def critique_of(hop: CycleHop) -> str:
        stood = stores.campaigns.standing_rounds(hop, moment=None).rounds[1]
        readout = stood.close.optimizer_state.feedback()
        assert readout is not None, "guard: round 1 banked a critique"
        return readout["priority_fix"]

    return _Repair(repaired, fork_direction, critique_of)


@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_a_correction_no_later_round_read_branches_equivalent(repair: _Repair) -> None:
    repaired, fork_direction, critique_of = repair
    ran, unread_branch = repaired("cycle_unread", consumed=False)
    assert fork_direction(unread_branch) is ForkDirection.EQUIVALENT, (
        "a correction no recorded decision reads forked the line off its parent"
    )
    assert critique_of(ran) == "framing: as recorded", "the repair rewrote the round its parent ran"


@pytest.mark.usefixtures("offline", "optimizing_memory")
def test_a_correction_a_later_round_read_supersedes_its_parent(repair: _Repair) -> None:
    repaired, fork_direction, _ = repair
    _, read_branch = repaired("cycle_read", consumed=True)
    assert fork_direction(read_branch) is ForkDirection.SUPERSEDE, (
        "round 2's banked candidates were generated off the hole and carried across as equivalent"
    )


# 4. An origin the gate halted, resumed past it


@pytest.mark.usefixtures("optimizing_memory")
def test_a_resume_takes_the_origin_the_gate_halted_past_it_and_every_later_launch_reads_the_pass(
    built_stores: Stores,
) -> None:
    stores = built_stores
    dead = sheet(measurement(i, None, error="boom", error_category="SERVER") for i in range(8))
    origin = prompted(instruction="origin")
    hop = CycleHop(campaign_id="cycle_gated__20260101-000000", cycle_id="cycle_gated")
    stored_campaign(stores, minted_campaign(hop))
    stores.campaigns.mint_cycle(hop)

    def launch(*, replayed: bool, crashes: bool = False) -> StopReason:
        """A launch as `build_run_observers` opens it, its loop capped before round 1; one that
        *crashes* writes no ending. The real resume path walks in the offline run's `gated/` leg."""
        ctx = seat(
            stores,
            hop=hop,
            origin=(origin, lambda _session: dead),
            optimization={"budget": {"rounds": 0}},
        )
        cycle, session = ctx.cycle, ctx.cycle.session
        session.state.clock = LaunchClock.now()
        rescope(spawn=inner_spawn_of(session))
        ctx.ledger.append(RunPhaseRecord(run_phase=RunPhase.RUNNING))
        if replayed:
            # The origin a resume replays stands on its close (`closed_rounds.py::closed_rounds`).
            closed = stores.campaigns.standing_rounds(hop, moment=None).rounds[0]
            set_cycle(
                cycle,
                line=[cycle.origin_round.model_copy(update={"at_offset": closed.at_offset})],
            )

        # Read before the launch writes, as `mint.py::open_launch` reads it.
        resumed = stores.campaigns.resumed_ending(hop)

        async def walk() -> LoopEnd:
            gated = await open_origin_round(
                cycle, [], ctx.ledger, spend=SpendRollup(), resumed_ending=resumed
            )
            return gated or await run_round_loop(cycle, cycle.config, session, ctx.ledger)

        with scoped(account=stores):
            book = spend_book()
        with scoped(ledger=ctx.ledger), scoped(book=book):
            end = asyncio.run(walk())
        if not crashes:
            ctx.ledger.append(RunPhaseRecord.stop(end.stop_reason))
        return end.stop_reason

    def passes() -> list[str]:
        decided = stores.campaigns.standing_rounds(hop, moment=None).decisions.get(0, [])
        return [d.inputs_ref.by for d in decided if isinstance(d.inputs_ref, OriginGatePass)]

    # An origin that scored no cell has nothing to gate: it halts.
    assert launch(replayed=False) is StopReason.ORIGIN_GATE, "an unscored origin held or ran on"
    assert passes() == [], "a halt banked a pass"
    # The resume's own RUNNING retires that ending, yet the pass is read off what it retired; the
    # launch dies before it writes an ending, so the next one takes up the gate's halt again.
    assert launch(replayed=True, crashes=True) is StopReason.ROUNDS_BUDGET, (
        "a resume halted at the gate again"
    )
    assert passes() == ["resume"], "the override lived only in the process that took it"
    assert launch(replayed=True) is StopReason.ROUNDS_BUDGET, "a banked pass was not read"
    assert passes() == ["resume"], "a resume after a crash banked the override twice"
    # A later launch, after an ending the gate never made, stands on the pass already banked.
    assert launch(replayed=True) is StopReason.ROUNDS_BUDGET, "a banked pass was not read"
    assert passes() == ["resume"], "one override was banked twice"
