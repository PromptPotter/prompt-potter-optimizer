"""Lost or corrupted measurement.

Owns `infrastructure/store/measurement_archive.py` and `archive_queries.py`,
`application/maintenance/`, `application/bench/resume_and_fork/`, `application/origin.py` and
`application/runner/loop.py`. A
replay served to the wrong content, a paid row dropped, a fork that inherits the wrong origin.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import IO, Any, NamedTuple

import httpx
import pytest
from factories import (
    LLM_ONLY,
    Configs,
    archive_row,
    arm_key,
    arm_state,
    arm_walk,
    degradation_health,
    delta_ruler,
    filed,
    filed_under,
    ledger_records,
    measurement,
    measurements,
    mint_fork,
    optimizer_state,
    pipeline_schema,
    pobb_knobs,
    round_result,
    samples,
    scorer_of,
    sheet,
    workspace,
)
from fastapi import Request
from offline_run import DATASET
from offline_world import Router
from world import Shared, World, shared_run

from promptpotter.application.bench.difficulty import ruler_rows
from promptpotter.application.bench.resume_and_fork import fork_siblings
from promptpotter.application.bench.resume_and_fork.decisions import decision
from promptpotter.application.bench.resume_and_fork.fork_siblings import (
    ForkCut,
    cut_operator_fork,
    fork_edge,
)
from promptpotter.application.bench.resume_and_fork.replayers import replay_all_mismatches
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.commands.launching import parse_cycle_start
from promptpotter.application.cycle_reads import feed_versions, open_family_ray
from promptpotter.application.hierarchy import hierarchy_version, list_hierarchy
from promptpotter.application.hierarchy_subtree import (
    LineOwners,
    Reach,
    Walked,
    earlier_measured,
    hierarchy_subtree,
    inner_host,
    line_owners,
    line_through,
    main_line,
    retired_by,
    subtree_cycle,
    subtree_version,
    supersede_line,
    unplaced_reason,
)
from promptpotter.application.initialization.session import drawn_partition
from promptpotter.application.intelligence.indexes.sample import SampleIndex
from promptpotter.application.jobs.launcher import opening as launch_opening
from promptpotter.application.jobs.launcher.admission import request_jobs
from promptpotter.application.jobs.launcher.launch import Inline, Launched
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.maintenance.archive_maintenance import (
    archive_datasets,
    compact_measurement_archive,
    reclaim_dataset,
    reindex_measurement_archive,
    restore_measurement_archive,
)
from promptpotter.application.mask.divergence import VerdictOutcome, find_divergences
from promptpotter.application.mask.load import load_mask_record
from promptpotter.application.mask.record import MaskRound
from promptpotter.application.optimizers.capo.members import (
    CapoObjective,
    PairedTCut,
    PopulationKept,
)
from promptpotter.application.optimizers.gepa.members import MinibatchTested
from promptpotter.application.optimizers.potter.election import RoundWinner
from promptpotter.application.optimizers.potter.pobb import PoBBCheck
from promptpotter.application.optimizers.potter.race import EliminationCut
from promptpotter.application.pipeline_resolve import (
    cycle_config,
    permitted_narrowing,
)
from promptpotter.application.run_stop import StopLoop, run_stop_detail, run_stop_reason
from promptpotter.application.scoring.closed_rounds import closed_round, closed_rounds
from promptpotter.application.scoring.measurement_log import open_cell
from promptpotter.application.scoring.paired import RoundPairs, grade_measurands
from promptpotter.application.scoring.race_protocol import View, WalkView
from promptpotter.application.scoring.row_policy import FieldRead, RowGround, RowKnobs, send_rows
from promptpotter.application.served_dashboard import ServedDashboard, served_dashboard
from promptpotter.application.views.render.phase import acts_line
from promptpotter.config.paths import MEASUREMENTS_DIR, user_data_root
from promptpotter.domain.activity import ActivityFeed
from promptpotter.domain.arms import ArmPosition, Measured
from promptpotter.domain.campaign import CampaignCycle
from promptpotter.domain.campaign_config import (
    CycleConfig,
)
from promptpotter.domain.command_kinds import (
    DeleteCampaignPayload,
    Door,
    ForkCyclePayload,
    ForkStart,
    StartRunPayload,
)
from promptpotter.domain.crowning_bar import CrowningBar
from promptpotter.domain.cycle_paths import (
    CycleDir,
    CycleHop,
    Subject,
)
from promptpotter.domain.fitness_fold import FoldBasis
from promptpotter.domain.fork import ConfigOverrides, ForkSpec, ForkTrigger
from promptpotter.domain.hierarchy import (
    HierarchyCampaign,
    HierarchyCandidate,
    HierarchyEntry,
    HierarchyRound,
    MainLineStep,
    SubtreeCycle,
    entries_under,
)
from promptpotter.domain.job import CampaignLaunch
from promptpotter.domain.narration import Narration
from promptpotter.domain.origin_panel import OriginPanel, RoundEnding
from promptpotter.domain.paired_reading import ArmKey, ReadingState, RoundArm
from promptpotter.domain.phases import PauseCause, StopReason
from promptpotter.domain.records.decision import OriginGatePass
from promptpotter.domain.records.lineage import (
    CycleMintedRecord,
    CycleStart,
    CycleSupersededRecord,
    SpawnedBy,
)
from promptpotter.domain.records.money import CycleShareRecord, PricedKeyRecord
from promptpotter.domain.records.rounds import ArmRecord, RoundEnteredRecord, RoundWarningRecord
from promptpotter.domain.records.run_phase import Ended, RunPhaseRecord
from promptpotter.domain.records.sample_passes import SampleScoredRecord
from promptpotter.domain.results import RoundResult
from promptpotter.domain.ruler import DeltaRuler
from promptpotter.domain.run_mode import OnDivergence, RunMode
from promptpotter.domain.run_standing import walked_position
from promptpotter.domain.sample import ArchiveEntry, Bank, FiledAnswer, Sample
from promptpotter.domain.scoring import CellSheet, MeasuredCell, Scorer
from promptpotter.infrastructure import producer_lock
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.run_scope import scoped
from promptpotter.infrastructure.store import archive_queries, measurement_archive
from promptpotter.infrastructure.store import io as store_io
from promptpotter.infrastructure.store.archive_queries import (
    ArmOf,
    CampaignWalks,
    OpenAt,
    bench_reads,
    list_populations,
    load_population,
    sample_fold_rows,
)
from promptpotter.infrastructure.store.campaign_store.ledger_scan import WALKED_ANSWERS
from promptpotter.infrastructure.store.dataset_access import DEFINITION_FILE, resolve_dataset
from promptpotter.infrastructure.store.io import (
    Appended,
    append_jsonl,
    append_line,
    file_stat,
    make_dirs,
    probe_lock,
    rewrite_lines,
    take_lock,
    write_bytes,
    write_json,
    write_text,
)
from promptpotter.infrastructure.store.layout import CycleLayout, campaigns_root_dir_for
from promptpotter.infrastructure.store.measurement_archive import (
    ArchiveFile,
    MeasurementArchive,
    ReplayFeed,
    config_key,
    standing,
)
from promptpotter.infrastructure.store.read_model import LedgerIndex, Moment, Projection
from promptpotter.infrastructure.store.session_pointer import (
    cleanup_stub_fork_if_empty,
    read_active_pointer,
)
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.main import potter_error_handler
from promptpotter.presentation.cli import main as cli_main
from promptpotter.shared.errors import (
    MINT_FRESH,
    BadRequestError,
    ConflictError,
    FileKind,
    FileUnreadableError,
    NotFoundError,
    PayloadInvalidError,
    PotterError,
)
from promptpotter.shared.hashing import ADDRESS_HEX
from promptpotter.shared.identity import SYSTEM_PRINCIPAL, default_identity
from promptpotter.shared.measurement_context import MeasurementRole

_CAMPAIGN = "testds__20260101-000000"


# 1. Replay eligibility


def test_a_full_chain_answer_never_replays_across_a_later_nodes_change(tmp_path: Path) -> None:
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


def test_a_grade_c_answer_never_replays(tmp_path: Path) -> None:
    archive = MeasurementArchive(tmp_path / "cells")
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


def test_a_later_rescore_or_remeasure_never_displaces_the_answer_measured_first(
    tmp_path: Path,
) -> None:
    # A diagnostic rescore files grade B, a remeasure grade A: neither is the answer a run paid for.
    archive = MeasurementArchive(tmp_path / "cells")
    feed = ReplayFeed(archive, LLM_ONLY)
    for at, said, grade in ((1, "measured", "A"), (2, "rescored", "B"), (3, "again", "A")):
        row = archive_row("q_rescored", predicted=said, pipeline_data={"terminal_node": "llm_only"})
        filed(archive, [row], filed_under(), grade=grade, created_at=f"2026-05-19T00:0{at}")
        moved = [b.cell.predicted for b in feed.advance().values() if b.cell.query == "q_rescored"]
        assert moved == (["measured"] if at == 1 else []), (
            f"the replay served {moved} once {said!r} was filed: a later row displaced the first"
        )


def test_a_cell_replays_on_its_samples_content_at_the_slot_of_whichever_bank_holds_it(
    tmp_path: Path,
) -> None:
    archive = MeasurementArchive(tmp_path / "cells")
    row = archive_row("q_aime_14", predicted="p", pipeline_data={"terminal_node": "llm_only"})
    filed(archive, [row])
    reusable = ReplayFeed(archive, LLM_ONLY).advance()
    wider = [
        Sample(id=0, query="q_aime_14", ground_truth="g"),
        Sample(id=14, query="q_new", ground_truth="g"),
        Sample(id=1, query="q_aime_14", ground_truth="not_g"),
        Sample(id=2, query="q_aime_14", ground_truth="g", source_pin={"git_commit_id": "moved"}),
    ]
    on_wider = {s.id: got.cell for s in wider if (got := reusable.get(s.key))}
    assert set(on_wider) == {0}, "a cell replayed onto a sample whose content it never measured"
    assert on_wider[0].query == "q_aime_14"


def test_two_walks_never_buy_one_cell_and_a_second_release_frees_no_claim(tmp_path: Path) -> None:
    # The holder files the answer before its claim retires; the waiter taking the claim replays it.
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


# The `score:F` lens moves the formula alone: the fork's origin replays what its parent paid.
@pytest.mark.usefixtures("optimizing_memory")
def test_a_cell_banked_under_one_formula_reads_under_another_as_a_fresh_walk_would(
    world: World, line: Shared
) -> None:
    adopted, (hop, banked_by) = world.adopt(line), line.made
    branch = closed_round(adopted.stores, hop, 1, banked_by, moment=None)
    assert branch is not None and branch.election is not None
    key = branch.election.ended_on
    paid = {cell.facts.answer for cell in branch.rows(key)}
    lens = ConfigOverrides(scoring={"per_cell": "0.5 * fitness + 0.25"})
    start = ForkStart(config_overrides=lens)
    fork = asyncio.run(adopted.fork(hop, 1, candidate_id=str(key), start=start))
    read_by = fork.session.state.scoring.scorer
    walked = [
        rec.result
        for rec in adopted.records(fork.hop)
        if isinstance(rec, SampleScoredRecord) and rec.round == 0
    ]
    assert walked and all(c.facts.cached and c.facts.answer in paid for c in walked), (
        "guard: the fork's origin bought an answer its parent had paid for"
    )
    worth = {c.key: read_by.grade(c.facts).grade.objective for c in walked}
    banked = {c.key: banked_by.grade(c.facts).grade.objective for c in walked}
    assert all(worth[k] != banked[k] for k in worth), "guard: the lens moved no cell's worth"
    assert {c.key: c.grade.objective for c in walked} == pytest.approx(worth), (
        "a replayed cell carried the grade of the formula it was banked under"
    )
    origin, scored = fork.result.origin, [w for w in worth.values() if w is not None]
    assert origin is not None and origin.composite is not None and len(scored) == len(worth)
    assert origin.composite.value == pytest.approx(sum(scored) / len(scored)), (
        "the fork's origin scored on another worth than its cells"
    )
    every = frozenset(worth)
    ruled = ruler_rows(adopted.stores, [("x", read_by.graded(walked))], every)
    assert {k: y for _, k, y in ruled} == pytest.approx(worth), (
        "the ruler read the banked formula's grades"
    )


# 2. The archive on disk


@pytest.mark.usefixtures("optimizing_memory")
def test_the_archive_on_disk_keeps_every_paid_answer(built_stores: Stores) -> None:
    archive = built_stores.archive
    entry = filed_under(LLM_ONLY, "kept")
    other: Configs = [("llm_only", {"model": "Y"})]
    refs: list[str] = []
    for at, said, configs, facts in (
        (1, "first", LLM_ONLY, {}),
        (2, "second", LLM_ONLY, {}),
        (3, "ERROR", LLM_ONLY, {"error_category": "UNKNOWN"}),
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


@contextlib.contextmanager
def _a_run_appending(stores: Stores) -> Iterator[None]:
    """A cycle `camp/cyc` whose producer lock another process holds for the block."""
    cycle_dir = stores.campaigns.cycle_dir(CycleHop(campaign_id="camp", cycle_id="cyc"))
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
        yield
    finally:
        run.stdin.close()
        run.wait()


def test_a_reindex_beside_a_run_in_another_process_refuses_until_it_ends(
    built_stores: Stores,
) -> None:
    """A row the run lands mid-rewrite lands in no index: its writer is asked, never raced."""
    with _a_run_appending(built_stores), pytest.raises(ConflictError) as refused:
        reindex_measurement_archive(built_stores)
    assert refused.value.code == "archive_writers"
    assert reindex_measurement_archive(built_stores, apply=True) == {"indexed": 0, "undescribed": 0}


@pytest.mark.usefixtures("optimizing_memory")
def test_a_reclaim_deletes_nothing_while_a_run_appends_and_only_what_its_plan_named_after(
    built_stores: Stores, compacted_rows: _Compacted
) -> None:
    """Paid answers deleted under a landing row, or past the plan consented to, are gone for good.
    One journey: the plan is what the apply is checked against."""
    archive, whole = built_stores.archive, compacted_rows.whole
    before = archive.population(whole)
    with _a_run_appending(built_stores):
        for apply in (False, True):
            refused = reclaim_dataset(built_stores, dataset="compacted", apply=apply)
            assert (refused.archive_writers, refused.files_touched) == (1, 0)
            assert archive.population(whole) == before, "a reclaim deleted beside a live producer"
    assert archive_datasets(built_stores).labels == ["compacted"], "the control offers no scope"
    plan = reclaim_dataset(built_stores, dataset="compacted")
    assert (plan.rows_moved, archive.population(whole)) == (2, before), "a dry run deleted"
    done = reclaim_dataset(built_stores, dataset="compacted", apply=True)
    assert (done.files_touched, done.rows_moved) == (plan.files_touched, plan.rows_moved)
    assert (
        archive.population(whole) == [] and archive.list_all(dataset_hash=whole.dataset_hash) == []
    )
    assert archive.keys(ArchiveFile.CELLS) == [], "a reclaim left the cell file it emptied"
    assert archive_datasets(built_stores).labels == [], "the control offers a dataset it emptied"


# Each step reads on from where the feed's last advance left it.
@pytest.mark.usefixtures("optimizing_memory")
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

    def raced(path: Path, *rows: dict[str, Any]) -> Appended:
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


@pytest.mark.usefixtures("optimizing_memory")
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


def test_a_cell_file_rewritten_to_its_size_in_one_tick_moves_its_signature(
    built_stores: Stores,
) -> None:
    # A graded population is cached on it: a shared version serves the grades of the rows replaced.
    archive, key = built_stores.archive, "0" * ADDRESS_HEX
    path = archive.cell_path(key)
    write_bytes(path, b'{"a": 1}\n')
    held = file_stat(path)
    before = archive.cell_signatures()[key]
    write_bytes(path, b'{"b": 2}\n')
    os.utime(path, ns=(held.st_atime_ns, held.st_mtime_ns))
    assert archive.cell_signatures()[key] != before, "a rewrite read as the version it replaced"


@pytest.mark.usefixtures("optimizing_memory")
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
    assert (moved_back.files_touched, moved_back.notes) == (1, ()), (
        "a clean restore refuses no file"
    )
    assert archive.population(whole) == before, "a restore did not return every moved field"


def _remembered_at(stores: Stores, scope: ArmOf, bank: Bank) -> set[str]:
    """Every answer a population read under the arm's *scope* holds."""
    with scoped(memory=scope):
        return {
            answer.answer
            for entry in list_populations(stores, bank)
            for answer in load_population(stores, entry, bank)
        }


