"""C2 — Statistical / numerical correctness.

The math the optimizer trusts: per-dataset scorer formulas, the evaluator
registry + composite fitness, the Rasch (IRT) fit + decision-information sample
picker, the Bayesian Posterior-of-Being-Best gate, and the L1/L2/L3 output
validators that score a proposal's conformance. Every assertion is wrong-reveal
— it fails when the formula is wrong, not when a wrapper is renamed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from promptpotter.application.intelligence.exploration import (
    Observation,
    extend_ruler,
    fit_rasch,
    fit_rasch_2pl,
    fit_theta_given_delta,
    graduate_ruler_model,
    parent_level_trajectory,
    select_round_subset,
)
from promptpotter.application.mask.backprop import accumulate_node_stats, select_rewind_round
from promptpotter.application.mask.record import (
    MaskCandidate,
    MaskCycle,
    MaskRound,
    SpineCycle,
)
from promptpotter.application.optimization.pobb.checks import (
    PoBBCheck,
    PoBBConfig,
)
from promptpotter.application.optimization.pobb.classification import terminal_ranking
from promptpotter.application.optimization.validators.l1_invariants import (
    detect_invariants,
)
from promptpotter.application.scoring.formula import (
    ScoringFormulaError,
    compile_scorer,
)
from promptpotter.application.scoring.formula.matchers import (
    _aime_match,
    _exact_match,
    _gsm8k_match,
)
from promptpotter.application.scoring.metrics import (
    compute_composite_fitness,
    matched_parent_stats,
)
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.escalation_signals import (
    EscalationTarget,
    ValidationFailure,
)
from promptpotter.domain.opt_search_point import (
    L2L3Memory,
    OptSearchPoint,
    WoundChannels,
)
from promptpotter.domain.phases import StopReason
from promptpotter.domain.pipeline_schema import (
    NodeType,
    ObservationMapping,
    PipelineNode,
    PipelineSchema,
)
from promptpotter.domain.results import (
    CandidateProposal,
    RoundResult,
    ScoredCandidate,
    is_leader_eligible,
)
from promptpotter.domain.ruler import (
    AbilityReading,
    DeltaRuler,
    anchor_id_of,
    flat_ruler_id,
    is_flat_ruler_id,
    theta_caveat,
)
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import extract_item_label
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.shared import extract_gsm8k_number
from promptpotter.shared.errors import RulerCoverageError
from promptpotter.shared.statistics import (
    paired_reading,
)
from tests.factories import (
    cycle_result,
    lost_round,
    measurement,
    measurements,
    round_result,
    scored_candidate,
)


def _result_min(predicted: str, ground_truth: str) -> dict:
    return {
        "query": "q",
        "predicted": predicted,
        "ground_truth": ground_truth,
        "hit": False,
        "fitness": 0.0,
        "error": None,
        "pipeline_data": None,
    }


def _eval_result(
    *,
    hit: bool = True,
    score: float = 1.0,
    total_time: float = 100.0,
    error: str | None = None,
    final_ranking: list | None = None,
    candidate_ranking: list | None = None,
    step_timings: dict | None = None,
    diagnostics: dict | None = None,
    ground_truth: str = "gt",
    predicted: str = "gt",
) -> dict:
    pd: dict = {"total_time": total_time}
    if final_ranking is not None:
        pd["final_ranking"] = final_ranking
    if candidate_ranking is not None:
        pd["candidate_ranking"] = candidate_ranking
    if step_timings is not None:
        pd["step_timings"] = step_timings
    if diagnostics is not None:
        pd["diagnostics"] = diagnostics
    return {
        "query": "q",
        "predicted": predicted,
        "ground_truth": ground_truth,
        "hit": hit,
        "fitness": score,
        # Both halves, as ``rescore_results`` stamps them: the composite means ``objective``, and
        # these fixtures declare no ``per_cell``, where it is the same float as ``fitness``.
        "objective": score,
        "error": error,
        "pipeline_data": pd,
    }


def _single_node_schema() -> PipelineSchema:
    """Minimal schema with one generic node and no node_type assignments."""
    return PipelineSchema(
        name="test", nodes=[PipelineNode(name="llm_only", node_type=NodeType.NONE)]
    )


def _ruler(
    delta: dict[int, float],
    *,
    mu: float = 0.0,
    sigma: float = 2.0,
    se: float | dict[int, float] = 0.5,
) -> DeltaRuler:
    """A locked ruler over a bare δ map — the shape most numeric tests care about.

    ``se`` per cell rather than flat is what the acquisition tests bend: the whole question
    there is which of two cells at the SAME difficulty a round buys, and a flat map cannot ask
    it."""
    return DeltaRuler(
        delta=dict(delta),
        delta_se=dict(se) if isinstance(se, dict) else dict.fromkeys(delta, se),
        mu_delta=mu,
        sigma_delta=sigma,
        sigma_theta=1.5,
        calibration_model="1PL",
        anchor_id=anchor_id_of(delta, mu, sigma, "1PL"),
    )


scipy = pytest.importorskip("scipy")  # transitively required by the PoBB math


# 1. Scorer formulas and the AST allowlist


@pytest.mark.parametrize(
    "fn,args,expected",
    [
        # _aime_match: last boxed wins / bad boxed → fallback / no numbers.
        (_aime_match, (r"First: \boxed{10}. Rechecking: \boxed{42}", "42"), 1.0),
        (_aime_match, (r"\boxed{undefined} The answer is 42", "42"), 1.0),
        (_aime_match, ("no numbers", "42"), 0.0),
        # extract_gsm8k_number: comma-stripped / #### preferred / none.
        (extract_gsm8k_number, ("#### 1,234",), 1234.0),
        (extract_gsm8k_number, ("I calculated 99 but #### 42",), 42.0),
        (extract_gsm8k_number, ("no numbers",), None),
        # _gsm8k_match: cross-format numeric equivalence / mismatch.
        (_gsm8k_match, ("42.0", "#### 42"), 1.0),
        (_gsm8k_match, ("#### 99", "#### 42"), 0.0),
        # _exact_match: last bold wins (case-insensitive) / no-marker / mismatch.
        (_exact_match, ("First try **No**. Corrected: **Yes**", "yes"), 1.0),
        (_exact_match, ("plain text answer", "Plain Text Answer"), 1.0),
        (_exact_match, ("foo", "bar"), 0.0),
    ],
)
def test_matcher_formula(fn, args, expected):
    assert fn(*args) == expected


@pytest.mark.parametrize(
    "formula",
    [
        "().__class__",
        "__import__('os').system('echo pwn')",
        "(lambda: 1)()",
        "[x for x in range(10)][0]",
        "predicted.__class__",
        "exact_match(predicted, ground_truth) := 1",
    ],
)
def test_compile_scorer_rejects_attribute_and_unsafe_syntax(formula: str) -> None:
    """Restricted-eval is bypassable; the AST allowlist is the real boundary."""
    with pytest.raises((ValueError, SyntaxError)):
        compile_scorer(formula, verifier_graded=False)


def test_scorer_rejects_non_finite_instead_of_scoring_it_perfect() -> None:
    # SILENT wrong-score, and it INVERTS. Python's `min` short-circuits on its first argument,
    # so `min(1.0, nan)` is `1.0` and the clamp `max(0.0, min(1.0, nan))` returns 1.0 — a NaN
    # scored a PERFECT sample. An unmeasured proxy or an empty denominator anywhere in a
    # composite formula therefore read as a flawless answer. `inf` clamps to 1.0 the same way.
    assert max(0.0, min(1.0, float("nan"))) == 1.0  # the trap, pinned
    for expr in ("1e400", "-1e400", "1e400 - 1e400"):
        with pytest.raises(ScoringFormulaError, match="non-finite"):
            compile_scorer(expr, verifier_graded=False).fitness(_result_min("a", "a"))

    # The reachable shape: a non-finite value arriving through `pipeline_data` — how an L4
    # proxy would deliver one — not a literal a human would notice in the formula string.
    result = _result_min("a", "a") | {"pipeline_data": {"after_N_rounds_delta": float("nan")}}
    with pytest.raises(ScoringFormulaError, match="non-finite"):
        compile_scorer("after_N_rounds_delta", verifier_graded=False).fitness(result)

    # The per-ROUND scorer clamps identically and was the twin hole: the fix to the per-sample
    # clamp above left the composite one wide open, so a NaN evaluator scored a perfect ROUND.
    # Both now pass through one `clamp_unit_score`; this pins that they cannot drift apart again.
    from promptpotter.application.scoring.formula import compile_round_scorer

    with pytest.raises(ScoringFormulaError, match="non-finite"):
        compile_round_scorer("accuracy")({"accuracy": float("nan")})


# 2. Composite fitness — what a score is made of


def _recall_schema() -> PipelineSchema:
    """Schema with candidate_source + ranker + cache — exercises all recall evaluators."""
    return PipelineSchema(
        name="test_recall",
        nodes=[
            PipelineNode(name="cache_lookup", node_type=NodeType.CACHE),
            PipelineNode(
                name="fuzzy",
                node_type=NodeType.CANDIDATE_SOURCE,
                observation_mappings=[
                    ObservationMapping(
                        pipeline_key="candidate_ranking", output_field="candidate_ranking"
                    )
                ],
            ),
            PipelineNode(
                name="ranker",
                node_type=NodeType.RANKER,
                observation_mappings=[
                    ObservationMapping(pipeline_key="final_ranking", output_field="final_ranking")
                ],
            ),
        ],
    )


def test_an_unmeasured_term_is_never_scored_as_zero() -> None:
    # SILENT wrong-score. Every empty-collection aggregate in the evaluator registry used to
    # return a PERFECT value — no rows meant "no errors" (0.0), "instant" (1.0), "maximally
    # compact" (1.0). The registry now omits the key, so a formula that names an unmeasured term
    # halts instead of scoring the round on a number nobody computed. The distinction matters:
    # a round that measured every sample and failed them all IS a 0.0; a round that measured
    # nothing is not.
    from promptpotter.application.scoring.evaluators import (
        compute_accuracy,
        compute_degraded_rate,
        compute_error_rate,
    )
    from promptpotter.application.scoring.formula import (
        ScoringTermMissingError,
        compile_round_scorer,
    )
    from promptpotter.domain.scoring import recorded_cost_s

    assert compute_accuracy(results=[]) is None
    assert compute_error_rate(results=[]) is None
    assert compute_degraded_rate(results=[]) is None

    # Latency stopped being an evaluator and became the `latency` CHANNEL
    # (`domain/scoring.py::recorded_cost_s`) — one reading, per row, so a mask and a `per_cell`
    # formula name the same number. It kept both properties, which is why they are pinned here
    # and not left to the deleted evaluator's grave: a CACHED replay stamps `total_time` 0.0 while
    # the work it replays took minutes, so reading that field priced a whole round at "instant"
    # and elected the arm that had doubled the clock. It reads `step_timings`, which survives the
    # stamp. An EMPTY timing map is a row that recorded no time at all — absent, never a 0.0
    # that would read as a free cell and divide into any budget the formula sets.
    def _timed(total: float, steps: dict[str, float]) -> dict[str, object]:
        pipeline_data = {"total_time": total, "step_timings": steps}
        return _result_min("q", "a") | {"pipeline_data": pipeline_data}

    assert recorded_cost_s(_timed(0.0, {"inner": 600.0})) == 600.0
    assert recorded_cost_s(_timed(0.0, {})) is None

    # All samples measured, all fatally deprecated → a verdict of 0.0, not an absence.
    deprecated = _result_min("q", "a") | {"error": "SCHEMA_VALIDATION_FAILED", "fitness": 0.0}
    assert compute_accuracy(results=[deprecated]) == 0.0

    # An errored row is the ABSENCE of a verdict — excluded from the mean, never a silent
    # 0.0 dragging a real score down (an L4 inner campaign dying of a timeout must not
    # halve its optimizer prompt's accuracy)...
    scored = _result_min("q", "a") | {"hit": True, "fitness": 1.0}
    errored = _result_min("ERROR", "a") | {"error": "boom", "error_category": "UNKNOWN"}
    del errored["fitness"], errored["hit"]  # real error rows carry neither
    assert compute_accuracy(results=[scored, errored]) == 1.0
    # ...while it still surfaces on the error channel, counted over ALL rows.
    assert compute_error_rate(results=[scored, errored]) == 0.5

    # The default composite (`accuracy`) refuses a round it cannot score, rather than 0.0.
    with pytest.raises(ScoringTermMissingError, match="accuracy"):
        compile_round_scorer(None)({"latency": 600.0})
    with pytest.raises(ScoringTermMissingError, match="latency"):
        compile_round_scorer("accuracy * 500.0 / latency")({"accuracy": 0.9})

    # But the GATEWAY over an EMPTY round is defined, not a crash: an operator skip at query
    # 0/N (or an all-excluded round) hands ``compute_composite_fitness`` no rows. It records the
    # 0.0 floor with ``total`` 0 — the no-evidence marker that keeps the candidate out of winner
    # election — rather than run the fail-loud scorer and halt the whole cycle.
    empty = compute_composite_fitness([], _single_node_schema(), opt_sp=None)
    assert empty["composite_fitness"] == 0.0
    assert empty["accuracy"] == 0.0
    assert empty["total"] == 0


def test_a_labelless_round_reports_absence_not_zero() -> None:
    """A comparison AGAINST A LABEL, run on a backend that has none, reports 0.0 — and that
    zero is banked.

    Every reading here walks a ranking for the ground truth. With none it is in nothing, so
    ``candidate_recall`` records "the ranker never retrieved it on any sample", every rank lands
    in ``not_found`` and every top-k is 0.0. All three persist: the evaluator map into
    ``rounds/round_NNNN.json`` and ``index.jsonl::scores``, where any ``score:`` lens re-reads
    it; the rank statistics into ``RoundResult.diagnostics``. Measured on a Harbor origin that
    solved EIGHT of ten, the round file carried ``not_found: 10`` and ``top-1: 0.0``.

    Silent by construction — the numbers are well-formed and in range, and the panel that
    renders them self-suppresses on an undiscriminating distribution, so nothing on screen
    disagrees. What decides it is the LABEL, never ``predicted == NO_RESULT``: that sentinel is
    set by a dataset's ``node_role`` declarations and fires on Harbor but not on L4, which
    declares ``l1_critique`` a ranker.
    """
    from promptpotter.application.optimization.round_analysis import compute_round_diagnostics
    from promptpotter.application.scoring.evaluators import materialize_round_values

    schema = _recall_schema()

    def rows(ground_truth: str) -> list[dict]:
        # The ranker RAN on every row (`step_timings` carries it), which is what puts these rows
        # in the recall denominator — the arm that fabricates the rate rather than abstaining.
        return [
            _eval_result(
                score=1.0,
                ground_truth=ground_truth,
                predicted="",
                step_timings={"ranker": 0.1, "fuzzy": 0.1},
                # Width 2: below that a ranking has no ordering to report and `gt_in_ranked`
                # is already absent for that reason, which would mask the one under test.
                final_ranking=[{"candidate": "wrong-a"}, {"candidate": "wrong-b"}],
                candidate_ranking=[{"candidate": "wrong-a"}, {"candidate": "wrong-b"}],
            )
            for _ in range(10)
        ]

    labelled = materialize_round_values(schema, rows("A"))
    labelless = materialize_round_values(schema, rows(""))

    # The labelled round genuinely missed on every row: 0.0 is a VERDICT there and must stay.
    assert labelled["candidate_recall"] == 0.0
    assert labelled["source_recall"] == 0.0
    # The labelless one has no such verdict to give. Omitted, not defaulted — the same contract
    # `test_an_unmeasured_term_is_never_scored_as_zero` pins for an unmeasured term, so a formula
    # naming it halts loud instead of scoring on a number nobody computed.
    assert "candidate_recall" not in labelless
    assert "source_recall" not in labelless
    # Everything that is NOT a label comparison still answers, on the same rows.
    assert labelless["accuracy"] == 1.0
    assert labelless["cache_hit_rate"] == 0.0

    def diagnostics(ground_truth: str):
        rr = round_result(1, results=rows(ground_truth))
        return compute_round_diagnostics(rr, [rr], schema)

    labelled_diag = diagnostics("A")
    assert labelled_diag.rank_buckets["not_found"] == 10
    assert labelled_diag.top_k_accuracy[1] == 0.0
    assert labelled_diag.samples[0].gt_in_ranked is False
    assert labelled_diag.samples[0].gt_in_source is False

    labelless_diag = diagnostics("")
    assert labelless_diag.rank_buckets == {}
    assert labelless_diag.top_k_accuracy == {}
    # The count of rows that WERE measured is not a rank claim and survives.
    assert labelless_diag.n_valid == 10
    # Per-sample, the same distinction: absent, never the positive claim `False`.
    assert labelless_diag.samples[0].gt_in_ranked is None
    assert labelless_diag.samples[0].gt_in_source is None


def test_the_constant_answer_floor_is_undefined_without_labels() -> None:
    """A verifier-graded bank has no constant answer, so it has no floor — and the two readers of
    that fact must not conflate "undefined" with "0.0".

    `class_floor` REFUSES such a bank rather than returning a number: `reasoning_margin`,
    `rewards_collapse` and `verdict_settled` all derive from it, so the screen's whole verdict is
    undefined, which is what a `None` would fail to say.

    Its second caller is the one that made this worth pinning. `runner/inner/spawn.py` computes the
    floor for every seat it seats, then REPORTS collapse risk — it is not the screen and owns no
    verdict. Unguarded it would take the refusal and die mid-spawn on the arrangement the recursion
    exists for (`pp-self` over a verifier-graded inner benchmark), with a message about a collapse
    reading from a path that was only ever logging one.
    """
    import inspect

    from promptpotter.application.runner.inner import spawn
    from promptpotter.application.seed_screen import SeedScreenError, class_floor

    labelled = [Sample(id=i, query=f"q{i}", ground_truth="A" if i else "B") for i in range(4)]
    assert class_floor(labelled) == 0.75

    with pytest.raises(SeedScreenError, match="verifier-graded"):
        class_floor([Sample(id=i, query=f"q{i}", ground_truth=None) for i in range(4)])

    # The guard at the caller, asserted on the source rather than by driving a whole inner spawn:
    # reaching that call needs a container backend, an inner campaign and real spend, and what is
    # actually load-bearing is that the refusal is not entered and its result is not compared.
    src = inspect.getsource(spawn._run_inner_campaign)
    assert "all_verifier_graded" in src, (
        "the class_floor call must not be reached on a labelless bank"
    )
    assert "bank_floor is not None and bank_floor >=" in src, (
        "a None floor must SKIP the collapse comparison, never compare as 0.0"
    )


def test_terminal_ranking_sources_the_prediction():
    """The prediction is the head of the LAST ranker/candidate_source node that emitted
    its key — final_ranking when the ranker ran, else the candidate pool. An empty
    terminal list is a verdict (NO_RESULT), not a fall-through to the earlier pool."""
    schema = (
        _recall_schema()
    )  # order: cache_lookup, fuzzy(candidate_ranking), ranker(final_ranking)

    def r(pd: dict) -> dict:
        return {"pipeline_data": pd}

    # Both keys present → the later ranker's final_ranking wins over the earlier pool.
    assert terminal_ranking(
        r(
            {
                "candidate_ranking": [{"candidate": "pool"}],
                "final_ranking": [{"candidate": "ranked"}],
            }
        ),
        schema,
    ) == [{"candidate": "ranked"}]
    # Token-terminal shape (no final_ranking) → the candidate pool IS the result ranking.
    assert terminal_ranking(r({"candidate_ranking": [("Nylon 6-6", 0.5)]}), schema) == [
        ("Nylon 6-6", 0.5)
    ]
    # Ranker ran but found nothing → [] (NO_RESULT), no fall-through to the pool.
    assert (
        terminal_ranking(
            r({"candidate_ranking": [{"candidate": "pool"}], "final_ranking": []}), schema
        )
        == []
    )
    # No ranking keys / no schema → [].
    assert terminal_ranking(r({"total_time": 1.0}), schema) == []
    assert terminal_ranking(r({"final_ranking": [{"candidate": "x"}]}), None) == []
    # Shape-agnostic head extraction: a (name, score) tuple item yields the name.
    assert extract_item_label(("Nylon 6-6", 0.5)) == "Nylon 6-6"


def test_composite_fitness_matches_default_formula():
    # The default formula is plain ``accuracy`` — composite_fitness must equal accuracy
    # so the decision metric and the headline number agree (no latency/recall/self-heal
    # blend inflating it above the real score). Degradation is gated by the round health
    # block, not folded into fitness.
    schema = _single_node_schema()
    results = [_eval_result(score=1.0, total_time=100), _eval_result(score=0.0, total_time=200)]
    scored = compute_composite_fitness(results, schema, opt_sp=None)
    assert scored["composite_fitness"] == pytest.approx(scored["accuracy"], abs=1e-9)
    assert scored["composite_fitness"] == pytest.approx(0.5, abs=1e-4)


def test_composite_fitness_zeroed_on_validation_failure():
    # A REAL OptSearchPoint, not a namespace stub. The stub carried a bare `object()`
    # where a `ValidationFailure` goes and had no `render()` — it only ever type-checked
    # because `compute_composite_fitness(opt_sp: Any)` accepted anything, and production
    # code grew a `hasattr(opt_sp, "render")` guard to tolerate it. The type is the contract.
    opt_sp = OptSearchPoint(
        memory=L2L3Memory(
            wounds=WoundChannels(
                validation_failures=[
                    ValidationFailure(
                        axis="llm_only.model", value="bad", allowed=[], reason="forbidden_axis"
                    )
                ]
            )
        )
    )
    scored = compute_composite_fitness(
        [_eval_result(score=1.0)], _single_node_schema(), opt_sp=opt_sp
    )
    assert scored["composite_fitness"] == 0.0


def test_a_correct_but_costly_cell_is_a_HIT_everywhere_it_is_thresholded() -> None:
    """`is_hit` is a predicate on CORRECTNESS, and two readers fed it the composite instead.

    `domain/scoring.py::CellScorer` states the contract and names the readers: *"``fitness`` is
    CORRECTNESS — what ``is_hit`` thresholds for the tape, the difficulty stratification and the
    failing-samples panel"*. `datasets/justlogic-d234/campaign.yaml` repeats it: a correct-but-
    expensive answer must stay a HIT, or L1 is sent to repair reasoning that was right.

    Both shipped datasets declare a `per_cell` composite that scales BELOW 1.0 (a latency or token
    penalty), so `objective < HIT_THRESHOLD` on every cell — `is_hit` returned False for all of
    them. Two consequences, both silent: the MANDATORY `diagnostics` panel rendered every correct
    sample to `l1_critique` as a miss, disagreeing with `failing_samples` in the same prompt; and
    the parent-hit stratum emptied, so `build_round_order`'s regression probe never fired and the
    round bought cells the composite penalised rather than cells the arm got wrong.
    """
    from promptpotter.application.intelligence.adaptive_queue_mechanism import build_round_order
    from promptpotter.application.optimization.round_analysis import _sample_diagnostics
    from promptpotter.domain.scoring import is_hit

    # One cell the arm got RIGHT and was charged for: correctness 1.0, composite 0.4.
    costly = {
        "sample_id": 1,
        "query": "q",
        "predicted": "p",
        "ground_truth": "g",
        "fitness": 1.0,
        "objective": 0.4,
        "pipeline_data": {},
    }

    diag = _sample_diagnostics([costly], None, None)
    assert [d.fitness for d in diag] == [1.0], "the diagnostics panel graded a cell on its cost"
    assert is_hit(diag[0].fitness), "a correct cell rendered to l1_critique as a miss"

    # The stratification reads the same number, so the cell is a parent HIT and can be probed.
    order = build_round_order({1: 1.0, 2: 0.0}, None, [1, 2])
    assert set(order) == {1, 2}
    assert not is_hit(0.4), "guard: the composite really is below the hit threshold"


def test_an_archive_row_is_graded_by_the_reading_scorer_not_its_stamp(monkeypatch) -> None:
    """The δ ruler grades archive rows with the CAMPAIGN's scorer, never a stamp on the row.

    ``objective`` is written by ``rescore_results`` under whatever formula was active when the row
    was banked, so reading it back pools one scale out of several — and a row banked before the
    field existed carries none at all. Read with ``.get("objective", 0.0)`` that absence is not a
    hole, it is a WRONG ANSWER: measured on `justlogic-d234`, 49,221 of 49,501 observations graded
    0.0, the fit collapsed δ to sd 0.243 across 600 cells, θ became logit-accuracy plus a constant,
    and the acquisition — which ranks on δ ≈ θ — bought progressively EASIER panels while the
    headline climbed 50.0% → 85.7% and `overlap` stayed flat.

    Silent harm: every number renders, the ruler reports 600 cells and a warm id, and the campaign
    reads as a clean climb.
    """
    import types

    from promptpotter.application.intelligence.hard_sample_archive import (
        build_archive_observations,
    )
    from promptpotter.domain.scoring import CellScorer
    from promptpotter.infrastructure.store import archive_views

    # Two cells the arm got RIGHT, banked before ``objective`` existed — and one stamped by a
    # formula that is not the reading campaign's, which must lose to the scorer just the same.
    rows = [
        {"sample_id": 1, "fitness": 1.0},
        {"sample_id": 2, "fitness": 1.0, "objective": 0.0},
    ]
    monkeypatch.setattr(
        archive_views,
        "list_runs",
        lambda *_a, **_k: [
            {"run_id": "r1", "prompt_fields_id": "cand-a", "provenance": {"grade": "A"}}
        ],
    )
    monkeypatch.setattr(archive_views, "run_signatures", lambda *_a, **_k: {"r1": (1, 1)})
    monkeypatch.setattr(archive_views, "load_run", lambda *_a, **_k: {"measurements": rows})

    stores = types.SimpleNamespace(
        archive=types.SimpleNamespace(base_dir="/nowhere-unique-to-this-test")
    )
    obs = build_archive_observations(
        stores,
        dataset_name="d",
        scorer=CellScorer(fitness=lambda r: r["fitness"], objective=lambda r: r["fitness"]),
        scorer_id="under-test",
    )

    assert [o.response for o in obs] == [1.0, 1.0], (
        "an archive row was graded from its stamp, not from the reading campaign's scorer"
    )


def test_the_mean_interval_is_clipped_to_the_support_the_metric_actually_has() -> None:
    """The band is drawn beside the point estimate the loop elected on, so an interval reaching
    outside [0,1] claims accuracy the quantity cannot have. `mean_ci` is a normal-CLT band
    carrying PoBB's 1/(4n) SE floor, so an arm that scored 0.0 on every cell comes out at
    ±0.082 unclipped — a whisker below zero, which the webapp then clamped at paint time. The
    clamp belongs here: a browser clamping it is a second place that decides what the number
    means, and every other reader of `mean_fitness_ci` got the raw one.

    The population is the SCOREABLE one, deliberately: a decision grades an errored row 0.0, but
    an interval must bracket the rows the estimate was drawn from, so an error row widens nothing.
    """
    from promptpotter.application.scoring.selection import mean_fitness_ci

    lo, hi = mean_fitness_ci(measurements([0.0] * 6))
    assert lo == 0.0 and hi is not None and hi > 0.0, "the floor is the metric's, not the band's"

    lo, hi = mean_fitness_ci(measurements([1.0] * 6))
    assert hi == 1.0 and lo is not None and lo < 1.0

    # A mid arm is untouched by the clamp — the band is a real interval, not a clamp artifact.
    lo, hi = mean_fitness_ci(measurements([0.0, 1.0] * 3))
    assert lo is not None and hi is not None and 0.0 < lo < 0.5 < hi < 1.0

    # Nothing scoreable is ABSENT, never a zero-width interval at 0.0.
    assert mean_fitness_ci([]) == (None, None)
    assert mean_fitness_ci([measurement(0, None, error_category="provider")]) == (None, None)

    # And an errored cell does not drag the band down: it never happened, so it widens nothing.
    # `_mean_fitness_by_cell` floors a grade-less row to 0.0 on purpose for the ELECTION, which is
    # why the filter sits at this entry and not inside it.
    clean = mean_fitness_ci(measurements([1.0] * 6))
    with_error = mean_fitness_ci(
        [*measurements([1.0] * 6), measurement(9, None, error_category="provider")]
    )
    assert with_error == clean


# 3. The δ ruler — the scale every θ is read on


def _synth_observations(
    theta_true: dict[str, float],
    delta_true: dict[int, float],
    n_per_pair: int = 8,
    seed: int = 0,
) -> list[Observation]:
    rng = np.random.default_rng(seed)
    obs = []
    for cid, t in theta_true.items():
        for sid, d in delta_true.items():
            p = 1.0 / (1.0 + np.exp(-(t - d)))
            for _ in range(n_per_pair):
                obs.append(
                    Observation(candidate_id=cid, sample_id=sid, response=float(rng.random() < p))
                )
    return obs


def _synth_2pl(
    theta: np.ndarray,
    delta: np.ndarray,
    disc: np.ndarray,
    *,
    n_per_pair: int,
    seed: int,
) -> list[Observation]:
    """Responses from a true 2PL model: p = σ(aₛ·(θ_c − δₛ))."""
    rng = np.random.default_rng(seed)
    obs: list[Observation] = []
    for i, th in enumerate(theta):
        for s, (d, a) in enumerate(zip(delta, disc, strict=True)):
            p = 1.0 / (1.0 + np.exp(-a * (th - d)))
            obs.extend(Observation(f"c{i}", s, bool(rng.random() < p)) for _ in range(n_per_pair))
    return obs


def test_rasch_recovers_known_parameters() -> None:
    # Wide spread + many obs/pair → empirical-Bayes MAP recovers both the
    # latent arrays and the population hyperparameters within tolerance.
    theta_true = {"strong": 1.5, "mid": 0.0, "weak": -1.5}
    delta_true = {1: -1.0, 2: 0.0, 3: 1.0, 4: 2.0}
    obs = _synth_observations(theta_true, delta_true, n_per_pair=80, seed=42)

    posterior = fit_rasch(obs)

    assert posterior.converged
    # Identifiability: both arrays anchored to mean(theta) == 0.
    assert abs(sum(posterior.theta.values()) / len(posterior.theta)) < 1e-6
    # Recovery is within the noise floor at n=80 obs/pair.
    # Loosen to 0.5 logits — math correctness is what we're guarding, not precision.
    theta_offset = sum(theta_true.values()) / len(theta_true)
    for cid, t_true in theta_true.items():
        assert abs(posterior.theta[cid] - (t_true - theta_offset)) < 0.5
    for sid, d_true in delta_true.items():
        assert abs(posterior.delta[sid] - (d_true - theta_offset)) < 0.5

    # Empirical Bayes estimates the priors instead of hardcoding them: the
    # hyperparameters track the true population spread (θ std ≈ 1.22, δ std
    # ≈ 1.12, mean δ ≈ 0.5), not the old fixed 1.5 / 2.0 sigmas.
    assert abs(posterior.sigma_theta - 1.22) < 0.6
    assert abs(posterior.sigma_delta - 1.12) < 0.6
    assert abs(posterior.mu_delta - 0.5) < 0.4


def test_fit_theta_given_delta_is_subset_invariant_unlike_accuracy() -> None:
    # The cross-round comparability guard (slice 2). A FIXED difficulty ruler δ;
    # two candidates measured on DISJOINT subsets whose raw accuracies INVERT their
    # true ability — the able one saw only HARD samples (low accuracy), the weak one
    # only EASY samples (high accuracy). θ-given-δ must recover the true ordering
    # (able > weak) where accuracy gets it backwards, and be ~subset-invariant.
    rng = np.random.default_rng(7)
    ruler = {1: -2.0, 2: -2.0, 3: -2.0, 4: 2.0, 5: 2.0, 6: 2.0}  # easy 1-3, hard 4-6
    easy, hard = [1, 2, 3], [4, 5, 6]

    def measure(cid: str, theta: float, sids: list[int], n: int = 200) -> list[Observation]:
        obs: list[Observation] = []
        for sid in sids:
            p = 1.0 / (1.0 + np.exp(-(theta - ruler[sid])))
            obs.extend(Observation(cid, sid, float(rng.random() < p)) for _ in range(n))
        return obs

    able = measure("able", 1.5, hard)  # high ability, hard subset → low accuracy
    weak = measure("weak", -0.5, easy)  # low ability, easy subset → high accuracy
    able_acc = sum(o.response for o in able) / len(able)
    weak_acc = sum(o.response for o in weak) / len(weak)
    assert weak_acc > able_acc  # raw accuracy INVERTS the true ability

    fit = fit_theta_given_delta(able + weak, ruler)
    assert fit["able"][0] > fit["weak"][0]  # θ recovers the true ordering
    assert abs(fit["able"][0] - 1.5) < 0.5  # within the noise floor at n=200/pair
    assert abs(fit["weak"][0] - (-0.5)) < 0.5
    assert all(se > 0 for _, se in fit.values())

    # Subset-invariance: the SAME θ measured on easy vs hard → ~same estimate (the
    # property accuracy lacks — easy accuracy ≫ hard accuracy for the same ability).
    same_easy = fit_theta_given_delta(measure("x", 0.8, easy), ruler)["x"][0]
    same_hard = fit_theta_given_delta(measure("x", 0.8, hard), ruler)["x"][0]
    assert abs(same_easy - same_hard) < 0.5

    # A sample absent from a WARM ruler RAISES. It used to be graded at δ=0, which is not a
    # neutral value but a position: on a ruler centred near +2.8 it scored an unmeasured cell as
    # easier than anything ever measured, and silently pulled θ down ~2 logits for every round
    # whose subset had walked off the scale. Loud beats plausible.
    with pytest.raises(RulerCoverageError) as caught:
        fit_theta_given_delta([Observation("ghost", 99, True)], ruler)
    assert "99" in str(caught.value)
    # The COLD ruler is the one legitimate flat read: θ is plain logit-accuracy, which depends on
    # no fit and so stays comparable across cycles. A single hit ⇒ θ > 0 under the N(0,σ²) prior.
    cold = fit_theta_given_delta([Observation("ghost", 99, True)], None)
    assert cold["ghost"][0] > 0.0

    # θ_se carries the quasi-likelihood dispersion correction. `Observation.response` is a GRADED
    # fitness, not a coin flip — a ranked-table answer at position 5 of 20, or the L4 outer
    # composite — and a graded response varies far less about its mean than Bernoulli assumes.
    # Measured against the true sampling spread of θ̂ at n=28: ×1.02 on binary, ×1.51 on
    # reciprocal-rank, ×4.66 on the L4 outer. That inflation is what left the outer election
    # unable to crown and PoBB unable to eliminate. SILENT: every gate reads a real signal as
    # noise, the run completes, and the loop reports "no candidate separated".
    graded = [Observation("g", sid, 0.5 + 0.02 * i) for i, sid in enumerate(easy * 6)]
    spread = [Observation("b", sid, float(i % 2)) for i, sid in enumerate(easy * 6)]
    assert (
        fit_theta_given_delta(graded, ruler)["g"][1] < fit_theta_given_delta(spread, ruler)["b"][1]
    )

    # ...and it must NOT move binary data: φ ≈ 1 there, so a dichotomous dataset is unchanged.
    # A correction that quietly re-scaled every existing campaign's SE would be the same class
    # of silent harm in the other direction.
    binary = measure("bin", 0.8, easy, n=60)
    p_hat = 1.0 / (1.0 + np.exp(-(fit_theta_given_delta(binary, ruler)["bin"][0] - (-2.0))))
    bern_se = 1.0 / np.sqrt(len(binary) * p_hat * (1 - p_hat) + 1 / 1.5**2)
    assert abs(fit_theta_given_delta(binary, ruler)["bin"][1] - bern_se) < 0.15 * bern_se

    # A response with NO residual spread carries no evidence about its own dispersion; the φ
    # floor stops that silence from being read as infinite confidence (SE → 0 ⇒ every gate fires).
    flat = [Observation("f", sid, 0.5) for sid in easy * 6]
    assert fit_theta_given_delta(flat, ruler)["f"][1] > 0.1


def test_2pl_recovers_discrimination_and_seam_is_invisible_when_flat() -> None:
    """The 2PL estimator + the one-ruler seam. Silent harm: if ``fit_theta_given_delta``
    read a ``(δ, a)`` ruler differently from a bare-δ ruler when a≡1, every θ would shift
    the moment a dataset graduated — a wrong winner with no error. And if 2PL couldn't
    recover discrimination, signal-chasing/weighting would key on noise."""
    # Seam invariance: a (δ, 1.0) ruler must give bit-identical θ to a bare-δ ruler.
    obs = [Observation("c1", s, float(s % 2 == 0)) for s in range(8)] + [
        Observation("c2", s, float(s % 3 == 0)) for s in range(8)
    ]
    bare = {s: 0.2 * s - 1.0 for s in range(8)}
    tupled = {s: (0.2 * s - 1.0, 1.0) for s in range(8)}
    fb, ft = fit_theta_given_delta(obs, bare), fit_theta_given_delta(obs, tupled)
    assert all(abs(fb[c][0] - ft[c][0]) < 1e-9 for c in fb)

    # 2PL recovers the discrimination STRUCTURE: alternating high (a=2.5) / low (a=0.4)
    # signal samples → the fit separates them (high aₛ ≫ low aₛ), the rank that matters.
    theta = np.linspace(-2.5, 2.5, 20)
    delta = np.linspace(-2.0, 2.0, 12)
    disc = np.array([2.5 if s % 2 == 0 else 0.4 for s in range(12)])
    post = fit_rasch_2pl(_synth_2pl(theta, delta, disc, n_per_pair=8, seed=1))
    high = float(np.median([post.discrimination[s] for s in range(0, 12, 2)]))
    low = float(np.median([post.discrimination[s] for s in range(1, 12, 2)]))
    assert high > low * 2.0  # high-signal samples discriminate far harder than noisy ones
    assert all(se > 0 for se in post.discrimination_se.values())


def test_graduation_gate_stays_1pl_until_2pl_wins_holdout() -> None:
    """The per-dataset graduation gate. Silent harm: graduating a dataset whose samples
    don't actually discriminate would fit aₛ to noise → an overfit ruler → wrong θ → wrong
    winner, with no error. The held-out CV gate must refuse 2PL unless it provably wins."""
    theta = np.linspace(-2.5, 2.5, 20)
    delta = np.linspace(-2.0, 2.0, 12)

    # (a) Genuinely discriminating data → graduates to 2PL.
    disc_varied = np.array([2.5 if s % 2 == 0 else 0.4 for s in range(12)])
    data_2pl = _synth_2pl(theta, delta, disc_varied, n_per_pair=8, seed=2)
    model_g, post_g = graduate_ruler_model(data_2pl, enable=True)
    assert model_g == "2PL"
    assert post_g.discrimination  # the chosen ruler carries discrimination

    # (b) Flat-discrimination data (true a≡1) → stays 1PL: 2PL can't win held-out.
    data_1pl = _synth_2pl(theta, delta, np.ones(12), n_per_pair=8, seed=3)
    model_flat, post_flat = graduate_ruler_model(data_1pl, enable=True)
    assert model_flat == "1PL"
    assert not post_flat.discrimination

    # (c) The operator switch forces 1PL even on discriminating data (hysteresis floor=off).
    model_off, _ = graduate_ruler_model(data_2pl, enable=False)
    assert model_off == "1PL"

    # (d) Too-sparse data can never graduate (no held-out evidence).
    sparse = [Observation("a", 1, True), Observation("a", 2, False), Observation("b", 1, False)]
    assert graduate_ruler_model(sparse, enable=True)[0] == "1PL"


def test_delta_ruler_stays_flat_until_a_second_arm_exists() -> None:
    # SILENT wrong-scale: with ONE ability the likelihood cannot separate "this item is hard" from
    # "this arm missed it", so δ collapses into a two-valued restatement of that arm's own hit
    # pattern — and every later θ in the cycle becomes a restatement of whether round 0 happened
    # to get the sample right. It is not an error; it is a plausible ruler that reads backwards.
    # It cost two campaigns: rounds carrying +14.3pp at p<0.05 were stamped `improved=False`.
    # The warm attempt now also runs BEFORE the round's election (`warm_ruler_if_cold`), which
    # relaxes the TIMING only — this is what pins the rule itself as untouched.
    from promptpotter.application.intelligence.exploration import Observation
    from promptpotter.application.optimization.cycle import _calibrate_delta_ruler

    n_min = 4
    arm_a = [Observation("a", sid, 1.0 if sid % 3 else 0.0) for sid in range(8)]

    # Eight distinct samples clears any sample floor on its own — the arm count is the binding
    # condition, and a fresh campaign hands this function exactly this shape.
    flat, _ = _calibrate_delta_ruler(None, n_min, enable_2pl=False, archive_obs=arm_a)
    assert flat is None

    arm_b = [Observation("b", sid, 1.0 if sid < 5 else 0.0) for sid in range(8)]
    warm, _ = _calibrate_delta_ruler(None, n_min, enable_2pl=False, archive_obs=arm_a + arm_b)
    assert warm is not None and warm.calibration_model == "1PL"


def test_ruler_id_names_the_scale_a_theta_was_read_on() -> None:
    """Comparability is a machine-checkable fact or it is nothing. The cold ruler shares ONE id
    everywhere — flat δ makes θ plain logit-accuracy, which depends on no fit — while any refit
    gets its own, because δ estimates move with the archive the fit saw."""
    # A cold cycle has no fit to name, so its id is minted from the OBJECTIVE instead — there is
    # no one flat id any more, because two objectives on a flat ruler are two scales.
    assert is_flat_ruler_id(flat_ruler_id("acc"))
    assert flat_ruler_id("acc") != flat_ruler_id("acc_minus_latency")

    fitted = _ruler({1: 0.5, 2: -0.25, 3: 0.0})
    # Key order is not part of the scale.
    assert fitted.anchor_id == anchor_id_of({3: 0.0, 2: -0.25, 1: 0.5}, 0.0, 2.0, "1PL")
    # …and a genuinely different δ is a different scale, however small the move.
    assert fitted.anchor_id != anchor_id_of({1: 0.5, 2: -0.2500001, 3: 0.0}, 0.0, 2.0, "1PL")
    assert not is_flat_ruler_id(fitted.anchor_id)

    # THE ANCHOR, NOT THE MEMBERSHIP. Anchored extension adds cells without moving the ones
    # already there, so a θ read before and after are on ONE scale and must share an id. Hashing
    # the membership would churn it every round, read a cycle as incomparable with ITSELF, and —
    # since `evidence.py` reads round 0's id into `Comparability` — poison cross-campaign
    # comparison too.
    grown = extend_ruler(fitted, [Observation("arm", 1, 1.0), Observation("arm", 9, 0.0)])
    assert set(grown.delta) == {1, 2, 3, 9}
    assert grown.anchor_id == fitted.anchor_id
    assert all(grown.delta[sid] == fitted.delta[sid] for sid in fitted.delta)


def test_an_instrument_reads_on_the_scale_its_spawner_fixed(tmp_path: Path) -> None:
    """The dominant error in the L4 loop was the ESTIMATOR, not the measurement: an inner cell's
    evidence epoch hides everything banked before it started, so the only arms its δ fit could see
    were its OWN and the scale came out of the treatment under test. Byte-identical origin rows
    returned θ spread wider than a winning margin.

    Drop the instrument branch and nothing raises — every cell silently self-fits again and the
    loop keeps electing winners on a scale each of them invented."""
    import contextvars
    import types

    from promptpotter.application.optimization.cycle import _given_ruler
    from promptpotter.shared.instrument import enter_instrument_mode

    given = _ruler({1: 0.5, 2: -0.25, 3: 0.0})

    session: Any = types.SimpleNamespace(
        dataset_name="inner-bench", state=types.SimpleNamespace(cycle_id="")
    )
    # No mode bound: an ordinary campaign fits its own, exactly as before.
    assert _given_ruler(session) is None

    def _inside_instrument() -> Any:
        enter_instrument_mode(evidence_epoch=frozenset(), optimizer_clamp=None, ruler=given)
        return _given_ruler(session)

    # Its own context, as a real spawn binds it — so the scale cannot leak back to the spawner.
    assert contextvars.copy_context().run(_inside_instrument) is given


def test_an_arm_that_beats_a_far_off_ruler_is_fit_not_oscillated_past() -> None:
    # `screen-taste-v0` cycle_df3de1e40b64: a graded objective puts every cell's δ near +5.55, and
    # the fit seeds at θ=0 — five logits away, where p saturates and the observed information is
    # the prior term alone. Undamped, `grad / info` is then a jump of ~14 logits into the OPPOSITE
    # saturation, and the iteration limit-cycles (14.06 → -19.46 → 16.09 → -19.68 → …) until
    # max_iter stops it wherever it stands. The three best arms of that run were each recorded
    # near θ = -25 with an SE in the hundreds, so every round refused to crown one.
    #
    # It is the arms sitting FURTHEST from the seed that overshoot, which on a hard ruler are the
    # arms that scored HIGHEST — so the failure is not noise, it reads improvement as collapse.
    delta = dict.fromkeys(range(20), 5.55)
    parent = [Observation("parent", sid, 0.20) for sid in delta]
    better = [Observation("better", sid, 0.45) for sid in delta]

    fit = fit_theta_given_delta(parent + better, delta, sigma_theta=1.34)
    parent_theta, parent_se = fit["parent"]
    better_theta, better_se = fit["better"]

    # Both land near the ruler they were measured on, not tens of logits away from it.
    assert 2.0 < parent_theta < 5.55, parent_theta
    assert parent_theta < better_theta < 7.0, better_theta
    # An SE in the hundreds is the tell that the iteration never converged at all.
    assert parent_se < 1.0 and better_se < 1.0, (parent_se, better_se)


def test_best_theta_is_re_read_on_the_warm_ruler_it_will_be_differenced_against() -> None:
    # SILENT, and it ends runs. `_restamp_on_warm` re-reads every banked θ onto the freshly locked
    # δ scale — but `tracking.best_theta` is one of those θ and was left on the cold one, where θ is
    # regularized logit-accuracy rather than a level centred on `mu_delta`. The ladder then
    # differences a WARM current reading against a COLD peak (`escalation/state.py::_improved`, and
    # the entry comparator `record_l2_fired` stamps): on an easy set the cold value is the larger,
    # so it pins for the rest of the run and every round after reports "no advance" — feeding
    # `l2_stall_count`, `l3_stall_count` and `STOP_L3_PATIENCE`, which terminates the cycle.
    # Nothing raises; the numbers all render.
    from types import SimpleNamespace

    from promptpotter.application.intelligence.exploration import Observation
    from promptpotter.application.optimization.cycle import (
        Cycle,
        CycleRoundState,
        _calibrate_delta_ruler,
        _cumulative_theta,
        _reading,
    )

    def rows(hit: set[int]) -> list[dict[str, Any]]:
        return [
            {"sample_id": sid, "objective": 1.0 if sid in hit else 0.0, "fitness": 1.0}
            for sid in range(8)
        ]

    origin_rows = rows({0, 1, 2, 3, 4, 5})
    round1_rows = rows({0, 1, 2, 3, 4, 5, 6})

    # An EASY set — most cells cleared by both arms, so the fit lands at mu_delta < 0 and the cold
    # reading is the larger of the two. That sign is the whole bug, so it is asserted, not assumed.
    ruler, _ = _calibrate_delta_ruler(
        None,
        4,
        enable_2pl=False,
        archive_obs=[Observation("a", sid, 1.0 if sid < 6 else 0.0) for sid in range(8)]
        + [Observation("b", sid, 1.0 if sid < 7 else 0.0) for sid in range(8)],
    )
    assert ruler is not None and ruler.mu_delta < 0

    cold = _reading(
        _cumulative_theta(origin_rows, None), None, objective_id="obj", results=origin_rows
    )
    assert cold is not None

    cycle = Cycle.__new__(Cycle)
    cycle.session = SimpleNamespace(scoring=SimpleNamespace(scorer_id="obj"))  # type: ignore[assignment]
    cycle.ruler = ruler
    cycle.rounds = [
        round_result(0, results=origin_rows, ability=cold),
        round_result(1, results=round1_rows, ability=cold),
    ]
    # What a live cold cycle banked: the peak taken on the flat ruler.
    cycle.tracking = CycleRoundState(best_theta=cold.theta, best_theta_se=cold.se)

    cycle._restamp_on_warm(_cumulative_theta(origin_rows, ruler))

    warm_thetas = [rr.ability.theta for rr in cycle.rounds if rr.ability is not None]
    assert len(warm_thetas) == 2
    assert cycle.tracking.best_theta == pytest.approx(max(warm_thetas))
    # The peak is the ROUNDS' max, never the stale cold seed carried through as a floor.
    assert cycle.tracking.best_theta < cold.theta
    # And it names the scale it will be differenced on, so `comparable_to` can refuse a cross-scale
    # read rather than silently making one.
    assert cycle.origin_round.ability is not None
    assert cycle.origin_round.ability.ruler_id == ruler.anchor_id


# 4. Electing a round winner


def _cs(
    *,
    candidate_id: str,
    accuracy: float,
    escalation_aborted: bool = False,
    elimination_stopped: bool = False,
    degradation_context: dict | None = None,
    elimination_context: dict | None = None,
) -> ScoredCandidate:
    return ScoredCandidate(
        candidate_id=candidate_id,
        label=candidate_id,
        changes_description="",
        accuracy=accuracy,
        composite_fitness=accuracy,
        total=20,
        evaluators={},
        escalation_aborted=escalation_aborted,
        elimination_stopped=elimination_stopped,
        degradation_context=degradation_context or {},
        elimination_context=elimination_context or {},
    )


def _health_row(
    statuses: dict[str, str], *warnings: dict[str, str], hit: bool = False, **extra: object
) -> dict:
    """One sample as the round verdict reads it — per-node step statuses plus whatever the
    BACKEND stamped about them. Every case below is a shape of this one row, so building it in
    one place is what stops them disagreeing about where a warning lives."""
    return {
        "hit": hit,
        # LABELLED, because every scenario built from this row is: the grade reads the label's
        # presence to tell "the model answered unreadably" from "this backend answers with a
        # reward and there is no label to read". Omitting it made every case here silently claim
        # to be the second one. Override with `ground_truth=""` for a verifier-graded row.
        "ground_truth": "A",
        "pipeline_data": {"diagnostics": {"step_statuses": statuses, "warnings": list(warnings)}},
        **extra,
    }


def test_round_winner_elects_by_ability_not_subset_accuracy() -> None:
    """Per-round-resubset drift guard. Two candidates measured on DIFFERENT subsets:
    the high-accuracy one only saw easy samples; the lower-accuracy one cleared HARD
    samples the origin always misses. Raw subset accuracy would crown the easy candidate;
    difficulty-adjusted ability (θ) — the gating metric — must crown the abler one.

    Silent harm: under per-round resubset the wrong winner is promoted with no error —
    the run completes, the dashboard looks fine, the lineage decays toward whoever drew
    the gentlest samples. The election ranks θ on the cycle's fixed δ ruler, so it does not.
    """
    from promptpotter.application.scoring.selection import elect_round_winner

    # Fixed δ ruler: easy {0..19} low difficulty, hard {20..39} high — the bank the election reads.
    ruler = _ruler({i: (-1.5 if i < 20 else 1.5) for i in range(40)})
    # Origin spans all 40: easy {0..19} hit, hard {20..39} missed.
    origin = [measurement(i, float(i < 20)) for i in range(40)]
    # Easy candidate: 16/20 on easy samples the origin also hits → accuracy 0.80, modest lift.
    weak_on_easy = [measurement(i, float(i < 16)) for i in range(20)]
    # Able candidate: 14/20 on HARD samples the origin always misses → accuracy 0.70, bigger lift.
    able_on_hard = [measurement(i, float(i < 34)) for i in range(20, 40)]
    results_by_id = {"weak_on_easy": weak_on_easy, "able_on_hard": able_on_hard}

    # Raw subset accuracy crowns the easy candidate (0.80 > 0.70)...
    assert sum(r["hit"] for r in weak_on_easy) / 20 > sum(r["hit"] for r in able_on_hard) / 20
    # ...but the θ-gated election crowns the abler one — it cleared HARD items (high δ), stronger
    # evidence of ability than more wins on easy items (low δ) everyone already passes.
    winner_id, abilities = elect_round_winner(
        ["weak_on_easy", "able_on_hard"], results_by_id, origin, 4, ruler, parent_bias=0.0
    )
    assert winner_id == "able_on_hard"
    # The fit rides out so the caller stamps θ from the same election fit (no second fit):
    # the abler candidate's θ outranks the easy one's despite the lower raw accuracy.
    assert abilities.theta["able_on_hard"] > abilities.theta["weak_on_easy"]


def test_a_thin_arm_cannot_win_on_a_margin_inside_its_own_noise() -> None:
    """`coverage_floor` IS PoBB's `n_min`, so every cut arm clears it and reaches the election.
    Ranked on a bare point estimate, a thin arm out-points a full panel on a margin smaller than
    its own SE.

    Silent harm: a winner is crowned, every number renders, and the lineage descends from a margin
    the round could not measure."""
    from promptpotter.application.intelligence.exploration import (
        PARENT_ABILITY_ID,
        candidate_abilities,
    )
    from promptpotter.application.scoring.selection import elect_round_winner
    from promptpotter.shared.statistics import p_exceeds

    ruler = _ruler(dict.fromkeys(range(28), 0.0))
    origin = measurements([1.0] * 14 + [0.0] * 14)
    deep = measurements([1.0] * 21 + [0.0] * 7)  # full panel, a well-measured gain
    shallow = measurements([1.0] * 5 + [0.0])  # cut at n_min, higher RATE on 1/6 the evidence

    ab = candidate_abilities({"deep": deep, "shallow": shallow}, origin, ruler)
    theta_p, se_p = ab.theta[PARENT_ABILITY_ID], ab.theta_se[PARENT_ABILITY_ID]
    p = {c: p_exceeds(ab.theta[c], ab.theta_se[c], theta_p, se_p) for c in ("deep", "shallow")}

    # The thin arm's POINT lift is larger, on nearly twice the SE — so it demonstrated less.
    assert ab.theta["shallow"] > ab.theta["deep"]
    assert ab.theta_se["shallow"] > 1.8 * ab.theta_se["deep"]
    assert p["deep"] > p["shallow"]
    args = (["deep", "shallow"], {"deep": deep, "shallow": shallow}, origin, 6, ruler)
    assert elect_round_winner(*args, parent_bias=0.0)[0] == "deep"

    # Ranking on P must never DISQUALIFY — that is what separates it from the `- theta_se` shrink
    # it replaced, which turned a wide-posterior gain negative and crowned nobody.
    assert (
        elect_round_winner(["shallow"], {"shallow": shallow}, origin, 6, ruler, parent_bias=0.0)[0]
        == "shallow"
    )


def test_the_bar_is_what_the_parent_can_do_not_the_draw_that_crowned_it() -> None:
    """A winner is the MAXIMUM over its round's electable arms, so its θ carries that round's
    largest noise draw and not just its ability. Nothing washes it out — ``rescore_parent``
    replays the winner's own cached rows, so the same inflated estimate is re-fit bit-for-bit
    every round after. ``parent_selection_bias`` subtracts E[max of k standard normals] × the
    winner's OWN SE so the bar stops ratcheting past what any arm can clear.

    Both ways of getting it wrong are silent, and they fail in opposite directions.
    UNDER-correct (drop the term) and a genuinely better arm is refused for the rest of the
    cycle: ``improved: false`` on every round with no reason recorded anywhere.
    OVER-correct — the paired ``√2`` SE, which charges the parent's noise to a maximum that
    never selected on it — and the bar sinks below the parent's real ability: on round 2 of
    `screen-taste-v0__0cb4d4` that turned a raw lift of −0.337 into +0.060 and crowned an arm
    below the parent on θ AND composite. A correction that over-corrects buys back exactly the
    noise-crowning it exists to stop, so the ~41% gap between the two is the whole subject.
    """
    import math

    from promptpotter.application.intelligence.exploration import (
        candidate_abilities,
        theta_lift_over_parent,
    )
    from promptpotter.application.scoring.selection import (
        elect_round_winner,
        parent_selection_bias,
    )

    def won(*, se: float | None, electable: int) -> RoundResult:
        """A round that crowned ``w`` over ``electable`` arms. ``winner_id`` is read off
        ``prompt_fields['lineage']``, which is where the live election stamps it."""
        return round_result(
            1,
            electable_count=electable,
            prompt_fields={"lineage": {"id": "w"}},
            candidate_scores=[scored_candidate("w", theta=0.5, theta_se=se)],
        )

    def held() -> RoundResult:
        return round_result(1, prompt_fields={})

    # ---- the term itself -------------------------------------------------------------
    # k=2 has a closed form — E[max of two standard normals] is 1/√π — so the table's entries
    # are checkable against arithmetic rather than against themselves.
    assert parent_selection_bias([won(se=1.0, electable=2)]) == pytest.approx(
        1 / math.sqrt(math.pi), abs=5e-5
    )
    # Linear in the winner's own SE: a sharply-measured winner carries almost no curse.
    assert parent_selection_bias([won(se=0.25, electable=2)]) == pytest.approx(
        0.25 / math.sqrt(math.pi), abs=2e-5
    )
    # A LONE electable arm was selected against nothing, so there is no maximum and no curse —
    # and the default ``electable_count`` of 0 clamps into that same safe end, never inventing
    # a correction for a round that never recorded how many arms it had.
    assert parent_selection_bias([won(se=0.9, electable=1)]) == 0.0
    assert parent_selection_bias([round_result(1, prompt_fields={"lineage": {"id": "w"}})]) == 0.0
    # Monotone in k, and CLAMPED past the table's end rather than extrapolated or IndexError-ing
    # on a round this loop does not produce.
    by_k = [parent_selection_bias([won(se=1.0, electable=k)]) for k in range(1, 7)]
    assert by_k == sorted(by_k) and by_k[0] == 0.0
    assert parent_selection_bias([won(se=1.0, electable=99)]) == pytest.approx(by_k[-1])

    # The STANDING parent is what the next round must beat, so the term is the one that crowned
    # it — the most recent round with a winner. A HELD round crowned nobody and must be walked
    # PAST, not read as "no curse": reading the newest round unconditionally zeroes the term for
    # every round after the first hold, which is most of a long cycle.
    assert parent_selection_bias(
        [won(se=0.40, electable=6), held(), won(se=0.10, electable=2), held()]
    ) == pytest.approx(0.10 / math.sqrt(math.pi), abs=2e-5)
    # No crowned round at all, and a winner with no θ fit (a cold ruler stamps none): 0.0, the
    # safe end again — an absent SE may not be defaulted into a correction nobody measured.
    assert parent_selection_bias([held(), held()]) == 0.0
    assert parent_selection_bias([]) == 0.0
    assert parent_selection_bias([won(se=None, electable=4)]) == 0.0

    # ---- and what it does to an election ---------------------------------------------
    ruler = _ruler(dict.fromkeys(range(28), 0.0))
    parent = measurements([1.0] * 15 + [0.0] * 13)
    challenger = measurements([1.0] * 13 + [0.0] * 15)  # two cells behind on the same panel
    deficit = -(theta_lift_over_parent(candidate_abilities({"c": challenger}, parent, ruler), "c"))
    assert deficit > 0.0, "the challenger must LOOK worse, or there is no bar to lower"

    def elect(bias: float) -> str:
        return elect_round_winner(["c"], {"c": challenger}, parent, 6, ruler, parent_bias=bias)[0]

    # Sized against the term itself, not a hardcoded z: a round whose winner SE puts the curse
    # just OVER the apparent deficit, and one that puts it just under.
    z = parent_selection_bias([won(se=1.0, electable=3)])
    covers = [won(se=deficit * 1.10 / z, electable=3)]
    tight = [won(se=deficit * 0.90 / z, electable=3)]

    # Uncorrected, the arm is refused — which is right only if the parent's θ were its ability.
    assert elect(0.0) == ""
    # The curse covers the deficit ⇒ the arm is crowned. This is the whole point of the term.
    assert elect(parent_selection_bias(covers)) == "c"
    # It does not ⇒ still refused. A term that admitted here would admit anything.
    assert elect(parent_selection_bias(tight)) == ""
    # …and the ~41% the paired √2 SE adds is exactly enough to crown it anyway. THE regression.
    assert elect(parent_selection_bias(tight) * math.sqrt(2.0)) == "c"


def test_leader_eligibility_bars_invalid_measurement_not_stops():
    """Winner-selection eligibility: fatal degradation disqualifies; a true PoBB *loss*
    (p_best < epsilon, lead not locked) disqualifies — the eliminator's own verdict that the
    candidate isn't the best; a LEADER_LOCKED stop stays eligible; a clean loser stays eligible.
    """
    fatal = _cs(
        candidate_id="C1.3",
        accuracy=0.8333,
        escalation_aborted=True,
        elimination_stopped=True,
        degradation_context={"fatal": True, "dominant_warning": "llm_only:empty_response"},
    )
    # A STOP IS NOT A VERDICT. A PoBB-stopped candidate stays electable: the stop said
    # "more samples will not change the answer", which is a budget fact, not a ranking one.
    # Reading it as a loss cost a real round — a candidate cut at 19/28 carried a genuine
    # +0.099 theta lift over origin and was the best thing measured, but its stop recorded
    # `p_best: 0.0` (a placeholder the futility gate never computed) and eligibility read
    # that as "PoBB says it lost". Every candidate in that round stopped the same way, so
    # the round crowned nobody. SILENT: `improved=False`, no winner, no reason recorded, and
    # the loop reports a flat cycle rather than a discarded improvement.
    pobb_stopped = _cs(
        candidate_id="C1.2",
        accuracy=0.40,
        elimination_stopped=True,
        elimination_context={"p_best": 0.048, "epsilon": 0.05, "gate": "epsilon"},
    )
    leader_locked = _cs(
        candidate_id="C1.1",
        accuracy=0.55,
        elimination_stopped=True,
        elimination_context={"p_best": 0.96, "epsilon": 0.05, "gate": "lock_in"},
    )
    clean_loser = _cs(candidate_id="C1.4", accuracy=0.45)

    # Only INVALID measurement disqualifies; ranking is the election's job alone.
    assert not is_leader_eligible(fatal)
    assert is_leader_eligible(pobb_stopped)
    assert is_leader_eligible(leader_locked)
    assert is_leader_eligible(clean_loser)


def test_a_round_that_measured_nothing_usable_names_which_way_it_broke():
    """Three ways a round produces no usable measurement. Each must NAME itself, because the
    next move differs completely between them — and every one of the three used to grade
    ``healthy`` or abstain.

    ``unscoreable`` (JustLogic): the pipeline RUNS — every ``step_status`` success, no warning —
    and emits no extractable label. The backend calls that a success, so the structural/transient
    channel is blind, and the loop spent rounds optimizing a prompt whose every output was
    unreadable. ``origin_unmeasured`` (L4): round-0 scoring produced ZERO rows, and graded
    healthy or abstained, candidates are then elected against NO baseline — the irreversible
    one. ``backend_unreachable``: every row errored, and since ``total`` counts only evidence
    rows, a round that ATTEMPTED work must not slip out as "nothing measured".

    Only the ORIGIN halts, and only on a real break: a non-origin round that measured nothing
    abstains rather than fabricating a verdict, and a wrong-but-extractable round IS a
    measurement — real labels, just wrong — which must stay gradable on accuracy."""
    from promptpotter.application.runner.termination import origin_gate_tripped
    from promptpotter.domain.phases import StopReason
    from promptpotter.domain.results_health import compute_round_health

    def answered(predicted: str) -> list[dict]:
        return [_health_row({"llm_only": "success"}, predicted=predicted) for _ in range(20)]

    unscoreable = compute_round_health(results=answered("NO_RESULT"), prior_healths=[])
    assert unscoreable is not None
    assert (unscoreable.grade, unscoreable.cause) == ("critical", "unscoreable")
    assert unscoreable.no_result_count == 20
    assert unscoreable.suggested_action and "answer_format" in unscoreable.suggested_action

    # …and the SAME rows without a label are a healthy round, not the same verdict. A
    # verifier-graded backend (Harbor: one containerized agent episode, graded by the task's own
    # `tests/test.sh`) has nothing for a ranker to emit, so its rows carry the NO_RESULT sentinel.
    # Read as unscoreable that grades the origin `critical` at 100%, tells the operator to fix an
    # `answer_format` that decides nothing, and reports a baseline that WAS measured — the reward
    # came back — as unmeasured. The LABEL is what separates the two cases and the sentinel is
    # not: whether it fires at all is a dataset's `node_role` declaration, so the other labelless
    # backend (L4, which declares `l1_critique` a ranker) never carries it.
    graded_by_verifier = [
        _health_row({"agent": "success"}, predicted="NO_RESULT", ground_truth="") for _ in range(20)
    ]
    verifier_round = compute_round_health(results=graded_by_verifier, prior_healths=[])
    assert verifier_round is not None
    assert (verifier_round.grade, verifier_round.cause) == ("healthy", None)
    assert verifier_round.no_result_count == 0

    unmeasured = compute_round_health(results=[], prior_healths=[], is_origin=True)
    assert unmeasured is not None
    assert (unmeasured.grade, unmeasured.cause) == ("critical", "origin_unmeasured")

    dead = compute_round_health(
        results=[
            {"error": "connect timeout", "error_category": "CONNECTION", "pipeline_data": None}
            for _ in range(10)
        ],
        prior_healths=[],
    )
    assert dead is not None
    assert (dead.grade, dead.cause) == ("critical", "backend_unreachable")
    assert dead.samples == 10 and dead.suggested_action is not None

    # Every one halts even in the LEAST-strict armed mode — that is the guarantee that a broken
    # origin never silently enters L1.
    for broken in (unscoreable, unmeasured, dead):
        assert origin_gate_tripped(broken, "critical_only") == StopReason.ORIGIN_GATE

    # A hard task emitting REAL labels is a measurement, not a broken floor.
    wrong = compute_round_health(results=answered("FALSE"), prior_healths=[])
    assert wrong is not None
    assert wrong.no_result_count == 0 and wrong.cause != "unscoreable"
    assert origin_gate_tripped(wrong, "critical_only") is None
    # …and a NON-origin round that measured nothing abstains, never a fabricated ``healthy``.
    assert compute_round_health(results=[], prior_healths=[]) is None


# 5. PoBB — who is eliminated, and when

_DUMMY_SP = JobSearchPoint()


def test_pobb_epsilon_is_graded_by_depth_not_scalar():
    """A raised ε must not spend its aggression at ``n_min``, where ONE discordant sample already
    drives ``p_best`` to ~0.2, NOR carry it into the tail, where cutting saves almost nothing. The
    bar is ``epsilon_floor`` at both ends and the full ``epsilon`` in the middle, ramping over
    ``n_min`` cells each side. Equal floor and ε — the default — leaves the bar flat.

    The ramp-OUT is the half that was missing: the bar sat at its maximum from ``2 * n_min`` until
    the tail guard switched cutting off, so an arm deep in its budget faced the same bar as one
    with the whole panel still to save. It lands on the floor exactly where the guard begins, so
    the two meet instead of cliffing.

    The ramp itself is what this pins. The DEPTHS below are a consequence of the dispersion rule
    and moved by one sample when φ stopped being floored at a constant (`fit_theta_given_delta`):
    an honest posterior is wider, so the near-tie dies at 9 rather than 8. Re-derive them, do not
    restore them, if that rule changes again — and they were, when the ~0.2 the first paragraph
    names stopped being something the bar rides out and became something `elimination_p_best`
    refuses to claim. A one-cell verdict is now uncuttable at every depth, so the arm carrying the
    ramp has to be one the pairs can actually speak about."""
    cfg = PoBBConfig(n_min=6, epsilon=0.30, epsilon_floor=0.15)
    graded = PoBBCheck(cfg, n_samples=28, ruler=None)
    assert graded.epsilon_at(6) == pytest.approx(0.15)
    assert graded.epsilon_at(9) == pytest.approx(0.225)
    assert graded.epsilon_at(12) == pytest.approx(0.30)
    # Clamped, never extrapolated: the ramp must not carry the bar ABOVE ε at any depth.
    assert graded.epsilon_at(15) == pytest.approx(0.30)
    assert max(graded.epsilon_at(n) for n in range(cfg.n_min, 29)) == pytest.approx(0.30)
    # …and back down to the floor as the remaining budget — all cutting can still save — runs out.
    assert graded.epsilon_at(20) == pytest.approx(0.20)
    # The last cuttable depth (`n_samples - n_min`) is where the guard takes over, at the floor.
    assert graded.epsilon_at(22) == pytest.approx(0.15)

    flat = PoBBCheck(
        PoBBConfig(n_min=6, epsilon=0.30, epsilon_floor=0.30), n_samples=28, ruler=None
    )
    assert flat.epsilon_at(6) == pytest.approx(0.30)
    # Shipped defaults sit floor and ε on the same constant, so an untouched config never grades.
    shipped = PoBBCheck(PoBBConfig(), n_samples=28, ruler=None)
    assert shipped.epsilon_at(shipped.n_min) == pytest.approx(shipped.epsilon)

    def arm_behind_perfect_prior(n: int, misses: int):
        check = PoBBCheck(cfg, n_samples=28, ruler=None)
        check.register_completed(measurements([1.0] * 28), candidate_id="winner", sp=_DUMMY_SP)
        check.set_current("arm")
        return check.check(
            measurements([0.0] * misses + [1.0] * (n - misses)),
            candidate_idx=1,
            n_total_candidates=2,
        )

    # One discordant loss is uncuttable at EVERY depth, not merely reprieved at the floor: a single
    # adverse cell caps p_best at 0.25, above every bar this ramp reaches.
    assert arm_behind_perfect_prior(6, 1) is None
    assert arm_behind_perfect_prior(9, 1) is None
    assert arm_behind_perfect_prior(22, 1) is None
    cut = arm_behind_perfect_prior(9, 2)
    assert cut is not None
    # The bar that FIRED is what the decision archives — a reader must see the ramped 0.225 at
    # n=9, never the configured 0.30, or the record cannot explain its own cut.
    assert cut.check_result["epsilon"] == pytest.approx(0.225)
    # Two behind is still cut at the floor: the reprieve is for a width, not for a loser.
    assert arm_behind_perfect_prior(6, 2) is not None


def test_pobb_locks_in_dominant_leader():
    """Current candidate dominating prior past lock_in_n_min fires LEADER_LOCKED."""
    check = PoBBCheck(
        PoBBConfig(n_min=4, epsilon=0.05, lock_in=0.95, lock_in_n_min=8, leader_lock_in=True),
        n_samples=20,
        ruler=None,
    )
    check.register_completed(measurements([0.0] * 20), candidate_id="weak_prior", sp=_DUMMY_SP)
    check.set_current("strong_current")
    sig = check.check(measurements([1.0] * 8), candidate_idx=1, n_total_candidates=3)
    assert sig is not None
    assert sig.target == EscalationTarget.LEADER_LOCKED
    cr = sig.check_result
    assert cr["leader_id"] == "weak_prior"
    assert cr["p_best"] >= 0.95
    assert cr["queries_scored"] == 8
    # Two candidates remain unscored (idx=1 of 3).
    assert sig.candidates_skipped == 1


async def test_paired_pobb_breaks_lucky_prefix_leader_trap():
    """Lucky-prefix 100% leader must NOT auto-eliminate a candidate measured on hard samples.

    Round 1 leader-locks at 8/8 on the easy prefix {0..7}. Round 2 candidate
    sees the sorter's HARD subset {9,12,13,14,8} — samples the leader was never
    scored on. Unpaired PoBB would flatten the candidate to p_best ≈ 0 and
    unfairly eliminate it. Paired PoBB fixes this by (a) excluding priors that
    don't cover the candidate's set when no backfill is wired, and (b)
    backfilling the leader on the missing samples when a backfill_fn IS wired.
    """
    leader_samples = [0, 1, 2, 3, 4, 5, 6, 7]
    candidate_samples = [9, 12, 13, 14, 8]  # disjoint from leader; AIME's hard sorter order.

    # --- Branch (a): no backfill_fn ⇒ incomplete prior is excluded, no elimination.
    check_no_backfill = PoBBCheck(PoBBConfig(n_min=4, epsilon=0.05), n_samples=20, ruler=None)
    check_no_backfill.register_completed(
        measurements([1.0] * 8, sample_ids=leader_samples),
        candidate_id="R1_lucky_winner",
        sp=_DUMMY_SP,
    )
    check_no_backfill.set_current("R2_challenger")
    sig = check_no_backfill.check(
        measurements([0.0] * 5, sample_ids=candidate_samples),
        candidate_idx=0,
        n_total_candidates=6,
    )
    assert sig is None, (
        "lucky-prefix leader incomplete on candidate's hard samples must NOT cause "
        "elimination — old unpaired code would have unfairly fired here"
    )

    # --- Branch (b): backfill_fn returns honest leader scores on the hard set ⇒ paired
    # comparison shows the leader is not actually 100% (it misses 4/5), and the
    # candidate (0/5) is no longer overwhelmingly dominated — no elimination.
    backfill_calls: list[tuple[int, ...]] = []

    async def _stub_backfill(_sp, samples, prior_id):
        backfill_calls.append(tuple(s.id for s in samples))
        assert prior_id == "R1_lucky_winner", (
            "the backfill must be told WHOSE catch-up it is running; inheriting the "
            "foreground candidate's identity is what mis-filed C1.1's rows as C1.2's"
        )
        # Leader misses 4/5 hard samples; only #13 is hit. Mirrors origin's
        # behavior on the same samples in the real cycle.
        truth = {9: False, 12: False, 13: True, 14: False, 8: False}
        return [
            {"sample_id": s.id, "fitness": (f := 1.0 if truth[s.id] else 0.0), "objective": f}
            for s in samples
        ]

    check_paired = PoBBCheck(
        PoBBConfig(n_min=4, epsilon=0.05), n_samples=20, ruler=None, backfill_fn=_stub_backfill
    )
    check_paired.register_completed(
        measurements([1.0] * 8, sample_ids=leader_samples),
        candidate_id="R1_lucky_winner",
        sp=_DUMMY_SP,
    )
    for sid in candidate_samples:
        await check_paired.backfill_for_sample(Sample(id=sid, query="q", ground_truth="t"))

    assert backfill_calls == [(9,), (12,), (13,), (14,), (8,)]
    leader_paired = check_paired.priors_by_sample["R1_lucky_winner"]
    assert all(str(sid) in leader_paired for sid in candidate_samples)

    check_paired.set_current("R2_challenger")
    sig = check_paired.check(
        measurements([0.0] * 5, sample_ids=candidate_samples),
        candidate_idx=0,
        n_total_candidates=6,
    )
    # Leader honest mean 1/5; candidate 0/5 — gap small ⇒ p_best must stay ≥ ε=0.05.
    if sig is not None:
        assert sig.check_result["p_best"] >= 0.05


def test_an_arm_in_the_last_n_min_cells_is_finished_not_discarded() -> None:
    """`n_min` at BOTH ends: an arm may not be judged on fewer than that many cells, and may not
    be discarded with fewer than that many left. Cutting in the tail saves almost nothing and
    costs the comparison outright — `matched_parent_stats` needs EVERY cell the parent measured,
    so an arm stopped one cell short is unrankable against the parent for the rest of the round.

    Live on `justlogic-d234__8ada8e` r1: an arm was cut at q27 of 28 having paid 96% of its cost,
    and the round then reported one readable arm and resolved nothing.

    Silent harm: the arm's rows are still banked, so the round LOOKS measured — it simply has
    nothing it can rank, and says so only in the `resolved nothing` line."""
    cfg = PoBBConfig(n_min=6, epsilon=0.30)

    def cut_at(n: int) -> object | None:
        check = PoBBCheck(cfg, n_samples=28, ruler=None)
        check.register_completed(measurements([1.0] * 28), candidate_id="winner", sp=_DUMMY_SP)
        check.set_current("arm")
        return check.check(measurements([0.0] * n), candidate_idx=1, n_total_candidates=2)

    # Hopeless arms in the BODY of the panel still die — the guard is a tail rule, not a reprieve.
    assert cut_at(9) is not None
    assert cut_at(22) is not None, "the last cut-eligible depth is n_samples - n_min"
    # …and in the tail they are finished instead, however far behind they are.
    assert cut_at(23) is None
    assert cut_at(27) is None, "one cell short of the panel is the case this exists for"


def test_a_collapse_cut_is_never_reported_as_an_epsilon_cut() -> None:
    """`elimination_stopped` covers two gates and only one measured anything: a collapse cut
    returns before the posterior, so `p_best`/`epsilon`/`n_priors`/`leader_id` are placeholders.
    Read through the ε fields it renders `0.0% < 15% vs ? (of 0 priors)` — four invented numbers —
    and tells the generator the strongest verdict the loop has was "NOT a verdict", leaving the
    collapsed idea free to be re-proposed.

    Silent harm: nothing errors, every field renders, and the number diagnosed from was never
    measured."""
    from promptpotter.application.optimization.dispatch.injections.panels import _candidate_fate
    from promptpotter.application.optimization.pobb.checks import PoBBCheck, PoBBConfig
    from promptpotter.domain.results import EliminationGate

    rows = [
        {
            "sample_id": i,
            "hit": i % 2 == 0,
            "fitness": 1.0 if i % 2 == 0 else 0.0,
            "predicted": "Uncertain",
            "ground_truth": "TRUE" if i % 2 == 0 else "FALSE",
        }
        for i in range(6)
    ]
    signal = PoBBCheck(PoBBConfig(), n_samples=28, ruler=None).check(rows, 0, 1)
    assert signal is not None, "a constant answerer must be cut at n_min"
    cr = signal.check_result
    assert cr["gate"] == EliminationGate.COLLAPSED
    # The ε fields were never computed, so nothing downstream may present one as measured.
    assert not {"p_best", "epsilon", "n_priors"} & cr.keys()

    def cand(cid: str, **ctx: object):
        return scored_candidate(
            cid,
            total=6,
            elimination_stopped=True,
            scored_samples=6,
            expected_samples=28,
            elimination_context={"queries_scored": 6, "total_queries": 28, **ctx},
        )

    collapsed = _candidate_fate(cand("collapsed", gate=EliminationGate.COLLAPSED), "sample")
    epsilon_cut = _candidate_fate(
        cand("eps", p_best=0.03, epsilon=0.15, gate=EliminationGate.EPSILON), "sample"
    )

    # What the GENERATOR is handed must invert between the two, and quote no ε number it lacks.
    assert "NOT a verdict" in epsilon_cut
    assert "NOT a verdict" not in collapsed and "VERDICT" in collapsed
    assert "ε" not in collapsed and "P(best)" not in collapsed


def test_only_an_epsilon_cut_banks_an_idea_as_measured_and_lost() -> None:
    """`lost_ideas` feeds the cross-round repeat gate, whose rejection is a synthetic-0 that never
    reaches an LLM. Only ε weighed the arm against its priors — LOCK_IN is the opposite verdict,
    COLLAPSED is the ABSENCE of a measurement, and a degradation cut names no gate because the node
    broke rather than the idea losing.

    Silent harm: banking any of the three blacklists an idea no round ever judged, and every later
    re-proposal is rejected as `repeat_variant` for the rest of the cycle with nothing raised."""
    from promptpotter.application.optimization.validators.l1_invariants import lost_ideas
    from promptpotter.domain.results import EliminationGate

    def banked(ctx: dict | None) -> bool:
        rnd = lost_round(1, "instruction", "count the premises first", elimination_context=ctx)
        return bool(lost_ideas([rnd]))

    assert banked({"gate": EliminationGate.EPSILON}), "an ε cut IS the measurement, and it lost"
    assert banked(None), "a plain accuracy loss is still a measured loss"
    assert not banked({"gate": EliminationGate.COLLAPSED})
    assert not banked({"gate": EliminationGate.LOCK_IN})
    assert not banked({}), "a degradation cut carries no gate — the node broke, not the idea"


def test_elimination_p_best_discriminates_on_graded_backend() -> None:
    """The PoBB ε-gate must read GRADED responses, not binarized hits.

    Silent harm: on a graded backend (L4 outer, reciprocal-rank) every ``hit`` is
    False, so a binarized gate fits identical all-0 θ for every arm and pins
    ``p_best = 0.5`` forever — elimination never discriminates, with no error.
    Graded inputs must separate a plainly-better candidate; binary inputs must be
    bit-identical to the historical hit-vector behavior.
    """
    from promptpotter.application.scoring.selection import elimination_p_best

    sids = list(range(12))
    ruler = None  # cold ruler — flat δ, the common early-cycle case

    # Graded regime: candidate consistently outscores the prior; hit would be all-0.
    strong = [0.66] * 12
    weak = [0.30] * 12
    p_best_strong, _ = elimination_p_best(strong, {"prior": weak}, sids, ruler)
    assert p_best_strong > 0.9, f"graded gate failed to discriminate: {p_best_strong}"
    p_best_weak, _ = elimination_p_best(weak, {"prior": strong}, sids, ruler)
    assert p_best_weak < 0.1
    # Identical grades ⇒ genuinely undecided.
    p_best_tie, _ = elimination_p_best(weak, {"prior": list(weak)}, sids, ruler)
    assert abs(p_best_tie - 0.5) < 1e-9

    # Binary regime: floats {0,1} are the same values the old bool vectors carried.
    cand_bits = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0]
    prior_bits = [1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    p_best_bin, _ = elimination_p_best(cand_bits, {"prior": prior_bits}, sids, ruler)
    p_best_bool, _ = elimination_p_best(
        [bool(b) for b in cand_bits],  # type: ignore[list-item]
        {"prior": [bool(b) for b in prior_bits]},  # type: ignore[dict-item]
        sids,
        ruler,
    )
    assert p_best_bin == p_best_bool  # bit-identical for binary datasets
    assert p_best_bin > 0.5


def test_a_cut_needs_more_than_one_cell_that_told_the_arms_apart() -> None:
    """The ε bar is absolute, so the θ posterior must not spend confidence the pairs never bought.

    Silent harm: concordant cells move θ but say nothing about which arm is better, and
    ``build_round_order`` lands one parent-hit probe in the first six slots. Where the base rate is
    low enough that the candidate answers none of them, the prefix holds exactly ONE discordant
    cell — and the unbounded fit reads that as decisive, cutting every arm of every round at a
    p_best identical to six decimals. The bar must not be reachable on one cell, and must stay
    reachable on three, or the guard has bought calibration with a gate that never fires.
    """
    from promptpotter.application.scoring.selection import elimination_p_best
    from promptpotter.config.settings import POBB_DEFAULT_EPSILON
    from promptpotter.domain.ruler import DeltaRuler

    sids = list(range(6))
    # `sealqa-longseal-12`'s own geometry: the probe the ordering lands at slot 4 is also the
    # EASIEST cell in the prefix, which is what let the fit read one cell as decisive.
    ruler = DeltaRuler(
        delta={0: 2.90, 1: 3.01, 2: 2.66, 3: 1.39, 4: 2.85, 5: 2.95},
        delta_se=dict.fromkeys(sids, 0.4),
        mu_delta=2.6,
        sigma_delta=0.6,
        sigma_theta=1.0,
        calibration_model="1PL",
        anchor_id="test-anchor",
    )
    candidate = [0.0] * 6  # the low-base-rate arm: nothing solved anywhere in the prefix

    thin_prior = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0]  # that probe, and nothing else
    p_thin, _ = elimination_p_best(candidate, {"prior": thin_prior}, sids, ruler)
    assert p_thin == pytest.approx(0.25), f"one adverse cell may not reach past 0.25: {p_thin}"
    assert p_thin > POBB_DEFAULT_EPSILON, "a one-cell verdict must not be cuttable (unbounded: .11)"

    wide_prior = [1.0, 0.0, 1.0, 1.0, 0.0, 0.0]  # a prior that genuinely outscored it
    p_wide, _ = elimination_p_best(candidate, {"prior": wide_prior}, sids, ruler)
    assert p_wide == pytest.approx(0.0625), f"three adverse cells support a cut: {p_wide}"
    assert p_wide < POBB_DEFAULT_EPSILON, "the width is there — ε decides, as it always did"


def test_a_fatal_row_ends_a_candidate_on_one_sighting_and_an_advisory_never_does() -> None:
    """``DegradationCheck`` is what stops paying for an arm the backend has broken, and both
    directions are silent. Fast-cutting on an ADVISORY warning eliminates an arm that is scoring
    fine — `web_search:low_document_count` fires whenever fewer than max_sites docs are gathered,
    so on a slow index it would cut every candidate in the round and the loop would report a
    winner nobody could beat. Not cutting on a FATAL one keeps buying cells from a node that
    cannot answer, and every one of them lands in the panel as a measured miss.

    The rate arm is the same fact one step slower: only genuinely-deprecated rows count toward the
    threshold, so an arm at 2/6 advisory survives while 3/6 fatal does not."""
    from promptpotter.application.optimization.pobb.checks import DegradationCheck

    def warn(kind: str) -> dict:
        return {
            "pipeline_data": {
                "diagnostics": {
                    "warnings": [
                        {"step": "entity_profiling", "code": "json_validate_failed", "kind": kind}
                    ]
                }
            }
        }

    check = DegradationCheck(threshold=0.4, min_samples=3)

    # ONE fatal sighting ends it, before `min_samples` is even reached.
    sig = check.check([measurement(0, 1.0), {**measurement(1, 1.0), **warn("structural")}], 0, 3)
    assert sig is not None
    assert sig.check_result["fatal"] is True
    assert sig.check_result["dominant_warning"] == "entity_profiling:json_validate_failed"
    assert sig.candidates_skipped == 2

    # An advisory sighting is not an elimination at ANY depth.
    advisory = [{**measurement(i, 1.0), **warn("transient")} for i in range(6)]
    assert check.check(advisory, 0, 3) is None

    # Below `min_samples` a non-fatal round decides nothing rather than deciding on two rows.
    assert check.check([measurement(0, 1.0), measurement(1, 0.0)], 0, 3) is None

    # The rate arm, with the fast path off so the threshold is what is under test.
    rated = DegradationCheck(threshold=0.4, min_samples=3, fatal_fastpath=False)
    fatal_row = {**measurement(0, 0.0), **warn("structural")}
    clean = [measurement(i, 1.0) for i in range(1, 6)]
    assert rated.check([fatal_row, *clean[:4]], 0, 3) is None  # 1/5 = 0.2, under the bar
    cut = rated.check([fatal_row, {**fatal_row, "sample_id": 9}, *clean[:2]], 0, 3)
    assert cut is not None and cut.check_result["degraded_rate"] == pytest.approx(0.5)


# 6. Which cells a round buys


def test_build_round_order_fronts_win_opportunities_with_hit_probes() -> None:
    """The shared round order is the elimination gate's evidence pipeline: parent-miss
    (win-opportunity) samples front-loaded ascending-δ, a parent-hit regression probe at
    every 4th slot descending-δ, cells the parent NEVER ANSWERED in their own stratum
    ordered by discrimination, deterministic tie-breaks. Silent harm: a wrong order
    re-creates the tie-prefix blindness — the round completes, no error, and dead
    candidates ride their full budget again.

    A known miss is a measured opportunity and an unknown is a gamble, so the knowns drain
    first. Filing unknowns as misses conflated the two: `is_hit` returns False for `None`
    and for a miss alike, and under `per_round_resubset` round 1's panel shares no cell
    with the parent, so EVERY cell became a win-opportunity sorted ascending and the panel
    led with its easiest. Live on `justlogic-d234__8f6499` r1 that put three cells nothing
    has missed in 8-10 archived measurements in the first six, and the ε-gate cut an arm on
    one discordant cell. The all-unknown arm below is that case; the mixed arm above it is
    why unknowns still may not simply be dropped to the tail of a round that has real misses."""
    from promptpotter.application.intelligence.adaptive_queue_mechanism import build_round_order

    ids = list(range(24))
    # Parent hits 0-8; misses 9-22; sample 23 never answered by the parent (unclassified).
    parent_grades = dict.fromkeys(range(9), 1.0) | dict.fromkeys(range(9, 23), 0.0)
    ruler = _ruler({sid: float(sid % 7) for sid in range(20)}, mu=2.85)

    order = build_round_order(parent_grades, ruler, ids)
    assert sorted(order) == ids
    # Deterministic: same inputs, same order.
    assert build_round_order(parent_grades, ruler, ids) == order

    hit_set = set(range(9))
    # Positions 4, 8, 12, 16 (1-indexed) carry parent-HIT probes while both strata remain.
    for pos in (4, 8, 12, 16):
        assert order[pos - 1] in hit_set, f"position {pos} should be a parent-hit probe"
    # All other early positions are win opportunities, never regression probes.
    non_probe_head = [order[i] for i in range(16) if (i + 1) % 4 != 0]
    assert all(sid not in hit_set for sid in non_probe_head)
    # Computed, not spelled: a hardcoded default here would pass while disagreeing with the ruler.
    unmeasured = ruler.mu_delta
    assert min(ruler.delta.values()) < unmeasured < max(ruler.delta.values())
    # MISS stratum walks ascending δ (a miss the ruler has no δ for still sits at the centre),
    # and every one is reached before the unknown GRADE: an opportunity the parent actually
    # failed outranks one nobody has tried.
    miss_positions = [sid for sid in order if sid not in hit_set and sid != 23]
    miss_keys = [(ruler.delta.get(sid, unmeasured), sid) for sid in miss_positions]
    assert miss_keys == sorted(miss_keys)
    assert order.index(23) > max(order.index(sid) for sid in miss_positions)
    # HIT stratum walks descending δ (likeliest regression points first).
    hit_positions = [sid for sid in order if sid in hit_set]
    hit_keys = [(-ruler.delta.get(sid, unmeasured), sid) for sid in hit_positions]
    assert hit_keys == sorted(hit_keys)

    # The round-1 case, and the one the two-state predicate got wrong: the parent has answered
    # NOTHING on this panel, so there is no miss stratum to front-load and no hit stratum to
    # probe from. The order must then lead with the cells that discriminate most — δ nearest the
    # scale's centre — not with the panel's easiest, which separate no arm from any other.
    all_unknown = build_round_order({}, ruler, ids)
    assert sorted(all_unknown) == ids
    spread = [abs(ruler.delta.get(sid, unmeasured) - unmeasured) for sid in all_unknown]
    assert spread == sorted(spread), (
        "an all-unknown panel must open on its most discriminating cells"
    )
    easiest = min(ruler.delta, key=lambda sid: (ruler.delta[sid], sid))
    assert all_unknown.index(easiest) >= 6, "the panel's easiest cell must not lead the round"


def test_ruler_learning_cannot_take_the_whole_round_panel() -> None:
    """The panel is bought to SEPARATE THE ARMS; refining δ gets a bounded reservation, not a vote.

    `pick_value` summed `decision_information_gain + delta_learning_gain`, and the second rises with
    δ's SE while the first is capped by an entropy — so wherever a difficulty exists in both a
    well-measured and a barely-measured cell, the sum takes the barely-measured one every time and
    the panel becomes a ruler-refinement queue. Live on `justlogic-d234` that bought 28 cells the
    archive passes 1.6% of the time; once those were measured and their SE fell, the next round
    bought cells it passes ~100% of. Both scored every arm identically and separated nothing.

    Dropping the term is not the fix either: a pure-decision panel converges on one difficulty
    (0.93 logits on the live ruler, under `BAND_COLLAPSE_LOGITS`), which is the state where θ is
    logit-accuracy plus a constant and ranking on it ranks on accuracy.

    Silent harm: every arm scores ~0 or ~1, `improved` reads false or trivially true, and the
    headline moves with panel composition while nothing reports that the panel moved.
    """
    from promptpotter.application.intelligence.exploration import (
        Observation,
    )
    from promptpotter.domain.sample import Sample

    # A bank shaped like a real one: a DENSE mid-difficulty cluster the arms have hammered, in
    # two measurement states, plus a SPARSE tail nobody has answered often. That shape is what
    # separates the three rankings — an SE tied smoothly to |δ| never exercises it, because there
    # the coarse cells sit where the decision term is already near zero and it wins on its own.
    delta: dict[int, float] = {}
    delta_se: dict[int, float] = {}
    n = 0
    for i in range(100):
        d = -0.6 + 1.2 * i / 99
        delta[n], delta_se[n] = d, 0.20
        n += 1
        delta[n], delta_se[n] = d, 0.90
        n += 1
    for i in range(50):
        delta[n], delta_se[n] = -4.33 + 8.66 * i / 49, 1.50
        n += 1
    ruler = _ruler(delta, se=delta_se)
    bank = [Sample(id=str(sid), query="q", ground_truth="g") for sid in sorted(delta)]
    # One arm in the race, sitting mid-scale: it passes the easy half and misses the hard half.
    own = [Observation("leader", sid, 1.0 if delta[sid] < 0 else 0.0) for sid in delta]
    budget = 28

    panel = [
        int(s.id)
        for s in select_round_subset(bank, own, budget, ruler=ruler, leader_ids={"leader"})
    ]

    assert len(set(panel)) == budget, "the panel duplicated or dropped cells"

    # A MINORITY, not the reservation's exact size: a coarse cell sitting near the leader can
    # legitimately win a decision slot on its own merits. What may never happen is the panel
    # being made of them, which is what the summed acquisition did — 28 of 28.
    coarse = {sid for sid, se in delta_se.items() if se > 1.0}
    from_coarse = len(coarse & set(panel))
    assert from_coarse <= budget // 3, (
        f"{from_coarse} of {budget} panel cells are the ones δ is least sure of — "
        "ruler learning outvoted the decision the panel exists to make"
    )

    span = max(delta[s] for s in panel) - min(delta[s] for s in panel)
    assert span > 1.0, f"panel spans {span:.2f} logits — a collapsed band is not a reading"


def test_the_panel_is_calibrated_to_the_arm_in_the_race_not_the_archive_best() -> None:
    """The round buys cells that separate THIS cycle's arms; the archive only lends the δ scale.

    ``select_round_subset`` is handed ``[*archive_observations, *cycle.rounds]`` because a θ fit
    wants every arm it can get, and it took ``max`` over all of them — the best searchpoint ever
    run on the dataset, which is not in this race. Live on `justlogic-d234` that was θ 1.79 against
    a parent at −0.01, so the panel came out two logits too hard: 28 cells the archive passes 1.6%
    of the time, every arm scored ~0, and the round resolved nothing.

    It was harmless while a flat ruler made every θ equal, which is why it surfaced only once δ
    was real — repairing an instrument is what lets the next defect downstream of it bite.

    Silent harm: the panel is legitimately hard, every number renders, and `improved: false` reads
    as "no candidate was better" rather than "nobody could have been measured".
    """
    from promptpotter.application.intelligence.exploration import (
        Observation,
    )
    from promptpotter.domain.sample import Sample

    delta = {i: -4.33 + 8.66 * i / 99 for i in range(100)}
    # Uniform SE, so ruler-learning cannot confound the read.
    ruler = _ruler(delta, se=0.50)
    bank = [Sample(id=str(sid), query="q", ground_truth="g") for sid in sorted(delta)]
    # The arm under test sits mid-scale; a far stronger arm rides in from the archive.
    leader = [Observation("leader", sid, 1.0 if delta[sid] < 0 else 0.0) for sid in delta]
    archive_star = [Observation("star", sid, 1.0 if delta[sid] < 2.5 else 0.0) for sid in delta]

    panel = [
        int(s.id)
        for s in select_round_subset(
            bank, [*archive_star, *leader], 28, ruler=ruler, leader_ids={"leader"}
        )
    ]
    mean_delta = sum(delta[s] for s in panel) / len(panel)

    # Near the arm in the race (θ ≈ 0), not near the archive's best (θ ≈ +2.6).
    assert abs(mean_delta) < 1.0, (
        f"panel centred at δ {mean_delta:+.2f} — calibrated to an arm that is not in this round"
    )


def test_panel_precision_names_the_lever_the_panel_needs() -> None:
    # A candidate's `matched_parent_lift` interval is computed from the spread of the per-cell
    # diffs and nothing else, so it cannot distinguish two opposite problems: cells that each
    # measured themselves badly, versus cells that genuinely disagree. They take opposite levers —
    # sharpen the instrument, or widen the panel/candidates — and the operator picks from these two
    # numbers. SILENT: every wrong value here is a plausible-looking bar that sends the next spend
    # at the wrong problem.
    from promptpotter.domain.l4.proxies import PARENT_LEVEL_SE_KEY, panel_precision

    # The on-disk key, pinned as a literal: it is a wire contract between the emit site, the
    # infra-key allow-list and the reader, and a rename that moved only the constant would
    # silently stop the panel finding any SE at all — which reads as "no cells", not as an error.
    assert PARENT_LEVEL_SE_KEY == "mean_parent_level_se"

    def rows(cells: dict[str, tuple[float, float]]) -> list[dict]:
        return [
            {
                "query": c,
                "fitness": (v + 1.0) / 3.0,
                "pipeline_data": {"mean_round_delta": v, "mean_parent_level_se": se},
            }
            for c, (v, se) in cells.items()
        ]

    flat_origin = rows(dict.fromkeys("abc", (0.0, 0.02)))

    # Cells that AGREE (diffs .50/.52/.48) but each measured itself poorly: the panel is reading
    # its own noise, and buying more cells like these buys nothing.
    noisy = panel_precision(
        rows({"a": (0.50, 0.40), "b": (0.52, 0.40), "c": (0.48, 0.40)}), flat_origin
    )
    assert noisy is not None
    # Claimed noise EXCEEDS the total spread it is a component of — impossible, and that
    # impossibility IS the finding. The retired `estimation_share` divided these and clamped with
    # `min(1.0, …)`, rendering a raw 5.55 as a tidy "100% measurement noise". Serving both bars
    # unreduced is what makes the contradiction visible instead of plausible.
    assert noisy.estimation_sd > noisy.observed_sd

    # Cells that genuinely DIFFER (.10/.90/.50) while each is measured sharply: the spread is
    # signal about where this optimizer prompt works, not instrument noise.
    real = panel_precision(
        rows({"a": (0.10, 0.02), "b": (0.90, 0.02), "c": (0.50, 0.02)}), flat_origin
    )
    assert real is not None and real.estimation_sd < real.observed_sd / 10
    assert real.observed_sd == pytest.approx(0.4)  # SAMPLE sd (n-1), not the population one

    # BOTH ARMS' errors enter, in quadrature. The variant and the origin are separate inner
    # campaigns on the same cell, so counting only the variant's understates the diff's error by
    # sqrt(2) — which makes a noise-dominated panel read as half signal.
    assert real.estimation_sd == pytest.approx((2 * 0.02**2) ** 0.5)
    assert real.n_cells == 3

    # One shared cell has no spread to decompose: None, never a fabricated 0.0 (which reads as
    # "all signal" and would argue for spending on more cells at the exact moment it cannot know).
    assert panel_precision(rows({"a": (0.5, 0.02)}), rows({"a": (0.0, 0.02)})) is None
    # A cell whose rows carry no SE is dropped rather than guessed at.
    assert (
        panel_precision(
            [{"query": "a", "fitness": 0.5, "pipeline_data": {"mean_round_delta": 0.5}}],
            [{"query": "a", "fitness": 0.3, "pipeline_data": {"mean_round_delta": 0.0}}],
        )
        is None
    )


def test_overlap_set_is_one_every_member_actually_answered() -> None:
    """The 1-to-1 bars rest on one property: every member of the parent line has answered every
    cell of the set its rate is read over. Break it and each bar still renders — over a smaller
    denominator, at a rate nothing measured, side by side as if comparable.

    Also pins the three rules that keep the set affordable and honest: a HELD round's parent
    re-score widens the parent's coverage; the panel is the ORIGIN's own cells and does not move,
    so a late member is topped up onto it rather than narrowing it for everyone before it; and a
    member is a CONFIGURATION — the RENDERED target prompt, not the six fields and not a lineage
    id. An L2/L3 transition re-mints the parent's OSP from the same fields, and `task_context`
    moves the render without moving the fields; either one mistaken puts two individuals' cells
    inside one bar, at a rate neither of them scored.
    """
    from promptpotter.domain.opt_search_point import IndividualLineage, OptSearchPoint
    from promptpotter.domain.results import (
        ScoredCandidate,
        measured_cells,
        origin_panel,
        parent_line,
    )

    def rows(*ids: int) -> list[dict[str, object]]:
        return [{"sample_id": i, "fitness": 1.0} for i in ids]

    def scored(cid: str, label: str) -> ScoredCandidate:
        return ScoredCandidate(
            candidate_id=cid, label=label, accuracy=0.5, composite_fitness=0.5, total=1
        )

    def rnd(
        n: int, cid: str, label: str, instruction: str, *ids: int, upstream: str = ""
    ) -> RoundResult:
        # `instruction` and `upstream` BOTH make the configuration here — the second only
        # through the render, which is the whole point.
        osp = OptSearchPoint(
            instruction=instruction,
            lineage=IndividualLineage(id=cid),
            memory={"task_context": {"upstream_context": upstream}},
        )
        return RoundResult(
            round=n,
            label=label,
            accuracy=0.5,
            total=len(ids),
            improved=n > 0,
            prompt_fields={**osp.prompt_field_dict(), "lineage": osp.lineage.model_dump()},
            results=rows(*ids),
            candidates_scored=1,
            candidate_scores=[scored(cid, label)],
            opt_sp=osp,
        )

    # C0 on 1..6; round 1 HELD (C0 re-scored on 7,8); round 2 crowned C2.1 on 5,6,7,9; round 3
    # HELD but an L2 transition re-minted the parent — same instruction, new id, no label.
    history = [
        rnd(0, "c0", "C0", "base", 1, 2, 3, 4, 5, 6),
        rnd(1, "c0", "C0", "base", 7, 8),
        rnd(2, "w2", "C2.1", "edited", 5, 6, 7, 9),
        rnd(3, "l2-remint", "C3.1", "edited", 5, 6),
    ]
    line = parent_line(history)
    # TWO members, not three: the L2 re-mint is the same configuration as C2.1, so it folds in
    # and keeps C2.1's label rather than appearing beside it as an unnamed twin.
    assert [(s.candidate_id, s.label) for s in line] == [("c0", "C0"), ("w2", "C2.1")]

    # A round-4 winner carrying C2.1's six fields verbatim and an emptied `task_context` is a
    # DIFFERENT individual: it runs a shorter prompt. Folded in, it would take C2.1's label and
    # bar, and its rows would overwrite C2.1's on every cell they share.
    ctx = parent_line([*history, rnd(4, "w4", "C4.1", "edited", 5, 6, 7, upstream="framing")])
    assert [s.candidate_id for s in ctx] == ["c0", "w2", "w4"]
    # The held round WIDENED the parent rather than replacing it — without that, 7 and 8 are
    # lost and cell 7 could never join the set below.
    assert measured_cells(line[0].rows) == {1, 2, 3, 4, 5, 6, 7, 8}

    c0, w2 = measured_cells(line[0].rows), measured_cells(line[1].rows)
    panel = origin_panel(c0, poolable=range(100), size=4)
    # THE invariant: C0 has answered every cell of the panel, so every bar is read on one exam
    # and C0 itself is never asked to re-measure.
    assert set(panel) <= c0
    # 9 is w2's but C0 never answered it, so it cannot be a shared basis at any price.
    assert 9 not in panel

    # FIXED, and a function of the ORIGIN alone: w2 shares NOT ONE cell with the panel and the
    # panel does not move an inch for it. An intersection over the members would contract to
    # nothing here; the cost of holding it still is w2 buying its own four cells, once.
    assert set(panel).isdisjoint(w2)
    assert origin_panel(c0, poolable=range(100), size=4) == panel
    assert sorted(set(panel) - w2) == [1, 2, 3, 4]
    # A cell the cycle can no longer buy is out, or a member is short one with no way to sit it.
    assert origin_panel(c0, poolable={1, 2, 3}, size=4) == [1, 2, 3]


# 7. Paired readings over shared cells


def test_matched_parent_stats_refuses_a_prefix_it_cannot_measure():
    """A wrong number carried forward with no error — this file's own bar.

    ``build_round_order`` stratifies the round on the PARENT's grades: every 4th slot is
    a cell it passed, the rest are cells it missed. So origin's rate on a truncated
    candidate's prefix is ``⌊n/4⌋/n`` — set by where PoBB stopped, not by the data — and
    both halves of the comparison are conditioned on the outcome that chose the subset, so
    a candidate of identical ability outscores origin there by regression to the mean.
    Measured over the 32 truncated rows banked on disk, that prediction held exactly 28
    times; the 19 candidates cut at six samples every one reported 0.1667.

    Nothing raises when it is wrong: the value renders into the scoreboard, the
    ``mutation_memory`` panel L1 reasons from, and the L4 narrative's top-arm pick.
    """
    schema = _single_node_schema()
    # Origin scored 20 samples: 10 hits (samples 0-9 hit, 10-19 miss).
    origin_results = [
        {**_eval_result(hit=i < 10, score=1.0 if i < 10 else 0.0), "sample_id": i}
        for i in range(20)
    ]
    # Candidate stopped after 8 of the *hardest* samples (origin's misses, ids 10..17).
    # Origin reads 0/8 there — but it reads 0/8 for ANY candidate cut at that depth, which
    # is what makes it unusable rather than merely harsh.
    truncated = [
        {**_eval_result(hit=i < 13, score=1.0 if i < 13 else 0.0), "sample_id": i}
        for i in range(10, 18)
    ]
    assert matched_parent_stats(origin_results, truncated, schema) is None
    # Covered the whole panel → a real comparison, on the origin's own measured set.
    full = matched_parent_stats(origin_results, origin_results, schema)
    assert full is not None
    assert full["total"] == 20
    assert full["accuracy"] == pytest.approx(0.5)
    # DISJOINT (per_round_resubset can hand a candidate samples the origin never measured):
    # no shared basis at all, so no comparison. This previously fell back to origin's full
    # rate, publishing a floor measured on cells the candidate never ran.
    disjoint_candidate = [
        {**_eval_result(hit=True, score=1.0), "sample_id": i} for i in range(100, 108)
    ]
    assert matched_parent_stats(origin_results, disjoint_candidate, schema) is None


def test_matched_parent_lift_drops_the_cell_that_measured_nothing() -> None:
    # An ERRORED cell measured nothing, and here "nothing" is not a low score: outer fitness
    # transforms `mean_round_delta`, so a floored 0.0 asserts the optimizer prompt drove the inner
    # loop maximally DOWN. The ELECTION grades it 0.0 on purpose (`_mean_fitness_by_cell` is
    # un-predicated so the overlap guard works); a published interval must not, because it is drawn
    # beside a point estimate and has to bracket that estimate's own population — the rule
    # `mean_fitness_ci` already follows on the same row. SILENT: the row looks like any other, the
    # interval still prints, and the arm is convicted on a cell that never ran.
    from promptpotter.application.scoring.selection import matched_parent_lift

    clean = [{"query": q, "sample_id": i, "fitness": 0.9} for i, q in enumerate("abc")]
    origin = [{"query": q, "sample_id": i, "fitness": 0.5} for i, q in enumerate("abc")]
    errored = [
        *clean,
        {
            "query": "d",
            "sample_id": 3,
            "fitness": 0.0,
            "error_category": "UNKNOWN",
            "predicted": "ERROR",
        },
    ]

    lift = matched_parent_lift(errored, [*origin, {"query": "d", "sample_id": 3, "fitness": 0.5}])
    assert lift is not None
    assert lift[0] == pytest.approx(0.4)  # not dragged toward the floor by cell d
    assert lift[1] < lift[0] < lift[2]

    # Below two shared cells there is no spread, so no interval — reported as absence rather than
    # as the `_normal_posterior` n=1 fallback, which invents an SE of 0.5 out of one reading.
    assert matched_parent_lift(clean[:1], origin[:1]) is None


def test_paired_reading_matches_ttest_rel_and_brackets_the_same_evidence_it_tests() -> None:
    """Checked against an INDEPENDENT oracle, so a sidedness flip or a df off-by-one goes red
    rather than agreeing with itself. The interval and the p come from ONE posterior, so the
    silent failure this pins is the two disagreeing about zero — a bracket excluding it beside a
    p that does not, or the reverse. The floor case pins the documented deviation instead of
    hiding it: ``_normal_posterior`` clips the SE at ``1/(4n)``, so a near-constant difference
    reads far LESS significant here than a textbook paired t-test."""
    from scipy.stats import ttest_rel

    # Spread wide enough that the 1/(4n) floor does not bind, so the two must agree exactly.
    cand = [0.90, 0.10, 0.85, 0.20, 0.75, 0.30, 0.95, 0.05]
    prior = [0.10, 0.85, 0.15, 0.80, 0.20, 0.70, 0.05, 0.90]
    reference = float(ttest_rel(cand, prior).pvalue)

    mean_d, lo, hi, p_two, n = paired_reading(cand, prior)
    assert n == len(cand)
    assert abs(mean_d - sum(c - p for c, p in zip(cand, prior, strict=True)) / n) < 1e-12
    assert p_two is not None and abs(p_two - reference) < 1e-12

    # One posterior, one verdict: a p above 0.05 and a bracket clearing zero cannot coexist.
    assert lo is not None and hi is not None and lo < mean_d < hi
    assert (p_two < 0.05) == (lo > 0.0 or hi < 0.0)

    p_greater = paired_reading(cand, prior, tail="greater")[3]
    assert p_greater is not None and abs(p_greater - reference / 2.0) < 1e-12

    # Reading a pair in either order must give one number — the whole point of the two-sided test.
    assert paired_reading(prior, cand)[3] == p_two

    # The floor binds: a difference this tight is "significant" to a textbook test and must not be
    # to this one.
    tight_cand = [0.5000001 * i for i in range(1, 7)]
    tight_prior = [0.5 * i for i in range(1, 7)]
    tight_p = paired_reading(tight_cand, tight_prior)[3]
    assert tight_p is not None and tight_p > float(ttest_rel(tight_cand, tight_prior).pvalue)

    # One pair tests nothing and brackets nothing — absent, not a p of 1.0 nor a zero-width bar.
    assert paired_reading([0.5], [0.1])[1:4] == (None, None, None)


def test_a_scenario_chain_stops_where_the_record_parts() -> None:
    """The mask's silent failure: walking PAST the round the two readings part. Every step after
    it is judged against a parent the run never carried — no candidate was measured against it, and
    L1 would have generated a different population from it — yet those steps render exactly like
    the prefix that is real, so a chart of "what would have happened" plots rounds that could not
    have. The round the walk stops on is also the round a fork applying this criterion is minted at
    (`resume_and_fork/resume.py`), so a chain that runs long misreports where that fork goes.

    Also pins the self-consistency floor: fed the criterion the run actually realized, the chain
    reproduces the recorded winners and never parts. One that cannot reproduce the record under its
    own formula is measuring the fold, not the formula.
    """
    from promptpotter.application.mask.record import MaskCandidate
    from promptpotter.application.mask.scenario import scenario_spine

    def cand(cid: str, acc: float, latency: float, *, winner: bool = False) -> MaskCandidate:
        return MaskCandidate(
            candidate_id=cid,
            evaluators={"accuracy": acc, "mean_latency_s": latency},
            accuracy=acc,
            n_scored=4,
            is_winner=winner,
        )

    origin = cand("c0", 0.50, 1.0)
    # Round 1: `slow` is the most accurate and is crowned. Round 2 exists only so that "the walk
    # stopped" is a claim with something to stop BEFORE — with no round after the parting, a chain
    # that ran on and one that halted are the same list.
    slow = cand("slow", 0.80, 9.0, winner=True)
    fast = cand("fast", 0.60, 1.0)
    steady = cand("steady", 0.85, 8.0, winner=True)
    cycle = MaskCycle(
        cycle_id="cycle_0",
        rounds=[
            MaskRound(cycle_id="cycle_0", round=0, candidates=[origin]),
            MaskRound(
                cycle_id="cycle_0",
                round=1,
                candidates=[slow, fast],
                parent_evaluators=dict(origin.evaluators),
                parent_accuracy=origin.accuracy,
            ),
            MaskRound(
                cycle_id="cycle_0",
                round=2,
                candidates=[steady],
                parent_evaluators=dict(slow.evaluators),
                parent_accuracy=slow.accuracy,
            ),
        ],
    )

    # Realized criterion ⇒ the record, reproduced, the two readings agreeing on every round.
    realized = scenario_spine(cycle, "accuracy")
    assert [(s.round, s.candidate_id, s.recorded_id) for s in realized] == [
        (0, "c0", "c0"),
        (1, "slow", "slow"),
        (2, "steady", "steady"),
    ]

    # Flip to a criterion that punishes latency: round 1 elects `fast` (0.55) over the origin
    # (0.45), where `slow` scores 0.35 — so it parts from the record's `slow` right there, and the
    # walk ends. Round 2 is NOT on the chain: `steady` was measured against `slow`, and what it
    # would have scored against `fast` is not a thing the record knows.
    flipped = scenario_spine(cycle, "accuracy - 0.05 * mean_latency_s")
    assert [(s.round, s.candidate_id, s.recorded_id) for s in flipped] == [
        (0, "c0", "c0"),
        (1, "fast", "slow"),
    ]


# 8. The L4 outer proxy — what one finished inner cycle says


def test_parent_level_trajectory_is_honest_single_scale() -> None:
    # The L4 outer proxy's inner-search signal. Every branch here is a SILENT wrong-number
    # class: a completed inner run reports a plausible number and the outer optimizes on it,
    # so a mis-built level is invisible — the run looks fine and the outer fitness is wrong.
    ruler = _ruler({1: -1.0, 2: 0.0, 3: 1.0})

    def on(theta: float, se: float, *, scale: DeltaRuler | None = ruler) -> AbilityReading:
        """A reading, not a bare pair — a level carries the SCALE it was read on, because that is
        what says whether it may be differenced against the next one."""
        cal = scale.calibration_model if scale is not None else None
        # Read on the whole ruler, so the round's own band IS the ruler's.
        span = scale.delta_span if scale is not None else None
        return AbilityReading(
            # ``scale=None`` is a COLD reading, named for the OBJECTIVE θ was logit-accuracy on
            # rather than sharing one id with every other cold one. Either way it is incomparable
            # with a fitted anchor, which is the whole point of the branches below.
            theta=theta,
            se=se,
            ruler_id=scale.anchor_id if scale is not None else flat_ruler_id("acc"),
            ruler_n=len(scale.delta) if scale is not None else 0,
            ruler_span=span,
            round_span=span,
            calibration_model=cal,
            caveat=theta_caveat(calibration_model=cal, round_span=span, ruler_span=span),
        )

    origin_theta = on(0.0, 0.30)

    def thetas(levels: list[tuple[float, float]]) -> list[float]:
        return [t for t, _ in levels]

    # LOGITS, not expected accuracy. θ and the ruler's δ share one INTERVAL scale — the point of
    # fitting Rasch at all — so an identical Δθ must read as an identical gain wherever the origin
    # sits. Projecting each θ back through the ruler's sigmoid before differencing compressed the
    # gain near the ceiling, so the strong-origin arm scored less for the same ability climb.
    # SILENT: the outer ranks optimizer prompts partly by which seed happened to draw an easy origin.
    low_o, low = parent_level_trajectory(on(-1.0, 0.2), [on(-0.5, 0.2)], ruler)
    high_o, high = parent_level_trajectory(on(1.5, 0.2), [on(2.0, 0.2)], ruler)
    assert low_o is not None and high_o is not None
    assert (
        thetas(low)[0] - low_o[0]
        == pytest.approx(thetas(high)[0] - high_o[0])
        == pytest.approx(0.5)
    )

    # THE PARENT THE ROUND ADOPTED — never a statistic over the round's proposals. A round's
    # value to the search is what it CROWNS; the arms it discards are the price of finding that,
    # and averaging them prices exploration as damage. For any mutation operator with mass below
    # the parent (all of them — that is why selection exists) E[mean θ] < θ_parent, so the mean
    # is negative for an exploring generator and ~0 for an inert one: the gradient inverted.
    # Measured on promptpotter-self__d8b5be before the fix: an inner round that adopted a
    # 28-sample winner at θ +0.27 while PoBB killed a dud at 6 samples (θ -1.84) recorded a level
    # of -0.79 — a 0.88-logit REGRESSION stamped on a round the loop marked improved=True. Over
    # the campaign the seed that gained 30 accuracy points scored 2.9x WORSE than one that gained
    # 4.6. SILENT throughout: every run completed and every number looked plausible.
    o_lvl, levels = parent_level_trajectory(origin_theta, [on(0.2702, 0.21)], ruler)
    assert o_lvl == (origin_theta.theta, origin_theta.se)
    assert levels == [(pytest.approx(0.2702), pytest.approx(0.21))]

    # A peak followed by a collapse must read LOWER than a sustained peak. Under a running max
    # the two are byte-identical, so an optimizer prompt that destroys the inner loop after one good
    # round scored as its best round forever.
    _, spike = parent_level_trajectory(origin_theta, [on(1.2, 0.2), on(-2.0, 0.2)], ruler)
    _, held = parent_level_trajectory(origin_theta, [on(1.2, 0.2), on(1.2, 0.2)], ruler)
    assert thetas(spike)[1] < thetas(spike)[0] and sum(thetas(spike)) < sum(thetas(held))

    # A round whose frontier could not be fit (every row errored) carries the PRIOR level: the
    # parent persists, and nothing says it moved. SILENT otherwise: a phantom negative, or a
    # dropped round that shortens the series the mean divides by.
    #
    # It carries BOTH HALVES of that level, and the SE half is the one that can go wrong quietly:
    # a carried θ paired with a fresh round's SE would report the panel a precision no measurement
    # bought, and an inverse-variance pool then weights the cell that measured nothing the highest.
    o2, lv2 = parent_level_trajectory(origin_theta, [None], ruler)
    assert o2 is not None and lv2 == [o2]
    _, carried = parent_level_trajectory(origin_theta, [on(1.0, 0.11), None], ruler)
    assert carried == [(1.0, 0.11), (1.0, 0.11)]

    # Regression preserved: an parent below origin yields a level BELOW origin (the negative
    # delta the outer steers away from), NOT floored at origin.
    o3, lv3 = parent_level_trajectory(origin_theta, [on(-2.0, 0.2)], ruler)
    assert o3 is not None and thetas(lv3)[0] < o3[0]

    # An origin that was never fit, or a COLD ruler, yields `(None, [])` so the caller EXCLUDES
    # the cycle (`no_evidence_reason`). Cold matters on its own: `fit_theta_given_delta` puts
    # every sample at δ=0 there, so θ collapses to that round's logit-accuracy and stops being
    # subset-invariant — differencing it across rounds compares two different scales.
    # SILENT: a dead inner campaign differenced against an invented floor reads as a huge lift.
    assert parent_level_trajectory(None, [on(1.0, 0.2)], ruler) == (None, [])
    assert parent_level_trajectory(origin_theta, [on(1.0, 0.2)], None) == (None, [])

    # A level read on ANOTHER SCALE is not a level on this one: differencing a flat reading
    # against a warm origin subtracts two estimators, not two abilities. It carries the prior
    # level forward, like a round that crowned nobody. SILENT — both numbers are plausible.
    _, mixed = parent_level_trajectory(origin_theta, [on(9.0, 0.2, scale=None)], ruler)
    assert mixed == [(origin_theta.theta, origin_theta.se)]
    # …and an origin off the cycle's own scale leaves nothing to difference against at all.
    assert parent_level_trajectory(on(0.0, 0.3, scale=None), [on(1.0, 0.2)], ruler) == (None, [])
    # A DIFFERENT warm ruler is just as incomparable as a flat one — the id is the whole test.
    other = _ruler({1: -0.5, 2: 0.25, 3: 2.0})
    _, foreign = parent_level_trajectory(origin_theta, [on(9.0, 0.2, scale=other)], ruler)
    assert foreign == [(origin_theta.theta, origin_theta.se)]


def test_compute_proxies_is_one_exact_mean_over_the_parent_levels() -> None:
    # SILENT wrong-score: the outer signal is ONE number, so any error in it is the whole
    # ranking. It must be the mean of the adopted levels minus the origin, over the cycle's
    # ROUND BUDGET. A divisor of any OTHER shape reappearing here (a declared target, or the
    # room `max(origin, 1-origin)`) fails loudly on the pins below, and so does a regression to
    # reading the series' last element: the two differ on every trajectory that is not flat.
    from promptpotter.domain.l4.proxies import (
        OUTER_PROXY_KEYS,
        compute_outer_proxies,
        mean_parent_level_se,
    )

    px = compute_outer_proxies(
        cycle_result([0.40, 0.55], 0.30, [round_result(1), round_result(2)])
    ).model_dump()
    assert px == {"mean_round_delta": pytest.approx(0.175)}  # endpoint would read 0.25
    # The emitted key set IS the dataset's declared `observation_mappings`; a field added here
    # and not declared there is silently dropped before the formula ever sees it.
    assert OUTER_PROXY_KEYS == ("mean_round_delta",)

    # A campaign that ends where it started scores exactly zero lift — not a small positive one.
    flat = compute_outer_proxies(cycle_result([0.30], 0.30, [round_result(1)]))
    assert flat.mean_round_delta == pytest.approx(0.0)

    # WHEN the lift lands is part of the score, and that is the point of the mean. These two
    # trajectories END in the same place; the one that climbed in round 1 and gave some back
    # scores well above the one that crawled. The endpoint read cannot separate them at all
    # (both 0.05), and on the banked 39-cell panel the mean measured a 26% smaller residual
    # while ranking the same arms — so this is a precision gain, not a change of subject.
    early = compute_outer_proxies(
        cycle_result([0.90, 0.35], 0.30, [round_result(1), round_result(2)])
    )
    late = compute_outer_proxies(
        cycle_result([0.05, 0.35], 0.30, [round_result(1), round_result(2)])
    )
    assert early.mean_round_delta == pytest.approx(0.325)
    assert late.mean_round_delta == pytest.approx(-0.10)
    assert early.mean_round_delta > late.mean_round_delta

    # THE DENOMINATOR IS THE BUDGET, NOT THE SERIES LENGTH — and getting this wrong is silent
    # in the worst direction. `lives` stops a STALLING cycle, so dividing by the rounds that ran
    # pays a cell for quitting: this trajectory lifted in round 2 and stopped, and over its own
    # 3 rounds it reads +0.30 — better than the identical search that sat through a 4th flat
    # round. Held forward to the declared budget both read +0.225, which is the same cell twice.
    quit_early = cycle_result(
        [0.30, 0.60, 0.60], 0.30, [round_result(i) for i in (1, 2, 3)], round_budget=4
    )
    ran_out = cycle_result(
        [0.30, 0.60, 0.60, 0.60], 0.30, [round_result(i) for i in (1, 2, 3, 4)], round_budget=4
    )
    # THE PADDING MUST NOT REACH THE PRECISION. `parent_level_series` stretches a short series by
    # repeating its last value; those slots carry no measurement. If they entered the cell's SE
    # as if they did, a cell that quit after 2 of 4 rounds would report itself SHARPER than one
    # that ran the budget out, and an inverse-variance pool would weight the cell that measured
    # LEAST the most. SILENT: the panel would report a tighter CI it never earned, and buy its
    # confidence from the arms that did the least work.
    se_kw = {"origin_level_se": 0.20, "round_parent_level_ses": [0.30, 0.40]}
    padded = cycle_result(
        [0.30, 0.60], 0.30, [round_result(i) for i in (1, 2)], round_budget=4, **se_kw
    )
    unpadded = cycle_result(
        [0.30, 0.60], 0.30, [round_result(i) for i in (1, 2)], round_budget=2, **se_kw
    )
    assert mean_parent_level_se(padded) == pytest.approx(mean_parent_level_se(unpadded))
    # mean(0.30, 0.40) and NOTHING ELSE — never sigma/sqrt(n), which would divide correlated,
    # NESTED frontier fits as if they were independent draws.
    assert mean_parent_level_se(padded) == pytest.approx(0.35)
    assert mean_parent_level_se(padded) > 0.35 / 2

    # SILENT wrong-number: the origin's own SE must NOT be in here. Every arm on a cell replays
    # the same round-0 rows, so `origin_level` is ONE measurement shared by both sides of
    # `variant - origin` and cancels exactly. Folding it in counted it twice, and on the banked
    # corpus that made the claimed noise 2.4x the total spread it is a component of — a ratio
    # that is impossible rather than merely large, and it read out as "100% noise" after clamping.
    assert mean_parent_level_se(padded) != pytest.approx((0.35**2 + 0.20**2) ** 0.5)
    # ...so the cell's own SE cannot depend on whether the origin was ever fit.
    no_origin_se = cycle_result(
        [0.30, 0.60],
        0.30,
        [round_result(i) for i in (1, 2)],
        round_budget=4,
        round_parent_level_ses=[0.30, 0.40],
    )
    assert mean_parent_level_se(no_origin_se) == pytest.approx(0.35)

    # No SE at all yields None — the same answer as "this cell was never fit". A fabricated 0.0
    # reads as an infinitely sharp cell and would dominate every weighting it entered.
    assert mean_parent_level_se(cycle_result([0.30], 0.30, [round_result(1)])) is None

    assert compute_outer_proxies(quit_early).mean_round_delta == pytest.approx(0.225)
    assert compute_outer_proxies(ran_out).mean_round_delta == pytest.approx(0.225)
    # An undeclared budget falls back to the series length rather than dividing by zero.
    assert compute_outer_proxies(
        cycle_result([0.30, 0.60, 0.60], 0.30, [round_result(i) for i in (1, 2, 3)])
    ).mean_round_delta == pytest.approx(0.20)


def test_compute_proxies_excludes_cycles_that_produced_no_evidence() -> None:
    # SILENT wrong-score. Every aggregate here is TOTAL on an empty input (`_mean([])` is 0.0),
    # so a cycle that never ran an L1 round scores `cleanliness = diversity_health = 1.0` — an
    # unexercised optimizer prompt reported as flawless, and a *high* outer fitness. Nothing errors.
    # The exclusion predicate must ask "produced evidence?", not "failed?" — the two answers
    # differ on every row below.
    from promptpotter.domain.l4.proxies import InnerCycleUnscoreableError, compute_outer_proxies

    # Zero L1 rounds, on a cycle that DID end on its own terms.
    empty = cycle_result([], 0.30, [], stop_reason=StopReason.TARGET_HIT)
    with pytest.raises(InnerCycleUnscoreableError):
        compute_outer_proxies(empty)

    # ONLY A SUCCESS OUTCOME IS A MEASUREMENT. The dangerous rows are the ones with rounds on
    # the board: a rail-truncated cycle looks exactly like a completed one, so every aggregate
    # below computes happily and reports a TRUNCATED trajectory as the optimizer prompt's verdict —
    # "it stopped improving" is indistinguishable from "we cut it off". That let provider mood
    # (a slow backend, a spend cap tripping on jittery reasoning-token counts, an operator's
    # Ctrl+C) masquerade as optimizer prompt quality. Measured before the fix: 3 of 36 inner cycles
    # on disk tripped `token_budget`, two truncating at rounds 4-5 of a 7-round budget, and
    # every one was scored. ONE reason stands for all seven: the verdict is read off the typed
    # `stop_reason_outcome` table, so enumerating the rest re-tests that table's rows here.
    truncated = cycle_result(
        [0.40, 0.55],
        0.30,
        [round_result(1), round_result(2)],
        stop_reason=StopReason.TOKEN_BUDGET,
    )
    with pytest.raises(InnerCycleUnscoreableError):
        compute_outer_proxies(truncated)

    # ...and it is EXCLUDED, never floored: the floor is `after_N_rounds_delta = -1`, which zeroes
    # cell (the lift core is multiplicative) — punishing the optimizer prompt for a slow provider,
    # which is the dead-cell bug in a new costume. An all-tooling-rounds cycle that would
    # otherwise floor (see the test above) is excluded once a rail truncated it.
    railed_and_empty = cycle_result(
        [0.40],
        0.30,
        [round_result(1, parse_failure="l1_provider_empty_response")],
        stop_reason=StopReason.TOKEN_BUDGET,
    )
    with pytest.raises(InnerCycleUnscoreableError):
        compute_outer_proxies(railed_and_empty)

    # Rounds ran, but the trajectory is empty → nothing to difference against origin. Without
    # the guard `first`/`after_N_rounds_delta` would both read a flat 0.0: "no lift" is a
    # plausible-looking number for "no measurement", which is what makes it dangerous.
    levelless = cycle_result([], 0.30, [round_result(1)])
    with pytest.raises(InnerCycleUnscoreableError):
        compute_outer_proxies(levelless)

    # Rounds AND levels, but the origin was never scored. Every delta here is measured against
    # that floor, so substituting 0.0 (the old `origin_acc` stand-in, itself 0.0 when nothing
    # was scored) reports the whole trajectory as an enormous lift over nothing — and it does so
    # for the CHEAPEST rows, since a crash at round 0 is what leaves the origin unscored.
    floorless = cycle_result([0.40, 0.55], None, [round_result(1), round_result(2)])
    with pytest.raises(InnerCycleUnscoreableError):
        compute_outer_proxies(floorless)

    # ...and a cycle that DID produce evidence still scores, on the same predicate.
    ok = cycle_result([0.40, 0.55], 0.30, [round_result(1), round_result(2)])
    ok_px = compute_outer_proxies(ok)
    assert ok_px.mean_round_delta == pytest.approx(0.175)


def test_an_inner_cell_id_is_the_prompts_it_runs_not_the_bytes_that_declared_them():
    """`inner_campaign_id` hashes the RESOLVED override. Two declarations that resolve to one set of
    optimizer prompts are one configuration: hashing the declaration bought two inner campaigns for
    it — two sandboxes, no shared cache, read as two independent observations of two levers — and
    left neither able to continue the rounds the other banked."""
    from promptpotter.application.optimization.dispatch.llm_call.prompts import resolved_overrides

    declared = {"l1_critique": {"instruction": "x"}}
    # Every drop the resolvers make: a rename that cannot be applied (self-rename), and a layout
    # edit that lands back on the node's own floor. Neither reaches a prompt.
    inert = {
        "l1_critique": {
            "instruction": "x",
            "output_schema_field_names": {"reasoning": "reasoning"},
            "layout": {},
        }
    }
    assert resolved_overrides(declared) == resolved_overrides(inert)
    assert declared != inert

    # A layout edit that DOES apply is a different configuration and must not collide with it.
    moved = {"l1_critique": {"instruction": "x", "layout": {"axis_memory": "thinking_style"}}}
    assert resolved_overrides(moved) != resolved_overrides(declared)

    # The model is the SINGLE inner-optimizer choice, fanned onto every node at render — so WHICH
    # node carried it is not a fact about the configuration. Keyed per-node, these two rendered
    # identical prompts under two ids and paid for two inner campaigns.
    assert resolved_overrides({"l1_critique": {"model": "m"}}) == resolved_overrides(
        {"l2_context": {"model": "m"}}
    )


# 9. L1 proposal validators — what is refused before it is scored


def _parent() -> OptSearchPoint:
    return OptSearchPoint(persona="Expert", instruction="Rank items.")


def _child(parent: OptSearchPoint, **changes) -> CandidateProposal:
    return CandidateProposal(opt_sp=parent.mutate(**changes))


# The two strings differ in wording and, in the tests below, in the FIELD they are written
# into — which is the invariant under test. They are close paraphrases on purpose: the gate is
# a lexical overlap test (`IDEA_MATCH_REJECT`), so a pair that shares little vocabulary would
# assert nothing about field-independence, only about the threshold.
_DEAD_IDEA = (
    "Derive new facts using modus ponens, modus tollens, disjunctive syllogism and chaining. "
    "Exhaust every derivation branch before concluding Uncertain."
)

_DEAD_IDEA_REPHRASED = (
    "Using modus ponens, modus tollens, disjunctive syllogism and chaining, derive new facts "
    "and exhaust each derivation branch before concluding Uncertain."
)


def test_the_two_collapse_kinds_are_counted_apart_in_one_population():
    """A clone of the parent and a clone of a SIBLING are different failures with the same cost —
    a scoring pass on a searchpoint already measured — and the round reports them separately
    because they ask for different next moves (widen the mutation vs. de-duplicate the batch).

    One population carrying both is the case that was untested: with a test per kind, a detector
    that stamped whichever reason it checked LAST onto every collapse passed both.
    """
    parent = _parent()
    clone = _child(parent)  # echoes the parent
    first = _child(parent, persona="Specialist")
    twin = _child(parent, persona="Specialist")  # echoes its sibling
    novel = _child(parent, instruction="Rank with care.")

    stats = detect_invariants([clone, first, twin, novel], parent, {})

    def reasons(p) -> list[str]:
        return [vf.reason for vf in p.opt_sp.memory.wounds.validation_failures]

    assert reasons(clone) == ["no_op_variant"]
    assert reasons(twin) == ["duplicate_variant"]
    # The first of a duplicate PAIR is the survivor — convicting both empties the round.
    assert reasons(first) == [] and reasons(novel) == []
    assert (stats.l1_n_no_op, stats.l1_n_duplicate) == (1, 1)
    assert stats.l1_yield == 0.5


def test_a_reproposed_idea_is_rejected_even_when_rewritten_into_another_field():
    """The re-proposal that burned 8 rounds on `justlogic-d234` — same idea, new field.

    Every exact-signature gate in the loop saw eight distinct mutations, because the generator
    rewrites the idea into `instruction`, then `thinking_style`, then a schema description. The
    round is charged for it either way, so a gate that only matches strings is a gate that never
    fires on the failure it was built for.
    """
    parent = _parent()
    prior = [lost_round(1, "instruction", _DEAD_IDEA)]
    proposals = [
        _child(parent, thinking_style=_DEAD_IDEA_REPHRASED),  # same idea, different field
        _child(parent, persona="A terse logician who commits to a label."),  # unrelated
    ]

    stats = detect_invariants(proposals, parent, {}, prior)

    reasons = [vf.reason for vf in proposals[0].opt_sp.memory.wounds.validation_failures]
    assert "repeat_variant" in reasons, "a rewritten re-proposal must still be caught"
    assert "round 1" in proposals[0].opt_sp.memory.wounds.validation_failures[0].value
    assert proposals[1].opt_sp.memory.wounds.validation_failures == [], "unrelated idea survives"
    assert stats.l1_n_repeat == 1

    # THE THREE QUIET ARMS, beside the fire case because that is what stops one of them going
    # vacuous alone: each is a way the gate could do more harm than the disease, and each is
    # the SAME rewrite against a different history.
    def repeats_caught(history, *proposals) -> int:
        stats = detect_invariants(list(proposals), parent, {}, history)
        assert not any(p.opt_sp.memory.wounds.validation_failures for p in proposals)
        return stats.l1_n_repeat

    # (1) EVERY proposal repeats. Rejecting them all hands PoBB an empty population and the
    # round is forfeit — strictly worse than re-testing a dead idea, which the ALREADY TRIED
    # panel still marks. A repeat may cost a candidate, never the whole round.
    assert (
        repeats_caught(
            prior,
            _child(parent, thinking_style=_DEAD_IDEA_REPHRASED),
            _child(parent, answer_format=_DEAD_IDEA),
        )
        == 0
    )
    # (2) The same idea, never measured: a prior that scored zero samples reads
    # ``accuracy == 0.0`` only because the field is a non-optional float, and 0/0 is absence
    # of evidence rather than a measured defeat.
    assert (
        repeats_caught(
            [lost_round(1, "instruction", _DEAD_IDEA, total=0, acc=0.0)],
            _child(parent, thinking_style=_DEAD_IDEA_REPHRASED),
            _child(parent, persona="A terse logician who commits to a label."),
        )
        == 0
    )
    # (3) The same idea, but it BEAT its matched parent (0.9 against 0.5). Refining a winner is
    # the search working; only measured LOSSES close a direction off.
    assert (
        repeats_caught(
            [lost_round(1, "instruction", _DEAD_IDEA, acc=0.9)],
            _child(parent, thinking_style=_DEAD_IDEA_REPHRASED),
            _child(parent, persona="A terse logician who commits to a label."),
        )
        == 0
    )


def test_parse_population_flags_dropped_optimizer_prompt_port():
    """An L4 candidate whose merged `l1_generate` prose drops an INLINE port
    (`{{citable_fields}}` in `answer_format`, `{{n_variants}}` in `task_intent`+`instruction`)
    is invalid (synthetic-0) — a severed channel once ran 4 inner campaigns as normal
    measurements, silently. The capability directives now ride the layout (guarded by
    `validate_l1_layout`'s mandatory set); the inline format ports guarded here can never
    move there, so this is the guard's permanent scope. Checked on MERGED params, so a
    child inheriting the broken prose from its parent (no override of its own) flags too."""
    from promptpotter.application.optimization.dispatch.llm_call.prompts import (
        base_optimizer_template,
    )
    from promptpotter.application.optimization.l1.population import parse_population

    schema = PipelineSchema(
        name="promptpotter-self",
        nodes=[PipelineNode(name="l1_generate", param_keys={"answer_format"})],
    )
    parent = _parent()
    base_answer_format = base_optimizer_template("l1_generate").answer_format
    dropped = CandidateProposal(
        opt_sp=parent.mutate(),
        pipeline_params_override={"l1_generate": {"answer_format": "Return JSON variants."}},
    )
    intact = CandidateProposal(
        opt_sp=parent.mutate(),
        pipeline_params_override={
            "l1_generate": {"answer_format": base_answer_format + " Be terse."}
        },
    )
    inherits_broken = CandidateProposal(opt_sp=parent.mutate(persona="Strict"))

    opt_sp_list, _ = parse_population(
        [dropped, intact],
        pipeline_params=None,
        schema=schema,
    )
    inherited_list, _ = parse_population(
        [inherits_broken],
        pipeline_params={"l1_generate": {"answer_format": "Answer without ports."}},
        schema=schema,
    )

    dropped_failures = opt_sp_list[0].memory.wounds.validation_failures
    # A 21-char replacement for a 1.5k field is ALSO a gutting, so this candidate now carries two
    # reasons. Selected rather than asserted whole: the subject here is the severed port.
    (port,) = [vf for vf in dropped_failures if vf.reason == "dropped_mandatory_placeholder"]
    assert port.axis == "l1_generate.prompt"
    assert "citable_fields" in port.value
    assert opt_sp_list[1].memory.wounds.validation_failures == []
    assert [vf.reason for vf in inherited_list[0].memory.wounds.validation_failures] == [
        "dropped_mandatory_placeholder"
    ]


def test_classify_result_routes_structural_warning_to_fatal() -> None:
    """One source of truth, two consumers: a warning the backend **source-stamped**
    ``kind=structural`` is a deterministic-for-config failure, so PoBB elimination must
    fast-cut it (``fatal_codes`` → ``dominant_fatal``) exactly as the degradation verdict
    grades it structural-critical off the same stamped field. A transient-stamped code
    stays advisory-only — NOT deprecated, since the measurement is still valid. An
    unstamped warning is NOT routed fatal (no guessing)."""
    from promptpotter.domain.rendering import classify_result

    structural = classify_result(
        {
            "pipeline_data": {
                "diagnostics": {
                    "warnings": [
                        {
                            "step": "entity_profiling",
                            "code": "json_validate_failed",
                            "kind": "structural",
                        }
                    ]
                }
            }
        }
    )
    assert "entity_profiling:json_validate_failed" in structural.fatal_codes
    assert structural.dominant_fatal == "entity_profiling:json_validate_failed"

    transient = classify_result(
        {
            "pipeline_data": {
                "diagnostics": {
                    "warnings": [
                        {"step": "web_search", "code": "low_document_count", "kind": "transient"}
                    ]
                }
            }
        }
    )
    assert "web_search:low_document_count" in transient.advisory_codes
    assert not transient.fatal_codes and not transient.infra_codes

    # No ``kind`` stamped → NOT fatal (the source-stamp is the only structural signal;
    # an unstamped warning under-counts rather than over-eliminating).
    unstamped = classify_result(
        {
            "pipeline_data": {
                "diagnostics": {
                    "warnings": [{"step": "entity_profiling", "code": "json_validate_failed"}]
                }
            }
        }
    )
    assert not unstamped.fatal_codes


def test_content_empty_on_a_result_that_answered_is_not_an_empty_response() -> None:
    """``content_empty`` describes ONE ATTEMPT; the backend retries it and the retry can
    answer. Reading it as a verdict on the RESULT stamps ``empty_response`` — a fatal code
    PoBB fast-cuts off a single sighting — onto a candidate that recovered and answered
    correctly. Measured on three archived ``gpt-oss-20b:nitro`` rows carrying
    ``content_empty`` + ``llm_retry`` (both stamped transient); two scored 1.0.

    The empty verdict still fires when the result really is empty, including the scorer's
    ``NO_RESULT`` sentinel — otherwise this guard would trade one silent misread for another.

    Second axis: a result that emitted NOTHING but did REASON is the route's fault, not the
    candidate's, whichever way the call ended. ``reasoning_tokens > 0`` proves the model
    worked, so ``stop`` and ``length`` are the same fault at two budgets and both route to
    infra, where they deprecate the sample without fast-eliminating the arm.
    """
    from promptpotter.config.settings import NO_RESULT
    from promptpotter.domain.rendering import classify_result

    def result(predicted: str, *, reasoning: int = 0, finish: str = "stop") -> dict[str, object]:
        return {
            "predicted": predicted,
            "pipeline_data": {
                "terminal_node": "llm_only",
                "step_tokens": {"llm_only": {"finish_reason": finish, "reasoning": reasoning}},
                "diagnostics": {
                    "warnings": [
                        {"step": "llm_only", "code": "content_empty", "kind": "transient"},
                        {"step": "llm_only", "code": "llm_retry", "kind": "transient"},
                    ]
                },
            },
        }

    recovered = classify_result(result("TRUE", reasoning=16))
    assert not recovered.fatal_codes, "a row that answered is not an empty response"
    assert "llm_only:content_empty" in recovered.advisory_codes

    for empty in ("", NO_RESULT):
        assert "llm_only:empty_response" in classify_result(result(empty)).fatal_codes

    for finish, code in (
        ("stop", "reasoning_only_response"),
        ("length", "reasoning_budget_exhausted"),
    ):
        thought = classify_result(result("", reasoning=5352, finish=finish))
        assert not thought.fatal_codes, (
            f"{finish} after reasoning is route shape, not candidate fault"
        )
        assert f"llm_only:{code}" in thought.infra_codes


# 10. Escalation and spend


def _spine_cycle(cycle_id: str, rounds: list[tuple[int, float]], **kw: object) -> SpineCycle:
    return SpineCycle(cycle_id=cycle_id, theta_by_round=dict(rounds), **kw)  # type: ignore[arg-type]


def test_a_theta_stall_verdict_must_clear_its_own_error() -> None:
    """The escalation ladder advances on "did the cycle improve", and a θ rise inside its own
    standard error is not an improvement. A bare ``>`` counts one, resets the stall counter, and
    holds the ladder at L2 forever — with no error anywhere: every round completes, L2 fires, and
    L3 simply never arrives.

    Measured on `justlogic-d234__082126`, whose numbers this replays. Round 3's θ rose +0.012 on
    se 0.198 — six hundredths of one standard error — which reset ``_l2_stall_count`` to zero and
    cost exactly one round. L3 would then have fired at round 5, but a round's escalation runs
    AFTER it closes and round 5 closed on `max_rounds`, so L3 never fired at all; rounds 3-5 spent
    49% of the run's budget re-testing candidates against a panel that had stopped moving.

    Silent harm: nothing distinguishes "L2 keeps firing because it is working" from "L2 keeps
    firing because noise keeps clearing its stall counter"."""
    from promptpotter.application.optimization.escalation.state import EscalationFSM, NextAction

    # (composite, θ, θ_se) per round, from the live run: composite frozen from round 2 on, θ
    # advancing once for real (+0.467) and then only by noise (+0.012, then flat).
    live = [
        (0.4853, -0.1296, 0.216),
        (0.6248, 0.3376, 0.202),
        (0.6248, 0.3498, 0.198),
        (0.6248, 0.3498, 0.198),
    ]

    def ladder(*, with_se: bool) -> list[str]:
        fsm, actions = EscalationFSM(), []
        for comp, theta, se in live:
            event = fsm.observe_l2_escalation(
                current_composite_fitness=comp,
                current_theta=theta,
                current_theta_se=se if with_se else None,
                l2_patience=2,
                l3_patience=1,
            )
            actions.append(str(event.next_action))
            if event.next_action != NextAction.FIRE_L2:
                break
            fsm.record_l2_fired(best_composite_fitness=comp, best_theta=theta)
        return actions

    # The real +0.467 move at round 2 still counts — the bar rejects noise, not signal.
    assert ladder(with_se=True) == ["fire_l2", "fire_l2", "fire_l2", "fire_l3"]
    # Without it the noise move buys another L2 round and L3 is pushed out of the run.
    assert NextAction.FIRE_L3 not in ladder(with_se=False)

    # The bar is the reading's own SE, applied on the θ scale only: a composite-scale verdict has
    # no error term to clear and must be untouched by it.
    assert EscalationFSM._improved(0.0, 0.0, 0.35, 0.34, 0.20)[0] is False
    assert EscalationFSM._improved(0.0, 0.0, 0.60, 0.34, 0.20)[0] is True
    assert EscalationFSM._improved(0.7, 0.6, None, None, 0.20) == (True, "composite")


def test_cached_calls_are_metered_but_not_billed(tmp_path: Path) -> None:
    # The upstream half of the bug above, and it is what actually failed: a cache hit emitted NO
    # token-usage record at all, so a replayed inner cycle reported zero cost and the L4 divisor
    # had nothing to divide by. Both halves are silent — the run completes and the dashboard reads
    # a plausible $0.00.
    #
    # The invariant, in one place: EVERY call the search makes lands in `incurred`; only a call
    # that reached the wire lands in the BILL. Collapse them either way and something breaks —
    # bill the cache hits and `spend_budget_usd` halts a run that cost nothing; meter only the
    # misses and the L4 origin arm reads as infinitely efficient.
    from promptpotter.domain.cycle_paths import CycleDir
    from promptpotter.domain.run_records import TokenUsageRecord
    from promptpotter.infrastructure.projections.live_dashboard.view import LiveDashboardView
    from promptpotter.infrastructure.store.layout import CycleLayout, cycle_dir_for

    cycle_dir = CycleDir(cycle_dir_for(tmp_path, CycleHop(campaign_id="c1", cycle_id="cyc1")))
    view = LiveDashboardView(
        cycle_dir=cycle_dir,
        state_path=CycleLayout(Path(cycle_dir)).dashboard,
        hop=CycleHop(campaign_id="c1", cycle_id="cyc1"),
        session_id="s1",
        l1_patience=2,
        n_variants=2,
        sp_budget_ttest=5,
        headline_metric="accuracy",
    )

    def usage(*, cached: bool) -> TokenUsageRecord:
        return TokenUsageRecord(
            kind="optimizer",
            node="l1_generate",
            model="openai/gpt-oss-120b",
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.02,
            cached=cached,
        )

    view._handle_token_usage(usage(cached=False))
    view._handle_token_usage(usage(cached=True))

    spend = view.state.spend
    # Two identical calls; one paid, one replayed. The bill counts one, the search made two.
    assert spend.total_used_usd == pytest.approx(0.02)
    assert spend.total_incurred_usd == pytest.approx(0.04)
    # The budget gate reads the bill, so a replay can never halt a run it cost nothing to make.
    assert view.spend_total_used_usd == pytest.approx(0.02)
    assert spend.loop.input_tokens == 1000  # billed tokens: the wire call only


def test_rerun_short_circuits_when_max_tokens_le_cached_completion() -> None:
    """When the cached failure is a token-budget exhaustion AND the rerun's
    ``max_tokens`` would be no larger than the cached run's actual output,
    the rerun branch must short-circuit — running it would burn an LLM call
    against the same (or tighter) budget for an identical DEPR result.
    """
    from promptpotter.application.scoring.sample_measurement import (
        _rerun_would_repeat_token_budget_failure,
    )

    cached = {
        "pipeline_data": {
            "diagnostics": {"warnings": [{"step": "llm_only", "code": "content_empty"}]},
            "step_tokens": {
                "llm_only": {
                    "input": 212,
                    "output": 3072,  # actual emitted == max_tokens cap
                    "finish_reason": "length",
                    "reasoning": 12226,  # reasoning chars > completion → budget exhausted
                },
            },
        },
    }
    # rerun at tighter budget: short-circuit
    assert _rerun_would_repeat_token_budget_failure(cached, {"llm_only": {"max_tokens": 1536}})
    # rerun at equal budget: short-circuit
    assert _rerun_would_repeat_token_budget_failure(cached, {"llm_only": {"max_tokens": 3072}})
    # rerun at larger budget: let it try
    assert not _rerun_would_repeat_token_budget_failure(cached, {"llm_only": {"max_tokens": 8192}})
    # non-budget failure: regular rerun logic applies
    non_budget = {"pipeline_data": {"diagnostics": {"warnings": []}, "step_tokens": {}}}
    assert not _rerun_would_repeat_token_budget_failure(
        non_budget, {"llm_only": {"max_tokens": 1536}}
    )


def test_mcts_backprop_does_not_double_count_a_fork_inherited_prefix():
    """The silent number: a fork's copied rounds are the SAME logical node as the parent's.

    Forking at round 2 physically copies parent rounds 0..1 into the child's dir. Counted
    naively, root r0 would see those copies as fresh descendants and its visit count would
    inflate — worse the deeper the lineage, and invisibly: the fold still returns a
    plausible number, UCB still picks *a* round, and the run still completes. Only the
    rewind is wrong.

    Tree here (canonical nodes only):
        root r0 → r1 → r2 → r3          (root's own spine)
                     ↘ fork r2 → r3      (child, cut at 2; its r0/r1 are copies)
    So r1 has 5 descendants-plus-self, and r0 has 6 — NOT 8, which is what counting the
    child's inherited r0/r1 copies would give.
    """
    root = _spine_cycle("root", [(0, 0.0), (1, 0.5), (2, 0.4), (3, 0.3)])
    # r0/r1 are byte-copies of root's, carried forward by the mint.
    fork = _spine_cycle(
        "fork", [(0, 0.0), (1, 0.5), (2, 2.0), (3, 2.4)], parent_cycle_id="root", fork_from_round=2
    )
    stats = accumulate_node_stats([root, fork])

    # Only the child's OWN rounds are nodes; its inherited prefix is not re-counted.
    assert ("fork", 0) not in stats
    assert ("fork", 1) not in stats
    assert stats[("root", 0)].visits == 6  # itself + r1,r2,r3 + fork r2,r3
    assert stats[("root", 1)].visits == 5
    assert stats[("root", 2)].visits == 2  # itself + root r3 only
    assert stats[("fork", 2)].visits == 2  # itself + fork r3

    # Q is the subtree's mean θ. The fork branch is where the ability actually went.
    assert stats[("fork", 2)].q == pytest.approx((2.0 + 2.4) / 2)
    assert stats[("root", 2)].q == pytest.approx((0.4 + 0.3) / 2)
    # r1's subtree spans BOTH branches — that is what backprop is for: the parent learns
    # from a descendant it never ran itself.
    assert stats[("root", 1)].q == pytest.approx((0.5 + 0.4 + 0.3 + 2.0 + 2.4) / 5)

    # ---- and the PICK the fold feeds, on the same tree ---------------------------------
    # UCB re-expands the ancestor whose subtree carries the ability, not the adjacent round
    # that merely happens to be nearest. Read off root's own spine, where θ collapses after
    # r1: stalled at r3, the target is r1.
    collapsed = [_spine_cycle("root", [(0, 0.0), (1, 2.0), (2, 0.1), (3, 0.0)])]
    assert select_rewind_round(collapsed, cycle_id="root", current_round=3) == 1
    # Nothing above round 0 — the caller must NOT fork, or a rewind to nowhere mints a
    # duplicate cycle and burns a whole run.
    assert select_rewind_round(collapsed, cycle_id="root", current_round=0) is None
