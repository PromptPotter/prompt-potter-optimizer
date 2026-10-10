"""A wrong θ — the scale every ability is read on.

Owns `application/intelligence/exploration.py` and `adaptive_queue_mechanism.py`,
`application/bench/difficulty.py`, `domain/ruler.py` and `domain/l4/proxies.py`. Pure estimator
arithmetic: each test recovers a known answer from data built to have one.
"""

from __future__ import annotations

from pathlib import Path
from statistics import NormalDist

import numpy as np
import pytest

from promptpotter.application.intelligence.exploration import (
    Observation,
    extend_ruler,
    fit_rasch,
    fit_theta_given_delta,
    graduate_ruler_model,
    parent_level_trajectory,
)
from promptpotter.domain.optimizer_state import PARSE_FAILURE_MALFORMED, PARSE_FAILURE_TOOLING
from promptpotter.domain.phases import StopReason
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.results import ArmAbility, RoundResult
from promptpotter.domain.ruler import (
    DELTA_STATE_LABEL,
    AbilityReading,
    DeltaRuler,
    DeltaState,
    ThetaCaveat,
    anchor_id_of,
    flat_ruler_id,
    series_levels,
    theta_band,
    theta_caveat,
    theta_plateau,
)
from promptpotter.infrastructure.store.io import read_yaml
from tests.factories import cycle_result, round_result, scored_candidate

# 1. The estimator


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


def test_rasch_recovers_known_parameters() -> None:
    theta_true = {"strong": 1.5, "mid": 0.0, "weak": -1.5}
    delta_true = {1: -1.0, 2: 0.0, 3: 1.0, 4: 2.0}
    obs = _synth_observations(theta_true, delta_true, n_per_pair=80, seed=42)

    posterior = fit_rasch(obs)

    assert posterior.converged
    assert abs(sum(posterior.theta.values()) / len(posterior.theta)) < 1e-6
    # 0.5 logits is the noise floor at n=80 obs/pair: the guard is correctness, not precision.
    theta_offset = sum(theta_true.values()) / len(theta_true)
    for cid, t_true in theta_true.items():
        assert abs(posterior.theta[cid] - (t_true - theta_offset)) < 0.5
    for sid, d_true in delta_true.items():
        assert abs(posterior.delta[sid] - (d_true - theta_offset)) < 0.5

    # The true population spread: θ std ≈ 1.22, δ std ≈ 1.12, mean δ ≈ 0.5.
    assert abs(posterior.sigma_theta - 1.22) < 0.6
    assert abs(posterior.sigma_delta - 1.12) < 0.6
    assert abs(posterior.mu_delta - 0.5) < 0.4


