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
from typing import Any

import pydantic
import pytest
import yaml

from promptpotter.domain.measurement_provenance import grade_run
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.infrastructure.store.io import _YamlDumper, read_yaml, write_yaml
from promptpotter.infrastructure.store.layout import validate_dataset_name
from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.hashing import content_hash


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
    JobSearchPoint(pipeline_params=None)
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
        [("l1_critique", {"model": "deepseek/deepseek-v4-flash:nitro", "max_tokens": floor - 1})]
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
    against the round's parent ``prompt_fields``, kept only when ``composite_ci_lo`` clears the
    matched origin, keyed by the run's answer-space signature so a logic block never reaches a
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
            hits=round(comp * 10),
            total=10,
            prompt_fields={**parent, **fields},  # RESOLVED fields, parent + this candidate's change
            matched_origin_composite=0.50,
            composite_ci_lo=ci_lo,
            composite_ci_hi=ci_lo + 0.1,
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
        enter_instrument_mode(evidence_epoch=frozenset(), optimizer_clamp=None)
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
    # A babysat run (a human edited an engine-locked value, ADR-0005) is forced to
    # C even on the otherwise-clean-A path — else a tainted point reused as clean
    # would silently bias the digest/L4 the same way connector noise does.
    prov = grade_run("optimization_loop", llm_batch, schema, human_intervened=True)
    assert prov.grade == "C" and prov.human_intervened is True


