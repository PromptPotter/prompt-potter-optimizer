"""Measurement / config integrity — the other silent-harm class.

These guard the inputs to a measurement being silently *correct*: the cache key
that distinguishes two configs (a collision serves the wrong cached scores), the
nested-only config shape (a flat map is silently misread), the scored prompt
staying free of optimizer-only state (an L3 plan leaking in means you score a
prompt you never meant to), and dataset-scoped cache reuse (a bleed serves one
dataset's results under another). Every failure here is silent — the run
completes, no error, wrong numbers. Resurrected after a coarse suite cut swept
them out with the loud-breakage shape/contract bulk.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pydantic
import pytest

from promptpotter.domain.measurement_provenance import grade_run
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive
from promptpotter.shared.hashing import content_hash


def test_content_hash_distinguishes_pipeline_params() -> None:
    """The measurement key must change when the config does — else the archive
    serves one config's cached scores for a different one."""
    dataset = [Sample(id=1, query="q", ground_truth="a")]
    sp_a = JobSearchPoint(pipeline_params={"steps": ["llm_ranking"]})
    sp_b = JobSearchPoint(pipeline_params={"steps": ["fuzzy_matching"]})
    assert sp_a.content_hash(dataset) != sp_b.content_hash(dataset)
    assert sp_a.content_hash(dataset) == content_hash(sp_a.render(), dataset, sp_a.pipeline_params)


def test_pipeline_params_rejects_flat_param_map() -> None:
    """``pipeline_params`` is nested-by-node ⇒ a flat ``{param: value}`` map is
    rejected, never silently misread as a node-keyed config."""
    JobSearchPoint(pipeline_params={"llm_only": {"model": "x", "temperature": 0.1}})
    JobSearchPoint(pipeline_params={"steps": ["llm_ranking"], "llm_ranking": {"prompt": "x"}})
    JobSearchPoint(pipeline_params={})
    JobSearchPoint(pipeline_params=None)
    with pytest.raises(pydantic.ValidationError):
        JobSearchPoint(pipeline_params={"model": "x", "temperature": 0.1})


def test_render_does_not_leak_l3_plan_into_target_prompt() -> None:
    """L3's plan reaches L1/L2/L3 prompts via ``_r_plan`` only — never via
    ``render``. A leak would silently score a plan-contaminated target prompt."""
    sentinel = "REVISED_OPTIMIZATION_FRAMEWORK_PLAN_SENTINEL"
    osp = OptSearchPoint(persona="Expert", instruction="Solve.", plan=sentinel)
    assert sentinel not in osp.render()


def _seed_run(archive: MeasurementArchive, *, run_id: str, dataset_name: str, hit: bool) -> None:
    """Minimal ``MeasurementArchive.save`` envelope — one sample whose query text
    is dataset-tagged, so a cross-dataset bleed is detectable by query overlap."""
    archive.save(
        "bk",
        run_id,
        {
            "run_id": run_id,
            "name": run_id,
            "content_hash": f"hash_{run_id}",
            "prompt_fields_id": "pf_x",
            "item_count": 1,
            "scores": {"accuracy": 1.0 if hit else 0.0, "total": 1},
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
                    "pipeline_data": {"terminated_at": "llm_only"},
                }
            ],
            "dataset_name": dataset_name,
        },
    )


@dataclass
class _StubNode:
    name: str
    is_llm: bool


@dataclass
class _StubSchema:
    nodes: list[_StubNode]