def test_fit_theta_given_delta_is_subset_invariant_unlike_accuracy() -> None:
    rng = np.random.default_rng(7)
    ruler = {1: -2.0, 2: -2.0, 3: -2.0, 4: 2.0, 5: 2.0, 6: 2.0}
    easy, hard = [1, 2, 3], [4, 5, 6]

    def measure(cid: str, theta: float, sids: list[int], n: int = 200) -> list[Observation]:
        obs: list[Observation] = []
        for sid in sids:
            p = 1.0 / (1.0 + np.exp(-(theta - ruler[sid])))
            obs.extend(Observation(cid, sid, float(rng.random() < p)) for _ in range(n))
        return obs

    able = measure("able", 1.5, hard)
    weak = measure("weak", -0.5, easy)
    able_acc = sum(o.response for o in able) / len(able)
    weak_acc = sum(o.response for o in weak) / len(weak)
    assert weak_acc > able_acc  # raw accuracy INVERTS the true ability

    fit = fit_theta_given_delta(able + weak, ruler)
    assert fit["able"][0] > fit["weak"][0]
    assert abs(fit["able"][0] - 1.5) < 0.5  # within the noise floor at n=200/pair
    assert abs(fit["weak"][0] - (-0.5)) < 0.5
    assert all(se > 0 for _, se in fit.values())

    same_easy = fit_theta_given_delta(measure("x", 0.8, easy), ruler)["x"][0]
    same_hard = fit_theta_given_delta(measure("x", 0.8, hard), ruler)["x"][0]
    assert abs(same_easy - same_hard) < 0.5

    # A sample absent from a WARM ruler enters no θ: δ=0 is a position on the scale, not neutral.
    assert fit_theta_given_delta([Observation("ghost", 99, True)], ruler) == {}
    off_scale = fit_theta_given_delta([*able, Observation("able", 99, False)], ruler)
    assert off_scale["able"] == fit_theta_given_delta(able, ruler)["able"]
    # A COLD ruler is the one legitimate flat read: θ is plain logit-accuracy, so one hit ⇒ θ > 0.
    cold = fit_theta_given_delta([Observation("ghost", 99, True)], None)
    assert cold["ghost"][0] > 0.0

    # θ_se is dispersion-corrected: a GRADED response varies less about its mean than Bernoulli.
    graded = [Observation("g", sid, 0.5 + 0.02 * i) for i, sid in enumerate(easy * 6)]
    spread = [Observation("b", sid, float(i % 2)) for i, sid in enumerate(easy * 6)]
    assert (
        fit_theta_given_delta(graded, ruler)["g"][1] < fit_theta_given_delta(spread, ruler)["b"][1]
    )

    # φ ≈ 1 on binary data, so the correction leaves a dichotomous dataset unchanged.
    binary = measure("bin", 0.8, easy, n=60)
    p_hat = 1.0 / (1.0 + np.exp(-(fit_theta_given_delta(binary, ruler)["bin"][0] - (-2.0))))
    bern_se = 1.0 / np.sqrt(len(binary) * p_hat * (1 - p_hat) + 1 / 1.5**2)
    assert abs(fit_theta_given_delta(binary, ruler)["bin"][1] - bern_se) < 0.15 * bern_se

    # The φ floor: no residual spread is not infinite confidence (SE → 0 ⇒ every gate fires).
    flat = [Observation("f", sid, 0.5) for sid in easy * 6]
    assert fit_theta_given_delta(flat, ruler)["f"][1] > 0.1

    # A (δ, 1.0) ruler gives the same θ as a bare-δ one, or every θ shifts at 2PL graduation.
    seam_obs = [Observation("c1", s, float(s % 2 == 0)) for s in range(8)] + [
        Observation("c2", s, float(s % 3 == 0)) for s in range(8)
    ]
    bare = {s: 0.2 * s - 1.0 for s in range(8)}
    fb = fit_theta_given_delta(seam_obs, bare)
    ft = fit_theta_given_delta(seam_obs, {s: (d, 1.0) for s, d in bare.items()})
    assert all(abs(fb[c][0] - ft[c][0]) < 1e-9 for c in fb)

    # Every δ sits five logits from the θ=0 seed, where an undamped `grad / info` step limit-cycles.
    far = dict.fromkeys(range(20), 5.55)
    parent = [Observation("parent", sid, 0.20) for sid in far]
    better = [Observation("better", sid, 0.45) for sid in far]
    damped = fit_theta_given_delta(parent + better, far, sigma_theta=1.34)
    parent_theta, parent_se = damped["parent"]
    better_theta, better_se = damped["better"]
    assert 2.0 < parent_theta < 5.55, parent_theta
    assert parent_theta < better_theta < 7.0, better_theta
    # An SE in the hundreds is the tell that the iteration never converged.
    assert parent_se < 1.0 and better_se < 1.0, (parent_se, better_se)


def _synth_2pl(
    theta: np.ndarray,
    delta: np.ndarray,
    disc: np.ndarray,
    *,
    n_per_pair: int,
    seed: int,
) -> list[Observation]:
    rng = np.random.default_rng(seed)
    obs: list[Observation] = []
    for i, th in enumerate(theta):
        for s, (d, a) in enumerate(zip(delta, disc, strict=True)):
            p = 1.0 / (1.0 + np.exp(-a * (th - d)))
            obs.extend(Observation(f"c{i}", s, bool(rng.random() < p)) for _ in range(n_per_pair))
    return obs


