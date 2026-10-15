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
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from factories import (
    loop_session,
    measurement,
    measurements,
    optimizer_state,
    pipeline_schema,
    pobb_knobs,
    scored_candidate,
    sheet,
    workspace,
)

from promptpotter.application.bench.node_context import NodeContext
from promptpotter.application.bench.resume_and_fork.fork_siblings import mint_operator_fork
from promptpotter.application.bench.resume_and_fork.replayers import replay_decisions
from promptpotter.application.campaign_config import (
    CampaignConfig,
    OptimizationConfig,
    apply_config_overrides,
)
from promptpotter.application.intelligence.hard_sample_archive import build_archive_observations
from promptpotter.application.intelligence.indexes.sample import SampleIndex
from promptpotter.application.maintenance.archive_maintenance import (
    compact_measurement_archive,
    restore_measurement_archive,
)
from promptpotter.application.optimizers.nodes import Measured, Proposals, RoundContext
from promptpotter.application.optimizers.potter.election import elect_on_theta
from promptpotter.application.optimizers.potter.pobb.checks import EliminationGate
from promptpotter.application.optimizers.potter.race import PoBBRace
from promptpotter.application.origin import (
    resolve_origin_opt_search_point,
    try_inherit_fork_origin,
)
from promptpotter.application.scoring import query_loop, search_point_scorer
from promptpotter.application.scoring.closed_rounds import closed_rounds
from promptpotter.application.scoring.formula import compile_scorer
from promptpotter.application.scoring.search_point_scorer import _replayable_on
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.optimizer_state import UnregisteredPayloadError
from promptpotter.domain.paired_reading import ReadingState
from promptpotter.domain.phase_views import ViewAnchors
from promptpotter.domain.pipeline_schema import NodePromptInfo, PipelineNode, PipelineSchema
from promptpotter.domain.results import OverlapReading, ReferenceReading, RoundResult, RunStanding
from promptpotter.domain.run_records import (
    CandidateMintedRecord,
    CandidateScoredRecord,
    ConfigOverrides,
    CycleSeed,
    ElectionRecord,
    ForkSpec,
    ForkTrigger,
    ResumeCheckpointRecord,
    RoundClosedRecord,
    RoundEnteredRecord,
    RoundStandingRecord,
)
from promptpotter.domain.sample import ArchiveEntry, Sample, sample_key
from promptpotter.domain.scoring import GRADE_KEYS, MeasuredCell
from promptpotter.domain.search_point import JobSearchPoint, TaskDecomposition
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.telemetry import reset_cycle_ledger, set_cycle_ledger
from promptpotter.infrastructure.store import archive_queries, measurement_archive
from promptpotter.infrastructure.store.archive_queries import (
    SampleFoldRow,
    list_populations,
    load_population,
    note_walked,
    sample_fold_rows,
    scope_memory_to_own_answers,
    write_sample_fold,
)
from promptpotter.infrastructure.store.io import write_text
from promptpotter.infrastructure.store.lineage_queries import build_lineage_tree
from promptpotter.infrastructure.store.measurement_archive import (
    MeasurementArchive,
    ReplayFeed,
    config_key,
    standing,
)
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import DatasetIdentityError

Configs = list[tuple[str, dict[str, Any]]]
_LLM_ONLY: Configs = [("llm_only", {"model": "X"})]
_CAMPAIGN = "testds__20260101-000000"


def _entry(node_configs: Configs, dataset_name: str) -> ArchiveEntry:
    return ArchiveEntry(
        config_key=config_key(node_configs),
        prompt_fields_id="pf_x",
        rendered_prompt_hash="",
        node_configs=node_configs,
        pipeline_params={},
        dataset_name=dataset_name,
    )


def _row(
    sid: int, query: str, *, predicted: str = "p", hit: bool = True, terminal: str = "llm_only"
) -> dict[str, Any]:
    return {
        "sample_id": sid,
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
    dataset_name: str,
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
        _entry(node_configs, dataset_name),
        [(MeasuredCell.from_wire(keyed(r)), provenance) for r in rows],
        role="panel",
        source=RunSource.OPTIMIZATION_LOOP.value,
        created_at=created_at,
    )


# 1. Replay eligibility


