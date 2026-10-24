"""A wrong θ — the scale every ability is read on.

Owns `application/intelligence/rasch.py` and `adaptive_queue_mechanism.py`,
`application/bench/difficulty.py`, `domain/ruler.py` and `domain/l4/proxies.py`. Pure estimator
arithmetic: each journey recovers a known answer from data built to have one.
"""

from __future__ import annotations

from contextvars import copy_context
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from factories import (
    PROMPT_INFO,
    arm_state,
    cycle_result,
    election,
    loop_session,
    measurement,
    pipeline_schema,
    prompted,
    round_result,
    seat,
    sheet,
    workspace,
)

from promptpotter.application.bench.difficulty import (
    DifficultyView,
    calibrate_delta_ruler,
    judging_ruler,
    session_ruler_scope,
)
from promptpotter.application.bench.node_context import NodeContext
from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.datasets.authored import load_dataset_campaign_config
from promptpotter.application.intelligence.adaptive_queue_mechanism import (
    covered_prefix,
    stratum_shares,
)
from promptpotter.application.intelligence.rasch import (
    Observation,
    RaschPosterior,
    build_round_order,
    fit_rasch,
    fit_theta_given_delta,
    graduate_ruler_model,
)
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.optimizers.nodes import ReadArm
from promptpotter.application.runner.bench import pair_on_bench
from promptpotter.application.runner.inner.tasks import inner_instrument_config, parse_panel
from promptpotter.application.scoring.paired import MemberRows
from promptpotter.config.paths import install_datasets_root
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.l4.proxies import (
    compute_outer_proxies,
    inner_cell_facts,
    mean_parent_level_se,
)
from promptpotter.domain.optimizer_state import PARSE_FAILURE_MALFORMED, PARSE_FAILURE_TOOLING
from promptpotter.domain.paired_reading import ArmKey, MemberAddress
from promptpotter.domain.phases import StopReason
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import PipelineNode
from promptpotter.domain.results import ArmSlot, CycleResult, RoundResult, origin_panel
from promptpotter.domain.ruler import (
    CellScopes,
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
from promptpotter.domain.scoring import GradedCell, Scorer
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.store.archive_queries import (
    ArmOf,
    CampaignWalks,
    CellOf,
    OpenAt,
    bind_memory_scope,
)
from promptpotter.infrastructure.store.dataset_access import read_dataset_definition
from promptpotter.infrastructure.store.ruler_store import RulerStore
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.shared.errors import CellUnscoreableError
from promptpotter.shared.identity import default_identity
from promptpotter.shared.measurement_context import RoleScope

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

    # θ given a fixed δ: subset-invariant, where raw accuracy inverts the true ability.
    rng = np.random.default_rng(7)
    ruler = {"1": -2.0, "2": -2.0, "3": -2.0, "4": 2.0, "5": 2.0, "6": 2.0}
    easy, hard = ["1", "2", "3"], ["4", "5", "6"]

    def measure(cid: str, theta: float, sids: list[str], n: int = 200) -> list[Observation]:
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
    assert fit_theta_given_delta([Observation("ghost", "99", True)], ruler) == {}
    off_scale = fit_theta_given_delta([*able, Observation("able", "99", False)], ruler)
    assert off_scale["able"] == fit_theta_given_delta(able, ruler)["able"], (
        "an off-ruler cell moved θ"
    )
    cold = fit_theta_given_delta([Observation("ghost", "99", True)], None)
    assert cold["ghost"][0] > 0.0, "a COLD ruler reads plain logit-accuracy: one hit is θ > 0"

    # θ_se is dispersion-corrected: a GRADED response varies less about its mean than Bernoulli.
    graded = [Observation("g", sid, 0.5 + 0.02 * i) for i, sid in enumerate(easy * 6)]
    spread = [Observation("b", sid, float(i % 2)) for i, sid in enumerate(easy * 6)]
    assert (
        fit_theta_given_delta(graded, ruler)["g"][1] < fit_theta_given_delta(spread, ruler)["b"][1]
    ), "a graded response was read as noisy as a coin flip"
    # Hits far below θ and misses far above, as a δ fit on these rows reads them: φ would be ≪ 1.
    separated = [Observation("bin", s, float(s in easy)) for s in [*easy, *hard] * 6]
    for binary in (measure("bin", 0.8, easy, n=60), separated):
        bin_theta, bin_se = fit_theta_given_delta(binary, ruler, sigma_theta=1.5)["bin"]
        p_hat = 1.0 / (1.0 + np.exp(-(bin_theta - np.array([ruler[o.key] for o in binary]))))
        bern_se = 1.0 / np.sqrt(float(np.sum(p_hat * (1 - p_hat))) + 1 / 1.5**2)
        assert abs(bin_se - bern_se) < 0.15 * bern_se, "φ moved the SE of a dichotomous dataset"
    flat = [Observation("f", sid, 0.5) for sid in easy * 6]
    assert fit_theta_given_delta(flat, ruler)["f"][1] > 0.1, (
        "no residual spread read as infinite confidence: every gate would fire"
    )

    # A 1PL entry and a 2PL entry at a = 1 are one ruler.
    seam_obs = [Observation("c1", str(s), float(s % 2 == 0)) for s in range(8)] + [
        Observation("c2", str(s), float(s % 3 == 0)) for s in range(8)
    ]
    bare = {str(s): 0.2 * s - 1.0 for s in range(8)}
    on_1pl = fit_theta_given_delta(seam_obs, bare)
    on_unit_a = fit_theta_given_delta(seam_obs, {s: (d, 1.0) for s, d in bare.items()})
    assert all(abs(on_1pl[c][0] - on_unit_a[c][0]) < 1e-9 for c in on_1pl), (
        "every θ shifts at 2PL graduation"
    )

    # Every δ sits five logits from the θ=0 seed, where an undamped `grad / info` step limit-cycles.
    far = dict.fromkeys(map(str, range(20)), 5.55)
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


def test_a_round_draws_orders_and_reads_its_cells_on_one_delta_ruler(tmp_path: Path) -> None:
    # Calibrated: eight samples clear any sample floor, so the arm count is the binding condition.
    arm_a = [Observation("a", str(sid), 1.0 if sid % 3 else 0.0) for sid in range(8)]
    arm_b = [Observation("b", str(sid), 1.0 if sid < 5 else 0.0) for sid in range(8)]
    flat = calibrate_delta_ruler(arm_a, 4, enable_2pl=False)
    assert flat is None, "one arm warmed the ruler: its δ would be read off the arm under test"
    warm = calibrate_delta_ruler(arm_a + arm_b, 4, enable_2pl=False)
    assert warm is not None and warm.calibration_model == "1PL", "a second arm left δ flat"

    # One tenant ruler, versioned by its folds. C0 hits a quarter of the bank; its child changed nothing.
    pool40 = [Sample(id=i, query=f"r{i}", ground_truth="t") for i in range(40)]
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

    # Drawn blind: the parent hits the easiest 16 of 40 cells on a graded ruler, and each round's
    # cells must still be the pool's, or the election reads rows the held-out bench never would.
    graded_pool = [Sample(id=i, query=f"d{i}", ground_truth="t") for i in range(40)]

    def hits_easiest(session: Any) -> ReadArm:
        origin = prompted(instruction="Answer.")
        rows = sheet(
            (measurement(s.id, float(s.id < 16), sample_key=s.key) for s in graded_pool),
            session.state.scoring.require_scorer().id,
        )
        c0 = arm_state("C0", slot=ArmSlot(round=0, idx=0, total=1), individual=origin)
        return ReadArm(ArmKey(origin.id), c0, rows)

    drawing = seat(
        workspace(tmp_path / "draw"),
        bank=graded_pool,
        origin=hits_easiest,
        framing=TaskDecomposition(pipeline_purpose="Answer each query."),
    )
    session = drawing.cycle.session
    session.store.ruler.fold(
        session_ruler_scope(session),
        [(f"f{arm}", s.key, 1.0 - (s.id + 0.5) / 40) for arm in range(8) for s in graded_pool],
        campaign="else",
        own=None,
    )
    drawing.cycle.difficulty.open_round(drawing.cycle.rounds)
    assert drawing.cycle.difficulty.ruler is not None, "guard: the draw under test is the warm one"
    sampler = drawing.cycle.optimizer.plan.sampler
    search = list(session.state.scoring.require_partition().search)

    def drawn_on(round_num: int) -> list[int]:
        at = replace(drawing, round_num=round_num)
        return [int(s.id) for s in sampler.draw(NodeContext(at, sampler), search).cells]

    rounds_drawn = {n: drawn_on(n) for n in range(1, 11)}
    assert drawn_on(3) == rounds_drawn[3], "a resumed round drew other cells"
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

    # Covered: a shuffle led by the `easy` cells on the `small` model still reaches every level.
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

    # Ordered: win opportunities first, a parent-hit probe every fourth slot.
    ids = list(range(24))
    cells = {sid: f"k{sid}" for sid in ids}
    parent_grades = {f"k{sid}": 1.0 if sid < 9 else 0.0 for sid in range(23)}
    ordering = _anchored({f"k{sid}": float(sid % 7) for sid in range(20)}, mu=2.85)
    on_slot = {sid: ordering.delta[key] for sid, key in cells.items() if key in ordering.delta}
    order = build_round_order(parent_grades, ordering, cells, n_min=0)
    assert sorted(order) == ids
    assert build_round_order(parent_grades, ordering, cells, n_min=0) == order, (
        "the order is not reproducible"
    )
    hit_ids = set(range(9))
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
    # Led: n_min cells θ is read on come first, so PoBB can cut at n_min, the probe kept.
    led = build_round_order(parent_grades, ordering, cells, n_min=6)
    assert all(cells[sid] in ordering.delta for sid in led[:6]), (
        "an off-ruler cell was walked before n_min θ-readable ones: no arm can be cut that early"
    )
    assert order.index(20) < 6 <= led.index(20), "guard: the strata alone walk off the ruler early"
    assert led[3] in hit_ids, "the lead dropped the regression probe"
    unknown = build_round_order({}, ordering, cells, n_min=0)
    assert sorted(unknown) == ids
    spread = [abs(on_slot.get(sid, centre) - centre) for sid in unknown]
    assert spread == sorted(spread), (
        "an all-unknown panel must open on its most discriminating cells"
    )
    easiest = min(on_slot, key=lambda sid: (on_slot[sid], sid))
    assert unknown.index(easiest) >= 6, "the panel's easiest cell must not lead the round"

    # Read: a child on cells the judging scale does not carry stays off it until its round folds.
    fitted = _anchored({"1": 1.5, "2": -1.25, "3": 0.0})
    child = [Observation("child", sid, float(sid == "7")) for sid in ("7", "8", "9")]
    assert fit_theta_given_delta(child, fitted.entries()) == {}, (
        "a child read on unlinked cells carried a θ"
    )
    tied = _anchored({"1": 0.0, "2": 0.0, "3": 3.0})
    for scale, read_on, expected in (
        (fitted, ["1", "2", "3"], None),
        (fitted, ["1", "2", "3", "7"], ThetaCaveat.UNMEASURED_DELTA),
        (tied, ["1", "2", "3"], ThetaCaveat.PRIOR_PINNED),
        (None, ["1", "2"], ThetaCaveat.COLD_RULER),
    ):
        served = theta_caveat(scale, read_on, scopes=1, all_miss=False)
        assert served is expected, "the pin cancels inside a lift, a skipped cell does not"
    # The id names the δ, never key order; a cold cycle's names its OBJECTIVE.
    assert fitted.anchor_id == anchor_id_of({"3": 0.0, "2": -1.25, "1": 1.5}, 0.0, 2.0, "1PL")
    assert fitted.anchor_id != anchor_id_of({"1": 1.5, "2": -1.2500001, "3": 0.0}, 0.0, 2.0, "1PL")
    assert flat_ruler_id("acc") != flat_ruler_id("acc_minus_latency")

    # Two datasets: two seats on `a`, two on `b`, two δ scales that never shared a cell.
    seated = [
        Sample(id=i, query=f"q{i}", ground_truth=None, measured_dataset=ds)
        for i, ds in enumerate(("a", "a", "b", "b"))
    ]
    key = {s.id: s.key for s in seated}
    scopes = CellScopes.of(seated, "outer")
    two = _anchored({key[0]: -2.0, key[1]: 0.0, key[2]: 0.5, key[3]: 2.0})

    def caveat(ids: list[int]) -> ThetaCaveat | None:
        cells = [key[i] for i in ids]
        return theta_caveat(two, cells, scopes=scopes.spanned(cells), all_miss=False)

    assert caveat([0, 1]) is None and caveat([0, 2]) is ThetaCaveat.DISJOINT_SCOPES

    def rows(hits: list[float]) -> list[GradedCell]:
        return [measurement(i, h, sample_key=key[i]) for i, h in enumerate(hits)]

    c0_rows, pick = rows([0.0, 1.0, 0.0, 0.0]), rows([1.0, 1.0, 1.0, 0.0])
    # C0 sat `a` alone; the line drew `b`, so C0 is topped up there rather than paired on `a` only.
    topped = origin_panel(
        [0, 1],
        line_cells=[0, 1, 2, 3],
        poolable=range(4),
        scope_of={s.id: scopes.by_key[s.key] for s in seated},
        size=2,
    )
    assert topped == [0, 1, 2, 3]

    def member(individual: str, cells: list[GradedCell]) -> MemberRows:
        return MemberRows(
            address=MemberAddress(
                path=(CycleHop(campaign_id="c", cycle_id="y"),),
                individual_id=individual,
                arm=None,
                pass_role=None,
            ),
            sheet=sheet(cells),
            bought=0,
            cut=False,
            scope=RoleScope.BENCH,
            instrument_id="i",
            dataset_hash=None,
            cell_set_id=None,
        )

    paired = pair_on_bench(
        member("c0", c0_rows), member("c1", pick), sample_keys=list(key.values()), instrument_id="i"
    )
    lift = paired.lift("fitness")
    assert lift is not None and lift.estimate.value == pytest.approx(0.5), (
        "the lift lost its pairing"
    )

    # Stratified: a stratum short of its share hands the rest on; an empty one takes nothing.
    assert stratum_shares([2, 10, 10], 9) == [2, 4, 3]
    assert stratum_shares([0, 5], 4) == [0, 4]
    assert stratum_shares([1, 1], 5) == [1, 1]
    assert stratum_shares([30], 20) == [20]

    # Traced: every round's ended-on individual re-read on ONE scale, whatever its election read.
    bank = [Sample(id=i, query=f"q{i}", ground_truth="t") for i in range(12)]
    scorer = Scorer(id="acc", per_cell=None, matcher=None, fitness=lambda _row: 0.0, objective=None)

    solve = pipeline_schema([PipelineNode(name="solve", tunes_llm=False)], name="ds")

    def series_of(
        memory: Any, stores: Stores, lines: list[Any], drawn: list[Sample] = bank
    ) -> list[Any]:
        session = loop_session(stores, solve, drawn, scorer=scorer)
        opened = judging_ruler(
            stores.ruler,
            scope,
            RulerReach(version=0, own=None),
            (),
            objective_id="acc",
            n_min=4,
            enable_2pl=False,
        )
        view = DifficultyView(session, 4, False, frozenset(), opened)

        def read() -> list[Any]:
            bind_memory_scope(memory)
            return view.level_series(lines)

        return copy_context().run(read)

    def graded(hits: list[float]) -> Any:
        return sheet(
            measurement(s.id, h, objective=h, sample_key=s.key)
            for s, h in zip(bank, hits, strict=True)
        )

    def closed(n: int, walked: dict[str, list[float]], pick: str | None, ended_on: str) -> Any:
        # Each election reads its own version; the series must read none of them.
        stamp = RulerStamp(id=f"v{n}", version=n, own=None, scope=scope, excluded=())
        return round_result(
            n,
            arms={ArmKey(k): arm_state(k) for k in walked},
            cells={ArmKey(k): graded(h) for k, h in walked.items()},
            election=election(pick, ended_on=ended_on, ruler=stamp),
        )

    hits = {
        "o": [float(i % 4 == 0) for i in range(12)],
        "a": [float(i % 2 == 0) for i in range(12)],
        "b": [float(i % 6 == 0) for i in range(12)],
    }
    # Graded, never binary: tied δ would pin most cells to the prior.
    warmed = workspace(tmp_path / "series")
    tenant = warmed.ruler
    tenant.fold(
        scope,
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
        tenant.fold(
            scope,
            [(k, c.key, c.grade.objective) for k, sh in rr.cells.items() for c in sh],
            campaign="run",
            own=None,
        )
    run += [
        closed(2, {"b": hits["b"], "a": hits["a"]}, None, "a"),
        round_result(3, election=None, arms={}, cells={}),
    ]
    [series] = series_of(OpenAt(), warmed, [run])
    levels = {rnd: series.level(rnd) for rnd in series.rounds}
    assert series.stamp.version == tenant.version() and series.stamp.excluded == ("a", "o"), (
        "the series read a scale its own parent line moved, or not the run's clock"
    )
    assert all(lv is not None and lv.ruler == series.stamp for lv in levels.values()), (
        "a round's level sat on the scale its own election read, not the series'"
    )
    assert levels[1] is not None and levels[0] is not None and levels[1].theta > levels[0].theta
    assert levels[1] == levels[2] == levels[3], (
        "a held round, or one that elected nothing, read another individual than the parent "
        "standing into it"
    )
    assert series.caveat is None and series.plateau() == pytest.approx(levels[3].theta), (
        "three rounds of one standing parent on one scale is the plateau"
    )
    # A fork that picked `b`: both lines on one fit less both lines' parents, or UCB sums two scales.
    fork = [run[0], closed(1, {"a": hits["a"], "b": hits["b"]}, "b", "b")]
    line, forked = series_of(OpenAt(), warmed, [run, fork])
    assert line.stamp == forked.stamp and line.stamp.excluded == ("a", "b", "o"), (
        "two lines of one family read their levels on two scales"
    )
    pin = RulerReach(version=frozen, own=None)
    [pinned] = series_of(CellOf(walks=None, ruler=pin), warmed, [run])  # type: ignore[arg-type]
    assert pinned.stamp.version == frozen and pinned.stamp.excluded == (), (
        "an inner cell's series read past the version its outer round opened on"
    )
    [own, other] = (
        series_of(ArmOf(CampaignWalks(None, c), frozen), warmed, [run])[0]  # type: ignore[arg-type]
        for c in ("run", "x")
    )
    assert own.stamp.excluded == ("a", "o") and other.stamp.excluded == (), (
        "an arm read another arm's lines past the freeze, or never its own"
    )
    lone = workspace(tmp_path / "lone")
    lone.ruler.fold(
        scope, [("f0", s.key, (s.id + 1) / 16) for s in bank], campaign="else", own=None
    )
    [cold] = series_of(OpenAt(), lone, [run])
    assert cold.stamp.cold and cold.caveat is ThetaCaveat.COLD_RULER and cold.plateau() is None, (
        "a series on no δ scale read as one that differences"
    )
    # Cold AND across two datasets: the caveat names the scopes, and still no level differences.
    split = [s.model_copy(update={"measured_dataset": "other"}) if s.id % 2 else s for s in bank]
    [both] = series_of(OpenAt(), lone, [run], split)
    assert both.stamp.cold and both.caveat is ThetaCaveat.DISJOINT_SCOPES, (
        "the series lost the caveat that outranks a cold scale"
    )
    assert all(
        both.level(r) is None and (read := both.reading(r)) is not None and read.caveat is not None
        for r in both.rounds
    ), "a level its series' caveat withholds read as one two rounds may difference"
    # Each line is caveated on its OWN cells: a line spanning two scopes withholds no other line.
    evens = [
        round_result(
            n,
            arms={ArmKey(k): arm_state(k)},
            cells={
                ArmKey(k): sheet(
                    measurement(s.id, h, objective=h, sample_key=s.key)
                    for s, h in zip(bank, hits[k], strict=True)
                    if s.id % 2 == 0
                )
            },
            election=election(
                k,
                ended_on=k,
                ruler=RulerStamp(id=f"v{n}", version=n, own=None, scope=scope, excluded=()),
            ),
        )
        for n, k in ((0, "o"), (1, "a"))
    ]
    mixed, plain = series_of(OpenAt(), warmed, [run, evens], split)
    assert mixed.caveat is not None and plain.caveat is None, (
        "one line's caveat withheld the levels of a line whose own cells carry none"
    )
    two_scopes = cycle_result([0.40, 0.55], 0.30, [round_result(1), round_result(2)])
    withheld = inner_cell_facts(two_scopes.model_copy(update={"level_series": both}))
    assert both.plateau() is None and withheld.mean_round_delta is None, (
        "a reader asked the stamp, not the series' caveat, and differenced a cold series"
    )
    assert withheld.series_reason is ThetaCaveat.DISJOINT_SCOPES, (
        "the withheld θ lift lost the caveat that outranks a cold scale"
    )


# 3. The L4 outer proxy — what one finished inner cycle says


def test_one_finished_inner_cycle_scores_the_optimizer_prompt_that_ran_it(tmp_path: Path) -> None:
    # The shipped panel measures in ONE unit: no inner cell may graduate its ruler to 2PL.
    d = install_datasets_root() / "promptpotter-self" / "panel"
    definition = read_dataset_definition(d)
    assert definition is not None and definition.panel is not None
    declared = definition.panel
    panel = parse_panel(declared)
    assert panel.tasks
    # The SHIPPED config, not a hand-built one — the question is what the panel runs under.
    base = load_dataset_campaign_config(d)
    cells = panel.resolve_cells(build_stores(default_identity(), projects_root=tmp_path))
    graph = parse_pipeline_response(cells.pipeline())
    for task in panel.tasks:
        task_spec = cells.spec(task)
        # The INNER backend's prompt nodes: what the recursion's cell pins and narrows.
        solver = [PipelineNode(name="llm_only", tunes_llm=True, prompt_info=PROMPT_INFO)]
        derived = inner_instrument_config(task_spec, base, solver=solver, n_search=40)
        assert derived.optimization.enable_2pl_graduation is False, (
            f"cell {task.id} may graduate its ruler to 2PL — its theta would then be in units of "
            "1/a while the rest of the panel is in logits, and the outer verdict pools them anyway"
        )
        assert [derived.optimizer_narrowing[n.name].param_keys for n in solver] == [
            list(n.prompt_fields) for n in solver
        ], "the solver's spend axes are the instrument: the inner search writes prompt text alone"
        assert derived.bench_trigger == "each_round" and derived.dataset_split is not None
        own = CampaignConfig.model_validate(
            dict(cells.by_dataset[task_spec.inner_dataset].campaign_config)
        )
        ran = select_optimizer(
            inner_instrument_config(task_spec, own, solver=solver, n_search=40).optimization
        )
        shown = cells.held(task).optimizer
        assert shown.treatment() == ran.treatment(), (
            "the manifest the outer mutates a cell against is not the one that cell runs"
        )
        assert shown.knobs("escalation") == ran.knobs("escalation")
        assert {n.name for n in graph.declared_nodes if n.tunes_llm} == set(ran.llm_nodes)

    def cyc(
        levels: list[float | None],
        *,
        origin: float | None = 0.30,
        rounds: list[RoundResult] | None = None,
        **kw: Any,
    ) -> CycleResult:
        ran = [round_result(i + 1) for i in range(len(levels))] if rounds is None else rounds
        return cycle_result(levels, origin, ran, **kw)

    def delta(levels: list[float | None], **kw: Any) -> float | None:
        return inner_cell_facts(cyc(levels, **kw)).mean_round_delta

    def lift(levels: list[float | None], bench: list[float | None], **kw: Any) -> float:
        px = compute_outer_proxies(cyc(levels, bench_origin=0.40, bench_levels=bench, **kw))
        return px.mean_bench_lift

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
    # `parent_level_series` pads a short series with its last value; padding carries no precision.
    se_kw: dict[str, Any] = {"origin_se": 0.90, "ses": [0.30, 0.40]}
    padded = mean_parent_level_se(cyc([0.30, 0.60], round_budget=4, **se_kw))
    assert padded == pytest.approx(mean_parent_level_se(cyc([0.30, 0.60], round_budget=2, **se_kw)))
    assert padded == pytest.approx(0.35), (
        "nested frontier fits are correlated: never sigma/sqrt(n); and the origin's SE cancels "
        "inside `variant - origin` and stays out"
    )
    for unsharp in (cyc([0.30]), cyc([0.30, 0.60], ses=[0.30, None])):
        assert mean_parent_level_se(unsharp) is None, "a fabricated 0.0 SE reads infinitely sharp"

    # The scored term is the held-out lift, the bench levels held forward the same way less the
    # bench origin's 0.40: churn on the search split's θ scores ZERO.
    churned = cyc([0.55, 0.55], bench_origin=0.40, bench_levels=[0.40, 0.40])
    churn = (inner_cell_facts(churned).mean_round_delta, compute_outer_proxies(churned))
    assert (churn[0], churn[1].mean_bench_lift) == pytest.approx((0.25, 0.0))
    assert lift([0.55, 0.55], [0.50, 0.50], round_budget=4) == pytest.approx(0.10)
    assert lift([0.30, 0.55], [0.40, 0.50], round_budget=4) == pytest.approx(0.075)

    # EXCLUDED, never floored: the -1 floor would punish the optimizer prompt for a slow provider.
    tooling = [round_result(1, parse_failure="l1_provider_empty_response")]
    for unscoreable in (
        cyc([], stop_reason=StopReason.TARGET_HIT),
        cyc([0.40, 0.55], stop_reason=StopReason.TOKEN_BUDGET),
        cyc([0.40], rounds=tooling, stop_reason=StopReason.TOKEN_BUDGET),
        cyc([0.55, 0.55], bench_origin=0.40, bench_levels=[0.50, None]),
        cyc([0.55], bench_origin=None),
    ):
        with pytest.raises(CellUnscoreableError):
            compute_outer_proxies(unscoreable)
    # A series that differences nothing is a SERVED fact beside the scored term, never an exclusion.
    for unread in (
        cyc([0.40, 0.55], caveat=ThetaCaveat.COLD_RULER),
        cyc([0.40, None]),
        cyc([0.40, 0.55], origin=None),
    ):
        facts = inner_cell_facts(unread)
        assert compute_outer_proxies(unread).mean_bench_lift == pytest.approx(0.0), (
            "a cold or holed search series excluded a cell its held-out lift scores"
        )
        assert facts.mean_round_delta is None and facts.series_reason, (
            "a θ lift read off a series that differences nothing, or withheld without its reason"
        )

    # Ending on its own terms, an all-tooling cycle is FLOORED: the loss reproduces.
    floored = compute_outer_proxies(cyc([0.40], rounds=tooling))
    assert floored.mean_bench_lift == -1.0

    def lost(reason: str) -> RoundResult:
        arm = arm_state("x", slot=ArmSlot(round=1, idx=0, total=1), invalid_reason=reason)
        return round_result(1, candidates_scored=0, arms={ArmKey("x"): arm})

    tooled = cyc([0.40], rounds=[lost(PARSE_FAILURE_TOOLING)])
    assert compute_outer_proxies(tooled).mean_bench_lift == -1.0, "empty replies floor"
    assert delta([0.40, 0.55], rounds=[lost(PARSE_FAILURE_MALFORMED), round_result(2)]) == (
        pytest.approx(0.175)
    ), "a malformed reply is the prompt's verdict and scores"