def test_graduation_gate_stays_1pl_until_2pl_wins_holdout() -> None:
    theta = np.linspace(-2.5, 2.5, 20)
    delta = np.linspace(-2.0, 2.0, 12)

    disc_varied = np.array([2.5 if s % 2 == 0 else 0.4 for s in range(12)])
    data_2pl = _synth_2pl(theta, delta, disc_varied, n_per_pair=8, seed=2)
    model_g, post_g = graduate_ruler_model(data_2pl, enable=True)
    assert model_g == "2PL"
    assert post_g.discrimination

    # True a≡1: 2PL cannot win held-out.
    data_1pl = _synth_2pl(theta, delta, np.ones(12), n_per_pair=8, seed=3)
    model_flat, post_flat = graduate_ruler_model(data_1pl, enable=True)
    assert model_flat == "1PL"
    assert not post_flat.discrimination

    model_off, _ = graduate_ruler_model(data_2pl, enable=False)
    assert model_off == "1PL"

    # Too sparse to hold any cell out.
    sparse = [Observation("a", 1, True), Observation("a", 2, False), Observation("b", 1, False)]
    assert graduate_ruler_model(sparse, enable=True)[0] == "1PL"


# 2. The δ ruler, and the cells a round reads it on


def test_delta_ruler_stays_flat_until_a_second_arm_exists() -> None:
    from promptpotter.application.bench.difficulty import calibrate_delta_ruler
    from promptpotter.application.intelligence.exploration import Observation

    n_min = 4
    arm_a = [Observation("a", sid, 1.0 if sid % 3 else 0.0) for sid in range(8)]

    # Eight samples clear any sample floor, so the arm count is the binding condition.
    flat, _ = calibrate_delta_ruler(None, n_min, enable_2pl=False, archive_obs=arm_a)
    assert flat is None

    arm_b = [Observation("b", sid, 1.0 if sid < 5 else 0.0) for sid in range(8)]
    warm, _ = calibrate_delta_ruler(None, n_min, enable_2pl=False, archive_obs=arm_a + arm_b)
    assert warm is not None and warm.calibration_model == "1PL"


def _ruler(
    delta: dict[int, float],
    *,
    mu: float = 0.0,
    sigma: float = 2.0,
    se: float | dict[int, float] = 0.5,
) -> DeltaRuler:
    return DeltaRuler(
        delta=dict(delta),
        delta_se=dict(se) if isinstance(se, dict) else dict.fromkeys(delta, se),
        mu_delta=mu,
        sigma_delta=sigma,
        sigma_theta=1.5,
        calibration_model="1PL",
        anchor_id=anchor_id_of(delta, mu, sigma, "1PL"),
    )


def test_a_child_read_on_cells_its_parent_skipped_this_round_stays_off_the_ruler() -> None:
    fitted = _ruler({1: 1.5, 2: -1.25, 3: 0.0})
    child = [Observation("child", sid, float(sid == 7)) for sid in (7, 8, 9)]
    parent_now = [Observation("parent", sid, 1.0) for sid in (1, 2, 3)]

    held = extend_ruler(fitted, child + parent_now, history=[])
    assert held.delta == fitted.delta
    assert held.unlinked([7, 8, 9]) == 3 and held.unlinked([1, 2]) == 0
    assert fit_theta_given_delta(child, held) == {}
    span = held.delta_span
    served = {
        n: theta_caveat(calibration_model="1PL", round_span=span, ruler_span=span, unlinked=n)
        for n in (0, 3)
    }
    assert served == {0: None, 3: ThetaCaveat.UNMEASURED_DELTA}
    # A prior-pinned round is its own state: the pin cancels inside a lift, a skipped cell does not.
    pinned = {
        n: theta_caveat(
            calibration_model="1PL", round_span=span, ruler_span=span, unlinked=n, pinned_share=0.5
        )
        for n in (0, 3)
    }
    assert pinned == {0: ThetaCaveat.PRIOR_PINNED, 3: ThetaCaveat.PRIOR_PINNED}

    earlier = [Observation("parent", sid, float(sid != 8)) for sid in (7, 8, 9)]
    linked = extend_ruler(fitted, child + parent_now, history=earlier + parent_now)
    assert set(linked.delta) == {1, 2, 3, 7, 8, 9}
    assert all(linked.delta[sid] == fitted.delta[sid] for sid in fitted.delta)
    assert linked.anchor_id == fitted.anchor_id
    assert linked.delta[8] > linked.delta[7]
    assert linked.unlinked([7, 8, 9]) == 0

    # ONE cell shared this round links the child, and the child carries its other cells on.
    chained = extend_ruler(fitted, [*child, Observation("parent", 7, 1.0), *parent_now], history=[])
    assert chained.unlinked([7, 8, 9]) == 0
    assert all(chained.delta[sid] == fitted.delta[sid] for sid in fitted.delta)

    # The id names the ANCHOR, never membership or key order; a cold cycle's names its OBJECTIVE.
    assert fitted.anchor_id == anchor_id_of({3: 0.0, 2: -1.25, 1: 1.5}, 0.0, 2.0, "1PL")
    assert fitted.anchor_id != anchor_id_of({1: 1.5, 2: -1.2500001, 3: 0.0}, 0.0, 2.0, "1PL")
    assert flat_ruler_id("acc") != flat_ruler_id("acc_minus_latency")