class _OriginForks(NamedTuple):
    hop: CycleHop
    scorer: Scorer
    # The crowned arm of `line`'s round 1, what it paid, and the arm's memory before either fork.
    key: ArmKey
    paid: set[str | None]
    before: set[str]
    unmodified: CycleHop
    edited: CycleHop


@pytest.fixture(scope="module")
def origin_forks(tmp_path_factory: pytest.TempPathFactory, line: Shared) -> Shared:
    """`line` steered at round 1 off its crown twice, its origin unmodified then edited, run once
    for the module (~1.6 s on `line`'s): each fact adopts its own copy."""

    async def act(world: World) -> _OriginForks:
        (hop, scorer), stores = line.made, world.stores
        branch = closed_round(stores, hop, 1, scorer, moment=None)
        assert branch is not None and branch.election is not None
        key = branch.election.ended_on
        bank = stores.tenant_datasets.labelled(DATASET)
        assert bank is not None
        before = _remembered_at(
            stores, ArmOf(CampaignWalks(stores.campaigns, hop.campaign_id), ruler_version=0), bank
        )
        unmodified = await world.fork(hop, 1, candidate_id=str(key), start=ForkStart())
        # An edited origin re-measures, so the fork walks answers no cycle before it walked.
        [(node, prompt)] = list(branch.arms[key].individual.prompts.items())[:1]
        field, text = next(iter(prompt.fields.items()))
        edited = {
            node: prompt.model_copy(update={"fields": {**prompt.fields, field: f"{text} Anew."}})
        }
        start = ForkStart(origin_prompts=edited)
        steered = await world.fork(hop, 1, candidate_id=str(key), start=start)
        paid = {cell.facts.answer for cell in branch.rows(key)}
        return _OriginForks(hop, scorer, key, paid, before, unmodified.hop, steered.hop)

    return shared_run(tmp_path_factory, act, on=line)


# Its foreign answers are the `--controlled` leg's (`offline_run.py::run_controlled`): no arm's
# memory reads one. Here, the fence is its campaign's walks as memory is read, never as it opened.
@pytest.mark.usefixtures("optimizing_memory")
def test_a_controlled_arm_remembers_only_the_answers_its_campaign_walked(
    world: World, origin_forks: Shared
) -> None:
    stores, forks = world.adopt(origin_forks).stores, origin_forks.made
    bank = stores.tenant_datasets.labelled(DATASET)
    assert bank is not None
    walks = CampaignWalks(stores.campaigns, forks.hop.campaign_id)
    arm = ArmOf(walks, ruler_version=0)
    after = _remembered_at(stores, arm, bank)
    assert forks.before < after, (
        "a cycle the campaign forked after the arm's scope opened is not its own"
    )
    assert after <= walks.read(WALKED_ANSWERS), "the arm remembers an answer it never walked"
    with scoped(memory=arm):
        assert sample_fold_rows(stores, dataset_hash=bank.dataset_hash) == [], (
            "an arm read the shared fold every campaign writes"
        )


# A fork re-walks round 1 as its parent did: what it files moves a population, its replays none.
@pytest.mark.usefixtures("optimizing_memory")
def test_an_arms_index_refresh_reloads_only_the_populations_its_walks_moved(
    world: World, line: Shared, monkeypatch: pytest.MonkeyPatch
) -> None:
    adopted, (hop, scorer) = world.adopt(line), line.made
    stores = adopted.stores
    bank = stores.tenant_datasets.labelled(DATASET)
    assert bank is not None
    arm = ArmOf(CampaignWalks(stores.campaigns, hop.campaign_id), ruler_version=0)
    signatures = stores.archive.cell_signatures()
    with scoped(memory=arm):
        admitted = frozenset(bank.position_of.values())
        index = SampleIndex.ensure_for(stores, scorer=scorer, bank=bank, sample_ids=admitted)
        assert index is not None
        held = index.records()
    asyncio.run(adopted.fork(hop, 1))
    moved = {k for k, v in stores.archive.cell_signatures().items() if signatures.get(k) != v}
    loaded: list[str] = []
    load = archive_queries.load_population

    def spy(stores: Stores, entry: ArchiveEntry, bank: Bank) -> list[FiledAnswer]:
        loaded.append(entry.config_key)
        return load(stores, entry, bank)

    monkeypatch.setattr(archive_queries, "load_population", spy)
    with scoped(memory=arm):
        index.refresh(stores, scorer=scorer)
        assert index.records() != held, "the index never folded the cells the fork filed"
    assert moved and sorted(loaded) == sorted(moved), (
        "a refresh reloaded a population no walk moved, or missed one that moved"
    )