def test_provenance_grade_separates_deliberate_from_connector() -> None:
    """The grade that gates the cross-cycle digest must not invert: a deliberate
    LLM-path run grades A (kept), an incidental connector short-circuit grades C
    (dropped). An inversion silently feeds the optimizer connector-retrieval noise
    instead of its real explored datapoints — no error, just a biased digest."""
    schema = _StubSchema([_StubNode("token_matching", False), _StubNode("llm_only", True)])
    llm_batch = [{"pipeline_data": {"terminated_at": "llm_only"}}]
    connector_batch = [{"pipeline_data": {"terminated_at": "token_matching"}}]
    assert grade_run("optimization_loop", llm_batch, schema).grade == "A"
    assert grade_run("origin", llm_batch, schema).grade == "A"
    assert grade_run("", connector_batch, schema).grade == "C"
    # one signal but not both → middling, never confused with a clean A
    assert grade_run("origin", connector_batch, schema).grade == "B"
    assert grade_run("", llm_batch, schema).grade == "B"


def _seed_graded(
    archive: MeasurementArchive, *, run_id: str, grade: str, terminated_at: str
) -> None:
    """Save one run carrying a provenance grade and a single dataset-tagged sample."""
    provenance: dict[str, Any] = {"grade": grade, "deliberate_source": grade != "C"}
    archive.save(
        "bk",
        run_id,
        {
            "run_id": run_id,
            "name": run_id,
            "content_hash": f"hash_{run_id}",
            "prompt_fields_id": "pf_x",
            "item_count": 1,
            "scores": {"accuracy": 1.0, "total": 1},
            "node_configs": [("llm_only", {"model": "X"})],
            "pipeline_params": {"llm_only": {"model": "X"}},
            "provenance": provenance,
            "created_at": "2026-05-19T00:00:00Z",
            "measurements": [
                {
                    "sample_id": 7,
                    "query": f"q_{run_id}",
                    "ground_truth": "g",
                    "predicted": "p",
                    "hit": True,
                    "fitness": 1.0,
                    "pipeline_data": {"terminated_at": terminated_at},
                }
            ],
            "dataset_name": "aime",
        },
    )


def test_reusable_results_min_grade_drops_connector_runs(tmp_path: Path) -> None:
    """``min_grade`` lets a clean-substrate read reuse only deliberately-explored
    measurements: a grade-C connector run is excluded, so its stale sample is not
    silently served as if it were a real evaluation. The default (no floor) keeps
    every run, so ordinary scoring caching is unchanged."""
    archive = MeasurementArchive(tmp_path)
    _seed_graded(archive, run_id="clean", grade="A", terminated_at="llm_only")
    _seed_graded(archive, run_id="connector", grade="C", terminated_at="token_matching")
    node_configs = [("llm_only", {"model": "X"})]

    everything = archive.load_reusable_results("bk", node_configs, dataset_name="aime")
    assert set(everything) == {"q_clean", "q_connector"}

    clean_only = archive.load_reusable_results(
        "bk", node_configs, dataset_name="aime", min_grade="A"
    )
    assert set(clean_only) == {"q_clean"}


def test_full_chain_rows_never_replay_on_prefix_match(tmp_path: Path) -> None:
    """A sample whose outcome consumed the FULL node chain (``terminated_at`` =
    last node — the L4 inner-recursion stamp) must not replay for a query that
    differs at a later node. The buggy stamp (``l1_critique``, a mid-chain node)
    let a candidate editing ``l2_context``/``l3_plan`` silently replay the
    origin's rows — a fake score with no error and no symptom (run b786e9 C1.3).
    A genuine mid-chain short-circuit still replays: that reuse is correct."""
    archive = MeasurementArchive(tmp_path)
    chain = ["l1_generate", "l1_critique", "l2_context", "l3_plan"]

    def _seed_chain(run_id: str, terminated_at: str) -> None:
        archive.save(
            "bk",
            run_id,
            {
                "run_id": run_id,
                "name": run_id,
                "content_hash": f"hash_{run_id}",
                "prompt_fields_id": "pf_x",
                "item_count": 1,
                "scores": {"accuracy": 1.0, "total": 1},
                "node_configs": [(n, {}) for n in chain],
                "pipeline_params": {},
                "created_at": "2026-07-03T00:00:00Z",
                "measurements": [
                    {
                        "sample_id": 1,
                        "query": f"q_{run_id}",
                        "ground_truth": "g",
                        "predicted": "p",
                        "hit": True,
                        "fitness": 1.0,
                        "pipeline_data": {"terminated_at": terminated_at},
                    }
                ],
                "dataset_name": "promptpotter-self",
            },
        )

    _seed_chain("full_chain", terminated_at="l3_plan")
    _seed_chain("short_circuit", terminated_at="l1_critique")

    # Query differs at l2_context → prefix match of length 2.
    query_configs: list[tuple[str, dict[str, Any]]] = [
        ("l1_generate", {}),
        ("l1_critique", {}),
        ("l2_context", {"layout": {"problem_description": ["critique"]}}),
        ("l3_plan", {}),
    ]
    cache = archive.load_reusable_results("bk", query_configs, dataset_name="promptpotter-self")
    assert "q_full_chain" not in cache, (
        "full-chain row replayed across a later-node config change — fake measurement"
    )
    assert "q_short_circuit" in cache, (
        "a genuine mid-chain short-circuit inside the trusted prefix should still reuse"
    )