def test_parent_level_trajectory_is_honest_single_scale() -> None:
    ruler = _ruler({1: -1.0, 2: 0.0, 3: 1.0})

    def on(theta: float, se: float, *, scale: DeltaRuler | None = ruler) -> AbilityReading:
        cal = scale.calibration_model if scale is not None else None
        # Read on the whole ruler, so the round's own band IS the ruler's.
        span = scale.delta_span if scale is not None else None
        return AbilityReading(
            # `scale=None` is a COLD reading, incomparable with any fitted anchor.
            theta=theta,
            se=se,
            ruler_id=scale.anchor_id if scale is not None else flat_ruler_id("acc"),
            ruler_n=len(scale.delta) if scale is not None else 0,
            ruler_span=span,
            round_span=span,
            calibration_model=cal,
            caveat=theta_caveat(
                calibration_model=cal, round_span=span, ruler_span=span, unlinked=0
            ),
        )

    origin_theta = on(0.0, 0.30)

    def thetas(levels: list[tuple[float, float]]) -> list[float]:
        return [t for t, _ in levels]

    # LOGITS, not expected accuracy: an identical Δθ is an identical gain wherever the origin sits.
    low_o, low = parent_level_trajectory(on(-1.0, 0.2), [on(-0.5, 0.2)], ruler)
    high_o, high = parent_level_trajectory(on(1.5, 0.2), [on(2.0, 0.2)], ruler)
    assert low_o is not None and high_o is not None
    assert (
        thetas(low)[0] - low_o[0]
        == pytest.approx(thetas(high)[0] - high_o[0])
        == pytest.approx(0.5)
    )

    # The level is the parent the round ADOPTED, never a statistic over its proposals.
    o_lvl, levels = parent_level_trajectory(origin_theta, [on(0.2702, 0.21)], ruler)
    assert o_lvl == (origin_theta.theta, origin_theta.se)
    assert levels == [(pytest.approx(0.2702), pytest.approx(0.21))]

    # A peak then a collapse reads LOWER than a sustained peak; a running max reads them alike.
    _, spike = parent_level_trajectory(origin_theta, [on(1.2, 0.2), on(-2.0, 0.2)], ruler)
    _, held = parent_level_trajectory(origin_theta, [on(1.2, 0.2), on(1.2, 0.2)], ruler)
    assert thetas(spike)[1] < thetas(spike)[0] and sum(thetas(spike)) < sum(thetas(held))

    # An unfit round carries the PRIOR level, θ and SE both.
    o2, lv2 = parent_level_trajectory(origin_theta, [None], ruler)
    assert o2 is not None and lv2 == [o2]
    _, carried = parent_level_trajectory(origin_theta, [on(1.0, 0.11), None], ruler)
    assert carried == [(1.0, 0.11), (1.0, 0.11)]

    # A parent below origin yields a level BELOW origin, never floored at it.
    o3, lv3 = parent_level_trajectory(origin_theta, [on(-2.0, 0.2)], ruler)
    assert o3 is not None and thetas(lv3)[0] < o3[0]

    # `(None, [])` makes the caller EXCLUDE the cycle (`no_evidence_reason`).
    assert parent_level_trajectory(None, [on(1.0, 0.2)], ruler) == (None, [])
    assert parent_level_trajectory(origin_theta, [on(1.0, 0.2)], None) == (None, [])

    # A level read on another scale carries the prior level forward.
    _, mixed = parent_level_trajectory(origin_theta, [on(9.0, 0.2, scale=None)], ruler)
    assert mixed == [(origin_theta.theta, origin_theta.se)]
    assert parent_level_trajectory(on(0.0, 0.3, scale=None), [on(1.0, 0.2)], ruler) == (None, [])
    # A DIFFERENT warm ruler is as incomparable as a flat one: the id is the whole test.
    other = _ruler({1: -0.5, 2: 0.25, 3: 2.0})
    _, foreign = parent_level_trajectory(origin_theta, [on(9.0, 0.2, scale=other)], ruler)
    assert foreign == [(origin_theta.theta, origin_theta.se)]

    # A flat run that includes a reading off the series scale is not a plateau.
    flat = [on(0.50, 0.2), on(0.51, 0.2), on(0.52, 0.2)]
    assert theta_plateau(flat) == pytest.approx(0.51)
    assert theta_plateau([on(0.50, 0.2), on(0.51, 0.2, scale=other), on(0.52, 0.2)]) is None
    assert theta_plateau([*flat[:2], None]) is None
    assert theta_plateau([on(0.1, 0.2), on(0.5, 0.2), on(0.9, 0.2)]) is None
    assert series_levels([on(0.5, 0.2), on(9.0, 0.2, scale=other), None]) == [0.5, None, None]
    cold = [on(0.5, 0.2, scale=None)] * 3
    assert all(r.caveat is not None for r in cold) and theta_plateau(cold) is None


