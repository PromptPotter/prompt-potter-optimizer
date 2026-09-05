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
import logging
import subprocess
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from unittest import mock

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
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import QueryMeasurement
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.infrastructure.store.io import read_yaml, write_yaml
from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive
from promptpotter.shared.errors import DatasetIdentityError
from promptpotter.shared.hashing import content_hash


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


# 1. Measurement identity — what the key distinguishes


def test_content_hash_distinguishes_pipeline_params() -> None:
    """The measurement key must change when the config does — else the archive
    serves one config's cached scores for a different one."""
    dataset = [Sample(id=1, query="q", ground_truth="a")]
    sp_a = JobSearchPoint(pipeline_params={"steps": ["llm_ranking"]})
    sp_b = JobSearchPoint(pipeline_params={"steps": ["fuzzy_matching"]})
    assert sp_a.content_hash(dataset) != sp_b.content_hash(dataset)
    assert sp_a.content_hash(dataset) == content_hash(sp_a.render(), dataset, sp_a.pipeline_params)


def test_sp_hash_is_not_recoverable_from_the_stripped_config() -> None:
    """``sp_hash`` is the ONE key joining a candidate to the rows it paid for, and the only
    config a candidate carries forward (``ScoredCandidate.resolved_pipeline_params``) is that
    config with the rendered ``prompt`` STRIPPED. So the hash cannot be re-derived downstream —
    a reader doing it gets a well-formed id that addresses no run, and neither the archive nor
    the ruler raises: they simply find nothing and report an arm with no measurements.

    That is not hypothetical. `repair.py` rebuilt its re-measurement point from the stripped
    field, so every hole it plugged went to the backend with no prompt and banked under a run
    keyed on ``sha256("")``. Both halves are pinned here: the recompute diverges, and the
    reconstruction through the OSP restores the point that ran."""
    from promptpotter.domain.pipeline_schema import (
        NodePromptInfo,
        PipelineNode,
        PipelineSchema,
    )

    schema = PipelineSchema(
        name="t",
        version="1",
        nodes=[
            PipelineNode(
                name="llm_only",
                wire_type="llm",
                node_type="",
                param_keys=[],
                prompt_info=NodePromptInfo(),
            )
        ],
    )
    opt_sp = OptSearchPoint(persona="Expert", instruction="Solve it.")
    sp = opt_sp.to_job_search_point(base_pipeline_params={}, schema=schema)
    assert sp.render(), "the point that runs carries the rendered prompt in its node config"

    stripped = JobSearchPoint(pipeline_params=sp.config_params, prompt_fields=sp.prompt_fields)
    assert stripped.sp_hash(schema) != sp.sp_hash(schema)
    assert not stripped.render(), "and the stripped twin reaches the backend with no prompt"

    restored = opt_sp.to_job_search_point(base_pipeline_params=sp.config_params, schema=schema)
    assert restored.sp_hash(schema) == sp.sp_hash(schema)


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