def test_layout_only_override_moves_optimizer_prompt_hash() -> None:
    """A layout-only L4 edit changes which evidence a node sees, so it must move
    that node's ``optimizer_prompt_hash`` — otherwise cross-cycle audits joining
    on the hash silently pool layout-differing inner cycles (run b786e9: C1.3's
    inner campaigns stamped the origin's hash). Prose-hash behavior is untouched:
    no override → identical hashes."""
    from promptpotter.application.optimization.dispatch.llm_call import (
        compute_optimizer_prompt_hashes,
        set_optimizer_prompt_overrides,
    )

    try:
        set_optimizer_prompt_overrides(None)
        baseline = compute_optimizer_prompt_hashes()
        assert compute_optimizer_prompt_hashes() == baseline

        # Valid layout edit (keeps the mandatory `diagnostics`) on one node.
        set_optimizer_prompt_overrides(
            {"l1_critique": {"layout": {"problem_description": ["diagnostics", "axis_memory"]}}}
        )
        edited = compute_optimizer_prompt_hashes()
        assert edited["l1_critique"] != baseline["l1_critique"], (
            "layout-only override left the node hash unchanged — audits would pool "
            "layout-differing cycles"
        )
        assert {k: v for k, v in edited.items() if k != "l1_critique"} == {
            k: v for k, v in baseline.items() if k != "l1_critique"
        }, "layout edit on one node must not move other nodes' hashes"
    finally:
        set_optimizer_prompt_overrides(None)