def test_build_round_order_fronts_win_opportunities_with_hit_probes() -> None:
    from promptpotter.application.intelligence.adaptive_queue_mechanism import build_round_order

    ids = list(range(24))
    parent_grades = dict.fromkeys(range(9), 1.0) | dict.fromkeys(range(9, 23), 0.0)
    ruler = _ruler({sid: float(sid % 7) for sid in range(20)}, mu=2.85)

    order = build_round_order(parent_grades, ruler, ids)
    assert sorted(order) == ids
    assert build_round_order(parent_grades, ruler, ids) == order

    hit_set = set(range(9))
    for pos in (4, 8, 12, 16):
        assert order[pos - 1] in hit_set, f"position {pos} should be a parent-hit probe"
    non_probe_head = [order[i] for i in range(16) if (i + 1) % 4 != 0]
    assert all(sid not in hit_set for sid in non_probe_head)
    # Computed, not spelled: a hardcoded default here would pass while disagreeing with the ruler.
    unmeasured = ruler.mu_delta
    assert min(ruler.delta.values()) < unmeasured < max(ruler.delta.values())
    miss_positions = [sid for sid in order if sid not in hit_set and sid != 23]
    miss_keys = [(ruler.delta.get(sid, unmeasured), sid) for sid in miss_positions]
    assert miss_keys == sorted(miss_keys)
    assert order.index(23) > max(order.index(sid) for sid in miss_positions)
    hit_positions = [sid for sid in order if sid in hit_set]
    hit_keys = [(-ruler.delta.get(sid, unmeasured), sid) for sid in hit_positions]
    assert hit_keys == sorted(hit_keys)

    all_unknown = build_round_order({}, ruler, ids)
    assert sorted(all_unknown) == ids
    spread = [abs(ruler.delta.get(sid, unmeasured) - unmeasured) for sid in all_unknown]
    assert spread == sorted(spread), (
        "an all-unknown panel must open on its most discriminating cells"
    )
    easiest = min(ruler.delta, key=lambda sid: (ruler.delta[sid], sid))
    assert all_unknown.index(easiest) >= 6, "the panel's easiest cell must not lead the round"


