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

import asyncio
import dataclasses
import io
import json
import logging
import re
import subprocess
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from unittest import mock

import pydantic
import pytest
import yaml

from promptpotter.application.scoring import query_loop
from promptpotter.application.scoring.search_point_scorer import (
    _assert_measured_content_matches,
)
from promptpotter.domain.escalation_signals import EscalationSignal, EscalationTarget
from promptpotter.domain.measurement_provenance import grade_run
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import PipelineSchema, PipelineView
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import QueryMeasurement
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.infrastructure.store.io import _YamlDumper, read_yaml, write_yaml
from promptpotter.infrastructure.store.layout import validate_dataset_name
from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import DatasetIdentityError
from promptpotter.shared.hashing import content_hash
from tests.factories import round_result


def _pipeline_schema(dataset: str) -> PipelineSchema:
    """The committed `datasets/{dataset}/pipeline.yaml`, parsed. `promptpotter-self` is the
    outer L4 campaign (it declares the schema levers); `justlogic-d234` is a plain inner one."""
    path = Path(__file__).resolve().parents[1] / "datasets" / dataset / "pipeline.yaml"
    return parse_pipeline_response(yaml.safe_load(path.read_text(encoding="utf-8")))


def _emittable_l1_params(schema: dict[str, Any], node: str = "l1_generate") -> set[str]:
    """The param keys an L1 variant may emit for *node* under *schema*."""
    variants = schema["properties"]["variants"]["items"]["properties"]
    node_props = variants["pipeline_params_override"]["properties"].get(node, {})
    return set(node_props.get("properties", {}))


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
    with pytest.raises(pydantic.ValidationError):
        JobSearchPoint(pipeline_params={"model": "x", "temperature": 0.1})


def test_render_does_not_leak_l3_plan_into_target_prompt() -> None:
    """L3's plan reaches L1/L2/L3 prompts via ``_r_plan`` only — never via
    ``render``. A leak would silently score a plan-contaminated target prompt."""
    sentinel = "REVISED_OPTIMIZATION_FRAMEWORK_PLAN_SENTINEL"
    opt_sp = OptSearchPoint(persona="Expert", instruction="Solve.", plan=sentinel)
    assert sentinel not in opt_sp.render()


def test_reasoning_model_below_token_floor_is_blocked_not_run() -> None:
    """A reasoning model pinned below its ``ModelProfile`` token floor must be caught at
    preflight, not after the money is spent. A gate that silently fails to fire lets the
    inner optimizer spend its whole budget reasoning and emit zero content
    (``reasoning_budget_exhausted``) — a paid-for, loop-stalling failure with no wrong-answer
    symptom, so the gate NOT firing is the silent harm here."""
    from promptpotter.application.preflight import check_model_reasoning_floors
    from promptpotter.infrastructure.llm.registry import model_profile

    prof = model_profile("deepseek/deepseek-v4-flash:nitro")
    assert prof is not None and prof.is_reasoning and prof.min_max_tokens >= 8000
    floor = prof.min_max_tokens

    # Below-floor reasoning model with an EXPLICIT cap → blocked (the l1_critique bug).
    below = check_model_reasoning_floors(
        [
            (
                "l1_critique",
                {"model": "deepseek/deepseek-v4-flash:nitro", "max_tokens": floor - 1},
            )
        ]
    )
    assert len(below) == 1 and "l1_critique" in below[0]

    # None of these is a violation: at/above floor, absent cap (provider ceiling — the
    # sanctioned default), non-reasoning suffix-normalized, and an unprofiled model.
    clean = check_model_reasoning_floors(
        [
            ("at_floor", {"model": "deepseek/deepseek-v4-flash:nitro", "max_tokens": floor}),
            ("absent_cap", {"model": "deepseek/deepseek-v4-flash:nitro"}),
            ("unprofiled", {"model": "some/unknown-model", "max_tokens": 10}),
        ]
    )
    assert clean == []


def test_earned_blocks_gate_on_credible_lift_and_task_fit() -> None:
    """The earned-block library must never feed the optimizer a noise-win or a cross-task block
    — both are wrong-content-forward with no error. Built from real ``ScoredCandidate.model_dump()``
    so it rides the SAME serialization a round file carries (the earlier fabricated
    ``prompt_fields_override`` shape the model never emits made this test green while the feature
    mined nothing): the changed reusable field is the candidate's RESOLVED ``prompt_fields`` diffed
    against the round's parent ``prompt_fields``, kept only when ``mean_fitness_ci_lo`` clears the
    matched parent, keyed by the run's answer-space signature so a logic block never reaches a
    ranking run."""
    from collections import defaultdict

    from promptpotter.application.intelligence.earned_blocks import _accumulate
    from promptpotter.domain.results import ScoredCandidate

    parent = {"persona": "You answer.", "instruction": "Do the task."}

    def scored(label: str, fields: dict[str, str], comp: float, ci_lo: float) -> dict[str, Any]:
        return ScoredCandidate(
            candidate_id=label,
            label=label,
            accuracy=comp,
            composite_fitness=comp,
            total=10,
            prompt_fields={**parent, **fields},  # RESOLVED fields, parent + this candidate's change
            matched_parent_composite=0.50,
            mean_fitness_ci_lo=ci_lo,
            mean_fitness_ci_hi=ci_lo + 0.1,
        ).model_dump()

    logic_run = {
        "prompt_fields": parent,  # the round's parent — what each candidate is diffed against
        "all_candidate_results": {
            "c1": [
                {"ground_truth": "TRUE"},
                {"ground_truth": "FALSE"},
                {"ground_truth": "Uncertain"},
            ]
        },
        "candidate_scores": [
            # credible: ci_lo 0.62 clears origin 0.50, changed a reusable field → kept
            scored("c-good", {"persona": "Be a careful logician."}, 0.70, 0.62),
            # noise win: composite up but ci_lo 0.48 below origin 0.50 → dropped
            scored("c-noise", {"persona": "Guess fast."}, 0.55, 0.48),
            # a long, task-specific field is never reusable material → dropped even if credible
            scored("c-long", {"instruction": "step 1 ..."}, 0.70, 0.62),
        ],
    }
    acc: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    _accumulate(logic_run, acc)
    fit = "FALSE|TRUE|Uncertain"
    assert (fit, "persona", "Be a careful logician.") in acc
    assert (fit, "persona", "Guess fast.") not in acc
    assert not any(field == "instruction" for _, field, _ in acc)


def test_earned_block_mining_is_blind_inside_an_instrument() -> None:
    """An instrument must not mine the campaign tree — its siblings ARE the tree.

    The earned-block library is cross-run MEMORY, which ``shared/instrument.py`` names as
    contamination for an instrument, but it reads campaign dirs rather than the archive, so
    the evidence epoch that hides memory from every other such read cannot reach it. Inside
    an L4 sandbox the only campaigns on disk are the sibling cells of the same outer run,
    which accumulate as it proceeds — so ungated, cell #1 renders the static fallback and
    cell #39 renders blocks mined from 38 finished siblings. Same cell, different prompt,
    ordered by how often the instrument has been used. Measured on a banked sandbox before
    the gate landed.

    The store here is a tripwire, so removing the gate IS the fault injection: the walk it
    guards is the first thing the miner does.
    """
    import contextvars

    from promptpotter.application.intelligence.earned_blocks import mine_earned_blocks
    from promptpotter.shared.instrument import enter_instrument_mode

    class _Tripwire:
        def iter_campaign_dirs(self) -> list[Path]:
            raise AssertionError("mined the campaign tree")

    class _Store:
        campaigns = _Tripwire()

    store: Any = _Store()

    with pytest.raises(AssertionError, match="mined the campaign tree"):
        mine_earned_blocks(store)

    def _inside_instrument() -> dict[str, Any]:
        enter_instrument_mode(evidence_epoch=frozenset(), optimizer_clamp=None, ruler=None)
        return mine_earned_blocks(store)

    # Own context, exactly as a real spawn binds it — and so the mode cannot leak sideways.
    assert contextvars.copy_context().run(_inside_instrument) == {}


def _archive(archive: MeasurementArchive, run_id: str, data: dict[str, Any]) -> None:
    """Seed one complete run — the whole measurement set is what is new."""
    archive.append_run(run_id, data, data["measurements"])


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
                    "pipeline_data": {"terminal_node": "llm_only"},
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
    llm_batch = [{"pipeline_data": {"terminal_node": "llm_only"}}]
    connector_batch = [{"pipeline_data": {"terminal_node": "token_matching"}}]
    assert grade_run("optimization_loop", llm_batch, schema).grade == "A"
    assert grade_run("origin", llm_batch, schema).grade == "A"
    assert grade_run("", connector_batch, schema).grade == "C"
    # one signal but not both → middling, never confused with a clean A
    assert grade_run("origin", connector_batch, schema).grade == "B"
    assert grade_run("", llm_batch, schema).grade == "B"
    # A babysat run (a human edited an engine-locked value, ADR-0005) is forced to
    # C even on the otherwise-clean-A path — else a tainted point reused as clean
    # would silently bias the digest/L4 the same way connector noise does.
    prov = grade_run("optimization_loop", llm_batch, schema, human_intervened=True)
    assert prov.grade == "C" and prov.human_intervened is True


def _seed_graded(
    archive: MeasurementArchive, *, run_id: str, grade: str, terminal_node: str, sample_id: int
) -> None:
    """Save one run carrying a provenance grade and a single sample. The two runs measure
    DIFFERENT samples — the cache keys on ``sample_id``, so they need distinct ones to
    coexist (they used to be told apart by query text alone, both stamped sample 7)."""
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
            "scores": {"accuracy": 1.0, "total": 1},
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


def test_reusable_results_min_grade_drops_connector_runs(tmp_path: Path) -> None:
    """``min_grade`` lets a clean-substrate read reuse only deliberately-explored
    measurements: a grade-C connector run is excluded, so its stale sample is not
    silently served as if it were a real evaluation. The default (no floor) keeps
    every run, so ordinary scoring caching is unchanged."""
    archive = MeasurementArchive(tmp_path)
    _seed_graded(archive, run_id="clean", grade="A", terminal_node="llm_only", sample_id=7)
    _seed_graded(
        archive, run_id="connector", grade="C", terminal_node="token_matching", sample_id=8
    )
    node_configs = [("llm_only", {"model": "X"})]

    everything = archive.load_reusable_results(node_configs, dataset_name="aime")
    assert set(everything) == {7, 8}

    clean_only = archive.load_reusable_results(node_configs, dataset_name="aime", min_grade="A")
    assert set(clean_only) == {7}
    assert clean_only[7]["query"] == "q_clean"


def test_reuse_serves_the_best_match_not_the_last_one_walked(tmp_path: Path) -> None:
    """``find_by_node_configs`` sorts best-first, and a walk that assigns unconditionally runs
    that order to its END — serving the row matching the FEWEST nodes. Nothing to see: both rows
    are real measurements, so the served answer is merely the wrong one of two."""
    archive = MeasurementArchive(tmp_path)
    chain = [("a", {"k": 1}), ("b", {"k": 2}), ("c", {"k": 3})]

    def _seed(run_id: str, configs: list[tuple[str, dict[str, Any]]], predicted: str) -> None:
        _archive(
            archive,
            run_id,
            {
                "run_id": run_id,
                "name": run_id,
                "content_hash": f"hash_{run_id}",
                "prompt_fields_id": "pf",
                "item_count": 1,
                "scores": {"accuracy": 1.0, "total": 1},
                "node_configs": configs,
                "pipeline_params": {},
                "created_at": "2026-07-03T00:00:00Z",
                "measurements": [
                    {
                        "sample_id": 1,
                        "query": "q",
                        "ground_truth": "g",
                        "predicted": predicted,
                        "hit": True,
                        "fitness": 1.0,
                        # Inside the trusted prefix either way, so the terminal-node rule below
                        # admits both rows and the ONLY thing separating them is match length.
                        "pipeline_data": {"terminal_node": "a"},
                    }
                ],
                "dataset_name": "d",
            },
        )

    # `narrow` matches one node, `wide` all three. Seeded narrow-first so a stable sort alone
    # cannot produce the right answer — the walk has to actually prefer the longer match.
    _seed("narrow", [("a", {"k": 1}), ("b", {"k": 99})], "from_narrow")
    _seed("wide", chain, "from_wide")

    cache = archive.load_reusable_results(chain, dataset_name="d")
    assert cache[1]["predicted"] == "from_wide", (
        "served the shorter prefix match — the sort's whole purpose is thrown away"
    )


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
    # Both runs measured the SAME cell (sample 1) — the cache is keyed by sample_id, so
    # the question is which row wins it, not whether two text-distinct keys coexist.
    cache = archive.load_reusable_results(query_configs, dataset_name="promptpotter-self")
    assert cache[1]["query"] == "q_short_circuit", (
        "a genuine mid-chain short-circuit inside the trusted prefix should still reuse"
    )
    assert cache[1]["pipeline_data"]["terminal_node"] == "l1_critique"
    assert all(r["query"] != "q_full_chain" for r in cache.values()), (
        "full-chain row replayed across a later-node config change — fake measurement"
    )