def test_inner_narrative_carries_evidence_within_budget() -> None:
    """The L4 outer loop's only raw evidence is the inner-campaign digest riding
    ``reasoning_trace``. If it silently drops the critique quote / winner edit /
    deltas, or overruns the panel cap (1200c head-keep would clip the LATEST
    rounds), the outer loop is evidence-starved again with no error and no
    symptom (run b786e9: transcripts degenerated to identity tokens)."""
    from promptpotter.application.runner.inner_recursion import (
        _inner_narrative,
        _InnerTaskSpec,
    )
    from promptpotter.domain.results import CycleResult, RoundResult, ScoredCandidate

    spec = _InnerTaskSpec(
        inner_dataset="justlogic", seed=3, n_samples=24, n_rounds=2, target=0.6, n_variants=2
    )

    def _round(n: int, desc: str, fix: str, highlights: list[str]) -> RoundResult:
        return RoundResult(
            round=n,
            label=f"C{n}.1" if n else "C0",
            accuracy=0.458,
            hits=11,
            total=24,
            improved=False,
            prompt_fields={},
            candidates_scored=2,
            candidate_scores=(
                []
                if n == 0
                else [
                    ScoredCandidate(
                        candidate_id=f"c{n}",
                        label=f"C{n}.1",
                        changes_description=desc,
                        accuracy=0.5,
                        composite_fitness=0.5,
                        hits=12,
                        total=24,
                        matched_origin_accuracy=0.458,
                        theta=0.31,
                        theta_se=0.42,
                    )
                ]
            ),
            critique={
                "priority_fix": fix,
                "suggested_axes": [],
                "failure_highlights": highlights,
            },
        )

    def _cycle(rounds: list[RoundResult], levels: list[float]) -> CycleResult:
        return CycleResult(
            rounds=rounds,
            n_rounds=len(levels),
            best_accuracy=0.5,
            best_round=1,
            origin_accuracy=0.458,
            origin_level=0.458,
            round_discovered_levels=levels,
            winner_prompt_fields={},
            stop_reason="MAX_ROUNDS",
            started_at="t0",
            finished_at="t1",
        )

    rounds = [
        _round(0, "", "add a formal entailment verification step", ["#132: hedged to Uncertain"]),
        _round(1, "added premise-tracking sub-step", "tighten the label format", []),
        _round(2, "set temperature 0.0", "", []),
    ]
    digest = _inner_narrative(_cycle(rounds, [0.472, 0.5]), spec)
    assert len(digest) <= 1150
    assert "formal entailment verification" in digest, "prior-round steer missing"
    assert "premise-tracking" in digest, "winner edit missing"
    assert "#132" in digest, "verbatim failure highlight missing"
    assert "D+0.042" in digest, "best-discovered delta missing"

    # Deep run: budget holds by eliding EARLIEST rounds, never the latest.
    deep = [_round(0, "", "steer zero " + "x" * 120, ["hl " + "y" * 180])] + [
        _round(n, f"edit {n} " + "z" * 90, f"fix {n} " + "w" * 110, []) for n in range(1, 9)
    ]
    digest = _inner_narrative(_cycle(deep, [0.46 + 0.005 * n for n in range(8)]), spec)
    assert len(digest) <= 1150
    assert "R8 " in digest, "latest round clipped — head-keep would starve the trajectory tail"
    assert "[earlier rounds elided]" in digest

    # No rounds at all → headline only, never an exception.
    digest = _inner_narrative(_cycle([], []), spec)
    assert digest.startswith("INNER justlogic seed-3")