def test_the_hard_sample_view_reads_the_one_ruler_and_fits_nothing() -> None:
    from promptpotter.application.intelligence.adaptive_queue_mechanism import (
        build_round_order,
        pick_value,
    )
    from promptpotter.application.intelligence.hard_sample_sorter import build_hard_samples

    observations = [Observation("a", sid, float(sid % 2)) for sid in (1, 2, 3, 7)]
    observations += [Observation("b", sid, 1.0) for sid in (1, 2, 3)]
    grades = {1: 1.0, 2: 0.0, 3: 1.0, 7: 0.0}

    # COLD: δ is absent, never 0.0 (a position on the scale), and no row is dropped.
    cold = build_hard_samples(
        observations, None, frontier=None, arm_theta={}, parent_grades=grades, cycle_id=None
    )
    assert cold.ruler.state == "not_fitted"
    assert cold.ruler.label == DELTA_STATE_LABEL[DeltaState.NOT_FITTED] is not None
    assert set(cold.samples) == {1, 2, 3, 7}
    assert all(
        on.state is DeltaState.NOT_FITTED and on.delta is None and on.pick_score is None
        for on in cold.samples.values()
    )

    # WARM: cell 7 was answered and the ruler does not carry it; cell 9 it carries unanswered.
    ruler = _ruler({1: 1.5, 2: -1.25, 3: 0.0, 9: 0.4})
    warm = build_hard_samples(
        observations,
        ruler,
        frontier=(0.3, 0.2),
        arm_theta={"a": 0.9},
        parent_grades=grades,
        cycle_id=None,
    )
    assert warm.ruler.ruler_id == ruler.anchor_id
    linked = {sid: on.delta for sid, on in warm.samples.items() if on.state is DeltaState.LINKED}
    assert linked == ruler.delta
    off = warm.samples[7]
    assert off.state is DeltaState.UNLINKED and off.label == DELTA_STATE_LABEL[off.state]
    assert off.delta is None and off.delta_se is None
    assert off.pick_score is None and off.p_hat is None
    assert warm.sample_order == [1, 3, 2, 7]
    # The stamped θ orders the arms, not the hit rate: "b" answered everything and holds no θ.
    assert warm.candidate_order == ["a", "b"]
    assert warm.samples[1].pick_score == pytest.approx(
        pick_value(0.3, ruler.sigma_theta**2, 0.3, 0.2**2, 1.5, 0.5)
    )
    assert warm.round_order == build_round_order(grades, ruler, [1, 2, 3, 7])
    bare = build_hard_samples(
        observations, ruler, frontier=None, arm_theta={}, parent_grades={}, cycle_id=None
    )
    assert bare.samples[1].delta == 1.5 and bare.samples[1].pick_score is None


def test_an_arms_theta_interval_is_the_one_the_ruler_computes() -> None:
    served = ArmAbility.of(0.4, 0.25, None)
    assert served is not None
    assert (served.ci_lo, served.ci_hi) == theta_band(0.4, 0.25)
    z = NormalDist().inv_cdf(0.975)
    assert (served.ci_lo, served.ci_hi) == pytest.approx((0.4 - z * 0.25, 0.4 + z * 0.25))
    # A θ with no SE has no interval: the bounds are absent, never the level repeated.
    bare = ArmAbility.of(0.4, None, ThetaCaveat.UNMEASURED_DELTA)
    assert bare is not None and (bare.ci_lo, bare.ci_hi) == (None, None)


# 3. The L4 outer proxy — what one finished inner cycle says


