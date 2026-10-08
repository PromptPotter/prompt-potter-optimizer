"""Lost or corrupted measurement.

Owns `infrastructure/store/measurement_archive.py` and `archive_queries.py`,
`application/maintenance/`, `application/bench/resume_and_fork/` and `application/origin.py`. A
replay served to the wrong content, a paid row dropped, a fork that inherits the wrong origin.
"""

from __future__ import annotations

import asyncio
import types
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from factories import pipeline_schema

from promptpotter.application.bench.resume_and_fork.replayers import replay_decisions
from promptpotter.application.maintenance.archive_maintenance import (
    compact_measurement_archive,
    restore_measurement_archive,
)
from promptpotter.application.scoring import query_loop
from promptpotter.application.scoring.search_point_scorer import _replayable_on
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.results import RoundResult
from promptpotter.domain.run_records import CycleSeed
from promptpotter.domain.sample import Sample, sample_key
from promptpotter.domain.search_point import JobSearchPoint, TaskDecomposition
from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive, ReplayFeed
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import DatasetIdentityError
from tests.factories import optimizer_state

# 1. Replay eligibility


def _archive(archive: MeasurementArchive, run_id: str, data: dict[str, Any]) -> None:
    """Seed one complete run — the whole measurement set is what is new.

    Grade A unless the caller stamps its own: `entry_grade` reads an unstamped row as C, and the
    reuse path excludes C, so an ungraded fixture is not replayable at all — every test here that
    is ABOUT matching would then pass or fail on provenance instead. Production rows always carry
    one (`loaders.py::build_dataset_run_data` grades every run it banks). Every row is keyed off its
    own content, as `measure_sample` keys every row it writes."""
    data.setdefault("provenance", {"grade": "A", "deliberate_source": True})
    for row in data["measurements"]:
        row.setdefault(
            "sample_key",
            sample_key(
                query=row["query"],
                ground_truth=row["ground_truth"],
                question=None,
                source_pin=None,
            ),
        )
    archive.append_run(run_id, data, data["measurements"])


def test_full_chain_rows_never_replay_on_prefix_match(tmp_path: Path) -> None:
    """A sample whose outcome consumed the FULL node chain (``terminal_node`` =
    last node — the L4 inner-recursion stamp) must not replay for a query that
    differs at a later node. The buggy stamp (``l1_critique``, a mid-chain node)
    let a candidate editing ``l2_context``/``l3_plan`` silently replay the
    origin's rows — a fake score with no error and no symptom (run b786e9 C1.3).
    A genuine mid-chain short-circuit still replays: that reuse is correct."""
    archive = MeasurementArchive(tmp_path)
    chain = ["l1_generate", "l1_critique", "l2_context", "l3_plan"]

    def _seed_chain(run_id: str, terminal_node: str) -> None:
        _archive(
            archive,
            run_id,
            {
                "run_id": run_id,
                "name": run_id,
                "content_hash": f"hash_{run_id}",
                "prompt_fields_id": "pf_x",
                "item_count": 1,
                "node_configs": [(n, {}) for n in chain],
                "pipeline_params": {},
                "created_at": "2026-07-03T00:00:00Z",
                "measurements": [
                    {
                        "sample_id": 1,
                        "query": "q",
                        "ground_truth": "g",
                        "predicted": run_id,
                        "hit": True,
                        "fitness": 1.0,
                        "pipeline_data": {"terminal_node": terminal_node},
                    }
                ],
                "dataset_name": "promptpotter-self",
            },
        )

    _seed_chain("full_chain", terminal_node="l3_plan")
    _seed_chain("short_circuit", terminal_node="l1_critique")

    # Query differs at l2_context → prefix match of length 2.
    query_configs: list[tuple[str, dict[str, Any]]] = [
        ("l1_generate", {}),
        ("l1_critique", {}),
        ("l2_context", {"layout": {"problem_description": ["critique"]}}),
        ("l3_plan", {}),
    ]
    # Both runs measured the SAME cell, so the question is which row wins it.
    cache = ReplayFeed(archive, query_configs).advance()
    served = [b.row["predicted"] for b in cache.values()]
    assert "full_chain" not in served, (
        "full-chain row replayed across a later-node config change — fake measurement"
    )
    assert served == ["short_circuit"], (
        "a genuine mid-chain short-circuit inside the trusted prefix should still reuse"
    )


