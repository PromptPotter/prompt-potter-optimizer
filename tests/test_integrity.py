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
            n_l1_rounds=len(levels),
            best_accuracy=0.5,
            best_round=1,
            origin_accuracy=0.458,
            origin_level=0.458,
            round_discovered_levels=levels,
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


def test_schema_description_axis_reaches_the_model_and_cannot_rename_a_field() -> None:
    """A description edit the optimizer emits must actually reach the schema, and must
    never reach a field NAME.

    Two silent harms, both invisible in a completed run. (1) If the key
    `build_l1_output_schema` grafts onto the emittable surface ever drifts from the key
    `resolve_node_schema_descriptions` reads back, the optimizer spends budget mutating an
    axis nobody consumes, and every such variant scores as a legitimate no-op — the outer
    fitness silently learns from noise. (2) `description` is free precisely because no
    parser reads it; a field NAME is the wire contract. An override that could rename
    `changes_description` would take the parser down with no schema error.
    """
    import json
    from pathlib import Path

    from promptpotter.application.optimization.dispatch.llm_call import (
        set_optimizer_prompt_overrides,
    )
    from promptpotter.application.optimization.dispatch.schemas import L1Variant
    from promptpotter.application.optimization.validators.l1_strict import (
        build_l1_output_schema,
        validate_overrides,
    )
    from promptpotter.domain.pipeline_parsing import parse_pipeline_response

    raw = json.loads(
        (
            Path(__file__).resolve().parents[1] / "datasets/promptpotter-self/pipeline.json"
        ).read_text(encoding="utf-8")
    )
    schema = parse_pipeline_response(raw)

    try:
        # No override bound → the schema is exactly Pydantic's, so C0 never drifts.
        set_optimizer_prompt_overrides(None)
        base = build_l1_output_schema(schema)
        base_props = base["schema"]["properties"]["variants"]["items"]["properties"]
        assert list(base_props) == list(L1Variant.model_fields)

        # The emittable key and the describable vocabulary are both closed.
        emittable = base_props["pipeline_params_override"]["properties"]["l1_generate"]
        grafted = emittable["properties"]["output_schema_descriptions"]
        assert emittable["additionalProperties"] is False
        assert grafted["additionalProperties"] is False
        assert set(grafted["properties"]) == set(L1Variant.model_fields)

        # The grafted key round-trips: what L1 may emit is what the resolver reads back.
        edit = {
            "l1_generate": {
                "output_schema_descriptions": {"changes_description": "EVIDENCE FIRST."}
            }
        }
        assert validate_overrides(edit, schema, forbidden_axes_strict=True) == []
        set_optimizer_prompt_overrides(edit)
        after = build_l1_output_schema(schema)
        after_props = after["schema"]["properties"]["variants"]["items"]["properties"]
        assert after_props["changes_description"]["description"] == "EVIDENCE FIRST."

        # Names are contract: an invented key is dropped, never grafted onto the wire.
        set_optimizer_prompt_overrides(
            {"l1_generate": {"output_schema_descriptions": {"candidate": "rename attempt"}}}
        )
        renamed = build_l1_output_schema(schema)
        renamed_props = renamed["schema"]["properties"]["variants"]["items"]["properties"]
        assert list(renamed_props) == list(L1Variant.model_fields)
        assert "candidate" not in renamed_props

        # The raw schema stays locked — descriptions are the ONLY unlocked schema surface.
        forbidden = validate_overrides(
            {"l1_generate": {"output_schema": {"type": "object"}}},
            schema,
            forbidden_axes_strict=True,
        )
        assert [f.reason for f in forbidden] == ["forbidden_axis"]
    finally:
        set_optimizer_prompt_overrides(None)