def test_compute_proxies_is_one_exact_mean_over_the_parent_levels() -> None:
    from promptpotter.domain.l4.proxies import compute_outer_proxies, mean_parent_level_se

    px = compute_outer_proxies(cycle_result([0.40, 0.55], 0.30, [round_result(1), round_result(2)]))
    assert px.mean_round_delta == pytest.approx(0.175)  # endpoint would read 0.25

    flat = compute_outer_proxies(cycle_result([0.30], 0.30, [round_result(1)]))
    assert flat.mean_round_delta == pytest.approx(0.0)

    # Both trajectories END at 0.35: WHEN the lift lands is part of the score.
    early = compute_outer_proxies(
        cycle_result([0.90, 0.35], 0.30, [round_result(1), round_result(2)])
    )
    late = compute_outer_proxies(
        cycle_result([0.05, 0.35], 0.30, [round_result(1), round_result(2)])
    )
    assert early.mean_round_delta == pytest.approx(0.325)
    assert late.mean_round_delta == pytest.approx(-0.10)
    assert early.mean_round_delta > late.mean_round_delta

    # The denominator is the round BUDGET: over the rounds that ran, quitting would pay +0.30.
    quit_early = cycle_result(
        [0.30, 0.60, 0.60], 0.30, [round_result(i) for i in (1, 2, 3)], round_budget=4
    )
    ran_out = cycle_result(
        [0.30, 0.60, 0.60, 0.60], 0.30, [round_result(i) for i in (1, 2, 3, 4)], round_budget=4
    )
    # `parent_level_series` pads a short series with its last value; padding carries no precision.
    se_kw = {"origin_level_se": 0.20, "round_level_ses": [0.30, 0.40]}
    padded = cycle_result(
        [0.30, 0.60], 0.30, [round_result(i) for i in (1, 2)], round_budget=4, **se_kw
    )
    unpadded = cycle_result(
        [0.30, 0.60], 0.30, [round_result(i) for i in (1, 2)], round_budget=2, **se_kw
    )
    assert mean_parent_level_se(padded) == pytest.approx(mean_parent_level_se(unpadded))
    # Never sigma/sqrt(n): nested frontier fits are correlated, not independent draws.
    assert mean_parent_level_se(padded) == pytest.approx(0.35)
    assert mean_parent_level_se(padded) > 0.35 / 2

    # The origin's SE stays out: one measurement shared by both sides of `variant - origin` cancels.
    assert mean_parent_level_se(padded) != pytest.approx((0.35**2 + 0.20**2) ** 0.5)
    no_origin_se = cycle_result(
        [0.30, 0.60],
        0.30,
        [round_result(i) for i in (1, 2)],
        round_budget=4,
        round_level_ses=[0.30, 0.40],
    )
    assert mean_parent_level_se(no_origin_se) == pytest.approx(0.35)

    # No SE yields None: a fabricated 0.0 reads as an infinitely sharp cell.
    assert mean_parent_level_se(cycle_result([0.30], 0.30, [round_result(1)])) is None

    assert compute_outer_proxies(quit_early).mean_round_delta == pytest.approx(0.225)
    assert compute_outer_proxies(ran_out).mean_round_delta == pytest.approx(0.225)
    # An undeclared budget falls back to the series length rather than dividing by zero.
    assert compute_outer_proxies(
        cycle_result([0.30, 0.60, 0.60], 0.30, [round_result(i) for i in (1, 2, 3)])
    ).mean_round_delta == pytest.approx(0.20)


