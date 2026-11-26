"""A wrong θ — the scale every ability is read on.

Owns `application/intelligence/rasch.py` and `adaptive_queue_mechanism.py`,
`application/bench/difficulty.py`, `domain/ruler.py` and `domain/l4/proxies.py`. Pure estimator
arithmetic: each journey recovers a known answer from data built to have one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
import pytest
import yaml
from factories import (
    arm_key,
    arm_state,
    cycle_result,
    election,
    jumping_clock,
    level_series,
    measurement,
    pipeline_schema,
    round_result,
    run_init_round,
    samples,
    scorer_of,
    sheet,
    workspace,
)
from offline_world import Router

from promptpotter.application.bench.difficulty import (
    JudgingRuler,
    calibrate_delta_ruler,
    judging_ruler,
    level_series_of,
    ruler_clock,
    session_ruler_scope,
)
from promptpotter.application.bench.node_context import NodeContext
from promptpotter.application.bench.round_analysis import compute_round_diagnostics
from promptpotter.application.intelligence.adaptive_queue_mechanism import (
    covered_prefix,
    stratum_shares,
)
from promptpotter.application.intelligence.rasch import (
    Observation,
    RaschPosterior,
    build_round_order,
    fit_rasch,
    fit_theta,
    graduate_ruler_model,
)
from promptpotter.application.runner.bench import LineSpan, own_level
from promptpotter.application.scoring.metrics import compute_composite_fitness
from promptpotter.application.scoring.paired import grade_measurands
from promptpotter.domain.cycle_result import CycleResult
from promptpotter.domain.l4.proxies import (
    OuterSampleProxies,
    compute_outer_proxies,
    inner_cell_facts,
    inner_cell_report,
    panel_precision,
)
from promptpotter.domain.optimizer_state import (
    PARSE_FAILURE_MALFORMED,
    PARSE_FAILURE_TOOLING,
)
from promptpotter.domain.phases import StopReason
from promptpotter.domain.results import RoundResult
from promptpotter.domain.ruler import (
    DeltaRuler,
    RulerOwn,
    RulerReach,
    RulerScope,
    RulerStamp,
    ThetaCaveat,
    anchor_id_of,
    flat_ruler_id,
    theta_caveat,
)
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import GradedCell
from promptpotter.domain.wounds import ValidationReason
from promptpotter.infrastructure.store.ruler_store import RulerStore
from promptpotter.shared.errors import CellUnscoreableError
from promptpotter.shared.identity import default_identity

# 1. The estimator


def _synth(
    theta: dict[str, float],
    delta: dict[str, float],
    *,
    n_per_pair: int,
    rng: np.random.Generator,
    disc: dict[str, float] | None = None,
) -> list[Observation]:
    obs: list[Observation] = []
    for cid, t in theta.items():
        for key, d in delta.items():
            p = 1.0 / (1.0 + np.exp(-(1.0 if disc is None else disc[key]) * (t - d)))
            obs.extend(Observation(cid, key, float(rng.random() < p)) for _ in range(n_per_pair))
    return obs


def test_the_estimator_recovers_the_scale_its_data_was_drawn_on() -> None:
    theta_true = {"strong": 1.5, "mid": 0.0, "weak": -1.5}
    delta_true = {"1": -1.0, "2": 0.0, "3": 1.0, "4": 2.0}
    posterior = fit_rasch(
        _synth(theta_true, delta_true, n_per_pair=80, rng=np.random.default_rng(42))
    )
    assert posterior.converged, "the joint fit stopped short of its root"
    assert abs(sum(posterior.theta.values()) / len(posterior.theta)) < 1e-6, "θ is mean-centred"
    # 0.5 logits is the noise floor at n=80 obs/pair: the guard is correctness, not precision.
    offset = sum(theta_true.values()) / len(theta_true)
    for cid, t_true in theta_true.items():
        assert abs(posterior.theta[cid] - (t_true - offset)) < 0.5, f"θ[{cid}] off its truth"
    for sid, d_true in delta_true.items():
        assert abs(posterior.delta[sid] - (d_true - offset)) < 0.5, f"δ[{sid}] off its truth"
    # The population the data was drawn from: the spread of each truth, and δ's centre on θ's scale.
    for fitted, truth, tol in (
        (posterior.sigma_theta, np.std(list(theta_true.values())), 0.6),
        (posterior.sigma_delta, np.std(list(delta_true.values())), 0.6),
        (posterior.mu_delta, np.mean(list(delta_true.values())) - offset, 0.4),
    ):
        assert abs(fitted - truth) < tol, "the population spread is not the one drawn"


_EASY, _HARD = ["1", "2", "3"], ["4", "5", "6"]
# δ −2 under the easy slots, +2 under the hard, 200 cells a slot.
_LEVELS = {f"{sid}.{i}": -2.0 if sid in _EASY else 2.0 for sid in _EASY + _HARD for i in range(200)}


def _drawn(theta: float, sids: list[str], rng: np.random.Generator) -> dict[str, float]:
    """An arm at *theta* answering every cell of *sids* on `_LEVELS`."""
    cells = [f"{sid}.{i}" for sid in sids for i in range(200)]
    p = 1.0 / (1.0 + np.exp(-(theta - np.array([_LEVELS[c] for c in cells]))))
    return dict(zip(cells, (rng.random(len(cells)) < p).astype(float).tolist(), strict=True))


def _theta_of(
    responses: dict[str, float], scale: Any, *, binary: bool = True
) -> tuple[float, float]:
    read = fit_theta(responses, scale, binary=binary)
    assert read is not None, "an arm with cells on the ruler carried no θ"
    return read


def test_theta_on_a_fixed_ruler_ranks_arms_by_ability_never_by_the_subset_they_read() -> None:
    rng, ruler, theta_of = np.random.default_rng(7), _LEVELS, _theta_of
    able, weak = _drawn(1.5, _HARD, rng), _drawn(-0.5, _EASY, rng)
    assert np.mean(list(weak.values())) > np.mean(list(able.values())), (
        "guard: raw accuracy must INVERT the true ability here"
    )
    able_theta, weak_theta = theta_of(able, ruler)[0], theta_of(weak, ruler)[0]
    assert able_theta > weak_theta, "θ on a fixed ruler ranked the arms by their subset"
    assert abs(able_theta - 1.5) < 0.5 and abs(weak_theta + 0.5) < 0.5, "θ off its truth"
    same_easy = theta_of(_drawn(0.8, _EASY, rng), ruler)[0]
    same_hard = theta_of(_drawn(0.8, _HARD, rng), ruler)[0]
    assert abs(same_easy - same_hard) < 0.5, "one ability read differently on two subsets"


def test_a_cell_off_a_warm_ruler_enters_no_theta_and_a_cold_one_reads_logit_fitness() -> None:
    # δ=0 is a position on the scale, not neutral.
    ruler, theta_of = _LEVELS, _theta_of
    able = _drawn(1.5, _HARD, np.random.default_rng(7))
    assert fit_theta({"99": 1.0}, ruler, binary=True) is None, "a cell off the ruler carried a θ"
    assert fit_theta({**able, "99": 0.0}, ruler, binary=True) == fit_theta(
        able, ruler, binary=True
    ), "an off-ruler cell moved θ"
    assert theta_of({"99": 1.0}, None)[0] > 0.0, (
        "a COLD ruler reads plain logit-fitness: one hit is θ > 0"
    )


def test_theta_se_reads_the_dispersion_its_scorer_declares() -> None:
    # A GRADED response varies less about its mean than Bernoulli.
    ruler, theta_of, easy, hard = _LEVELS, _theta_of, _EASY, _HARD
    graded = {f"{sid}.{i}": 0.5 + 0.02 * i for i, sid in enumerate(easy * 6)}
    spread = {f"{sid}.{i}": float(i % 2) for i, sid in enumerate(easy * 6)}
    assert theta_of(graded, ruler, binary=False)[1] < theta_of(spread, ruler)[1], (
        "a graded response was read as noisy as a coin flip"
    )
    # Hits far below θ and misses far above: φ fitted on them is ≪ 1. Through `frontier`, a sheet
    # under `label_match` holds φ = 1 by its scorer's declaration; the same cells under `rr` fit it.
    separated = [(f"{s}.{i}", float(s in easy)) for i, s in enumerate([*easy, *hard] * 6)]
    cells = [measurement(n, y, objective=y, sample_key=k) for n, (k, y) in enumerate(separated)]
    stamp = RulerStamp(
        id="v", version=0, own=None, scope=RulerScope(scorer="s", dataset="ds"), excluded=()
    )
    judging = JudgingRuler(_anchored(ruler), stamp)
    rate, score = (
        judging.frontier(scorer_of(formula).graded(cells))
        for formula in ("label_match(predicted, ground_truth)", "rr(ground_truth_rank)")
    )
    assert rate is not None and score is not None and score.se < 0.8 * rate.se, (
        "θ_se read one φ under a two-valued scorer and a graded one"
    )
    # φ is the scorer's declaration, never read off the values: a 0.999 for a 1.0 is still Bernoulli.
    hits = {f"{sid}.{i}": float(i % 3 != 0) for i, sid in enumerate(easy * 4)}
    nudged = theta_of({**hits, next(k for k, y in hits.items() if y): 0.999}, ruler)[1]
    assert abs(nudged - theta_of(hits, ruler)[1]) < 0.01 * nudged, (
        "a 0.999 re-read φ off the values"
    )

    flat = {f"{sid}.{i}": 0.5 for i, sid in enumerate(easy * 6)}
    assert theta_of(flat, ruler, binary=False)[1] > 0.1, (
        "no residual spread read as infinite confidence: every gate would fire"
    )


def test_theta_holds_across_a_2pl_graduation_and_converges_five_logits_out() -> None:
    theta_of = _theta_of
    # A 1PL entry and a 2PL entry at a = 1 are one ruler.
    bare = {str(s): 0.2 * s - 1.0 for s in range(8)}
    on_unit_a = {s: (d, 1.0) for s, d in bare.items()}
    for arm in (
        {str(s): float(s % 2 == 0) for s in range(8)},
        {str(s): float(s % 3 == 0) for s in range(8)},
    ):
        assert abs(theta_of(arm, bare)[0] - theta_of(arm, on_unit_a)[0]) < 1e-9, (
            "every θ shifts at 2PL graduation"
        )

    # Every δ sits five logits from the θ=0 seed, where an undamped `grad / info` step limit-cycles.
    far = dict.fromkeys(map(str, range(20)), 5.55)
    parent_theta, parent_se = theta_of(dict.fromkeys(far, 0.20), far, binary=False)
    better_theta, better_se = theta_of(dict.fromkeys(far, 0.45), far, binary=False)

    assert 2.0 < parent_theta < better_theta < 7.0 and parent_theta < 5.55, (
        parent_theta,
        better_theta,
    )
    assert parent_se < 1.0 and better_se < 1.0, (
        "an SE in the hundreds: the iteration never converged"
    )


def test_the_ruler_graduates_to_2pl_only_where_2pl_wins_held_out() -> None:
    theta = {f"c{i}": float(t) for i, t in enumerate(np.linspace(-2.5, 2.5, 20))}
    delta = {str(s): float(d) for s, d in enumerate(np.linspace(-2.0, 2.0, 12))}
    varied = _synth(
        theta,
        delta,
        n_per_pair=8,
        rng=np.random.default_rng(2),
        disc={s: 2.5 if int(s) % 2 == 0 else 0.4 for s in delta},
    )
    unit_a = _synth(theta, delta, n_per_pair=8, rng=np.random.default_rng(3))
    sparse = [
        Observation("a", "1", True),
        Observation("a", "2", False),
        Observation("b", "1", False),
    ]
    for data, enable, model, why in (
        (varied, True, "2PL", "varied discrimination never graduated"),
        (unit_a, True, "1PL", "2PL won on true a≡1"),
        (varied, False, "1PL", "graduation ran while disabled"),
        (sparse, True, "1PL", "too sparse to hold a cell out, yet graduated"),
    ):
        graduated, post = graduate_ruler_model(data, enable=enable)
        assert graduated == model, why
        assert bool(post.discrimination) == (model == "2PL"), "the posterior is not the model named"


# 2. The δ ruler, and the cells a round reads it on


def _anchored(delta: dict[str, float], *, mu: float = 0.0) -> DeltaRuler:
    fit = RaschPosterior(
        theta={}, theta_se={}, delta=delta, delta_se=dict.fromkeys(delta, 0.5), mu_delta=mu
    )
    return fit.anchored("1PL")


def test_a_delta_ruler_warms_only_once_a_second_arm_answers() -> None:
    # Eight samples clear any sample floor, so the arm count is the binding condition.
    arm_a = [Observation("a", str(sid), 1.0 if sid % 3 else 0.0) for sid in range(8)]
    arm_b = [Observation("b", str(sid), 1.0 if sid < 5 else 0.0) for sid in range(8)]
    flat = calibrate_delta_ruler(arm_a, 4, enable_2pl=False)
    assert flat is None, "one arm warmed the ruler: its δ would be read off the arm under test"
    warm = calibrate_delta_ruler(arm_a + arm_b, 4, enable_2pl=False)
    assert warm is not None and warm.calibration_model == "1PL", "a second arm left δ flat"


def test_one_tenant_ruler_judges_an_arm_only_on_the_rows_it_may_read(tmp_path: Path) -> None:
    # Versioned by its folds. C0 hits a quarter of the bank; its child changed nothing.
    pool40 = samples(40, "t", query="r")
    c0_hits = {s.key for s in pool40 if s.id % 4 == 3}
    race_rows = [
        (cid, s.key, float(s.key in c0_hits)) for cid in ("c0-address", "child") for s in pool40
    ]
    foreign = [(f"f{arm}", s.key, float((s.id + arm) % 3 != 0)) for arm in range(3) for s in pool40]
    race = [Observation(*row) for row in race_rows]
    store, scope = RulerStore(tmp_path), RulerScope(scorer="acc", dataset="ds")
    opened = store.version()
    arm, stranger = RulerOwn(campaign="arm", since=opened), RulerOwn(campaign="x", since=opened)
    for folded, by, own in (
        (foreign, "else", None),
        (foreign, "else", None),
        (race_rows, "arm", arm),
    ):
        store.fold(scope, folded, campaign=by, own=own)
    assert store.version() == opened + 2, "a re-fold of rows already held ticked the version"
    other = [(f"g{arm}", s.key, float(s.id % 2)) for arm in range(3) for s in pool40]
    store.fold(RulerScope(scorer="acc", dataset="other"), other, campaign="else", own=None)

    def judging(own: RulerOwn | None, judged: set[str]) -> Any:
        reach = RulerReach(version=store.version(), own=own)
        return judging_ruler(
            store, scope, reach, judged, objective_id="acc", n_min=4, enable_2pl=False
        )

    outside = calibrate_delta_ruler([Observation(*row) for row in foreign], 4, enable_2pl=False)
    optimizing = judging(None, {"c0-address", "child", "never-measured"})
    assert outside is not None and optimizing.ruler is not None
    assert optimizing.ruler.anchor_id == outside.anchor_id, (
        "the judged arms' rows, or another dataset's, entered the scale that judges them"
    )
    assert optimizing.stamp.excluded == ("c0-address", "child"), "one scale, two stamps"
    foreign_arm = judging(stranger, set())
    assert foreign_arm.ruler is None and foreign_arm.stamp.cold, (
        "an arm read another campaign's rows folded after its head-to-head opened"
    )
    on_race = calibrate_delta_ruler(race, 4, enable_2pl=False)
    compared = judging(arm, set())
    assert on_race is not None and compared.ruler is not None, (
        "an arm's own elections never reached its ruler, so it draws one frozen subset forever"
    )
    assert compared.ruler.anchor_id == on_race.anchor_id and compared.stamp.own == arm
    # Another campaign's identical rows past the freeze are not the arm's: its fold still lands.
    store.fold(scope, foreign, campaign="arm", own=arm)
    assert store.version() == opened + 4, (
        "an arm's rows were dropped as held by a line it never reads"
    )


class _Warm(NamedTuple):
    """One warm round on a graded ruler: the parent hits the easiest 16 of 40 cells, and each
    round's cells must still be the pool's, or the election reads rows the held-out bench never would."""

    drawing: Any
    pool: list[Any]
    sampler: Any
    search: list[Any]

    @property
    def session(self) -> Any:
        return self.drawing.cycle.session

    def drawn_on(self, round_num: int) -> list[int]:
        at = replace(self.drawing, round_num=round_num)
        return [
            int(s.id) for s in self.sampler.draw(NodeContext(at, self.sampler), self.search).cells
        ]

    def ordered(self, round_num: int) -> list[int]:
        at = replace(self.drawing, round_num=round_num)
        return [
            int(s.id) for s in self.sampler.draw(NodeContext(at, self.sampler), self.search).order
        ]

    def folded(self, arms: str, grade: Callable[[Any], float]) -> None:
        """A fold of every pool cell by eight arms, the round reopened on the lines it now holds."""
        self.session.store.ruler.fold(
            session_ruler_scope(self.session),
            [(f"{arms}{arm}", s.key, grade(s)) for arm in range(8) for s in self.pool],
            campaign="else",
            own=None,
        )
        self.drawing.cycle.difficulty.open_round(
            self.drawing.cycle.rounds, ruler_clock(self.session)
        )


@pytest.fixture
def warm(offline: Router) -> _Warm:  # ~3 s to open for the first fact, ~0.5 s after
    graded_pool = samples(40, "TRUE", query="d")
    answered = offline.backend.matches

    def hits_easiest(payload: dict[str, Any]) -> dict[str, Any]:
        answer = answered(payload)
        hit = int(payload["query"].removeprefix("d")) < 16
        answer["data"]["final_ranking"][0]["candidate"] = "TRUE" if hit else "FALSE"
        return answer

    offline.backend.matches = hits_easiest  # type: ignore[method-assign]
    drawing = jumping_clock(run_init_round(workspace(), offline, graded_pool))
    session = drawing.cycle.session
    made = _Warm(
        drawing,
        graded_pool,
        drawing.cycle.optimizer.plan.sampler,
        list(session.state.scoring.partition.search),
    )
    made.folded("f", lambda s: 1.0 - (s.id + 0.5) / 40)
    assert drawing.cycle.difficulty.ruler is not None, "guard: the draw under test is the warm one"
    return made


@pytest.mark.usefixtures("optimizing_memory")
def test_a_round_draws_fresh_cells_off_the_pool_whichever_its_parents_ruler(warm: _Warm) -> None:
    rounds_drawn = {n: warm.drawn_on(n) for n in range(1, 11)}
    assert warm.drawn_on(3) == rounds_drawn[3], "a resumed round drew other cells"
    assert len({tuple(sorted(ids)) for ids in rounds_drawn.values()}) == len(rounds_drawn), (
        "a round re-read the cells an earlier round drew"
    )
    assert all(min(ids) < 10 and max(ids) >= 30 for ids in rounds_drawn.values()), (
        "a round's draw narrowed onto one band of the ruler"
    )
    parent_rate = sum(sid < 16 for ids in rounds_drawn.values() for sid in ids) / sum(
        len(ids) for ids in rounds_drawn.values()
    )
    assert parent_rate == pytest.approx(16 / 40, abs=0.08), (
        "the parent reads one rate on its rounds' cells and another on the pool"
    )


@pytest.mark.usefixtures("optimizing_memory")
def test_a_reentered_round_orders_on_the_lines_it_opened_on(warm: _Warm) -> None:
    opened_on = warm.drawing.cycle.difficulty.stamp.reach
    live = warm.ordered(3)
    warm.folded("g", lambda s: (s.id + 0.5) / 40)
    assert warm.ordered(3) != live, "guard: the fold moves a draw read on the current lines"
    warm.drawing.cycle.difficulty.open_round(warm.drawing.cycle.rounds, opened_on)
    assert warm.ordered(3) == live, "a re-entered round walked an order its opening never drew"


def test_a_draw_led_by_one_level_still_covers_every_dataset_and_model_of_its_pool() -> None:
    pool = [
        Sample(id=i, query=f"{d}/{m}/{seed}", ground_truth=None, source_pin={"d": d, "m": m})
        for i, (d, m, seed) in enumerate(
            (d, m, seed) for d in ("easy", "hard") for m in ("small", "large") for seed in (0, 1)
        )
    ]
    drawn = covered_prefix(pool, 3)
    for axis in ("d", "m"):
        covered = {s.source_pin[axis] for s in drawn}  # type: ignore[index]
        assert len(covered) == 2, "a draw dropped a dataset or model of its pool"
    assert drawn[0].query == "easy/small/0", "the cover reordered a draw it already covered"


class _OrderPanel(NamedTuple):
    ids: list[int]
    panel: dict[int, Sample]
    parent_grades: dict[str, float]
    ordering: DeltaRuler
    on_slot: dict[int, float]
    hit_ids: set[int]


@pytest.fixture
def order_panel() -> _OrderPanel:
    """24 slots, the parent hitting the first 9, a ruler carrying the first 20; slot 23 is ungraded."""
    panel = {s.id: s for s in samples(24)}
    ids = list(panel)
    parent_grades = {panel[sid].key: 1.0 if sid < 9 else 0.0 for sid in range(23)}
    ordering = _anchored({panel[sid].key: float(sid % 7) for sid in range(20)}, mu=2.85)
    on_slot = {sid: ordering.delta[s.key] for sid, s in panel.items() if s.key in ordering.delta}
    return _OrderPanel(ids, panel, parent_grades, ordering, on_slot, set(range(9)))


def test_a_round_order_leads_with_win_opportunities_and_probes_a_parent_hit_every_fourth_slot(
    order_panel: _OrderPanel,
) -> None:
    ids, panel, parent_grades, ordering, on_slot, hit_ids = order_panel
    order = build_round_order(parent_grades, ordering, panel, n_min=0)
    assert sorted(order) == ids
    assert build_round_order(parent_grades, ordering, panel, n_min=0) == order, (
        "the order is not reproducible"
    )
    for pos in (4, 8, 12, 16):
        assert order[pos - 1] in hit_ids, f"position {pos} should be a parent-hit probe"
    assert not hit_ids & {order[i] for i in range(16) if (i + 1) % 4}, "a hit took a miss's slot"
    # Computed, not spelled: a hardcoded default here would pass while disagreeing with the ruler.
    centre = ordering.mu_delta
    assert min(ordering.delta.values()) < centre < max(ordering.delta.values())
    misses = [sid for sid in order if sid not in hit_ids and sid != 23]
    miss_keys = [(on_slot.get(sid, centre), sid) for sid in misses]
    assert miss_keys == sorted(miss_keys), "misses are not led by the easiest"
    assert order.index(23) > max(order.index(sid) for sid in misses), "an unknown cell led a miss"
    hit_keys = [(-on_slot.get(sid, centre), sid) for sid in order if sid in hit_ids]
    assert hit_keys == sorted(hit_keys), "hit probes are not led by the hardest"


def test_a_round_order_walks_n_min_cells_on_the_ruler_first_and_keeps_its_probe(
    order_panel: _OrderPanel,
) -> None:
    # PoBB can cut at n_min only once n_min θ-readable cells are walked.
    _, panel, parent_grades, ordering, _, hit_ids = order_panel
    order = build_round_order(parent_grades, ordering, panel, n_min=0)
    led = build_round_order(parent_grades, ordering, panel, n_min=6)
    assert all(panel[sid].key in ordering.delta for sid in led[:6]), (
        "an off-ruler cell was walked before n_min θ-readable ones: no arm can be cut that early"
    )
    assert order.index(20) < 6 <= led.index(20), "guard: the strata alone walk off the ruler early"
    assert led[3] in hit_ids, "the lead dropped the regression probe"


def test_a_panel_with_no_parent_grade_opens_on_its_most_discriminating_cells(
    order_panel: _OrderPanel,
) -> None:
    ids, panel, _, ordering, on_slot, _ = order_panel
    centre = ordering.mu_delta
    unknown = build_round_order({}, ordering, panel, n_min=0)
    assert sorted(unknown) == ids
    spread = [abs(on_slot.get(sid, centre) - centre) for sid in unknown]
    assert spread == sorted(spread), (
        "an all-unknown panel must open on its most discriminating cells"
    )
    easiest = min(on_slot, key=lambda sid: (on_slot[sid], sid))
    assert unknown.index(easiest) >= 6, "the panel's easiest cell must not lead the round"


def test_an_arm_cut_at_four_of_six_cells_over_three_benchmarks_has_read_all_three() -> None:
    # δ alone would walk benchmark "a"'s easy misses first: a cut at 4 then never reads "c".
    rows = samples(6)
    panel = {s.id: s.model_copy(update={"measured_dataset": "abc"[s.id // 2]}) for s in rows}
    ordering = _anchored({s.key: float(s.id) for s in rows}, mu=2.5)
    grades = {s.key: 0.0 for s in rows}
    order = build_round_order(grades, ordering, panel, n_min=0)
    assert {panel[sid].measured_dataset for sid in order[:4]} == set("abc")
    assert [sid for sid in order if panel[sid].measured_dataset == "a"] == [0, 1], (
        "a benchmark's rows lost their δ order"
    )
    one = {sid: s.model_copy(update={"measured_dataset": None}) for sid, s in panel.items()}
    assert build_round_order(grades, ordering, one, n_min=0) == [0, 1, 2, 3, 4, 5], (
        "a one-benchmark panel's order moved"
    )


def test_a_theta_off_the_judging_scale_is_withheld_and_its_caveat_names_why() -> None:
    # A child on cells the judging scale does not carry stays off it until its round folds.
    fitted = _anchored({"1": 1.5, "2": -1.25, "3": 0.0})
    child = {sid: float(sid == "7") for sid in ("7", "8", "9")}
    assert fit_theta(child, fitted.entries(), binary=True) is None, (
        "a child read on unlinked cells carried a θ"
    )
    tied = _anchored({"1": 0.0, "2": 0.0, "3": 3.0})
    for scale, read_on, expected in (
        (fitted, ["1", "2", "3"], None),
        (fitted, ["1", "2", "3", "7"], ThetaCaveat.UNMEASURED_DELTA),
        (tied, ["1", "2", "3"], ThetaCaveat.PRIOR_PINNED),
        (None, ["1", "2"], ThetaCaveat.COLD_RULER),
    ):
        served = theta_caveat(scale, read_on, prior_dominated=False)
        assert served is expected, "a state θ was read in is not the caveat it carries"


def test_a_line_read_on_cells_the_scale_lacks_still_differences_its_levels() -> None:
    # Adaptive rows read each arm on its own cells: θ is item-free on one scale, so they difference.
    for caveat, differences in (
        (ThetaCaveat.UNMEASURED_DELTA, True),
        (ThetaCaveat.COLLAPSED_BAND, False),
        (ThetaCaveat.PRIOR_PINNED, False),
    ):
        series = level_series(0.30, [0.31, 0.32, 0.30], caveat=caveat)
        first, last = series.level(0), series.level(3)
        lift = None if first is None or last is None else last.lift_over(first)
        read = (lift is not None, series.plateau() is not None)
        assert read == (differences, differences), (
            f"`{caveat}` withheld a level it only footnotes, or read one it withholds"
        )


def test_a_round_whose_band_collapsed_withholds_its_own_level_alone() -> None:
    series = level_series(0.30, [0.31, 0.32, 0.30])
    collapsed = series.levels[2]
    assert collapsed is not None
    levels = {
        **series.levels,
        2: collapsed.model_copy(update={"caveat": ThetaCaveat.COLLAPSED_BAND}),
    }
    line = series.model_copy(update={"levels": levels})
    read = [rnd for rnd in line.rounds if line.level(rnd) is not None]
    assert read == [0, 1, 3], "one round's collapsed band withheld another round's level"


def test_a_ruler_id_names_its_delta_never_key_order_and_a_cold_one_names_its_objective() -> None:
    fitted = _anchored({"1": 1.5, "2": -1.25, "3": 0.0})
    assert fitted.anchor_id == anchor_id_of({"3": 0.0, "2": -1.25, "1": 1.5}, 0.0, 2.0, "1PL")
    assert fitted.anchor_id != anchor_id_of({"1": 1.5, "2": -1.2500001, "3": 0.0}, 0.0, 2.0, "1PL")
    assert flat_ruler_id("acc") != flat_ruler_id("acc_minus_latency")


def test_a_stratum_short_of_its_share_hands_the_rest_on_and_an_empty_one_takes_nothing() -> None:
    assert stratum_shares([2, 10, 10], 9) == [2, 4, 3]
    assert stratum_shares([0, 5], 4) == [0, 4]
    assert stratum_shares([1, 1], 5) == [1, 1]
    assert stratum_shares([30], 20) == [20]


class _Traced(NamedTuple):
    closed: Callable[..., Any]
    hits: dict[str, list[float]]
    tenant: RulerStore
    frozen: int
    run: list[Any]


_SCOPE = RulerScope(scorer="acc", dataset="ds")


def _series(tenant: RulerStore, lines: list[Any], own: RulerOwn | None = None) -> list[Any]:
    reach = RulerReach(version=tenant.version(), own=own)
    return level_series_of(tenant, _SCOPE, reach, lines, n_min=4, enable_2pl=False)


@pytest.fixture
def traced() -> _Traced:
    """A run of four rounds on a warm tenant ruler."""
    bank = samples(12, "t")

    def graded(hits: list[float]) -> Any:
        return sheet(
            measurement(s.id, h, objective=h, sample_key=s.key)
            for s, h in zip(bank, hits, strict=True)
        )

    def closed(n: int, walked: dict[str, list[float]], pick: str | None, ended_on: str) -> Any:
        # Each election reads its own version; the series must read none of them.
        stamp = RulerStamp(id=f"v{n}", version=n, own=None, scope=_SCOPE, excluded=())
        return round_result(
            n,
            arms=[arm_state(k) for k in walked],
            cells={arm_key(k): graded(h) for k, h in walked.items()},
            election=election(pick and arm_key(pick), ended_on=arm_key(ended_on), ruler=stamp),
        )

    hits = {
        "o": [float(i % 4 == 0) for i in range(12)],
        "a": [float(i % 2 == 0) for i in range(12)],
        "b": [float(i % 6 == 0) for i in range(12)],
    }
    # Graded, never binary: tied δ would pin most cells to the prior.
    tenant = workspace().ruler
    tenant.fold(
        _SCOPE,
        [(f"f{arm}", s.key, (s.id + 0.5) / 12) for arm in range(8) for s in bank],
        campaign="else",
        own=None,
    )
    frozen = tenant.version()
    run = [
        closed(0, {"o": hits["o"]}, "o", "o"),
        closed(1, {"a": hits["a"], "b": hits["b"]}, "a", "a"),
    ]
    for rr in run:
        # After its election, as `DifficultyView.fold` banks a round.
        rows = [
            (rr.arms[k].config_key, c.key, c.grade.objective)
            for k, sh in rr.cells.items()
            for c in sh
        ]
        tenant.fold(_SCOPE, rows, campaign="run", own=None)
    run += [
        closed(2, {"b": hits["b"], "a": hits["a"]}, None, "a"),
        round_result(3, election=None, arms=[], cells={}),
    ]
    return _Traced(closed, hits, tenant, frozen, run)


def test_every_round_reads_its_standing_parent_on_the_one_scale_its_series_stamps(
    traced: _Traced,
) -> None:
    [series] = _series(traced.tenant, [traced.run])
    levels = {rnd: series.level(rnd) for rnd in series.rounds}
    assert series.stamp.version == traced.tenant.version() and series.stamp.excluded == (
        "a",
        "o",
    ), "the series read a scale its own parent line moved, or not the run's clock"
    assert all(lv is not None and lv.ruler == series.stamp for lv in levels.values()), (
        "a round's level sat on the scale its own election read, not the series'"
    )
    assert levels[1] is not None and levels[0] is not None and levels[1].theta > levels[0].theta
    assert levels[1] == levels[2] == levels[3], (
        "a held round, or one that elected nothing, read another individual than the parent "
        "standing into it"
    )
    assert series.plateau() == pytest.approx(levels[3].theta), (
        "three rounds of one standing parent on one scale is the plateau"
    )


def test_a_series_reads_one_fit_less_every_lines_parents_and_no_row_past_its_reach(
    traced: _Traced,
) -> None:
    # Two scales would let UCB sum across a fork and its parent line; an L4 cell reads its outer
    # cycle's round-0 version plus its own lines.
    t = traced
    fork = [t.run[0], t.closed(1, {"a": t.hits["a"], "b": t.hits["b"]}, "b", "b")]
    for lines, own, excluded in (
        ([t.run, fork], None, ("a", "b", "o")),
        ([t.run], RulerOwn(campaign="run", since=t.frozen), ("a", "o")),
        ([t.run], RulerOwn(campaign="x", since=t.frozen), ()),
    ):
        read = _series(t.tenant, lines, own)
        assert {s.stamp for s in read} == {read[0].stamp} and read[0].stamp.excluded == excluded, (
            f"{len(lines)} line(s) at {own} read their levels on two scales, or past their reach"
        )


def test_a_series_on_no_delta_scale_withholds_its_theta_lift_with_its_caveat(
    traced: _Traced,
) -> None:
    lone = workspace(default_identity("lone")).ruler
    lone.fold(
        _SCOPE,
        [("f0", s.key, (s.id + 1) / 16) for s in samples(12, "t")],
        campaign="else",
        own=None,
    )
    [cold] = _series(lone, [traced.run])
    caveats = {lv.caveat for lv in cold.levels.values() if lv is not None}
    assert cold.stamp.cold and caveats == {ThetaCaveat.COLD_RULER} and cold.plateau() is None, (
        "a series on no δ scale read as one that differences"
    )
    ran = [round_result(1), round_result(2)]
    withheld = inner_cell_facts(
        cycle_result([0.40, 0.55], 0.30, ran).model_copy(update={"level_series": cold})
    )
    assert withheld.mean_round_delta is None, (
        "a reader asked the stamp, not the series' caveat, and differenced a cold series"
    )
    assert withheld.series_reason is ThetaCaveat.COLD_RULER, (
        "the withheld θ lift lost the caveat its series carries"
    )


def test_a_round_no_cell_ran_serves_no_error_rate() -> None:
    for scored, rate in ((0, None), (2, 0.0)):
        rr = round_result(1, candidates_scored=scored)
        rr = rr.model_copy(update={"diagnostics": compute_round_diagnostics(rr, None)})
        served = rr.model_dump(mode="json")["diagnostics"]
        assert served["error_rate"] == rate and served["warning_rate"] == rate, (
            f"{scored} arms scored served error_rate {served['error_rate']}, not {rate}"
        )


# 3. The L4 outer proxy — what one finished inner cycle says


def cyc(
    levels: list[float | None],
    *,
    origin: float | None = 0.30,
    rounds: list[RoundResult] | None = None,
    **kw: Any,
) -> tuple[CycleResult, list[RoundResult]]:
    """A finished cycle and the rounds its ledger closed, as `spawn.py` reads them."""
    ran = [round_result(i + 1) for i in range(len(levels))] if rounds is None else rounds
    return cycle_result(levels, origin, ran, **kw), ran


def delta(levels: list[float | None], **kw: Any) -> float | None:
    return inner_cell_facts(cyc(levels, **kw)[0]).mean_round_delta


def test_an_inner_cells_theta_lift_is_its_parent_levels_mean_over_the_round_budget() -> None:
    # The parent levels held forward to the round budget, averaged, less the origin's 0.30.
    walked: tuple[tuple[list[float | None], int | None, float, str], ...] = (
        ([0.40, 0.55], None, 0.175, "the mean read the endpoint (0.25)"),
        ([0.30], None, 0.0, "a flat cycle read a lift"),
        ([0.90, 0.35], None, 0.325, "WHEN the lift lands is part of the score"),
        ([0.05, 0.35], None, -0.10, "WHEN the lift lands is part of the score"),
        ([0.30, 0.60, 0.60], 4, 0.225, "quitting early was paid over the rounds that ran"),
        ([0.30, 0.60, 0.60, 0.60], 4, 0.225, "the denominator is not the round budget"),
        ([0.30, 0.60, 0.60], None, 0.20, "an undeclared budget is the series length"),
    )
    for levels, budget, mean, why in walked:
        kw = {} if budget is None else {"round_budget": budget}
        assert delta(levels, **kw) == pytest.approx(mean), why


def test_an_inner_cell_scores_its_held_out_curve_and_excludes_what_it_cannot_read() -> None:
    # The scored term is the held-out curve's area (`runner/bench.py::read_curve`): churn on the
    # search split's θ scores ZERO.
    churned = cyc([0.55, 0.55], bench_origin=0.40, bench_levels=[0.40, 0.40])
    churn = (
        inner_cell_facts(churned[0]).mean_round_delta,
        compute_outer_proxies(*churned),
    )
    assert (churn[0], churn[1].held_out_lift) == pytest.approx((0.25, 0.0))
    lifted = cyc([0.55, 0.55], bench_origin=0.40, bench_levels=[0.50, 0.50])
    assert compute_outer_proxies(*lifted).held_out_lift == pytest.approx(0.10)

    # EXCLUDED, never floored: the -1 floor would punish the optimizer prompt for a slow provider.
    tooling = [round_result(1, parse_failure=PARSE_FAILURE_TOOLING)]
    for unscoreable in (
        cyc([0.40, 0.55], stop_reason=StopReason.TOKEN_BUDGET),
        cyc([0.40], rounds=tooling, stop_reason=StopReason.TOKEN_BUDGET),
        cyc([0.55, 0.55], bench_origin=0.40, bench_levels=[0.50, None]),
        cyc([0.55], bench_origin=None),
        (cyc([0.55])[0].model_copy(update={"curve": None}), []),
    ):
        with pytest.raises(CellUnscoreableError):
            compute_outer_proxies(*unscoreable)
    # A series that differences nothing is a SERVED fact beside the scored term, never an exclusion.
    for unread in (
        cyc([0.40, 0.55], caveat=ThetaCaveat.COLD_RULER),
        cyc([0.40, None]),
        cyc([0.40, 0.55], origin=None),
    ):
        facts = inner_cell_facts(unread[0])
        assert compute_outer_proxies(*unread).held_out_lift == pytest.approx(0.0), (
            "a cold or holed search series excluded a cell its held-out lift scores"
        )
        assert facts.mean_round_delta is None and facts.series_reason, (
            "a θ lift read off a series that differences nothing, or withheld without its reason"
        )


# The L4 round reads its mean held-out lift over MEASURED cells, the floored share beside it:
# a floor is a typed fact on the row, never a -1 inside the mean.
def test_an_l4_round_means_its_measured_cells_and_reads_its_floored_share_beside() -> None:
    owned = cyc([0.40], stop_reason=StopReason.OPTIMIZER_ABORT)
    rows = [
        measurement(sid, None, pipeline_data={"cell_report": report}).facts
        for sid, report in enumerate(
            [
                inner_cell_report(
                    inner_cell_facts(cyc([0.40])[0]),
                    OuterSampleProxies(floored=0, held_out_lift=held, held_out_lift_se=0.02),
                )
                for held in (0.0, 0.0, 0.0, 0.05, 0.0, 0.0375)
            ]
            + [inner_cell_report(inner_cell_facts(owned[0]), compute_outer_proxies(*owned))]
        )
    ]
    family = yaml.safe_load(
        (Path(__file__).parents[1] / "research/datasets/promptpotter-self/family.yaml").read_text()
    )
    l4 = scorer_of(family["campaign"]["scoring"]["per_sample"])
    graded = l4.sheet(rows)
    round_fold = compute_composite_fitness(graded, l4, pipeline_schema([]))
    assert round_fold.term_means == pytest.approx({"floored": 1 / 7, "held_out_lift": 0.0875 / 6})
    # The L4 formula is no matcher's call, so every printer's source reads a score; a matcher's a rate.
    l4_sources = (
        round_fold.fitness_unit,
        own_level(graded, l4.fitness_unit).fitness.unit,  # type: ignore[union-attr]
        grade_measurands(l4)[0].unit,
    )
    assert l4_sources == ("score",) * 3, "a non-rate formula's fitness printed as a percent"
    assert grade_measurands(scorer_of())[0].unit == "rate"


# Ending on its own terms, a cycle none of whose L1 proposals reached a cell is FLOORED,
# whatever its replies' failure: potter banks an empty panel, a paper optimizer Invalid arms.
def test_an_inner_cycle_no_proposal_of_which_reached_a_cell_floors() -> None:
    owned = cyc([0.40], stop_reason=StopReason.OPTIMIZER_ABORT)
    floor = OuterSampleProxies(floored=1, held_out_lift=None, held_out_lift_se=None)

    def failed(reason: ValidationReason, rnd: int) -> RoundResult:
        return round_result(rnd, parse_failure=reason)

    for spoiled in (
        [failed(PARSE_FAILURE_TOOLING, 1)],
        [failed(PARSE_FAILURE_MALFORMED, 1), failed(PARSE_FAILURE_MALFORMED, 2)],
        [round_result(1, candidates_scored=0, no_op=1, dup=1)],
        [round_result(1, candidates_scored=0, no_op=2), failed(PARSE_FAILURE_MALFORMED, 2)],
    ):
        assert compute_outer_proxies(*cyc([0.40, 0.40], rounds=spoiled)) == floor, (
            f"no proposal of {[r.round for r in spoiled]} reached a cell, yet the cell scored"
        )
    assert compute_outer_proxies(*owned) == floor, (
        "its optimizer's own abort was excluded or scored as a held lift"
    )
    reached = [failed(PARSE_FAILURE_MALFORMED, 1), round_result(2, no_op=1)]
    assert compute_outer_proxies(*cyc([0.40, 0.55], rounds=reached)).floored == 0, (
        "a cycle one proposal of which reached a cell floored"
    )
    assert delta([0.40, 0.55], rounds=reached) == pytest.approx(0.175), (
        "a malformed reply is the prompt's verdict and scores"
    )


def test_panel_precision_separates_outer_cells_on_the_held_out_estimate_never_theta() -> None:
    # Every cell carries ONE θ series, so a θ reading would see no spread at all.
    facts = inner_cell_facts(cycle_result([0.40, 0.55], 0.30, [round_result(1), round_result(2)]))
    l4 = scorer_of("0.0 if floored else max(0.0, min(1.0, held_out_lift + 0.5))")

    def arm(lifts: list[float | None]) -> tuple[GradedCell, ...]:
        held = [
            OuterSampleProxies(floored=0, held_out_lift=lift, held_out_lift_se=0.03)
            if lift is not None
            else OuterSampleProxies(floored=1, held_out_lift=None, held_out_lift_se=None)
            for lift in lifts
        ]
        rows = [
            measurement(
                sid,
                None,
                query=f"cell {sid}",
                pipeline_data={"cell_report": inner_cell_report(facts, px)},
            )
            for sid, px in enumerate(held)
        ]
        return l4.sheet([row.facts for row in rows]).cells

    read = panel_precision(arm([0.10, 0.20, 0.0, None]), arm([0.0, 0.0, 0.0, 0.0]))
    assert read is not None and read.n_cells == 3, "a floored cell entered the precision read"
    assert (read.observed_sd, read.estimation_sd) == pytest.approx((0.10, 0.03 * 2**0.5)), (
        "the panel's spread was read off θ, not the held-out lift and its SE"
    )


def test_an_inner_cell_floors_on_the_rounds_its_line_spent() -> None:
    def spoiled(rnd: int) -> RoundResult:
        return round_result(rnd, parse_failure=PARSE_FAILURE_MALFORMED)

    def proxies(spans: list[LineSpan]) -> OuterSampleProxies:
        rounds = [rr for span in spans for rr in span.own]
        return compute_outer_proxies(cycle_result([0.40, 0.40], 0.30, rounds), rounds)

    # Its first cycle reached a cell in round 1; a rebase cut at 1 and the fork spoiled every round.
    reached_then_forked = [
        LineSpan([round_result(1), spoiled(2)], None),
        LineSpan([spoiled(1), spoiled(2)], 1),
    ]
    assert proxies(reached_then_forked).floored == 0, (
        "the round a rebase discarded never judged whether a proposal reached a cell"
    )
    # Every round of both cycles spoiled: the floor names the four the line spent, not the fork's two.
    all_spoiled = [LineSpan([spoiled(1), spoiled(2)], None), LineSpan([spoiled(1), spoiled(2)], 1)]
    assert proxies(all_spoiled).floored == 1, "a line that spent every round on spoils scored"


def test_an_abort_floors_only_where_the_optimizer_prompt_owns_it() -> None:
    def ran(*cells: Any) -> list[RoundResult]:
        sheet = scorer_of().sheet([cell.facts for cell in cells])
        return [round_result(1), round_result(2, cells={"x": sheet})]

    def aborted(rounds: list[RoundResult]) -> OuterSampleProxies:
        result = cycle_result(
            [0.40, 0.55],
            0.30,
            rounds,
            stop_reason=StopReason.OPTIMIZER_ABORT,
            bench_origin=0.40,
            bench_levels=[0.45, 0.50],
        )
        return compute_outer_proxies(result, rounds)

    # The owner is what the line FILED before the abort: a 504 is the backend's, never the prompt's.
    backend = ran(measurement(0, None, error="504: no reply", error_category="SERVER"))
    assert aborted(backend).floored == 0, "an abort on a backend fault floored its prompt"
    prompt = ran(measurement(0, 0.0), measurement(1, None, error="bad", error_category="PIPELINE"))
    assert aborted(prompt) == OuterSampleProxies(
        floored=1, held_out_lift=None, held_out_lift_se=None
    ), "an abort the optimizer prompt owns was scored as a measured lift"
