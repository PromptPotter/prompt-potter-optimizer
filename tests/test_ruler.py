"""A wrong θ — the scale every ability is read on.

Owns `application/intelligence/exploration.py` and `adaptive_queue_mechanism.py`,
`application/bench/difficulty.py`, `domain/ruler.py` and `domain/l4/proxies.py`. Pure estimator
arithmetic: each test recovers a known answer from data built to have one.
"""

from __future__ import annotations

from pathlib import Path

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
from promptpotter.domain.results import RoundResult
from promptpotter.domain.ruler import (
    AbilityReading,
    DeltaRuler,
    ThetaCaveat,
    anchor_id_of,
    flat_ruler_id,
    theta_caveat,
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

    # A sample absent from a WARM ruler enters no θ. It used to be graded at δ=0, which is not a
    # neutral value but a position: on a ruler centred near +2.8 it scored an unmeasured cell as
    # easier than anything ever measured, and silently pulled θ down ~2 logits for every round
    # whose subset had walked off the scale.
    assert fit_theta_given_delta([Observation("ghost", 99, True)], ruler) == {}
    off_scale = fit_theta_given_delta([*able, Observation("able", 99, False)], ruler)
    assert off_scale["able"] == fit_theta_given_delta(able, ruler)["able"]
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

    # The one-ruler seam: a (δ, 1.0) ruler must give the same θ as a bare-δ ruler, or every θ
    # shifts the moment a dataset graduates to 2PL — a wrong winner with no error.
    seam_obs = [Observation("c1", s, float(s % 2 == 0)) for s in range(8)] + [
        Observation("c2", s, float(s % 3 == 0)) for s in range(8)
    ]
    bare = {s: 0.2 * s - 1.0 for s in range(8)}
    fb = fit_theta_given_delta(seam_obs, bare)
    ft = fit_theta_given_delta(seam_obs, {s: (d, 1.0) for s, d in bare.items()})
    assert all(abs(fb[c][0] - ft[c][0]) < 1e-9 for c in fb)

    # `screen-taste-v0` cycle_df3de1e40b64: a graded objective puts every cell's δ near +5.55, and
    # the fit seeds at θ=0 — five logits away, where p saturates and the observed information is
    # the prior term alone. Undamped, `grad / info` is then a jump of ~14 logits into the OPPOSITE
    # saturation, and the iteration limit-cycles until max_iter stops it wherever it stands. It is
    # the arms sitting FURTHEST from the seed that overshoot, which on a hard ruler are the arms
    # that scored HIGHEST — so the failure reads improvement as collapse.
    far = dict.fromkeys(range(20), 5.55)
    parent = [Observation("parent", sid, 0.20) for sid in far]
    better = [Observation("better", sid, 0.45) for sid in far]
    damped = fit_theta_given_delta(parent + better, far, sigma_theta=1.34)
    parent_theta, parent_se = damped["parent"]
    better_theta, better_se = damped["better"]
    # Both land near the ruler they were measured on, not tens of logits away from it.
    assert 2.0 < parent_theta < 5.55, parent_theta
    assert parent_theta < better_theta < 7.0, better_theta
    # An SE in the hundreds is the tell that the iteration never converged at all.
    assert parent_se < 1.0 and better_se < 1.0, (parent_se, better_se)


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


# 2. The δ ruler, and the cells a round reads it on


def test_delta_ruler_stays_flat_until_a_second_arm_exists() -> None:
    # SILENT wrong-scale: with ONE ability the likelihood cannot separate "this item is hard" from
    # "this arm missed it", so δ collapses into a two-valued restatement of that arm's own hit
    # pattern — and every later θ in the cycle becomes a restatement of whether round 0 happened
    # to get the sample right. It is not an error; it is a plausible ruler that reads backwards.
    # It cost two campaigns: rounds carrying +14.3pp at p<0.05 were stamped `improved=False`.
    # The warm attempt now also runs BEFORE the round's election (`warm_ruler_if_cold`), which
    # relaxes the TIMING only — this is what pins the rule itself as untouched.
    from promptpotter.application.bench.difficulty import calibrate_delta_ruler
    from promptpotter.application.intelligence.exploration import Observation

    n_min = 4
    arm_a = [Observation("a", sid, 1.0 if sid % 3 else 0.0) for sid in range(8)]

    # Eight distinct samples clears any sample floor on its own — the arm count is the binding
    # condition, and a fresh campaign hands this function exactly this shape.
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


def test_a_child_read_on_cells_its_parent_skipped_this_round_stays_off_the_ruler() -> None:
    """A selector buys the parent only the cells it compares on, so a rejected child can hold cells
    no arm on the scale answered THIS round. Linking through this round's rows alone made the
    selector's parent cells a hidden duty of the ruler, and the cell it missed killed the round.

    Grade the cell at a default instead and nothing raises — the child's θ is read against a
    position nobody measured, and the round elects on it."""
    fitted = _ruler({1: 1.5, 2: -1.25, 3: 0.0})
    child = [Observation("child", sid, float(sid == 7)) for sid in (7, 8, 9)]
    parent_now = [Observation("parent", sid, 1.0) for sid in (1, 2, 3)]

    # No arm on the scale answered 7-9 anywhere in the cycle: they stay off, declared.
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

    # The parent answered them in an EARLIER round of this cycle: its ability, anchored on every
    # cell it has, carries them onto the scale without moving a δ already there.
    earlier = [Observation("parent", sid, float(sid != 8)) for sid in (7, 8, 9)]
    linked = extend_ruler(fitted, child + parent_now, history=earlier + parent_now)
    assert set(linked.delta) == {1, 2, 3, 7, 8, 9}
    assert all(linked.delta[sid] == fitted.delta[sid] for sid in fitted.delta)
    assert linked.anchor_id == fitted.anchor_id
    assert linked.delta[8] > linked.delta[7]
    assert linked.unlinked([7, 8, 9]) == 0

    # One cell shared this round puts the child on the scale, and the child carries its others.
    chained = extend_ruler(fitted, [*child, Observation("parent", 7, 1.0), *parent_now], history=[])
    assert chained.unlinked([7, 8, 9]) == 0
    assert all(chained.delta[sid] == fitted.delta[sid] for sid in fitted.delta)

    # The id names the ANCHOR, not the membership — which is why the extensions above keep it. Key
    # order is not part of the scale; a genuinely different δ is a different one, however small
    # the move; and a cold cycle has no fit to name, so its id is minted from the OBJECTIVE.
    assert fitted.anchor_id == anchor_id_of({3: 0.0, 2: -1.25, 1: 1.5}, 0.0, 2.0, "1PL")
    assert fitted.anchor_id != anchor_id_of({1: 1.5, 2: -1.2500001, 3: 0.0}, 0.0, 2.0, "1PL")
    assert flat_ruler_id("acc") != flat_ruler_id("acc_minus_latency")


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
            caveat=theta_caveat(
                calibration_model=cal, round_span=span, ruler_span=span, unlinked=0
            ),
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


# 3. The L4 outer proxy — what one finished inner cycle says


def test_compute_proxies_is_one_exact_mean_over_the_parent_levels() -> None:
    # SILENT wrong-score: the outer signal is ONE number, so any error in it is the whole
    # ranking. It must be the mean of the adopted levels minus the origin, over the cycle's
    # ROUND BUDGET. A divisor of any OTHER shape reappearing here (a declared target, or the
    # room `max(origin, 1-origin)`) fails loudly on the pins below, and so does a regression to
    # reading the series' last element: the two differ on every trajectory that is not flat.
    from promptpotter.domain.l4.proxies import compute_outer_proxies, mean_parent_level_se

    px = compute_outer_proxies(cycle_result([0.40, 0.55], 0.30, [round_result(1), round_result(2)]))
    assert px.mean_round_delta == pytest.approx(0.175)  # endpoint would read 0.25

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
    se_kw = {"origin_level_se": 0.20, "round_level_ses": [0.30, 0.40]}
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
        round_level_ses=[0.30, 0.40],
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
    from promptpotter.domain.l4.proxies import compute_outer_proxies
    from promptpotter.shared.errors import CellUnscoreableError

    # Zero L1 rounds, on a cycle that DID end on its own terms.
    empty = cycle_result([], 0.30, [], stop_reason=StopReason.TARGET_HIT)
    with pytest.raises(CellUnscoreableError):
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
    with pytest.raises(CellUnscoreableError):
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
    with pytest.raises(CellUnscoreableError):
        compute_outer_proxies(railed_and_empty)

    # Rounds ran, but the trajectory is empty → nothing to difference against origin. Without
    # the guard `first`/`after_N_rounds_delta` would both read a flat 0.0: "no lift" is a
    # plausible-looking number for "no measurement", which is what makes it dangerous.
    levelless = cycle_result([], 0.30, [round_result(1)])
    with pytest.raises(CellUnscoreableError):
        compute_outer_proxies(levelless)

    # Rounds AND levels, but the origin was never scored. Every delta here is measured against
    # that floor, so substituting 0.0 (the old `origin_acc` stand-in, itself 0.0 when nothing
    # was scored) reports the whole trajectory as an enormous lift over nothing — and it does so
    # for the CHEAPEST rows, since a crash at round 0 is what leaves the origin unscored.
    floorless = cycle_result([0.40, 0.55], None, [round_result(1), round_result(2)])
    with pytest.raises(CellUnscoreableError):
        compute_outer_proxies(floorless)

    # The same all-tooling cycle ending on its own terms is FLOORED, not excluded: losing every
    # round's candidates to an empty response is reproducible, so it is the optimizer prompt's.
    tooling = cycle_result(
        [0.40], 0.30, [round_result(1, parse_failure="l1_provider_empty_response")]
    )
    assert compute_outer_proxies(tooling).mean_round_delta == -1.0

    # A paper preset's round keeps its arms, so the same reading comes off them: every arm lost to
    # an empty reply floors, while one malformed reply is the prompt's own verdict and scores.
    def lost(reason: str) -> RoundResult:
        arm = scored_candidate("x", invalid_reason=reason)
        return round_result(1, candidates_scored=0, candidate_scores=[arm])

    empty_arms = cycle_result([0.40], 0.30, [lost(PARSE_FAILURE_TOOLING)])
    assert compute_outer_proxies(empty_arms).mean_round_delta == -1.0
    malformed = cycle_result([0.40, 0.55], 0.30, [lost(PARSE_FAILURE_MALFORMED), round_result(2)])
    assert compute_outer_proxies(malformed).mean_round_delta == pytest.approx(0.175)


def test_the_l4_dataset_is_recognized_as_one(tmp_path: Path) -> None:
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
    assert spec.is_file(), f"the L4 probe would read {d.name} as a plain dataset ({spec})"
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

    # The manifest the outer mutates against — its graph, base templates, wire limits and
    # identity — is the one EVERY cell runs, the panel's overlays laid on that cell's dataset's own.
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