def test_judge_identity_moves_the_searchpoint_hash() -> None:
    """Swapping a judge, its models, its rubric, or the TERM it is read under re-cuts the key.

    An archive row is keyed on node configs. A judge that changed without moving the key would
    have every verdict taken under the OLD grader replayed under the new one — silently, and in
    whichever direction the new grader happens to be more lenient. Re-keying is the same fact one
    level up: the same rubric read under a different term banks a different set of observations,
    so a formula naming the old term would raise on rows that look eligible."""
    from promptpotter.application.pipeline_resolve import resolve_pipeline_config_params
    from promptpotter.domain.pipeline_parsing import parse_pipeline_response
    from promptpotter.judges.protocol import JudgeSpec, JudgeStage

    schema = parse_pipeline_response(
        {
            "nodes": {
                "llm_only": {"type": "generation", "config": {"model": "m", "provider": "p"}}
            },
            "pipelines": {"default": ["llm_only"]},
        }
    )
    active = schema.active_steps_excluding([])

    def sp_hash(judges: dict[str, JudgeSpec]) -> str:
        return schema.sp_hash(
            resolve_pipeline_config_params(active, {}, None, schema, judges=judges)
        )

    def spec(name: str, model: str) -> JudgeSpec:
        return JudgeSpec(name=name, stages=[JudgeStage(model=model, provider="p")])

    one = {"answer": spec("sealqa", "a")}
    hashes = {
        "none": sp_hash({}),
        "sealqa@a": sp_hash(one),
        "sealqa@b": sp_hash({"answer": spec("sealqa", "b")}),
        # Same models, different RUBRIC — the fingerprint hashes the prompt text, so this moves
        # even though nothing an operator wrote in the config differs.
        "simpleqa@a": sp_hash({"answer": spec("simpleqa", "a")}),
        # Same judge, same models, read under a different TERM.
        "rekeyed": sp_hash({"correctness": spec("sealqa", "a")}),
        # A step ADDED. The cell now carries two graded observations, not one.
        "two_steps": sp_hash({"answer": spec("sealqa", "a"), "grounded": spec("simpleqa", "a")}),
    }
    assert len(set(hashes.values())) == len(hashes), f"judge identity collides: {hashes}"
    assert sp_hash(one) == hashes["sealqa@a"], "an unchanged judge must not move the key"
    # Declaration order is the STEP order for a reader, never part of what was measured — two
    # campaigns declaring the same graders in a different order graded the same cells identically.
    assert (
        sp_hash({"grounded": spec("simpleqa", "a"), "answer": spec("sealqa", "a")})
        == hashes["two_steps"]
    ), "declaration order must not re-cut the measurement key"


def test_a_judge_graded_row_rescores_without_calling_a_model() -> None:
    """Re-grading an archived row is FREE, and must stay free.

    ``rescore_results`` runs at six sites that re-derive over already-banked rows — the δ ruler,
    A/B replay, resume, exploration, the origin gate, the hard-sample archive. The judge's verdict
    is banked into ``pipeline_data`` at measure time precisely so those paths read a number instead
    of re-billing one LLM call per archived row, every time an index warms."""
    from factories import measurement

    from promptpotter.application.scoring.formula import compile_scorer, rescore_results
    from promptpotter.judges import call as judge_call

    calls: list[str] = []

    async def _explode(*_a: Any, **_k: Any) -> tuple[str, str]:
        calls.append("asked a model")
        return "", ""

    original, judge_call.ask = judge_call.ask, _explode
    try:
        # The banked verdict, exactly as `materialize_sample_values` lands it: top-level in
        # pipeline_data, which is what makes it addressable from the formula at all.
        row = measurement(sample_id=0, fitness=0.0, pipeline_data={"sealqa": 1.0})
        scorer = compile_scorer("sealqa", None, verifier_graded=False)
        rescore_results([row], scorer)
    finally:
        judge_call.ask = original

    assert row["fitness"] == 1.0, "the banked verdict is what the formula must read"
    assert calls == [], f"rescoring an archived row reached a model: {calls}"


def test_a_conversation_reaches_the_formula_only_as_projected_scalars() -> None:
    """The turn channel must stay unreachable from a formula; its projection must not be.

    ``turns`` is compacted out of cold rows, so a formula that walked it would raise on cells it
    had already scored."""
    from factories import measurement

    from promptpotter.application.scoring.formula import compile_scorer, rescore_results
    from promptpotter.application.scoring.formula.compiler import (
        CELL_INTRINSIC_NAMES,
        ScoringTermMissingError,
    )
    from promptpotter.domain.scoring import TURN_SCALAR_KEYS, turn_scalars

    assert not (TURN_SCALAR_KEYS & CELL_INTRINSIC_NAMES), (
        "a projected term colliding with an intrinsic is dropped by cell_namespace's splat, "
        "silently, leaving a key no formula can reach"
    )

    turns = [
        {"index": 1, "source": "agent", "step": "retrieve", "tools": ["bash", "bash"]},
        {"index": 2, "source": "agent", "step": "retrieve", "tools": []},
        {"index": 3, "source": "agent", "step": "answer", "tools": ["bash"]},
    ]
    scalars = turn_scalars(turns)  # type: ignore[arg-type]
    assert scalars == {
        "n_turns": 3.0,
        "n_tool_calls": 3.0,
        "retrieve_turns": 2.0,
        "answer_turns": 1.0,
    }
    assert turn_scalars([]) == {}, "no conversation is absence, never a zeroed count"

    # The point of the whole projection: a formula can NAME these. Scored off a row shaped the way
    # `measure_sample` banks one.
    row = measurement(
        sample_id=0, fitness=0.0, pipeline_data={"env_reward": 1.0, "turns": turns, **scalars}
    )
    scorer = compile_scorer(
        "env_reward * (1.0 if n_turns <= 4 else 0.5) * min(1.0, retrieve_turns / 2.0)",
        None,
        verifier_graded=True,
    )
    rescore_results([row], scorer)
    assert row["fitness"] == 1.0

    # Indexing and attribute access are refused at compile; `len` is a bare Call, so it compiles
    # and fails at eval. Both are stops — pinned here so adding `len` to SAFE_BUILTINS is caught.
    for formula in ("turns[0]", "turns.index"):
        with pytest.raises(ValueError, match="disallowed syntax"):
            compile_scorer(formula, None, verifier_graded=True)

    with pytest.raises(ScoringTermMissingError):
        rescore_results(
            [measurement(sample_id=0, fitness=0.0, pipeline_data={"turns": turns})],
            compile_scorer("len(turns)", None, verifier_graded=True),
        )


