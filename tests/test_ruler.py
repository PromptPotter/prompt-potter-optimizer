"""A wrong θ — the scale every ability is read on.

Owns `application/intelligence/rasch.py` and `adaptive_queue_mechanism.py`,
`application/bench/difficulty.py`, `domain/ruler.py` and `domain/l4/proxies.py`. Pure estimator
arithmetic: each journey recovers a known answer from data built to have one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
from factories import cycle_result, round_result, scored_candidate

from promptpotter.application.bench.difficulty import calibrate_delta_ruler
from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.datasets.loaders import samples_from_dicts
from promptpotter.application.intelligence.adaptive_queue_mechanism import build_round_order
from promptpotter.application.intelligence.rasch import (
    Observation,
    RaschPosterior,
    extend_ruler,
    fit_rasch,
    fit_theta_given_delta,
    graduate_ruler_model,
    parent_level_trajectory,
    select_round_subset,
)
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.runner.inner.tasks import (
    inner_instrument_config,
    load_inner_tasks,
    resolve_inner_cells,
    resolve_inner_task,
)
from promptpotter.connectors import get
from promptpotter.domain.l4.proxies import compute_outer_proxies, mean_parent_level_se
from promptpotter.domain.optimizer_state import PARSE_FAILURE_MALFORMED, PARSE_FAILURE_TOOLING
from promptpotter.domain.phases import StopReason
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.results import CycleResult, RoundResult
from promptpotter.domain.ruler import (
    AbilityReading,
    DeltaRuler,
    ThetaCaveat,
    anchor_id_of,
    flat_ruler_id,
    series_levels,
    theta_caveat,
    theta_plateau,
)
from promptpotter.domain.sample import Sample
from promptpotter.infrastructure.store.io import read_yaml
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.shared.errors import CellUnscoreableError
from promptpotter.shared.identity import default_identity

# 1. The estimator


def _synth(
    theta: dict[str, float],
    delta: dict[int, float],
    *,
    n_per_pair: int,
    rng: np.random.Generator,
    disc: dict[int, float] | None = None,
) -> list[Observation]:
    obs: list[Observation] = []
    for cid, t in theta.items():
        for sid, d in delta.items():
            p = 1.0 / (1.0 + np.exp(-(1.0 if disc is None else disc[sid]) * (t - d)))
            obs.extend(Observation(cid, sid, float(rng.random() < p)) for _ in range(n_per_pair))
    return obs


def test_the_estimator_recovers_the_scale_its_data_was_drawn_on() -> None:
    theta_true = {"strong": 1.5, "mid": 0.0, "weak": -1.5}
    delta_true = {1: -1.0, 2: 0.0, 3: 1.0, 4: 2.0}
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

    # θ given a fixed δ: subset-invariant, where raw accuracy inverts the true ability.
    rng = np.random.default_rng(7)
    ruler = {1: -2.0, 2: -2.0, 3: -2.0, 4: 2.0, 5: 2.0, 6: 2.0}
    easy, hard = [1, 2, 3], [4, 5, 6]

    def measure(cid: str, theta: float, sids: list[int], n: int = 200) -> list[Observation]:
        return _synth({cid: theta}, {sid: ruler[sid] for sid in sids}, n_per_pair=n, rng=rng)

    able, weak = measure("able", 1.5, hard), measure("weak", -0.5, easy)
    assert sum(o.response for o in weak) / len(weak) > sum(o.response for o in able) / len(able), (
        "guard: raw accuracy must INVERT the true ability here"
    )
    fit = fit_theta_given_delta(able + weak, ruler)
    assert fit["able"][0] > fit["weak"][0], "θ on a fixed ruler ranked the arms by their subset"
    assert abs(fit["able"][0] - 1.5) < 0.5 and abs(fit["weak"][0] + 0.5) < 0.5, "θ off its truth"
    same_easy = fit_theta_given_delta(measure("x", 0.8, easy), ruler)["x"][0]
    same_hard = fit_theta_given_delta(measure("x", 0.8, hard), ruler)["x"][0]
    assert abs(same_easy - same_hard) < 0.5, "one ability read differently on two subsets"

    # A sample absent from a WARM ruler enters no θ: δ=0 is a position on the scale, not neutral.
    assert fit_theta_given_delta([Observation("ghost", 99, True)], ruler) == {}
    off_scale = fit_theta_given_delta([*able, Observation("able", 99, False)], ruler)
    assert off_scale["able"] == fit_theta_given_delta(able, ruler)["able"], (
        "an off-ruler cell moved θ"
    )
    cold = fit_theta_given_delta([Observation("ghost", 99, True)], None)
    assert cold["ghost"][0] > 0.0, "a COLD ruler reads plain logit-accuracy: one hit is θ > 0"

    # θ_se is dispersion-corrected: a GRADED response varies less about its mean than Bernoulli.
    graded = [Observation("g", sid, 0.5 + 0.02 * i) for i, sid in enumerate(easy * 6)]
    spread = [Observation("b", sid, float(i % 2)) for i, sid in enumerate(easy * 6)]
    assert (
        fit_theta_given_delta(graded, ruler)["g"][1] < fit_theta_given_delta(spread, ruler)["b"][1]
    ), "a graded response was read as noisy as a coin flip"
    binary, prior = measure("bin", 0.8, easy, n=60), 1.5
    bin_theta, bin_se = fit_theta_given_delta(binary, ruler, sigma_theta=prior)["bin"]
    p_hat = 1.0 / (1.0 + np.exp(-(bin_theta - ruler[easy[0]])))
    bern_se = 1.0 / np.sqrt(len(binary) * p_hat * (1 - p_hat) + 1 / prior**2)
    assert abs(bin_se - bern_se) < 0.15 * bern_se, "φ moved the SE of a dichotomous dataset"
    flat = [Observation("f", sid, 0.5) for sid in easy * 6]
    assert fit_theta_given_delta(flat, ruler)["f"][1] > 0.1, (
        "no residual spread read as infinite confidence: every gate would fire"
    )

    # A 1PL entry and a 2PL entry at a = 1 are one ruler.
    seam_obs = [Observation("c1", s, float(s % 2 == 0)) for s in range(8)] + [
        Observation("c2", s, float(s % 3 == 0)) for s in range(8)
    ]
    bare = {s: 0.2 * s - 1.0 for s in range(8)}
    on_1pl = fit_theta_given_delta(seam_obs, bare)
    on_unit_a = fit_theta_given_delta(seam_obs, {s: (d, 1.0) for s, d in bare.items()})
    assert all(abs(on_1pl[c][0] - on_unit_a[c][0]) < 1e-9 for c in on_1pl), (
        "every θ shifts at 2PL graduation"
    )

    # Every δ sits five logits from the θ=0 seed, where an undamped `grad / info` step limit-cycles.
    far = dict.fromkeys(range(20), 5.55)
    damped = fit_theta_given_delta(
        [Observation("parent", s, 0.20) for s in far]
        + [Observation("better", s, 0.45) for s in far],
        far,
        sigma_theta=1.34,
    )
    (parent_theta, parent_se), (better_theta, better_se) = damped["parent"], damped["better"]
    assert 2.0 < parent_theta < better_theta < 7.0 and parent_theta < 5.55, damped
    assert parent_se < 1.0 and better_se < 1.0, (
        "an SE in the hundreds: the iteration never converged"
    )

    # The 2PL graduation gate: 1PL until 2PL wins held-out.
    theta = {f"c{i}": float(t) for i, t in enumerate(np.linspace(-2.5, 2.5, 20))}
    delta = {s: float(d) for s, d in enumerate(np.linspace(-2.0, 2.0, 12))}
    varied = _synth(
        theta,
        delta,
        n_per_pair=8,
        rng=np.random.default_rng(2),
        disc={s: 2.5 if s % 2 == 0 else 0.4 for s in delta},
    )
    unit_a = _synth(theta, delta, n_per_pair=8, rng=np.random.default_rng(3))
    sparse = [Observation("a", 1, True), Observation("a", 2, False), Observation("b", 1, False)]
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


def _anchored(delta: dict[int, float], *, mu: float = 0.0) -> DeltaRuler:
    fit = RaschPosterior(
        theta={}, theta_se={}, delta=delta, delta_se=dict.fromkeys(delta, 0.5), mu_delta=mu
    )
    return fit.anchored("1PL")


def test_a_round_draws_orders_and_reads_its_cells_on_one_delta_ruler() -> None:
    # Calibrated: eight samples clear any sample floor, so the arm count is the binding condition.
    arm_a = [Observation("a", sid, 1.0 if sid % 3 else 0.0) for sid in range(8)]
    arm_b = [Observation("b", sid, 1.0 if sid < 5 else 0.0) for sid in range(8)]
    flat, _ = calibrate_delta_ruler(None, 4, enable_2pl=False, archive_obs=arm_a)
    assert flat is None, "one arm warmed the ruler: its δ would be read off the arm under test"
    warm, _ = calibrate_delta_ruler(None, 4, enable_2pl=False, archive_obs=arm_a + arm_b)
    assert warm is not None and warm.calibration_model == "1PL", "a second arm left δ flat"

    # Drawn: every `easy` cell on the `small` model outprices the rest; rank alone buys only those.
    pool = [
        Sample(id=i, query=f"{d}/{m}/{seed}", ground_truth=None, source_pin={"d": d, "m": m})
        for i, (d, m, seed) in enumerate(
            (d, m, seed) for d in ("easy", "hard") for m in ("small", "large") for seed in (0, 1)
        )
    ]
    worth = {int(s.id): 9.0 if s.query.startswith("easy/small") else 1.0 for s in pool}
    drawn = select_round_subset(pool, [], 3, worth=worth)
    for axis in ("d", "m"):
        covered = {s.source_pin[axis] for s in drawn}  # type: ignore[index]
        assert len(covered) == 2, "a priced draw dropped a dataset or model of its pool"
    assert drawn[0].query == "easy/small/0", "the draw is not led by its best-priced cell"

    # Ordered: win opportunities first, a parent-hit probe every fourth slot.
    ids = list(range(24))
    parent_grades = dict.fromkeys(range(9), 1.0) | dict.fromkeys(range(9, 23), 0.0)
    ordering = _anchored({sid: float(sid % 7) for sid in range(20)}, mu=2.85)
    order = build_round_order(parent_grades, ordering, ids)
    assert build_round_order(parent_grades, ordering, ids) == order, "the order is not reproducible"
    hits = set(range(9))
    for pos in (4, 8, 12, 16):
        assert order[pos - 1] in hits, f"position {pos} should be a parent-hit probe"
    assert not hits & {order[i] for i in range(16) if (i + 1) % 4}, "a hit took a miss's slot"
    # Computed, not spelled: a hardcoded default here would pass while disagreeing with the ruler.
    centre = ordering.mu_delta
    assert min(ordering.delta.values()) < centre < max(ordering.delta.values())
    misses = [sid for sid in order if sid not in hits and sid != 23]
    miss_keys = [(ordering.delta.get(sid, centre), sid) for sid in misses]
    assert miss_keys == sorted(miss_keys), "misses are not led by the easiest"
    assert order.index(23) > max(order.index(sid) for sid in misses), "an unknown cell led a miss"
    hit_keys = [(-ordering.delta.get(sid, centre), sid) for sid in order if sid in hits]
    assert hit_keys == sorted(hit_keys), "hit probes are not led by the hardest"
    unknown = build_round_order({}, ordering, ids)
    spread = [abs(ordering.delta.get(sid, centre) - centre) for sid in unknown]
    assert spread == sorted(spread), (
        "an all-unknown panel must open on its most discriminating cells"
    )
    easiest = min(ordering.delta, key=lambda sid: (ordering.delta[sid], sid))
    assert unknown.index(easiest) >= 6, "the panel's easiest cell must not lead the round"

    # Read: a child on cells its parent skipped this round stays off the ruler.
    fitted = _anchored({1: 1.5, 2: -1.25, 3: 0.0})
    child = [Observation("child", sid, float(sid == 7)) for sid in (7, 8, 9)]
    parent_now = [Observation("parent", sid, 1.0) for sid in (1, 2, 3)]
    held = extend_ruler(fitted, child + parent_now, history=[])
    assert held.delta == fitted.delta, "an unlinked child's cells entered the ruler"
    assert held.unlinked([7, 8, 9]) == 3 and held.unlinked([1, 2]) == 0
    assert fit_theta_given_delta(child, held) == {}, "a child read on unlinked cells carried a θ"
    span = held.delta_span
    for pinned, caveats in (
        (None, (None, ThetaCaveat.UNMEASURED_DELTA)),
        (0.5, (ThetaCaveat.PRIOR_PINNED,) * 2),
    ):
        served = tuple(
            theta_caveat(
                calibration_model="1PL",
                round_span=span,
                ruler_span=span,
                unlinked=n,
                pinned_share=pinned,
            )
            for n in (0, 3)
        )
        assert served == caveats, "the pin cancels inside a lift, a skipped cell does not"
    earlier = [Observation("parent", sid, float(sid != 8)) for sid in (7, 8, 9)]
    linked = extend_ruler(fitted, child + parent_now, history=earlier + parent_now)
    assert set(linked.delta) == {1, 2, 3, 7, 8, 9} and linked.unlinked([7, 8, 9]) == 0
    assert all(linked.delta[sid] == fitted.delta[sid] for sid in fitted.delta), "the anchor moved"
    assert linked.anchor_id == fitted.anchor_id and linked.delta[8] > linked.delta[7]
    chained = extend_ruler(fitted, [*child, Observation("parent", 7, 1.0), *parent_now], history=[])
    assert chained.unlinked([7, 8, 9]) == 0, "ONE shared cell this round must link the child"
    assert all(chained.delta[sid] == fitted.delta[sid] for sid in fitted.delta), "the anchor moved"
    # The id names the ANCHOR, never membership or key order; a cold cycle's names its OBJECTIVE.
    assert fitted.anchor_id == anchor_id_of({3: 0.0, 2: -1.25, 1: 1.5}, 0.0, 2.0, "1PL")
    assert fitted.anchor_id != anchor_id_of({1: 1.5, 2: -1.2500001, 3: 0.0}, 0.0, 2.0, "1PL")
    assert flat_ruler_id("acc") != flat_ruler_id("acc_minus_latency")

    # Traced: the parent level is one honest scale across rounds.
    ruler = _anchored({1: -1.0, 2: 0.0, 3: 1.0})
    other = _anchored({1: -0.5, 2: 0.25, 3: 2.0})

    def on(theta: float, se: float, *, scale: DeltaRuler | None = ruler) -> AbilityReading:
        # `scale=None` is a COLD reading; read on the whole ruler, the round's band IS the ruler's.
        cal = scale.calibration_model if scale is not None else None
        band = scale.delta_span if scale is not None else None
        return AbilityReading(
            theta=theta,
            se=se,
            ruler_id=scale.anchor_id if scale is not None else flat_ruler_id("acc"),
            ruler_n=len(scale.delta) if scale is not None else 0,
            ruler_span=band,
            round_span=band,
            calibration_model=cal,
            caveat=theta_caveat(
                calibration_model=cal, round_span=band, ruler_span=band, unlinked=0
            ),
        )

    def trace(
        origin: AbilityReading | None,
        *rounds: AbilityReading | None,
        scale: DeltaRuler | None = ruler,
    ) -> Any:
        return parent_level_trajectory(origin, list(rounds), scale)

    origin = on(0.0, 0.30)
    at_origin = (origin.theta, origin.se)
    (low_o, _), [(low, _)] = trace(on(-1.0, 0.2), on(-0.5, 0.2))
    (high_o, _), [(high, _)] = trace(on(1.5, 0.2), on(2.0, 0.2))
    assert low - low_o == pytest.approx(high - high_o) == pytest.approx(0.5), (
        "LOGITS, not expected accuracy: an identical Δθ is an identical gain wherever origin sits"
    )
    assert trace(origin, on(0.2702, 0.21)) == (
        at_origin,
        [(pytest.approx(0.2702), pytest.approx(0.21))],
    ), "the level is the parent the round ADOPTED, never a statistic over its proposals"
    _, spike = trace(origin, on(1.2, 0.2), on(-2.0, 0.2))
    _, sustained = trace(origin, on(1.2, 0.2), on(1.2, 0.2))
    assert spike[1][0] < spike[0][0] and sum(t for t, _ in spike) < sum(t for t, _ in sustained), (
        "a peak then a collapse must read LOWER than a sustained peak"
    )
    for rounds, levels, why in (
        ((None,), [at_origin], "an unfit round carries the PRIOR level, θ and SE both"),
        ((on(1.0, 0.11), None), [(1.0, 0.11)] * 2, "an unfit round carries the PRIOR level"),
        ((on(9.0, 0.2, scale=None),), [at_origin], "a level on another scale was read"),
        ((on(9.0, 0.2, scale=other),), [at_origin], "a DIFFERENT warm ruler is as incomparable"),
    ):
        assert trace(origin, *rounds)[1] == levels, why
    below_o, below = trace(origin, on(-2.0, 0.2))
    assert below[0][0] < below_o[0], "a parent below origin was floored at it"
    # `(None, [])` makes the caller EXCLUDE the cycle (`no_evidence_reason`).
    for excluded in (
        trace(None, on(1.0, 0.2)),
        trace(origin, on(1.0, 0.2), scale=None),
        trace(on(0.0, 0.3, scale=None), on(1.0, 0.2)),
    ):
        assert excluded == (None, []), "a cycle with no comparable origin was not excluded"
    plateau = [on(0.50, 0.2), on(0.51, 0.2), on(0.52, 0.2)]
    assert theta_plateau(plateau) == pytest.approx(0.51)
    for not_flat in (
        [on(0.50, 0.2), on(0.51, 0.2, scale=other), on(0.52, 0.2)],
        [*plateau[:2], None],
        [on(0.1, 0.2), on(0.5, 0.2), on(0.9, 0.2)],
        [on(0.5, 0.2, scale=None)] * 3,
    ):
        assert theta_plateau(not_flat) is None, "a run off the series scale read as a plateau"
    assert series_levels([on(0.5, 0.2), on(9.0, 0.2, scale=other), None]) == [0.5, None, None]
    assert on(0.5, 0.2, scale=None).caveat is not None, "a cold reading carried no caveat"


# 3. The L4 outer proxy — what one finished inner cycle says


def test_one_finished_inner_cycle_scores_the_optimizer_prompt_that_ran_it(tmp_path: Path) -> None:
    # The shipped panel measures in ONE unit: no inner cell may graduate its ruler to 2PL.
    d = Path(__file__).resolve().parents[1] / "datasets" / "promptpotter-self"
    l4 = get("promptpotter")
    spec = d / l4.experiment_file
    panel = load_inner_tasks(spec)
    assert panel.tasks
    # The SHIPPED config, not a hand-built one — the question is what the panel runs under.
    base = CampaignConfig.model_validate(read_yaml(d / "campaign.yaml")["campaign_config"])
    cells = resolve_inner_cells(build_stores(default_identity(), projects_root=tmp_path), panel)
    rows = {
        row.query: row
        for row in samples_from_dicts(l4.extract_experiment(l4.resolve_experiment(read_yaml(spec))))
    }
    graph = parse_pipeline_response(cells.pipeline())
    for task in panel.tasks:
        task_spec = resolve_inner_task(cells, rows[task.id])
        derived = inner_instrument_config(task_spec, base, llm_node="llm_only", n_scored=40)
        assert derived.optimization.enable_2pl_graduation is False, (
            f"cell {task.id} may graduate its ruler to 2PL — its theta would then be in units of "
            "1/a while the rest of the panel is in logits, and the outer verdict pools them anyway"
        )
        own = CampaignConfig.model_validate(
            dict(cells.by_dataset[task_spec.inner_dataset].campaign_config)
        )
        ran = select_optimizer(
            inner_instrument_config(task_spec, own, llm_node="llm_only", n_scored=40).optimization
        )
        assert cells.optimizer.treatment() == ran.treatment(), (
            "the manifest the outer mutates against is not the one this cell runs"
        )
        assert cells.optimizer.knobs("escalation") == ran.knobs("escalation")
        assert {n.name for n in graph.declared_nodes if n.tunes_llm} == set(ran.llm_nodes)

    def cyc(
        levels: list[float],
        *,
        origin: float | None = 0.30,
        rounds: list[RoundResult] | None = None,
        **kw: Any,
    ) -> CycleResult:
        ran = [round_result(i + 1) for i in range(len(levels))] if rounds is None else rounds
        return cycle_result(levels, origin, ran, **kw)

    def delta(levels: list[float], **kw: Any) -> float:
        return compute_outer_proxies(cyc(levels, **kw)).mean_round_delta

    # The parent levels held forward to the round budget, averaged, less the origin's 0.30.
    for levels, budget, mean, why in (
        ([0.40, 0.55], None, 0.175, "the mean read the endpoint (0.25)"),
        ([0.30], None, 0.0, "a flat cycle read a lift"),
        ([0.90, 0.35], None, 0.325, "WHEN the lift lands is part of the score"),
        ([0.05, 0.35], None, -0.10, "WHEN the lift lands is part of the score"),
        ([0.30, 0.60, 0.60], 4, 0.225, "quitting early was paid over the rounds that ran"),
        ([0.30, 0.60, 0.60, 0.60], 4, 0.225, "the denominator is not the round budget"),
        ([0.30, 0.60, 0.60], None, 0.20, "an undeclared budget is the series length"),
    ):
        kw = {} if budget is None else {"round_budget": budget}
        assert delta(levels, **kw) == pytest.approx(mean), why
    # `parent_level_series` pads a short series with its last value; padding carries no precision.
    se_kw: dict[str, Any] = {"origin_level_se": 0.20, "round_level_ses": [0.30, 0.40]}
    padded = mean_parent_level_se(cyc([0.30, 0.60], round_budget=4, **se_kw))
    assert padded == pytest.approx(mean_parent_level_se(cyc([0.30, 0.60], round_budget=2, **se_kw)))
    assert padded == pytest.approx(0.35), "nested frontier fits are correlated: never sigma/sqrt(n)"
    no_origin_se = cyc([0.30, 0.60], round_budget=4, round_level_ses=[0.30, 0.40])
    assert mean_parent_level_se(no_origin_se) == pytest.approx(0.35), (
        "the origin's SE cancels inside `variant - origin` and stays out"
    )
    assert mean_parent_level_se(cyc([0.30])) is None, "a fabricated 0.0 SE reads infinitely sharp"

    # EXCLUDED, never floored: the -1 floor would punish the optimizer prompt for a slow provider.
    tooling = [round_result(1, parse_failure="l1_provider_empty_response")]
    for unscoreable in (
        cyc([], stop_reason=StopReason.TARGET_HIT),
        cyc([0.40, 0.55], stop_reason=StopReason.TOKEN_BUDGET),
        cyc([0.40], rounds=tooling, stop_reason=StopReason.TOKEN_BUDGET),
        cyc([], rounds=[round_result(1)]),
        cyc([0.40, 0.55], origin=None),
    ):
        with pytest.raises(CellUnscoreableError):
            compute_outer_proxies(unscoreable)

    # Ending on its own terms, an all-tooling cycle is FLOORED: the loss reproduces.
    assert delta([0.40], rounds=tooling) == -1.0

    def lost(reason: str) -> RoundResult:
        arm = scored_candidate("x", invalid_reason=reason)
        return round_result(1, candidates_scored=0, candidate_scores=[arm])

    assert delta([0.40], rounds=[lost(PARSE_FAILURE_TOOLING)]) == -1.0, "empty replies floor"
    assert delta([0.40, 0.55], rounds=[lost(PARSE_FAILURE_MALFORMED), round_result(2)]) == (
        pytest.approx(0.175)
    ), "a malformed reply is the prompt's verdict and scores"