def test_layout_only_override_moves_optimizer_prompt_hash() -> None:
    """A layout-only L4 edit changes which evidence a node sees, so it must move
    that node's ``optimizer_prompt_hash`` — otherwise cross-cycle audits joining
    on the hash silently pool layout-differing inner cycles (run b786e9: C1.3's
    inner campaigns stamped the origin's hash). Prose-hash behavior is untouched:
    no override → identical hashes."""
    from promptpotter.application.optimization.dispatch.llm_call.prompts import (
        compute_optimizer_prompt_hashes,
        set_optimizer_prompt_overrides,
    )

    try:
        set_optimizer_prompt_overrides(None)
        baseline = compute_optimizer_prompt_hashes()
        assert compute_optimizer_prompt_hashes() == baseline

        # Valid layout edit (`diagnostics` stays placed) on one node.
        set_optimizer_prompt_overrides(
            {"l1_critique": {"layout": {"axis_memory": "thinking_style"}}}
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
    from promptpotter.application.runner.inner.spawn import _inner_narrative
    from promptpotter.application.runner.inner.tasks import InnerTaskSpec
    from promptpotter.domain.results import CycleResult, RoundResult, ScoredCandidate

    spec = InnerTaskSpec(
        inner_dataset="justlogic-d234", seed=3, n_samples=24, n_rounds=2, n_variants=2
    )

    def _round(n: int, desc: str, fix: str, highlights: list[str]) -> RoundResult:
        return RoundResult(
            round=n,
            label=f"C{n}.1" if n else "C0",
            accuracy=0.458,
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
                        total=24,
                        matched_parent_accuracy=0.458,
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
            n_l1_rounds=len(levels),
            best_accuracy=0.5,
            best_round=1,
            origin_accuracy=0.458,
            origin_level=0.458,
            round_parent_levels=levels,
            winner_prompt_fields={},
            stop_reason="max_rounds",
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


def test_evidence_channel_clips_are_visible_and_tail_preserving(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Three silent evidence corruptions from run b786e9: (1) `priority_fix` was
    hard-cut mid-quote with no marker — the clipped steer read as a complete
    instruction and candidates faithfully implemented the fragment; (2) the
    reasoning-trace head-keep dropped the CONCLUSION — the one step the critique
    is ordered to quote; (3) an over-cap `task_context` field hard-sliced mid-word
    at the render site. All three produce wrong prompt content with no error."""
    from promptpotter.application.optimization.dispatch.bundle import (
        CycleSlice,
        InjectionBundle,
        RoundDigest,
    )
    from promptpotter.application.optimization.dispatch.injections.layer_state import (
        _r_task_context,
    )
    from promptpotter.application.optimization.dispatch.injections.panels import (
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

    # (3) task_context is AUTHORED text, so it is rendered verbatim — never clipped.
    # The two channels above are DERIVED (reasoning traces, sample rows): they rank their
    # content and can say what they dropped. An authored field has no rankable rows and no
    # principled cut, so a renderer that clips it is guessing which half the operator meant.
    # It guessed wrong for 248 rounds on justlogic-d234. The budget moved to mint, where the
    # author can act on it (`TaskDecomposition.check_budget`).
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
        # Items joined for the assertion below; `task_context` is one item by contract.
        rendered = "".join(i.text for i in _r_task_context(bundle))
    assert long_field in rendered, "authored framing must reach the LLM whole"
    assert "…" not in rendered, "authored text is never clipped"
    assert not any("cap" in r.getMessage() for r in caplog.records), (
        "no truncation, so no overrun warning — the budget is enforced at mint instead"
    )


def test_hit_cache_respects_dataset(tmp_path: Path) -> None:
    """``load_reusable_results`` scopes by dataset — identical node-configs and a
    colliding sample_id across datasets must NOT serve one dataset's cached
    results under the other.

    The cache is keyed by ``sample_id``, so the two datasets' keys COLLIDE by
    construction (both seed sample 14): the guarantee can only be read off the
    values. It is `dataset_name` — required, never `None` — that keeps them apart,
    and each cache must carry its OWN dataset's measurement, hit flag and all.
    """
    archive = MeasurementArchive(tmp_path)
    _seed_run(archive, run_id="aime_cached", dataset_name="aime", hit=True)
    _seed_run(archive, run_id="just_fresh", dataset_name="justlogic", hit=False)

    node_configs = [("llm_only", {"model": "X"})]
    aime_cache = archive.load_reusable_results(node_configs, dataset_name="aime")
    just_cache = archive.load_reusable_results(node_configs, dataset_name="justlogic")

    assert set(aime_cache) == {14} and set(just_cache) == {14}
    assert aime_cache[14]["query"] == "q_aime_14"
    assert just_cache[14]["query"] == "q_justlogic_14"
    # The bleed this guards: aime's cached HIT must not be served for justlogic's miss.
    assert aime_cache[14]["hit"] is True
    assert just_cache[14]["hit"] is False

    # An unscoped slice would pool both datasets under the one colliding id — so it is
    # refused outright rather than silently serving whichever run sorted last.
    with pytest.raises(ValueError, match="dataset_name"):
        archive.load_reusable_results(node_configs, dataset_name="")


def test_recut_rows_under_a_used_dataset_name_are_refused(tmp_path: Path) -> None:
    """The archive can answer "what was measured at slot 14", never "what was measured for THIS
    question" — ``sample_id`` is a position and the query text is not in the cache key.

    So re-cutting rows under a name already used serves every prior against a different question,
    with no error anywhere: the run completes and each replayed score is attributed to text that
    did not produce it. The guard reads the content off the stored row itself, which is why it
    needs nothing on disk that measurement rows do not already carry — and why it catches ONE
    edited row, where a whole-dataset fingerprint would only report the aggregate.
    """
    archive = MeasurementArchive(tmp_path)
    _seed_run(archive, run_id="aime_cached", dataset_name="aime", hit=True)
    cache = cast(
        "dict[int, QueryMeasurement]",
        archive.load_reusable_results([("llm_only", {"model": "X"})], dataset_name="aime"),
    )
    assert set(cache) == {14}, "precondition: the prior is reusable at slot 14"

    same = [Sample(id=14, query="q_aime_14", ground_truth="g")]
    _assert_measured_content_matches(cache, same, "aime")

    # Ground truth alone is enough — a relabelled row is as wrong as a replaced question.
    for recut in (
        [Sample(id=14, query="an entirely different question", ground_truth="g")],
        [Sample(id=14, query="q_aime_14", ground_truth="not_g")],
    ):
        with pytest.raises(DatasetIdentityError, match="sample_id 14"):
            _assert_measured_content_matches(cache, recut, "aime")


def test_export_carries_one_round_whole_and_survives_the_file() -> None:
    """The export is the one artifact leaving this package, and both ways it can lie are silent.

    Its prompt and its numbers must come from ONE round — pair a winner's fields with a
    neighbour's fitness and the consumer reads a prompt that never scored what the file says it
    scored, with nothing anywhere to raise. And the fields must survive the round trip: the
    round document carries ``few_shot_examples`` structured, while ``CycleResult``'s wire-side
    ``winner_prompt_fields`` has already flattened them to a rendered ``few_shot_block`` —
    reading the export off that one produces a file that looks complete and reconstructs a
    prompt missing its demonstrations.
    """
    from promptpotter.domain.export import (
        EXPORT_ARTIFACT_VERSION,
        ExportVersionError,
        build_prompt_export,
        parse_prompt_export,
    )
    from promptpotter.domain.ruler import AbilityReading

    shots = [{"input": "2+2", "output": "4", "explanation": None}]
    winner = round_result(
        3,
        accuracy=0.75,
        composite_fitness=0.81,
        total=28,
        matched_parent_lift=0.1,
        ability=AbilityReading(
            theta=0.42,
            se=0.1,
            ruler_id="anchor-x",
            ruler_n=28,
            ruler_span=3.4,
            round_span=2.1,
            calibration_model="1PL",
            caveat=None,
        ),
        prompt_fields={
            "persona": "P",
            "instruction": "I",
            "plan": "PL",
            "few_shot_examples": shots,
        },
        pipeline_params={"steps": ["llm_only"], "llm_only": {"model": "m", "prompt": "rendered"}},
    )
    export = build_prompt_export(
        winner,
        tool_version="0.0.0",
        campaign_id="c",
        cycle_id="cy",
        dataset_name="d",
        dataset_hash="h",
        optimizer_prompt_hash="o",
        stop_reason="completed",
        finished_at=utcnow_iso(),
        formula="accuracy",
        origin_accuracy=0.65,
        origin_composite_fitness=0.65,
    )

    m = export.measurement
    assert (m.round, m.accuracy, m.composite_fitness, m.n) == (3, 0.75, 0.81, 28)
    # The exported θ names the ruler it was read on — without it no reader outside this cycle
    # can say what the level is comparable to.
    assert m.ability is not None
    assert (m.matched_parent_lift, m.ability.theta, m.ability.ruler_id) == (0.1, 0.42, "anchor-x")

    # `steps` is wire scaffold and the rendered prompt is `prompt_fields` again — neither is a
    # tunable, and one artifact does not state a fact twice.
    assert export.tuned_params == {"llm_only": {"model": "m"}}

    restored = parse_prompt_export(export.model_dump_json()).template()
    assert (restored.persona, restored.instruction, restored.plan) == ("P", "I", "PL")
    assert [ex.model_dump() for ex in restored.few_shot_examples] == shots

    bumped = json.loads(export.model_dump_json())
    bumped["artifact_version"] = EXPORT_ARTIFACT_VERSION + 1
    with pytest.raises(ExportVersionError, match="this build reads"):
        parse_prompt_export(json.dumps(bumped))


def test_schema_description_axis_reaches_the_target_and_cannot_rename_a_field() -> None:
    """A `description` edit folds into the TARGET's wire schema; an invented key never lands.

    The core structured-output lever: `l1_generate` describes a TARGET node's own output
    schema (justlogic-d234's `{reasoning, answer}`), keyed by that node's fields, on any
    `output_schema`-bearing target — no per-dataset opt-in. Silently harmful two ways. If the
    key the schema advertises drifts from the field the fold writes, the optimizer spends
    budget on an axis the wire never carries, and every variant scores as a legitimate no-op.
    And `description` is free only because no parser reads it — a field NAME is the contract,
    so an invented field must be dropped before the wire, never grafted on.
    """
    from promptpotter.application.optimization.dispatch.l1_wire_schema import (
        build_l1_response_schema,
    )
    from promptpotter.application.optimization.validators.l1_strict import validate_overrides
    from promptpotter.domain.pipeline_overlay import fold_schema_descriptions

    schema = _pipeline_schema("justlogic-d234")
    node = schema.get_node("llm_only")
    assert node is not None and node.output_schema is not None
    fields = list(node.output_schema.fields)  # ["reasoning", "answer"] — the closed set

    # EMIT: the lever is handed to L1, keyed by the target node's OWN fields, schema-driven.
    emitted = _emittable_l1_params(
        build_l1_response_schema(schema, citable_fields=()), node="llm_only"
    )
    assert "output_schema_descriptions" in emitted
    describable = build_l1_response_schema(schema, citable_fields=())["properties"]["variants"][
        "items"
    ]["properties"]["pipeline_params_override"]["properties"]["llm_only"]["properties"][
        "output_schema_descriptions"
    ]
    assert set(describable["properties"]) == set(fields)

    # A description edit is a valid `object` override (declared, type-checked).
    edit = {"llm_only": {"output_schema_descriptions": {"answer": "ANSWER FIRST."}}}
    assert validate_overrides(edit, schema) == []

    # APPLY: the fold rewrites the wire schema's prose and removes the virtual key; an
    # invented field (`made_up`) never reaches the wire; and no edit bound → schema untouched.
    base_cfg = dict(node.current_config)
    pp = {
        "llm_only": {
            **base_cfg,
            "output_schema_descriptions": {"answer": "ANSWER FIRST.", "made_up": "dropped"},
        }
    }
    fold_schema_descriptions(pp, schema)
    props = pp["llm_only"]["output_schema"]["properties"]
    assert props["answer"]["description"] == "ANSWER FIRST."
    assert "made_up" not in props
    assert "output_schema_descriptions" not in pp["llm_only"]

    untouched = {"llm_only": dict(base_cfg)}
    before = json.dumps(untouched, sort_keys=True)
    fold_schema_descriptions(untouched, schema)
    assert json.dumps(untouched, sort_keys=True) == before  # no override → byte-identical wire

    # The raw schema stays locked — the `description` prose is the ONLY unlocked schema surface.
    forbidden = validate_overrides(
        {"llm_only": {"output_schema": {"type": "object"}}},
        schema,
    )
    assert [f.reason for f in forbidden] == ["forbidden_axis"]


def test_emittable_params_are_declared_and_an_invented_one_is_rejected() -> None:
    """`node_param_keys` is the single emittable surface — and every reader must read it.

    An invented PARAM is not dropped the way a hallucinated NODE is: absent a membership
    check it merges into `pipeline_params` and rides to the wire. The round completes and
    the candidate's fitness is attributed to an axis that does not exist. Same set, two
    readers: a graft on one side alone is either an unhonoured edit or an unguarded one.
    """
    from promptpotter.application.optimization.dispatch.l1_wire_schema import (
        build_l1_response_schema,
    )
    from promptpotter.application.optimization.validators.l1_strict import validate_overrides

    schema = _pipeline_schema("promptpotter-self")
    emitted = build_l1_response_schema(schema, citable_fields=())["properties"]["variants"][
        "items"
    ]["properties"]["pipeline_params_override"]["properties"]
    for node, keys in schema.node_param_keys().items():
        assert set(emitted[node]["properties"]) <= keys, (
            f"{node}: the schema declares a key `validate_overrides` rejects as unknown_param"
        )

    # A param no node advertises is rejected, not silently merged.
    reasons = [
        (f.axis, f.reason)
        for f in validate_overrides({"l1_generate": {"invented_knob": 1}}, schema)
    ]
    assert reasons == [("l1_generate.invented_knob", "unknown_param")]
    # A nested param is declared `object`, so a scalar in its slot is caught rather than
    # coerced — depth comes from the declaration, never from sniffing the value.
    assert [f.reason for f in validate_overrides({"l1_critique": {"layout": "hdr"}}, schema)] == [
        "type_mismatch"
    ]
    assert validate_overrides({"l1_critique": {"layout": {"instruction": ["plan"]}}}, schema) == []


def test_nested_param_override_accumulates_instead_of_reverting_its_parent() -> None:
    """A `param_types: object` param merges one level; siblings the child did not name survive.

    A nested param is ONE key in the node config, so a node-level `{**existing, **incoming}`
    spread replaces it whole: a candidate that improves a single `output_schema_descriptions`
    entry silently reverts every entry its parent earned, and the axis cannot accumulate
    across generations. The `object` declaration is what buys the depth, so every nested
    param the schema grafts must carry one. An `array` must NOT merge — a list is an ordering.
    """
    from promptpotter.application.optimization.l1.population import merge_pipeline_params

    schema = _pipeline_schema("promptpotter-self")

    # Every nested param a node's schema can graft accumulates, not just the first one:
    # `output_schema_field_names` + `layout` on the optimizer's own nodes (pp-self),
    # `output_schema_descriptions` on any target node (justlogic-d234's `llm_only`, below).
    for node, nested in (("l1_generate", "output_schema_field_names"), ("l1_critique", "layout")):
        got = merge_pipeline_params(
            {node: {nested: {"a": "A", "b": "B"}}},
            {node: {nested: {"a": "A2"}}},
            schema,
        )
        assert got is not None
        assert got[node][nested] == {"a": "A2", "b": "B"}, (
            f"{node}.{nested} is not declared `param_types: object` — a child override reverts "
            f"its parent's siblings"
        )

    # The description axis accumulates on the TARGET node, keyed by that node's fields.
    just = _pipeline_schema("justlogic-d234")
    base = {
        "llm_only": {
            "temperature": 0.7,
            "output_schema_descriptions": {"reasoning": "A", "answer": "B"},
        }
    }
    merged = merge_pipeline_params(
        base, {"llm_only": {"output_schema_descriptions": {"reasoning": "A2"}}}, just
    )
    assert merged is not None
    assert merged["llm_only"]["output_schema_descriptions"] == {"reasoning": "A2", "answer": "B"}
    assert merged["llm_only"]["temperature"] == 0.7
    # The origin is never aliased or mutated by a candidate's merge.
    assert base["llm_only"]["output_schema_descriptions"]["reasoning"] == "A"

    # A named slot's list REPLACES; an unnamed slot keeps the floor.
    lay_base = {
        "l2_context": {"layout": {"problem_description": ["critique"], "instruction": ["plan"]}}
    }
    lay = merge_pipeline_params(
        lay_base, {"l2_context": {"layout": {"problem_description": ["axis_memory"]}}}, schema
    )
    assert lay is not None
    assert lay["l2_context"]["layout"] == {
        "problem_description": ["axis_memory"],
        "instruction": ["plan"],
    }

    # Undeclared param → node-level shallow semantics, unchanged.
    plain = merge_pipeline_params(
        {"l1_generate": {"persona": "x", "instruction": "y"}},
        {"l1_generate": {"persona": "z"}},
        schema,
    )
    assert plain == {"l1_generate": {"persona": "z", "instruction": "y"}}


def test_schema_field_rename_is_locked_by_default_and_never_silently_half_applies() -> None:
    """The field-NAME lever, from the fork that unlocks it to the parse that honours it.

    Three silent harms, none of which raise. (1) A rename the emitted schema advertises but
    the response model does not alias fails EVERY parse of EVERY round — schema and model must
    derive from one function. (2) Gating the *apply* on the inner cycle's own config would
    silently drop every rename an outer campaign emits (the inner loads its own
    `campaign.json`), scoring a no-op as a legitimate mutation. (3) `populate_by_name` must
    stay off: if the old key still validated, a rename the model ignored would look applied.
    """
    from promptpotter.application.campaign_config import CampaignConfig, OptimizationConfig
    from promptpotter.application.optimization.dispatch.l1_wire_schema import (
        build_l1_response_schema,
        effective_l1_field_names,
    )
    from promptpotter.application.optimization.dispatch.llm_call.prompts import (
        set_optimizer_prompt_overrides,
    )
    from promptpotter.application.optimization.dispatch.schemas import (
        L1GenerateOutput,
        build_l1_response_model,
    )
    from promptpotter.application.runner.entry import _apply_config_overrides
    from promptpotter.domain.run_records import ConfigOverrides

    outer = _pipeline_schema("promptpotter-self")
    inner = _pipeline_schema("justlogic-d234")
    rename = {"changes_description": "mutation_rationale"}

    try:
        # Locked by default: the outer cannot even emit the rename key.
        set_optimizer_prompt_overrides(None)
        assert "output_schema_field_names" not in _emittable_l1_params(
            build_l1_response_schema(outer, citable_fields=())
        )
        unlocked = _emittable_l1_params(
            build_l1_response_schema(outer, citable_fields=(), schema_field_rename=True)
        )
        assert "output_schema_field_names" in unlocked

        # Only a fork opens it: the delta reaches the fork's config, the parent stays frozen
        # (its rounds must remain comparable), and an unrelated fork inherits rather than resets.
        base = CampaignConfig(optimization=OptimizationConfig(degradation_threshold=0.05))
        forked = _apply_config_overrides(base, ConfigOverrides(schema_field_rename=True))
        assert forked.optimization.schema_field_rename is True
        assert base.optimization.schema_field_rename is False
        inherited = _apply_config_overrides(forked, ConfigOverrides(max_rounds=3))
        assert inherited.optimization.schema_field_rename is True

        # The inner cycle applies a bound rename even though its OWN knob is off (default).
        set_optimizer_prompt_overrides({"l1_generate": {"output_schema_field_names": rename}})
        assert effective_l1_field_names() == rename
        variant = build_l1_response_schema(inner, citable_fields=())["properties"]["variants"][
            "items"
        ]
        assert "mutation_rationale" in variant["properties"]
        assert "changes_description" not in variant["properties"]
        assert "mutation_rationale" in variant["required"]

        # Schema and model agree: the wire key parses, and binds back onto the real field.
        model = build_l1_response_model(effective_l1_field_names(), parent_text={})
        parsed = model.model_validate(
            {
                "variants": [
                    {
                        "mutation_rationale": "r",
                        "prompt_fields_override": {"persona": "p"},
                    }
                ]
            }
        )
        assert parsed.variants[0].changes_description == "r"

        # No backward compatibility: the OLD key must now fail, so an unhonoured rename is
        # a parse failure (charged 1.0) rather than a silently-scored no-op.
        with pytest.raises(pydantic.ValidationError):
            model.model_validate(
                {
                    "variants": [
                        {
                            "changes_description": "r",
                            "prompt_fields_override": {"persona": "p"},
                        }
                    ]
                }
            )

        # An ambiguous rename onto a surviving field is dropped, not applied.
        set_optimizer_prompt_overrides(
            {
                "l1_generate": {
                    "output_schema_field_names": {"changes_description": "prompt_fields_override"}
                }
            }
        )
        assert effective_l1_field_names() == {}

        # Nothing bound → the plain model, allocated once.
        set_optimizer_prompt_overrides(None)
        assert (
            build_l1_response_model(effective_l1_field_names(), parent_text={}) is L1GenerateOutput
        )
    finally:
        set_optimizer_prompt_overrides(None)


def test_adopt_advances_identity_and_carries_the_wound_ledger():
    """The single adoption seam (``Cycle.adopt``) — used for an L1 win and an L2/L3
    transition alike — must ADVANCE lineage to the new parent (parent = the outgoing
    one) while CARRYING the outgoing parent's persistent memory: the wound ledger and
    L2's l1_layout. ``mutate`` deliberately resets those two on a child, so a seam that
    forgets to carry them silently drops the failures the search already paid to discover
    — no error, the next round just re-invites the mistake. The surface the adoption
    OWNS (here task_context) must instead come from the new parent.
    """
    from promptpotter.application.optimization.cycle import Cycle

    parent = OptSearchPoint(persona="Expert", instruction="Rank.")
    parent.memory.wounds.l3_note = "prior failure ledger"
    # An L1 winner is a `mutate` child: it inherits task_context but resets wounds.
    winner = parent.mutate(
        source="l1_generate", changes_description="try X", task_context={"domain": "biotech"}
    )
    assert winner.memory.wounds.l3_note == ""  # the reset adopt must repair
    prior_id = parent.lineage.id

    cyc = object.__new__(Cycle)
    cyc.opt_sp = parent
    cyc.adopt(winner, advanced={"task_context": winner.memory.task_context})

    # Identity advanced to the winner, parented on the outgoing parent.
    assert cyc.opt_sp is winner
    assert cyc.opt_sp.lineage.id == winner.lineage.id
    assert cyc.opt_sp.lineage.parent_id == prior_id
    # The wound ledger carried forward (would be silently lost without copy_memory_to).
    assert cyc.opt_sp.memory.wounds.l3_note == "prior failure ledger"
    # The OWNED surface came from the new parent, not the carried memory.
    assert cyc.opt_sp.memory.task_context.domain == "biotech"


def test_a_forks_attempts_stay_separate_on_the_campaigns_timeline(built_stores) -> None:
    """A fork that ran three rounds contributes THREE attempts, never one merged bar.

    Silent by construction, which is why it rides here. The lineage tree is a read model, so
    a wrong shape raises nothing — and the version this replaced built the fork as a course
    and then hoisted its grandchildren onto a single node, painting that node with the fork's
    `best_accuracy`. Three distinct searchpoints, three distinct scores, would render as one
    plausible bar wearing a number none of them measured. The only fork on disk had a single
    real attempt, so it looked correct and shipped.

    Also pins the two facts that make the merged shape unreachable: a fork's `C0` names the
    candidate it was cut from (`parent_id` spans cycles), so it is a REPLAY and collapses
    into it; and the surviving attempts are renumbered onto the campaign's ONE sequence,
    because `C{round}.{n}` is a course's private counter and every fork mints its own `C1.1`.
    """
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.infrastructure.store.lineage_views import build_lineage_tree

    campaign_id, root = "demo__aaaaaa", "cycle_1111"
    base = built_stores.base_dir / "campaigns" / campaign_id
    base.mkdir(parents=True, exist_ok=True)
    (base / "campaign.json").write_text(
        Campaign(
            campaign_id=campaign_id,
            dataset_name="demo",
            created_at="2026-01-01T00:00:00",
            root_cycle_id=root,
        ).model_dump_json()
    )

    def write(cycle_id: str, cands: list[tuple[int, str, str | None]], **index: Any) -> None:
        cdir = base / "cycles" / cycle_id
        (cdir / ".runtime").mkdir(parents=True, exist_ok=True)
        (cdir / "index.json").write_text(json.dumps({"status": "finished", **index}))
        (cdir / ".runtime" / "ledger.jsonl").write_text(
            "".join(
                json.dumps(
                    {
                        "record_type": "candidate_minted",
                        "round": rnd,
                        "idx": 0,
                        "candidate_id": cid,
                        "parent_id": parent,
                        "label": f"C{rnd}.1" if rnd else "C0",
                    }
                )
                + "\n"
                for rnd, cid, parent in cands
            )
        )

    # The root mints its OWN round-1 candidate, so the fork's round-1 attempt has a rival for
    # the `C1.n` slot and the renumber actually has to move it. Without this the fork's
    # attempts are each first-of-round on the parent's timeline too, every private label
    # survives unchanged, and the renumber this test exists to pin is a silent no-op.
    write(root, [(0, "root-c0", None), (1, "root-c1", "root-c0")])
    write(
        f"{root}_fork_beef",
        [
            # The fork's origin NAMES the candidate it was cut from — that is what makes it
            # a replay rather than an attempt, and it is structural, not a convention.
            (0, "fork-c0", "root-c0"),
            (1, "fork-c1", "fork-c0"),
            (2, "fork-c2", "fork-c1"),
            (3, "fork-c3", "fork-c2"),
        ],
        parent_cycle_id=root,
        fork={"from_candidate_id": "root-c0"},
        best_accuracy=0.42,
    )

    kids = build_lineage_tree(
        built_stores, (CycleHop(campaign_id=campaign_id, cycle_id=root),)
    ).children

    # Every attempt keeps its own identity — not one node wearing 0.42.
    assert [k.id for k in kids] == ["root-c0", "root-c1", "fork-c1", "fork-c2", "fork-c3"]
    # THE RENUMBER: the fork minted its round-1 attempt as `C1.1`, but the root already owns
    # that slot, so on the campaign's one sequence it is `C1.2`.
    assert [k.label for k in kids] == ["C0", "C1.1", "C1.2", "C2.1", "C3.1"]
    # The replayed origin IS `root-c0`, measured again — it is not a second node.
    assert "fork-c0" not in {k.id for k in kids}

    # `course_label` keeps the MINTING course's private position through that renumber, and
    # this is the silent-harm half: `dashboard.json` is per-cycle, so a fork's own projection
    # speaks the fork's counter — `C1.1`, the label the campaign timeline just took away.
    # Drop the field (or fold it into the renumber's update dict) and every join against a
    # fork's own projection — the L4 samples panel's cell → inner-run lookup — resolves
    # nothing: no error, no empty state, just cells that quietly report no run.
    assert [k.course_label for k in kids] == ["C0", "C1.1", "C1.1", "C2.1", "C3.1"]
    fork_attempt = kids[2]
    assert (fork_attempt.label, fork_attempt.course_label) == ("C1.2", "C1.1")
    # A candidate this course minted itself has one position, so its two labels agree.
    assert all(k.label == k.course_label for k in kids if k.id.startswith("root-"))


def test_lineage_serves_the_election_lift_joined_on_the_minting_label(built_stores) -> None:
    """The gate's verdict reaches the tree, and each course answers with its OWN numbers.

    `matched_parent_lift` is stamped during the ELECTION — a phase after the ledger already
    wrote its `candidate_scored` snapshot — so `LedgerCandidate` cannot carry it and declaring
    it there would yield an all-null column on every live run. It is folded from the course's
    own `dashboard.json` rounds instead, joined on the MINTING label.

    Silent both ways if the join slips: a wrong key serves `None` everywhere, and a join
    against the parent's projection would caption one course's crown with another course's
    lift. Neither raises.
    """
    import json

    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.infrastructure.store.lineage_views import build_lineage_tree

    campaign_id, root = "lift__aaaaaa", "cycle_lift0"
    base = built_stores.base_dir / "campaigns" / campaign_id
    base.mkdir(parents=True, exist_ok=True)
    (base / "campaign.json").write_text(
        Campaign(
            campaign_id=campaign_id,
            dataset_name="demo",
            created_at="2026-01-01T00:00:00",
            root_cycle_id=root,
        ).model_dump_json()
    )

    def write(cycle_id: str, lift: float | None, **index: object) -> None:
        cdir = base / "cycles" / cycle_id
        (cdir / ".runtime").mkdir(parents=True, exist_ok=True)
        (cdir / "index.json").write_text(json.dumps({"status": "finished", **index}))
        (cdir / ".runtime" / "ledger.jsonl").write_text(
            "".join(
                json.dumps(
                    {
                        "record_type": "candidate_minted",
                        "round": rnd,
                        "idx": 0,
                        "candidate_id": f"{cycle_id}-c{rnd}",
                        "parent_id": None if rnd == 0 else f"{cycle_id}-c0",
                        "label": f"C{rnd}.1" if rnd else "C0",
                    }
                )
                + "\n"
                for rnd in (0, 1)
            )
        )
        (cdir / "dashboard.json").write_text(
            json.dumps(
                {
                    "rounds": [
                        {
                            "round": 1,
                            "candidates": [
                                {
                                    "label": "C1.1",
                                    "matched_parent_lift": lift,
                                    "matched_parent_lift_ci_lo": None if lift is None else 0.04,
                                    "matched_parent_lift_ci_hi": None if lift is None else 0.2,
                                }
                            ],
                        }
                    ]
                }
            )
        )

    write(root, 0.12)
    # A fork carries its OWN dashboard, with a different number in the same slot.
    write(
        f"{root}_fork_beef",
        0.31,
        parent_cycle_id=root,
        fork={"from_candidate_id": f"{root}-c0"},
    )

    kids = build_lineage_tree(
        built_stores, (CycleHop(campaign_id=campaign_id, cycle_id=root),)
    ).children
    by_id = {k.id: k for k in kids}

    own = by_id[f"{root}-c1"]
    assert (own.matched_parent_lift, own.matched_parent_lift_ci_lo) == (0.12, 0.04)
    # The fork's attempt is RENUMBERED onto this timeline, so the label the campaign shows it
    # under is no longer the one its own projection speaks. It must still read its own 0.31 —
    # a join on the renumbered `label` would silently hand it the root's 0.12.
    contributed = by_id[f"{root}_fork_beef-c1"]
    assert contributed.matched_parent_lift == 0.31
    # Round 0 elects nothing, so nothing captions it.
    assert by_id[f"{root}-c0"].matched_parent_lift is None


def test_two_inner_runs_of_one_benchmark_cell_both_reach_the_tree(built_stores) -> None:
    """A cycle_id is not an identity, and inside an L4 sandbox that is not a corner case.

    An inner cycle's id is the content hash of the BENCHMARK CELL's origin, so every outer
    candidate measured on the same cell mints the identical one — `C0` on seed-0 and `C1.1`
    on seed-0 are two runs, one cycle_id, told apart only by their campaign. The family walk
    de-duplicates visited cycles (its guard against a corrupt `parent_cycle_id` looping), and
    keyed on the cycle_id alone that guard silently ATE the second run: the tree served one
    course, the operator's sidebar showed the candidate measured on one cell out of two, and
    the `/ray` chronology — which shares this walk — lost its records too.

    Silent in the way this file exists for. Nothing errors; a candidate simply reports a
    smaller panel than it ran, and reads as complete.
    """
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.infrastructure.store.lineage_views import build_lineage_tree

    outer_campaign, outer_cycle = "l4__aaaaaa", "cycle_outer"
    cell = "cycle_seed0"  # ONE id, minted twice — the whole point

    outer = built_stores.base_dir / "campaigns" / outer_campaign / "cycles" / outer_cycle
    (outer / ".runtime").mkdir(parents=True, exist_ok=True)
    (outer / "index.json").write_text(json.dumps({"status": "finished"}))
    (outer / ".runtime" / "ledger.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "record_type": "candidate_minted",
                    "round": rnd,
                    "idx": 0,
                    "candidate_id": cid,
                    "parent_id": parent,
                    "label": label,
                }
            )
            + "\n"
            for rnd, cid, parent, label in [
                (0, "outer-c0", None, "C0"),
                (1, "outer-c11", "outer-c0", "C1.1"),
            ]
        )
    )

    # The sandbox is flat and keyed on the owning (tenant, campaign, cycle) — one per outer
    # cycle, shared by every candidate this course measured, which is what puts the two
    # colliding inner ids in one store.
    from promptpotter.infrastructure.store.layout import inner_sandbox_dir

    sandbox = (
        inner_sandbox_dir(
            built_stores.shared_root,
            str(built_stores.tenant_id),
            CycleHop(campaign_id=outer_campaign, cycle_id=outer_cycle),
        )
        / built_stores.tenant_id
    )
    for inner_campaign, spawned, best in [
        ("bench__aaaaaa", {"candidate_label": "C0", "task": "bench/seed-0"}, 0.57),
        (
            "bench__bbbbbb",
            {"candidate_id": "outer-c11", "candidate_label": "C1.1", "task": "bench/seed-0"},
            0.61,
        ),
    ]:
        cdir = sandbox / "campaigns" / inner_campaign / "cycles" / cell
        (cdir / ".runtime").mkdir(parents=True, exist_ok=True)
        (cdir / "index.json").write_text(
            json.dumps({"status": "finished", "spawned_by": spawned, "best_accuracy": best})
        )
        (cdir / ".runtime" / "ledger.jsonl").write_text("")

    kids = build_lineage_tree(
        built_stores, (CycleHop(campaign_id=outer_campaign, cycle_id=outer_cycle),)
    ).children

    assert [k.label for k in kids] == ["C0", "C1.1"]
    # Each candidate keeps the run that measured IT — one course each, not one course and a
    # hole, and not both piled under whichever campaign sorted first.
    runs = {k.label: k.children for k in kids}
    assert [c.path[-1].campaign_id for c in runs["C0"]] == ["bench__aaaaaa"]
    assert [c.path[-1].campaign_id for c in runs["C1.1"]] == ["bench__bbbbbb"]
    # …carrying its own number. The two share `id`, so only the PATH tells them apart.
    assert [c.best_accuracy for c in runs["C0"]] == [0.57]
    assert [c.best_accuracy for c in runs["C1.1"]] == [0.61]
    assert {c.id for c in runs["C0"]} == {c.id for c in runs["C1.1"]} == {cell}


def test_the_time_ray_pages_without_a_hole_and_never_doubles_a_forks_parent(
    built_stores,
) -> None:
    """Paging the ray covers every record exactly once — under churn — and a fork does not
    replay its parent.

    Every failure mode here is silent, which is why this rides here: a chronology is
    *plausible* with a record missing, doubled, or misplaced — nothing errors, the operator
    simply reads a wrong story and has no way to know. The variants pinned:

      * **A hole or overlap between windows.** The cursor is the merge key
        ``(ts_eff, encoded_path, offset)`` of the oldest returned item; consecutive windows
        must partition the key space exactly.
      * **A cycle discovered between two windows.** A fork minted AFTER the head fetch has
        only keys above every outstanding cursor, so it must enter at the next head fetch —
        never inside a deep page, where it would displace genuinely-older records that then
        surface nowhere.
      * **A fork replaying its parent.** ``CycleEventLog.iter()`` virtually walks the
        parent's prefix before a fork's own appends; the ray reads the parent too, so going
        through ``iter()`` would emit every parent record twice. It reads each ledger's own
        FILE for exactly this reason.
      * **The monotonic clamp.** Records are stamped at construction and appended later, so
        a raw-timestamp sort can place a record *before* the record that caused it.
      * **A record before the file's first parseable timestamp.** A fabricated epoch would
        sort below every outstanding cursor and mutate already-served windows; it is
        skipped, while still consuming its physical offset.
    """
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.infrastructure.store.family_ray_views import (
        build_family_ray,
        decode_ray_cursor,
        ray_validator_parts,
    )
    from promptpotter.infrastructure.store.lineage_views import iter_family_courses

    campaign_id, root = "ray__aaaaaa", "cycle_ray1"
    base = built_stores.base_dir / "campaigns" / campaign_id
    base.mkdir(parents=True, exist_ok=True)
    (base / "campaign.json").write_text(
        Campaign(
            campaign_id=campaign_id,
            dataset_name="demo",
            created_at="2026-01-01T00:00:00",
            root_cycle_id=root,
        ).model_dump_json()
    )

    def write(cycle_id: str, stamps: list[str], **index: Any) -> None:
        cdir = base / "cycles" / cycle_id
        (cdir / ".runtime").mkdir(parents=True, exist_ok=True)
        (cdir / "index.json").write_text(json.dumps({"status": "finished", **index}))
        (cdir / ".runtime" / "ledger.jsonl").write_text(
            "".join(
                json.dumps(
                    {
                        "record_type": "phase",
                        "phase": "round",
                        "event": "display",
                        "round": i,
                        "payload": {},
                        "timestamp": ts,
                    }
                )
                + "\n"
                for i, ts in enumerate(stamps)
            )
        )

    def stamp(second: int) -> str:
        return f"2026-01-01T00:00:{second:02d}Z"

    # Interleaved in TIME but separate on disk — the fork ran concurrently with its parent,
    # which is the thing only a chronology can show. Offset 3 of the root is stamped BEFORE
    # offset 2: the inversion the clamp exists to repair. The fork's offset 0 has no
    # parseable timestamp: skipped, but its physical offset still counts.
    write(root, [stamp(0), stamp(2), stamp(8), stamp(4)])
    write(
        f"{root}_fork_beef",
        ["not-a-timestamp", stamp(1), stamp(3), stamp(5)],
        parent_cycle_id=root,
        fork={"from_candidate_id": "root-c0"},
    )

    path = (CycleHop(campaign_id=campaign_id, cycle_id=root),)
    courses = iter_family_courses(built_stores, path)

    def window(before: str | None, limit: int):
        return build_family_ray(courses, limit=limit, before=decode_ray_cursor(before))

    whole = window(None, 50)
    assert len(whole.items) == 7, "every timestamped family record rides the ray once"
    assert whole.cursor_prev is None

    # A fork's own file is its own records — the parent's four are not replayed into it —
    # and the unparseable-timestamp record consumed offset 0 without riding.
    seen = [(item.path[-1].cycle_id, item.offset) for item in whole.items]
    assert len(seen) == len(set(seen)), "no record appears at two positions"
    assert [off for cyc, off in seen if cyc == root] == [0, 1, 2, 3]
    assert [off for cyc, off in seen if cyc != root] == [1, 2, 3]

    # THE CLAMP. The root's 4th record is stamped 4s but was appended after the one stamped
    # 8s; append order is the only authority the file has, so it displays at 8s and stays put.
    assert [item.ts for item in whole.items if item.path[-1].cycle_id == root] == [
        stamp(0),
        stamp(2),
        stamp(8),
        stamp(8),
    ]

    # PAGING. Three windows of three; the union must reconstruct `whole` exactly — no
    # duplicate (the operator would read one event twice) and no hole (they would read a
    # story with an event removed, and nothing anywhere would say so).
    paged: list[tuple[str, int]] = []
    cursor, guard = None, 0
    while True:
        page = window(cursor, 3)
        paged = [(i.path[-1].cycle_id, i.offset) for i in page.items] + paged
        cursor = page.cursor_prev
        guard += 1
        assert guard < 10, "paging did not terminate"
        if cursor is None:
            break
    assert paged == seen

    # CHURN. A fork minted AFTER a head fetch must not enter a deep page served under an
    # older cursor — its records are newer than everything served, and a deep page that
    # admitted them would evict genuinely-older records into nowhere. It enters the next
    # HEAD fetch instead. (The validator must move too, or the new fork 304s into
    # invisibility.)
    head = window(None, 3)
    parts_before = ray_validator_parts(courses, limit=3, before=None)
    write(
        f"{root}_fork_cafe",
        [stamp(20), stamp(21)],
        parent_cycle_id=root,
        fork={"from_candidate_id": "root-c0"},
    )
    courses = iter_family_courses(built_stores, path)  # the route re-walks per request
    assert ray_validator_parts(courses, limit=3, before=None) != parts_before

    deep = window(head.cursor_prev, 3)
    assert all(item.ts < stamp(20) for item in deep.items)
    assert [(i.path[-1].cycle_id, i.offset) for i in deep.items] == paged[1:4]

    fresh = window(None, 3)
    assert [item.ts for item in fresh.items] == [stamp(8), stamp(20), stamp(21)]


def test_a_conditional_validator_moves_when_its_inputs_do(built_stores) -> None:
    """A validator that misses an input 304s a changed body FOREVER — the operator reads a
    stale tree or ray as current, and nothing anywhere errors. The two ways that happens,
    both pinned: a query value not folded into the ETag ("no lens" colliding with
    ``lens=""``), and an mtime that does not move on an append.
    """
    import os

    from promptpotter.infrastructure.store.io import newest_mtime_ns
    from promptpotter.presentation.api.routers.campaigns._conditional import weak_etag

    assert weak_etag(1, None, None) != weak_etag(1, "", None), "'no lens' must not collide"

    probe = built_stores.base_dir / "probe.jsonl"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text("x")
    os.utime(probe, ns=(1_700_000_000_000_000_000,) * 2)
    first = newest_mtime_ns(probe)
    os.utime(probe, ns=(1_700_000_000_001_000_000,) * 2)  # +1 ms: a same-second append
    second = newest_mtime_ns(probe)
    assert first is not None and second is not None and second > first
    assert weak_etag(first) != weak_etag(second)
    assert newest_mtime_ns(built_stores.base_dir / "absent.jsonl") is None


def test_collapse_counts_derive_from_candidate_scores_and_cannot_be_stamped() -> None:
    """The collapse counts are ONE recording of one fact, and the round file proves it.

    A collapsed candidate is not dropped — it rides ``candidate_scores`` with ``invalid=True``
    and its ``validation_failures``. The counts used to ALSO be stored as three scalars, so the
    same fact lived twice in one document with nothing checking they agreed; a disagreement is
    silent (both numbers look plausible, and the round completes either way), and it is what
    every reader — ``review.md``, the terminal, the L4 mode-collapse proxy — would report from.

    Two things are pinned. (1) The counts come from ``candidate_scores``, keyed by reason, one
    reason per candidate — a variant tripping several invariants collapsed once. (2) A caller
    that still passes the old kwargs cannot override them: ``RoundResult`` is ``extra="ignore"``,
    so a stale writer fails silently rather than loudly, and the derived value must win.
    """
    from promptpotter.domain.escalation_signals import ValidationFailure
    from promptpotter.domain.results import RoundResult, ScoredCandidate

    def collapsed(reason: str, *extra: str) -> ScoredCandidate:
        return ScoredCandidate(
            candidate_id=f"c-{reason}-{len(extra)}",
            label=reason,
            accuracy=0.0,
            composite_fitness=0.0,
            total=0,
            invalid=True,
            validation_failures=[
                ValidationFailure(axis="variant", value="x", allowed=["y"], reason=r)
                for r in (reason, *extra)
            ],
        )

    def scored_ok() -> ScoredCandidate:
        return ScoredCandidate(
            candidate_id="c-live",
            label="C1.9",
            accuracy=0.6,
            composite_fitness=0.6,
            total=10,
        )

    rr = RoundResult(
        round=1,
        label="r1",
        accuracy=0.6,
        total=10,
        improved=False,
        prompt_fields={},
        candidates_scored=5,
        l1_n_no_op=99,  # a stale writer still stamping — must NOT win
        l1_n_duplicate=99,
        l1_n_repeat=99,
        candidate_scores=[
            collapsed("no_op_variant"),
            collapsed("duplicate_variant"),
            collapsed("repeat_variant"),
            # trips two invariants — collapsed ONCE, counted once
            collapsed("repeat_variant", "duplicate_variant"),
            scored_ok(),
        ],
    )

    assert rr.l1_collapsed == {
        "no_op_variant": 1,
        "duplicate_variant": 1,
        "repeat_variant": 2,
    }
    assert (rr.l1_n_no_op, rr.l1_n_duplicate, rr.l1_n_repeat) == (1, 1, 2)
    assert rr.l1_n_no_op != 99, "a stamped value must never beat the derivation"

    # The counts still reach disk and the API (`computed_field`), and survive the round trip
    # that `rounds/round_NNNN.json` is read back through.
    dumped = rr.model_dump()
    assert dumped["l1_n_repeat"] == 2
    assert RoundResult.model_validate(dumped).l1_n_repeat == 2


def test_repeat_marker_reads_the_idea_not_the_field_it_was_written_into() -> None:
    """`mutation_memory`'s ↺ marker is the loop's only defence against re-proposal, and both
    ways it can fail are SILENT — the panel still renders a full, plausible record either way.

    The failure it exists to catch, measured on `justlogic-d234`: one idea ("exhaust modus
    tollens / disjunctive syllogism before answering Uncertain") was re-proposed for 8 straight
    rounds, each time rewritten into a DIFFERENT field — instruction, then thinking_style, then
    output_schema_descriptions.reasoning, then task_intent. Keyed on field+value the rows look
    unrelated, so nothing objected and the cycle burned 8 rounds on one hypothesis.

    The inverse failure is what a first cut of this actually did: fingerprinting the rendered
    `field: "value"` row made the field NAME part of the idea, so it paired two unrelated edits
    to one field and still missed the cross-field repeat — the marker fires, reads plausibly,
    and means the opposite of what it says.
    """
    from promptpotter.domain.candidate_diff import IDEA_MATCH_MARK, same_idea
    from promptpotter.domain.candidate_diff import idea_fingerprint as _idea_fingerprint

    def _same_idea(a: frozenset[str], b: frozenset[str]) -> bool:
        return same_idea(a, b, threshold=IDEA_MATCH_MARK)

    # One idea, two fields — must match on vocabulary alone.
    as_instruction = (
        "Derive new facts using modus ponens, modus tollens, disjunctive syllogism and "
        "chaining. Exhaust every derivation before concluding Uncertain."
    )
    as_reasoning = (
        "Apply modus tollens, disjunctive syllogism and contrapositive to derive new facts, "
        "exhausting each derivation branch before you conclude Uncertain."
    )
    assert _same_idea(_idea_fingerprint([as_instruction]), _idea_fingerprint([as_reasoning])), (
        "the same idea rewritten into another field must still register as already tried"
    )

    # Genuinely different ideas that happen to share a field must NOT match.
    other_idea = (
        "Return the label as a bare token with no surrounding prose, punctuation or markdown "
        "fence, so the parser reads exactly one word from the response body."
    )
    assert not _same_idea(_idea_fingerprint([as_instruction]), _idea_fingerprint([other_idea])), (
        "distinct mutations must not be collapsed — a false ↺ hides a real attempt"
    )

    # The field name must contribute nothing: same value, different field, identical print.
    assert _idea_fingerprint([as_instruction]) == _idea_fingerprint([as_instruction]), "unstable"
    assert not (
        _idea_fingerprint(["output_schema_descriptions.reasoning"])
        & _idea_fingerprint([as_instruction])
    ), "field-name tokens leaked into the idea fingerprint"


# Binary formats legitimately full of NULs. Suffix-scoped so a NEW binary kind must be
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


def test_yaml_emitter_never_reinterprets_a_string_it_wrote(tmp_path: Path) -> None:
    """A config value that survives the write as a *different type* is silent harm.

    YAML 1.1 — which PyYAML implements — resolves bare ``off``/``no``/``TRUE`` to
    booleans and ``0755`` to an int. Two live values sit on that edge: the JustLogic
    label enum is ``["TRUE", "FALSE"]`` and ``promptpotter-self`` sets
    ``prompt_block_catalogue: "off"``. If the emitter ever stopped quoting them, a
    written config would come back with a boolean where a label belongs and the
    pipeline would grade every sample against it — no error, wrong numbers.
    """
    path = tmp_path / "hazards.yaml"
    payload = {k: k for k in _YAML_1_1_HAZARDS} | {"nested": {"labels": list(_YAML_1_1_HAZARDS)}}
    write_yaml(path, payload)
    assert read_yaml(path) == payload


def test_yaml_emitter_keeps_prose_in_block_scalars() -> None:
    """The format exists so prose folds; a quoted blob is the failure it replaces.

    Not cosmetic in one direction: the emitter must reach block style *without*
    rewriting the string, because prompt text is hashed into measurement identity.
    So this pins both halves — the style is block, and the value is untouched.
    """
    paragraphs = (
        "A paragraph long enough that it must wrap somewhere, which is the whole reason the "
        "config tier moved off JSON in the first place.\n\n"
        "A second paragraph, equally long, so the emitter has to fold both of them and keep "
        "the blank line that separates them intact."
    )
    bullets = "Consider:\n- one\n- two"
    out = io.StringIO()
    yaml.dump(
        {"paragraphs": paragraphs, "bullets": bullets},
        out,
        Dumper=_YamlDumper,
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )
    text = out.getvalue()
    assert "\\n" not in text, f"fell back to a quoted blob instead of a block scalar:\n{text}"
    assert "paragraphs: >-" in text
    assert "bullets: |-" in text
    assert yaml.safe_load(text) == {"paragraphs": paragraphs, "bullets": bullets}


# Every boolean-valued path in the operator-authored config tier, keyed by
# ``{dataset}/{stem}::{dotted.path}`` so the entry survives the file's extension.
# ``assets/optimizer/resolved_schemas.json`` is out of scope by construction — it is
# generated, so it cannot grow a boolean by the hand-edit slip this census catches.
_CONFIG_TIER_BOOLEANS = frozenset(
    {
        "aime_2025/campaign::campaign_config.optimization.seed_heatmap_from_archive",
        "aime_2025/pipeline::nodes.llm_only.optimizer.observation_mappings[0].is_llm",
        "bbeh/pipeline::nodes.llm_only.optimizer.observation_mappings[0].is_llm",
        "email-tagging/pipeline::nodes.llm_only.optimizer.observation_mappings[0].is_llm",
        "gsm8k/pipeline::nodes.llm_only.optimizer.observation_mappings[0].is_llm",
        "justlogic-d234/pipeline::nodes.llm_only.config.output_schema.additionalProperties",
        "justlogic-d234/pipeline::nodes.llm_only.optimizer.observation_mappings[0].is_llm",
        "lca-termnorm/pipeline::nodes.cache_lookup.short_circuit",
        "lca-termnorm/pipeline::nodes.entity_profiling.optimizer.observation_mappings[0].is_llm",
        "lca-termnorm/pipeline::nodes.fuzzy_matching.short_circuit",
        "lca-termnorm/pipeline::nodes.llm_ranking.optimizer.observation_mappings[0].is_llm",
        "lca-termnorm/pipeline::nodes.web_search.config.extra_snippets",
        "lca-termnorm/pipeline::nodes.web_search.config.extract_pdf",
        "lca-termnorm/pipeline::nodes.web_search.config.spellcheck",
        "screen-taste-v0/pipeline::nodes.llm_only.optimizer.observation_mappings[0].is_llm",
    }
)

_CONFIG_TIER_GLOBS = (
    "*/pipeline.*",
    "*/campaign.*",
    "*/task_context.*",
    "*/inner_tasks.*",
    "*/prompts/*.*",
    "*/prompts.*",
    "*/sweep/*.*",
)


def _config_tier_files(root: Path) -> list[Path]:
    return sorted({p for g in _CONFIG_TIER_GLOBS for p in root.glob(g) if p.is_file()})


def _load_config(path: Path) -> Any:
    # Mid-migration the tier is part JSON, part YAML. Once it is all YAML this
    # collapses to read_yaml.
    raw = path.read_text(encoding="utf-8")
    return json.loads(raw) if path.suffix == ".json" else yaml.safe_load(raw)


def _boolean_paths(node: Any, prefix: str) -> list[str]:
    if isinstance(node, bool):
        return [prefix]
    if isinstance(node, dict):
        return [
            p for k, v in node.items() for p in _boolean_paths(v, f"{prefix}.{k}" if prefix else k)
        ]
    if isinstance(node, list):
        return [p for i, v in enumerate(node) for p in _boolean_paths(v, f"{prefix}[{i}]")]
    return []


def test_every_shipped_dataset_dir_is_recognized(built_stores) -> None:
    """A dataset that stops being a dataset does not raise — it just disappears.

    "Is this directory a dataset" is answered by probing for a config file. Rename that
    file and the probe answers no: the resolver 404s a shipped benchmark and the picker
    silently drops it, with nothing in any log. The install tier IS the directory
    listing, so derive the expectation from disk rather than authoring a name set
    (``promptpotter/CLAUDE.md``: a membership test over NAMES is a bug).
    """
    from promptpotter.infrastructure.store.dataset_access import (
        list_readable_datasets,
        readable_dataset_dir,
    )

    root = Path(__file__).resolve().parents[1] / "datasets"
    store = dataclasses.replace(built_stores, benchmarks_root=root)
    shipped = {d.name for d in root.iterdir() if d.is_dir() and not d.name.startswith((".", "_"))}
    listed = {r.name for r in list_readable_datasets(store) if r.tier == "install"}
    assert listed == shipped, (
        f"shipped but unlisted: {sorted(shipped - listed)}; listed but absent: "
        f"{sorted(listed - shipped)}"
    )
    for name in sorted(shipped):
        assert readable_dataset_dir(store, name).is_dir()


def test_the_l4_dataset_is_recognized_as_one() -> None:
    """The is-this-L4 probe and the loader must agree — a disagreement is silent.

    ``runner/inner/spawn.py`` decides whether to verify the outer observation contract
    by probing for the inner-task spec. Miss it and the check is skipped, undeclared
    inner keys are dropped, and the outer formula scores a measurement nobody took.

    The panel it loads must also measure in ONE UNIT, which is the second half here. Under
    1PL the δ ruler pins θ's scale through the logistic link; under 2PL it carries a
    discrimination ``a`` and θ becomes units of ``1/a``. Each inner cycle decides graduation
    from its own held-out CV, so WHICH cells graduate is a property of the draw rather than of
    the optimizer prompt under test — and the panel would then average and t-test a mixture of
    scales. Silent in the worst way: every cell completes, every number is plausible, and the
    pooled verdict is wrong with no symptom.
    """
    from promptpotter.application.campaign_config import load_campaign_config
    from promptpotter.application.runner.inner.tasks import (
        inner_instrument_config,
        load_inner_tasks,
        resolve_inner_task,
    )
    from promptpotter.connectors import CONNECTORS
    from promptpotter.infrastructure.store.io import read_yaml

    d = Path(__file__).resolve().parents[1] / "datasets" / "promptpotter-self"
    spec = d / CONNECTORS["promptpotter"].experiment_file
    assert spec.is_file(), f"the L4 probe would read {d.name} as a plain dataset ({spec})"
    panel = load_inner_tasks(spec)
    assert panel.tasks

    # The SHIPPED config, not a hand-built one — the question is what the panel runs under.
    base = load_campaign_config(read_yaml(d / "campaign.yaml")["campaign_config"])
    ctx = types.SimpleNamespace(dataset_config_dir=d)
    for task in panel.tasks:
        derived = inner_instrument_config(
            resolve_inner_task(ctx, task.id),
            base,
            llm_node="llm_only",
            n_scored=40,
        )
        assert derived.optimization.enable_2pl_graduation is False, (
            f"cell {task.id} may graduate its ruler to 2PL — its theta would then be in "
            "units of 1/a while the rest of the panel is in logits, and the outer verdict "
            "pools them anyway"
        )


def test_shipped_config_booleans_match_the_pinned_census() -> None:
    """A dropped quote in a hand-edited config is a type change, and it is silent.

    Pydantic catches it wherever a field is typed, but the tier is full of
    ``dict[str, Any]`` regions — ``nodes.*.config``, ``param_allowed_values`` — where
    nothing does. There ``prompt_block_catalogue: off`` parses happily as ``False``
    and the run reads a knob nobody set. Census, not a schema: the set of places a
    boolean legitimately lives is small and stable, so anything new is a slip.
    """
    root = Path(__file__).resolve().parents[1] / "datasets"
    found: set[str] = set()
    for path in _config_tier_files(root):
        data = _load_config(path)
        stem = path.relative_to(root).with_suffix("").as_posix()
        found |= {f"{stem}::{p}" for p in _boolean_paths(data, "")}

    assert found == _CONFIG_TIER_BOOLEANS, (
        "boolean census moved. A NEW entry is usually an unquoted YAML 1.1 word "
        f"(off/no/yes/on/TRUE) that used to be a string: {sorted(found - _CONFIG_TIER_BOOLEANS)}. "
        f"A MISSING entry means a real boolean was removed: {sorted(_CONFIG_TIER_BOOLEANS - found)}."
    )


# named here deliberately rather than widening the guard by accident.
_BINARY_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf", ".woff", ".woff2"}
)


def _tracked_files(root: Path) -> list[str]:
    """Tracked paths that are also ON DISK — the one walk every repo-wide scan here shares.

    A file deleted in the worktree but still in the index is an ordinary mid-refactor state, and a
    scan that reads the index and then opens each path blindly dies on it with `FileNotFoundError`
    — not a finding, just a crash, and in a shared checkout it turns one colleague's unstaged
    deletion into a red suite for everyone. A deleted file also makes no claims, so there is
    nothing to skip past: filtering here is what these scans mean by "tracked".
    """
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True
    ).stdout
    out: list[str] = []
    for raw in listing.split(b"\x00"):
        if not raw:
            continue
        rel = raw.decode("utf-8", "surrogateescape")
        if (root / rel).is_file():
            out.append(rel)
    return out


def test_no_raw_nul_bytes_in_tracked_text_files() -> None:
    """A raw NUL makes ripgrep skip the whole file SILENTLY when recursing.

    Not a style nit — it corrupts every audit run over the repo, and it is
    self-concealing: `rg` cannot find NUL-bearing files either, because searching
    `\x00` skips exactly the files that contain one. The tool cannot see its own
    blind spot, so re-running it never surfaces the lie. Meanwhile a NUL is a legal
    string char, so `tsc` / eslint / `next build` / `pytest` all stay green.

    2026-07-17: two tracked files held one — `webapp/lib/hooks/useConnector.ts`
    (a live consumer of three API readers, so audits "proved" those dead) and
    `docs/specs/code-debt-cleanup.md`, where the entry *describing* the raw NUL had
    pasted a raw NUL into the clause prescribing the fix. Between them they
    manufactured false dead-code findings twice.
    """
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for rel in _tracked_files(root):
        path = root / rel
        if path.suffix.lower() in _BINARY_SUFFIXES:
            continue
        if b"\x00" in path.read_bytes():
            offenders.append(rel)

    assert not offenders, (
        f"raw NUL byte in tracked text file(s): {offenders}. ripgrep will silently skip "
        "these when recursing, so every 'zero call sites' claim over them is false. "
        "Replace the byte with a visible separator, or add the suffix to _BINARY_SUFFIXES "
        "if the file is genuinely binary."
    )


_CLAUDE_LINK = re.compile(r"\]\(([^)\s]*?)(?:#([^)]*))?\)")
# A "§ Name" that FOLLOWS a link on the same line, i.e. a claim about the linked file.
# The name ends at the first punctuation a heading cannot contain. Deliberately not greedy:
# an over-wide capture is worse than no check, because a scan that cries wolf gets xfailed
# and then the entire claim surface goes unchecked — the exact failure this guards.
_CLAUDE_SECTION = re.compile(
    r"\]\(([^)\s]*?)(?:#[^)]*)?\)[^.\n]{0,40}?§\s*([A-Za-z][^.,;`|)\n\[]{2,60}?)"
    r"(?=\s+[—+·]|\s*$|\s*\(|,|\.\s|\s*\*\*|$)",
    re.M,
)
_CLAUDE_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$", re.M)
# The published front page cannot use relative links — it renders off-repo (GitHub, PyPI) —
# so it pins doc paths as absolute blob URLs, which the relative-link arm above skips by
# design. Same claim, different spelling: resolve the path half against the repo.
_GITHUB_BLOB = re.compile(
    r"https://github\.com/[^/\s)]+/[^/\s)]+/(?:blob|tree)/main/([^)\s#]+)(?:#([^)\s]+))?"
)
# The spelling the repo actually uses OUTSIDE markdown: a bare backticked path, not `](link)`.
# Leading dot allowed — `.claude/**` is cited from source. A citation that NARRATES the death
# ("`x.md`, gone 2026-06-18") is exempt: naming what was removed is the point of the sentence.
_CODE_DOC_PATH = re.compile(r"`(\.?[A-Za-z0-9_][A-Za-z0-9_./-]*\.md)`")
_NARRATED_DEATH = re.compile(
    r"`\.?[A-Za-z0-9_][A-Za-z0-9_./-]*\.md`[^`\n]{0,24}?\b(?:gone|deleted|removed|retired)\b"
)
# Basenames the SYSTEM writes per campaign — not documents in this repo. 27 of the raw hits, all
# false. Path-qualified (`datasets/gsm8k/dataset.md`) still resolves normally.
_RUNTIME_ARTIFACT_MD = frozenset(
    {"log.md", "review.md", "summary.md", "dataset.md", "task_description.md", "README.md"}
)
_CODE_SUFFIXES = (".py", ".ts", ".tsx", ".yaml", ".yml", ".toml", ".css", ".js")
# A backticked doc path followed by `§ Name`, unlinked. The gap is 3 chars, not the 40 the linked arm allows: at 40 this
# binds `architecture.md`'s own "§0" to a neighbouring `CLAUDE.md` mention. `]` is in the stop
# class because a markdown link straight after the § otherwise captures "Data model]".
_CODE_SECTION = re.compile(
    r"`(\.?[A-Za-z0-9_][A-Za-z0-9_./-]*\.md)`[ ,)]{0,3}§\s*[\"']?"
    r"([A-Za-z][^.,;`|)\n\[\]\"]{2,60}?)"
    r"(?=\s+[—+·→]|\s*$|\s*\(|,|\.\s|\s*\*\*|\"|$)",
    re.M,
)
# The repo's most-cited anchors are paragraph leads, not headings — `**The framing is frozen…**`
# is cited from `domain/search_point.py`. Headings alone would red-flag legitimate citations,
# and a check that cries wolf gets xfailed, taking the whole surface with it.
_CLAUDE_BOLD_LEAD = re.compile(r"^[>\-*|\s]{0,6}\*\*(.+?)\*\*", re.M)
_CLAUDE_CARD = re.compile(r"^##\s+Load-bearing\s*$(.*?)(?=^##\s|\Z)", re.M | re.S)
_CLAUDE_CARD_TARGET = re.compile(r"→\s*§\s*(.+?)\s*$", re.M)
# Each entry is a shape that reads as a fact but silently decays into a false one.
_CLAUDE_BANNED = {
    "line-number reference": re.compile(r"\.(?:py|ts|tsx):\d+"),
    "R-NN rule tag": re.compile(r"(?<![A-Z])R-\d\d\b"),
}


def _claude_headings(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    text = path.read_text(encoding="utf-8")
    return {re.sub(r"[`*]", "", h).strip() for h in _CLAUDE_HEADING.findall(text)}


def _claude_anchors(path: Path) -> set[str]:
    """Headings PLUS bold paragraph leads — both are cited with `§` in this repo."""
    if not path.is_file():
        return set()
    text = path.read_text(encoding="utf-8")
    return _claude_headings(path) | {
        re.sub(r"[`*_]", "", b).strip() for b in _CLAUDE_BOLD_LEAD.findall(text)
    }


def _resolve_doc_ref(root: Path, tracked_md: list[str], citing: str, target: str) -> str | None:
    """Relative to the citing file, then to the repo root, then unique-suffix.

    The suffix step is what the repo actually writes — `optimization/CLAUDE.md`, `roadmap.md` —
    and it must stay UNIQUE rather than basename-only, because twelve files are named
    `CLAUDE.md` and a basename match would resolve any of them to whichever came first.
    """
    for cand in ((root / citing).parent / target, root / target):
        try:
            rel = cand.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        if rel in tracked_md:
            return rel
    hits = [m for m in tracked_md if m == target or m.endswith("/" + target)]
    return hits[0] if len(hits) == 1 else None


def test_claude_md_claims_resolve() -> None:
    """A CLAUDE.md pointing at a section, file or anchor that does not exist.

    Silent by construction, and in the one direction that matters: nothing reads these
    files but an agent, and an agent that follows a dead pointer does not raise — it
    reads a rule that is not there, or fails to find one that is, and then edits the
    code accordingly. Every gate stays green throughout. The same reasoning that put
    `test_every_test_named_by_the_package_exists` in this file: a claim like this is
    false exactly the way "covered" reads when nothing ran.

    Four assertions, each a shape that decayed in practice:

    1. `§ Name` after a link resolves to a heading in the file that link named. Two had
       gone dead (a `structured-output.md` section renamed out from under its reference,
       and a pre-flight-gate bullet that lost its letter) while two more that LOOK dead
       resolve fine — so this must be resolve-or-fail, never a ban on the syntax.
    2. Every `](path)` and `](path#anchor)` resolves on disk.
    3. Every `## Load-bearing` right-hand side is a heading in the SAME file. This is what
       stops the card becoming a second owner of the rule it indexes: the only content it
       may carry is a string that also exists as a heading.
    4. No banned token. A `file.py:120` reference rots on the next edit to that file and
       cannot be checked by reading it; an `R-NN` tag cites a rule registry this repo does
       not have.

    (4) runs over **every tracked `docs/**/*.md` and `docs/**/*.yaml`**, not just the
    CLAUDE.md tree, because both its bans govern all docs rather than the contract-file
    shape. Scope has now been the failure twice, the same way each time — a ban is written
    for the whole docs tree and then applied to a subset of it, so whatever sits outside
    rots with the gate green. The CLAUDE.md-only scope let `code-debt-cleanup.md` collect
    nine line refs, every one stale (`select_round_subset` cited at :225, actually at
    :806), plus twelve `R-NN` tags outliving the registry that defined them
    (`.claude/skills/potter-dev/rules.md`, gone 2026-06-18). The `.md`-only scope that
    replaced it then let the control-plane spec keep three refs into routes that had been
    migrated away entirely — `active.py:236` and `:254` now point at an unrelated handler,
    `backends.py:58` at another. Each now says the thing by name instead.

    (1) and (2) run over the same wide scope, for the third instance of that one failure: a
    pointer between two `docs/` pages is the same claim as a pointer out of a CLAUDE.md, and
    checking only the latter meant a page could be renamed, folded or deleted while every
    sibling that linked it kept a dead link with the gate green. Only (3) stays CLAUDE.md-
    scoped, because the `## Load-bearing` card is a contract-file shape and nothing else has
    one.

    Two more spellings of the same claim, both found unscanned while the tree was being
    consolidated. `.claude/**/*.md` joins (1) and (2) because a skill file is read by exactly
    the reader this test exists for — `potter-self/SKILL.md` carried a link out through an
    absolute home-directory path that resolved inside the repo to nothing, and said so twice.
    And (5): a published-front-page link cannot be relative, because README renders off-repo,
    so `README.md` spells its doc pointers as `github.com/…/blob/main/<path>` — twenty-six of
    them, which the relative arm skips on the `http` guard. That is the same claim as `](…)`
    wearing a hostname, and it is the one nothing could catch: rename a doc and the buyer-
    facing page 404s while every gate stays green. (5) runs over every tracked `.md`, since
    any file may cite the published spelling.

    What it cannot catch, so nobody reads a green as more than it is: a count that is
    simply wrong, semantic drift in a claim about behaviour, two plausible owners for one
    rule, and — self-concealingly — an UNTRACKED CLAUDE.md, since `git ls-files` cannot
    see one. That last gap is the same shape as the NUL scan's above, and it is why a
    gitignored copy of `datasets/CLAUDE.md` shipped in the wheel unnoticed: fully visible
    to every tool an agent uses, invisible to every tool built on git.
    """
    root = Path(__file__).resolve().parents[1]
    tracked = _tracked_files(root)

    def slug(heading: str) -> str:
        return re.sub(r"[^a-z0-9\- ]", "", heading.lower()).replace(" ", "-")

    broken: list[str] = []
    for rel in (
        p
        for p in tracked
        if p.endswith("CLAUDE.md")
        or ((p.startswith("docs/") or p.startswith(".claude/")) and p.endswith(".md"))
    ):
        path = root / rel
        text = path.read_text(encoding="utf-8")

        for target, anchor in _CLAUDE_LINK.findall(text):
            if target.startswith(("http", "mailto")):
                continue
            dest = path if not target else (path.parent / target).resolve()
            if not dest.exists():
                broken.append(f"{rel}: link -> {target}")
            elif (
                anchor
                and dest.is_file()
                and slug(anchor) not in {slug(h) for h in _claude_headings(dest)}
            ):
                broken.append(f"{rel}: anchor -> {target or '(self)'}#{anchor}")

        for target, name in _CLAUDE_SECTION.findall(text):
            name = name.strip().rstrip("*_ ")
            dest = path if not target else (path.parent / target).resolve()
            if not dest.is_file():
                continue
            if not any(h.lower().startswith(name.lower()) for h in _claude_headings(dest)):
                broken.append(f"{rel}: § {name!r} is not a heading in {target or '(self)'}")

        if rel.endswith("CLAUDE.md"):
            own = _claude_headings(path)
            for card in _CLAUDE_CARD.findall(text):
                for entry in _CLAUDE_CARD_TARGET.findall(card):
                    if re.sub(r"[`*]", "", entry).strip() not in own:
                        broken.append(f"{rel}: Load-bearing -> § {entry!r} is not a heading here")

        for label, pattern in _CLAUDE_BANNED.items():
            broken += [f"{rel}: {label} {hit!r}" for hit in pattern.findall(text)]

    for rel in (p for p in tracked if p.startswith("docs/") and p.endswith(".yaml")):
        text = (root / rel).read_text(encoding="utf-8")
        for label, pattern in _CLAUDE_BANNED.items():
            broken += [f"{rel}: {label} {hit!r}" for hit in pattern.findall(text)]

    for rel in (p for p in tracked if p.endswith(".md")):
        text = (root / rel).read_text(encoding="utf-8")
        for target, anchor in _GITHUB_BLOB.findall(text):
            dest = root / target
            if not dest.exists():
                broken.append(f"{rel}: blob url -> {target}")
            elif (
                anchor
                and dest.is_file()
                and slug(anchor) not in {slug(h) for h in _claude_headings(dest)}
            ):
                broken.append(f"{rel}: blob anchor -> {target}#{anchor}")

    tracked_md = [p for p in tracked if p.endswith(".md")]
    scannable = [p for p in tracked if not p.startswith("webapp/node_modules")]

    # (6) A doc path cited from CODE. This is the third instance of the scope lesson above, one
    # level down: cycle-fixtures.md (deleted) left live pointers in `fixtures.ts` and
    # `vitest.config.ts` with every gate green, because nothing outside markdown was ever read.
    for rel in (p for p in scannable if p.endswith(_CODE_SUFFIXES)):
        for line in (root / rel).read_text(encoding="utf-8").split("\n"):
            if _NARRATED_DEATH.search(line):
                continue
            for target in set(_CODE_DOC_PATH.findall(line)):
                if target in _RUNTIME_ARTIFACT_MD:
                    continue
                if _resolve_doc_ref(root, tracked_md, rel, target) is None:
                    broken.append(f"{rel}: code -> {target}")

    # (7) The UNLINKED backticked-path + section spelling, over every tracked text file. chat-foundation.md
    # was cited as §7, §6 and §4a from three files while only ever having §0-§4 — all three in this
    # spelling, which arm (1) cannot see because it requires a preceding `](link)`.
    for rel in scannable:
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for target, name in _CODE_SECTION.findall(text):
            dest = _resolve_doc_ref(root, tracked_md, rel, target)
            if dest is None:
                continue
            name = name.strip().rstrip("*_ ")
            anchors = _claude_anchors(root / dest)
            if not any(
                a.lower().startswith(name.lower()) or name.lower().startswith(a.lower())
                for a in anchors
            ):
                broken.append(f"{rel}: `{target}` § {name!r} is not an anchor there")

    assert not broken, (
        "CLAUDE.md claim(s) that do not resolve:\n  " + "\n  ".join(sorted(broken)) + "\n"
        "An agent following one of these reads a rule that is not there, silently. Fix the "
        "pointer, or delete the claim — never relax the check."
    )


def test_every_test_named_by_the_package_exists() -> None:
    """A docstring that names an enforcement test which was never written.

    Same silent-harm class as the NUL scan above, and the same reason it must be a
    scan: the harm is that a later reader *trusts the lock and stops checking*. Four
    of these were live — ``test_no_direct_archive_access_outside_facade``,
    ``test_no_raw_httpexception_in_api``, ``test_every_injection_renderer_is_wired``,
    ``test_no_bare_string_decision_kinds``. Two of them named a rule that IS enforced,
    just somewhere else (an import-time assert; a typed parameter); one named a rule
    nothing enforced at all, and a fourth write slipped past that facade for a year.
    Nothing fails when a claim like this is false — it simply reads as covered.

    Scope is package code + ``CLAUDE.md``, the two places that make binding claims.
    Prose docs are excluded on purpose: they say "a ``test_structure`` scan" meaning
    the kind, not the file, and an allowlist to tell those apart would be the same
    unchecked claim in a new place.
    """
    root = Path(__file__).resolve().parents[1]
    tracked = _tracked_files(root)

    defined: set[str] = set()
    for rel in tracked:
        if rel.startswith("tests/") and rel.endswith(".py"):
            defined.add(Path(rel).stem)  # a module may be cited as a whole
            body = (root / rel).read_text(encoding="utf-8")
            defined |= set(re.findall(r"^\s*(?:async\s+)?def (test_[a-z0-9_]+)", body, re.M))

    claimed: dict[str, set[str]] = {}
    for rel in tracked:
        if not ((rel.startswith("promptpotter/") and rel.endswith(".py")) or "CLAUDE.md" in rel):
            continue
        text = (root / rel).read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"\btest_[a-z0-9_]+", text):
            claimed.setdefault(m.group(0).removesuffix("_py"), set()).add(rel)

    missing = {name: sorted(where) for name, where in claimed.items() if name not in defined}
    assert not missing, (
        f"named test(s) that do not exist: {missing}. Delete the claim or name the real "
        "enforcement — an import-time assert and a typed signature are both better locks "
        "than a test, but neither is the test the docstring promised."
    )


def test_declared_command_payloads_match_their_models() -> None:
    """The spec DECLARES a payload and the model ENFORCES it; a field on one side only is a
    promise nobody keeps, and it is silent in both directions. A spec-only field is one the
    browser is told to send and the server 422s as undeclared; a model-only field is one the
    server accepts while the document that is supposed to be the closed inbound set never
    named it. Three had already drifted, including a `pattern` the model deliberately refuses
    to carry so that ONE slug rule governs both ingest and mint.

    Names, requiredness, and any declared ``pattern`` — six of those were spec-only, refusing
    nothing. Numeric and length bounds are deliberately NOT compared: restating them here would
    rebuild the duplication this test exists to police, and a bound the spec understates is a
    documentation nit rather than a promise the server breaks.
    """
    from promptpotter.presentation.api.middleware.command_dispatcher import (
        PAYLOAD_MODEL_FOR_KIND,
    )

    doc = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "docs/specs/m12-api-openapi.yaml").read_text(
            encoding="utf-8"
        )
    )
    schemas = doc["components"]["schemas"]

    def flatten(node: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
        """``$ref`` and ``allOf`` resolved to (declared properties, required names)."""
        if "$ref" in node:
            return flatten(schemas[node["$ref"].rsplit("/", 1)[-1]])
        props: dict[str, Any] = {}
        required: set[str] = set()
        for part in node.get("allOf", [node]):
            if "$ref" in part or "allOf" in part:
                p, r = flatten(part)
            else:
                p, r = dict(part.get("properties", {})), set(part.get("required", ()))
            props |= p
            required |= r
        return props, required

    def model_pattern(model: Any, field: str) -> str | None:
        return next(
            (m.pattern for m in model.model_fields[field].metadata if getattr(m, "pattern", None)),
            None,
        )

    mismatched: list[str] = []
    for kind, model in sorted(PAYLOAD_MODEL_FOR_KIND.items()):
        operation = doc["paths"][f"/commands/{kind}"]["post"]
        body = operation["requestBody"]["content"]["application/json"]["schema"]
        payload = next(
            part["properties"]["payload"]
            for part in body.get("allOf", [body])
            if "payload" in part.get("properties", {})
        )
        properties, required = flatten(payload)
        declared = set(properties)
        enforced = set(model.model_fields)
        enforced_required = {n for n, f in model.model_fields.items() if f.is_required()}
        if declared != enforced:
            mismatched.append(
                f"{kind}: spec-only={sorted(declared - enforced)} "
                f"model-only={sorted(enforced - declared)}"
            )
        if required != enforced_required:
            mismatched.append(
                f"{kind}: required spec={sorted(required)} model={sorted(enforced_required)}"
            )
        for name in sorted(declared & enforced):
            spec_pattern = properties[name].get("pattern")
            if spec_pattern != model_pattern(model, name):
                mismatched.append(
                    f"{kind}.{name}: pattern spec={spec_pattern!r} "
                    f"model={model_pattern(model, name)!r}"
                )
    assert not mismatched, f"declared payload != enforced payload: {mismatched}"


def test_declared_command_kinds_match_the_wired_set() -> None:
    """A command kind that LOOKS live in the spec but is not wired, or the reverse.

    ``m12-api-openapi.yaml`` is schema-first by design: a kind is declared before its
    handler exists, and those carry ``x-status: declared-not-wired``. That makes the
    ABSENCE of the marker a positive claim — "this one is live" — and nothing checked
    it. The claim is read by humans and by us: `chat-foundation.md` once advertised
    `endorse-candidate` as a shipped UI affordance on the strength of this file alone.

    Silent in both directions, which is why it is here rather than left to fail loud.
    A stale "live" declaration surfaces only when someone POSTs it and gets a 404
    `command_kind_unknown` — after the button was built. A wired-but-undeclared kind
    is worse and quieter: an inbound mutation reaching the dispatcher with no entry in
    the document that is supposed to be the closed inbound set, so it never went
    through the declare-and-review step ADR-0001 requires.

    It cannot be an import-time assert beside the registry (the pattern `tests/CLAUDE.md`
    prefers): the fact lives in a 175 KB YAML doc, and no production module should read
    the repo's own doc bytes at import to answer it. Same reasoning as the NUL scan above.
    """
    from promptpotter.presentation.api.routers.commands import _WIRED_KINDS, commands_router

    # Kinds served by their own typed route rather than the generic ``POST /commands/
    # {kind}`` dispatcher, so they are absent from ``_WIRED_KINDS``. Read off the router
    # itself: a hand-written set here would leave a NEW typed route in neither half of the
    # comparison, and pass — which is exactly the quiet direction the docstring names.
    typed_routes = {
        route.path.rsplit("/", 1)[1]
        for route in commands_router.routes
        if "{" not in getattr(route, "path", "{")
    }
    wired = set(_WIRED_KINDS) | typed_routes

    spec_path = Path(__file__).resolve().parents[1] / "docs" / "specs" / "m12-api-openapi.yaml"
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))

    declared_live: set[str] = set()
    declared_not_wired: set[str] = set()
    for path, operations in spec["paths"].items():
        if not path.startswith("/commands/"):
            continue
        kind = path.rsplit("/", 1)[1]
        for operation in operations.values():
            if not isinstance(operation, dict):
                continue
            target = (
                declared_not_wired
                if operation.get("x-status") == "declared-not-wired"
                else declared_live
            )
            target.add(kind)

    assert not (declared_live - wired), (
        f"declared live in m12-api-openapi.yaml but NOT wired: {sorted(declared_live - wired)}. "
        "A POST to one 404s `command_kind_unknown`. Either wire it, or mark the operation "
        "`x-status: declared-not-wired`."
    )
    assert not (wired - declared_live), (
        f"wired but not declared live: {sorted(wired - declared_live)}. Every inbound command "
        "is declared in m12-api-openapi.yaml BEFORE its handler lands (ADR-0001). Add the "
        "operation, or drop the `x-status: declared-not-wired` marker it still carries."
    )
    assert not (declared_not_wired & wired), (
        f"marked `declared-not-wired` but actually wired: {sorted(declared_not_wired & wired)}. "
        "The marker is now a lie in the safe direction — drop it."
    )


def test_declared_reads_are_served_paths() -> None:
    """The other half of the contract: a GET the spec promises must exist on the app.

    The command half above is checked against a Python set; the reads had nothing, and
    they drift in a way no reader notices. Four of them spelled their path parameters
    ``{campaignId}``/``{cycleId}`` while FastAPI served ``{campaign_id}``/``{cycle_id}``
    — a document describing an API nobody could call at those URLs, valid YAML, no test
    unhappy. Fixed by hand once; this is what keeps it fixed.

    ``openapi.generated.json`` is the app's own answer (``scripts/build_openapi.py``,
    regenerated and diffed in CI), so this compares the promise against the routing
    table rather than against a second document. The ``/api/v1`` prefix is the mount
    point: the spec declares paths relative to it, the app serves them under it.

    Deliberately paths only. Schemas and parameters are NOT compared — the two documents
    describe the API at different altitudes, and ``build_openapi.py`` says why a
    disagreement there is information rather than drift. A promised path that does not
    exist is not a disagreement; it is a dead link.
    """
    spec_path = Path(__file__).resolve().parents[1] / "docs" / "specs" / "m12-api-openapi.yaml"
    generated = Path(__file__).resolve().parents[1] / "docs" / "specs" / "openapi.generated.json"
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    served = set(json.loads(generated.read_text(encoding="utf-8"))["paths"])

    dead = sorted(
        f"GET {path}"
        for path, operations in spec["paths"].items()
        if isinstance(operations.get("get"), dict)
        and operations["get"].get("x-status") != "declared-not-wired"
        and f"/api/v1{path}" not in served
    )
    assert not dead, (
        f"declared in m12-api-openapi.yaml but served at no such path: {dead}. Either the "
        "path parameter is spelled differently from the handler's argument (the camelCase "
        "case), the route moved, or the operation needs `x-status: declared-not-wired`."
    )


def _fake_connector(name: str, **overrides: Any):
    """A minimally valid Connector, so a test can bend exactly one field."""
    from promptpotter.connectors.protocol import Connector

    return Connector(
        name=name,
        wire_adapter=lambda query, params: {"q": query},
        session_factory=lambda: object(),
        extract_experiment=lambda raw: ([], []),
        **overrides,
    )


class _FakeEntryPoint:
    """Stands in for an installed distribution's entry point (`.name`/`.value`/`.dist`)."""

    def __init__(self, dist: str, obj: Any, *, boom: Exception | None = None) -> None:
        self.name = "label"
        self.value = f"{dist.replace('-', '_')}:CONNECTOR"
        self.dist = type("D", (), {"name": dist})()
        self._obj = obj
        self._boom = boom

    def load(self) -> Any:
        if self._boom is not None:
            raise self._boom
        return self._obj


def test_third_party_connectors_load_and_are_held_to_the_same_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `promptpotter.connectors` entry point is published (stable-api.md §1).

    Four rules, and every one of them fails SILENTLY if it is dropped from ``_load``:
    a plugin that never loads looks like a plugin nobody installed; a plugin that
    shadows ``termnorm``/``promptpotter`` replaces an object the loop reaches by name
    (``runner/inner/tasks.py`` reads ``CONNECTORS["promptpotter"]``); a plugin that
    skips ``_validate`` registers half-wired and fails a campaign instead of an import;
    and a plugin whose module raises, if it were merely skipped, comes back later as an
    unexplained ``connector 'x' not registered``.

    The group name itself is asserted because renaming it un-registers every plugin in
    the world at once — it is a published string, not an implementation detail.
    """
    import promptpotter.connectors as reg

    assert reg.ENTRY_POINT_GROUP == "promptpotter.connectors"

    def _with(eps: list[Any]):
        monkeypatch.setattr(reg, "entry_points", lambda group: eps if group else [])
        return reg._load()

    # 1. A valid plugin joins the registry, keeps the built-ins, and records its origin.
    registry, origins = _with([_FakeEntryPoint("acme-backend", _fake_connector("acme"))])
    # Derived from `_BUILTIN`, never a hand-authored name list: this asserts that a plugin JOINS
    # the built-ins, and spelling them out made a third connector look like a regression.
    assert set(registry) == {*reg._BUILTIN, "acme"}
    assert registry["promptpotter"] is reg._BUILTIN["promptpotter"]
    # Format pinned, not merely probed for a substring: `stable-api.md` §1 publishes it as
    # the way to trace a name that greps to nothing, and it is the entry point's VALUE that
    # says which object was imported — its label ("label" here) is free and identifies nothing.
    assert origins["acme"] == "acme-backend: acme_backend:CONNECTOR"
    assert origins["termnorm"] == "built-in"

    # 2. Shadowing a built-in is refused — this is the one that would swap the L4 object.
    for shipped in reg._BUILTIN:
        with pytest.raises(RuntimeError, match="may not replace a built-in"):
            _with([_FakeEntryPoint("evil", _fake_connector(shipped))])

    # 3. The SAME validator that guards our two runs over theirs. `in_process` without
    #    `in_process_run` is the invariant BackendClient.run_query depends on.
    with pytest.raises(RuntimeError, match="requires in_process_run set"):
        _with([_FakeEntryPoint("acme", _fake_connector("acme", execution="in_process"))])

    # 4. A broken or wrongly-typed entry point is fatal at import, not skipped.
    with pytest.raises(RuntimeError, match="failed to import"):
        _with([_FakeEntryPoint("acme", None, boom=ImportError("plugin is broken"))])
    with pytest.raises(RuntimeError, match=r"not a promptpotter\.connectors\.Connector"):
        _with([_FakeEntryPoint("acme", {"not": "a connector"})])


# --- Root resolution -------------------------------------------------------
# Where the package reads its config and writes the operator's data. Silent by
# construction: a root that resolves to the WRONG EXISTING directory raises
# nothing -- the package reads something, writes somewhere, the run completes.
# Both shipped bugs were this shape. Campaigns landed in ``site-packages``,
# which pip deletes on upgrade; and the optimizer enumerated
# ``site-packages/datasets`` -- the HuggingFace library's own directory, a
# declared extra of this project -- offering ``arrow_dataset.py`` as a
# candidate dataset. The suite runs from a checkout, where all three roots
# resolve to the repo and every branch below is dead, so these fake the
# ABSENCE of a checkout: the installed shape, unreachable from a normal run.


@pytest.fixture
def unbound_roots():
    """``source_checkout_root`` is ``lru_cache``d — without this a test that moves
    ``PACKAGE_ROOT`` reads the previous test's answer. Not autouse: it would then
    fire for every test in this file, which resolves roots for real."""
    from promptpotter.config import paths

    paths.source_checkout_root.cache_clear()
    yield paths
    paths.source_checkout_root.cache_clear()


def _fake_install(paths: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A package dir with no ``pyproject.toml`` beside it — i.e. a wheel."""
    package = tmp_path / "site-packages" / "promptpotter"
    package.mkdir(parents=True)
    monkeypatch.setattr(paths, "PACKAGE_ROOT", package)
    paths.source_checkout_root.cache_clear()
    return package


def _fake_checkout(
    paths: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, name: str
) -> Path:
    """A package dir whose parent carries a ``pyproject.toml`` naming *name*."""
    root = tmp_path / "checkout"
    package = root / "promptpotter"
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text(f'[project]\nname = "{name}"\n', encoding="utf-8")
    monkeypatch.setattr(paths, "PACKAGE_ROOT", package)
    paths.source_checkout_root.cache_clear()
    return root


def test_a_checkout_is_recognised_by_project_name_not_by_a_bare_marker(
    unbound_roots: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``site-packages`` is a directory anyone may drop a ``pyproject.toml`` into, and
    mistaking a foreign one for this repo puts user data back inside the install."""
    paths = unbound_roots
    root = _fake_checkout(paths, tmp_path, monkeypatch, name="promptpotter")
    assert paths.source_checkout_root() == root

    _fake_checkout(paths, tmp_path / "other", monkeypatch, name="somebody-elses-package")
    assert paths.source_checkout_root() is None

    _fake_install(paths, tmp_path / "installed", monkeypatch)
    assert paths.source_checkout_root() is None


def test_user_data_root_resolves_env_then_checkout_then_app_data(
    unbound_roots: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All three branches. The middle one keeps an existing checkout — and
    ``deploy-linux/``, which runs from one — writing exactly where it always has."""
    paths = unbound_roots
    root = _fake_checkout(paths, tmp_path, monkeypatch, name="promptpotter")

    monkeypatch.delenv("PROMPTPOTTER_HOME", raising=False)
    assert paths.user_data_root() == root / ".promptpotter"

    monkeypatch.setenv("PROMPTPOTTER_HOME", str(tmp_path / "elsewhere"))
    assert paths.user_data_root() == (tmp_path / "elsewhere").resolve()

    monkeypatch.delenv("PROMPTPOTTER_HOME")
    _fake_install(paths, tmp_path / "installed", monkeypatch)
    assert "site-packages" not in paths.user_data_root().parts


def test_benchmark_root_is_install_content_and_never_the_huggingface_package(
    unbound_roots: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A checkout reads ``datasets/``; a wheel reads its own staged assets.

    The banned answer gets its own assertion because it is a REAL directory whenever
    the ``[benchmarks]`` extra is installed — a wrong path that exists is the failure
    mode with no symptom.
    """
    paths = unbound_roots
    root = _fake_checkout(paths, tmp_path, monkeypatch, name="promptpotter")
    assert paths.benchmark_datasets_root() == root / "datasets"

    package = _fake_install(paths, tmp_path / "installed", monkeypatch)
    resolved = paths.benchmark_datasets_root()
    assert resolved == package / "assets" / "benchmarks"
    assert resolved != package.parent / "datasets"


def test_only_the_optimizer_manifest_may_be_shadowed(
    unbound_roots: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One FILE, not the directory: a directory-level override would silently invite a
    hand-written ``resolved_schemas.json``, which is generated from the Pydantic models."""
    paths = unbound_roots
    package = _fake_install(paths, tmp_path, monkeypatch)
    home = tmp_path / "home"
    monkeypatch.setenv("PROMPTPOTTER_HOME", str(home))

    assert paths.optimizer_pipeline_path() == package / "assets" / "optimizer" / "pipeline.yaml"

    override = home / "optimizer" / "pipeline.yaml"
    override.parent.mkdir(parents=True)
    override.write_text("nodes: {}\n", encoding="utf-8")
    assert paths.optimizer_pipeline_path() == override
    assert paths.optimizer_assets_root() == package / "assets" / "optimizer"


def test_one_dataset_name_rule_reaches_every_entry_point() -> None:
    """A slug ingest mints must be a slug the wire will mint a campaign against.

    They were two patterns — ``_DATASET_NAME_PATTERN`` in the commands router and
    ``validate_dataset_name`` in the store — and they disagreed on a leading digit,
    so ``2024-sales.csv`` uploaded fine and then 400'd at mint. Silent because each
    surface looked correct alone.

    The rule is also lowercase-only, which is the half that never fired: a dataset
    name IS a directory name, and on Windows/macOS ``Foo`` and ``foo`` are one
    directory while ``slug_exists`` would answer for two.
    """
    from promptpotter.application.datasets.draft_campaign import default_slug_from_filename
    from promptpotter.presentation.api.middleware.command_dispatcher import (
        MintCampaignPayload,
        ReplaceDatasetPayload,
    )

    for filename in ("2024-sales.csv", "Q3_report.csv", "customers.csv"):
        slug = default_slug_from_filename(filename)
        validate_dataset_name(slug)  # the ingest path's gate
        # Both wire payloads that name a dataset defer to that ONE rule rather than restating it.
        assert MintCampaignPayload(dataset_name=slug).dataset_name == slug
        assert ReplaceDatasetPayload(slug=slug).slug == slug

    for bad in ("Foo", "UPPER", "-leading", "_leading", "has space", "has.dot", ""):
        with pytest.raises(ValueError):
            validate_dataset_name(bad)


def test_every_persisted_timestamp_is_canonical_utc() -> None:
    """``+00:00`` and ``Z`` are the same instant and sort in opposite orders.

    ``created_at`` is compared as a STRING in six places, one of which
    (``routers/origins.py``) uses ``min()`` to elect an origin group's canonical
    campaign — so a campaign minted through a path that skipped ``utcnow_iso``
    silently outranked one minted a second earlier. Nothing errors; the wrong
    campaign is simply named canonical.
    """
    assert utcnow_iso().endswith("Z")
    same_instant = ("2026-08-01T10:00:00+00:00", "2026-08-01T10:00:00Z")
    assert min(same_instant) != max(same_instant), "the two spellings do not sort equal"

    # Scoped to ONE file, this could not see the two sites that were actually wrong:
    # `evidence.py` and `files.py` both minted `+00:00`. The rule is
    # about the SPELLING, so the check is `.isoformat()` on a datetime anywhere in the
    # package — `strftime` (id suffixes, explicit `Z`) and `.timestamp()` (epoch floats)
    # are different jobs and stay legal.
    pkg = Path(__file__).resolve().parents[1] / "promptpotter"
    offenders = [
        str(py.relative_to(pkg))
        for py in pkg.rglob("*.py")
        if py.name != "clock.py"
        and re.search(
            r"\bdatetime\.(now|fromtimestamp|utcnow)\([^)]*\)\.isoformat",
            py.read_text(encoding="utf-8"),
        )
    ]
    assert not offenders, (
        f"{offenders} mint a timestamp outside shared/clock.py — use utcnow_iso() for now, "
        "iso_z(dt) for an instant you already hold. Bare .isoformat() writes '+00:00'."
    )


def _tree_bytes(root: Path) -> dict[str, bytes]:
    """Every file under *root*, keyed by relative path — a byte-exact snapshot."""
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def test_committed_framing_never_writes_into_install_content(built_stores: Any) -> None:
    """Framing committed for a benchmark lands in the TENANT tree, never beside the definition it
    was derived from — and the ONE reader finds it there.

    Four of the shipped benchmarks ship a ``task_description.md`` with no ``task_context.yaml``,
    ``email-tagging`` (the one ``docs/manual/02-install.md`` opens with) among them. Under a wheel
    the definition dir resolves inside ``site-packages``: pip deletes it on upgrade and a system
    install refuses the write. The ROW half of this rule got a test when the rows moved out; this
    is the half that shipped without one.
    """
    from promptpotter.application.optimization.task_context import committed_task_context

    install = built_stores.benchmarks_root / "gsm8k"
    install.mkdir(parents=True)
    (install / "pipeline.yaml").write_text("backend_type: local\n", encoding="utf-8")
    (install / "task_description.md").write_text("Solve grade-school math.\n", encoding="utf-8")
    before = _tree_bytes(install)

    built_stores.tenant_datasets.save_task_context("gsm8k", {"domain": "grade-school arithmetic"})

    assert _tree_bytes(install) == before, (
        "the decomposition was written into benchmarks_root — install content, "
        "read-only under a wheel (site-packages)"
    )
    assert built_stores.tenant_datasets.task_context_path("gsm8k").is_file()
    # The one reader every seam uses — identity, C0, seed-screen — resolves tenant-first.
    assert committed_task_context(built_stores, "gsm8k").domain == "grade-school arithmetic"


def test_inner_campaign_id_separates_two_candidates_and_is_stable() -> None:
    """A cell's inner campaign is addressed by CONTENT, and two candidates must not collide.

    Silent harm, and the reason this key could not simply be the ``cycle_id``: that id is a
    benchmark-CELL hash, so C0, C1.1 and C1.2 measuring seed-3 all derive the same one
    (``cycle_19ab182342b7`` is shared by four campaigns on disk). The optimizer-prompt
    overrides are the only thing that tells the candidates apart, so a key that dropped them
    would file two candidates' inner runs under one campaign — and because
    ``_open_inner_campaign`` CONTINUES an existing campaign, the second candidate would
    inherit the first's banked rounds and be scored on a trajectory it never ran. No error,
    no missing directory: just one candidate's measurement reported as another's.

    Stability across processes matters for the same reason from the other side: a key that
    varied per process would never find its own campaign, so every retry would re-mint and
    the continuation would silently never happen.
    """
    from promptpotter.application.runner.inner.spawn import inner_campaign_id
    from promptpotter.application.runner.inner.tasks import InnerTaskSpec

    spec = InnerTaskSpec(
        inner_dataset="justlogic-d234", seed=3, n_samples=28, n_rounds=4, n_variants=3
    )
    c1 = {"l1_generate": {"instruction": "widen the axes"}}
    c2 = {"l1_generate": {"instruction": "narrow the axes"}}

    assert inner_campaign_id(spec, c1) != inner_campaign_id(spec, c2), (
        "two candidates differing only in one override field share a campaign — "
        "their measurements merge under one id with no error"
    )
    # The origin (no overrides) is a third distinct arm, not a nameless default.
    assert (
        len({inner_campaign_id(spec, c1), inner_campaign_id(spec, c2), inner_campaign_id(spec, {})})
        == 3
    )
    # A different cell of the SAME candidate is a different campaign too.
    assert inner_campaign_id(spec.model_copy(update={"seed": 6}), c1) != inner_campaign_id(spec, c1)
    # Recomputation is stable, and key order in the override dict is not part of the identity.
    assert inner_campaign_id(spec, c1) == inner_campaign_id(spec, dict(c1))
    assert inner_campaign_id(spec, {"a": {"x": "1"}, "b": {"y": "2"}}) == inner_campaign_id(
        spec, {"b": {"y": "2"}, "a": {"x": "1"}}
    )

    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from promptpotter.application.runner.inner.spawn import inner_campaign_id;"
            "from promptpotter.application.runner.inner.tasks import InnerTaskSpec;"
            "s=InnerTaskSpec(inner_dataset='justlogic-d234',seed=3,n_samples=28,n_rounds=4,n_variants=3);"
            "print(inner_campaign_id(s,{'l1_generate':{'instruction':'widen the axes'}}))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == inner_campaign_id(spec, c1), (
        "the campaign key is not stable across processes — every retry would re-mint "
        "instead of continuing, and the banked rounds would be orphaned in silence"
    )


def test_unscoreable_cells_counts_holes_but_not_stops_or_deprecated_rows() -> None:
    """A HOLE is a cell that was attempted and returned nothing — not a stop, not a retry.

    The silent direction is the false NEGATIVE. If this stops recognising an errored row,
    the panel gate never fires and rounds resume being elected on incomplete comparisons —
    which is the original defect, and it ran a whole campaign without anyone noticing: two
    of C1.1's six cells returned no measurement, it was ranked against a rival measured on
    a different five, and it won.

    The two near-misses are pinned in the other direction because the obvious arithmetic
    (``scored_samples - total``) counts both, and both occur on real runs:

    * a PoBB-eliminated candidate simply stopped early — those cells were never attempted;
    * a *deprecated* row is a sample the classifier marked fatal — ``content_empty`` where
      the retry beside it never produced an answer either. It carries no ``error_category``
      and is already excluded from ``total``, so the arithmetic form would halt an otherwise
      healthy cycle; through the L4 recursion a halted inner cycle is itself unscoreable, so
      one such sample would take the whole outer run down.

    The row that motivated this guard was NOT of that kind: it carried ``content_empty`` and
    answered on the retry, and only reached here because ``classify_result`` read an
    attempt-level advisory as a verdict on the result. That is fixed at the predicate now
    (``domain/rendering.py``), so a recovered retry is an ordinary scored row and never needs
    this protection — which stays, for samples that really did come back empty.
    """
    from promptpotter.application.optimization.pobb.classification import is_deprecated
    from promptpotter.config.settings import NO_RESULT
    from promptpotter.domain.results import unscoreable_cells

    def row(sample_id: int, **extra: Any) -> dict[str, Any]:
        return {"sample_id": sample_id, "predicted": "TRUE", **extra}

    # The real C1.1 shape: six attempted cells, two returned no measurement.
    holed = [row(i) for i in range(4)] + [
        row(4, predicted="ERROR", error_category="UNKNOWN", error="ran past its deadline"),
        row(5, predicted="ERROR", error_category="UNKNOWN", error="ran past its deadline"),
    ]
    assert unscoreable_cells(holed) == 2

    # The real C1.2 shape: PoBB cut it at five cells. Complete, not holed.
    assert unscoreable_cells([row(i) for i in range(5)]) == 0
    assert unscoreable_cells([]) == 0

    # The real inner C2.2 shape: a fatal-classified transient with NO error_category. The
    # retry left nothing extractable, which is what separates it from the recovered row in
    # ``test_content_empty_on_a_result_that_answered_is_not_an_empty_response``.
    deprecated = row(
        22,
        predicted=NO_RESULT,
        pipeline_data={
            "diagnostics": {
                "warnings": [
                    {
                        "step": "llm_only",
                        "code": "content_empty",
                        "message": "finish_reason=error",
                        "kind": "transient",
                    }
                ]
            }
        },
    )
    assert is_deprecated(deprecated), "fixture drift — this row must classify as deprecated"
    assert unscoreable_cells([row(0), row(1), row(2), deprecated]) == 0, (
        "a classifier-deprecated sample was counted as a hole — the gate would halt a "
        "healthy cycle on a transient retry the loop already handles"
    )


def test_a_rate_belongs_to_the_provider_model_pair_not_the_model_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A price is a property of WHO billed it. The table registers one model under many
    vendors at prices that differ several-fold, so a model-only lookup answers with
    somebody else's list — and the old chain did exactly that, matching across providers
    by suffix and then by bare substring.

    The row that paid for this: every optimizer call goes to OpenRouter's
    ``deepseek/deepseek-v4-flash``, which is character-for-character DeepSeek's own
    first-party key at $0.14/$0.28 against OpenRouter's listed $0.088/$0.176. ``None`` is
    the honest answer; it arms the "USD cap inactive" warning instead of quoting a 1.6x
    guess as a measurement.

    This docstring used to add "OpenRouter returns no wire cost on that route", and that
    was never true — the route reports ``cost`` on every call and our own client dropped
    it before anyone downstream could read it (see
    ``test_wire_cost_reaches_the_response_or_nothing_prices_the_optimizer``). The estimate
    was the only number because of a bug on THIS side, and an explanation naming upstream
    is why nobody went looking for it. The rule below is unaffected: a wire cost overrides
    the table, and where there is none the pair-keyed lookup is still what answers.

    Driven from a FIXTURE table, not the shipped one. The claim is about the resolution
    rule, and pinning it to today's prices makes it assert two things at once — the first
    version read the operator's local ``.promptpotter/rates.json`` (2519 keys) and would
    have gone red against the checked-in bundled floor (2253, no ``deepseek-v4-flash``)
    on CI and on every fresh clone, with its own "table unavailable" guard unable to see
    the difference. Upstream re-keying a model must not be able to red this.
    """
    import promptpotter.shared.pricing as spend_mod

    table = {
        # The defect in one row: DeepSeek's own first-party key, character-for-character
        # OpenRouter's model id, and OpenRouter has NO key of its own here — which is
        # exactly the shipped table's shape for this model, and why the old chain's
        # cross-provider match had something wrong to reach for.
        "deepseek/deepseek-v4-flash": spend_mod.Rate(0.00000014, 0.00000028),
        "openrouter/openai/gpt-oss-20b": spend_mod.Rate(0.00000004, 0.00000015),
        # Groq answers a provider-less model id while the table keys it prefixed.
        "groq/openai/gpt-oss-120b": spend_mod.Rate(0.00000015, 0.0000006),
        # The bare namespace the table keeps first-party OpenAI/Anthropic in, and the only row
        # here carrying cache tiers — reads at 0.1x input, writes at 1.25x.
        "gpt-4o": spend_mod.Rate(0.0000025, 0.00001, 0.000003125, 0.00000025),
    }
    monkeypatch.setattr(spend_mod, "load_rates", lambda: table)
    lookup_rate, compute_usd = spend_mod.lookup_rate, spend_mod.compute_usd

    # 1. The defect. The bare key exists and is a DIFFERENT vendor's price, so a
    #    provider-less lookup answers with it — and asking AS OpenRouter must refuse
    #    rather than quote it, even though OpenRouter's own key is right there.
    assert lookup_rate("deepseek/deepseek-v4-flash") == table["deepseek/deepseek-v4-flash"]
    assert lookup_rate("deepseek/deepseek-v4-flash", "openrouter") is None

    # 2. A routing suffix selects another upstream provider with its own rate (measured ~6x
    #    on a nitro route), so the base-model price is not an approximation of it.
    assert lookup_rate("openai/gpt-oss-20b:nitro", "openrouter") is None

    # 3. Composition still resolves what it should: the provider-prefixed convention...
    assert lookup_rate("openai/gpt-oss-20b", "openrouter") == table["openrouter/openai/gpt-oss-20b"]
    #    ...the wire echoing its own provider back inside the model id...
    assert lookup_rate("groq:openai/gpt-oss-120b", "groq") == lookup_rate(
        "openai/gpt-oss-120b", "groq"
    )
    #    ...and the bare namespace the table keeps first-party OpenAI/Anthropic in.
    assert lookup_rate("gpt-4o", "openai") == table["gpt-4o"]

    # 4. And the provider reaches the pricing call, not just the lookup beneath it.
    assert compute_usd("deepseek/deepseek-v4-flash", 10, 10, provider="openrouter") is None
    assert compute_usd("deepseek/deepseek-v4-flash", 10, 10) is not None

    # 5. A cache read is a SUBSET of the input count, so it is re-priced OUT of it rather than
    #    added on top — 1000 input of which 800 cached bills 200 cold + 800 at the read tier.
    cold = compute_usd("gpt-4o", 1000, 0, provider="openai")
    hit = compute_usd("gpt-4o", 1000, 0, provider="openai", cache_read_tokens=800)
    assert cold == pytest.approx(1000 * 0.0000025)
    assert hit == pytest.approx(200 * 0.0000025 + 800 * 0.00000025)
    assert hit < cold
    #    A model whose table row carries no cache tier bills the read at the INPUT price, so the
    #    number is UNCHANGED rather than silently discounted by a rate nobody sourced.
    assert compute_usd(
        "openai/gpt-oss-20b", 1000, 0, provider="openrouter", cache_read_tokens=800
    ) == pytest.approx(compute_usd("openai/gpt-oss-20b", 1000, 0, provider="openrouter"))


def test_wire_cost_reaches_the_response_or_nothing_prices_the_optimizer() -> None:
    """The provider's own price must survive the client, because on the optimizer route it is
    the ONLY price there is: the rate table has no ``openrouter/deepseek/*`` key and correctly
    refuses to quote DeepSeek's first-party number for an OpenRouter call, so a dropped wire
    cost leaves the call unpriced with no error anywhere.

    That is what happened. ``call.py`` read ``response.usage["cost"]`` while the client built
    ``usage`` from four token keys and never copied it, so every optimizer row on disk carried
    ``cost_usd: null``, ``spend.loop.used_usd`` read $0.00 in every cycle ever run, and
    ``store/account_spend.py::record_cost_usd`` floored each call to 0.0 — a USD ceiling that could not
    see the half of the bill it was capping. Nothing raised; the numbers were simply absent.

    Silent because the shape is right and only the value is missing: an unpriced call and a
    free call are the same row.
    """
    from openai.types.chat import ChatCompletion

    from promptpotter.infrastructure.llm.openai_compat import _attempt_cost, _billed_cost

    def completion(usage: dict[str, object] | None) -> ChatCompletion:
        return ChatCompletion.model_validate(
            {
                "id": "c",
                "object": "chat.completion",
                "created": 0,
                "model": "deepseek/deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "{}"},
                    }
                ],
                **({"usage": usage} if usage is not None else {}),
            }
        )

    # OpenRouter's real shape: `cost` rides as an EXTRA on the usage object (the SDK's models
    # are extra="allow"), beside `cost_details`/`is_byok`. Measured live on this route.
    priced = completion(
        {
            "prompt_tokens": 263,
            "completion_tokens": 152,
            "total_tokens": 415,
            "cost": 7.938e-05,
            "cost_details": {"upstream_inference_cost": 0},
            "is_byok": False,
        }
    )
    assert _attempt_cost(priced) == 7.938e-05

    # A provider that reports nothing (Groq, OpenAI) must yield None, not 0.0 — 0.0 is a
    # measurement and would silently satisfy the cap it should have escalated to the table.
    unpriced = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
    assert _attempt_cost(completion(unpriced)) is None
    assert _attempt_cost(completion(None)) is None

    # A schema-repair retry bills BOTH round-trips, same contract the token sums follow.
    assert _billed_cost(1e-05, 2e-05) == pytest.approx(3e-05)
    # ...and one silent half must not drag a real number down to nothing.
    assert _billed_cost(None, 2e-05) == pytest.approx(2e-05)
    assert _billed_cost(1e-05, None) == pytest.approx(1e-05)
    assert _billed_cost(None, None) is None


def test_an_arm_read_on_two_instruments_is_not_a_replicate(built_stores: Any) -> None:
    """The silent harm: a replicate spread is the cheapest noise reading on the board, and it is
    only noise if the INSTRUMENT held while the arm repeated. The engine's measurement identity
    moves on any panel, layout, estimator or inner-prompt edit, so campaigns sharing an arm across
    a week of commits were never replicates — and the read said "that spread is noise, not an
    effect" over what is actually the engine's own drift. Nothing raises; the number just gets
    believed, and every later power calculation is anchored to it."""
    arm = {"l1_generate": "same-arm-hash"}
    cells = {"q1": 0.20, "q2": 0.80, "q3": 0.50}
    for cid, instrument, shift in [
        ("gsm8k__aaaaaa", "instrument-A", 0.0),
        ("gsm8k__bbbbbb", "instrument-A", 0.05),
        ("gsm8k__cccccc", "instrument-B", 0.11),
    ]:
        _write_campaign(
            built_stores,
            cid,
            "gsm8k",
            created_at=f"2026-01-0{cid[-1]}",
            origin_cells={q: v + shift for q, v in cells.items()},
            arm_hashes=arm,
            instrument_id=instrument,
        )

    held = _evidence(built_stores, ["gsm8k__aaaaaa", "gsm8k__bbbbbb"])
    assert [r.n_instruments for r in held.replicates] == [1]

    # Same arm, one campaign read on a second instrument — the group is still SERVED, because
    # "your replicates were never replicates" is the finding, but it may not read as noise.
    moved = _evidence(built_stores, ["gsm8k__aaaaaa", "gsm8k__bbbbbb", "gsm8k__cccccc"])
    assert [r.n_instruments for r in moved.replicates] == [2]
    assert moved.replicates[0].level_spread > held.replicates[0].level_spread


# --- L4 measurement identity: the freeze ratchet ------------------------------

# What `datasets/promptpotter-self/` currently fingerprints to. A pin, never a target: moving it
# is allowed and sometimes right, but it is a CORPUS RESET and must be paid for on purpose — the
# reason for a move belongs in the commit body, which is what this test's own message asks for.
L4_INNER_ORIGIN = "630b3eeae841"


def test_the_two_optimizer_manifests_describe_one_graph() -> None:
    """`promptpotter-self` IS the optimizer seen as a target, so an edit evolved on either layer
    lifts onto the other only while both declare the same nodes wired the same way. Let the two
    drift and nothing raises: each renders its own correct-looking picture, each keeps scoring,
    and the transfer silently stops being a transfer — a `l2_context` edit measured on the outer
    layer lands on an inner node reached by a different path, or by none.

    The `nodes` + `pipelines` blocks are the whole declaration (`derive_pipeline_view`), so
    comparing the derived graphs compares everything that decides it."""
    from promptpotter.application.optimization.dispatch.llm_call.prompts import optimizer_manifest

    outer = parse_pipeline_response(dict(optimizer_manifest())).view
    inner = _pipeline_schema("promptpotter-self").view
    assert outer is not None and inner is not None

    def graph(view: PipelineView) -> tuple[set[str], set[tuple[str, str, str]]]:
        return (
            {n.id for n in view.nodes},
            {(e.from_, e.to, e.kind) for e in view.edges},
        )

    assert graph(outer) == graph(inner), (
        "the optimizer manifest and datasets/promptpotter-self/pipeline.yaml no longer describe "
        "the same graph. Whichever one you edited, mirror the `nodes` / `pipelines` change onto "
        "the other — until then an optimizer prompt evolved on one layer cannot be lifted onto "
        "the other, and nothing else in the system will say so."
    )


def test_the_l4_measurement_identity_moves_only_when_someone_meant_it() -> None:
    """The silent harm: this fingerprint keys every banked inner campaign, and it is computed from
    the ENGINE — dispatch panel prose, ``NODE_LAYOUTS``, the estimator's own source, the inner
    prompts, the seed roster, the inner benchmark's config. So an ordinary edit to any of those
    voids the whole L4 archive with nothing raised anywhere: the next campaign simply re-measures
    its origin, cannot be compared to the ones before it, and a replicate arm reads its own code
    drift back as noise.

    Nothing else can catch it. The cost lands weeks later as "why does nothing accumulate", which
    is a question about a number that no longer exists to be asked about.

    Re-pin DELIBERATELY, in the commit that moves it, with the reason in the commit body — the
    ``<surface-ledger>`` discipline, applied to measurement identity instead of surface count."""
    from promptpotter.connectors import CONNECTORS

    identity = CONNECTORS["promptpotter"].identity_config(Path("datasets/promptpotter-self"))
    assert identity == {"l1_generate": {"inner_origin": L4_INNER_ORIGIN}}, (
        f"L4 measurement identity moved to {identity}. Every banked inner campaign under "
        f"{L4_INNER_ORIGIN} just stopped replaying, so the next `new promptpotter-self` "
        "re-measures its origin and compares to nothing. If that is what you meant — a batched "
        "inner-side change — re-pin L4_INNER_ORIGIN here and say why in the commit body. If it "
        "is not, the edit reached inside the fingerprint: dispatch/panel prose, NODE_LAYOUTS, "
        "the estimator source (exploration/metrics/selection/proxies), the inner optimizer "
        "prompts, inner_tasks.yaml, or the inner benchmark's pipeline/campaign config. The OUTER "
        "optimizer prompts (assets/optimizer/sets/) are outside it and free to edit."
    )


# --- sample look-ahead: the depth must not reach the record -------------------


class _OrderedFakeBackend:
    """Finishes LATER samples FIRST. With uniform latency two slots complete in submission order
    anyway, so the test would pass without the loop ordering anything.

    ``slowest_last`` inverts that, and the barrier check needs it: when the FIRST sample is the
    last to finish, its group-mates have already drained by the time it is absorbed, so a sliding
    refill and a group barrier are indistinguishable. Absorbing the first while the rest still run
    is the only arrangement in which the two differ."""

    def __init__(self, n: int, *, slowest_last: bool = False) -> None:
        self.slowest_last = slowest_last
        self.n = n
        self.calls: list[int] = []
        self._inflight = 0
        # How many were ALREADY running as each call began — the backend's own witness that a
        # window physically opened, which the walk's declared depth cannot supply. Read its PEAK
        # only: a later reading also falls when a peer retires early, so on a loaded box the tail
        # of this series measures the scheduler rather than the arming.
        self.entries: list[int] = []

    async def measure(self, sample: Sample, session: Any, *, pipeline_params: Any = None) -> Any:
        self.calls.append(sample.id)
        self._inflight += 1
        self.entries.append(self._inflight)
        rank = sample.id if self.slowest_last else (self.n - sample.id + 1)
        try:
            await asyncio.sleep(rank * 0.01)
        finally:
            self._inflight -= 1
        return {
            "sample_id": sample.id,
            "query": sample.query,
            "ground_truth": sample.ground_truth,
            "predicted": sample.ground_truth,
            "fitness": 1.0,
            "objective": 1.0,
            "cached": False,
            "error": None,
            "pipeline_data": {"total_time": 0.5},
        }


class _CutAfter:
    """Stands in for the PoBB gate on the same seam, firing where the test picks rather than
    where a posterior has to be coaxed to."""

    name = "cut_after"

    def __init__(self, n: int) -> None:
        self.n = n

    def check(self, results: list[Any], ci: int, ct: int) -> Any:
        if len(results) < self.n:
            return None
        return EscalationSignal(
            check_name=self.name,
            target=EscalationTarget.ELIMINATE_CANDIDATE,
            check_result={"queries_scored": len(results)},
            candidate_idx=ci,
            candidates_scored=len(results),
            candidates_skipped=0,
        )


async def _walk(
    dataset: list[Sample],
    *,
    armed: int,
    cut_at: int | None,
    max_cells: int = 2,
    arming: str = "round",
    hold: bool = False,
    slowest_last: bool = False,
) -> dict[str, Any]:
    backend = _OrderedFakeBackend(len(dataset), slowest_last=slowest_last)
    request = {"cells": armed}
    # The one seam stubbed; the window, cursors, checkpoints and discard are shipping code.
    with mock.patch.object(query_loop, "measure_sample", backend.measure):
        session = types.SimpleNamespace(
            scoring=types.SimpleNamespace(scorer=lambda r: 1.0, round_scorer=None),
            pause_check=None,
            skip_check=None,
            skip_consume=None,
            budget_tripped=None,
            # The flag's real behaviour: consuming it removes the file, so the next read is 1.
            # A constant would let a `batch` walk re-arm itself group after group and hide the
            # one thing that arming promises — that a press buys exactly the group it released.
            sample_lookahead_check=(lambda: request["cells"]),
            # `hold` is the operator pressing again while a group runs — the arming is back the
            # instant it is spent, which is the only condition under which the group BARRIER is
            # observable at all (spent-and-left-alone drains identically either way).
            sample_lookahead_consume=(lambda: request.update(cells=armed if hold else 1)),
            backend_client=types.SimpleNamespace(
                max_cells_in_flight=max_cells, concurrency_arming=arming
            ),
        )
        depths: list[int] = []
        result = await query_loop.run_query_loop(
            types.SimpleNamespace(pipeline_params={}),
            dataset,
            session,
            cached_sample_results={},
            deprecated_samples={},
            on_sample_scored=None,
            on_sample_starting=lambda q, i, t, sid, depth: depths.append(depth),
            degradation_checks=[_CutAfter(cut_at)] if cut_at else [],
            candidate_idx=0,
            n_total_candidates=1,
            axes=None,
            persist_fresh=lambda rows: {"accuracy": 1.0},
            running_scores=lambda rows: {"accuracy": 1.0},
            on_sample_pre_check=None,
        )
    return {
        "rows": result.results,
        "stop_reason": result.stop_reason,
        "calls": list(backend.calls),
        "entries": list(backend.entries),
        "depths": depths,
        "max_depth": max(depths) if depths else 0,
    }


async def test_sample_lookahead_changes_the_bill_and_never_the_record() -> None:
    """Look-ahead must move the wall clock and NOTHING a measurement is read from.

    Silent by construction: if the second in-flight sample could reach the archive, or shift where a
    candidate is cut, one campaign would record different rows under a throughput toggle with
    nothing raised — and the arming would become a steer, forcing a babysat stamp."""
    dataset = [Sample(id=i, query=f"q{i}", ground_truth=str(i % 2)) for i in range(1, 9)]

    # 1. A candidate that runs to completion records byte-identical rows, and costs the same.
    d1 = await _walk(dataset, armed=1, cut_at=None)
    d2 = await _walk(dataset, armed=2, cut_at=None)
    assert d2["max_depth"] == 2, "arming did not open the window — the rest proves nothing"
    assert d1["max_depth"] == 1
    assert d1["rows"] == d2["rows"]
    assert d1["calls"] == d2["calls"]

    # 2. Absorption is in WALK order even though the backend finished later samples first.
    assert [r["sample_id"] for r in d2["rows"]] == [s.id for s in dataset]

    # 3. A candidate cut mid-walk is cut at the same sample and records the same rows — the
    #    in-flight acquisition is discarded, not appended, and not error-filled twice.
    c1 = await _walk(dataset, armed=1, cut_at=4)
    c2 = await _walk(dataset, armed=2, cut_at=4)
    assert c2["max_depth"] == 2, "window never opened on the cut walk"
    assert c1["stop_reason"] == c2["stop_reason"] == "escalation"
    assert c1["rows"] == c2["rows"]

    # 4. …and the only difference is on the bill: AT MOST one extra call, sometimes none (awaiting
    #    an already-finished task does not yield, so the slot is cancelled before its request went
    #    out). Equality here would pin a scheduling accident; two would mean the window overgrew.
    assert 0 <= len(c2["calls"]) - len(c1["calls"]) <= 1

    # 5. The BACKEND's ceiling binds, not the request: a connector declaring 1 has nothing to
    #    overlap, and the operator cannot arm past what one declaring 2 will hold. This is the
    #    half that used to be answered by `execution != "remote_http"` — a transport fact
    #    standing in for a cost one, which pinned every in-process backend to 1 including the
    #    one whose sample is a whole nested campaign.
    assert (await _walk(dataset, armed=4, cut_at=None, max_cells=1))["max_depth"] == 1
    assert (await _walk(dataset, armed=4, cut_at=None, max_cells=2))["max_depth"] == 2

    # 6. `batch` arming buys exactly the group it released — the operator's whole reason for
    #    picking a number where a round runs hours and cannot bound the press. Three launch
    #    together, the walk drains them before releasing anything, and the arming is spent, so
    #    the remaining five run alone. A sliding window would keep re-filling behind each
    #    absorption and leave "which samples did my press pay for" unanswerable.
    b = await _walk(dataset, armed=3, cut_at=None, max_cells=4, arming="batch")
    assert b["depths"] == [3, 3, 3, 1, 1, 1, 1, 1]
    assert b["rows"] == d1["rows"]

    # 7. A press landing while samples are in flight TOPS THE WINDOW UP rather than waiting for
    #    them to drain — the press exists to shorten the wait. `hold` re-arms the instant the
    #    arming is spent, i.e. an operator pressing again at every boundary. Read against clause
    #    6's identical walk: same arming, same ceiling, and the ONLY difference is the press, so
    #    the two depth series bracket exactly what it buys — held tops up where unheld collapses.
    #    Asserted on `depths` (the window the walk OPENED) rather than on backend-observed
    #    overlap, which is a wall-clock race: an in-flight peer that retires early lowers the
    #    reading without the window having closed, so a loaded box reported a press that never
    #    landed. `entries` still carries the launch burst below — the half it can answer exactly.
    held = await _walk(
        dataset, armed=3, cut_at=None, max_cells=4, arming="batch", hold=True, slowest_last=True
    )
    assert max(held["entries"]) == 3, "the group never physically overlapped"
    assert held["depths"] == [3] * len(dataset)
    assert b["depths"] != held["depths"]
    assert held["rows"] == d1["rows"]

    # 8. …and `round` arming does NOT self-consume here: it is spent by the round that scored
    #    under it (`l1/score/winner.py`), so the walk holds the depth to its own end.
    r = await _walk(dataset, armed=2, cut_at=None, max_cells=4, arming="round")
    assert r["depths"] == [2] * len(dataset)


def test_a_descend_tail_arms_the_inner_cycle_and_never_the_outer() -> None:
    """An inner cycle's id is content-addressed and repeats across sibling sandboxes, so the
    ``(campaign_id, cycle_id)`` every command carries names the OUTER cycle at any depth. Dropping
    the ``descend`` tail therefore does not fail — it silently arms the outer campaign instead of
    the inner run the operator was looking at, and acks ``applied`` either way. Nothing downstream
    can tell the two apart: both addresses resolve, both write a well-formed flag.
    """
    from pydantic import ValidationError

    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.presentation.api.deps import decode_descend
    from promptpotter.presentation.api.middleware.command_dispatcher import (
        DescendableCyclePayload,
        PauseCyclePayload,
        SetSampleLookaheadPayload,
    )

    outer = CycleHop(campaign_id="ppself__aaaaaa", cycle_id="cycle_0")
    wire: dict[str, Any] = {
        "campaign_id": outer.campaign_id,
        "cycle_id": outer.cycle_id,
        "descend": "inner__ppself__aaaaaa::cycle_x",
    }
    armed = SetSampleLookaheadPayload(**wire, cells=3)
    assert isinstance(armed, DescendableCyclePayload), "the tail is declared by TYPE"
    path = (outer, *decode_descend(armed.descend))
    assert path[-1] == CycleHop(campaign_id="inner__ppself__aaaaaa", cycle_id="cycle_x")

    # A kind declaring no tail REFUSES one — `extra="forbid"`, so it needs no list of which
    # kinds descend. Ignoring the key instead is the same misdirection by another route: the
    # command would apply to the outer cycle with the operator's address discarded.
    with pytest.raises(ValidationError):
        PauseCyclePayload(**wire)

    # The tail is spent on the address and never recorded: after resolution `campaign_id` /
    # `cycle_id` ARE the inner cycle's, so a `descend` on the ledger would address it twice.
    assert "descend" not in armed.model_dump()


def _evidence(stores: Any, campaign_ids: list[str], **kwargs: Any) -> Any:
    """Read those campaigns as `campaign:` subjects — one root origin each, which is what every
    assertion below is about."""
    from promptpotter.application.evidence import SubjectSpec, subject_evidence

    return subject_evidence(stores, [SubjectSpec("campaign", c) for c in campaign_ids], **kwargs)


def _write_campaign(
    stores: Any,
    campaign_id: str,
    dataset_name: str,
    *,
    created_at: str,
    origin_cells: dict[str, float | None],
    candidate_cells: dict[str, float] | None = None,
    origin_pipeline_data: dict[str, dict[str, Any]] | None = None,
    candidate_pipeline_data: dict[str, dict[str, Any]] | None = None,
    arm_hashes: dict[str, str] | None = None,
    instrument_id: str | None = None,
) -> None:
    """A minimal campaign tree: manifest + one cycle whose round 0 holds ORDINARY origin rows —
    a `query` and a `fitness`, no `pipeline_data`. That is what every non-L4 campaign on disk
    looks like, and there is none in the dev store to read."""
    from promptpotter.infrastructure.store.layout import campaign_cycles_dir

    root = stores.campaigns._campaigns_root() / campaign_id
    (root / "cycles" / "cycle_0" / "rounds").mkdir(parents=True, exist_ok=True)
    (root / "campaign.json").write_text(
        json.dumps(
            {
                "created_at": created_at,
                # The manifest NAMES the cycle the read opens — a real one always does, and a
                # walk that guessed instead read a fork's origin under its parent's name.
                "root_cycle_id": "cycle_0",
                # The manifest NAMES the cycle the read opens — a real one always does, and a
                # walk that guessed instead read a fork's origin under its parent's name.
                "campaign_config": {"dataset_name": dataset_name},
            },
        ),
        encoding="utf-8",
    )
    pd = origin_pipeline_data or {}
    # Every real row carries the `sample_id` it was measured on — the key a sample-set mask
    # filters by, and without it a fixture cannot express a masked read at all.
    acr: dict[str, list[dict[str, Any]]] = {
        "origin": [
            {
                "query": q,
                "sample_id": i,
                "fitness": v,
                **({"pipeline_data": pd[q]} if q in pd else {}),
            }
            for i, (q, v) in enumerate(origin_cells.items())
        ]
    }
    rounds = campaign_cycles_dir(root) / "cycle_0" / "rounds"
    round_zero: dict[str, Any] = {
        "round": 0,
        "accuracy": 0.5,
        # Round 0 names its single arm on `candidate_scores` exactly as every later round does —
        # `C0`, one entry. A fixture without it can express a document the engine never writes.
        "candidate_scores": [{"candidate_id": "origin", "label": "C0"}],
        "all_candidate_results": acr,
    }
    if arm_hashes is not None:
        round_zero["optimizer_prompt_hashes"] = arm_hashes
    if instrument_id is not None:
        round_zero["pipeline_params"] = {"l1_generate": {"inner_origin": instrument_id}}
    (rounds / "round_0000.json").write_text(json.dumps(round_zero), encoding="utf-8")
    # Every real cycle has one, and `spend` is read off it for the roster's spend column — the
    # run-order confound correlates on that, so without it the fixture cannot exercise it.
    (root / "cycles" / "cycle_0" / "dashboard.json").write_text(
        json.dumps({"rounds": [{"round": 0, "accuracy": 0.5}]}),
        encoding="utf-8",
    )
    if candidate_cells is not None:
        (rounds / "round_0001.json").write_text(
            json.dumps(
                {
                    "round": 1,
                    "candidate_scores": [
                        {
                            "candidate_id": "c1",
                            "label": "C1.1",
                            "pipeline_params_override": {"llm_only": {"temperature": "0.7"}},
                        }
                    ],
                    "all_candidate_results": {
                        "c1": [
                            {
                                "query": q,
                                "fitness": v,
                                **(
                                    {"pipeline_data": (candidate_pipeline_data or {})[q]}
                                    if q in (candidate_pipeline_data or {})
                                    else {}
                                ),
                            }
                            for q, v in candidate_cells.items()
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )


def test_evidence_reads_an_ordinary_campaign_with_no_l4_anywhere(built_stores: Any) -> None:
    """The non-L4 path has no data in any dev store, so nothing else exercises it. If the level
    fallback broke, every ordinary comparison would render an empty chart and no error."""
    cells_a = {"q1": 0.2, "q2": 0.8, "q3": 0.5}
    _write_campaign(
        built_stores, "gsm8k__aaaaaa", "gsm8k", created_at="2026-01-01", origin_cells=cells_a
    )
    _write_campaign(
        built_stores,
        "gsm8k__bbbbbb",
        "gsm8k",
        created_at="2026-01-02",
        origin_cells={"q1": 0.4, "q2": 0.9, "q3": 0.6},
    )

    # The third id answers nothing, and must be NAMED rather than quietly dropped: a selection
    # that thins itself in silence is how a two-campaign reading gets read as a three-campaign one.
    ev = _evidence(built_stores, ["gsm8k__aaaaaa", "gsm8k__bbbbbb", "gsm8k__ghost0"])
    assert ev.unread_subjects == ["campaign:gsm8k__ghost0"]

    # Levels come off each row's own `fitness` — the L4 proxy is simply absent here.
    assert [c.value for c in ev.subjects] == [pytest.approx(1.5 / 3), pytest.approx(1.9 / 3)]
    assert ev.metric.scored_cells == ["q1", "q2", "q3"]
    assert {c.campaign_id for c in ev.subjects} == {"gsm8k__aaaaaa", "gsm8k__bbbbbb"}
    # The plotted values and the merged estimate are fields of ONE row, so no join can put a bar
    # and its interval on two different campaigns.
    assert ev.subjects[0].values == {"q1": 0.2, "q2": 0.8, "q3": 0.5}
    # Two campaigns, three shared cells — the decomposition answers, on fitness cells.
    assert ev.variance is not None and ev.variance.n_cells == 3
    assert ev.power is not None
    assert ev.comparability.reason == "ruler_unstamped"  # no round doc carries a stamp yet
    assert "mean_round_delta" not in json.dumps(ev.model_dump())


def test_evidence_refuses_to_pool_levels_across_datasets(built_stores: Any) -> None:
    """Different datasets measure different things, so the levels are not one quantity — and the
    cells never intersect, which is what makes the decomposition ABSENT rather than empty."""
    _write_campaign(
        built_stores,
        "gsm8k__aaaaaa",
        "gsm8k",
        created_at="2026-01-01",
        origin_cells={"q1": 0.2, "q2": 0.8},
    )
    _write_campaign(
        built_stores,
        "bbeh__cccccc",
        "bbeh",
        created_at="2026-01-02",
        origin_cells={"z1": 0.3, "z2": 0.7},
    )

    ev = _evidence(built_stores, ["gsm8k__aaaaaa", "bbeh__cccccc"])

    assert ev.comparability.reason == "datasets_differ"
    assert ev.comparability.verdict is False
    assert ev.comparability.datasets == ["bbeh", "gsm8k"]
    # Absent, not zeroed: there is no cell both measured, so there is nothing to decompose.
    assert ev.variance is None
    assert ev.power is None
    assert ev.metric.scored_cells == []


def test_latency_reads_banked_step_timings_not_this_replays_wall_clock(built_stores: Any) -> None:
    """The silent one. A cached row banks ``total_time: 0.0`` while ``step_timings`` still carries
    the seconds the work actually cost, so a latency metric built on ``total_time`` reports a
    replayed campaign as near-instant — a plausible number, no error, and wrong. Measured on the
    dev store: one campaign there reads 693.7 s the right way and 222.9 s the wrong way."""
    cells = {"q1": 0.2, "q2": 0.8, "q3": 0.5}
    replayed = {q: {"total_time": 0.0, "step_timings": {"a": 600.0, "b": 400.0}} for q in cells}
    _write_campaign(
        built_stores,
        "gsm8k__aaaaaa",
        "gsm8k",
        created_at="2026-01-01",
        origin_cells=cells,
        origin_pipeline_data=replayed,
    )

    ev = _evidence(built_stores, ["gsm8k__aaaaaa"], metric="latency")

    reading = ev.subjects[0]
    assert reading.value == pytest.approx(1000.0)  # not 0.0, which `total_time` would have given
    assert reading.n_cells == 3
    assert reading.unscorable_cells == []


def test_an_unreadable_channel_is_absent_never_zero(built_stores: Any) -> None:
    """The same silent class one layer down: a row with no ``step_tokens`` must make cost and
    tokens UNAVAILABLE, not free. A zero here would rank an unmeasured campaign cheapest."""
    _write_campaign(
        built_stores,
        "gsm8k__aaaaaa",
        "gsm8k",
        created_at="2026-01-01",
        origin_cells={"q1": 0.2, "q2": 0.8},
    )

    ev = _evidence(built_stores, ["gsm8k__aaaaaa"])
    # Not offered at all — a picker listing a metric nothing here can answer is how an operator
    # reads a wall of "unavailable" and concludes the number is broken.
    assert {m.key for m in ev.metric.catalogue} == {"measurand"}
    assert "cost" not in ev.metric.namespace and "tokens" not in ev.metric.namespace

    # Named anyway, through the composed-expression door, it is UNSCORABLE — never a free zero.
    for channel in ("cost", "tokens"):
        ev = _evidence(built_stores, ["gsm8k__aaaaaa"], metric=f"expr:{channel}")
        reading = ev.subjects[0]
        assert reading.value is None, channel
        assert (reading.n_cells, reading.unscorable_cells) == (0, ["q1", "q2"]), channel
        assert ev.metric.scored_cells == []
        # Absent rather than zeroed all the way down: nothing to decompose, nothing to resolve.
        assert ev.variance is None and ev.power is None


def test_a_cell_that_genuinely_cost_nothing_reads_zero_not_absent(built_stores: Any) -> None:
    """The converse of the rule above, and the easier one to write by accident: `x or y` treats a
    real 0.0 as falsy, so a cell that genuinely cost nothing falls through and reports as one that
    was never priced. Free and unmeasured are different facts and must not render the same."""
    _write_campaign(
        built_stores,
        "gsm8k__aaaaaa",
        "gsm8k",
        created_at="2026-01-01",
        origin_cells={"q1": 0.2, "q2": 0.8},
        origin_pipeline_data={
            q: {"mean_round_delta": 0.3, "inner_spend_usd": 0.0, "inner_tokens": 0}
            for q in ("q1", "q2")
        },
    )

    ev = _evidence(built_stores, ["gsm8k__aaaaaa"], metric="cost")
    assert ev.subjects[0].value == pytest.approx(0.0)
    assert ev.subjects[0].n_cells == 2 and ev.subjects[0].unscorable_cells == []
    assert "cost" in {m.key for m in ev.metric.catalogue}


def test_a_metric_selector_the_layer_cannot_resolve_raises_rather_than_scores(
    built_stores: Any,
) -> None:
    """Every rejection is the CALLER's mistake, surfaced as one. A formula reaching a name no cell
    carries must not fall through to a default measurand and answer under the wrong one."""
    _write_campaign(
        built_stores, "gsm8k__aaaaaa", "gsm8k", created_at="2026-01-01", origin_cells={"q1": 0.2}
    )
    for selector in ("bogus", "expr:", "expr:fitness +", "expr:__import__('os')", "expr:nope * 2"):
        with pytest.raises((ValueError, SyntaxError)):
            _evidence(built_stores, ["gsm8k__aaaaaa"], metric=selector)

    # A cell that divides by zero is UNSCORABLE — not a 400, and not an infinity carried forward.
    ev = _evidence(built_stores, ["gsm8k__aaaaaa"], metric="expr:fitness / (fitness - fitness)")
    assert ev.subjects[0].value is None
    assert ev.subjects[0].unscorable_cells == ["q1"]


def test_pairwise_pairs_on_shared_cells_and_holm_corrects_across_the_table(
    built_stores: Any,
) -> None:
    """Two campaigns, one test; three campaigns, three — and an adjusted value is never smaller
    than the raw one it corrects. A raw p read as if it were the only comparison is the error the
    whole table exists to prevent."""
    cells = {"q1": 0.20, "q2": 0.80, "q3": 0.50, "q4": 0.35}
    for i, (cid, shift) in enumerate(
        [("gsm8k__aaaaaa", 0.0), ("gsm8k__bbbbbb", 0.05), ("gsm8k__cccccc", 0.11)]
    ):
        _write_campaign(
            built_stores,
            cid,
            "gsm8k",
            created_at=f"2026-01-0{i + 1}",
            origin_cells={q: v + shift for q, v in cells.items()},
        )

    two = _evidence(built_stores, ["gsm8k__aaaaaa", "gsm8k__bbbbbb"])
    assert len(two.metric.pairwise) == 1
    assert two.metric.n_tests == 1
    only = two.metric.pairwise[0]
    # `a` precedes `b` in the roster's oldest-first order, so `median_shift = b - a` reads one way.
    assert (only.subject_a, only.subject_b) == (
        "campaign:gsm8k__aaaaaa",
        "campaign:gsm8k__bbbbbb",
    )
    assert only.median_shift == pytest.approx(0.05)
    assert only.n_cells == 4
    assert only.p_adjusted == pytest.approx(only.p_value)  # m=1 is the identity
    # Every cell moved the same way, which is the most an exact test can ever see — so p lands ON
    # the width's floor. A t-test reports 0.0 here, which is resolution four pairs do not hold.
    assert only.p_value == pytest.approx(2 / 2**4)

    three = _evidence(built_stores, ["gsm8k__aaaaaa", "gsm8k__bbbbbb", "gsm8k__cccccc"])
    assert len(three.metric.pairwise) == 3
    for row in three.metric.pairwise:
        assert row.p_value is not None and row.p_adjusted is not None
        assert row.p_adjusted >= row.p_value
        # Four paired cells cannot bracket at 95% without assuming a shape, so no bracket is
        # served. `fmt_ci` renders that as absent, which is the honest reading of this width.
        assert row.ci_lo is None and row.ci_hi is None


def test_one_shared_cell_reports_a_value_and_refuses_an_interval(built_stores: Any) -> None:
    """A bracket from one reading is a fiction, and `fmt_ci`'s rule is that an absent interval must
    READ as absent. Serve the point estimate; serve no bounds and no test."""
    _write_campaign(
        built_stores, "gsm8k__aaaaaa", "gsm8k", created_at="2026-01-01", origin_cells={"q1": 0.2}
    )
    _write_campaign(
        built_stores, "gsm8k__bbbbbb", "gsm8k", created_at="2026-01-02", origin_cells={"q1": 0.6}
    )

    ev = _evidence(built_stores, ["gsm8k__aaaaaa", "gsm8k__bbbbbb"])

    first = ev.subjects[0]
    assert first.value == pytest.approx(0.2)
    assert first.ci_lo is None and first.ci_hi is None
    pair = ev.metric.pairwise[0]
    assert pair.median_shift == pytest.approx(0.4)
    assert pair.p_value is None and pair.p_adjusted is None  # nothing was tested, so no 1.0
    assert ev.metric.n_tests == 0


def test_the_edit_ranking_is_read_on_the_SELECTED_metric(built_stores: Any) -> None:
    """The silent one this collapse exists to kill: the ranking sat under the metric picker and
    answered in level units whatever was picked, so "this edit is worth +0.45" read as seconds on
    a latency read. Same edit, same rows, two metrics — the numbers must differ and each must be
    in its own units."""
    seconds = {q: {"step_timings": {"a": 600.0, "b": 400.0}} for q in ("q1", "q2")}
    faster = {q: {"step_timings": {"a": 400.0, "b": 200.0}} for q in ("q1", "q2")}
    _write_campaign(
        built_stores,
        "gsm8k__aaaaaa",
        "gsm8k",
        created_at="2026-01-01",
        origin_cells={"q1": 0.2, "q2": 0.4},
        origin_pipeline_data=seconds,
        candidate_cells={"q1": 0.6, "q2": 0.9},
        candidate_pipeline_data=faster,
    )

    on_level = _evidence(built_stores, ["gsm8k__aaaaaa"], include_ranking=True)
    assert on_level.edits[0].anchor_effect == pytest.approx(0.45)  # (0.6-0.2 + 0.9-0.4) / 2

    on_latency = _evidence(built_stores, ["gsm8k__aaaaaa"], include_ranking=True, metric="latency")
    # The edit cut 1000 s to 600 s on both cells: seconds SAVED read as a negative effect, and
    # `latency`'s own direction (lower is better) is what tells the reader that is an improvement.
    assert on_latency.edits[0].anchor_effect == pytest.approx(-400.0)
    assert on_latency.edits[0].n_cells == 2


def test_a_cell_no_row_answered_is_not_counted_against_the_metric(built_stores: Any) -> None:
    """`unscorable_cells` is what a surface renders as `x` on the cell axis. A
    row carrying no fitness and no pipeline_data measured NOTHING, so counting it there blames the
    metric for a cell that was never on the board — a plausible number, and no error anywhere."""
    _write_campaign(
        built_stores,
        "gsm8k__aaaaaa",
        "gsm8k",
        created_at="2026-01-01",
        origin_cells={"q1": 0.2, "q2": 0.8, "q3": None},
    )

    row = _evidence(built_stores, ["gsm8k__aaaaaa"]).subjects[0]
    assert (row.n_cells, row.unscorable_cells) == (2, [])
    assert set(row.values) == {"q1", "q2"}


def test_the_measurand_is_named_for_what_the_selection_actually_carries(built_stores: Any) -> None:
    """A cell on the recursion IS a whole inner campaign, so its headline number is that seed's
    lift over its OWN origin; a cell on an ordinary dataset is a sample, which has no origin to
    lift over. One catalogue entry, labelled for what it is — the same number under two names is
    what sent a reader to the wrong column."""
    _write_campaign(
        built_stores,
        "gsm8k__aaaaaa",
        "gsm8k",
        created_at="2026-01-01",
        origin_cells={"q1": 0.2, "q2": 0.8},
    )
    plain = _evidence(built_stores, ["gsm8k__aaaaaa"]).metric.spec
    assert (plain.key, plain.expression, plain.label) == ("measurand", "fitness", "Fitness")

    seeded = {q: {"mean_round_delta": v} for q, v in {"q1": 0.3, "q2": 0.9}.items()}
    _write_campaign(
        built_stores,
        "ppself__bbbbbb",
        "promptpotter-self",
        created_at="2026-01-02",
        origin_cells={"q1": 0.2, "q2": 0.8},
        origin_pipeline_data=seeded,
    )
    inner = _evidence(built_stores, ["ppself__bbbbbb"]).metric.spec
    assert (inner.key, inner.expression, inner.label) == ("measurand", "lift", "Lift over origin")
    # …and the composed score the campaign's own formula made of that lift is a DIFFERENT number,
    # so it is offered beside it rather than as a second name for it.
    ev = _evidence(built_stores, ["ppself__bbbbbb"])
    assert ev.subjects[0].value == pytest.approx(0.6)  # the lift, not the 0.5 fitness
    assert [m.key for m in ev.metric.catalogue][:2] == ["measurand", "fitness"]


def test_a_mixed_selection_offers_only_what_BOTH_campaigns_carry(built_stores: Any) -> None:
    """Availability is INTERSECTED. Unioned, a metric only one campaign carries is offered as a
    comparison — one side plots bars and the other an em-dash, which reads as a result rather than
    as a question that was never asked of it."""
    _write_campaign(
        built_stores,
        "ppself__aaaaaa",
        "shared",
        created_at="2026-01-01",
        origin_cells={"q1": 0.2, "q2": 0.8},
        origin_pipeline_data={q: {"mean_round_delta": 0.3} for q in ("q1", "q2")},
    )
    _write_campaign(
        built_stores,
        "plain__bbbbbb",
        "shared",
        created_at="2026-01-02",
        origin_cells={"q1": 0.4, "q2": 0.9},
    )

    both = _evidence(built_stores, ["ppself__aaaaaa", "plain__bbbbbb"])
    # The seed lift is one campaign's alone, so the measurand falls back to what both answer.
    assert both.metric.spec.expression == "fitness"
    assert "lift" not in both.metric.namespace
    assert all(c.value is not None for c in both.subjects)

    # Alone, that campaign is read on its lift — the intersection is not a global downgrade.
    assert _evidence(built_stores, ["ppself__aaaaaa"]).metric.spec.expression == "lift"


def test_a_backfilled_seed_fact_lands_on_ITS_OWN_cell(built_stores: Any, monkeypatch: Any) -> None:
    """The silent one in the migration: the join back from an outer row to the inner campaign that
    produced it is `(outer campaign, outer cycle, round, task)`, and two campaigns run the SAME
    task names. Keyed on the task alone, every seed inherits some other campaign's numbers —
    plausible, ordered, and wrong, with nothing on screen to say so."""
    from promptpotter.application import restamp

    # `restamp` addresses the trees `config/paths.py` names, from any CWD — point it at this one.
    monkeypatch.setattr(
        restamp, "DEFAULT_PROJECTS_ROOT", built_stores.campaigns._campaigns_root().parent.parent
    )

    for outer, level in (("ppself__aaaaaa", 0.25), ("ppself__bbbbbb", 0.75)):
        _write_campaign(
            built_stores,
            outer,
            "promptpotter-self",
            created_at="2026-01-01",
            origin_cells={"seed-0": 0.5},
            origin_pipeline_data={"seed-0": {"mean_round_delta": 0.1}},
        )
        _write_inner_cycle(built_stores, outer, task="seed-0", origin=level, ended=level + 0.4)

    index = restamp._inner_cycle_index()
    for outer, level in (("ppself__aaaaaa", 0.25), ("ppself__bbbbbb", 0.75)):
        cycle_dir = index[(outer, "cycle_0", 0, "seed-0")]
        facts = restamp._facts_from_inner_cycle(cycle_dir)
        assert facts["inner_origin_level"] == pytest.approx(level)
        assert facts["inner_final_lift"] == pytest.approx(0.4)
        assert facts["inner_campaign_id"] == f"inner__{outer}"


def _write_inner_cycle(
    stores: Any, outer_campaign: str, *, task: str, origin: float, ended: float
) -> None:
    """One inner campaign in the sandbox tree, carrying the `spawned_by` pointer the join reads.
    Sandboxes are a SIBLING of the workspace, which is what a `*`-per-level glob misses."""
    from promptpotter.infrastructure.store.layout import inner_sandboxes_dir

    root = stores.campaigns._campaigns_root().parent.parent
    box = inner_sandboxes_dir(root) / f"inner_{outer_campaign}"
    cyc = box / "t" / "campaigns" / f"inner__{outer_campaign}" / "cycles" / "cycle_x"
    cyc.mkdir(parents=True, exist_ok=True)
    (cyc / "index.json").write_text(
        json.dumps(
            {
                "n_rounds": 3,
                "stop_reason": "max_rounds",
                "spawned_by": {
                    "outer_campaign_id": outer_campaign,
                    "outer_cycle_id": "cycle_0",
                    "round": 0,
                    "task": task,
                },
            }
        ),
        encoding="utf-8",
    )
    (cyc / "dashboard.json").write_text(
        json.dumps(
            {
                "spend": {
                    "total_used_usd": 0.5,
                    "backend": {"input_tokens": 10, "output_tokens": 5},
                },
                "rounds": [
                    {"ability": {"theta": origin}},
                    {"ability": {"theta": ended}},
                ],
            }
        ),
        encoding="utf-8",
    )


def test_a_course_reads_at_the_winner_its_LEDGER_crowned(built_stores: Any) -> None:
    """Two silent ways a branch reads as the wrong thing, and neither errors — the chart just
    plots a candidate that never won.

    The crown is joined on LABEL: a resume re-mints `candidate_id`, so joining on the id resolves
    to nothing and the course silently falls back to its origin, reading a branch that improved as
    one that never moved. And the head is the LAST crowned round, not the best-scoring candidate
    on the branch: a round that HELD crowns nobody, so the head must stay where the run left it.
    """
    from promptpotter.application.evidence import SubjectSpec, subject_evidence

    _write_campaign(
        built_stores,
        "gsm8k__aaaaaa",
        "gsm8k",
        created_at="2026-01-01",
        origin_cells={"q1": 0.2, "q2": 0.4},
        candidate_cells={"q1": 0.6, "q2": 0.9},
    )
    # The ledger crowns `C1.1` at round 1 under a candidate_id it does NOT carry — which is what a
    # resume leaves behind, and the state an id-join reads as "no winner".
    _write_election(built_stores, "gsm8k__aaaaaa", "cycle_0", round_num=1, winner_label="C1.1")

    course = SubjectSpec("course", "gsm8k__aaaaaa", "cycle_0")
    ev = subject_evidence(built_stores, [course], include_trajectory=True)
    head = ev.subjects[0]
    assert head.values == {"q1": 0.6, "q2": 0.9}, "the branch reads at its crowned winner"
    # The channel is named for the BRANCH; the searchpoint it currently reads at is resolved
    # beside it, so "which point am I looking at" needs no trajectory to answer.
    assert (head.label, head.candidate_id) == ("cycle_0", "c1")

    # The branch BEHIND it — origin first, each point on its own cells, so a round that scored a
    # different subset is not redrawn on evidence it never had.
    assert head.trajectory is not None
    assert [(p.round, p.label, p.n_cells) for p in head.trajectory] == [
        (0, "C0", 2),
        (1, "C1.1", 2),
    ]
    assert head.trajectory[0].value == pytest.approx(0.3)

    # A campaign subject is the ORIGIN of the same cycle and stays there — the two are different
    # questions, and collapsing them would make "compare the branches" unaskable.
    root = subject_evidence(built_stores, [SubjectSpec("campaign", "gsm8k__aaaaaa")])
    assert root.subjects[0].values == {"q1": 0.2, "q2": 0.4}
    assert root.subjects[0].trajectory is None

    # One searchpoint, addressed directly: the same rows, keyed and labelled as itself.
    point = subject_evidence(
        built_stores, [SubjectSpec("candidate", "gsm8k__aaaaaa", "cycle_0", "c1")]
    ).subjects[0]
    assert (point.key, point.label, point.values) == (
        "candidate:gsm8k__aaaaaa/cycle_0/c1",
        "C1.1",
        {"q1": 0.6, "q2": 0.9},
    )

    # An id nothing measured is NAMED, never quietly dropped — the same rule a missing campaign
    # rides, one address level down.
    ghost = SubjectSpec("candidate", "gsm8k__aaaaaa", "cycle_0", "nope")
    assert subject_evidence(built_stores, [course, ghost]).unread_subjects == [ghost.key]


def test_a_masked_channel_plots_measurements_and_never_the_full_set(built_stores: Any) -> None:
    """The mask's two silent harms, both of which render as an ordinary bar.

    A sample mask must DROP rows, never re-derive them: a value averaged over 28 samples plotted
    under a 17-sample label is not the answer to "what if we had used fewer", and nothing on the
    chart would say so. And a masked channel must key APART from the unmasked one, or the two
    collapse to one row and the comparison the mask was opened for silently disappears.
    """
    from promptpotter.application.evidence import SubjectSpec, parse_subject, subject_evidence

    _write_campaign(
        built_stores,
        "gsm8k__aaaaaa",
        "gsm8k",
        created_at="2026-01-01",
        origin_cells={"q1": 0.2, "q2": 0.4, "q3": 0.9},
    )
    plain = SubjectSpec("course", "gsm8k__aaaaaa", "cycle_0")
    masked = parse_subject(f"{plain.key};samples=0,1")

    ev = subject_evidence(built_stores, [plain, masked])
    assert [r.key for r in ev.subjects] == [plain.key, masked.key], "the mask is part of the key"
    full, subset = ev.subjects[0], ev.subjects[1]
    assert full.values == {"q1": 0.2, "q2": 0.4, "q3": 0.9}
    assert full.mask is None
    # Rows DROPPED, never re-derived: the third cell is absent rather than folded into a mean
    # that would read as this branch's answer over a set it was never asked about.
    assert subset.values == {"q1": 0.2, "q2": 0.4}
    assert subset.mask is not None and subset.mask.samples == [0, 1]

    # A mask that matches nothing is UNREAD, not an empty bar at zero — the same rule a campaign
    # with no scored origin rides.
    nothing = parse_subject(f"{plain.key};samples=41,42")
    assert subject_evidence(built_stores, [plain, nothing]).unread_subjects == [nothing.key]

    # A lens is a COURSE question — a campaign is an origin no election reaches, and offering it
    # there would answer under a criterion that decided nothing.
    for bad in (
        "campaign:gsm8k__aaaaaa;lens=score:accuracy",
        "course:gsm8k__aaaaaa/cycle_0;lens=abort:all_off",
        "course:gsm8k__aaaaaa/cycle_0;nope=1",
        "course:gsm8k__aaaaaa/cycle_0;in=missing-the-separator",
    ):
        with pytest.raises(ValueError):
            parse_subject(bad)

    # WHERE a subject lives is part of its key. An inner cycle id repeats across sibling `.inner/`
    # sandboxes, so two seeds of one L4 panel are addressed by the same `(campaign, cycle)` pair —
    # drop the sandbox chain from the key and the second silently overwrites the first in the
    # read's own subject map, plotting one seed's cells under both channels' names.
    here = SubjectSpec("course", "inner__x", "cycle_0")
    a = parse_subject(f"{here.key};in=outer__a::cycle_1")
    b = parse_subject(f"{here.key};in=outer__b::cycle_1")
    assert a.key != b.key != here.key
    assert parse_subject(a.key) == a, "the key round-trips, so a served row re-addresses itself"
    assert a.inside[0].campaign_id == "outer__a"
    # Nothing on disk answers either, and an unresolvable sandbox is UNREAD rather than a raise:
    # one dead channel must not take the other channels of the read down with it.
    assert subject_evidence(built_stores, [plain, a]).unread_subjects == [a.key]


def _write_election(
    stores: Any, campaign_id: str, cycle_id: str, *, round_num: int, winner_label: str
) -> None:
    """One `election` record on the cycle's ledger — the only place a crown is written, and the
    only thing that separates a round that elected from one still scoring."""
    from promptpotter.infrastructure.store.layout import CycleLayout, campaign_cycles_dir

    layout = CycleLayout(
        campaign_cycles_dir(stores.campaigns._campaigns_root() / campaign_id) / cycle_id
    )
    layout.ledger.parent.mkdir(parents=True, exist_ok=True)
    with layout.ledger.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {"record_type": "election", "round": round_num, "winner_label": winner_label}
            )
            + "\n"
        )


def test_ranking_is_opt_in_and_opens_no_later_round_until_asked(built_stores: Any) -> None:
    """The one expensive walk. Off, a round-1 document on disk must not reach the response at
    all — a flag that merely hides the result would still have paid for the walk."""
    _write_campaign(
        built_stores,
        "gsm8k__aaaaaa",
        "gsm8k",
        created_at="2026-01-01",
        origin_cells={"q1": 0.2, "q2": 0.4},
        candidate_cells={"q1": 0.6, "q2": 0.9},
    )

    off = _evidence(built_stores, ["gsm8k__aaaaaa"])
    assert off.ranking_computed is False and off.edits == []

    on = _evidence(built_stores, ["gsm8k__aaaaaa"], include_ranking=True)
    assert on.ranking_computed is True and len(on.edits) == 1
    # Paired candidate − origin on the cells both measured: (0.6-0.2 + 0.9-0.4) / 2.
    assert on.edits[0].anchor_effect == pytest.approx(0.45)
    assert on.edits[0].n_cells == 2


def test_composition_selects_round_robin_so_no_panel_starves_the_frame() -> None:
    """The optimizer must never be asked to judge a number it was not told the meaning of.

    Panels used to bound themselves and each bound was chosen alone, so their sum ran ~2x the node
    ceiling: whether a small panel reached the prompt at all depended on how much the large ones
    happened to want that round. Nothing errors when it does not — the model simply reasons without
    its objective, its precision and its caveats, and the round completes looking normal.

    Round-robin over ITEMS is what fixes it: every panel places its first item before any panel
    places its second. Ordering greedily instead — the obvious implementation — fails this.
    """
    from promptpotter.application.optimization.dispatch.bundle import (
        FENCE_CLOSE,
        FENCE_OPEN_PREFIX,
        Item,
    )
    from promptpotter.application.optimization.dispatch.compose import select
    from promptpotter.application.optimization.dispatch.injections.registry import INJECTIONS

    # One panel that would eat any budget, and the short frame panels behind it in layout order.
    big = [Item(f"row {i}: " + "x" * 400, trusted=False) for i in range(12)]
    rendered = {
        "sample_transcripts": big,
        "measurand": [Item("OBJECTIVE: composite_fitness = 0.51")],
        "confounds": [Item("LIVE CAVEATS: COLD RULER")],
        "budget_state": [Item("BUDGET: round 1 of 4")],
    }
    order = ["sample_transcripts", "measurand", "confounds", "budget_state"]

    picked, coverage = select(rendered, order, budget=2_000)

    # The big panel is THINNED, not dropped, and not served whole.
    assert 0 < coverage["sample_transcripts"].placed < coverage["sample_transcripts"].produced
    # …and every short panel behind it still arrived, whole.
    for name in ("measurand", "confounds", "budget_state"):
        assert picked[name] == rendered[name][0].text, f"{name} starved by the panel ahead of it"

    # The composition — not the panel — states what it showed, because only it knows.
    assert "showed" in picked["sample_transcripts"]
    # Its untrusted rows are fenced ONCE, around the surviving run, so the tag cannot be split.
    assert picked["sample_transcripts"].count(FENCE_OPEN_PREFIX) == 1
    assert picked["sample_transcripts"].count(FENCE_CLOSE) == 1

    # Coverage reports SILENCE at zero. `injection_chars` omits empty panels by construction, so a
    # panel that never rendered in a whole campaign read exactly like one nobody put in the layout
    # — which is how one sat on three floors, unfired, for 414 optimizer calls.
    picked2, coverage2 = select({**rendered, "l1_wounds": []}, [*order, "l1_wounds"], budget=2_000)
    assert coverage2["l1_wounds"].produced == 0
    assert picked2["l1_wounds"] == ""

    # A budget too small for even one item yields nothing, never half of one.
    picked3, _ = select(rendered, order, budget=5)
    assert all(v == "" for v in picked3.values())

    # An INDIVISIBLE panel is all-or-nothing at every budget. Half the prompt under edit is not a
    # smaller view of it — every mutation is a whole-field replacement, so a field the generator
    # cannot see is one it overwrites blind. Which panels those are is asked of the kind each
    # signal declares (`InjectionKind.divisible`), never of a list here: the hand-authored set this
    # replaced named `task_context` and silently skipped `rendered_prompt`.
    fields = ("persona", "task_intent", "instruction", "thinking_style", "answer_format")
    edit_order = ["rendered_prompt", "sample_transcripts", "measurand"]
    edit_rendered = {
        **rendered,
        "rendered_prompt": [Item(f"[{f}] " + "y" * 300) for f in fields],
    }
    whole = frozenset(n for n in edit_order if not INJECTIONS[n].kind.divisible)
    assert "rendered_prompt" in whole, "the artifact under edit must never arrive truncated"
    for squeeze in (400, 900, 1_600, 3_000, 6_000):
        _, cov = select(edit_rendered, edit_order, budget=squeeze, exempt=whole)
        for name in whole:
            c = cov[name]
            assert c.placed in (0, c.produced), (
                f"{name} placed {c.placed}/{c.produced} items at budget {squeeze} — "
                "an indivisible panel was served in half"
            )

    # Overrunning the ceiling is silent — the prompt just grows and the round completes — so both
    # surcharges must be priced off what `_emit` writes. An alternating panel is written one fence
    # per run, and only a panel that places something can be thinned.
    alternating = [Item("t" * 80) if i % 2 else Item("u" * 80, trusted=False) for i in range(8)]
    squeezed = {"diagnostics": alternating, **rendered}
    for tight in range(200, 3_400, 37):
        picked4, _ = select(squeezed, ["diagnostics", *order], budget=tight)
        assert sum(len(t) for t in picked4.values()) <= tight, (
            f"composition overran its {tight}-char ceiling"
        )
        for text in picked4.values():
            assert text.count(FENCE_OPEN_PREFIX) == text.count(FENCE_CLOSE)


def test_a_mandatory_panel_outranks_a_discretionary_indivisible_one() -> None:
    """A mandatory name is a promise about the prompt the node RECEIVES. Layout order alone cannot
    keep it: whole panels are placed in an earlier pass than divisible ones, so a mandatory
    DIVISIBLE panel loses to every discretionary INDIVISIBLE one wherever the layout puts it.
    Nothing raises when that happens — a refused panel reads exactly like a quiet one.
    """
    from promptpotter.application.optimization.dispatch.bundle import Item
    from promptpotter.application.optimization.dispatch.compose import select

    order = ["mutation_memory", "answer_distribution"]
    rendered = {
        "mutation_memory": [Item("m" * 600)],
        "answer_distribution": [Item("header"), Item("a" * 200), Item("b" * 200)],
    }
    exempt = frozenset({"mutation_memory"})

    # The trap must be live, or the assertion below passes without the floor doing anything.
    starved, _ = select(rendered, order, 800, exempt=exempt)
    assert not starved["answer_distribution"], "vacuous — the panel fits without a first claim"

    served, coverage = select(
        rendered, order, 800, exempt=exempt, mandatory=frozenset({"answer_distribution"})
    )
    assert served["answer_distribution"], "a mandatory panel was refused whole"
    assert coverage["answer_distribution"].placed >= 2, "a header alone promises rows it never buys"


def test_a_mandatory_panel_larger_than_the_budget_is_still_served() -> None:
    """First claim on a budget is not the same promise as being present, and reading it as one is
    what shipped: at L4 ``rendered_prompt`` renders the inner optimizer prompts at ~9.1k against a
    ~7.1k injection budget, so it was refused WHOLE on every round and the outer generator spent a
    whole campaign rewriting prompts it had never been shown. The floor is the node's SUBJECT — it
    is admitted whatever it costs.

    And charged SEPARATELY, which is the second half of the same lesson: paying the floor out of the
    discretionary purse only moved which half went missing. `promptpotter-self__d9b228` then ran a
    10.5k floor against the 7k allowance and refused EVERY evidence panel on every call, so the
    generator saw the prompt it was rewriting and nothing it had already measured. A node's
    allowance must mean the same thing at every depth, or its evidence is rationed by frame size.
    """
    from promptpotter.application.optimization.dispatch.bundle import Item
    from promptpotter.application.optimization.dispatch.compose import select

    order = ["rendered_prompt", "mutation_memory"]
    rendered = {
        "rendered_prompt": [Item("s" * 3000)],
        "mutation_memory": [Item("m" * 200)],
    }
    exempt = frozenset({"rendered_prompt"})

    # The trap must be live: under a bounded pass this panel cannot fit at any priority.
    starved, _ = select(rendered, order, 800, exempt=exempt)
    assert not starved["rendered_prompt"], "vacuous — the subject fits inside the budget"

    served, coverage = select(
        rendered, order, 800, exempt=exempt, mandatory=frozenset({"rendered_prompt"})
    )
    assert len(served["rendered_prompt"]) >= 3000, "the node was handed no subject"
    assert coverage["rendered_prompt"].dropped == 0
    # And the evidence beside it survives: the floor does not spend the allowance, so a large
    # subject costs the node nothing it would otherwise have been shown.
    assert served["mutation_memory"], "the floor ate the evidence budget"
    assert coverage["mutation_memory"].dropped == 0


def test_the_l4_generator_is_shown_the_optimizer_prompts_it_rewrites() -> None:
    """The regression for `promptpotter-self__b40e8b`: three rounds and $2.61 spent with
    ``injection_dropped == {'rendered_prompt': 1}`` on every one. The generator's instruction says
    "CURRENT INNER OPTIMIZER PROMPTS below is the text you are rewriting… carry every contract
    forward", and the block was never in the prompt — one candidate was then rejected
    ``guts_inherited_contract`` for shortening a field it had not been shown.

    Composes the real floor layout against an L4-shaped schema, so it fails if the mandatory floor
    ever stops being admitted whatever it costs.
    """
    from promptpotter.application.optimization.dispatch.bundle import (
        OPTIMIZER_DISCRETIONARY_CHARS,
        CycleSlice,
        InjectionBundle,
        RoundDigest,
    )
    from promptpotter.application.optimization.dispatch.facade import (
        DispatchHub,
        injection_coverage_counts,
    )
    from promptpotter.application.optimization.dispatch.llm_call.prompts import (
        load_optimizer_prompt,
    )
    from promptpotter.domain.l1_layout import NODE_LAYOUTS, default_l1_layout
    from promptpotter.domain.opt_search_point import PROMPT_STRING_FIELDS, OptSearchPoint
    from promptpotter.domain.pipeline_schema import PipelineNode, PipelineSchema
    from promptpotter.domain.round_diagnostics import RoundDiagnostics

    fields = list(PROMPT_STRING_FIELDS)
    schema = PipelineSchema(
        name="promptpotter-self",
        version="1",
        nodes=[
            PipelineNode(
                name=name,
                wire_type="llm",
                node_type="",
                param_keys=fields,
                param_types=dict.fromkeys(fields, "string"),
            )
            for name in NODE_LAYOUTS
        ],
    )
    bundle = InjectionBundle(
        opt_sp=OptSearchPoint(),
        pipeline_schema=schema,
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

    subject = DispatchHub.render_items("rendered_prompt", bundle)
    subject_chars = sum(len(i.text) for i in subject)
    allowance = OPTIMIZER_DISCRETIONARY_CHARS["l1_generate"]
    # Non-vacuous: the whole defect is that the node's own subject outweighs the budget the
    # discretionary panels share. If it ever fits, this test proves nothing.
    assert subject_chars > allowance, (
        f"vacuous — the inner optimizer prompts ({subject_chars}c) now fit inside the "
        f"discretionary allowance ({allowance}c), so nothing is being kept against a budget"
    )

    filled, _, rendered, coverage = DispatchHub.fill(
        load_optimizer_prompt("l1_generate"),
        default_l1_layout(),
        bundle,
        node="l1_generate",
    )
    assert "CURRENT INNER OPTIMIZER PROMPTS" in filled.render(), (
        "the generator was handed no subject — it is rewriting text it cannot see"
    )
    assert len(rendered["rendered_prompt"]) >= subject_chars
    starved = set(injection_coverage_counts(coverage)) & NODE_LAYOUTS["l1_generate"].mandatory
    assert not starved, f"mandatory panel(s) refused by the budget: {sorted(starved)}"


def test_a_rewritable_prompt_field_declares_its_ceiling_only_where_it_runs_long() -> None:
    """A model rewriting a prompt field imitates the length it was shown, so it shrinks only when
    handed a number — and the number is prompt text, so it is declared only where the field runs
    long. Both nodes below declare the same params and differ only in carrying a layout, so this
    fails if the ceiling reaches every node and fails if it reaches none.
    """
    from promptpotter.application.optimization.dispatch.bundle import (
        OPTIMIZER_PROMPT_FIELD_MAX_CHARS,
    )
    from promptpotter.application.optimization.dispatch.l1_wire_schema import (
        build_l1_response_schema,
    )
    from promptpotter.domain.l1_layout import NODE_LAYOUTS
    from promptpotter.domain.pipeline_schema import PipelineNode, PipelineSchema

    bounded, unbounded = "instruction", "persona"
    assert bounded in OPTIMIZER_PROMPT_FIELD_MAX_CHARS
    assert unbounded not in OPTIMIZER_PROMPT_FIELD_MAX_CHARS

    def node(name: str) -> PipelineNode:
        return PipelineNode(
            name=name,
            wire_type="llm",
            node_type="",
            param_keys=[bounded, unbounded],
            param_types={bounded: "string", unbounded: "string"},
        )

    optimizer = next(n for n in NODE_LAYOUTS)
    ordinary = "llm_only"
    assert NODE_LAYOUTS.get(ordinary) is None
    schema = build_l1_response_schema(
        PipelineSchema(name="t", version="1", nodes=[node(optimizer), node(ordinary)]),
        citable_fields=["critique"],
        n_variants=1,
    )
    per_node = schema["properties"]["variants"]["items"]["properties"]["pipeline_params_override"][
        "properties"
    ]

    ceiling = OPTIMIZER_PROMPT_FIELD_MAX_CHARS[bounded]
    assert per_node[optimizer]["properties"][bounded].get("maxLength") == ceiling
    assert "maxLength" not in per_node[ordinary]["properties"][bounded], (
        "an ordinary node's instruction stays under 650c unasked — declaring a ceiling there "
        "spends prompt text on a bound that never binds"
    )
    assert "maxLength" not in per_node[optimizer]["properties"][unbounded], (
        "a field the corpus shows is already short takes no entry, and the table is the only "
        "thing that decides which do"
    )


def test_digest_reads_the_ruler_off_the_cycle_not_the_unabsorbed_round() -> None:
    """`absorb_round` stamps a round's ruler identity AFTER the critique call, so on that path the
    round document still reads cold. Sourced from it, `confounds` told the distiller "COLD RULER"
    on every warm round — a panel stating a falsehood, with no symptom anywhere to catch it."""
    from unittest.mock import Mock

    from factories import round_result

    from promptpotter.application.campaign_config import CampaignConfig, OptimizationConfig
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.optimization.dispatch.facade import build_bundle
    from promptpotter.application.optimization.dispatch.injections.panels import _r_confounds
    from promptpotter.domain.ruler import DeltaRuler

    session = Mock()
    session.scoring.scorer_cell_formula = "fitness"
    session.spend_used = None
    warm = DeltaRuler(
        delta={1: -0.5, 2: 0.5, 3: 1.0},
        delta_se={},
        discrimination={},
        mu_delta=0.0,
        sigma_delta=1.0,
        sigma_theta=1.0,
        calibration_model="1PL",
        anchor_id="anchor-x",
        anchored_at_round=1,
    )
    cycle = Cycle(
        session=session,
        config=CampaignConfig(optimization=OptimizationConfig(degradation_threshold=0.05)),
        rounds=[round_result(0)],
        ruler=warm,
    )
    # Exactly what `run_l1_critique` is handed: the round the loop has not folded in yet — so it
    # carries no reading of its own, and the digest's can only have come off the cycle.
    latest = round_result(
        1,
        results=[
            {"sample_id": s, "fitness": f, "objective": f}
            for s, f in ((1, 1.0), (2, 0.0), (3, 1.0))
        ],
    )
    assert latest.ability is None
    bundle = build_bundle(cycle, latest_round=latest)

    ability = bundle.digest.ability
    assert ability is not None
    assert ability.scale() == "ruler anchor-x, 3 cells, 1PL"
    assert "COLD RULER" not in _r_confounds(bundle)


def test_a_fold_carries_the_offset_it_is_of_and_the_tail_resumes_there(tmp_path: Path) -> None:
    """A materialized fold must say WHICH MOMENT it is of, or nothing can join it back to the
    chronology it came from.

    ``DerivedView.on_record`` opened with ``del offset`` for as long as the class existed, so no
    state written to disk recorded where on the ledger it stood. The SSE snapshot is the one
    consumer that needed the number, so it invented a private ``snapshot_at_offset`` — measuring a
    DIFFERENT thing. ``snapshot_frame`` parked the tail at END-OF-FILE while the body it shipped
    was a DEBOUNCED write reflecting an earlier offset, so every record between the two reached
    neither half: the snapshot did not carry it and the tail began after it. Silent, and bounded
    by nothing the reader controls — a client attaching mid-burst lost the whole burst.
    """
    import json as _json

    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.phases import PhaseEvent
    from promptpotter.domain.run_records import PhaseRecord
    from promptpotter.infrastructure.ledger import CycleEventLog
    from promptpotter.infrastructure.projections.base import DerivedView
    from promptpotter.infrastructure.projections.event_stream import CycleLedgerTail
    from promptpotter.infrastructure.store.layout import CycleLayout

    assert PhaseEvent  # the vocabulary these records speak
    layout = CycleLayout(tmp_path)
    log = CycleEventLog(layout.ledger)

    fold = DerivedView()
    assert fold.at_offset == -1, "nothing folded yet is not offset 0 — 0 is a real record"
    log.bind(fold)
    for i in range(5):
        assert log.append(PhaseRecord(phase="round", event="complete", round=i)) == i
    assert fold.at_offset == 4, "the fold knows the offset of the last record it folded"

    # The body a debounced writer left behind: a fold of offsets 0..1, while the file already
    # holds 0..4. The tail must pick up at 2 and deliver the three the body does not carry.
    layout.dashboard.write_text(
        _json.dumps({"declared_phase": "running", "at_offset": 1}), encoding="utf-8"
    )
    hop = CycleHop(campaign_id="c__aaaaaa", cycle_id="cycle_0")
    tail = CycleLedgerTail(tmp_path, hop)
    frame = tail.snapshot_frame()
    assert frame.payload["snapshot_at_offset"] == 2
    assert [e.sequence for e in tail.read_new()] == [2, 3, 4], (
        "the records the debounced body had not folded are DELIVERED, not skipped"
    )

    # A body naming no offset — a warming shape, or a file written before the fold stamped one —
    # still parks at end-of-file, which is what it did all along.
    layout.dashboard.write_text(_json.dumps({"declared_phase": "running"}), encoding="utf-8")
    assert CycleLedgerTail(tmp_path, hop).snapshot_frame().payload["snapshot_at_offset"] == 5


def test_a_replayed_fold_rebuilds_the_trajectory_and_a_forks_history_is_bounded_on_its_own(
    tmp_path: Path,
) -> None:
    """A fold read back off disk must carry the same trajectory the live one did, and a fork's
    prefix must be cut where the manifest says.

    Two silent losses. ``round:display`` carries the whole ``RoundResult`` on an ``exclude=True``
    field, so a replay saw the headline scalars and NO trajectory row — a past moment served with
    an empty ``rounds[]`` looks like a campaign that measured nothing, and nothing errors. And
    ``forked_at_offset`` counts the parent's OWN records while ``iter`` applied that bound to the
    parent's whole CHAIN, so a fork OF A FORK replayed the first N of its grandparent and dropped
    its parent entirely — a plausible, shorter history in place of the real one.
    """
    from promptpotter.domain.cycle_paths import CycleDir, CycleHop
    from promptpotter.domain.run_records import PhaseRecord
    from promptpotter.infrastructure.ledger import CycleEventLog, open_with_history
    from promptpotter.infrastructure.projections.live_dashboard.view import (
        LiveDashboardView,
        fold_at,
    )
    from promptpotter.infrastructure.store.io import write_json
    from promptpotter.infrastructure.store.layout import CycleLayout

    from .factories import round_result

    cycles = tmp_path / "campaigns" / "c__aaaaaa" / "cycles"
    root_dir = cycles / "cycle_0"
    hop = CycleHop(campaign_id="c__aaaaaa", cycle_id="cycle_0")
    root_layout = CycleLayout(root_dir)
    view = LiveDashboardView(
        CycleDir(root_dir),
        state_path=root_layout.dashboard,
        hop=hop,
        session_id="s1",
        l1_patience=3,
        n_variants=2,
        sp_budget_ttest=5,
        headline_metric="accuracy",
    )
    log = CycleEventLog.open(CycleDir(root_dir))
    log.bind(view)
    for rnd in (0, 1):
        rr = round_result(rnd)
        # The round document is the OTHER carrier of what `live_round_result` holds in memory.
        write_json(root_layout.round_file(rnd), rr.model_dump(), default=str)
        log.append(
            PhaseRecord(
                phase="round",
                event="display",
                round=rnd,
                live_round_result=rr,
                payload={"round_result": {"round": rnd, "accuracy": rr.accuracy}},
            )
        )
    live_rounds = [r.round for r in view.state.rounds]
    assert live_rounds == [0, 1]

    replayed = fold_at(root_dir, hop)
    assert [r.round for r in replayed.rounds] == live_rounds, (
        "the replay rebuilds the trajectory from the round documents, not from a prior dashboard"
    )
    assert replayed.at_offset == view.at_offset

    # Chain: root(2 records) -> mid(cut at 2, 1 own record) -> leaf(cut at 1).
    def _sibling(cycle_id: str, parent: str, cut: int) -> Path:
        d = cycles / cycle_id
        write_json(CycleLayout(d).manifest, {"parent_cycle_id": parent, "forked_at_offset": cut})
        return d

    mid_dir = _sibling("cycle_0_fork_1", "cycle_0", cut=2)
    CycleEventLog.open(CycleDir(mid_dir)).append(
        PhaseRecord(phase="round", event="display", round=2)
    )
    leaf_dir = _sibling("cycle_0_fork_1_fork_2", "cycle_0_fork_1", cut=1)
    CycleEventLog.open(CycleDir(leaf_dir)).append(
        PhaseRecord(phase="round", event="display", round=3)
    )

    walked = [rec.round for _offset, rec in open_with_history(CycleDir(leaf_dir)).iter()]
    assert walked == [0, 1, 2, 3], (
        "the grandparent's records come first WHOLE, then the parent's own up to the cut — "
        "a bound read against the parent's chain would have stopped inside the grandparent"
    )


def test_an_illegal_inner_steer_is_rejected_and_a_real_steer_is_not() -> None:
    """Two edits an L4 candidate can make that score without measuring anything, both silent:
    nothing raises, and each reads as a plausible optimizer improvement in every artifact.

    STOPPING — `mean_round_delta` averages over the inner rounds, so an early-stop rule raises it
    by dropping the flat tail, no better search behind it, and the round crowns it. Verbatim from
    the round-2 candidate that provoked the gate (`C2.1`).

    LEVEL — a seed is one whole inner run, so a rule keyed to the seed panel renders empty where
    it lands: the candidate is unrunnable rather than wrong, and re-measures the parent.
    Verbatim from `C2.2`, and round 1 crowned prose of the same shape before it.

    The other half is the one that costs more to get wrong: a REJECTION is destructive and leaves
    no trace, so legitimate steers carrying the same words must survive."""
    from promptpotter.application.optimization.validators.l1_strict import (
        L1_INNER_STEER_IS_LEGAL,
    )

    c21 = (
        "Critique each inner run's trajectory by monitoring the change in score from round to "
        "round. If the absolute difference between the current round's score and the previous "
        "round's score is less than 0.05 for two consecutive rounds, trigger early stop."
    )
    outcome = L1_INNER_STEER_IS_LEGAL.run({"l1_critique": {"instruction": c21}})
    assert outcome is not None
    # BOTH families, and that is the candidate rather than the gate being loose: it ends the loop
    # AND tells a node that critiques one run to iterate over runs. One reason per family, so L2
    # is handed each defect rather than whichever matched first.
    assert {f.reason for f in outcome.evidence["failures"]} == {
        "steers_inner_stopping",
        "steers_across_seeds",
    }
    assert {f.axis for f in outcome.evidence["failures"]} == {"l1_critique.instruction"}

    c22 = (
        "Generate candidate prompts that differ in their underlying reasoning approach, not just "
        "in wording. For each seed, first examine the critique to identify the specific logical "
        "step the current prompt fails on."
    )
    outcome = L1_INNER_STEER_IS_LEGAL.run({"l1_generate": {"instruction": c22}})
    assert outcome is not None
    (failure,) = outcome.evidence["failures"]
    assert failure.reason == "steers_across_seeds"

    # Carries a stop word AND a round word, and is exactly the edit this loop wants — a token
    # match would convict it. Nothing here ends the loop.
    keep = "Stop proposing the same axis in consecutive rounds; name a new mechanism each round."
    assert L1_INNER_STEER_IS_LEGAL.run({"l1_critique": {"instruction": keep}}) is None

    # The bare word is legitimate one level up and in "seed prompt" — only the QUANTIFIED forms
    # name a collection the inner node cannot see.
    seeded = "Ground each candidate in the seed prompt's own wording before proposing a rewrite."
    assert L1_INNER_STEER_IS_LEGAL.run({"l1_generate": {"instruction": seeded}}) is None

    # Scoped to the inner OPTIMIZER nodes. The same words in a target-pipeline prompt steer a
    # task, not a loop, and there is no round budget for them to reach.
    assert L1_INNER_STEER_IS_LEGAL.run({"llm_only": {"instruction": c21}}) is None


def test_a_gutted_prompt_field_is_rejected_and_a_tightening_is_not() -> None:
    """An override REPLACES its field whole, so a short replacement for a long parent deletes
    every contract the parent carried in plain prose — the output shape, the forbidden moves,
    the evidence it must ground on. `L1_PROMPT_PLACEHOLDERS_INTACT` catches only the contracts
    spelled as `{{slots}}`; the rest raise nothing and read as a bold edit, and the round then
    measures a crippled inner optimizer against a whole one.

    The parent is resolved the way the run resolves it, so the length compared against is the
    text the generator was shown — parent override first, manifest template otherwise."""
    from promptpotter.application.optimization.validators.l1_strict import (
        _GUTTABLE_MIN_CHARS,
        L1_PROMPT_FIELD_NOT_GUTTED,
        _parent_field_text,
    )

    parent = {"l1_critique": {"instruction": "x" * 3000}}

    outcome = L1_PROMPT_FIELD_NOT_GUTTED.run(
        {"l1_critique": {"instruction": "y" * 400}}, pipeline_params=parent
    )
    assert outcome is not None
    (failure,) = outcome.evidence["failures"]
    assert failure.reason == "guts_inherited_contract"
    assert failure.value == "400B replaces 3000B"

    # A real tightening keeps the contracts and survives.
    assert (
        L1_PROMPT_FIELD_NOT_GUTTED.run(
            {"l1_critique": {"instruction": "y" * 2000}}, pipeline_params=parent
        )
        is None
    )

    # A short field cannot be gutted — `thinking_style` is a couple of lines, and every rewrite
    # of one would otherwise convict.
    assert (
        L1_PROMPT_FIELD_NOT_GUTTED.run(
            {"l1_critique": {"thinking_style": "y" * 10}},
            pipeline_params={"l1_critique": {"thinking_style": "x" * 200}},
        )
        is None
    )

    # The manifest fallback is live: with no parent override, the parent is the template's own
    # text and it is long enough to be reachable. Pins the wiring, not a byte count.
    assert len(_parent_field_text("l1_critique", "instruction", None)) > _GUTTABLE_MIN_CHARS


def test_a_round_missing_its_critique_is_re_sent_before_the_generator_reads() -> None:
    """`critique` is L1_MANDATORY, and two paths leave it empty: the producer skips the last round
    of an invocation — which a `resume` then walks past — and a terminal provider failure there is
    absorbed so the round can still close. Five of eleven rounds on `screen-taste-v0__26f943` had
    none, including every one of the four it stalled on, and nothing on any channel said so."""
    import asyncio
    from unittest.mock import Mock, patch

    from factories import round_result

    from promptpotter.application.campaign_config import CampaignConfig, OptimizationConfig
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.optimization.l1 import critique as critique_mod
    from promptpotter.domain.phases import StopLoop, StopReason

    def _cycle(prior: object) -> Cycle:
        session = Mock()
        session.state.cycle_id = "cyc"
        return Cycle(
            session=session,
            config=CampaignConfig(optimization=OptimizationConfig(degradation_threshold=0.05)),
            rounds=[round_result(0), prior],
        )

    rows = [{"sample_id": 1, "fitness": 1.0}]

    # A round that HAS one is never re-sent — the skip must stay free.
    kept = round_result(1, results=rows)
    kept.critique = {"priority_fix": "already here"}
    cycle = _cycle(kept)
    calls: list[int] = []
    with patch.object(critique_mod, "run_l1_critique", side_effect=AssertionError("re-sent")):
        asyncio.run(critique_mod.ensure_prior_critique(cycle))
    assert kept.critique == {"priority_fix": "already here"}

    # A round MISSING one is re-sent, and the result lands on disk — or the next resume pays for
    # a call this one already made, and the round file keeps saying the generator had no steer.
    bare = round_result(1, results=rows)
    bare.critique = None
    cycle = _cycle(bare)

    async def _distil(*_a: object, **_k: object) -> dict[str, str]:
        calls.append(1)
        return {"priority_fix": "distilled late"}

    with patch.object(critique_mod, "run_l1_critique", side_effect=_distil):
        asyncio.run(critique_mod.ensure_prior_critique(cycle))
    assert calls == [1]
    assert bare.critique == {"priority_fix": "distilled late"}
    cycle.session.store.campaigns.save_round_file.assert_called_once()

    # A transient failure is re-sent, not surrendered to: the second attempt carries the round.
    flaky = round_result(1, results=rows)
    flaky.critique = None
    cycle = _cycle(flaky)
    tries: list[int] = []

    async def _second_time(*_a: object, **_k: object) -> dict[str, str]:
        tries.append(1)
        if len(tries) < 2:
            raise RuntimeError("provider down")
        return {"priority_fix": "arrived on the re-send"}

    with patch.object(critique_mod, "run_l1_critique", side_effect=_second_time):
        asyncio.run(critique_mod.ensure_prior_critique(cycle))
    assert len(tries) == 2
    assert flaky.critique == {"priority_fix": "arrived on the re-send"}

    # Exhausting the re-sends HALTS. A round whose generator would run with an empty MANDATORY
    # panel is a compromised decision point, and spending a full panel on it is the waste.
    blind = round_result(1, results=rows)
    blind.critique = None
    cycle = _cycle(blind)
    seen: list[dict[str, object]] = []
    attempts: list[int] = []

    async def _always_down(*_a: object, **_k: object) -> dict[str, str]:
        attempts.append(1)
        raise RuntimeError("provider down")

    with (
        patch.object(critique_mod, "run_l1_critique", side_effect=_always_down),
        patch.object(critique_mod, "emit_round_warning", lambda **kw: seen.append(kw)),
        patch.object(critique_mod, "declare_run_phase", lambda *a, **k: None),
        pytest.raises(StopLoop) as halted,
    ):
        asyncio.run(critique_mod.ensure_prior_critique(cycle))
    assert len(attempts) == critique_mod.CRITIQUE_RESEND_ATTEMPTS
    assert halted.value.reason is StopReason.PAUSED
    assert [w["kind"] for w in seen] == ["l1_critique_unavailable"]
    assert seen[0]["severity"] == "error"
    cycle.session.store.campaigns.save_round_file.assert_not_called()


def test_the_schema_carries_the_datasets_nodes_not_the_backends_whole_inventory(
    tmp_path: Path,
) -> None:
    """A discovered backend answers ``GET /pipeline`` with everything it can serve; the DATASET
    says which of those are this campaign's.

    `screen-taste-v0` declares one `llm_only` ranker and inherited six of TermNorm's other nodes —
    3,789 chars of every optimizer prompt, every round, offering `web_search` and
    `entity_profiling` levers to a film-ranking pipeline. Nothing failed: the model correctly
    ignored them and paid the attention anyway.

    The narrowing is the dataset's declaration UNIONED with the running chain, never
    `active_steps` alone — `promptpotter-self` declares `l2_context` and `l3_plan` off-chain
    deliberately, and dropping them would silently delete the L4 arc's main mutation targets."""
    from promptpotter.application.datasets.prompts import dataset_declared_nodes
    from promptpotter.application.pipeline_resolve import _resolve_active_schema
    from promptpotter.domain.pipeline_parsing import parse_pipeline_response

    def _dataset(nodes: dict[str, Any], pipelines: dict[str, list[str]]) -> Path:
        d = tmp_path / f"ds{len(list(tmp_path.iterdir()))}"
        d.mkdir()
        (d / "pipeline.yaml").write_text(
            yaml.safe_dump({"name": "x", "nodes": nodes, "pipelines": pipelines}),
            encoding="utf-8",
        )
        return d

    # The backend's whole inventory, as discovery returns it.
    backend = parse_pipeline_response(
        {
            "name": "termnorm",
            "version": "v0",
            "description": "",
            "nodes": {
                "llm_only": {"type": "generation", "optimizer": {"param_keys": ["temperature"]}},
                "web_search": {"type": "enrichment", "optimizer": {"param_keys": ["num_results"]}},
                "entity_profiling": {
                    "type": "generation",
                    "optimizer": {"param_keys": ["persona"]},
                },
            },
            "pipelines": {"default": ["llm_only"]},
        }
    )
    assert sorted(backend.node_param_keys()) == ["entity_profiling", "llm_only", "web_search"]

    # A dataset declaring ONE node keeps one — `config: {}` still declares it.
    one_node = _dataset({"llm_only": {"config": {}}}, {"default": ["llm_only"]})
    assert dataset_declared_nodes(one_node) == frozenset({"llm_only"})
    _active, narrowed = _resolve_active_schema(
        backend, exclude=[], narrowing={}, dataset_dir=one_node
    )
    assert sorted(narrowed.node_param_keys()) == ["llm_only"]

    # A node the dataset does not declare but the chain RUNS survives: narrowing may only ever
    # drop what nothing executes.
    two_step = parse_pipeline_response(
        {
            "name": "termnorm",
            "version": "v0",
            "description": "",
            "nodes": {
                "llm_only": {"type": "generation", "optimizer": {"param_keys": ["temperature"]}},
                "web_search": {"type": "enrichment", "optimizer": {"param_keys": ["num_results"]}},
                "entity_profiling": {
                    "type": "generation",
                    "optimizer": {"param_keys": ["persona"]},
                },
            },
            "pipelines": {"default": ["web_search", "llm_only"]},
        }
    )
    _active, kept = _resolve_active_schema(two_step, exclude=[], narrowing={}, dataset_dir=one_node)
    assert sorted(kept.node_param_keys()) == ["llm_only", "web_search"]
    assert "entity_profiling" not in kept.node_param_keys()

    # A dataset with no pipeline.yaml has no opinion — never "no nodes".
    empty = tmp_path / "bare"
    empty.mkdir()
    _active, untouched = _resolve_active_schema(
        backend, exclude=[], narrowing={}, dataset_dir=empty
    )
    assert sorted(untouched.node_param_keys()) == ["entity_profiling", "llm_only", "web_search"]

    # The L4 case my first attempt broke: off-chain optimizer nodes the dataset DECLARES survive.
    pp_self = _pipeline_schema("promptpotter-self")
    assert "l2_context" not in pp_self.active_steps
    _active, l4 = _resolve_active_schema(
        pp_self,
        exclude=[],
        narrowing={},
        dataset_dir=Path(__file__).resolve().parents[1] / "datasets" / "promptpotter-self",
    )
    assert {"l2_context", "l3_plan"} <= set(l4.node_param_keys())
