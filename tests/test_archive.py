"""Lost or corrupted measurement.

Owns `infrastructure/store/measurement_archive.py` and `archive_queries.py`,
`application/maintenance/`, `application/bench/resume_and_fork/` and `application/origin.py`. A
replay served to the wrong content, a paid row dropped, a fork that inherits the wrong origin.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from factories import loop_session, pipeline_schema, workspace

from promptpotter.application.bench.resume_and_fork.replayers import replay_decisions
from promptpotter.application.maintenance.archive_maintenance import (
    compact_measurement_archive,
    restore_measurement_archive,
)
from promptpotter.application.scoring import query_loop
from promptpotter.application.scoring.search_point_scorer import _replayable_on
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.optimizer_state import UnregisteredPayloadError
from promptpotter.domain.paired_reading import ReadingState
from promptpotter.domain.results import OverlapReading, RoundResult
from promptpotter.domain.run_records import (
    CandidateMintedRecord,
    CandidateScoredRecord,
    CycleSeed,
    ElectionRecord,
    ForkSpec,
    ForkTrigger,
    ResumeCheckpointRecord,
    RoundClosedRecord,
    RoundEnteredRecord,
)
from promptpotter.domain.sample import Sample, sample_key
from promptpotter.domain.scoring import MeasuredCell
from promptpotter.domain.search_point import JobSearchPoint, TaskDecomposition
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store import measurement_archive
from promptpotter.infrastructure.store.measurement_archive import (
    MeasurementArchive,
    ReplayFeed,
    config_key,
    standing,
)
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import DatasetIdentityError
from tests.factories import optimizer_state, scored_candidate, sheet

# 1. Replay eligibility


_LLM_ONLY: list[tuple[str, dict[str, Any]]] = [("llm_only", {"model": "X"})]


def _entry(node_configs: list[tuple[str, dict[str, Any]]], dataset_name: str) -> dict[str, Any]:
    return {
        "config_key": config_key(node_configs),
        "dataset_name": dataset_name,
        "prompt_fields_id": "pf_x",
        "node_configs": node_configs,
    }


def _file(
    archive: MeasurementArchive,
    rows: list[dict[str, Any]],
    *,
    dataset_name: str,
    node_configs: list[tuple[str, dict[str, Any]]] = _LLM_ONLY,
    **stamps: str,
) -> list[str]:
    """Grade A unless stamped: the replay feed reads an unstamped answer as C and never serves C."""
    stamped = [
        {
            **(
                {
                    "sample_key": sample_key(
                        query=row["query"],
                        ground_truth=row["ground_truth"],
                        question=None,
                        source_pin=None,
                    )
                }
                if "query" in row and "ground_truth" in row
                else {}
            ),
            "role": "panel",
            "source": RunSource.OPTIMIZATION_LOOP.value,
            "provenance": "A",
            "created_at": "2026-05-19T00:00:00Z",
            **stamps,
            **row,
        }
        for row in rows
    ]
    return archive.file_answers(_entry(node_configs, dataset_name), stamped)


def test_full_chain_rows_never_replay_on_prefix_match(tmp_path: Path) -> None:
    archive = MeasurementArchive(tmp_path)
    chain: list[tuple[str, dict[str, Any]]] = [
        (n, {}) for n in ("l1_generate", "l1_critique", "l2_context", "l3_plan")
    ]

    def _seed_chain(predicted: str, terminal_node: str) -> None:
        _file(
            archive,
            [
                {
                    "sample_id": 1,
                    "query": "q",
                    "ground_truth": "g",
                    "predicted": predicted,
                    "hit": True,
                    "fitness": 1.0,
                    "pipeline_data": {"terminal_node": terminal_node},
                }
            ],
            dataset_name="promptpotter-self",
            node_configs=chain,
        )

    # Filed LAST on purpose: wrongly served, it is the cell's most recent and no answer hides it.
    _seed_chain("short_circuit", terminal_node="l1_critique")
    _seed_chain("full_chain", terminal_node="l3_plan")

    query_configs: list[tuple[str, dict[str, Any]]] = [
        ("l1_generate", {}),
        ("l1_critique", {}),
        ("l2_context", {"layout": {"problem_description": ["critique"]}}),
        ("l3_plan", {}),
    ]
    cache = ReplayFeed(archive, query_configs).advance()
    served = [b.row["predicted"] for b in cache.values()]
    assert "full_chain" not in served, (
        "full-chain row replayed across a later-node config change — fake measurement"
    )
    assert served == ["short_circuit"], (
        "a genuine mid-chain short-circuit inside the trusted prefix should still reuse"
    )


def _seed_graded(
    archive: MeasurementArchive, *, name: str, grade: str, terminal_node: str, sample_id: int
) -> None:
    """The query carries `name`, so two answers are DIFFERENT samples and both can be served."""
    _file(
        archive,
        [
            {
                "sample_id": sample_id,
                "query": f"q_{name}",
                "ground_truth": "g",
                "predicted": "p",
                "hit": True,
                "fitness": 1.0,
                "pipeline_data": {"terminal_node": terminal_node},
            }
        ],
        dataset_name="aime",
        provenance=grade,
    )


def test_a_grade_C_answer_is_never_replayed(tmp_path: Path) -> None:
    archive = MeasurementArchive(tmp_path)
    _seed_graded(archive, name="clean", grade="A", terminal_node="llm_only", sample_id=7)
    _seed_graded(archive, name="connector", grade="C", terminal_node="token_matching", sample_id=8)

    served = ReplayFeed(archive, _LLM_ONLY).advance()
    assert [b.row["query"] for b in served.values()] == ["q_clean"]


def _seed_cell(archive: MeasurementArchive, *, dataset_name: str, hit: bool) -> None:
    _file(
        archive,
        [
            {
                "sample_id": 14,
                "query": f"q_{dataset_name}_14",
                "ground_truth": "g",
                "predicted": "p",
                "hit": hit,
                "fitness": 1.0 if hit else 0.0,
                "pipeline_data": {"terminal_node": "llm_only"},
            }
        ],
        dataset_name=dataset_name,
    )


def test_a_cell_replays_by_its_content_never_its_dataset_or_slot(tmp_path: Path) -> None:
    archive = MeasurementArchive(tmp_path)
    _seed_cell(archive, dataset_name="aime", hit=True)
    _seed_cell(archive, dataset_name="justlogic", hit=False)
    reusable = ReplayFeed(archive, _LLM_ONLY).advance()

    wider = [
        Sample(id=0, query="q_aime_14", ground_truth="g"),
        Sample(id=14, query="q_new", ground_truth="g"),
        Sample(id=1, query="q_aime_14", ground_truth="g", source_pin={"git_commit_id": "moved"}),
    ]
    served = _replayable_on(wider, reusable, "aime-wide")

    assert set(served) == {0}, "only the sample aime measured replays, at the slot it holds HERE"
    assert served[0].query == "q_aime_14" and served[0].sample_id == 0

    # Readers keyed on `(dataset_name, sample_id)` pool a re-cut slot; a relabel alone is a re-cut.
    same = [Sample(id=14, query="q_aime_14", ground_truth="g")]
    assert set(_replayable_on(same, reusable, "aime")) == {14}
    for recut in (
        [Sample(id=14, query="an entirely different question", ground_truth="g")],
        [Sample(id=14, query="q_aime_14", ground_truth="not_g")],
    ):
        with pytest.raises(DatasetIdentityError):
            _replayable_on(recut, reusable, "aime")

    holder, waiter = (ReplayFeed(MeasurementArchive(tmp_path), _LLM_ONLY) for _ in range(2))
    held = holder.claim("k_held")
    assert held is not None and waiter.claim("k_held") is None
    held.publish({"sample_id": 0, "sample_key": "k_held", "predicted": "p"}, grade="B")
    beside = waiter.claim("k_beside")
    assert beside is not None, "one cell's claim held another sample of its configuration"
    assert waiter.claimed_row("k_beside") is None
    assert (waiter.claimed_row("k_held") or {}).get("predicted") == "p"
    held.release()
    beside.release()


def _persisting_walk(root: Path, panel: list[Sample]) -> tuple[Any, JobSearchPoint]:
    from promptpotter.application.scoring.formula import compile_scorer
    from promptpotter.domain.pipeline_schema import NodePromptInfo, PipelineNode

    schema = pipeline_schema(
        name="claims",
        nodes=[PipelineNode(name="solve", tunes_llm=False, prompt_info=NodePromptInfo())],
    )
    session = loop_session(workspace(root), schema, panel, backend_id="b")
    session.scoring.scorer = compile_scorer(
        "label_match(predicted, ground_truth)", None, verifier_graded=False
    )
    sp = OptSearchPoint(instruction="Answer.").to_job_search_point(
        schema=schema, framing=TaskDecomposition(), demo=[]
    )
    return session, sp


def _drawn_row(sample: Sample, predicted: str) -> MeasuredCell:
    return MeasuredCell(
        sample_id=sample.id,
        sample_key=sample.key,
        query=sample.query,
        ground_truth="a",
        predicted=predicted,
    )


def _walk_panel(root: Path, panel: list[Sample], per_sample: str, per_cell: str | None) -> Any:
    from promptpotter.application.scoring import search_point_scorer
    from promptpotter.application.scoring.formula import compile_scorer

    session, sp = _persisting_walk(root, panel)
    session.scoring.scorer = compile_scorer(per_sample, per_cell, verifier_graded=False)

    async def _measure(sample: Sample, _session: Any, *, pipeline_params: Any) -> MeasuredCell:
        return _drawn_row(sample, "b" if sample.id % 2 else "a")

    with mock.patch.object(query_loop, "measure_sample", _measure):
        walked = asyncio.run(
            search_point_scorer.score_search_point(
                sp,
                panel,
                session,
                label="panel",
                measured=None,
            )
        )
    return session, walked


def test_rows_banked_under_one_formula_read_under_another_as_a_fresh_run_would(
    tmp_path: Path,
) -> None:
    from promptpotter.application.intelligence.hard_sample_archive import (
        build_archive_observations,
    )
    from promptpotter.application.scoring.formula import compile_scorer
    from promptpotter.domain.scoring import GRADE_KEYS
    from promptpotter.infrastructure.store import archive_queries

    def fresh_scorer(per_sample: str, per_cell: str | None) -> Any:
        return compile_scorer(per_sample, per_cell, verifier_graded=False)

    panel = [Sample(id=i, query=f"q{i}", ground_truth="a") for i in range(6)]
    banked_under = ("label_match(predicted, ground_truth)", None)
    read_under = ("0.5 * label_match(predicted, ground_truth)", "0.5 * fitness + 0.25")

    shared, banked_walk = _walk_panel(tmp_path / "shared", panel, *banked_under)
    assert all(GRADE_KEYS & r.keys() for r in banked_walk.sheet.wire()), "guard: the walk graded"
    banked = archive_queries.walked_answers(shared.store, banked_walk.cells)
    assert len(banked) == len(panel) and not any(GRADE_KEYS & r.keys() for r in banked)
    filed = shared.store.archive.cell_signatures()

    _, fresh = _walk_panel(tmp_path / "fresh", panel, *read_under)
    worth = {c.sample_id: c.grade.objective for c in fresh.sheet}
    assert len(set(worth.values())) == 2, "guard: hits and misses are worth different amounts"

    ruler = build_archive_observations(
        shared.store,
        dataset_name="claims",
        scorer=fresh_scorer(*read_under),
        sample_ids=None,
    )
    assert {o.sample_id: o.response for o in ruler} == pytest.approx(worth)
    _, replayed = _walk_panel(tmp_path / "shared", panel, *read_under)
    assert all(c.facts.cached for c in replayed.sheet), "guard: the second read bought nothing"
    assert [c[:3] for c in replayed.cells] == [c[:3] for c in banked_walk.cells]
    assert shared.store.archive.cell_signatures() == filed
    assert {c.sample_id: c.grade.objective for c in replayed.sheet} == worth
    assert replayed.scores.composite_fitness == pytest.approx(fresh.scores.composite_fitness)

    # A population that grew after it was folded reads at its present length, never the folded one.
    from promptpotter.application.intelligence.indexes.sample import SampleIndex

    def ruler_cells() -> dict[int, float]:
        observed = build_archive_observations(
            shared.store,
            dataset_name="claims",
            scorer=fresh_scorer(*read_under),
            sample_ids=None,
        )
        return {o.sample_id: o.response for o in observed}

    SampleIndex.ensure_for(
        shared.store,
        scorer=fresh_scorer(*read_under),
        dataset_name="claims",
        sample_ids=None,
    )
    assert ruler_cells() == pytest.approx(worth), "the fold's cells are the regraded ones"
    wider = [*panel, *(Sample(id=i, query=f"q{i}", ground_truth="a") for i in range(6, 10))]
    _walk_panel(tmp_path / "shared", wider, *banked_under)
    _, fresh_wider = _walk_panel(tmp_path / "fresh", wider, *read_under)
    assert ruler_cells() == pytest.approx(
        {c.sample_id: c.grade.objective for c in fresh_wider.sheet}
    )


# 2. The archive on disk


def test_a_reader_tailing_a_rewritten_cell_file_skips_no_answer(
    built_stores: Stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = built_stores.archive
    reader = MeasurementArchive(archive.base_dir)
    feed = ReplayFeed(reader, _LLM_ONLY)

    def _bank(sid: int, dataset_name: str = "reidx", via: MeasurementArchive = archive) -> None:
        cell = {"sample_id": sid, "sample_key": f"k{sid}", "predicted": "p", "trace": "T" * 400}
        _file(via, [cell], dataset_name=dataset_name)

    for sid in (1, 2, 3):
        _bank(sid)
    assert set(feed.advance()) == {"k1", "k2", "k3"}
    file_key = config_key(_LLM_ONLY)
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
    own = ReplayFeed(archive, _LLM_ONLY)
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


def test_every_answer_of_a_cell_is_kept_and_reindex_destroys_none(built_stores: Stores) -> None:
    archive = built_stores.archive
    entry = _entry(_LLM_ONLY, "reidx")
    other: list[tuple[str, dict[str, Any]]] = [("llm_only", {"model": "Y"})]

    def _answer(predicted: str, at: int, **facts: Any) -> dict[str, Any]:
        return {
            "sample_id": 1,
            "predicted": predicted,
            **facts,
            "created_at": f"2026-05-19T00:{at:02}",
        }

    refs = [
        *_file(archive, [_answer("first", 1)], dataset_name="reidx"),
        *_file(archive, [_answer("second", 2)], dataset_name="reidx"),
        *_file(archive, [_answer("ERROR", 3, error_category="transient")], dataset_name="reidx"),
        *_file(archive, [_answer("elsewhere", 4)], dataset_name="reidx", node_configs=other),
    ]
    assert len(set(refs)) == 4
    held = archive.population(entry)
    assert [row["predicted"] for row in held] == ["first", "second", "ERROR"]
    assert standing(held)[1]["predicted"] == "second"

    described = {e["config_key"] for e in archive.list_all(dataset_name="reidx")}
    assert described == {config_key(_LLM_ONLY), config_key(other)}
    assert archive.reindex() == {"indexed": 2, "undescribed": 0}
    assert {e["config_key"] for e in archive.list_all(dataset_name="reidx")} == described
    assert archive.population(entry) == held
    assert all(archive.answer(ref) is not None for ref in refs)


def _compactable_cell(sample_id: int, **extra: object) -> dict[str, object]:
    return {
        "sample_id": sample_id,
        "query": f"q{sample_id}",
        "ground_truth": "g",
        "predicted": "p",
        "fitness": 1.0,
        "objective": 1.0,
        "hit": True,
        "scored": {"auto": {"fitness": 1.0, "formula": "label_match(predicted, ground_truth)"}},
        "error_category": "",
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
            **extra,
        },
    }


def test_compaction_round_trips_every_field_it_moved(built_stores: Stores) -> None:
    archive = built_stores.archive
    entry = _entry(_LLM_ONLY, "reidx")
    # TWO filings, so a restore must clear each answer's own compaction stamp.
    _file(archive, [_compactable_cell(1)], dataset_name="reidx")
    _file(archive, [_compactable_cell(2)], dataset_name="reidx")
    before = [dict(row) for row in archive.population(entry)]
    assert len(before) == 2 and not any("compaction" in row for row in before)

    report = compact_measurement_archive(built_stores, dataset="reidx", apply=True)
    assert report.files_touched == 1
    assert report.rows_moved == 2
    assert report.bytes_freed > 0

    mid = archive.population(entry)
    hot = mid[0]
    assert "hit" not in hot
    assert "scored" not in hot
    assert "reasoning_trace" not in hot["pipeline_data"]
    # The reading campaign's scorer re-grades from these, so compaction leaves them hot.
    assert hot["predicted"] == "p"
    assert hot["ground_truth"] == "g"
    assert hot["pipeline_data"]["step_tokens"] == {"llm_only": {"input": 10, "output": 5}}
    assert hot["pipeline_data"]["step_timings"] == {"llm_only": 0.5}
    assert all("compaction" in row for row in mid)

    restored = restore_measurement_archive(built_stores, dataset="reidx", apply=True)
    assert restored.files_touched == 1
    assert restored.conflicts == 0
    assert archive.population(entry) == before


# 3. Replayed decisions, and forks


def _r(score: float) -> dict:
    # `objective` equals `fitness` here deliberately; `factories.measurement` diverges the two.
    return {
        "query": "q",
        "predicted": "p",
        "ground_truth": "g",
        "fitness": score,
        "objective": score,
    }


def _decisions(*recs: dict[str, Any]) -> list[ResumeCheckpointRecord]:
    return [ResumeCheckpointRecord.model_validate(rec) for rec in recs]


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


def test_a_replayer_that_cannot_re_derive_is_not_reported_as_a_match() -> None:
    round_data = _round(
        round=0,
        all_candidate_results={"c1": sheet([{**_r(1.0), "sample_id": 0}])},
    )
    div = replay_decisions(
        round_data,
        _decisions(
            {
                "kind": "round_winner",
                "inputs_ref": {"candidate_ids": ["c1"], "round_num": 0, "coverage_floor": 1},
                "outcome": "c1",
                "data": {"parent_cells": [{**_r(0.0), "sample_id": 0}]},
            }
        ),
    )
    assert div is not None, "an unreproducible decision was reported as a clean replay"
    assert div.kind == "replay_error:round_winner", (
        "the mismatch must name WHICH check went blind, not just that one did"
    )


def test_a_cut_made_past_an_errored_cell_replays_on_the_cells_it_was_fit_on() -> None:
    from types import SimpleNamespace

    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.optimizers.nodes import RoundContext
    from promptpotter.application.optimizers.potter.pobb.checks import EliminationGate
    from promptpotter.application.optimizers.potter.race import PoBBRace
    from promptpotter.domain.search_point import JobSearchPoint
    from tests.factories import measurement, measurements, pobb_knobs

    cycle = SimpleNamespace(
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
        NodeContext(RoundContext(cycle, 3, callbacks=None), "pobb"),  # type: ignore[arg-type]
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
    signal = race.rule([]).check(walked.cells)
    reading = race.judge(signal, candidate_id="c1", results=walked.cells, labels={"c1": "C3.1"})
    assert reading is not None and reading.context["gate"] == EliminationGate.EPSILON

    [decision] = cycle.pending_decisions
    assert decision.data["candidate_sample_ids"] == ["0", "1", "3", "4", "5", "6", "7"]
    replayed = replay_decisions(
        _round(round=3, all_candidate_results={"c1": walked}),
        _decisions(
            {
                "kind": decision.kind,
                "round": decision.round,
                "inputs_ref": decision.inputs_ref,
                "outcome": decision.outcome,
                "data": decision.data,
            }
        ),
    )
    assert replayed is None, "the cut the live rule made is the cut its own record replays"


_CAMPAIGN = "testds__20260101-000000"


def test_inherit_fork_origin_unmodified_inherits_else_rescores(built_stores: Stores) -> None:
    from types import SimpleNamespace

    from promptpotter.application.origin import (
        resolve_origin_opt_search_point,
        try_inherit_fork_origin,
    )
    from promptpotter.application.scoring.formula import compile_scorer
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.search_point import JobSearchPoint, TaskDecomposition

    stores = built_stores
    parent = "cycle_inherit_parent"
    fork = "cycle_inherit_parent_fork_abc123"
    prompt = {"instruction": "do the thing", "persona": "you are precise"}

    parent_hop = CycleHop(campaign_id=_CAMPAIGN, cycle_id=parent)
    stores.campaigns.mint_cycle(parent_hop)
    CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(parent_hop))).append(
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
                    ),
                ],
            )
        )
    )
    stores.campaigns.mint_fork_cycle(
        parent_hop,
        fork,
        ForkSpec(
            trigger=ForkTrigger.OPERATOR_STEERED,
            reason="",
            issued_by="",
            from_round=1,
            from_candidate_id="c1",
        ),
        from_round=0,
    )

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

    # Resolved as `establish_campaign_origin` does: the fork seed wins.
    unmodified_seed = CycleSeed(origin_prompt_fields=dict(prompt), origin_source="fork_seed")
    unmodified_osp = resolve_origin_opt_search_point(
        [], seed=unmodified_seed, pipeline_params=None, schema=PipelineSchema()
    )
    inherited = try_inherit_fork_origin(
        session,  # type: ignore[arg-type]
        unmodified_seed,
        resolved_origin=unmodified_osp,
        sp=JobSearchPoint(),
        framing=TaskDecomposition(),
    )
    assert inherited is not None
    # The branch-point CANDIDATE's recorded accuracy, never its round's 0.4.
    assert inherited.report.accuracy == 0.2
    assert isinstance(inherited.resolved_origin, OptSearchPoint)
    assert inherited.resolved_origin.lineage.source == "origin"

    edited_seed = CycleSeed(
        origin_prompt_fields={**prompt, "instruction": "do it differently"},
        origin_source="fork_seed",
    )
    edited = try_inherit_fork_origin(
        session,  # type: ignore[arg-type]
        edited_seed,
        resolved_origin=resolve_origin_opt_search_point(
            [], seed=edited_seed, pipeline_params=None, schema=PipelineSchema()
        ),
        sp=JobSearchPoint(),
        framing=TaskDecomposition(),
    )
    assert edited is None


def test_an_applied_scenario_forks_at_its_round_and_carries_the_criterion(
    built_stores: Stores,
) -> None:
    from promptpotter.application.bench.resume_and_fork.fork_siblings import (
        mint_operator_fork,
    )
    from promptpotter.application.campaign_config import (
        CampaignConfig,
        OptimizationConfig,
        apply_config_overrides,
    )
    from promptpotter.domain.run_records import ConfigOverrides

    store = built_stores.campaigns
    parent = CycleHop(campaign_id=_CAMPAIGN, cycle_id="cycle_applyscenario")
    store.mint_cycle(parent)
    criterion = "0.9 * accuracy + 0.1 * (1 - mean_latency_s)"

    fork_id = mint_operator_fork(
        stores=built_stores,
        hop=parent,
        from_round=2,
        from_candidate_id="",
        seed=CycleSeed(config_overrides=ConfigOverrides(scoring=criterion)),
        keep_rounds=True,
    )
    child = parent.model_copy(update={"cycle_id": fork_id})
    index = store.load(child)
    assert index is not None and index.fork is not None
    assert index.fork.issued_by == str(built_stores.identity.user_id)

    # OPERATOR_REWIND is the rebase branch, the one that lifts rounds 0..N-1.
    assert index.fork.trigger is ForkTrigger.OPERATOR_REWIND
    assert index.fork.from_round == 2

    seed = store.read_cycle_seed(child)
    assert seed is not None
    base = CampaignConfig(optimization=OptimizationConfig(degradation_threshold=0.05))
    applied = apply_config_overrides(base, seed.config_overrides)
    assert applied.scoring == criterion
    assert base.scoring is None


def _stand_round(ledger: CycleEventLog, n: int, candidate_id: str, *, crowned: bool = True) -> None:
    label = f"C{n}.1" if n else "C0"
    selected = [label] if crowned else []
    scores = scored_candidate(candidate_id, label=label)
    ledger.append(RoundEnteredRecord(round=n))
    ledger.append(CandidateMintedRecord(round=n, idx=0, candidate_id=candidate_id, label=label))
    ledger.append(CandidateScoredRecord(round=n, candidate_idx=0, candidate_total=1, scores=scores))
    ledger.append(ElectionRecord(round=n, selected_labels=selected, elects_on="accuracy"))
    ledger.append(
        RoundClosedRecord.of(
            _round(round=n, label=label, candidate_scores=[scores], selected_labels=selected)
        )
    )


def test_a_rewind_leaves_no_election_close_or_crown_standing_past_it(built_stores: Stores) -> None:
    from promptpotter.infrastructure.store.lineage_queries import build_lineage_tree

    stores = built_stores
    hop = CycleHop(campaign_id=_CAMPAIGN, cycle_id="cycle_rewound")
    stores.campaigns.mint_cycle(hop)
    ledger = CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(hop)))
    for n, candidate_id in enumerate(("c0", "c1", "c2")):
        _stand_round(ledger, n, candidate_id)

    def served() -> list[tuple[str, bool, bool]]:
        tree = build_lineage_tree(stores, (hop,))
        return [
            (kid.id, kid.reading.election.held, kid.reading.election.selected)
            for kid in tree.children
        ]

    assert served() == [("c0", True, True), ("c1", True, True), ("c2", True, True)], "guard"

    ledger.append(RoundEnteredRecord(round=1, rewound=True))
    standing = stores.campaigns.standing_rounds(hop)
    assert list(standing.rounds) == list(standing.elections) == [0]
    assert standing.crowns == {0: "C0"}
    assert served() == [("c0", True, True)]

    _stand_round(ledger, 1, "c1_again", crowned=False)
    assert served() == [("c0", True, True), ("c1_again", True, False)]
    # The next round's parent is still the origin, and its line runs THROUGH the round that held.
    origin, held = build_lineage_tree(stores, (hop,)).children
    assert (origin.stands, origin.course_winner, origin.course_latest) == (True, True, False)
    assert (held.stands, held.course_winner, held.course_latest) == (False, False, True)
    assert [(s.round, s.rows) for s in origin.main_line] == [(0, [origin.row]), (1, [])]
    assert [(s.round, s.rows) for s in held.main_line] == [(0, [origin.row]), (1, [held.row])]
    assert origin.row != held.row
    assert held.origin_row == origin.row


def test_a_rewind_moves_the_selection_for_the_ledger_the_tree_and_the_index_alike(
    built_stores: Stores,
) -> None:
    from promptpotter.domain.phase_views import ViewAnchors
    from promptpotter.domain.results import RunStanding
    from promptpotter.domain.run_records import RoundStandingRecord
    from promptpotter.infrastructure.store.lineage_queries import build_lineage_tree

    store = built_stores.campaigns
    hop = CycleHop(campaign_id=_CAMPAIGN, cycle_id="cycle_restood")
    store.mint_cycle(hop)
    ledger = CycleEventLog.open(CycleDir(store.cycle_dir(hop)))
    closed: list[RoundResult] = []
    for n in range(3):
        label = f"C{n}.1" if n else "C0"
        pick = OptSearchPoint(instruction=label)
        scores = scored_candidate(pick.id, label=label)
        closed.append(
            _round(
                round=n,
                label=label,
                opt_sp=pick,
                candidate_scores=[scores],
                selected_labels=[label],
            )
        )
        stood = RunStanding.after(closed, lives=None, spent=None)
        ledger.append(RoundEnteredRecord(round=n))
        ledger.append(RoundClosedRecord.of(closed[-1]))
        ledger.append(RoundStandingRecord(round=n, run_standing=stood, anchors=ViewAnchors()))

    def selected() -> list[str]:
        index = store.load(hop)
        assert index is not None
        readers = (
            store.standing_rounds(hop).standing,
            build_lineage_tree(built_stores, (hop,)).run_standing,
            index.standing,
        )
        return [reader.selection.label for reader in readers]  # type: ignore[union-attr]

    assert selected() == ["C2.1"] * 3, "guard"
    store.rewind_to_round(hop, 1)
    assert selected() == ["C1.1"] * 3


def test_a_forks_own_ledger_displaces_its_parents_prefix_once(built_stores: Stores) -> None:
    store = built_stores.campaigns
    parent = CycleHop(campaign_id=_CAMPAIGN, cycle_id="cycle_displaced")
    store.mint_cycle(parent)
    ledger = CycleEventLog.open(CycleDir(store.cycle_dir(parent)))
    for n in range(3):
        _stand_round(ledger, n, f"p{n}")

    fork = store.mint_fork_cycle(
        parent,
        "cycle_displaced_fork_r1",
        ForkSpec(trigger=ForkTrigger.OPERATOR_REWIND, reason="", issued_by="", from_round=3),
        from_round=3,
    )
    assert [c.candidate_id for c in store.standing_rounds(fork).candidates()] == ["p0", "p1", "p2"]

    own = CycleEventLog.open(CycleDir(store.cycle_dir(fork)))
    _stand_round(own, 1, "f1")
    _stand_round(own, 2, "f2", crowned=False)

    standing = store.standing_rounds(fork)
    assert [c.candidate_id for c in standing.candidates()] == ["p0", "f1", "f2"]
    closed_on = [held.close.candidate_scores[0].candidate_id for held in standing.rounds.values()]
    assert closed_on == ["p0", "f1", "f2"]
    assert standing.crowns == {0: "C0", 1: "C1.1"}
    assert [c.candidate_id for c in store.standing_rounds(parent).candidates()] == [
        "p0",
        "p1",
        "p2",
    ]


def test_a_close_naming_an_unregistered_payload_stops_the_read(built_stores: Stores) -> None:
    """A ledger read SKIPS a line that no longer validates; this close must raise instead."""
    store = built_stores.campaigns
    hop = CycleHop(campaign_id=_CAMPAIGN, cycle_id="cycle_of_a_peer")
    store.mint_cycle(hop)
    ledger = CycleEventLog.open(CycleDir(store.cycle_dir(hop)))
    _stand_round(ledger, 0, "c0")
    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    close = json.loads(lines[-1])
    close["optimizer_state"]["manifest"] = "uninstalled"
    ledger.path.write_text("\n".join([*lines[:-1], json.dumps(close), ""]), encoding="utf-8")

    with pytest.raises(UnregisteredPayloadError):
        list(CycleEventLog.open(CycleDir(store.cycle_dir(hop))).iter())
    with pytest.raises(UnregisteredPayloadError):
        store.standing_rounds(hop)