def test_a_judge_never_grades_a_cell_that_has_no_answer() -> None:
    """A cell with no answer must cost nothing and bank nothing.

    ``predicted`` is the ``NO_RESULT`` sentinel on every cell of a backend that emits no ranking —
    which is the whole of ``harbor``, and any episodic backend after it. A judge reading it raw
    renders ``Answer: NO_RESULT`` into its rubric, bills a model call, and banks whatever category
    comes back as a graded observation of an answer that does not exist. Both halves are the harm:
    a fabricated reading enters the composite the election is decided on, and it is paid for.

    Absence is the only honest verdict here, and it must be reached BEFORE the prompt is rendered,
    so the assertion is a score AND a spend."""
    import asyncio

    from factories import measurement

    from promptpotter.config.settings import NO_RESULT
    from promptpotter.judges import call as judge_call
    from promptpotter.judges.grounding import ANSWER_GROUNDING
    from promptpotter.judges.protocol import JudgeSpec, JudgeStage

    calls: list[str] = []

    async def _explode(*_a: Any, **_k: Any) -> tuple[str, str]:
        calls.append("asked a model")
        return "A", ""

    # A cell that RAN and left a trace — so nothing but the missing answer can explain the
    # absence, and the no-trace arm cannot be what fired.
    row = measurement(
        sample_id=0,
        fitness=0.0,
        predicted=NO_RESULT,
        pipeline_data={"reasoning_trace": "searched the docs, found the founding date"},
    )
    spec = JudgeSpec(name="answer_grounding", stages=[JudgeStage(model="m", provider="p")])
    original, judge_call.ask = judge_call.ask, _explode
    try:
        verdict = asyncio.run(ANSWER_GROUNDING.grade(spec, row))
    finally:
        judge_call.ask = original

    assert verdict.score is None, f"a sentinel answer was graded as {verdict.label!r}"
    assert calls == [], f"a cell with no answer was billed a grading: {calls}"


# 2. Replay eligibility — which banked row may be served back


def _archive(archive: MeasurementArchive, run_id: str, data: dict[str, Any]) -> None:
    """Seed one complete run — the whole measurement set is what is new.

    Grade A unless the caller stamps its own: `entry_grade` reads an unstamped row as C, and the
    reuse path excludes C, so an ungraded fixture is not replayable at all — every test here that
    is ABOUT matching would then pass or fail on provenance instead. Production rows always carry
    one (`loaders.py::build_dataset_run_data` grades every run it banks)."""
    data.setdefault("provenance", {"grade": "A", "deliberate_source": True})
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