# The re-cut moves the name the relabel placed on the bank.
@pytest.mark.usefixtures("optimizing_memory")
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
    replays = ReplayFeed(archive, compacted).advance()
    assert {s.id for s in renamed.samples if s.key in replays} == {
        0,
        1,
    }, "a cell filed under the bank stopped replaying once its name moved"

    # A re-cut drops q1 and moves q2: an old answer opens at its own bank's slot, and a holdout
    # read under the old bank stays spent under the new one.
    q1, q2 = compacted_bank.samples
    recut = Bank.of([Sample(id=0, query="q_new", ground_truth="g"), q2])
    built_stores.tenant_datasets.put_bank(recut)
    built_stores.tenant_datasets.relabel("compacted-renamed", recut.dataset_hash)
    opened = open_cell(built_stores, compacted_refs[q1.query])
    assert opened.position == compacted_bank.position_of[q1.key], (
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


async def test_a_deleted_campaigns_cells_stay_the_memory_of_the_next_campaign_on_its_dataset(
    world: World, line: Shared
) -> None:
    adopted, (a, _) = world.adopt(line), line.made
    stores = adopted.stores

    def memory(campaign_id: str, bank: Bank) -> set[str]:
        """Every answer a population read at an optimizing run of *campaign_id*'s scope holds."""
        with scoped(memory=OpenAt(CampaignWalks(stores.campaigns, campaign_id))):
            return {
                answer.answer
                for entry in list_populations(stores, bank)
                for answer in load_population(stores, entry, bank)
            }

    # B is minted after A is deleted, on A's bank: its own walks name none of A's cells.
    bank = stores.tenant_datasets.labelled(DATASET)
    assert bank is not None
    held = memory(a.campaign_id, bank)
    assert held, "vacuous: campaign A filed nothing"
    await CommandDispatcher(stores).dispatch(
        CommandCall(DeleteCampaignPayload(campaign_id=a.campaign_id), door=Door.HOST)
    )
    assert not stores.campaigns.campaign_root_dir(a.campaign_id).exists(), "guard"
    b = await adopted.minted(rows=32)
    assert held <= memory(b.campaign_id, bank), (
        "a deleted campaign took its cells out of the archive's memory"
    )


# 3. Replayed decisions, and forks


@pytest.fixture(scope="module")
def line(tmp_path_factory: pytest.TempPathFactory) -> Shared:
    """Potter's three rounds on 32 rows, run once for the module (~2.4 s): each fact takes
    `world.adopt(line)`, its own copy, at ~25 ms."""

    async def act(world: World) -> tuple[CycleHop, Scorer]:
        ran = await world.campaign(rounds=3, rows=32)
        return ran.hop, ran.session.state.scoring.scorer

    return shared_run(tmp_path_factory, act)


def _round(**kw: Any) -> RoundResult:
    return RoundResult(
        **{
            "folds": {},
            "election": None,
            "origin_panel": OriginPanel.unread(ReadingState.NOT_HELD, RoundEnding.HELD),
            "health": degradation_health(),
            "optimizer_state": optimizer_state(),
        }
        | kw
    )


def _minted(
    stores: Stores, offline: Router, tmp_path: Path, name: str = "world"
) -> tuple[CycleHop, CycleEventLog]:
    """A campaign minted into *stores* by the world's real mint, no round run: its root cycle and
    the ledger a held handle appends to."""
    hop = asyncio.run(World(stores, offline, tmp_path / name).minted())
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


_STEERED = ForkSpec(
    trigger=ForkTrigger.OPERATOR_STEERED,
    reason="",
    issued_by=SYSTEM_PRINCIPAL,
    from_candidate=RoundArm(round=1, arm=ArmKey("c1")),
)


def _writes_under(
    stores: Stores, hop: CycleHop, held: CycleEventLog
) -> dict[str, Callable[[], object]]:
    """Every way a write lands under a cycle: a held ledger handle, an index write, a lock."""
    layout = CycleLayout(stores.campaigns.cycle_dir(hop))
    return {
        "a held handle's append": lambda: held.append(RunPhaseRecord.stop(StopReason.CRASHED)),
        "an index write": lambda: write_json(layout.manifest, {}),
        "a producer lock": lambda: take_lock(layout.producer_lock, wait_s=0),
    }


def test_a_store_off_a_workspace_shape_is_refused(tmp_path: Path) -> None:
    # Off them, io's anchor never binds: a write into a deleted cycle re-makes it.
    for root in (tmp_path / "projects", user_data_root() / "projects" / "x" / "projects"):
        with pytest.raises(ValueError):
            build_stores(default_identity(), projects_root=root)


def test_a_write_past_its_campaigns_delete_answers_not_found_and_makes_no_campaign_dir(
    built_stores: Stores,
    offline: Router,
    tmp_path: Path,
) -> None:
    hop, held = _minted(built_stores, offline, tmp_path)
    assert built_stores.campaigns.delete_campaign(hop.campaign_id, keep_results=False)
    writes = _writes_under(built_stores, hop, held) | {
        "a fork mint": lambda: built_stores.campaigns.mint_cycle(
            hop.model_copy(update={"cycle_id": "c_fork"}),
            CycleMintedRecord(
                parent=f"../{hop.cycle_id}", forked_at_offset=0, cut_round=0, fork=_STEERED
            ),
        ),
    }
    for what, write in writes.items():
        with pytest.raises(NotFoundError):
            write()
        assert not built_stores.campaigns.campaign_root_dir(hop.campaign_id).exists(), (
            f"{what} after the delete re-made a campaign no manifest names"
        )


def test_a_write_past_its_cycles_cleanup_answers_not_found_and_makes_no_cycle_dir(
    built_stores: Stores,
    offline: Router,
    tmp_path: Path,
) -> None:
    root, _ = _minted(built_stores, offline, tmp_path)
    stub = _mint_sibling(built_stores.campaigns, root, "cycle_stub", _STEERED, cut_round=0)
    held = CycleEventLog.open(CycleDir(built_stores.campaigns.cycle_dir(stub)))
    assert cleanup_stub_fork_if_empty(campaign_store=built_stores.campaigns, hop=stub)[0]
    for what, write in _writes_under(built_stores, stub, held).items():
        with pytest.raises(NotFoundError):
            write()
        assert not built_stores.campaigns.cycle_dir(stub).exists(), (
            f"{what} after the cleanup re-made a cycle its campaign no longer lists"
        )


def test_a_draft_ledger_append_past_its_discard_answers_not_found_and_makes_no_draft_dir(
    built_stores: Stores,
) -> None:
    drafts, draft_id = built_stores.drafts, "d_0123456789abcdef"
    make_dirs(drafts.draft_dir(draft_id))
    held = CycleEventLog.open(CycleDir(drafts.draft_dir(draft_id)))
    held.append(RoundWarningRecord(kind="layer_parse_failure", message="billed"))
    drafts.discard(drafts.hold(draft_id))
    with pytest.raises(NotFoundError):
        held.append(RoundWarningRecord(kind="layer_parse_failure", message="past the discard"))
    assert not drafts.draft_dir(draft_id).exists(), "a held handle re-made a discarded draft"


def test_a_cycle_deleted_under_a_replace_reads_not_found_and_its_projection_warns_nothing(
    built_stores: Stores, offline: Router, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hop, held = _minted(built_stores, offline, tmp_path)
    cycle = built_stores.campaigns.cycle_dir(hop)
    said: list[type[Exception]] = []

    class _Deriving(Projection):
        derives = "derived.json"

        def _handle_round_warning(self, record: RoundWarningRecord) -> None:  # noqa: ARG002 -- routed by its annotation
            try:
                write_json(cycle / self.derives, {})
            except Exception as fault:
                said.append(type(fault))
                raise

    replace = os.replace

    def deleting_first(src: str, dst: str, *args: object, **kwargs: object) -> None:
        shutil.rmtree(cycle)
        replace(src, dst, *args, **kwargs)  # type: ignore[arg-type]

    with LedgerIndex.of(held.path).projecting() as holding, monkeypatch.context() as patched:
        holding.add(_Deriving())
        patched.setattr(os, "replace", deleting_first)
        # A warning appended into the gone cycle would raise here, into the append that landed.
        held.append(RoundWarningRecord(kind="layer_parse_failure", message="m"))
    assert said == [NotFoundError], "a replace under a deleted cycle said its loss another way"
    assert not cycle.exists(), "the projection's fault re-made the deleted cycle"


def test_a_replace_whose_temporary_was_swept_under_a_standing_cycle_raises_as_itself(
    built_stores: Stores, offline: Router, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A reset's `**/.*.tmp` sweep in the window before the replace: the cycle stands, so no "gone".
    hop, _ = _minted(built_stores, offline, tmp_path)
    cycle = built_stores.campaigns.cycle_dir(hop)
    replace = os.replace

    def swept_first(src: str, dst: str, *args: object, **kwargs: object) -> None:
        os.unlink(src)
        replace(src, dst, *args, **kwargs)  # type: ignore[arg-type]

    for path in (cycle / "index.json", cycle / "rounds" / "round_0001.json"):
        make_dirs(path.parent)
        with monkeypatch.context() as patched:
            patched.setattr(os, "replace", swept_first)
            with pytest.raises(FileNotFoundError):
                write_json(path, {})


@pytest.mark.skipif(sys.platform == "win32", reason="Windows deletes no cycle under a held lock")
def test_an_append_whose_cycle_is_deleted_before_its_cut_answers_not_found(
    built_stores: Stores, offline: Router, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hop, held = _minted(built_stores, offline, tmp_path)
    cycle = built_stores.campaigns.cycle_dir(hop)
    with open(held.path, "ab") as torn:
        torn.write(b'{"torn')

    def deleting_first(name: str, mode: str, *args: Any, **kwargs: Any) -> IO[bytes]:
        if mode == "r+b":
            shutil.rmtree(cycle)
        return open(name, mode, *args, **kwargs)

    monkeypatch.setattr(store_io, "open", deleting_first, raising=False)
    with pytest.raises(NotFoundError):
        held.append(RoundWarningRecord(kind="layer_parse_failure", message="past the delete"))
    assert not cycle.exists(), "the cut re-made the deleted cycle"


def test_a_relative_write_into_a_deleted_campaign_answers_not_found(
    built_stores: Stores, offline: Router, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hop, _ = _minted(built_stores, offline, tmp_path)
    root = built_stores.campaigns.campaign_root_dir(hop.campaign_id)
    shutil.rmtree(root)
    with monkeypatch.context() as patched:
        patched.chdir(root.parent)
        with pytest.raises(NotFoundError):
            write_json(Path(root.name) / "manifest.json", {})
    assert not root.exists(), "a relative path re-made a deleted campaign"
    # A linked spelling (POSIX: an unprivileged Windows user may make no symlink).
    if sys.platform != "win32":
        (tmp_path / "linked").symlink_to(root.parent)
        with pytest.raises(NotFoundError):
            write_json(tmp_path / "linked" / root.name / "manifest.json", {})
        assert not root.exists(), "a linked path re-made a deleted campaign"
    # An 8.3 short spelling of the same dir, where the volume keeps one.
    if sys.platform == "win32":
        import ctypes

        short = ctypes.create_unicode_buffer(32768)
        ctypes.windll.kernel32.GetShortPathNameW(str(root.parent), short, len(short))
        if short.value and short.value != str(root.parent):
            with pytest.raises(NotFoundError):
                write_json(Path(short.value) / root.name / "manifest.json", {})
            assert not root.exists(), "an 8.3 short path re-made a deleted campaign"


def test_a_standing_cycles_projection_fault_warns_whatever_its_type(
    built_stores: Stores, offline: Router, tmp_path: Path
) -> None:
    # A purged archive's `NotFoundError` and a sibling's unreadable index are no gone cycle.
    _, held = _minted(built_stores, offline, tmp_path)
    faults: tuple[Exception, ...] = (
        NotFoundError("archive no longer holds samples"),
        FileUnreadableError(FileKind.STORE, path="index.json", reason="line 0: invalid"),
    )
    for fault in faults:

        class _Faulting(Projection):
            derives = "derived.json"
            raised = fault

            def _handle_round_warning(self, record: RoundWarningRecord) -> None:  # noqa: ARG002 -- routed by its annotation
                raise self.raised

        with LedgerIndex.of(held.path).projecting() as holding:
            holding.add(_Faulting())
            held.append(RoundWarningRecord(kind="layer_parse_failure", message="m"))
    warned = [r.kind for r in ledger_records(held) if isinstance(r, RoundWarningRecord)]
    assert warned.count("derived_file_stale") == len(faults), (
        f"a standing cycle's stale derived file went unreported: {warned}"
    )


def test_a_write_refuses_only_a_campaign_or_cycle_by_its_place_never_a_dir_so_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def refused(write: Callable[[], object]) -> bool:
        try:
            write()
        except NotFoundError:
            return True
        return False

    # A data root nested where a campaign would sit, its first write made before anything else.
    root = tmp_path.resolve() / "projects" / "x" / "campaigns" / "D"
    monkeypatch.setenv("PROMPTPOTTER_HOME", str(root))
    fresh = refused(lambda: write_json(root / "logs" / "x.json", {}))
    stores = workspace()
    tenant = workspace(default_identity("campaigns")).campaigns
    # Seated by hand: this data root is the fact's own, which the world's landing refuses once a
    # stray file stands in it.
    gone_campaign = CycleHop(campaign_id=_CAMPAIGN, cycle_id="cycle_kept")
    stores.campaigns.mint_cycle(gone_campaign)
    shutil.rmtree(stores.campaigns.campaign_root_dir(gone_campaign.campaign_id))
    cut = workspace(default_identity("cut"))
    gone_cycle = CycleHop(campaign_id=_CAMPAIGN, cycle_id="cycle_cut")
    cut.campaigns.mint_cycle(gone_cycle)
    shutil.rmtree(cut.campaigns.cycle_dir(gone_cycle))
    next_door = CycleHop(campaign_id=_CAMPAIGN, cycle_id="cycle_beside")
    cut.campaigns.mint_cycle(next_door)
    beside = cut.campaigns.cycle_dir(next_door)
    datasets = stores.tenant_datasets
    writes: dict[str, tuple[Callable[[], object], bool]] = {
        "a dataset named cycles": (
            lambda: write_text(datasets.dataset_dir("cycles") / ".staged" / "f.md", ""),
            False,
        ),
        "a dataset slug projects/a/campaigns/b": (
            lambda: write_text(
                datasets.dataset_dir("projects/a/campaigns/b") / ".staged" / "f.md", ""
            ),
            False,
        ),
        "a root mint in a tenant named campaigns": (
            lambda: tenant.mint_cycle(CycleHop(campaign_id="c1", cycle_id="k1")),
            False,
        ),
        "a write under a deleted campaign": (
            lambda: write_json(stores.campaigns.cycle_dir(gone_campaign) / "r.json", {}),
            True,
        ),
        "an append through '..' into a deleted cycle": (
            lambda: append_line(
                beside / ".." / gone_cycle.cycle_id / ".runtime" / "l.jsonl", b"{}"
            ),
            True,
        ),
    }
    assert {"a nested data root's first write": fresh} | {
        what: refused(write) for what, (write, _) in writes.items()
    } == {"a nested data root's first write": False} | {
        what: refuses for what, (_, refuses) in writes.items()
    }
    assert not stores.campaigns.campaign_root_dir(gone_campaign.campaign_id).exists()
    assert not cut.campaigns.cycle_dir(gone_cycle).exists(), "a '..' re-made a deleted cycle"


def test_a_write_under_a_level_that_stands_but_is_no_directory_raises_an_os_error(
    tmp_path: Path,
) -> None:
    (tmp_path / "file").write_text("")
    absent = tmp_path / "absent"
    if sys.platform == "win32":  # a symlink needs a privilege there; a junction does not
        import _winapi

        absent.mkdir()
        _winapi.CreateJunction(str(absent), str(tmp_path / "dangling"))
        absent.rmdir()
    else:
        (tmp_path / "dangling").symlink_to(absent)
    for level in ("file", "dangling"):
        with pytest.raises(OSError):
            write_json(tmp_path / level / "a" / "b.json", {})


def _keyed_ruler(rows: Sequence[Sample]) -> DeltaRuler:
    """A warm ruler on *rows* by their `Sample.key`, δ = slot − 4: an answer at θ tells most on the
    row whose δ sits nearest it."""
    slots = delta_ruler({int(s.id): float(s.id) - 4.0 for s in rows})
    return slots.model_copy(
        update={
            "delta": {s.key: slots.delta[str(s.id)] for s in rows},
            "delta_se": {s.key: slots.delta_se[str(s.id)] for s in rows},
        }
    )


_TEN = samples(10, "g")


def _row_knobs(**bend: Any) -> RowKnobs:
    return pobb_knobs(
        row_choice="informative", refit="per_round", shape="per_candidate", dispatch="batch"
    ).model_copy(update=bend)


def test_each_walk_leads_with_the_row_its_theta_reads_most_and_loses_none() -> None:
    walks = (ArmKey("c1"), ArmKey("c2"))
    fit = FieldRead(
        theta={walks[0]: -2.0, walks[1]: 3.0},
        variance=dict.fromkeys(walks, 1.0),
        p_best=dict.fromkeys(walks, 0.5),
        bar=dict.fromkeys(walks, 0.05),
    )
    warm = _keyed_ruler(_TEN)
    # knobs, the fit, the ruler, the round's θ, c1's calls out → the slot each walk leads with, and
    # the slots a block of one row a walk sends each.
    table: list[
        tuple[dict[str, str], FieldRead[ArmKey] | None, DeltaRuler | None, int, int, Any]
    ] = [
        # `per_round` refit reads the parent's θ for every walk, never the field's.
        ({}, fit, warm, 0, 0, ((4, 4), ([4], [4]))),
        # `per_decision` under `per_candidate`: one shared θ set, so both answer the same rows.
        ({"refit": "per_decision"}, fit, warm, 0, 0, ((2, 2), ([2], [2]))),
        # `per_decision` under `per_round`: each at its own θ, the block's two calls one a walk.
        ({"refit": "per_decision", "shape": "per_round"}, fit, warm, 0, 0, ((2, 7), ([2], [7]))),
        # Before a check first fits, the round's θ stands in for every walk's.
        ({"refit": "per_decision", "shape": "per_round"}, None, warm, 1, 0, ((5, 5), ([5], [5]))),
        # A call out keeps its seat at the head; the choice ranks only the rows behind it.
        ({}, fit, warm, 0, 1, ((0, 4), ([], [4]))),
        # `random`, or a cold ruler that tells every row alike, keeps the sampler's order.
        ({"row_choice": "random"}, fit, warm, 0, 0, ((0, 0), ([0], [0]))),
        ({}, fit, None, 0, 0, ((0, 0), ([0], [0]))),
    ]
    for bend, read, ruler, theta, out, (led, sent) in table:
        view = View(
            {key: WalkView([], _TEN, out if key == walks[0] else 0, len(_TEN)) for key in walks},
            opening=False,
        )
        ground: RowGround[ArmKey] = RowGround(ruler, float(theta), 4, 1, {})
        chosen = send_rows(view, {}, read, _row_knobs(**bend), ground)
        assert tuple(int(chosen.order[key][0].id) for key in walks) == led, (
            f"{bend} at θ={theta}: the walks led with the wrong rows"
        )
        assert tuple([int(s.id) for s in chosen.sends[key]] for key in walks) == sent, (
            f"{bend} at θ={theta}: the block sent the wrong rows"
        )
        for key in walks:
            assert sorted(int(s.id) for s in chosen.order[key]) == list(range(10)), (
                f"{bend}: the choice lost or added a row"
            )


def test_a_rolling_decision_sends_none_where_the_calls_out_can_settle_the_cut() -> None:
    # ε(n) 0.05: `contested` and `idle` sit just above it, `sure` far past it.
    read = FieldRead(
        theta={},
        variance=dict.fromkeys(map(ArmKey, ("contested", "idle", "sure")), 1.0),
        p_best={ArmKey("contested"): 0.06, ArmKey("idle"): 0.06, ArmKey("sure"): 0.9},
        bar=dict.fromkeys(map(ArmKey, ("contested", "idle", "sure")), 0.05),
    )
    rows, deep = samples(6, "r"), sheet(measurements([1.0] * 6)).cells
    # walk → its depth's cells, its calls out, and the new calls a decision then sends it.
    table = {
        # One call out on a decision one answer can move across its bar: it waits on that call.
        "contested": (deep, 1, 0),
        # None out: nothing can settle it, so it is sent the one call that can.
        "idle": (deep, 0, 1),
        # No answer moves it to its bar: every row its budget takes is sent at once.
        "sure": (deep, 0, 6),
        # Unread yet: sent the calls that bring it to the depth the cut first reads.
        "young": ((), 0, 4),
    }
    knobs = _row_knobs(row_choice="random", dispatch="rolling")
    ground: RowGround[ArmKey] = RowGround(None, 0.0, 4, 1, {})

    def sent(outs: Mapping[str, int]) -> dict[str, int]:
        view = View(
            {
                ArmKey(name): WalkView(taken, rows, outs[name], len(taken) + len(rows))
                for name, (taken, _, _) in table.items()
            },
            opening=False,
        )
        return {key: len(s) for key, s in send_rows(view, {}, read, knobs, ground).sends.items()}

    first = sent({name: out for name, (_, out, _) in table.items()})
    assert first == {name: calls for name, (_, _, calls) in table.items()}, (
        f"a rolling decision sent {first}"
    )
    settled = sent({name: out + first[name] for name, (_, out, _) in table.items()})
    assert settled == dict.fromkeys(table, 0), (
        f"a decision whose calls out can settle every walk sent {settled}"
    )


def test_the_round_names_the_row_choice_its_race_ran() -> None:
    warm = _keyed_ruler(_TEN)
    for row_choice, ruler, ran in (
        ("informative", warm, "informative"),
        # A cold ruler tells every row alike: `informative` there runs the sampler's order.
        ("informative", None, "cold"),
        ("random", warm, "random"),
    ):
        assert _row_knobs(row_choice=row_choice).rows_ran(ruler) == ran, (
            f"{row_choice} on a {'warm' if ruler else 'cold'} ruler reported as another choice"
        )


def _cut(
    walked: Mapping[ArmKey, CellSheet], parent: CellSheet, **knobs: Any
) -> tuple[dict[ArmKey, Any], EliminationCut | None]:
    """PoBB's check on *walked* beside the parent's prior, and the record a cut of `c1` files."""
    check = PoBBCheck(
        pobb_knobs(**knobs), n_min=4, ruler=_keyed_ruler(_TEN), parent=ArmKey("parent")
    )
    check.register_completed(parent, key=ArmKey("parent"))
    stops = check.check({ArmKey("parent"): parent, **walked}, {key: len(_TEN) for key in walked})
    stop = stops.get(ArmKey("c1"))
    return stops, None if stop is None else EliminationCut.of(stop, ArmKey("c1"), ArmKey("parent"))


def _graded_ten(grades: Mapping[int, float | None]) -> CellSheet:
    return sheet(
        measurement(i, g, sample_key=_TEN[i].key)
        if g is not None
        else measurement(i, None, sample_key=_TEN[i].key, error="boom", error_category="SERVER")
        for i, g in grades.items()
    )


def test_a_cut_replays_on_the_cells_it_was_fit_on_and_on_its_rivals_grades() -> None:
    parent = _graded_ten(dict.fromkeys(range(10), 1.0))
    # An errored cell is read by no side: the cut's record names the seven its field was fit on.
    walked = _graded_ten({i: None if i == 2 else 1.0 if i == 4 else 0.0 for i in range(8)})
    stops, cut = _cut({ArmKey("c1"): walked}, parent, epsilon=0.30)
    assert cut is not None and stops[ArmKey("c1")].check_result["gate"] == "epsilon"
    assert cut.cells[ArmKey("c1")] == [_TEN[i].key for i in range(8) if i != 2]
    # The round banks the parent re-read on its cells beside the arm, as the loop files it.
    banked = {ArmKey("c1"): walked, ArmKey("parent"): parent}
    assert (
        replay_all_mismatches(_round(round=3, cells=banked), [decision(cut, True, node=None)]) == []
    ), "the cut the live rule made is not the cut its own record replays"
    # A rival's re-grade reaches the replay: a parent that now misses every cell cuts nothing.
    regraded = {**banked, ArmKey("parent"): _graded_ten(dict.fromkeys(range(10), 0.0))}
    [rival_moved] = replay_all_mismatches(
        _round(round=3, cells=regraded), [decision(cut, True, node=None)]
    )
    assert rival_moved.current_outcome is False, "the replay read its rival's grades by value"


def test_a_per_round_cut_reads_rows_its_rivals_never_answered_and_its_record_replays() -> None:
    parent = _graded_ten(dict.fromkeys(range(10), 1.0))
    # Apart: c1 lost 0..5, c2 won 4..9, so the two share two rows, short of the depth a cut reads.
    apart = {
        ArmKey("c1"): _graded_ten(dict.fromkeys(range(6), 0.0)),
        ArmKey("c2"): _graded_ten(dict.fromkeys(range(4, 10), 1.0)),
    }
    for shape, cuts in (("per_candidate", False), ("per_round", True)):
        stops, cut = _cut(apart, parent, shape=shape)
        assert (cut is not None) is cuts, f"{shape}: the union read cut {sorted(stops)}"
        if cut is None:
            continue
        assert set(cut.cells[ArmKey("c1")]) == {s.key for s in _TEN[:6]}, "the arm's cells moved"
        banked = {ArmKey("parent"): parent, **apart}
        assert (
            replay_all_mismatches(_round(round=3, cells=banked), [decision(cut, True, node=None)])
            == []
        ), "a per_round cut does not replay on its own record"


def test_every_replayer_reads_an_absent_arm_as_no_cut_no_gain_and_no_winner() -> None:
    unwalked, bar_arm = ArmKey("c1"), ArmKey("parent")
    objective = CapoObjective(length_penalty=0.0, length_norm=1)
    paired = PairedTCut(
        objective=objective,
        arm=unwalked,
        queries_scored=8,
        alpha=0.05,
        survivors=1,
        raced_against=[bar_arm],
        outscored_by=[bar_arm],
    )
    _, cut = _cut(
        {unwalked: _graded_ten(dict.fromkeys(range(8), 0.0))},
        _graded_ten(dict.fromkeys(range(10), 1.0)),
        epsilon=0.30,
    )
    assert cut is not None, "guard: the check cut the arm"
    for inputs, recorded in (
        (cut, True),
        (paired, True),
        (
            MinibatchTested(
                arm=unwalked, parent_id="p", parent_scores={"g0": 0.0}, sigma=0.0, sigma_prime=1.0
            ),
            True,
        ),
        (
            RoundWinner(
                arms=[unwalked],
                coverage_floor=1,
                bar=CrowningBar(statistic="paired_point", admission=0.75),
                next_parent="not_lost",
                parent=bar_arm,
                cells=None,
                pairs=RoundPairs(
                    path=(CycleHop(campaign_id="camp", cycle_id="cyc"),),
                    instrument_id="bank",
                    dataset_hash="ds",
                    measurands=tuple(grade_measurands(scorer_of())),
                ),
            ),
            [RoundEnding.CROWNED, unwalked],
        ),
        (PopulationKept(survivors=[unwalked], earlier={}), [unwalked]),
    ):
        [gone] = replay_all_mismatches(
            _round(round=3, cells={}),
            [decision(inputs, recorded, node=None)],
        )
        assert gone.current_outcome in (False, None, [], [RoundEnding.HELD, None]), (
            f"{inputs.kind}: an absent arm's decision replayed"
        )
    # The reference's reading: each survivor on every cell it was read on, earlier rounds' included,
    # so an incumbent's deeper blocks outrank an offspring's one lucky block.
    chars = {"target_prompt_chars": 10}
    lucky = sheet(
        measurements([1.0, 1.0, 0.0, 0.0], sample_ids=[10, 11, 12, 13], pipeline_data=chars)
    )
    rows = {unwalked: lucky, bar_arm: sheet(measurements([0.0] * 4, pipeline_data=chars))}
    for earlier, ranked in (
        ({bar_arm: {f"e{i}": 1.0 for i in range(8)}}, [bar_arm, unwalked]),
        ({}, [unwalked, bar_arm]),
    ):
        survived = PopulationKept(survivors=[unwalked, bar_arm], earlier=earlier)
        assert survived.rank(rows) == ranked, f"population_kept on {earlier} ranked otherwise"


def _selected(stores: Stores, hop: CycleHop) -> set[str]:
    """The selection every reader of what stands serves: the ledger, the subtree, the index."""
    index = stores.campaigns.load(hop, moment=None)
    assert index is not None
    readers = (
        stores.campaigns.standing_rounds(hop, moment=None).standing,
        subtree_cycle(stores, (hop,)).facts.standing,
        index.standing,
    )
    return {reader.selection.label for reader in readers}  # type: ignore[union-attr]


def _candidates(stores: Stores, hop: CycleHop) -> list[tuple[int, HierarchyCandidate]]:
    """Each candidate the subtree serves, by the round it hangs from."""
    cycle = subtree_cycle(stores, (hop,))
    return [
        (r.facts.round, c)  # type: ignore[union-attr]
        for r in cycle.children
        for c in r.children
        if isinstance(c, HierarchyCandidate)
    ]


@pytest.mark.usefixtures("optimizing_memory")
def test_a_rewind_retracts_every_election_close_and_crown_past_it_for_every_reader(
    world: World, line: Shared
) -> None:
    stores, (hop, _) = world.adopt(line).stores, line.made
    crowned = stores.campaigns.standing_rounds(hop, moment=None).crowns
    assert len(crowned) > 1 and len(_selected(stores, hop)) == 1, "guard: a crown past the origin"
    stores.campaigns.rewind_to_round(hop, 0)
    stood = stores.campaigns.standing_rounds(hop, moment=None)
    assert list(stood.rounds) == list(stood.elections) == [0] and stood.crowns == {0: crowned[0]}, (
        "an election, close or crown stood past the rewind"
    )
    assert len(_selected(stores, hop)) == 1, (
        "the ledger, the subtree and the index disagree on the selection"
    )
    assert {rnd for rnd, _ in _candidates(stores, hop)} == {0}, (
        "a candidate of a retracted round is still served"
    )


@pytest.mark.usefixtures("optimizing_memory")
def test_a_resume_rewinding_that_halts_on_a_divergence_below_it_displaces_no_round_past_it(
    world: World, line: Shared
) -> None:
    adopted, (hop, _) = world.adopt(line), line.made
    closed = adopted.stores.campaigns.standing_rounds(hop, moment=None).last_closed
    assert closed is not None
    adopted.edit_proposer()
    halted = asyncio.run(adopted.resume(hop, from_round=closed - 1))
    assert halted.result.stop_reason is StopReason.DIVERGED, "guard: the edit diverged"
    assert adopted.stores.campaigns.standing_rounds(hop, moment=None).last_closed == closed, (
        "a resume its divergence halted had already rewound the line"
    )


@pytest.mark.usefixtures("optimizing_memory")
def test_a_line_rebased_onto_a_fork_names_the_fork_in_its_stop(world: World, line: Shared) -> None:
    adopted, (hop, _) = world.adopt(line), line.made
    adopted.edit_proposer()
    forked = asyncio.run(adopted.resume(hop, on_divergence=OnDivergence.FORK))
    assert forked.hop != hop, "guard: the divergence forked"
    stop = adopted.stores.campaigns.last(hop, RunPhaseRecord)
    assert stop is not None and isinstance(stop.state, Ended), "guard: the parent ended"
    assert stop.state.stop_reason is StopReason.REBASED, "guard: the parent rebased"
    assert forked.hop.cycle_id in stop.state.detail and "resume_divergence" in stop.state.detail, (
        f"the parent's stop names no fork it continues on: {stop.state.detail!r}"
    )


@pytest.mark.usefixtures("optimizing_memory")
def test_a_round_the_archive_lost_or_a_rewind_past_the_ledger_offers_a_resume_the_terminal_takes(
    world: World, line: Shared
) -> None:
    # Each act is printed in the flag its payload declares: `--from-round` parsed nowhere.
    stores, (hop, scorer) = world.adopt(line).stores, line.made
    last = stores.campaigns.standing_rounds(hop, moment=None).last_closed
    assert last is not None
    shutil.rmtree(stores.archive.base_dir / MEASUREMENTS_DIR)
    with pytest.raises(NotFoundError) as lost:
        closed_round(stores, hop, last, scorer, moment=None)
    with pytest.raises(BadRequestError) as past:
        stores.campaigns.rewind_to_round(hop, last + 9)
    taken = [
        cli_main._PARSER.parse_args(spelled.split())
        for refused in (lost.value, past.value)
        for spelled in re.findall(r"`([^`]+)`", "".join(acts_line(refused.next_acts)))
    ]
    assert [(t.command, t.resume_from_round) for t in taken] == [
        ("resume", last - 1),
        ("resume", None),
    ], "a lost round's act resumed past it, or an act was spelled in a flag the terminal lacks"


@pytest.mark.usefixtures("optimizing_memory")
def test_a_reentered_rounds_arm_folds_only_its_new_walk(world: World, line: Shared) -> None:
    adopted, (hop, scorer) = world.adopt(line), line.made
    stores = adopted.stores
    entered = closed_round(stores, hop, 1, scorer, moment=None)
    assert entered is not None
    key, state = next(iter(entered.arms.items()))
    stores.campaigns.rewind_to_round(hop, 0)
    # Every live fold reads the rewind off its record.
    folds = Narration()
    walk = ArmRecord(round=1, arm=key, state=state)
    [cell] = arm_walk(
        CycleEventLog(stores.projects_root / "rewalk.jsonl"),
        [(0, ("k0", "a", False))],
        rnd=1,
        individual=state.individual.id,
        role=MeasurementRole.PANEL,
    )
    [rewind] = [
        rec
        for rec in adopted.records(hop)
        if isinstance(rec, RoundEnteredRecord) and rec.rewound and rec.round == 1
    ]
    folds.announce(walk)
    folds.basis = FoldBasis.of(scorer, pipeline_schema([]))
    folds.take(cell)
    assert folds.fold(1, walk.arm) is not None, "guard"
    folds.enter(rewind.round)
    with pytest.raises(LookupError):
        folds.take(cell)
    folds.announce(walk)
    assert folds.fold(1, walk.arm) is None, (
        "a re-announced arm folded the cells of the pass a rewind retracted"
    )


@pytest.mark.usefixtures("optimizing_memory")
def test_an_uncrowned_rerun_of_a_rewound_round_never_takes_the_stand(
    world: World, line: Shared
) -> None:
    adopted, (hop, _) = world.adopt(line), line.made
    crowned = adopted.stores.campaigns.standing_rounds(hop, moment=None).crowns
    rerun = max(crowned) + 1
    asyncio.run(adopted.resume(hop, from_round=rerun))
    stood = adopted.stores.campaigns.standing_rounds(hop, moment=None)
    assert stood.crowns == crowned and rerun in stood.rounds, "guard: the rerun crowned nothing"
    standing = {c.facts.individual for _, c in _candidates(adopted.stores, hop) if c.facts.stands}
    assert standing == {crowned[max(crowned)]}, "an uncrowned re-run took the stand"


# An inode number outlives its file, so only the bytes tell the old file's tail from a new file.
@pytest.mark.usefixtures("optimizing_memory")
def test_a_ledger_reminted_onto_its_freed_inode_grown_past_the_old_one_is_read_as_itself(
    world: World, line: Shared
) -> None:
    stores, (hop, _) = world.adopt(line).stores, line.made
    asked = ForkCyclePayload(campaign_id=hop.campaign_id, cycle_id=hop.cycle_id, round=1)
    cut = hop.model_copy(
        update={"cycle_id": cut_operator_fork(stores, asked, CycleStart()).take(stores)}
    )
    # A reader indexed the cut's ledger before it was replaced.
    stores.campaigns.standing_rounds(cut, moment=None)
    old, grown = stores.campaigns.layout(cut).ledger, stores.campaigns.layout(hop).ledger
    assert grown.stat().st_size > old.stat().st_size, "guard"
    old.write_bytes(grown.read_bytes())
    assert (
        stores.campaigns.standing_rounds(cut, moment=None).crowns
        == stores.campaigns.standing_rounds(hop, moment=None).crowns
    ), "a re-minted ledger was read as the tail of the file it replaced"


# Disposable, so a re-run remedies it.
def test_an_export_this_build_cannot_read_is_refused_as_an_export(
    world: World, line: Shared
) -> None:
    stores, (hop, _) = world.adopt(line).stores, line.made
    stores.campaigns.layout(hop).export.write_text("{not an export", encoding="utf-8")
    with pytest.raises(FileUnreadableError) as unread_export:
        stores.campaigns.read_export(hop)
    assert unread_export.value.kind is FileKind.EXPORT, "an export was refused as another file"


# Never half-read; the remedy it names deletes the campaign, as it stands.
@pytest.mark.usefixtures("optimizing_memory")
def test_an_unreadable_close_refuses_its_cycle_to_every_reader_and_the_remedy_keeps_paid_rows(
    world: World, line: Shared
) -> None:
    stores, (stranded, scorer) = world.adopt(line).stores, line.made
    retired: list[int] = []

    def _retire(lines: list[str]) -> tuple[list[str], None]:
        records = [json.loads(line) for line in lines]
        retired.append(max(i for i, rec in enumerate(records) if "optimizer_state" in rec))
        records[retired[0]]["a_field_no_build_declares"] = True
        return [json.dumps(rec) + "\n" for rec in records], None

    # `rewrite_lines` writes a fresh file: a ledger index takes a same-inode growth for an append.
    rewrite_lines(stores.campaigns.layout(stranded).ledger, _retire)
    paid = [entry.config_key for entry in stores.archive.list_all()]
    readers: dict[str, Callable[[], object]] = {
        "resume": lambda: stores.campaigns.standing_rounds(stranded, moment=None),
        "export": lambda: closed_rounds(stores, stranded, scorer, moment=None),
        "index": lambda: stores.campaigns.load(stranded, moment=None),
        "subtree": lambda: subtree_cycle(stores, (stranded,)),
        "dashboard": lambda: served_dashboard(stores, stranded),
    }
    for name, read in readers.items():
        with pytest.raises(FileUnreadableError) as unreadable:
            read()
        refused = unreadable.value
        assert refused.kind is FileKind.LEDGER, f"the {name} read refused no ledger"
        assert refused.details["reason"].startswith(f"line {retired[0]}:"), (
            f"the {name} read refused another line"
        )
        assert stranded.campaign_id in refused.details["path"], (
            f"the {name} read refused another campaign's ledger"
        )
    asyncio.run(
        CommandDispatcher(stores).dispatch(
            CommandCall(DeleteCampaignPayload(campaign_id=stranded.campaign_id), door=Door.HOST)
        )
    )
    assert not stores.campaigns.campaign_root_dir(stranded.campaign_id).exists(), (
        "the remedy left the refused campaign standing"
    )
    assert paid and [entry.config_key for entry in stores.archive.list_all()] == paid, (
        "the remedy took a paid measurement with the campaign"
    )


@pytest.mark.usefixtures("optimizing_memory")
def test_an_unmodified_fork_origin_inherits_the_branch_points_reading_and_an_edited_one_rescores(
    world: World, origin_forks: Shared
) -> None:
    stores, forks = world.adopt(origin_forks).stores, origin_forks.made

    def origin_answers(fork: CycleHop) -> set[str | None]:
        origin = closed_round(stores, fork, 0, forks.scorer, moment=None)
        assert origin is not None and origin.election is not None
        return {cell.facts.answer for cell in origin.rows(origin.election.ended_on)}

    assert forks.paid and origin_answers(forks.unmodified) == forks.paid, (
        "an unmodified fork origin rescored what its parent paid for"
    )
    assert origin_answers(forks.edited).isdisjoint(forks.paid), (
        "an edited fork origin inherited a measurement it never earned"
    )


def _operator_fork(stores: Stores, hop: CycleHop, steer: ForkStart, **asked: Any) -> CycleHop:
    """An operator's fork of *hop* at round 2 cut and taken, its *steer* parsed as the fork door
    parses it."""
    campaign = stores.campaigns.campaign(hop.campaign_id)
    parsed = parse_cycle_start(steer, stores, CampaignCycle(campaign, hop.cycle_id))
    payload = ForkCyclePayload(campaign_id=hop.campaign_id, cycle_id=hop.cycle_id, round=2, **asked)
    return hop.model_copy(
        update={"cycle_id": cut_operator_fork(stores, payload, parsed).take(stores)}
    )


def _laid(stores: Stores, hop: CycleHop) -> CycleConfig:
    return cycle_config(
        stores, CampaignCycle(stores.campaigns.campaign(hop.campaign_id), hop.cycle_id)
    )


def _measured_in(world: World, hop: CycleHop, rnd: int) -> tuple[ArmKey, Measured]:
    """The first arm round *rnd* measured, with the params its record ran."""
    return next(
        (r.arm, r.state)
        for r in world.records(hop)
        if isinstance(r, ArmRecord) and r.round == rnd and isinstance(r.state, Measured)
    )


# No supersede, no inbox void, no moved pointer, no stub; nor does a cut past the last close.
@pytest.mark.usefixtures("optimizing_memory")
def test_a_rewind_its_launch_refuses_has_written_nothing_on_its_parent(
    world: World, line: Shared
) -> None:
    ran = world.adopt(line)
    stores, store, (hop, _) = ran.stores, ran.stores.campaigns, line.made

    def parent_standing() -> tuple[object, ...]:
        index = store.load(hop, moment=None)
        assert index is not None
        cycles = sorted(d.name for d in store.campaign_cycle_dirs(hop.campaign_id))
        return cycles, read_active_pointer(store.workspace), index.final

    standing = parent_standing()
    # The backend unreachable, so the launch at round 2 refuses as it opens.
    ran.net.backend.faults["/pipeline"] = [httpx.ConnectError("down")] * 8
    for refused_round in (2, 99):
        asked = ForkCyclePayload(
            campaign_id=hop.campaign_id,
            cycle_id=hop.cycle_id,
            round=refused_round,
            keep_rounds=True,
        )
        with pytest.raises(PotterError):
            asyncio.run(
                CommandDispatcher(stores, inline=Inline()).dispatch(
                    CommandCall(asked, door=Door.HOST)
                )
            )
        assert store.last(hop, CycleSupersededRecord) is None, (
            f"a refused rewind at round {refused_round} superseded its parent"
        )
        assert parent_standing() == standing, (
            f"a refused rewind at round {refused_round} left a stub or moved its parent"
        )


@pytest.mark.usefixtures("optimizing_memory")
def test_a_fork_that_raises_past_its_cut_before_its_take_leaves_no_stub(
    monkeypatch: pytest.MonkeyPatch, world: World, line: Shared
) -> None:
    ran = world.adopt(line)
    stores, store, (hop, _) = ran.stores, ran.stores.campaigns, line.made
    cycles = sorted(d.name for d in store.campaign_cycle_dirs(hop.campaign_id))

    def torn(*_args: object, **_kwargs: object) -> None:
        raise OSError("the write tore")

    # Its land (between the cut and the land), and its act on its parent (after its run is held).
    for owner, name in ((launch_opening, "claim_cycle"), (type(store), "act")):
        with monkeypatch.context() as tearing:
            tearing.setattr(owner, name, torn)
            asked = ForkCyclePayload(campaign_id=hop.campaign_id, cycle_id=hop.cycle_id, round=2)
            with pytest.raises(OSError, match="tore"):
                asyncio.run(
                    CommandDispatcher(stores, inline=Inline()).dispatch(
                        CommandCall(asked, door=Door.HOST)
                    )
                )
        assert sorted(d.name for d in store.campaign_cycle_dirs(hop.campaign_id)) == cycles, (
            f"a fork whose {name} raised past its cut left its stub cycle"
        )


# A withdraw whose delete fails keeps the stub, and the API's refusal body names it.
@pytest.mark.usefixtures("optimizing_memory")
def test_a_fork_whose_withdraw_fails_names_its_kept_stub_in_the_served_refusal(
    monkeypatch: pytest.MonkeyPatch, world: World, line: Shared
) -> None:
    ran = world.adopt(line)
    stores, store, (hop, _) = ran.stores, ran.stores.campaigns, line.made
    cycles = {d.name for d in store.campaign_cycle_dirs(hop.campaign_id)}

    def taken(*_args: object, **_kwargs: object) -> None:
        raise ConflictError("the cycle is taken")

    def held(*_args: object, **_kwargs: object) -> None:
        raise OSError("the stub is held")

    monkeypatch.setattr(launch_opening, "claim_cycle", taken)
    monkeypatch.setattr(fork_siblings, "cleanup_stub_fork_if_empty", held)
    asked = ForkCyclePayload(campaign_id=hop.campaign_id, cycle_id=hop.cycle_id, round=2)
    with pytest.raises(ConflictError, match="taken") as refused:
        asyncio.run(
            CommandDispatcher(stores, inline=Inline()).dispatch(CommandCall(asked, door=Door.HOST))
        )
    (stub,) = {d.name for d in store.campaign_cycle_dirs(hop.campaign_id)} - cycles
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    served = json.loads(bytes(asyncio.run(potter_error_handler(request, refused.value)).body))
    assert any(stub in note for note in served["notes"]), (
        f"the API's refusal body does not name the stub its failed withdraw kept: {served}"
    )


# The steered forks cut from the applied scenario's fork, and from each other.
@pytest.mark.usefixtures("optimizing_memory")
def test_an_applied_scenario_forks_at_its_round_keeps_the_holdout_and_carries_its_criterion(
    world: World, line: Shared
) -> None:
    stores, (hop, _) = world.adopt(line).stores, line.made
    store = stores.campaigns
    criterion = "0.9 * accuracy + 0.1 * (1 - mean_latency_s)"
    scenario = ForkStart(config_overrides=ConfigOverrides(scoring=criterion))
    applied = _operator_fork(stores, hop, scenario, keep_rounds=True)
    index = store.load(applied, moment=None)
    assert index is not None and index.fork is not None
    assert index.fork.issued_by.id == str(stores.identity.user_id), "the fork names no issuer"
    assert drawn_partition(store, applied) == drawn_partition(store, hop), (
        "a redrawn holdout reads the fork's bench lift on rows its parent never held out"
    )
    for kind in (RunPhaseRecord, CycleShareRecord):
        assert store.last(hop, kind) is not None and store.last(applied, kind) is None, (
            f"a fork read its parent's {kind.__name__}: its phase or its share is the parent's"
        )
    # OPERATOR_REWIND is the rebase branch, the one that lifts rounds 0..N-1; it stands on the
    # pick the parent's line stood on into the cut.
    crowns = store.standing_rounds(hop, moment=None).crowns
    stood = crowns[max(r for r in crowns if r < 2)]
    assert (index.fork.trigger, index.cut_round, index.fork.from_candidate) == (
        ForkTrigger.OPERATOR_REWIND,
        2,
        RoundArm(round=1, arm=ArmKey(stood)),
    ), "a rewind's record names another arm than the pick its parent's line stood on"
    scored = (_laid(stores, applied).config.scoring, _laid(stores, hop).config.scoring)
    assert scored[0] == criterion != scored[1], (
        "the scenario's criterion missed its fork, or wrote through to its parent"
    )
    # A steer outside what the campaign permits is the babysit act, and a fork of that fork lays
    # its start over its parent's: steering nothing more, it still runs babysat, and its mint
    # stamps the intervention every answer it files is graded by.
    permitted = permitted_narrowing(stores, store.campaign(hop.campaign_id).root_cycle)
    steer = ForkStart(pipeline_overlay={"llm_only": {"model": "x"}})
    babysat = _operator_fork(stores, applied, steer, keep_rounds=True)
    assert _laid(stores, babysat).steers_outside(permitted), "an unpermitted steer forked clean"
    back = _operator_fork(stores, babysat, ForkStart(), keep_rounds=True)
    onward = _laid(stores, back)
    assert (onward.start_overlay, onward.steers_outside(permitted), onward.config.scoring) == (
        {"llm_only": {"model": "x"}},
        True,
        criterion,
    ), "a fork of a babysat fork dropped what its line laid, or ran clean"
    for fork, babysit in ((applied, False), (babysat, True), (back, True)):
        index = store.load(fork, moment=None)
        assert index is not None and index.human_intervened is babysit, (
            f"{fork.cycle_id} runs {'babysat' if babysit else 'clean'}, its mint stamped otherwise"
        )


# With no edit it steers nothing and stamps no intervention; a field cleared in the panel drops
# the candidate's value, and the layer below shows.
@pytest.mark.usefixtures("optimizing_memory")
def test_a_panel_fork_off_a_candidate_runs_its_record_and_sends_only_the_operators_edit(
    world: World, line: Shared
) -> None:
    ran = world.adopt(line)
    stores, (hop, _) = ran.stores, line.made
    permitted = permitted_narrowing(stores, stores.campaigns.campaign(hop.campaign_id).root_cycle)
    candidate, measured = _measured_in(ran, hop, 2)
    ran_on = {n: c for n, c in measured.resolved_params.items() if n != "steps"}
    cleared = ForkStart(pipeline_overlay={"llm_only": {"route_order": None}})
    for start, laid in (
        (ForkStart(), ran_on),
        (
            cleared,
            {"llm_only": {k: v for k, v in ran_on["llm_only"].items() if k != "route_order"}},
        ),
    ):
        fork = _operator_fork(stores, hop, start, candidate_id=str(candidate), start=start)
        panel = _laid(stores, fork)
        index = stores.campaigns.load(fork, moment=None)
        assert (panel.start_overlay, panel.steers_outside(permitted)) == (laid, False), (
            "a panel fork laid other params than its candidate's record and its edit, or steered"
        )
        assert index is not None and not index.human_intervened, (
            "a panel fork steering inside the menu was stamped babysat"
        )


def test_a_rewind_fork_asked_to_rewind_its_parent_too_refuses() -> None:
    # `resume --rewind 2 --from 1`, as the terminal lays it: no run of its would rewind its parent.
    with pytest.raises(PayloadInvalidError):
        PayloadInvalidError.validating(
            ForkCyclePayload,
            {
                "campaign_id": "c",
                "cycle_id": "cycle_c",
                "round": 2,
                "keep_rounds": True,
                "mode": RunMode(from_round=1),
            },
        )


# It goes ahead on its own authority over the parent (bl1.B5).
@pytest.mark.usefixtures("optimizing_memory")
def test_a_fork_by_another_issuer_withdraws_the_launch_queued_on_its_parent(
    world: World, line: Shared
) -> None:
    stores, (hop, _) = world.adopt(line).stores, line.made
    room = [0]
    queue = JobRegistry(stores.jobs_dir, capacity=lambda _live: room[0])
    (parents_launch,) = request_jobs(
        stores=stores, job_registry=queue, holder=CampaignLaunch("ds1"), hop=hop
    )
    assert parents_launch.queued, "guard: the parent's launch holds a place in line"
    room[0] = 1
    other = dataclasses.replace(
        stores, identity=dataclasses.replace(stores.identity, claims={"principal": "sub-1"})
    )
    asked = ForkCyclePayload(
        campaign_id=hop.campaign_id, cycle_id=hop.cycle_id, round=2, keep_rounds=True
    )

    async def forked() -> None:
        dispatched = await CommandDispatcher(other, job_registry=queue, inline=Inline()).dispatch(
            CommandCall(asked, door=Door.HOST)
        )
        launched = dispatched.result
        assert isinstance(launched, Launched) and launched.held is not None
        # The journey asks what the held fork does to its parent, not what its run does next.
        with contextlib.suppress(RuntimeError):
            async with (await launched.held).closing:
                raise RuntimeError("the fork's run is not this journey's")

    asyncio.run(forked())
    withdrawn = queue.get(parents_launch.job_id)
    assert withdrawn is not None and withdrawn.released and withdrawn.queued is False, (
        "a fork left its parent's queued launch to run beside it"
    )
    assert stores.campaigns.holder(hop) is None, "a withdrawn launch still claims the parent"
    assert stores.campaigns.last(hop, CycleSupersededRecord) is not None, "the fork never took"


class _Displaced(NamedTuple):
    cut: ForkCut
    parent: CycleHop
    fork: CycleHop
    before: list[tuple[int, ArmKey]]


@pytest.fixture(scope="module")
def displaced(tmp_path_factory: pytest.TempPathFactory, line: Shared) -> Shared:
    """`line` (`parent`) forked at round 2 through the `fork_cycle` door (`fork`), and a campaign
    paused after round 1 with a fork cut off it and not yet taken (`cut`); run once for the
    module (~1.6 s on `line`'s): each fact adopts its own copy."""

    async def act(world: World) -> _Displaced:
        parent, _ = line.made
        before = sorted(world.stores.campaigns.standing_rounds(parent, moment=None).arms)
        fork = (await world.fork(parent, 2, keep_rounds=True)).hop
        paused = (await world.campaign(rounds=2, step_rounds=0)).hop
        asked = ForkCyclePayload(
            campaign_id=paused.campaign_id, cycle_id=paused.cycle_id, round=1, keep_rounds=True
        )
        cut = cut_operator_fork(world.stores, asked, CycleStart())
        return _Displaced(cut, parent, fork, before)

    return shared_run(tmp_path_factory, act, on=line)


@pytest.mark.usefixtures("optimizing_memory")
def test_a_forks_own_ledger_displaces_its_parents_prefix_once(
    world: World, displaced: Shared
) -> None:
    ran = world.adopt(displaced)
    store, (_, parent, fork, before) = ran.stores.campaigns, displaced.made
    on_parent = store.standing_rounds(parent, moment=None)
    on_fork = store.standing_rounds(fork, moment=None)
    own = {(r.round, r.arm) for r in ran.records(fork) if isinstance(r, ArmRecord)}
    assert sorted(on_parent.arms) == before, "the fork's rounds displaced its parent's own"
    assert {a for a in on_fork.arms if a[0] < 2} == {a for a in before if a[0] < 2}, (
        "the fork does not continue its parent's rounds below its cut"
    )
    assert {a for a in on_fork.arms if a[0] >= 2} == own and own, (
        "a parent round stood beside the fork's own, or the fork's own stood twice"
    )
    assert {r: k for r, k in on_fork.crowns.items() if r < 2} == {
        r: k for r, k in on_parent.crowns.items() if r < 2
    }, "the fork re-crowned its parent's prefix"


# A fork's line is its parent's below the cut: its bars lead with those candidates, and its Spend
# figure bills the chain its dashboard folds (its own appends alone read $0 as a rebase starts).
@pytest.mark.usefixtures("optimizing_memory")
def test_a_forks_served_line_leads_with_its_parents_rounds_and_bills_their_chain(
    displaced: Shared,
) -> None:
    _, parent, fork, _ = displaced.made
    with displaced.reading() as stores:
        tree = hierarchy_subtree(stores, Subject(fork.campaign_id).address, None, None, moment=None)
        dash = served_dashboard(stores, fork)
    cycles = {e.address: e for e in entries_under(tree) if isinstance(e, SubtreeCycle)}
    below = [
        c.address
        for rnd in cycles[Subject.of((parent,)).address].children
        if isinstance(rnd, HierarchyRound) and rnd.facts.round < 2
        for c in rnd.children
    ]
    fork_entry = cycles[Subject.of((fork,)).address]
    own = {
        c.address
        for rnd in fork_entry.children
        if isinstance(rnd, HierarchyRound)
        for c in rnd.children
    }
    assert isinstance(dash, ServedDashboard), "guard"
    line = [a.address for a in dash.line]
    assert below and sorted(line[: len(below)]) == sorted(below), (
        "the fork's line does not lead with its parent's candidates below the cut"
    )
    assert own and set(line[len(below) :]) == own, (
        "the fork's line past its cut is not the candidates its own cycle ran"
    )
    on_fork = fork_entry.facts
    # Tokens, exactly: the offline world bills no USD.
    assert dash.spend_by_round.rounds[:2] == [0, 1] and dash.spend_metered.billed_tokens > 0, (
        "guard: the prefix billed nothing, so no bill below tells the chain apart"
    )
    assert on_fork.spend.billed_tokens == dash.spend_metered.billed_tokens, (
        "the fork's Spend figure is not the bill its dashboard folds beside it"
    )


# Pure over the standing line: a cut hangs from the newest crown below it, round 0 its origin's.
@pytest.mark.usefixtures("optimizing_memory")
def test_a_line_stands_on_its_origin_until_an_election_crowns_another(
    world: World, displaced: Shared
) -> None:
    ran = world.adopt(displaced)
    standing = ran.stores.campaigns.standing_rounds(displaced.made.parent, moment=None)
    crowns = standing.crowns
    assert len(crowns) > 1, "guard: the line crowned past its origin"
    for cut_round in range(max(standing.rounds) + 2):
        below = max(r for r in crowns if r < max(cut_round, 1))
        assert fork_edge(standing, cut_round, None) == RoundArm(round=below, arm=crowns[below]), (
            f"a cut at {cut_round} stands on another candidate than the newest crown below it"
        )


def test_a_fork_of_a_line_that_never_walked_its_origin_is_refused_cutting_nothing(
    world: World,
) -> None:
    hop = asyncio.run(world.minted())
    cycles = world.stores.campaigns.cycle_dir(hop).parent
    with pytest.raises(ConflictError, match="measured no origin") as refused:
        asyncio.run(world.fork(hop, 0))
    assert refused.value.next_acts == (MINT_FRESH,), "the refusal names no fresh mint"
    assert [p.name for p in cycles.iterdir()] == [hop.cycle_id], "a refused fork cut a cycle"


@pytest.mark.usefixtures("optimizing_memory")
def test_a_steps_silence_is_the_familys_wherever_a_window_cuts_the_ray(
    world: World, displaced: Shared
) -> None:
    ran = world.adopt(displaced)

    def gaps(limit: int) -> dict[str, float]:
        ray = open_family_ray(ran.stores, (displaced.made.fork,), limit=limit, before=None)
        return {step.address: step.facts.gap_before_s for step in ray.window().steps}

    whole = gaps(10_000)
    assert len(whole) > 2, "guard"
    for limit in (1, 2, len(whole) - 1):
        windowed = gaps(limit)
        assert len(windowed) == limit and {s: whole[s] for s in windowed} == windowed, (
            f"a {limit}-step window re-measured the silence before its first step"
        )


# A holder declares its own stop.
@pytest.mark.usefixtures("optimizing_memory")
def test_a_supersede_ends_its_parent_only_where_no_producer_holds_it(
    world: World, displaced: Shared
) -> None:
    ran = world.adopt(displaced)
    stores, cut = ran.stores, displaced.made.cut

    def ended() -> StopReason | None:
        index = stores.campaigns.load(cut.parent, moment=None)
        assert index is not None
        return index.stop_reason

    assert ended() is None, "guard: the cut's parent stands paused"
    # Held as a server-launched run holds it, from a lock of its own process.
    lock = CycleLayout(stores.campaigns.cycle_dir(cut.parent)).producer_lock
    producer = take_lock(lock, wait_s=0)
    assert producer is not None
    with probe_lock(lock) as seen:
        assert seen, "a reader's probe missed the producer holding the cycle"
    cut.take(stores)
    producer.release()
    assert ended() is None, "a supersede ended its parent under the producer running it"
    lock.touch()
    with probe_lock(lock):
        assert not producer_lock.held(lock), (
            "a reader's probe in flight read as a producer, so a supersede would skip its stop"
        )
    cut.take(stores)
    assert ended() is StopReason.REBASED, "a superseded parent no producer holds runs on"


def _listed(stores: Stores, campaign_id: str) -> HierarchyCampaign:
    [listed] = list_hierarchy(
        stores,
        inside=(),
        dataset=None,
        lifecycle="all",
        campaign_id=campaign_id,
        attached_only=False,
        depth=1,
    ).entries
    assert isinstance(listed, HierarchyCampaign), "guard"
    return listed


# The hierarchy reads the line the supersede moved, and the bench chip its line's archive rows.
@pytest.mark.usefixtures("optimizing_memory")
def test_a_superseded_campaigns_line_bench_and_stand_are_its_forks(
    world: World, displaced: Shared
) -> None:
    stores, fork = world.adopt(displaced).stores, displaced.made.fork
    listed = _listed(stores, fork.campaign_id)
    assert listed.facts.live_cycle == Subject.of((fork,)).address, (
        "the campaign's line stayed on the cycle a supersede retired"
    )
    [line_cycle] = [c for c in listed.children if c.address == listed.facts.live_cycle]
    assert line_cycle.facts.standing is not None, "guard"
    assert all(c.facts.bench is None for c in listed.children if c is not line_cycle), (
        "the bench graded a cycle that does not hold the line"
    )
    assert [r for r in listed.figures if r.key == "bench"] == [
        r for r in line_cycle.figures if r.key == "bench"
    ], "the campaign's bench is not its line's"
    picked = line_cycle.facts.standing.selection
    assert picked is not None, "guard"
    # The fork's pick may be a round of the prefix it reads: it stands on the cycle owning that round.
    subtree = hierarchy_subtree(stores, listed.address, None, None, moment=None)
    line_stood = [
        e
        for cycle in entries_under(subtree)
        if isinstance(cycle, SubtreeCycle)
        for rnd in cycle.children
        if isinstance(rnd, HierarchyRound)
        for e in rnd.children
        if e.facts.stands
    ]
    assert [(e.facts.reading.arm.round, e.facts.reading.arm.key) for e in line_stood] == [
        (picked.round, picked.key)
    ], "the subtree stands a candidate other than the one the line's index selected"
    assert line_cycle.facts.standing.changes == line_stood[0].facts.reading.changes_description, (
        "the cycle's standing names another pick's change than the candidate it stands"
    )


# The bench chip grades archive rows, so a regrade at rest must answer the next ask with a body.
@pytest.mark.usefixtures("optimizing_memory")
def test_an_archive_write_moves_the_subtrees_validator_and_the_feeds_store_version(
    world: World, displaced: Shared, tmp_path: Path
) -> None:
    stores = world.adopt(displaced).stores
    address = _listed(stores, displaced.made.fork.campaign_id).address
    validator = subtree_version(stores, address)
    feed_jobs = JobRegistry(tmp_path / "feed" / "jobs", capacity=lambda _live: 1)
    fed = feed_versions(stores, feed_jobs)["store:"]
    filed(
        stores.archive,
        [archive_row("q_regraded", predicted="p")],
        filed_under(dataset_hash="regradeds"),
    )
    assert subtree_version(stores, address) != validator, (
        "an archive write left the subtree's validator standing: its BENCH chip goes stale behind a 304"
    )
    moved = feed_versions(stores, feed_jobs)["store:"]
    assert moved.process == fed.process and moved.n > fed.n, (
        "the feed's store version did not move forward on an archive write"
    )
    assert feed_versions(stores, feed_jobs)["store:"] == moved, "an unmoved store moved its version"


@pytest.mark.usefixtures("optimizing_memory")
def test_a_subtrees_validator_stands_while_another_campaign_of_its_store_appends(
    world: World, displaced: Shared
) -> None:
    # A tree re-asked on every append of its store rebuilds behind each other run's beat.
    stores, (_, parent, mounted, _) = world.adopt(displaced).stores, displaced.made
    address = _listed(stores, mounted.campaign_id).address
    family = {mounted.campaign_id, parent.campaign_id}
    other = next(c for c in stores.campaigns.list_campaign_ids() if c not in family)
    validator = subtree_version(stores, address)
    elsewhere = stores.campaigns.campaign(other).root_hop
    stores.campaigns.append(elsewhere, PricedKeyRecord(priced_key="elsewhere"))
    assert subtree_version(stores, address) == validator, (
        "another campaign's append moved the mounted subtree's validator"
    )
    stores.campaigns.append(mounted, PricedKeyRecord(priced_key="here"))
    assert subtree_version(stores, address) != validator, "its own append left it standing"


def test_a_hand_edit_to_an_authored_definition_moves_the_hierarchy_and_the_feed(
    world: World, displaced: Shared, tmp_path: Path
) -> None:
    # An authored tier has no writer: no stamp moves, so its version is the definition's own stat.
    stores = world.adopt(displaced).stores
    campaign = stores.campaigns.load_campaign(displaced.made.fork.campaign_id)
    assert campaign is not None
    definition = resolve_dataset(stores, campaign.dataset_name).directory / DEFINITION_FILE
    feed_jobs = JobRegistry(tmp_path / "feed" / "jobs", capacity=lambda _live: 1)
    etag, fed = hierarchy_version(stores, inside=()), feed_versions(stores, feed_jobs)["store:"]
    with definition.open("a", encoding="utf-8") as hand:
        hand.write("\n# an operator's hand edit\n")
    assert hierarchy_version(stores, inside=()) != etag, (
        "a hand edit to the definition left the hierarchy's ETag standing: a 304 serves its old card"
    )
    assert feed_versions(stores, feed_jobs)["store:"].n > fed.n, (
        "a hand edit to the definition left the account feed's store version standing"
    )


@pytest.mark.usefixtures("optimizing_memory")
def test_a_running_parents_pause_landing_after_its_supersede_keeps_it_superseded(
    world: World, displaced: Shared
) -> None:
    # The fork files its parent's pause for the loop, then supersedes it: the pause lands last.
    ran = world.adopt(displaced)
    stores, cut = ran.stores, displaced.made.cut
    cut.take(stores)
    stores.campaigns.append(
        cut.parent, RunPhaseRecord.stop(StopReason.PAUSED, cause=PauseCause.INTERRUPT)
    )
    index = stores.campaigns.load(cut.parent, moment=None)
    assert index is not None and index.superseded_by == cut.child.cycle_id, (
        "the parent's own pause erased its supersede: the line still reads as the parent's"
    )


# A start on the cycle a supersede retired would run its line twice, paid: no read offers one,
# and the launch door refuses it, naming the fork the line runs on.
@pytest.mark.usefixtures("optimizing_memory")
def test_a_superseded_cycle_offers_no_start_and_its_launch_door_refuses_one(
    world: World, displaced: Shared
) -> None:
    stores, parent, fork = world.adopt(displaced).stores, displaced.made.parent, displaced.made.fork
    listed = _listed(stores, fork.campaign_id)
    [retired] = [c for c in listed.children if c.address == Subject.of((parent,)).address]
    admission = retired.state.admission
    assert admission.offers is None and admission.refusal is not None, (
        "the retired cycle's run control offers a start beside the fork its line runs on"
    )
    assert fork.cycle_id in admission.refusal.message, "the refusal names no fork"
    start = StartRunPayload(campaign_id=parent.campaign_id, cycle_id=parent.cycle_id)

    async def launch() -> object:
        dispatcher = CommandDispatcher(stores, inline=Inline())
        return (await dispatcher.dispatch(CommandCall(start, door=Door.TERMINAL))).result

    with pytest.raises(ConflictError, match=fork.cycle_id):
        asyncio.run(launch())


def _cycle_of(hop: CycleHop) -> str:
    return Subject.of((hop,)).address


class _Family(NamedTuple):
    stores: Stores
    hops: dict[str, CycleHop]
    tree: HierarchyEntry


# A trunk; a rewind fork of it cut at 2 (kid) and one of the kid cut at 3 (grand); a steer off a
# round-1 arm the trunk never crowned. Every fact on it only reads.
@pytest.fixture(scope="module")
def family_tree(tmp_path_factory: pytest.TempPathFactory, displaced: Shared) -> _Family:
    """`displaced`'s parent as the trunk and its fork as the kid, a rewind fork of the kid and a
    steer off an uncrowned arm, run once for the module (~1.7 s on `displaced`'s): facts read it."""

    async def act(world: World) -> dict[str, CycleHop]:
        trunk, kid = displaced.made.parent, displaced.made.fork
        grand = (await world.fork(kid, 3, keep_rounds=True)).hop
        stood = world.stores.campaigns.standing_rounds(trunk, moment=None)
        uncrowned = next(k for k in stood.rounds[1].close.arms if k != stood.crowns[1])
        steered = (await world.fork(trunk, 1, candidate_id=uncrowned)).hop
        return {"trunk": trunk, "kid": kid, "grand": grand, "steered": steered}

    shared = shared_run(tmp_path_factory, act, on=displaced)
    with shared.reading() as stores, scoped(memory=OpenAt()):
        hops = shared.made
        tree = hierarchy_subtree(
            stores, Subject(hops["trunk"].campaign_id).address, None, None, moment=None
        )
        return _Family(stores, hops, tree)


def _owned(
    stores: Stores, hop: CycleHop, moment: Moment | None = None
) -> dict[int, tuple[CycleHop, ArmKey | None]]:
    """By round, the cycle owning it and what that cycle crowned there, as *hop* stood at *moment*:
    off each cycle's own ledger and mint record, never the subtree."""
    stood = stores.campaigns.standing_rounds(hop, moment=moment)
    index = stores.campaigns.load(hop, moment=None)
    assert index is not None
    cut = index.cut_round or 0
    own = {k: (hop, stood.crowns.get(k)) for k in stood.rounds if k >= cut}
    if not cut or index.parent_cycle_id is None or index.forked_at_offset is None:
        return own
    parent = hop.model_copy(update={"cycle_id": index.parent_cycle_id})
    at_cut = Moment(stores.campaigns.ledger(parent), index.forked_at_offset - 1)
    return {k: v for k, v in _owned(stores, parent, at_cut).items() if k < cut} | own


def test_a_reread_stamps_the_latest_walk_of_its_individual() -> None:
    def walk(rnd: int, role: MeasurementRole = MeasurementRole.PANEL) -> RoundResult:
        at = ArmPosition(round=rnd, idx=0, total=1)
        return round_result(rnd, arms=[arm_state("a", position=at, role=role)])

    re_read = MeasurementRole.PARENT
    for line, stamped in (([walk(1), walk(2)], 2), ([walk(1), walk(2, re_read)], 1)):
        walked = walked_position(line, arm_key("a"))
        assert walked is not None and walked.round == stamped, (
            f"a re-read stamps a walk other than its latest: {walked} for round {stamped}"
        )
    assert walked_position([walk(1, re_read)], arm_key("a")) is None, "a re-read walked nothing"


def test_an_inner_run_hangs_on_the_arm_its_spawn_record_names_or_says_why_none_holds_it() -> None:
    a = ArmKey("a")
    # The asker's arms by round: round 2's is the re-read of round 1's candidate.
    ran = {(1, a): "a@1", (2, a): "a@1"}

    def spawned(role: MeasurementRole | None, rnd: int = 1) -> SpawnedBy:
        return SpawnedBy(round=rnd, individual_id="a", role=role, cycle_id="c", task="t")

    asked = Mapping[tuple[int, ArmKey], str] | None
    rows: list[tuple[SpawnedBy | None, asked, str | None, str]] = [
        (spawned(MeasurementRole.PANEL), ran, "a@1", ""),
        (spawned(MeasurementRole.PARENT, 2), ran, "a@1", ""),
        (spawned(MeasurementRole.PANEL, 3), ran, None, "the panel arm its record names is not on"),
        (spawned(MeasurementRole.PANEL), None, None, "is not among this campaign's cycles"),
        (None, ran, None, "it carries no spawn record"),
        (spawned(None), ran, None, "spawned by an origin pass"),
        (spawned(MeasurementRole.OVERLAP), ran, None, "(overlap) is not one of a round's arms"),
    ]
    for record, asker, host, why in rows:
        assert inner_host(record, asker) == host, f"{record} hangs on another candidate"
        said = unplaced_reason(record, asker_listed=asker is not None)
        assert host is not None or why in said, f"{record} hangs unplaced as {said!r}"


def test_a_line_owns_each_round_off_the_read_that_ran_it_and_its_election_off_the_one_that_stated_it() -> (
    None
):
    t, k, g = "trunk", "kid", "grand"

    def both(owned: dict[int, str]) -> LineOwners[str]:
        return LineOwners(owned, owned)

    trunk = both(dict.fromkeys(range(4), t))
    kid = both({0: t, 1: t, 2: k, 3: k})
    # A rebase cut at 2 restates round 1 electing the arm its layer named: it ran none of it.
    rebased = LineOwners({0: t, 1: t, 2: k, 3: k}, {0: t, 1: k, 2: k, 3: k})
    rows: list[tuple[list[int], int, int, LineOwners[str] | None, str, LineOwners[str]]] = [
        ([0, 1, 2, 3], 0, 0, None, t, trunk),
        # A rewind fork cut at 2 lifts what its parent ran below the cut.
        ([2, 3], 2, 2, trunk, k, kid),
        # A fork of a fork lifts through: round 2 stays the kid's, 0 and 1 the trunk's.
        ([3, 4], 3, 3, kid, g, both({0: t, 1: t, 2: k, 3: g, 4: g})),
        # A steer lifts no round: it runs its own line from its origin.
        ([0, 1], 0, 0, trunk, k, both({0: k, 1: k})),
        # A round re-run in place below the cut is the fork's own.
        ([1, 2], 1, 2, trunk, k, both({0: t, 1: k, 2: k})),
        # A fork that has run nothing yet stands on its parent's prefix.
        ([], 2, 2, trunk, k, both({0: t, 1: t})),
        # A re-elected round names its parent's candidates, and its election lifts through a fork.
        ([1, 2, 3], 2, 2, trunk, k, rebased),
        ([3], 3, 3, rebased, g, LineOwners({0: t, 1: t, 2: k, 3: g}, {0: t, 1: k, 2: k, 3: g})),
    ]
    for stated, entered, cut, below, me, owned in rows:
        assert line_owners(stated, entered, cut, below, me) == owned, (
            f"{me} stating {stated} from {entered} cut at {cut} owns rounds off the wrong read"
        )


def test_a_candidates_line_runs_its_hang_then_each_crown_up_to_the_first_after_it() -> None:
    def steps(*pairs: tuple[int, list[str]]) -> list[MainLineStep]:
        return [MainLineStep(round=r, candidates=c) for r, c in pairs]

    hung = steps((0, ["o"]), (1, ["a"]))
    rows: list[tuple[int, dict[int, str | None], list[MainLineStep], list[MainLineStep]]] = [
        (0, {0: "o", 1: "a"}, [], steps((0, ["x"]))),
        (2, {0: "o", 1: "a", 2: "b", 3: "c"}, [], steps((0, ["o"]), (1, ["a"]), (2, ["x"]))),
        # An election that crowned none is a step with none; one past it does not end the line.
        (1, {0: "o", 1: "a", 2: None, 3: "c"}, [], steps((0, ["o"]), (1, ["x"]), (2, []))),
        # A round no election closed is no step.
        (3, {0: "o", 2: "b"}, [], steps((0, ["o"]), (2, ["b"]), (3, ["x"]))),
        # A cycle hung from a candidate continues that candidate's line.
        (3, {2: "k"}, hung, [*hung, *steps((2, ["k"]), (3, ["x"]))]),
    ]
    for rnd, crowns, through, expected in rows:
        assert main_line(rnd, "x", crowns, through) == expected, (
            f"round {rnd}'s candidate under {crowns} reads the wrong line"
        )
    line = steps((0, ["o"]), (1, ["a"]), (2, ["x"]))
    assert line_through(line, "a") == line[:2], "a hung line runs past its candidate"


def test_a_supersede_retires_exactly_what_is_off_its_line_within_its_reach() -> None:
    trunk, kid, kid2, grand = object(), object(), object(), object()
    # Each supersede fork as (its parent, itself), in listing order; a steer is none of them.
    lines = [
        ([(trunk, kid), (kid, grand)], trunk, [kid, grand]),
        ([(trunk, kid), (trunk, kid2)], trunk, [kid2, kid]),
        ([(trunk, kid), (trunk, kid2), (kid, grand)], trunk, [kid2, kid, grand]),
        ([(trunk, kid), (kid, grand)], kid, [grand]),
        ([], trunk, []),
    ]
    for supersedes, of, line in lines:
        assert supersede_line(of, supersedes) == line, "a supersede line misses or misorders a link"
    rows: list[tuple[int, list[Reach[str]], str | None]] = [
        (2, [], None),
        (2, [Reach("k", 2, 3, False)], "k"),
        (1, [Reach("k", 2, 3, True)], None),
        # A fork that died before the round retires nothing there.
        (3, [Reach("k", 2, 2, False)], None),
        (3, [Reach("g", 3, 4, False), Reach("k", 2, 4, False)], "k"),
        # Tied rival cuts: the first on the line, the nearest link and then the newest.
        (2, [Reach("k2", 2, 3, False), Reach("k", 2, 3, False)], "k2"),
        # A supersede of a supersede: the grand holds what the kid lifted, the kid retires it.
        (3, [Reach("k", 2, 4, False), Reach("g", 3, 4, True)], "k"),
    ]
    for rnd, reaches, by in rows:
        assert retired_by(rnd, reaches) == by, (
            f"round {rnd} under {reaches} retired by the wrong fork"
        )


def test_a_child_descends_from_its_parents_newest_measurement_never_a_copy_that_measured_none() -> (
    None
):
    def walked(
        of: str, rnd: int, *, invalid: bool = False, on: bool = True, own: bool = True
    ) -> Walked[str]:
        return Walked(of, of.split("@")[0], rnd, invalid, on, own)

    rows: list[tuple[list[Walked[str]], dict[str, str]]] = [
        # Round 1 re-proposed the origin verbatim, refused before a cell.
        ([walked("o@0", 0), walked("o@1", 1, invalid=True)], {"o": "o@0"}),
        ([walked("o@0", 0), walked("o@1", 1)], {"o": "o@1"}),
        # The reader's own round and later measure nothing earlier.
        ([walked("o@2", 2), walked("o@3", 3)], {}),
        # On the reader's line over a later round off it; its own cycle over another at a tie.
        ([walked("o@0", 0), walked("o@1", 1, on=False)], {"o": "o@0"}),
        ([walked("o@1", 1), walked("o@1'", 1, own=False)], {"o": "o@1"}),
    ]
    for measured, newest in rows:
        assert earlier_measured(2, measured) == newest, f"{measured} descends from the wrong walk"


class _Fork(NamedTuple):
    hop: CycleHop
    parent: str
    cut: int | None


def _forks(family: _Family) -> dict[str, _Fork]:
    """Each fork of the trunk's family by cycle, off its mint record: its parent and its cut (None
    for a steer, which lifts no round)."""
    out = {}
    for name, hop in family.hops.items():
        index = family.stores.campaigns.load(hop, moment=None)
        assert index is not None
        if name != "l4" and index.parent_cycle_id is not None:
            out[hop.cycle_id] = _Fork(hop, index.parent_cycle_id, index.cut_round or None)
    return out


# A lifted round keeps the crown its parent elected, and a parting dims every fork whose start
# was proposed past it (a rewind's at its cut, a steer's where its arm was), the fork's forks with it.
@pytest.mark.usefixtures("optimizing_memory")
def test_the_mask_reads_the_lines_the_subtree_reads(family_tree: _Family) -> None:
    stores, trunk, grand = family_tree.stores, family_tree.hops["trunk"], family_tree.hops["grand"]
    mask = load_mask_record(stores, trunk.campaign_id, lens=None)
    crowned = {
        c.cycle_id: {
            r.round: next((m.key for m in r.candidates if m.is_selected), None) for r in c.rounds
        }
        for c in mask.cycles
    }
    assert crowned[grand.cycle_id] == {k: c for k, (_, c) in _owned(stores, grand).items()}, (
        "the mask crowns a lifted round other than where its parent elected it"
    )
    forks, cycles = _forks(family_tree), {e.address: e for e in entries_under(family_tree.tree)}

    def started(fork: _Fork) -> int:
        """The trunk round the line of *fork* left it in, off the subtree's hang of each fork."""
        if fork.parent != trunk.cycle_id:
            return started(forks[fork.parent])
        hung = cycles[_cycle_of(fork.hop)]
        assert isinstance(hung, SubtreeCycle) and hung.facts.from_candidate is not None
        steered_at = Subject.parse_address(hung.facts.from_candidate).round
        assert steered_at is not None
        return fork.cut or steered_at

    for parting in (0, 1, 2):

        def parts(rnd: MaskRound, at: int = parting) -> VerdictOutcome:
            return VerdictOutcome(diverged=(rnd.cycle_id, rnd.round) == (trunk.cycle_id, at))

        parted = find_divergences(mask, parts)
        dimmed = {(trunk.cycle_id, k) for k in _owned(stores, trunk) if k > parting} | {
            (cid, k)
            for cid, f in forks.items()
            if started(f) > parting
            for k in _owned(stores, f.hop)
        }
        assert sorted(parted.divergent) == sorted(dimmed), (
            f"a parting at {parting} dims a fork started at or before it, or spares one past it"
        )


# 4. An origin the gate halted, resumed past it


@pytest.fixture(scope="module")
def gated(tmp_path_factory: pytest.TempPathFactory) -> Shared:
    """Potter's origin under `origin_gate: strict`, every cell refused, halted at the gate; run once
    for the module (~0.3 s): a fact resuming it adopts its own copy, one reading it reads in place."""

    async def act(world: World) -> CycleHop:
        return (await world.campaign(origin_gate="strict", down=True)).hop

    return shared_run(tmp_path_factory, act)


def _passes(world: World, hop: CycleHop) -> list[str]:
    decided = world.stores.campaigns.standing_rounds(hop, moment=None).decisions.get(0, [])
    return [d.inputs.by for d in decided if isinstance(d.inputs, OriginGatePass)]


# Each launch reads the pass the one before it banked.
def test_a_resume_takes_the_origin_the_gate_halted_past_it_and_every_later_launch_reads_the_pass(
    world: World, gated: Shared
) -> None:
    run, hop = world.adopt(gated), gated.made
    assert _passes(run, hop) == [], "a halt banked a pass"
    # Still refused: the resume is the operator's override, paused before round 1 closes, so the
    # next launch asks the gate again, after an ending the gate never made.
    run.net.backend.down = True
    for step, ending in ((0, StopReason.PAUSED), (None, StopReason.ROUNDS_BUDGET)):
        launch = run.resume(hop, step_rounds=step)
        ended = asyncio.run(launch).result.stop_reason
        assert ended is ending, f"a launch stepping {step} ended {ended}, not {ending}"
        assert _passes(run, hop) == ["resume"], "the override was lost, or banked twice"


def test_a_halts_own_words_are_the_stop_its_run_serves_and_its_chat_says(gated: Shared) -> None:
    # The gate's words, raised where the run ends, reach the served stop and the chat through the
    # close every launch takes (`entry.py::close_cycle`).
    hop = gated.made
    with gated.reading() as stores:
        served = stores.run_state(hop).stop
        ledger = stores.campaigns.layout(hop).ledger
        notices = LedgerIndex.of(ledger).view(ActivityFeed).notices
    assert served is not None and served.stop_reason is StopReason.ORIGIN_GATE and served.detail, (
        "the halt served no words of its own"
    )
    assert any(said.detail == served.detail for said in notices), "the chat lost the halt's words"
    # A layer's terminate stops in its own words.
    aborted = StopLoop(StopReason.OPTIMIZER_ABORT, detail="l2: the backend's index is gone")
    assert (run_stop_reason(aborted), run_stop_detail(aborted)) == (
        StopReason.OPTIMIZER_ABORT,
        aborted.detail,
    ), "an abort's stop lost the words its layer gave"