def test_nested_param_override_accumulates_instead_of_reverting_its_parent() -> None:
    """A `param_types: object` param merges one level; siblings the child did not name survive.

    The silent harm: a nested param is ONE key in the node config, so the node-level
    `{**existing, **incoming}` spread replaced it whole. A candidate that improved a single
    `output_schema_descriptions` entry silently reverted every entry its parent earned — the
    description axis could not accumulate across generations, and a `--sweep` would have
    measured the merge instead of the lever, then (per `schema-description-axis.md`) reverted
    a correct idea on the strength of a false negative.

    An `array` param must NOT merge: a list is an ordering, and a merged ordering is
    meaningless. `layout`'s per-slot lists are the live case, and this is the same contract
    `resolve_node_layout` states — one nesting contract, not two.

    The declaration is what makes the merge happen, so EVERY nested param the schema grafts
    must be declared. Undeclared, it silently reverts to whole-replace — the same bug, one
    param over. `output_schema_field_names` shipped undeclared and this asserts the pair.
    """
    import json
    from pathlib import Path

    from promptpotter.application.optimization.l1.population import merge_pipeline_params
    from promptpotter.domain.pipeline_parsing import parse_pipeline_response

    raw = json.loads(
        (
            Path(__file__).resolve().parents[1] / "datasets/promptpotter-self/pipeline.json"
        ).read_text(encoding="utf-8")
    )
    schema = parse_pipeline_response(raw)

    # Every nested param the l1_generate schema can graft accumulates, not just the first one.
    for nested in ("output_schema_descriptions", "output_schema_field_names"):
        got = merge_pipeline_params(
            {"l1_generate": {nested: {"a": "A", "b": "B"}}},
            {"l1_generate": {nested: {"a": "A2"}}},
            schema,
        )
        assert got is not None
        assert got["l1_generate"][nested] == {"a": "A2", "b": "B"}, (
            f"{nested} is not declared `param_types: object` — a child override reverts its "
            f"parent's siblings"
        )

    # `object` — the child's key wins, the parent's untouched sibling survives.
    base = {
        "l1_generate": {
            "temperature": 0.7,
            "output_schema_descriptions": {"changes_description": "A", "variant_name": "B"},
        }
    }
    merged = merge_pipeline_params(
        base, {"l1_generate": {"output_schema_descriptions": {"changes_description": "A2"}}}, schema
    )
    assert merged is not None
    assert merged["l1_generate"]["output_schema_descriptions"] == {
        "changes_description": "A2",
        "variant_name": "B",
    }
    assert merged["l1_generate"]["temperature"] == 0.7
    # The origin is never aliased or mutated by a candidate's merge.
    assert base["l1_generate"]["output_schema_descriptions"]["changes_description"] == "A"

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

    # Undeclared param → node-level shallow semantics, unchanged. Depth comes from the
    # declaration, never from sniffing `isinstance`.
    plain = merge_pipeline_params(
        {"l1_generate": {"persona": "x", "instruction": "y"}},
        {"l1_generate": {"persona": "z"}},
        schema,
    )
    assert plain == {"l1_generate": {"persona": "z", "instruction": "y"}}


def test_emittable_param_surface_is_one_set_the_schema_and_the_validator_agree_on() -> None:
    """`node_param_keys` is the single emittable surface — and every reader must read it.

    The silent harm: `validate_overrides` had no membership check, so a param the node never
    advertised merged into `pipeline_params` unchecked and rode to the wire. Unlike a
    hallucinated NODE (which `merge_pipeline_params` drops), an invented PARAM survives, the
    round completes, and the candidate is scored as though the edit were a legitimate axis.
    Nothing raises; the fitness is simply attributed to a param that does not exist.

    Same set, two readers: what `build_l1_output_schema` declares is exactly what
    `validate_overrides` accepts. A graft on one side without the other is either an
    unhonoured edit (schema-only) or an unguarded one (validator-only).
    """
    import json
    from pathlib import Path

    from promptpotter.application.optimization.validators.l1_strict import (
        build_l1_output_schema,
        validate_overrides,
    )
    from promptpotter.domain.pipeline_parsing import parse_pipeline_response

    raw = json.loads(
        (
            Path(__file__).resolve().parents[1] / "datasets/promptpotter-self/pipeline.json"
        ).read_text(encoding="utf-8")
    )
    schema = parse_pipeline_response(raw)

    emitted = build_l1_output_schema(schema)["schema"]["properties"]["variants"]["items"][
        "properties"
    ]["pipeline_params_override"]["properties"]
    surface = schema.node_param_keys()
    # The field-NAME lever is dropped from BOTH the emitted schema and the surface's
    # locked view — the lock is structural, so the LLM cannot emit a key that isn't there.
    assert "output_schema_field_names" not in emitted["l1_generate"]["properties"]
    for node, keys in surface.items():
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
    # merged as a string — `_matches_declared_type` used to wave through every non-scalar type.
    assert [f.reason for f in validate_overrides({"l1_critique": {"layout": "hdr"}}, schema)] == [
        "type_mismatch"
    ]
    assert validate_overrides({"l1_critique": {"layout": {"instruction": ["plan"]}}}, schema) == []