def test_a_grade_C_run_is_never_replayed_from_either_entry(tmp_path: Path) -> None:
    """A grade-C run is never served back as a cache hit, so its stale sample cannot pose as a real
    evaluation: replayed rows are re-archived under the READING run, which `build_dataset_run_data`
    grades from its own ``source``/``human_intervened``, so a served C cell re-enters as A and
    reaches the δ ruler `hard_sample_archive` keeps it out of (ADR-0005: every consumer excludes C).

    Both entries are pinned because the floor used to be a PARAMETER — the facade passed it and the
    core defaulted to serving everything, so reuse was the one consumer of the grade that excluded
    nothing, and reaching the core directly was enough to launder a C cell into the ruler."""
    import types

    from promptpotter.infrastructure.store import archive_views

    archive = MeasurementArchive(tmp_path)
    _seed_graded(archive, run_id="clean", grade="A", terminal_node="llm_only", sample_id=7)
    _seed_graded(
        archive, run_id="connector", grade="C", terminal_node="token_matching", sample_id=8
    )
    node_configs = [("llm_only", {"model": "X"})]

    from_core = archive.load_reusable_results(node_configs, dataset_name="aime")
    assert set(from_core) == {7}, "the DB core served a grade-C run without being asked to exclude"
    assert from_core[7]["query"] == "q_clean"

    served = archive_views.reusable_results(
        types.SimpleNamespace(archive=archive), node_configs, dataset_name="aime"
    )
    assert set(served) == {7}, "the reuse facade served a grade-C run as a cache hit"


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


# 3. Contamination of a scored prompt


def test_render_does_not_leak_l3_plan_into_target_prompt() -> None:
    """L3's plan reaches L1/L2/L3 prompts via ``_r_plan`` only — never via
    ``render``. A leak would silently score a plan-contaminated target prompt."""
    sentinel = "REVISED_OPTIMIZATION_FRAMEWORK_PLAN_SENTINEL"
    opt_sp = OptSearchPoint(persona="Expert", instruction="Solve.", plan=sentinel)
    assert sentinel not in opt_sp.render()


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


# 4. The searchpoint's param surface


def _pipeline_schema(dataset: str) -> PipelineSchema:
    """The committed `datasets/{dataset}/pipeline.yaml`, parsed. `promptpotter-self` is the
    outer L4 campaign (it declares the schema levers); `justlogic-d234` is a plain inner one."""
    path = Path(__file__).resolve().parents[1] / "datasets" / dataset / "pipeline.yaml"
    return parse_pipeline_response(yaml.safe_load(path.read_text(encoding="utf-8")))


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


# 5. The dispatch frame — what a node is shown, within what budget


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
    from promptpotter.domain.l1_layout import NODE_LAYOUTS
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
        load_optimizer_prompt("l1_generate"), bundle, node="l1_generate"
    )
    assert "CURRENT INNER OPTIMIZER PROMPTS" in filled.render(), (
        "the generator was handed no subject — it is rewriting text it cannot see"
    )
    assert len(rendered["rendered_prompt"]) >= subject_chars
    starved = set(injection_coverage_counts(coverage)) & NODE_LAYOUTS["l1_generate"].mandatory
    assert not starved, f"mandatory panel(s) refused by the budget: {sorted(starved)}"


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


# 6. L4 steering — an edit that reaches outside its own level


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


# 7. Money — what a call is billed, and against which price


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
            JobSearchPoint(),
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


class _CountingClient:
    """One provider, counting round-trips. ``chat`` is the seam a judge actually reaches."""

    def __init__(self, reply: str = "A") -> None:
        self.reply = reply
        self.calls = 0

    async def chat(self, **_kw: Any) -> Any:
        from promptpotter.infrastructure.llm.response import LLMResponse

        self.calls += 1
        return LLMResponse(
            content=self.reply,
            model="grader-1",
            usage={"prompt_tokens": 11, "completion_tokens": 1},
        )


async def _grade_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, reply: str
) -> tuple[_CountingClient, list[Any], Any]:
    """Grade one identical cell twice through the real evaluator, and report what it cost."""
    from factories import measurement

    from promptpotter.infrastructure.store.stores import LLMReuseCache
    from promptpotter.judges import build_evaluators
    from promptpotter.judges import call as judge_call
    from promptpotter.judges.protocol import JudgeSpec, JudgeStage

    client = _CountingClient(reply)
    monkeypatch.setattr(judge_call, "get_llm_client", lambda _p: client)
    metered: list[Any] = []
    monkeypatch.setattr(judge_call, "emit_token_usage", lambda **kw: metered.append(kw))

    cache = LLMReuseCache(tmp_path, "judge_reuse")
    (ev,) = build_evaluators(
        {"answer": JudgeSpec(name="sealqa", stages=[JudgeStage(model="grader-1", provider="p")])},
        cache=cache,
    )
    for _ in range(2):
        row = measurement(sample_id=0, fitness=0.0)
        row["query"], row["predicted"], row["ground_truth"] = "who?", "Ada", "Ada"
        last = await ev.compute(result=row, schema=None)
    return client, metered, last