def test_evidence_channel_clips_are_visible_and_tail_preserving(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Three silent evidence corruptions from run b786e9: (1) `priority_fix` was
    hard-cut mid-quote with no marker — the clipped steer read as a complete
    instruction and candidates faithfully implemented the fragment; (2) the
    reasoning-trace head-keep dropped the CONCLUSION — the one step the critique
    is ordered to quote; (3) an over-cap `task_context` field hard-sliced mid-word
    at the render site. All three produce wrong prompt content with no error."""
    from promptpotter.application.optimization.dispatch.hub import (
        CycleSlice,
        InjectionBundle,
        RoundDigest,
    )
    from promptpotter.application.optimization.dispatch.hub.injections.layer_state import (
        _r_task_context,
    )
    from promptpotter.application.optimization.dispatch.hub.injections.panels import (
        _edges_at_line,
    )
    from promptpotter.application.optimization.dispatch.schemas import L1CritiqueOutput
    from promptpotter.domain.opt_search_point import L2L3Memory
    from promptpotter.domain.round_diagnostics import RoundDiagnostics
    from promptpotter.domain.search_point import TaskDecomposition

    # (1) over-cap priority_fix clips at a word boundary WITH a visible marker.
    long_fix = "thinking_style: add verification - addresses " + "pattern word " * 40
    out = L1CritiqueOutput(priority_fix=long_fix)
    assert len(out.priority_fix) <= 320
    assert out.priority_fix.endswith("…"), "silent truncation is the defect being fixed"
    assert not out.priority_fix.removesuffix("…").endswith(" pa"), "mid-word cut"
    # Under-cap passes through untouched.
    assert L1CritiqueOutput(priority_fix="short steer").priority_fix == "short steer"

    # (2) head+tail keep: the trace's final (decisive) line survives the clip.
    trace = "\n".join(f"step {i}: infer premise {i}" for i in range(60)) + "\nCONCLUSION: FALSE"
    clipped = _edges_at_line(trace, 400)
    assert len(clipped) <= 430
    assert "CONCLUSION: FALSE" in clipped, "head-keep starved the quotable wrong step"
    assert clipped.startswith("step 0"), "head context lost"
    assert "[…middle elided]" in clipped
    # Under-cap text passes through whole.
    assert _edges_at_line("a\nb", 400) == "a\nb"

    # (3) over-cap task_context field clips at a word boundary WITH a visible marker.
    long_field = "data_characteristics: " + "pattern word " * 30
    bundle = InjectionBundle(
        opt_sp=OptSearchPoint(
            memory=L2L3Memory(task_context=TaskDecomposition(key_challenges=long_field))
        ),
        pipeline_schema=None,
        cycle_slice=CycleSlice(
            round_num=1,
            current_accuracy=0.5,
            best_accuracy=0.5,
            best_round=0,
            l1_stall_count=0,
            l2_round=0,
            l2_stall_count=0,
            l3_round=0,
            l3_stall_count=0,
            exploration_budget="tight",
        ),
        digest=RoundDigest(diagnostics=RoundDiagnostics(n_valid=0, samples=[]), critique=None),
        axes=None,
    )
    with caplog.at_level(logging.WARNING):
        rendered = _r_task_context(bundle)
    assert "…" in rendered, "silent truncation is the defect being fixed"
    clipped_field = rendered.split("key_challenges: ", 1)[1].split("\n", 1)[0]
    clipped_prefix = clipped_field.removesuffix("…")
    assert long_field.startswith(clipped_prefix)
    next_char = long_field[len(clipped_prefix) : len(clipped_prefix) + 1]
    assert next_char in (" ", ""), "mid-word cut"
    assert any("over the" in r.getMessage() and "cap" in r.getMessage() for r in caplog.records), (
        "injection_budget_overrun warning must still fire on truncation"
    )


def test_hit_cache_respects_dataset(tmp_path: Path) -> None:
    """``load_reusable_results`` scopes by dataset — identical node-configs and a
    colliding sample_id across datasets must NOT serve one dataset's cached
    results under the other."""
    archive = MeasurementArchive(tmp_path)
    _seed_run(archive, run_id="aime_cached", dataset_name="aime", hit=True)
    _seed_run(archive, run_id="just_fresh", dataset_name="justlogic", hit=False)

    node_configs = [("llm_only", {"model": "X"})]
    aime_cache = archive.load_reusable_results("bk", node_configs, dataset_name="aime")
    just_cache = archive.load_reusable_results("bk", node_configs, dataset_name="justlogic")

    aime_queries = set(aime_cache.keys())
    just_queries = set(just_cache.keys())
    assert aime_queries.isdisjoint(just_queries)
    assert any("aime" in q for q in aime_queries)
    assert any("justlogic" in q for q in just_queries)


def test_noop_probe_survives_invariant_nuke() -> None:
    """The NO-OP probe is a deliberate origin-identical arm (noise-floor measurement).
    ``detect_invariants`` flagging it ``no_op_variant`` would route it through the
    synthetic-0 skip path — silently reporting a noise floor of exactly 0 instead of
    measuring one. The probe must pass unflagged and stay out of the yield stats."""
    from promptpotter.application.optimization.l1.generate import noop_probe_proposal
    from promptpotter.application.optimization.validators.l1_strict import detect_invariants
    from promptpotter.domain.opt_search_point import OptSearchPoint

    parent = OptSearchPoint(persona="Expert", instruction="Solve.")
    probe = noop_probe_proposal(parent)
    assert probe.is_probe
    assert probe.osp.prompt_field_dict() == parent.prompt_field_dict()

    stats = detect_invariants([probe], parent)
    assert probe.osp.memory.wounds.validation_failures == []
    assert stats.l1_n_no_op == 0
    assert stats.l1_yield == 1.0