def test_compute_proxies_excludes_cycles_that_produced_no_evidence() -> None:
    from promptpotter.domain.l4.proxies import compute_outer_proxies
    from promptpotter.shared.errors import CellUnscoreableError

    # Zero L1 rounds, on a cycle that DID end on its own terms.
    empty = cycle_result([], 0.30, [], stop_reason=StopReason.TARGET_HIT)
    with pytest.raises(CellUnscoreableError):
        compute_outer_proxies(empty)

    # TOKEN_BUDGET stands for every rail: the verdict reads the typed `stop_reason_outcome` table.
    truncated = cycle_result(
        [0.40, 0.55],
        0.30,
        [round_result(1), round_result(2)],
        stop_reason=StopReason.TOKEN_BUDGET,
    )
    with pytest.raises(CellUnscoreableError):
        compute_outer_proxies(truncated)

    # EXCLUDED, never floored: the -1 floor would punish the optimizer prompt for a slow provider.
    railed_and_empty = cycle_result(
        [0.40],
        0.30,
        [round_result(1, parse_failure="l1_provider_empty_response")],
        stop_reason=StopReason.TOKEN_BUDGET,
    )
    with pytest.raises(CellUnscoreableError):
        compute_outer_proxies(railed_and_empty)

    levelless = cycle_result([], 0.30, [round_result(1)])
    with pytest.raises(CellUnscoreableError):
        compute_outer_proxies(levelless)

    floorless = cycle_result([0.40, 0.55], None, [round_result(1), round_result(2)])
    with pytest.raises(CellUnscoreableError):
        compute_outer_proxies(floorless)

    # Ending on its own terms, the same all-tooling cycle is FLOORED: the loss is reproducible.
    tooling = cycle_result(
        [0.40], 0.30, [round_result(1, parse_failure="l1_provider_empty_response")]
    )
    assert compute_outer_proxies(tooling).mean_round_delta == -1.0

    # Arms all lost to an empty reply floor; a malformed reply is the prompt's verdict and scores.
    def lost(reason: str) -> RoundResult:
        arm = scored_candidate("x", invalid_reason=reason)
        return round_result(1, candidates_scored=0, candidate_scores=[arm])

    empty_arms = cycle_result([0.40], 0.30, [lost(PARSE_FAILURE_TOOLING)])
    assert compute_outer_proxies(empty_arms).mean_round_delta == -1.0
    malformed = cycle_result([0.40, 0.55], 0.30, [lost(PARSE_FAILURE_MALFORMED), round_result(2)])
    assert compute_outer_proxies(malformed).mean_round_delta == pytest.approx(0.175)


def test_the_l4_dataset_is_recognized_as_one(tmp_path: Path) -> None:
    """The shipped panel measures in ONE unit: no inner cell may graduate its ruler to 2PL."""
    from promptpotter.application.campaign_config import load_campaign_config
    from promptpotter.application.datasets.loaders import samples_from_dicts
    from promptpotter.application.optimizer_manifest import select_optimizer
    from promptpotter.application.runner.inner.tasks import (
        inner_instrument_config,
        load_inner_tasks,
        resolve_inner_cells,
        resolve_inner_task,
    )
    from promptpotter.connectors import get
    from promptpotter.infrastructure.store.stores import build_stores
    from promptpotter.shared.identity import default_identity

    d = Path(__file__).resolve().parents[1] / "datasets" / "promptpotter-self"
    spec = d / get("promptpotter").experiment_file
    panel = load_inner_tasks(spec)
    assert panel.tasks

    # The SHIPPED config, not a hand-built one — the question is what the panel runs under.
    base = load_campaign_config(read_yaml(d / "campaign.yaml")["campaign_config"])
    cells = resolve_inner_cells(build_stores(default_identity(), projects_root=tmp_path), panel)
    l4 = get("promptpotter")
    resolved = l4.extract_experiment(l4.resolve_experiment(read_yaml(spec)))
    rows = {row.query: row for row in samples_from_dicts(resolved)}
    for task in panel.tasks:
        derived = inner_instrument_config(
            resolve_inner_task(cells, rows[task.id]),
            base,
            llm_node="llm_only",
            n_scored=40,
        )
        assert derived.optimization.enable_2pl_graduation is False, (
            f"cell {task.id} may graduate its ruler to 2PL — its theta would then be in "
            "units of 1/a while the rest of the panel is in logits, and the outer verdict "
            "pools them anyway"
        )

    # The manifest the outer mutates against is the one EVERY cell runs.
    shown = cells.optimizer
    graph = parse_pipeline_response(cells.pipeline())
    for task in panel.tasks:
        task_spec = resolve_inner_task(cells, rows[task.id])
        cell = inner_instrument_config(
            task_spec,
            load_campaign_config(dict(cells.by_dataset[task_spec.inner_dataset].campaign_config)),
            llm_node="llm_only",
            n_scored=40,
        )
        ran = select_optimizer(cell.optimization)
        assert shown.treatment() == ran.treatment()
        assert shown.knobs("escalation") == ran.knobs("escalation")
        assert {n.name for n in graph.declared_nodes if n.tunes_llm} == set(ran.llm_nodes)