async def test_a_second_grading_of_one_comparison_is_not_re_billed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stale-data ladder re-enters ``measure_sample`` twice more per degraded sample, and two
    candidates whose mutation did not change the answer present the grader an identical comparison
    — so without reuse a judged campaign pays for the same verdict over and over, invisibly.

    Both halves are asserted, because each fails on its own. The provider is reached ONCE, and the
    replay is still METERED — flagged ``cached`` — since the cell was still graded and grading cost
    must stay invariant to our cache history, exactly as ``llm_call`` and ``emit_step_token_usage``
    keep it."""
    client, metered, score = await _grade_twice(tmp_path, monkeypatch, reply="A")

    assert score == 1.0, "the replayed reply must grade identically, not merely cheaply"
    assert client.calls == 1, f"an identical comparison re-billed the provider: {client.calls}x"
    assert [m["cached"] for m in metered] == [False, True], "a served grading went unmetered"
    assert {m["kind"] for m in metered} == {"judge"}, "grading spend landed outside its own bucket"


async def test_an_empty_grading_reply_is_never_made_permanent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Emptiness is a TRANSIENT provider failure, and the key is the prompt hash — so storing one
    makes it permanent for every future grading of that comparison, in a tenant-global tree that
    outlives the run and the campaign both. A re-run does not clear it, and nothing on any surface
    points at the cache: the operator sees a cell that cannot be graded, forever.

    Same scar as ``llm_call``'s, at the judge's own chokepoint."""
    client, _metered, score = await _grade_twice(tmp_path, monkeypatch, reply="   ")

    assert score is None, "an unreadable grading is an absent verdict, never a zero"
    assert client.calls == 2, "an empty reply was cached and replayed as if it were a verdict"