def _seed_graded(
    archive: MeasurementArchive, *, run_id: str, grade: str, terminal_node: str, sample_id: int
) -> None:
    """Save one run carrying a provenance grade and a single sample, its query named after the run
    so the two runs measure DIFFERENT samples and both can be served at once."""
    provenance: dict[str, Any] = {"grade": grade, "deliberate_source": grade != "C"}
    _archive(
        archive,
        run_id,
        {
            "run_id": run_id,
            "name": run_id,
            "content_hash": f"hash_{run_id}",
            "prompt_fields_id": "pf_x",
            "item_count": 1,
            "node_configs": [("llm_only", {"model": "X"})],
            "pipeline_params": {"llm_only": {"model": "X"}},
            "provenance": provenance,
            "created_at": "2026-05-19T00:00:00Z",
            "measurements": [
                {
                    "sample_id": sample_id,
                    "query": f"q_{run_id}",
                    "ground_truth": "g",
                    "predicted": "p",
                    "hit": True,
                    "fitness": 1.0,
                    "pipeline_data": {"terminal_node": terminal_node},
                }
            ],
            "dataset_name": "aime",
        },
    )


def test_a_grade_C_run_is_never_replayed(tmp_path: Path) -> None:
    """A grade-C run is never served back as a cache hit, so its stale sample cannot pose as a real
    evaluation: replayed rows are re-archived under the READING run, which `build_dataset_run_data`
    grades from its own ``source``/``human_intervened``, so a served C cell re-enters as A and
    reaches the δ ruler `hard_sample_archive` keeps it out of (ADR-0005: every consumer excludes C)."""
    archive = MeasurementArchive(tmp_path)
    _seed_graded(archive, run_id="clean", grade="A", terminal_node="llm_only", sample_id=7)
    _seed_graded(
        archive, run_id="connector", grade="C", terminal_node="token_matching", sample_id=8
    )

    served = ReplayFeed(archive, [("llm_only", {"model": "X"})]).advance()
    assert [b.row["query"] for b in served.values()] == ["q_clean"]