def _seed_graded(
    archive: MeasurementArchive, *, run_id: str, grade: str, terminated_at: str, sample_id: int
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
    _seed_graded(archive, run_id="clean", grade="A", terminated_at="llm_only", sample_id=7)
    _seed_graded(
        archive, run_id="connector", grade="C", terminated_at="token_matching", sample_id=8
    )
    node_configs = [("llm_only", {"model": "X"})]

    everything = archive.load_reusable_results(node_configs, dataset_name="aime")
    assert set(everything) == {7, 8}

    clean_only = archive.load_reusable_results(node_configs, dataset_name="aime", min_grade="A")
    assert set(clean_only) == {7}
    assert clean_only[7]["query"] == "q_clean"


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
    # Both runs measured the SAME cell (sample 1) — the cache is keyed by sample_id, so
    # the question is which row wins it, not whether two text-distinct keys coexist.
    cache = archive.load_reusable_results(query_configs, dataset_name="promptpotter-self")
    assert cache[1]["query"] == "q_short_circuit", (
        "a genuine mid-chain short-circuit inside the trusted prefix should still reuse"
    )
    assert cache[1]["pipeline_data"]["terminated_at"] == "l1_critique"
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
    from promptpotter.application.runner.inner.cycle import _inner_narrative
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
            n_l1_rounds=len(levels),
            best_accuracy=0.5,
            best_round=1,
            origin_accuracy=0.458,
            origin_level=0.458,
            round_adopted_levels=levels,
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
        rendered = _r_task_context(bundle)
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
    from promptpotter.application.optimization.validators.l1_strict import (
        build_l1_response_schema,
        validate_overrides,
    )
    from promptpotter.domain.opt_search_point import fold_schema_descriptions

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
    from promptpotter.application.optimization.validators.l1_strict import (
        build_l1_response_schema,
        validate_overrides,
    )

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
    from promptpotter.application.optimization.dispatch.llm_call.prompts import (
        set_optimizer_prompt_overrides,
    )
    from promptpotter.application.optimization.dispatch.schemas import (
        L1GenerateOutput,
        build_l1_response_model,
    )
    from promptpotter.application.optimization.validators.l1_strict import (
        build_l1_response_schema,
        effective_l1_field_names,
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
        base = CampaignConfig(
            optimization=OptimizationConfig(improvement_threshold=0.01, degradation_threshold=0.05)
        )
        forked, _ = _apply_config_overrides(base, None, ConfigOverrides(schema_field_rename=True))
        assert forked.optimization.schema_field_rename is True
        assert base.optimization.schema_field_rename is False
        inherited, _ = _apply_config_overrides(forked, None, ConfigOverrides(max_rounds=3))
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
        model = build_l1_response_model(effective_l1_field_names())
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
        assert build_l1_response_model(effective_l1_field_names()) is L1GenerateOutput
    finally:
        set_optimizer_prompt_overrides(None)


def test_adopt_advances_identity_and_carries_the_wound_ledger():
    """The single adoption seam (``Cycle.adopt``) — used for an L1 win and an L2/L3
    transition alike — must ADVANCE lineage to the new incumbent (parent = the outgoing
    one) while CARRYING the outgoing incumbent's persistent memory: the wound ledger and
    L2's l1_layout. ``mutate`` deliberately resets those two on a child, so a seam that
    forgets to carry them silently drops the failures the search already paid to discover
    — no error, the next round just re-invites the mistake. The surface the adoption
    OWNS (here task_context) must instead come from the new incumbent.
    """
    from promptpotter.application.optimization.cycle import Cycle

    incumbent = OptSearchPoint(persona="Expert", instruction="Rank.")
    incumbent.memory.wounds.l3_note = "prior failure ledger"
    # An L1 winner is a `mutate` child: it inherits task_context but resets wounds.
    winner = incumbent.mutate(
        source="l1_generate", changes_description="try X", task_context={"domain": "biotech"}
    )
    assert winner.memory.wounds.l3_note == ""  # the reset adopt must repair
    prior_id = incumbent.lineage.id

    cyc = object.__new__(Cycle)
    cyc.opt_sp = incumbent
    cyc.adopt(winner, advanced={"task_context": winner.memory.task_context})

    # Identity advanced to the winner, parented on the outgoing incumbent.
    assert cyc.opt_sp is winner
    assert cyc.opt_sp.lineage.id == winner.lineage.id
    assert cyc.opt_sp.lineage.parent_id == prior_id
    # The wound ledger carried forward (would be silently lost without copy_memory_to).
    assert cyc.opt_sp.memory.wounds.l3_note == "prior failure ledger"
    # The OWNED surface came from the new incumbent, not the carried memory.
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
            hits=0,
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
            hits=6,
            total=10,
        )

    rr = RoundResult(
        round=1,
        label="r1",
        accuracy=0.6,
        hits=6,
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
    from promptpotter.domain.opt_search_point import (
        IDEA_MATCH_MARK,
        same_idea,
    )
    from promptpotter.domain.opt_search_point import (
        idea_fingerprint as _idea_fingerprint,
    )

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

    ``runner/inner/cycle.py`` decides whether to verify the outer observation contract
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
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True
    ).stdout
    offenders: list[str] = []
    for raw in listing.split(b"\x00"):
        if not raw:
            continue
        rel = raw.decode("utf-8", "surrogateescape")
        path = root / rel
        if path.suffix.lower() in _BINARY_SUFFIXES or not path.is_file():
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
       cannot be checked by reading it; an `R-NN` tag cites a rule registry this repo has
       never had (`potter-debt-sweep/SKILL.md` says so itself).

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
    `backends.py:58` at another. Each now says the thing by name instead. The other three
    assertions stay CLAUDE.md-scoped.

    What it cannot catch, so nobody reads a green as more than it is: a count that is
    simply wrong, semantic drift in a claim about behaviour, two plausible owners for one
    rule, and — self-concealingly — an UNTRACKED CLAUDE.md, since `git ls-files` cannot
    see one. That last gap is the same shape as the NUL scan's above, and it is why a
    gitignored copy of `datasets/CLAUDE.md` shipped in the wheel unnoticed: fully visible
    to every tool an agent uses, invisible to every tool built on git.
    """
    root = Path(__file__).resolve().parents[1]
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.split("\n")

    def slug(heading: str) -> str:
        return re.sub(r"[^a-z0-9\- ]", "", heading.lower()).replace(" ", "-")

    broken: list[str] = []
    for rel in (p for p in tracked if p.endswith("CLAUDE.md")):
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

        own = _claude_headings(path)
        for card in _CLAUDE_CARD.findall(text):
            for entry in _CLAUDE_CARD_TARGET.findall(card):
                if re.sub(r"[`*]", "", entry).strip() not in own:
                    broken.append(f"{rel}: Load-bearing -> § {entry!r} is not a heading here")

        for label, pattern in _CLAUDE_BANNED.items():
            broken += [f"{rel}: {label} {hit!r}" for hit in pattern.findall(text)]

    for rel in (p for p in tracked if p.startswith("docs/") and p.endswith((".md", ".yaml"))):
        text = (root / rel).read_text(encoding="utf-8")
        for label, pattern in _CLAUDE_BANNED.items():
            broken += [f"{rel}: {label} {hit!r}" for hit in pattern.findall(text)]

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
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, check=True, text=True
    ).stdout.split()

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
    assert set(registry) == {"termnorm", "promptpotter", "acme"}
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
    root = _fake_checkout(paths, tmp_path, monkeypatch, name="promptpotter-optimizer")
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
    root = _fake_checkout(paths, tmp_path, monkeypatch, name="promptpotter-optimizer")

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
    root = _fake_checkout(paths, tmp_path, monkeypatch, name="promptpotter-optimizer")
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
    from promptpotter.presentation.api.routers.commands import _require_dataset_name

    for filename in ("2024-sales.csv", "Q3_report.csv", "customers.csv"):
        slug = default_slug_from_filename(filename)
        validate_dataset_name(slug)  # the ingest path's gate
        assert _require_dataset_name({"dataset_name": slug}) == slug  # the wire's

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
    # `optimizer_prompt_ranking.py` and `files.py` both minted `+00:00`. The rule is
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


async def test_first_sight_framing_never_writes_into_install_content(
    built_stores: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A benchmark shipping no ``task_context.yaml`` gets one decomposed on first run —
    into the TENANT tree, never beside the definition it was derived from.

    Four of the ten shipped benchmarks are in exactly this state, ``email-tagging``
    (the one ``docs/manual/02-install.md`` opens with) among them. Under a wheel the
    definition dir resolves inside ``site-packages``: pip deletes it on upgrade and a
    system install refuses the write. The ROW half of this rule got a test when the
    rows moved out; this is the half that shipped without one, so the same defect
    survived in the framing path.
    """
    from promptpotter.application.optimization import task_context as tc_mod

    install = built_stores.benchmarks_root / "gsm8k"
    install.mkdir(parents=True)
    (install / "pipeline.yaml").write_text("backend_type: local\n", encoding="utf-8")
    (install / "task_description.md").write_text("Solve grade-school math.\n", encoding="utf-8")
    before = _tree_bytes(install)

    async def _decompose(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"task_context": {"domain": "grade-school arithmetic"}}

    monkeypatch.setattr(tc_mod, "decompose_prompt_fields", _decompose)
    resolved = await tc_mod.load_or_build_task_context(
        built_stores, "gsm8k", campaign_id="c", context=None
    )

    assert resolved.domain == "grade-school arithmetic"
    assert _tree_bytes(install) == before, (
        "the decomposition was written into benchmarks_root — install content, "
        "read-only under a wheel (site-packages)"
    )
    assert built_stores.tenant_datasets.task_context_path("gsm8k").is_file()

    # Second run reads it back with no LLM call at all.
    async def _explode(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise AssertionError("a cached decomposition was recomputed")

    monkeypatch.setattr(tc_mod, "decompose_prompt_fields", _explode)
    again = await tc_mod.load_or_build_task_context(
        built_stores, "gsm8k", campaign_id="c", context=None
    )
    assert again.domain == "grade-school arithmetic"


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
    from promptpotter.application.runner.inner.cycle import inner_campaign_id
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
            "from promptpotter.application.runner.inner.cycle import inner_campaign_id;"
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
    first-party key at $0.14/$0.28 against OpenRouter's listed $0.088/$0.176. OpenRouter
    returns no wire cost on that route, so the estimate was the only number and nothing
    could contradict it. ``None`` is the honest answer; it arms the "USD cap inactive"
    warning instead of quoting a 1.6x guess as a measurement.

    Driven from a FIXTURE table, not the shipped one. The claim is about the resolution
    rule, and pinning it to today's prices makes it assert two things at once — the first
    version read the operator's local ``.promptpotter/rates.json`` (2519 keys) and would
    have gone red against the checked-in bundled floor (2253, no ``deepseek-v4-flash``)
    on CI and on every fresh clone, with its own "table unavailable" guard unable to see
    the difference. Upstream re-keying a model must not be able to red this.
    """
    import promptpotter.shared.spend as spend_mod

    table = {
        # The defect in one row: DeepSeek's own first-party key, character-for-character
        # OpenRouter's model id, and OpenRouter has NO key of its own here — which is
        # exactly the shipped table's shape for this model, and why the old chain's
        # cross-provider match had something wrong to reach for.
        "deepseek/deepseek-v4-flash": (0.00000014, 0.00000028),
        "openrouter/openai/gpt-oss-20b": (0.00000004, 0.00000015),
        # Groq answers a provider-less model id while the table keys it prefixed.
        "groq/openai/gpt-oss-120b": (0.00000015, 0.0000006),
        # The bare namespace the table keeps first-party OpenAI/Anthropic in.
        "gpt-4o": (0.0000025, 0.00001),
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
