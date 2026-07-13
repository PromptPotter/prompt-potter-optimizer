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

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pydantic
import pytest

from promptpotter.domain.measurement_provenance import grade_run
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive
from promptpotter.shared.hashing import content_hash


def _pipeline_schema(dataset: str) -> PipelineSchema:
    """The committed `datasets/{dataset}/pipeline.json`, parsed. `promptpotter-self` is the
    outer L4 campaign (it declares the schema levers); `justlogic` is a plain inner one."""
    path = Path(__file__).resolve().parents[1] / "datasets" / dataset / "pipeline.json"
    return parse_pipeline_response(json.loads(path.read_text(encoding="utf-8")))


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
    osp = OptSearchPoint(persona="Expert", instruction="Solve.", plan=sentinel)
    assert sentinel not in osp.render()


def _seed_run(archive: MeasurementArchive, *, run_id: str, dataset_name: str, hit: bool) -> None:
    """Minimal ``MeasurementArchive.save`` envelope — one sample whose query text
    is dataset-tagged, so a cross-dataset bleed is detectable by query overlap."""
    archive.save(
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
    archive: MeasurementArchive, *, run_id: str, grade: str, terminated_at: str, sample_id: int
) -> None:
    """Save one run carrying a provenance grade and a single sample. The two runs measure
    DIFFERENT samples — the cache keys on ``sample_id``, so they need distinct ones to
    coexist (they used to be told apart by query text alone, both stamped sample 7)."""
    provenance: dict[str, Any] = {"grade": grade, "deliberate_source": grade != "C"}
    archive.save(
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
        archive.save(
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
    from promptpotter.application.runner.inner import InnerTaskSpec
    from promptpotter.application.runner.inner.cycle import _inner_narrative
    from promptpotter.domain.results import CycleResult, RoundResult, ScoredCandidate

    spec = InnerTaskSpec(inner_dataset="justlogic", seed=3, n_samples=24, n_rounds=2, n_variants=2)

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
    schema (justlogic's `{reasoning, answer}`), keyed by that node's fields, on any
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

    schema = _pipeline_schema("justlogic")
    node = schema.get_node("llm_only")
    assert node is not None and node.output_schema is not None
    fields = list(node.output_schema.fields)  # ["reasoning", "answer"] — the closed set

    # EMIT: the lever is handed to L1, keyed by the target node's OWN fields, schema-driven.
    emitted = _emittable_l1_params(build_l1_response_schema(schema), node="llm_only")
    assert "output_schema_descriptions" in emitted
    describable = build_l1_response_schema(schema)["properties"]["variants"]["items"]["properties"][
        "pipeline_params_override"
    ]["properties"]["llm_only"]["properties"]["output_schema_descriptions"]
    assert set(describable["properties"]) == set(fields)

    # A description edit is a valid `object` override (declared, type-checked).
    edit = {"llm_only": {"output_schema_descriptions": {"answer": "ANSWER FIRST."}}}
    assert validate_overrides(edit, schema, forbidden_axes_strict=True) == []

    # APPLY: the fold rewrites the wire schema's prose and removes the virtual key; an
    # invented field (`made_up`) never reaches the wire; and no edit bound → schema untouched.
    base_cfg = dict(node.current_config)
    pp = {
        "llm_only": {
            **base_cfg,
            "output_schema_descriptions": {"answer": "ANSWER FIRST.", "made_up": "dropped"},
        }
    }
    fold_schema_descriptions(pp)
    props = pp["llm_only"]["output_schema"]["properties"]
    assert props["answer"]["description"] == "ANSWER FIRST."
    assert "made_up" not in props
    assert "output_schema_descriptions" not in pp["llm_only"]

    untouched = {"llm_only": dict(base_cfg)}
    before = json.dumps(untouched, sort_keys=True)
    fold_schema_descriptions(untouched)
    assert json.dumps(untouched, sort_keys=True) == before  # no override → byte-identical wire

    # The raw schema stays locked — the `description` prose is the ONLY unlocked schema surface.
    forbidden = validate_overrides(
        {"llm_only": {"output_schema": {"type": "object"}}},
        schema,
        forbidden_axes_strict=True,
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
    emitted = build_l1_response_schema(schema)["properties"]["variants"]["items"]["properties"][
        "pipeline_params_override"
    ]["properties"]
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
    # `output_schema_descriptions` on any target node (justlogic's `llm_only`, below).
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
    just = _pipeline_schema("justlogic")
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


def test_schema_violation_is_a_non_result_not_a_wrong_answer() -> None:
    """A response that misses its declared answer slot yields `final_ranking: []`, not a MISS.

    Projecting the whole `{reasoning, answer}` blob (or an empty string) into
    `final_ranking[0]` makes every schema violation grade as a *confident wrong answer* — the
    run completes, the score looks real, and a schema regression is indistinguishable from the
    model getting the logic wrong. `sample_measurement` already yields NO_RESULT on an empty
    ranking; the connector's job is to produce one, identically in both execution arms.
    """
    import asyncio

    from promptpotter.connectors.llm_only import llm_only_in_process_run
    from promptpotter.infrastructure.llm import models as llm_models

    schema = {"type": "object", "properties": {"reasoning": {}, "answer": {}}}
    base_cfg = {"provider": "p", "model": "m", "prompt": "q"}

    def _run(parsed: Any, content: str = "", cfg_extra: dict[str, Any] | None = None) -> list[Any]:
        resp = llm_models.LLMResponse(content=content, model="m", parsed=parsed)

        class _Client:
            async def chat(self, *_: Any, **__: Any) -> Any:
                return resp

        import promptpotter.infrastructure.llm as llm_pkg

        original = llm_pkg.get_llm_client
        llm_pkg.get_llm_client = lambda _p: _Client()  # type: ignore[assignment]
        try:
            payload = {"node_config": {"llm_only": {**base_cfg, **(cfg_extra or {})}}}
            out = asyncio.run(llm_only_in_process_run("q", payload))
        finally:
            llm_pkg.get_llm_client = original  # type: ignore[assignment]
        return list(out["data"]["final_ranking"])

    schema_cfg = {"output_schema": schema, "answer_field": "answer"}
    # The named slot is destructured — the reasoning never reaches the matcher.
    assert _run({"reasoning": "because", "answer": "TRUE"}, cfg_extra=schema_cfg) == ["TRUE"]
    # Slot absent, and response that never decoded to an object: both are NON-results.
    assert _run({"reasoning": "because"}, cfg_extra=schema_cfg) == []
    assert _run(None, content="TRUE", cfg_extra=schema_cfg) == []
    # An `output_schema` without `answer_field` would silently grade the wrong slot.
    with pytest.raises(ValueError, match="answer_field"):
        _run({"answer": "TRUE"}, cfg_extra={"output_schema": schema})
    # No schema declared → text mode, unchanged; an empty answer is still a non-result.
    assert _run(None, content="**TRUE**") == ["**TRUE**"]
    assert _run(None, content="   ") == []


def test_schema_field_rename_is_locked_by_default_and_never_silently_half_applies() -> None:
    """The field-NAME lever, from the fork that unlocks it to the parse that honours it.

    Three silent harms, none of which raise. (1) A rename the emitted schema advertises but
    the response model does not alias fails EVERY parse of EVERY round — schema and model must
    derive from one function. (2) Gating the *apply* on the inner cycle's own config would
    silently drop every rename an outer campaign emits (the inner loads its own
    `campaign.json`), scoring a no-op as a legitimate mutation. (3) `populate_by_name` must
    stay off: if the old key still validated, a rename the model ignored would look applied.
    """
    from promptpotter.application.config import CampaignConfig, OptimizationConfig
    from promptpotter.application.optimization.dispatch.llm_call import (
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
    inner = _pipeline_schema("justlogic")
    rename = {"changes_description": "mutation_rationale"}

    try:
        # Locked by default: the outer cannot even emit the rename key.
        set_optimizer_prompt_overrides(None)
        assert "output_schema_field_names" not in _emittable_l1_params(
            build_l1_response_schema(outer)
        )
        unlocked = _emittable_l1_params(build_l1_response_schema(outer, schema_field_rename=True))
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
        variant = build_l1_response_schema(inner)["properties"]["variants"]["items"]
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