def _seed_run(archive: MeasurementArchive, *, run_id: str, dataset_name: str, hit: bool) -> None:
    """Minimal run envelope — one sample whose query text is dataset-tagged, so a
    cross-dataset bleed is detectable by query overlap."""
    _archive(
        archive,
        run_id,
        {
            "run_id": run_id,
            "name": run_id,
            "content_hash": f"hash_{run_id}",
            "prompt_fields_id": "pf_x",
            "item_count": 1,
            "node_configs": [("llm_only", {"model": "X"})],
            "pipeline_params": {"llm_only": {"model": "X"}},
            "created_at": "2026-05-19T00:00:00Z",
            "measurements": [
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
            "dataset_name": dataset_name,
        },
    )


def test_a_cell_replays_by_its_content_never_its_dataset_or_slot(tmp_path: Path) -> None:
    """A cell is the sample's content under the instrument's configuration, so a sample carried
    into a wider panel under a new name replays what was measured on it. Keyed by dataset and
    position instead, twenty tasks re-measured the ten they shared with a ten-task panel — paid
    again for cells already banked, with nothing on screen saying so.

    The other direction is the one that hands a wrong score to a decision: a slot another dataset
    measured, holding different content here, must not bleed; nor may a task whose upstream pin
    moved, though its id — the query — is unchanged."""
    archive = MeasurementArchive(tmp_path)
    _seed_run(archive, run_id="aime_cached", dataset_name="aime", hit=True)
    _seed_run(archive, run_id="just_fresh", dataset_name="justlogic", hit=False)
    reusable = ReplayFeed(archive, [("llm_only", {"model": "X"})]).advance()

    wider = [
        Sample(id=0, query="q_aime_14", ground_truth="g"),
        Sample(id=14, query="q_new", ground_truth="g"),
        Sample(id=1, query="q_aime_14", ground_truth="g", source_pin={"git_commit_id": "moved"}),
    ]
    served = _replayable_on(wider, reusable, "aime-wide")

    assert set(served) == {0}, "only the sample aime measured replays, at the slot it holds HERE"
    assert served[0]["hit"] is True and served[0]["sample_id"] == 0

    # Replay matches by content and cannot be fooled; the readers keyed on POSITION can. The δ
    # ruler, the sample index and hard samples key a sample by ``(dataset_name, sample_id)``, so
    # rows re-cut under a name already used would pool two questions' history in one slot. The
    # guard reads the content off the stored row itself, so it catches ONE edited row — and
    # ground truth alone is enough: a relabelled row is as wrong as a replaced question.
    same = [Sample(id=14, query="q_aime_14", ground_truth="g")]
    assert set(_replayable_on(same, reusable, "aime")) == {14}
    for recut in (
        [Sample(id=14, query="an entirely different question", ground_truth="g")],
        [Sample(id=14, query="q_aime_14", ground_truth="not_g")],
    ):
        with pytest.raises(DatasetIdentityError):
            _replayable_on(recut, reusable, "aime")


def _persisting_walk(root: Path, panel: list[Sample]) -> tuple[Any, JobSearchPoint]:
    """A session archiving under ``root`` and the one searchpoint it scores — each caller its own
    stores, as each process has its own."""
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.scoring.formula import compile_scorer
    from promptpotter.domain.pipeline_schema import NodePromptInfo, PipelineNode
    from promptpotter.infrastructure.store.stores import build_stores
    from promptpotter.shared.identity import default_identity

    schema = pipeline_schema(
        name="claims",
        nodes=[PipelineNode(name="solve", tunes_llm=False, prompt_info=NodePromptInfo())],
    )
    session = Session(
        store=build_stores(
            default_identity(), projects_root=root / "projects", benchmarks_root=root / "datasets"
        ),
        backend_id="b",
        backend_client=types.SimpleNamespace(  # type: ignore[arg-type]
            max_cells_in_flight=1,
            cancel_stops_billing=True,
            holds_own_sends=True,
            derives_spend_bounds=False,
            backpressure=types.SimpleNamespace(reading=lambda: None),
        ),
        pipeline_schema=schema,
        samples=panel,
        dataset_name="claims",
    )
    session.source = RunSource.OPTIMIZATION_LOOP
    session.scoring.scorer = compile_scorer(
        "label_match(predicted, ground_truth)", None, verifier_graded=False
    )
    sp = OptSearchPoint(instruction="Answer.").to_job_search_point(
        schema=schema, framing=TaskDecomposition(), demo=[]
    )
    return session, sp


def _drawn_row(sample: Sample, predicted: str) -> dict[str, Any]:
    """What ``measure_sample`` returns: the cell's facts, ungraded."""
    return {
        "sample_id": sample.id,
        "sample_key": sample.key,
        "query": sample.query,
        "ground_truth": "a",
        "predicted": predicted,
        "error": None,
    }


def _walk_panel(root: Path, panel: list[Sample], per_sample: str, per_cell: str | None) -> Any:
    """One persisting walk of ``panel`` under the formula pair, answering "a" on even ids."""
    from promptpotter.application.scoring import search_point_scorer
    from promptpotter.application.scoring.formula import compile_scorer

    session, sp = _persisting_walk(root, panel)
    session.scoring.scorer = compile_scorer(per_sample, per_cell, verifier_graded=False)

    async def _measure(sample: Sample, _session: Any, *, pipeline_params: Any) -> dict:
        return _drawn_row(sample, "b" if sample.id % 2 else "a")

    with mock.patch.object(query_loop, "measure_sample", _measure):
        walked = asyncio.run(
            search_point_scorer.score_search_point(
                sp,
                panel,
                session,
                label="panel",
                measured=None,
                on_sample_scored=None,
                on_sample_starting=None,
            )
        )
    return session, walked


def test_rows_banked_under_one_formula_read_under_another_as_a_fresh_run_would(
    tmp_path: Path,
) -> None:
    """A campaign whose formula differs from the one its archive rows were measured under must read
    them exactly as a fresh run under ITS formula would — cell by cell, through the ``per_cell``
    composite and its miss-cost share. Graded off a stored ``fitness``, the composite silently
    mixes the writer's correctness with the reader's price: the ruler and the replayed walk both
    render, on the wrong numbers."""
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
    # The walk grades its rows; the archive keeps what was measured. A grade is ONE formula's
    # reading of a cell, and banked beside the facts it reaches every later reader as its own.
    assert all(GRADE_KEYS & r.keys() for r in banked_walk.results), "guard: the walk graded"
    banked = archive_queries.load_run(shared.store, banked_walk.run_id)["measurements"]
    assert len(banked) == len(panel) and not any(GRADE_KEYS & r.keys() for r in banked)

    _, fresh = _walk_panel(tmp_path / "fresh", panel, *read_under)
    worth = {r["sample_id"]: r["objective"] for r in fresh.results}
    assert len(set(worth.values())) == 2, "guard: hits and misses are worth different amounts"

    ruler = build_archive_observations(
        shared.store,
        dataset_name="claims",
        scorer=fresh_scorer(*read_under),
        scorer_id="read_under",
        sample_ids=None,
    )
    assert {o.sample_id: o.response for o in ruler} == pytest.approx(worth)
    _, replayed = _walk_panel(tmp_path / "shared", panel, *read_under)
    assert all(r.get("cached") for r in replayed.results), "guard: the second read bought nothing"
    assert {r["sample_id"]: r["objective"] for r in replayed.results} == worth
    assert replayed.scores["composite_fitness"] == pytest.approx(fresh.scores["composite_fitness"])

    # The sample fold carries each run's graded cells and the ruler reads them from there, so a
    # run that grew after it was folded must read at its present length, never the folded one.
    from promptpotter.application.intelligence.indexes.sample import SampleIndex

    def ruler_cells() -> dict[int, float]:
        observed = build_archive_observations(
            shared.store,
            dataset_name="claims",
            scorer=fresh_scorer(*read_under),
            scorer_id="read_under",
            sample_ids=None,
        )
        return {o.sample_id: o.response for o in observed}

    SampleIndex.ensure_for(
        shared.store,
        scorer=fresh_scorer(*read_under),
        scorer_id="read_under",
        dataset_name="claims",
        sample_ids=None,
    )
    assert ruler_cells() == pytest.approx(worth), "the fold's cells are the regraded ones"
    wider = [*panel, *(Sample(id=i, query=f"q{i}", ground_truth="a") for i in range(6, 10))]
    _walk_panel(tmp_path / "shared", wider, *banked_under)
    _, fresh_wider = _walk_panel(tmp_path / "fresh", wider, *read_under)
    assert ruler_cells() == pytest.approx(
        {r["sample_id"]: r["objective"] for r in fresh_wider.results}
    )


# 2. The archive on disk


def _archive_run(
    archive: object,
    *,
    run_id: str,
    content_hash: str,
    measurements: list[dict[str, object]] | None = None,
    name: str | None = None,
) -> None:
    """Minimal ``MeasurementArchive.append_run`` envelope — one dataset-tagged sample.

    ``name`` is the run LABEL, which compaction reads to decide eligibility; it defaults to the
    run_id, as it does for every other caller here."""
    items = measurements or [{"sample_id": 1, "query": f"q_{run_id}", "hit": True}]
    archive.append_run(  # type: ignore[attr-defined]
        run_id,
        {
            "run_id": run_id,
            "name": name if name is not None else run_id,
            "content_hash": content_hash,
            "prompt_fields_id": "pf",
            "item_count": len(items),
            "node_configs": [("llm_only", {"model": "X"})],
            "created_at": f"2026-05-19T00:00:{run_id[-2:]}Z",
            "measurements": items,
            "dataset_name": "reidx",
        },
        items,
    )


def test_partial_walk_log_folds_to_the_full_record(built_stores: Stores) -> None:
    """The run detail is an append-only log: a save writes only the NEW rows, so a walk that
    dies mid-dataset must still fold back to everything already paid for — and a re-walk of
    the same run must supersede a sample in place, never duplicate it and never shrink.

    This is the archive's half of the aborted-run invariant: the silent harm is an interrupted run whose log reads back short, quietly dropping
    measurements nothing will ever pay for again."""
    archive = built_stores.archive
    # Two saves, as the scoring walk makes them: sample 1, then sample 2 appended.
    _archive_run(archive, run_id="r_a", content_hash="h", measurements=[{"sample_id": 1}])
    _archive_run(
        archive,
        run_id="r_a",
        content_hash="h",
        measurements=[{"sample_id": 2, "hit": True}],
    )
    detail = archive.load_by_id("r_a")
    assert detail is not None
    assert [m["sample_id"] for m in detail["measurements"]] == [1, 2]

    # A re-measure of sample 1 supersedes in place — last-wins by sample_id.
    _archive_run(
        archive,
        run_id="r_a",
        content_hash="h",
        measurements=[{"sample_id": 1, "hit": True}],
    )
    detail = archive.load_by_id("r_a")
    assert detail is not None
    assert [m["sample_id"] for m in detail["measurements"]] == [1, 2]
    assert detail["measurements"][0]["hit"] is True

    # Compaction drops the superseded rows without losing a measurement.
    archive.compact_run("r_a")
    compacted = archive.load_by_id("r_a")
    assert compacted == detail

    # force_fresh REPLACES: an append-only log has to be told to forget.
    archive.reset_run("r_a")
    assert archive.load_by_id("r_a") is None

    # A reader in another process tails both logs from where it stopped, and a compaction swaps
    # the file under it — onto a freed inode, on ext4. Resumed at the old offset, it skips every
    # row banked since: never replayed, so bought again.
    reader = MeasurementArchive(archive.base_dir)
    feed = ReplayFeed(reader, [("llm_only", {"model": "X"})])

    def _cell(sid: int) -> dict[str, object]:
        return {"sample_id": sid, "sample_key": f"k{sid}", "predicted": "p"}

    def _bank(run_id: str, sid: int, name: str = "r") -> None:
        header = {
            "run_id": run_id,
            "name": name,
            "content_hash": "h",
            "prompt_fields_id": "pf",
            "item_count": 1,
            "node_configs": [("llm_only", {"model": "X"})],
            "provenance": {"grade": "A", "deliberate_source": True},
            "created_at": "2026-05-19T00:00:00Z",
            "dataset_name": "reidx",
        }
        archive.append_run(run_id, header, [_cell(sid)])

    for sid in (1, 2, 3):
        _bank("r_t", sid)
    assert set(feed.advance()) == {"k1", "k2", "k3"}
    log = archive._detail_path("r_t")
    read_to = log.stat().st_size
    # Rewritten IN PLACE, as a swap onto a reused inode reads: the dead headers go, one row lands.
    lines = archive.detail_lines("r_t")
    log.write_text("".join(ln for ln in lines if '"k": "run"' not in ln) + lines[-1])
    _bank("r_t", 4)
    assert log.stat().st_size < read_to
    assert set(feed.advance()) == {"k4"}, "a row banked after the compaction was skipped"

    reader.list_all()
    tailed = reader._index_path().stat().st_size
    assert archive.maintain_index()
    # One entry long enough to straddle the offset the reader stopped at in the old file.
    _bank("r_long", 5, name="n" * tailed)
    assert "r_long" in {e["run_id"] for e in reader.list_all()}, "an index entry was skipped"


def test_two_readings_of_one_searchpoint_are_two_runs_and_reindex_destroys_neither(
    built_stores: Stores,
) -> None:
    """A run is identified by ``run_id``; ``content_hash`` is a property of what it MEASURED, and
    two runs legitimately share one. The label is not in the hash, so `origin_<h>` and
    `parent_<h>` are the same searchpoint on the same rows read twice.

    Keyed on ``content_hash``, the index harms twice, silently: (1) one entry survives for the
    pair, its `scores`/`item_count`/`source`/`provenance` whichever landed last — `_save_run` fires
    per sample, so an in-flight 3-of-30 overwrites a complete 30-of-30 that `AxisIndex` and
    `archive_top_runs` read into the optimizer prompt; (2) ``reindex`` unlinks the loser's detail
    file as an orphan, destroying paid LLM spend and reporting it as GC."""
    archive = built_stores.archive
    _archive_run(archive, run_id="run_10", content_hash="h_a")
    _archive_run(archive, run_id="run_11", content_hash="h_b")
    # The SAME searchpoint on the same rows, read a second time under its own label.
    _archive_run(archive, run_id="run_12", content_hash="h_a")

    rows = {e["run_id"]: e["content_hash"] for e in archive.list_all(dataset_name="reidx")}
    assert rows == {"run_10": "h_a", "run_11": "h_b", "run_12": "h_a"}

    counts = archive.reindex()
    assert counts["indexed"] == 3  # three runs, not two hashes

    after = {e["run_id"]: e["content_hash"] for e in archive.list_all(dataset_name="reidx")}
    assert after == rows  # reindex reproduces the fold, doesn't shrink it
    for run_id in ("run_10", "run_11", "run_12"):
        assert archive.load_by_id(run_id) is not None  # nothing was deleted


def _compactable_cell(sample_id: int, **extra: object) -> dict[str, object]:
    """One richly-stamped measured cell — every field compaction may move, and every field the
    ruler re-grades from, so a test can assert both halves off one row."""
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
    """Compaction MOVES fields out of a measurement row into a gzip cold store; restore puts them
    back. If the round trip is lossy the harm is silent and irreversible in the worst way — the
    rows are paid LLM spend, and nothing raises when one comes back missing a key.

    Also pins the half that must survive compaction on its own: the archive is re-graded by the
    READING campaign's scorer, so ``predicted``/``ground_truth``/``step_tokens``/``step_timings``
    have to still be there while the run is compacted, or a later campaign start raises
    ``ScoringTermMissingError`` over an archive that looks fine on disk."""
    archive = built_stores.archive
    # TWO saves, as the scoring walk makes them — so the log carries TWO header rows and the fold
    # reads the LAST. A stamp cleared from only the first header leaves the winning row still
    # reading as compacted after a restore, which nothing raises on; real logs here run to 12.
    _archive_run(
        archive,
        run_id="run_c0",
        content_hash="h_c0",
        name="panel",
        measurements=[_compactable_cell(1)],
    )
    _archive_run(
        archive,
        run_id="run_c0",
        content_hash="h_c0",
        name="panel",
        measurements=[_compactable_cell(2)],
    )
    before = archive.load_by_id("run_c0")
    assert before is not None
    assert "compaction" not in before

    report = compact_measurement_archive(built_stores, dataset="reidx", apply=True)
    assert report.runs_touched == 1
    assert report.rows_moved == 2
    assert report.bytes_freed > 0

    mid = archive.load_by_id("run_c0")
    assert mid is not None
    hot = mid["measurements"][0]
    # Moved out...
    assert "hit" not in hot
    assert "scored" not in hot
    assert "reasoning_trace" not in hot["pipeline_data"]
    # ...and the re-grading inputs still present, which is the whole safety of the field choice.
    assert hot["predicted"] == "p"
    assert hot["ground_truth"] == "g"
    assert hot["pipeline_data"]["step_tokens"] == {"llm_only": {"input": 10, "output": 5}}
    assert hot["pipeline_data"]["step_timings"] == {"llm_only": 0.5}
    # The compacted state is LEGIBLE — absent a stamp, "never had a trace" and "lost its trace to
    # compaction" are the same read, and every consumer has to guess which.
    assert mid["compaction"]["rows"] == 2

    restored = restore_measurement_archive(built_stores, dataset="reidx", apply=True)
    assert restored.runs_touched == 1
    assert restored.conflicts == 0
    assert archive.load_by_id("run_c0") == before
    assert "compaction" not in (archive.load_by_id("run_c0") or {})


# 3. Replayed decisions, and forks


def _r(score: float) -> dict:
    # ``objective`` is what θ is fit on. Pinned equal here deliberately — `factories.measurement`
    # diverges the two, and these rows are about the rescore, not about the composite.
    return {
        "query": "q",
        "predicted": "p",
        "ground_truth": "g",
        "fitness": score,
        "objective": score,
    }


def _decisions(*recs: dict[str, Any]) -> list[dict[str, Any]]:
    """The round's ledger decisions, as `scan_ledger_decisions` hands them to the replay."""
    return list(recs)


def _round(**kw: Any) -> RoundResult:
    """A round carrying only what the replayers read; the scoring scalars are inert."""
    return RoundResult.model_validate(
        {
            "label": "C0",
            "accuracy": None,
            "composite_fitness": None,
            "total": 0,
            "improved": False,
            "prompt_fields": {},
            "candidates_scored": 0,
            "selected_labels": [],
            "optimizer_state": optimizer_state().model_dump(),
            **kw,
        }
    )


def test_a_replayer_that_cannot_re_derive_is_not_reported_as_a_match() -> None:
    """A record the replay cannot reproduce is a THIRD state, never agreement.

    The replayers raise on purpose where a decision does not re-derive — a `ROUND_WINNER` with no
    `parent_bias`/`parent_cells` anchor.
    The walker caught every exception and counted it as a match, so the rounds nothing could verify
    were exactly the rounds that reported clean: `--fork-on-divergence` never fired, and the resume
    continued on a winner no rule had reproduced. Silent in the worst direction — a ledger missing
    an anchor replays green from end to end.
    """
    round_data = _round(
        round=0,
        all_candidate_results={"c1": [{**_r(1.0), "sample_id": 0}]},
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
    """The live rule fits on the arm's GRADED cells, and the decision archives those. Silent harm:
    archived as the walk, the cut reads un-reproducible and a fork DISCARDS every round from here on."""
    from types import SimpleNamespace

    from promptpotter.application.optimizers.potter.pobb.checks import EliminationGate
    from promptpotter.application.optimizers.potter.race import PoBBRace
    from promptpotter.domain.search_point import JobSearchPoint
    from tests.factories import measurement, measurements, pobb_knobs

    cycle = SimpleNamespace(
        optimizer=SimpleNamespace(knobs=lambda node: pobb_knobs(epsilon=0.30)),
        config=SimpleNamespace(optimization=SimpleNamespace(elimination_n_min=4)),
        difficulty=SimpleNamespace(ruler=None),
        tracking=SimpleNamespace(
            current_results=measurements([1.0] * 10), current_sp=JobSearchPoint()
        ),
        rounds=[],
        pending_decisions=[],
        session=SimpleNamespace(backend_client=SimpleNamespace(measured_unit="sample")),
    )
    race = PoBBRace(
        SimpleNamespace(cycle=cycle, round_num=3, callbacks=None),
        SimpleNamespace(cells=list(range(10))),
        lambda sp, sample, prior_id: None,
        node="pobb",
    )
    rows = [
        measurement(i, None, error="boom", error_category="transient")
        if i == 2
        else measurement(i, 1.0 if i == 4 else 0.0)
        for i in range(8)
    ]
    signal = race.rule([]).check(rows)
    reading = race.judge(signal, candidate_id="c1", results=rows, labels={"c1": "C3.1"})
    assert reading is not None and reading.context["gate"] == EliminationGate.EPSILON

    [decision] = cycle.pending_decisions
    assert decision.data["candidate_sample_ids"] == ["0", "1", "3", "4", "5", "6", "7"]
    replayed = replay_decisions(
        _round(round=3, all_candidate_results={"c1": rows}),
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


# Every cycle lives inside a campaign.
_CAMPAIGN = "testds__20260101-000000"


def test_inherit_fork_origin_unmodified_inherits_else_rescores(built_stores: Stores) -> None:
    """A no-modification operator fork inherits its branch-point candidate's RECORDED
    accuracy as C0 (no re-score under a nondeterministic backend); an edited prompt
    renders differently and falls back to ``None`` → the caller re-scores."""
    from types import SimpleNamespace

    from promptpotter.application.origin import (
        resolve_origin_opt_search_point,
        try_inherit_fork_origin,
    )
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.search_point import JobSearchPoint, TaskDecomposition

    stores = built_stores
    parent = "cycle_inherit_parent"
    fork = "cycle_inherit_parent_fork_abc123"
    prompt = {"instruction": "do the thing", "persona": "you are precise"}

    stores.campaigns.create(CycleHop(campaign_id=_CAMPAIGN, cycle_id=parent), {})
    stores.campaigns.save_round_file(
        CycleHop(campaign_id=_CAMPAIGN, cycle_id=parent),
        _round(
            round=1,
            label="C1.1",
            accuracy=0.4,
            total=10,
            improved=True,
            candidate_scores=[
                {
                    "candidate_id": "c1",
                    "label": "C1.1",
                    "run_id": None,
                    "prompt_fields": prompt,
                    "accuracy": 0.2,
                    "composite_fitness": 0.2,
                    "total": 10,
                    "outcome": "measured",
                },
            ],
        ),
    )
    stores.campaigns.create(
        CycleHop(campaign_id=_CAMPAIGN, cycle_id=fork),
        {
            "parent_cycle_id": parent,
            "fork": {
                "trigger": "operator_steered",
                "from_round": 1,
                "from_candidate_id": "c1",
            },
        },
    )

    session = SimpleNamespace(
        store=stores,
        campaign_id=_CAMPAIGN,
        state=SimpleNamespace(cycle_id=fork),
        hop=CycleHop(campaign_id=_CAMPAIGN, cycle_id=fork),
        experiment_extract={},
        dataset_config_dir=None,
        pipeline_schema=PipelineSchema(),
    )

    # Resolve the origin OSP exactly as ``establish_campaign_origin`` does (fork-seed wins).
    unmodified_seed = CycleSeed(origin_prompt_fields=dict(prompt), origin_source="fork_seed")
    unmodified_osp = resolve_origin_opt_search_point({}, seed=unmodified_seed)
    inherited = try_inherit_fork_origin(
        session,  # type: ignore[arg-type]
        unmodified_seed,
        resolved_origin=unmodified_osp,
        sp=JobSearchPoint(),
        framing=TaskDecomposition(),
    )
    assert inherited is not None
    # The branch point's OWN measurement, carried whole — not a re-rolled number, and not
    # an accuracy with the rest of the report re-derived around it.
    assert inherited.report.accuracy == 0.2
    # C0 carries the OSP object, so the inherited origin keeps the lineage the seed stamped.
    assert isinstance(inherited.resolved_origin, OptSearchPoint)
    assert inherited.resolved_origin.lineage.source == "origin"

    edited_seed = CycleSeed(
        origin_prompt_fields={**prompt, "instruction": "do it differently"},
        origin_source="fork_seed",
    )
    edited = try_inherit_fork_origin(
        session,  # type: ignore[arg-type]
        edited_seed,
        resolved_origin=resolve_origin_opt_search_point({}, seed=edited_seed),
        sp=JobSearchPoint(),
        framing=TaskDecomposition(),
    )
    assert edited is None


def test_an_applied_scenario_forks_at_its_round_and_carries_the_criterion(
    built_stores: Stores,
) -> None:
    """ "Apply this criterion from round N" must lift rounds 0..N-1 AND land the criterion.

    Both halves are silent when wrong. A fork that took the OFFSHOOT branch would restart round
    numbering at 1 and re-measure an origin the operator meant to keep — no error, just a branch
    answering a different question. And a `scoring` override dropped on the way in leaves the fork
    running under the parent's criterion, which is the one thing the operator pressed the button to
    change: every round it then produces is evidence for a formula that was never applied.

    `scoring` sits on `CampaignConfig` itself rather than under `optimization`, so it needs its own
    bucket in `apply_config_overrides` — folded into the nested copy beside the run limits it
    would vanish with every gate green.
    """
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
    store.create(parent, {"parent_session_id": "sess-apply"})
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
    # Its author is the identity the stores were built for; no caller names one.
    index = store.load(child)
    assert index is not None
    assert index["fork"]["issued_by"] == str(built_stores.identity.user_id)

    # The REBASE branch: the record says which act this was, and it is the one that lifts rounds.
    assert index["fork"]["trigger"] == "operator_rewind"
    assert index["forked_from_round"] == 2

    # …and the criterion survives the ledger round-trip into the fork's effective config.
    seed = store.read_cycle_seed(child)
    assert seed is not None
    base = CampaignConfig(optimization=OptimizationConfig(degradation_threshold=0.05))
    applied = apply_config_overrides(base, seed.config_overrides)
    assert applied.scoring == criterion
    assert base.scoring is None  # the parent's frozen config is never mutated