async def test_an_unusable_cache_entry_costs_a_re_sample_and_never_the_cell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reuse cache exists to make grading cheaper, so nothing in it may cost a MEASUREMENT.

    A truncated write or an entry an older build shaped raises on read and on validate alike — and
    ``ask`` sits under ``measure_sample``'s catch-all, so a raise there banks the whole cell as an
    ERROR and discards a backend answer already paid for. Worse, it does so on every future run:
    the key is the prompt hash, and the tree is tenant-global. Absent, unreadable and stale are one
    answer — sample it again."""
    from factories import measurement

    from promptpotter.infrastructure.store.stores import LLMReuseCache
    from promptpotter.judges import build_evaluators
    from promptpotter.judges import call as judge_call
    from promptpotter.judges.protocol import JudgeSpec, JudgeStage

    client = _CountingClient("A")
    monkeypatch.setattr(judge_call, "get_llm_client", lambda _p: client)
    monkeypatch.setattr(judge_call, "emit_token_usage", lambda **_kw: None)

    cache = LLMReuseCache(tmp_path, "judge_reuse")
    (ev,) = build_evaluators(
        {"answer": JudgeSpec(name="sealqa", stages=[JudgeStage(model="grader-1", provider="p")])},
        cache=cache,
    )
    row = measurement(sample_id=0, fitness=0.0)
    row["query"], row["predicted"], row["ground_truth"] = "who?", "Ada", "Ada"
    assert await ev.compute(result=row, schema=None) == 1.0

    # Poison every entry the first grading wrote — a half-written file is the realistic shape.
    poisoned = list(tmp_path.glob("judge_reuse/*.json"))
    assert poisoned, "the first grading banked nothing, so this proves nothing"
    for path in poisoned:
        path.write_text('{"content": ', encoding="utf-8")

    assert await ev.compute(result=row, schema=None) == 1.0, "a bad entry cost the cell its grade"
    assert client.calls == 2, "an unusable entry must fall through to a fresh sample"


def test_a_judge_term_cannot_take_a_name_that_already_measures_something() -> None:
    """A judge's TERM is the one evaluator name an operator picks, so it is the one that collides.

    Two collisions, both silent and both the same harm — a formula reads a number measuring
    something else. A term `cell_namespace` binds itself is dropped by the ``pipeline_data`` splat,
    so the formula scores the intrinsic; a term a package evaluator owns is written AFTER it by
    ``materialize_sample_values``, so the formula scores the judge under a name promising retrieval
    coverage. Neither raises anywhere downstream, and both reach the archive.

    Both names are read off the live registries rather than typed here: what is asserted is that
    the sets stay disjoint, not what is in them."""
    from promptpotter.application.scoring.evaluators import all_evaluators
    from promptpotter.application.scoring.formula.compiler import CELL_INTRINSIC_NAMES
    from promptpotter.judges import build_evaluators
    from promptpotter.judges.protocol import JudgeSpec, JudgeStage

    stage = JudgeStage(model="grader-1", provider="p")
    taken = [
        next(ev.name for ev in all_evaluators() if ev.scope == "per_sample"),
        next(iter(sorted(CELL_INTRINSIC_NAMES))),
        "not an identifier",
    ]
    for term in taken:
        with pytest.raises(ValueError):
            build_evaluators({term: JudgeSpec(name="sealqa", stages=[stage])})


def test_a_grader_that_raises_never_costs_the_cell_it_graded() -> None:
    """A grading is cheap; the cell it reads is not. No failure in a judge may spend the second.

    ``ask`` never raising covers the provider. It does not cover anything else thrown inside
    ``grade`` — a rubric placeholder the caller does not fill, a label outside ``to_score``, a
    third-party judge's own bug — and any of those reaches ``measure_sample``'s catch-all, which
    banks ``pipeline_data=None``. The backend answer is then gone: paid for, never archived, and
    indistinguishable on the round file from a backend that was down.
    """
    import asyncio

    from factories import measurement

    from promptpotter.judges import JUDGES, build_evaluators
    from promptpotter.judges.protocol import Judge, JudgeSpec, JudgeStage

    async def _explode(_spec: Any, _result: Any) -> Any:
        raise RuntimeError("this judge is broken")

    row = measurement(
        sample_id=0,
        fitness=0.0,
        predicted="Paris",
        pipeline_data={"reasoning_trace": "read the source"},
    )
    JUDGES["_raises"] = Judge(
        name="_raises",
        version="1",
        description="d",
        rubric="r",
        grade=_explode,
        labels=("X",),
        to_score={"X": 1.0},
        needs_gold=False,
    )
    try:
        (ev,) = build_evaluators(
            {"answer_ok": JudgeSpec(name="_raises", stages=[JudgeStage(model="m", provider="p")])}
        )
        score = asyncio.run(ev.compute(result=row, schema=None))
    finally:
        del JUDGES["_raises"]

    assert score is None, "a judge's own bug was banked as a graded score"
    assert row["pipeline_data"], "the paid backend measurement was discarded with the grading"
    assert "RuntimeError" in row["pipeline_data"]["answer_ok_why"]


def test_a_stage_no_judge_asks_is_refused_before_a_cell_is_bought() -> None:
    """``fingerprint`` hashes the whole stage chain, so a stage nothing reads is not free.

    It re-cuts every archive key and re-pays for the whole panel while changing no verdict — the
    expensive direction of silent, since both the old rows and the new ones look right."""
    from promptpotter.judges import build_evaluators
    from promptpotter.judges.protocol import JudgeSpec, JudgeStage

    chain = [JudgeStage(model="grade-1", provider="p"), JudgeStage(model="tie-break", provider="p")]
    with pytest.raises(ValueError):
        build_evaluators({"answer_correct": JudgeSpec(name="sealqa", stages=chain)})


# 8. Where the package reads and writes

# Bare scalars YAML 1.1 resolves to a non-string: the write-side hazard `write_yaml` must quote.
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


def _tree_bytes(root: Path) -> dict[str, bytes]:
    """Every file under *root*, keyed by relative path — a byte-exact snapshot."""
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


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