def test_schema_field_rename_is_locked_by_default_and_never_silently_half_applies() -> None:
    """The field-NAME lever. Three silent harms, none of which raise.

    (1) A rename the emitted schema advertises but the response model does not alias fails
    EVERY parse of EVERY round — the schema and the model must derive from one function.
    (2) Gating the *apply* on the inner cycle's own config would silently drop every rename an
    outer campaign emits (an inner campaign loads the inner dataset's `campaign.json`, not the
    outer's), scoring a no-op as a legitimate mutation.
    (3) `populate_by_name` must stay off: if the old key still validated, a rename the model
    ignored would look applied, and the axis would measure nothing.
    """
    import json
    from pathlib import Path

    from promptpotter.application.optimization.dispatch.llm_call import (
        set_optimizer_prompt_overrides,
    )
    from promptpotter.application.optimization.dispatch.schemas import (
        L1GenerateOutput,
        build_l1_response_model,
    )
    from promptpotter.application.optimization.validators.l1_strict import (
        build_l1_output_schema,
        effective_l1_field_names,
    )
    from promptpotter.domain.pipeline_parsing import parse_pipeline_response

    root = Path(__file__).resolve().parents[1]
    outer = parse_pipeline_response(
        json.loads((root / "datasets/promptpotter-self/pipeline.json").read_text(encoding="utf-8"))
    )
    inner = parse_pipeline_response(
        json.loads((root / "datasets/justlogic/pipeline.json").read_text(encoding="utf-8"))
    )

    def emittable(schema: dict) -> set[str]:
        variants = schema["schema"]["properties"]["variants"]["items"]["properties"]
        node = variants["pipeline_params_override"]["properties"].get("l1_generate", {})
        return set(node.get("properties", {}))

    rename = {"changes_description": "mutation_rationale"}
    try:
        # Locked by default: the outer cannot even emit the key.
        set_optimizer_prompt_overrides(None)
        assert "output_schema_field_names" not in emittable(build_l1_output_schema(outer))
        # Unlocked: the key appears. Descriptions stay free either way.
        unlocked = emittable(build_l1_output_schema(outer, schema_field_rename=True))
        assert "output_schema_field_names" in unlocked
        assert "output_schema_descriptions" in unlocked

        # The inner cycle applies a bound rename even though its OWN knob is off (default).
        set_optimizer_prompt_overrides({"l1_generate": {"output_schema_field_names": rename}})
        assert effective_l1_field_names() == rename
        variant = build_l1_output_schema(inner)["schema"]["properties"]["variants"]["items"]
        assert "mutation_rationale" in variant["properties"]
        assert "changes_description" not in variant["properties"]
        assert "mutation_rationale" in variant["required"]

        # Schema and model agree: the wire key parses, and binds back onto the real field.
        model = build_l1_response_model(effective_l1_field_names())
        parsed = model.model_validate(
            {
                "variants": [
                    {
                        "variant_name": "v",
                        "mutation_rationale": "r",
                        "prompt_fields_override": {"persona": "p"},
                    }
                ]
            }
        )
        assert parsed.variants[0].changes_description == "r"
        assert isinstance(parsed, L1GenerateOutput)

        # No backward compatibility: the OLD key must now fail, so an unhonoured rename is
        # a parse failure (charged 1.0) rather than a silently-scored no-op.
        with pytest.raises(pydantic.ValidationError):
            model.model_validate(
                {
                    "variants": [
                        {
                            "variant_name": "v",
                            "changes_description": "r",
                            "prompt_fields_override": {"persona": "p"},
                        }
                    ]
                }
            )

        # An ambiguous rename onto a surviving field is dropped, not applied.
        set_optimizer_prompt_overrides(
            {"l1_generate": {"output_schema_field_names": {"changes_description": "variant_name"}}}
        )
        assert effective_l1_field_names() == {}

        # Nothing bound → the plain model, allocated once.
        set_optimizer_prompt_overrides(None)
        assert build_l1_response_model(effective_l1_field_names()) is L1GenerateOutput
    finally:
        set_optimizer_prompt_overrides(None)