def _walk_panel(root: Path, panel: list[Sample], per_sample: str, per_cell: str | None) -> Any:
    schema = pipeline_schema(
        name="claims",
        nodes=[PipelineNode(name="solve", tunes_llm=False, prompt_info=NodePromptInfo())],
    )
    session = loop_session(workspace(root), schema, panel, backend_id="b")
    session.scoring.scorer = compile_scorer(per_sample, per_cell, verifier_graded=False)
    sp = OptSearchPoint(instruction="Answer.").to_job_search_point(
        schema=schema, framing=TaskDecomposition(), demo=[]
    )

    async def _measure(sample: Sample, _session: Any, *, pipeline_params: Any) -> MeasuredCell:
        predicted = "b" if sample.id % 2 else "a"
        return MeasuredCell(
            sample_id=sample.id,
            sample_key=sample.key,
            query=sample.query,
            ground_truth="a",
            predicted=predicted,
        )

    bound = set_cycle_ledger(CycleEventLog(root / "walk.jsonl"))
    try:
        with mock.patch.object(query_loop, "measure_sample", _measure):
            walked = asyncio.run(
                search_point_scorer.score_search_point(
                    sp, panel, session, label="panel", measured=None
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
            [_row(1, "q", predicted=predicted, terminal=terminal)],
            dataset_name="promptpotter-self",
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

    for name, grade, terminal, sid, dataset_name, hit in (
        ("clean", "A", "llm_only", 7, "aime", True),
        ("connector", "C", "token_matching", 8, "aime", True),
        ("aime_14", "A", "llm_only", 14, "aime", True),
        ("justlogic_14", "A", "llm_only", 14, "justlogic", False),
    ):
        row = _row(sid, f"q_{name}", hit=hit, terminal=terminal)
        _file(archive, [row], dataset_name=dataset_name, provenance=grade)
    reusable = ReplayFeed(archive, _LLM_ONLY).advance()
    assert {b.cell.query for b in reusable.values()} == {
        "q_clean",
        "q_aime_14",
        "q_justlogic_14",
    }, "a grade-C answer was replayed"
    wider = [
        Sample(id=0, query="q_aime_14", ground_truth="g"),
        Sample(id=14, query="q_new", ground_truth="g"),
        Sample(id=1, query="q_aime_14", ground_truth="g", source_pin={"git_commit_id": "moved"}),
    ]
    on_wider = _replayable_on(wider, reusable, "aime-wide")
    assert set(on_wider) == {0}, "only the sample aime measured replays, at the slot it holds HERE"
    assert on_wider[0].query == "q_aime_14" and on_wider[0].sample_id == 0
    # Readers keyed on `(dataset_name, sample_id)` pool a re-cut slot; a relabel alone is a re-cut.
    same = [Sample(id=14, query="q_aime_14", ground_truth="g")]
    assert set(_replayable_on(same, reusable, "aime")) == {14}
    for recut in (
        [Sample(id=14, query="an entirely different question", ground_truth="g")],
        [Sample(id=14, query="q_aime_14", ground_truth="not_g")],
    ):
        with pytest.raises(DatasetIdentityError):
            _replayable_on(recut, reusable, "aime")

    # Two walks never buy one cell: the waiter reads the answer its holder publishes.
    holder, waiter = (
        ReplayFeed(MeasurementArchive(tmp_path / "cells"), _LLM_ONLY) for _ in range(2)
    )
    held = holder.claim("k_held")
    assert held is not None and waiter.claim("k_held") is None, "two walks bought one cell"
    held.publish(MeasuredCell(sample_id=0, sample_key="k_held", predicted="p"), grade="B")
    beside = waiter.claim("k_beside")
    assert beside is not None, "one cell's claim held another sample of its configuration"
    assert waiter.claimed_cell("k_beside") is None
    published = waiter.claimed_cell("k_held")
    assert published is not None and published.predicted == "p", "the waiter lost the paid answer"
    held.release()
    beside.release()

    # Rows banked under one formula read under another exactly as a fresh run would.
    panel = [Sample(id=i, query=f"q{i}", ground_truth="a") for i in range(6)]
    banked_under = ("label_match(predicted, ground_truth)", None)
    read_under = ("0.5 * label_match(predicted, ground_truth)", "0.5 * fitness + 0.25")
    shared, banked_walk = _walk_panel(tmp_path / "shared", panel, *banked_under)
    assert all(GRADE_KEYS & r.keys() for r in banked_walk.sheet.wire()), "guard: the walk graded"
    banked = archive_queries.walked_answers(shared.store, banked_walk.cells)
    assert len(banked) == len(panel), "a walked answer was not banked"
    filed = shared.store.archive.cell_signatures()
    _, fresh = _walk_panel(tmp_path / "fresh", panel, *read_under)
    worth = {c.sample_id: c.grade.objective for c in fresh.sheet}
    assert len(set(worth.values())) == 2, "guard: hits and misses are worth different amounts"

    def ruler_cells() -> dict[int, float]:
        observed = build_archive_observations(
            shared.store,
            dataset_name="claims",
            scorer=compile_scorer(*read_under, verifier_graded=False),
            sample_ids=None,
        )
        return {o.sample_id: o.response for o in observed}

    assert ruler_cells() == pytest.approx(worth), "the ruler read the banked formula's grades"
    _, replayed = _walk_panel(tmp_path / "shared", panel, *read_under)
    assert all(c.facts.cached for c in replayed.sheet), "guard: the second read bought nothing"
    assert [c[:3] for c in replayed.cells] == [c[:3] for c in banked_walk.cells]
    assert shared.store.archive.cell_signatures() == filed, "a replay filed a second answer"
    assert {c.sample_id: c.grade.objective for c in replayed.sheet} == worth
    assert replayed.scores.composite_fitness == pytest.approx(fresh.scores.composite_fitness)
    SampleIndex.ensure_for(
        shared.store,
        scorer=compile_scorer(*read_under, verifier_graded=False),
        dataset_name="claims",
        sample_ids=None,
    )
    assert ruler_cells() == pytest.approx(worth), "the fold's cells are the regraded ones"
    wider_panel = [*panel, *(Sample(id=i, query=f"q{i}", ground_truth="a") for i in range(6, 10))]
    _walk_panel(tmp_path / "shared", wider_panel, *banked_under)
    _, fresh_wider = _walk_panel(tmp_path / "fresh", wider_panel, *read_under)
    assert ruler_cells() == pytest.approx(
        {c.sample_id: c.grade.objective for c in fresh_wider.sheet}
    ), "a population that grew after it was folded read at its folded length"


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
        row = {"sample_id": 1, "predicted": said, **facts}
        refs += _file(
            archive,
            [row],
            dataset_name="kept",
            node_configs=configs,
            created_at=f"2026-05-19T00:{at:02}",
        )
    assert len(set(refs)) == 4
    held = archive.population(entry)
    assert [a.cell.predicted for a in held] == ["first", "second", "ERROR"], "an answer was dropped"
    assert standing(held)[1].cell.predicted == "second", "an errored answer stood over a graded one"
    described = {e.config_key for e in archive.list_all(dataset_name="kept")}
    assert described == {config_key(_LLM_ONLY), config_key(other)}
    assert archive.reindex() == {"indexed": 2, "undescribed": 0}
    assert {e.config_key for e in archive.list_all(dataset_name="kept")} == described
    assert archive.population(entry) == held, "reindex destroyed an answer"
    assert all(archive.answer(ref) is not None for ref in refs), "reindex lost an address"

    # A reader tailing a cell file rewritten under it skips no answer.
    tailed: Configs = [("llm_only", {"model": "T"})]
    reader = MeasurementArchive(archive.base_dir)
    feed = ReplayFeed(reader, tailed)

    def _bank(sid: int, dataset_name: str = "reidx", via: MeasurementArchive = archive) -> None:
        cell = {
            "sample_id": sid,
            "sample_key": f"k{sid}",
            "predicted": "p",
            "pipeline_data": {"reasoning_trace": "T" * 400},
        }
        _file(via, [cell], dataset_name=dataset_name, node_configs=tailed)

    for sid in (1, 2, 3):
        _bank(sid)
    assert set(feed.advance()) == {"k1", "k2", "k3"}
    file_key = config_key(tailed)
    cells = archive._cell_path(file_key)
    read_to = cells.stat().st_size
    # Rewritten IN PLACE, as an ext4 swap onto a freed inode reads: every line kept, each shorter.
    cells.write_text("".join(ln.replace("T" * 400, "") for ln in archive.detail_lines(file_key)))
    _bank(4)
    assert cells.stat().st_size < read_to
    assert set(feed.advance()) == {"k4"}, "an answer filed after the rewrite was skipped"
    assert reader.entry(file_key, "reidx") is not None
    archive.reindex()
    _bank(5, dataset_name="reidx-wide")
    assert reader.entry(file_key, "reidx-wide") is not None, "an index entry was skipped"
    # The other process files between this writer's look at the file and its own append.
    own = ReplayFeed(archive, tailed)
    own.advance()
    append_row = measurement_archive.append_row

    def raced(path: Path, *rows: dict[str, Any]) -> int:
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
    for sid in (1, 2):
        row = {
            **_row(sid, f"q{sid}"),
            "ground_truth_rank": 0,
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
        _file(archive, [row], dataset_name="compacted", node_configs=compacted)
    whole = _entry(compacted, "compacted")
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
    def individual(name: str) -> ArchiveEntry:
        return ArchiveEntry(
            config_key=config_key([("", {"individual": name})]),
            prompt_fields_id=name,
            rendered_prompt_hash="",
            node_configs=[],
            pipeline_params={},
            dataset_name="ds",
        )

    def bank(name: str, said: list[str], at: int) -> list[str]:
        return archive.file_answers(
            individual(name),
            [(MeasuredCell(sample_id=i, predicted=p), "A") for i, p in enumerate(said)],
            role="panel",
            source="optimization_loop",
            created_at=f"2026-09-28T00:00:{at:02d}Z",
        )

    def listed() -> set[str]:
        return {e.prompt_fields_id for e in list_populations(built_stores, dataset_name="ds")}

    def remembered(name: str) -> list[str]:
        return [a.cell.predicted for a in load_population(built_stores, individual(name))]

    def fold_row(key: str) -> SampleFoldRow:
        return SampleFoldRow(config_key=key, sp=key, fk="formula", sig=[], graded=[])

    bank("foreign", ["yes", "no"], 1)
    bank("shared", ["no", "no"], 2)
    write_sample_fold(built_stores, dataset_name="ds", rows=[fold_row("x")])

    def inside_arm() -> tuple[set[str], list[str], list[SampleFoldRow]]:
        scope_memory_to_own_answers(set(bank("origin", ["yes", "no"], 3)))
        assert listed() == {"origin"}, "the arm saw a population its campaign never walked"
        # A replayed answer joins the arm's memory exactly as a filed one does.
        for answer in bank("shared", ["yes", "yes"], 0):
            note_walked(answer)
        write_sample_fold(built_stores, dataset_name="ds", rows=[fold_row("y")])
        return listed(), remembered("shared"), sample_fold_rows(built_stores, dataset_name="ds")

    arm_listed, arm_shared, arm_fold = contextvars.copy_context().run(inside_arm)
    assert arm_listed == {"origin", "shared"}
    assert arm_shared == ["yes", "yes"], (
        "another campaign's answer of a shared cell steered the arm"
    )
    assert arm_fold == [], "the arm read a fold its scope never admitted"
    assert listed() == {"foreign", "shared", "origin"}, "the scope leaked out of the arm"
    assert remembered("shared") == ["no", "no"]
    assert sample_fold_rows(built_stores, dataset_name="ds") == [fold_row("x")]


# 3. Replayed decisions, and forks


def _round(**kw: Any) -> RoundResult:
    return RoundResult.model_validate(
        {
            "label": "C0",
            "accuracy": None,
            "composite_fitness": None,
            "total": 0,
            "improved": False,
            "elects_on": "ability",
            "prompt_fields": {},
            "candidates_scored": 0,
            "selected_labels": [],
            "leading_label": None,
            "overlap": OverlapReading.unpaired(ReadingState.NOT_HELD, 0, False),
            "optimizer_state": optimizer_state().model_dump(),
            **kw,
        }
    )


def _minted(stores: Stores, cycle_id: str) -> tuple[CycleHop, CycleEventLog]:
    hop = CycleHop(campaign_id=_CAMPAIGN, cycle_id=cycle_id)
    stores.campaigns.mint_cycle(hop)
    return hop, CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(hop)))


def _stand_round(
    ledger: CycleEventLog, closed: list[RoundResult], name: str, *, crowned: bool = True
) -> str:
    n = len(closed)
    label = f"C{n}.1" if n else "C0"
    pick = OptSearchPoint(instruction=name)
    selected = [label] if crowned else []
    scores = scored_candidate(pick.id, label=label)
    closed.append(
        _round(
            round=n, label=label, opt_sp=pick, candidate_scores=[scores], selected_labels=selected
        )
    )
    ledger.append(RoundEnteredRecord(round=n))
    ledger.append(CandidateMintedRecord(round=n, idx=0, candidate_id=pick.id, label=label))
    ledger.append(CandidateScoredRecord(round=n, candidate_idx=0, candidate_total=1, scores=scores))
    ledger.append(ElectionRecord(round=n, selected_labels=selected, elects_on="accuracy"))
    ledger.append(RoundClosedRecord.of(closed[-1]))
    stood = RunStanding.after(closed, lives=None, spent=None)
    ledger.append(RoundStandingRecord(round=n, run_standing=stood, anchors=ViewAnchors()))
    return pick.id


def test_a_closed_round_replays_its_decisions_and_a_rewind_retracts_them(
    built_stores: Stores,
) -> None:
    stores = built_stores
    # A replayer that cannot re-derive its decision is a named divergence, never a clean match.
    blind = replay_decisions(
        _round(
            round=0,
            all_candidate_results={
                "c1": sheet(
                    [measurement(0, 1.0, objective=1.0, query="q", predicted="p", ground_truth="g")]
                )
            },
        ),
        [
            ResumeCheckpointRecord.model_validate(
                {
                    "kind": "round_winner",
                    "inputs_ref": {"candidate_ids": ["c1"], "round_num": 0, "coverage_floor": 1},
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
    racing = SimpleNamespace(
        optimizer=SimpleNamespace(knobs=lambda node: pobb_knobs(epsilon=0.30)),
        config=SimpleNamespace(optimization=SimpleNamespace(elimination_n_min=4)),
        difficulty=SimpleNamespace(ruler=None),
        tracking=SimpleNamespace(
            current_results=list(sheet(measurements([1.0] * 10))), current_sp=JobSearchPoint()
        ),
        rounds=[],
        pending_decisions=[],
    )
    race = PoBBRace(
        NodeContext(RoundContext(racing, 3, callbacks=None), "pobb"),  # type: ignore[arg-type]
        SimpleNamespace(cells=list(range(10))),  # type: ignore[arg-type]
        lambda sp, sample, prior_id: None,  # type: ignore[arg-type, return-value]
        measured_unit="sample",
    )
    walked = sheet(
        measurement(i, None, error="boom", error_category="transient")
        if i == 2
        else measurement(i, 1.0 if i == 4 else 0.0)
        for i in range(8)
    )
    reading = race.judge(
        race.rule([]).check(walked.cells),
        candidate_id="c1",
        results=walked.cells,
        labels={"c1": "C3.1"},
    )
    assert reading is not None and reading.context["gate"] == EliminationGate.EPSILON
    [cut] = racing.pending_decisions
    assert cut.data["candidate_sample_ids"] == [str(i) for i in range(8) if i != 2]
    assert replay_decisions(_round(round=3, all_candidate_results={"c1": walked}), [cut]) is None, (
        "the cut the live rule made is not the cut its own record replays"
    )

    # A parent's charged error replays at the grade the live election read.
    scorer = compile_scorer("label_match(predicted, ground_truth)", None, verifier_graded=False)

    def filed(model: str, answers: dict[int, str | None]) -> Any:
        rows = [
            {"sample_id": sid, "query": f"q{sid}", "ground_truth": "g"}
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
            dataset_name="replayds",
            node_configs=[("llm_only", {"model": model})],
        )
        return scorer.sheet(a.cell for ref in refs if (a := stores.archive.answer(ref)) is not None)

    # Level with the parent on the nine cells both answered: only the charged error separates them.
    shared = {sid: "g" if sid < 5 else "x" for sid in range(9)}
    parent, child = OptSearchPoint(instruction="parent"), OptSearchPoint(instruction="child")
    bar, arm = filed("P", {**shared, 9: None}), filed("C", shared)
    assert bar.cells[9].scored and bar.cells[9].grade.fitness == 0.0, "guard"
    electing = SimpleNamespace(
        difficulty=SimpleNamespace(ruler=None),
        rounds=[],
        pending_decisions=[],
        opt_sp=parent,
        population=[parent],
    )
    scores = [scored_candidate(child.id, label="C1.1")]
    selection = elect_on_theta(
        NodeContext(RoundContext(electing, 1, callbacks=None), "election"),  # type: ignore[arg-type]
        Measured(
            rows={child.id: arm},
            scores=scores,
            scored=[child],
            parent=ReferenceReading(opt_sp=parent, report=scored_candidate(parent.id)),
            parent_rows=bar,
            references={parent.id: bar},
            electable=[child],
            coverage_floor=1,
            cut=None,
        ),
        Proposals([]),
    )
    assert selection.selected_id == child.id, "guard: the live election charges the error"
    charged, ledger = _minted(stores, "cycle_charged_parent")
    ledger.append(RoundEnteredRecord(round=1))
    ledger.append(
        RoundClosedRecord.of(
            _round(
                round=1,
                label="C1.1",
                improved=True,
                opt_sp=child,
                candidate_scores=scores,
                selected_labels=["C1.1"],
                results=arm,
                all_candidate_results={child.id: arm},
                reference_results={parent.id: bar},
            )
        )
    )
    [closed_on_disk] = closed_rounds(stores, charged, scorer)
    [election] = electing.pending_decisions
    assert replay_decisions(closed_on_disk, [election]) is None, (
        "the replay read the parent's charged error as a cell nobody scored"
    )

    # Rounds stand; a rewind retracts every election, close and crown past it, for every reader.
    hop, ledger = _minted(stores, "cycle_rewound")
    closed: list[RoundResult] = []
    ids = [_stand_round(ledger, closed, name) for name in ("c0", "c1", "c2")]

    def served() -> list[tuple[str, bool, bool]]:
        tree = build_lineage_tree(stores, (hop,))
        return [(k.id, k.reading.election.held, k.reading.election.selected) for k in tree.children]

    def selected() -> list[str]:
        index = stores.campaigns.load(hop)
        assert index is not None
        readers = (
            stores.campaigns.standing_rounds(hop).standing,
            build_lineage_tree(stores, (hop,)).run_standing,
            index.standing,
        )
        return [reader.selection.label for reader in readers]  # type: ignore[union-attr]

    assert served() == [(i, True, True) for i in ids] and selected() == ["C2.1"] * 3, "guard"
    stores.campaigns.rewind_to_round(hop, 1)
    assert selected() == ["C1.1"] * 3, (
        "the ledger, the tree and the index disagree on the selection"
    )
    stores.campaigns.rewind_to_round(hop, 0)
    stood = stores.campaigns.standing_rounds(hop)
    assert list(stood.rounds) == list(stood.elections) == [0] and stood.crowns == {0: "C0"}, (
        "an election, close or crown stood past the rewind"
    )
    assert served() == [(ids[0], True, True)]
    again = _stand_round(ledger, closed[:1], "c1_again", crowned=False)
    assert served() == [(ids[0], True, True), (again, True, False)]
    origin, held = build_lineage_tree(stores, (hop,)).children
    assert (origin.stands, held.stands) == (True, False), "an uncrowned re-run took the stand"

    # A ledger read SKIPS a line that fails validation; a close naming an unregistered payload
    # stops the read instead. `write_text` swaps in a fresh file: a ledger index takes a
    # same-inode growth for an append, and no writer here grows a file in place.
    peer, ledger = _minted(stores, "cycle_of_a_peer")
    _stand_round(ledger, [], "c0")
    records = [json.loads(line) for line in ledger.path.read_text(encoding="utf-8").splitlines()]
    close = max(i for i, rec in enumerate(records) if "optimizer_state" in rec)
    records[close]["optimizer_state"]["manifest"] = "uninstalled"
    write_text(ledger.path, "".join(json.dumps(rec) + "\n" for rec in records))
    with pytest.raises(UnregisteredPayloadError):
        list(CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(peer))).iter())
    with pytest.raises(UnregisteredPayloadError):
        stores.campaigns.standing_rounds(peer)


def test_a_fork_inherits_its_parents_decisions_and_displaces_them_once(
    built_stores: Stores,
) -> None:
    stores, store = built_stores, built_stores.campaigns
    # An unmodified fork origin inherits the branch-point candidate's reading; an edited one rescores.
    prompt = {"instruction": "do the thing", "persona": "you are precise"}
    parent, ledger = _minted(stores, "cycle_inherit_parent")
    ledger.append(
        RoundClosedRecord.of(
            _round(
                round=1,
                label="C1.1",
                accuracy=0.4,
                total=10,
                improved=True,
                candidate_scores=[
                    scored_candidate(
                        "c1", label="C1.1", prompt_fields=prompt, accuracy=0.2, total=10
                    )
                ],
            )
        )
    )
    fork = "cycle_inherit_parent_fork_abc123"
    spec = ForkSpec(
        trigger=ForkTrigger.OPERATOR_STEERED,
        reason="",
        issued_by="",
        from_round=1,
        from_candidate_id="c1",
    )
    store.mint_fork_cycle(parent, fork, spec, from_round=0)
    session = SimpleNamespace(
        store=stores,
        campaign_id=_CAMPAIGN,
        state=SimpleNamespace(cycle_id=fork),
        hop=CycleHop(campaign_id=_CAMPAIGN, cycle_id=fork),
        experiment_extract={},
        dataset_config_dir=None,
        pipeline_schema=PipelineSchema(),
        # The branch-point round banked no row here, so no cell is ever graded.
        scoring=SimpleNamespace(
            require_scorer=lambda: compile_scorer(
                "label_match(predicted, ground_truth)", None, verifier_graded=False
            )
        ),
    )

    def inherit(fields: dict[str, str]) -> Any:
        # Resolved as `establish_campaign_origin` does: the fork seed wins.
        seed = CycleSeed(origin_prompt_fields=fields, origin_source="fork_seed")
        resolved = resolve_origin_opt_search_point(
            [], seed=seed, pipeline_params=None, schema=PipelineSchema()
        )
        return try_inherit_fork_origin(
            session,  # type: ignore[arg-type]
            seed,
            resolved_origin=resolved,
            sp=JobSearchPoint(),
            framing=TaskDecomposition(),
        )

    inherited = inherit(dict(prompt))
    assert inherited is not None, "an unmodified fork origin rescored what its parent paid for"
    assert inherited.report.accuracy == 0.2, "the fork read its round's 0.4, not its candidate's"
    assert isinstance(inherited.resolved_origin, OptSearchPoint)
    assert inherited.resolved_origin.lineage.source == "origin"
    assert inherit({**prompt, "instruction": "do it differently"}) is None, (
        "an edited fork origin inherited a measurement it never earned"
    )

    # An applied scenario forks at its round and carries its criterion.
    scenario, _ = _minted(stores, "cycle_applyscenario")
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
    index = store.load(applied)
    assert index is not None and index.fork is not None
    assert index.fork.issued_by == str(stores.identity.user_id), "the fork names no issuer"
    # OPERATOR_REWIND is the rebase branch, the one that lifts rounds 0..N-1.
    assert (index.fork.trigger, index.fork.from_round) == (ForkTrigger.OPERATOR_REWIND, 2)
    seed = store.read_cycle_seed(applied)
    assert seed is not None
    base = CampaignConfig(optimization=OptimizationConfig(degradation_threshold=0.05))
    assert apply_config_overrides(base, seed.config_overrides).scoring == criterion
    assert base.scoring is None, "applying a scenario wrote through to its base config"

    # A fork's own ledger displaces its parent's prefix once.
    displaced, ledger = _minted(stores, "cycle_displaced")
    closed: list[RoundResult] = []
    prefix = [_stand_round(ledger, closed, f"p{n}") for n in range(3)]
    fork_hop = store.mint_fork_cycle(
        displaced,
        "cycle_displaced_fork_r1",
        ForkSpec(trigger=ForkTrigger.OPERATOR_REWIND, reason="", issued_by="", from_round=3),
        from_round=3,
    )

    def candidates(hop: CycleHop) -> list[str]:
        return [c.candidate_id for c in store.standing_rounds(hop).candidates()]

    assert candidates(fork_hop) == prefix, "the fork does not continue its parent's rounds"
    own = CycleEventLog.open(CycleDir(store.cycle_dir(fork_hop)))
    forked = closed[:1]
    mine = [_stand_round(own, forked, "f1"), _stand_round(own, forked, "f2", crowned=False)]
    stood = store.standing_rounds(fork_hop)
    assert candidates(fork_hop) == [prefix[0], *mine], "a parent round stood beside the fork's own"
    assert [r.close.candidate_scores[0].candidate_id for r in stood.rounds.values()] == [
        prefix[0],
        *mine,
    ]
    assert stood.crowns == {0: "C0", 1: "C1.1"}
    assert candidates(displaced) == prefix, "the fork's rounds displaced its parent's own"
