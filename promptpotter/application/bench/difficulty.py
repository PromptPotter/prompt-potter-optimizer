"""The δ ruler as the bench serves it: a view over archive facts under an explicit scope, which any
selector may read as a declared input — potter's θ election reads it like any other."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from promptpotter.application.intelligence.exploration import (
    ORIGIN_ABILITY_ID,
    Observation,
    dedup_observations,
    extend_ruler,
    fit_theta_given_delta,
    graded_response,
    graduate_ruler_model,
    observations_from_results,
)
from promptpotter.application.intelligence.hard_sample_archive import build_archive_observations
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.results import RoundResult, measured_cells, merge_known_outcomes
from promptpotter.domain.ruler import (
    AbilityReading,
    DeltaRuler,
    flat_ruler_id,
    is_flat_ruler_id,
    theta_caveat,
)
from promptpotter.domain.scoring import is_graded
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.shared.errors import RulerUnpersistedError
from promptpotter.shared.instrument import instrument_mode

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session

logger = logging.getLogger(__name__)

__all__ = ["DifficultyView", "RulerScope"]

# Which archive rows a fit reads — `dataset`: every campaign's on the cycle's dataset
# (`docs/architecture.md` § Three data scopes).
RulerScope = Literal["dataset"]


def _reading(
    theta: tuple[float, float] | None,
    ruler: DeltaRuler | None,
    *,
    objective_id: str,
    results: Sequence[Mapping[str, Any]],
) -> AbilityReading | None:
    """The SOLE stamping site: a θ pair, the scale it was read on, and whether that scale makes it
    ability at all — minted together, so no round can carry an ability whose scale disagrees with
    the ruler that produced it, or a caveat that disagrees with either.

    ``objective_id`` is read only on the cold arm, where θ is plain logit-accuracy and the
    objective is the whole of what separates two readings. ``results`` are the rows THIS θ was fit
    on; their cells decide the round's own δ span, which is half of the collapsed-band reading."""
    if theta is None:
        return None
    cells = list(measured_cells(results))
    band = ruler.band_span(cells) if ruler is not None else None
    round_span = band[0] if band is not None else None
    ruler_span = ruler.delta_span if ruler is not None else None
    calibration = ruler.calibration_model if ruler is not None else None
    pinned = ruler.pinned_share(cells) if ruler is not None else None
    return AbilityReading(
        theta=theta[0],
        se=theta[1],
        ruler_id=ruler.anchor_id if ruler is not None else flat_ruler_id(objective_id),
        ruler_n=len(ruler.delta) if ruler is not None else 0,
        ruler_span=ruler_span,
        round_span=round_span,
        calibration_model=calibration,
        caveat=theta_caveat(
            calibration_model=calibration,
            round_span=round_span,
            ruler_span=ruler_span,
            pinned_share=pinned,
        ),
    )


def _calibrate_delta_ruler(
    origin_results: list[dict[str, Any]] | None,
    n_min: int,
    *,
    enable_2pl: bool,
    archive_obs: list[Observation],
) -> tuple[DeltaRuler | None, tuple[float, float] | None]:
    """The ANCHORING fit — the scale every later θ readout is measured against
    (``docs/methods/verdict-resolution.md``). It locks the anchor; ``extend_ruler`` grows the
    membership afterwards without moving it. Cold start returns ``None``, which reads FLAT."""

    origin_obs = [
        Observation(ORIGIN_ABILITY_ID, int(sid), graded_response(r))
        for r in origin_results or []
        if (sid := r.get("sample_id")) is not None and is_graded(r)
    ]
    # ``origin_obs`` wins the cells both hold: an inner cycle that cache-replays its origin
    # banked that run INSIDE its own evidence epoch, where the archive read cannot see it.
    obs = dedup_observations(archive_obs, origin_obs)
    if not obs:
        return None, None
    # Two warmth conditions, both knowable without fitting — below either the ruler stays flat
    # and the fit would be discarded unread, so skip the 1PL + 2PL + CV storm entirely.
    # DISTINCT SAMPLES ≥ n_min: both fits key δ on ``sorted({o.sample_id})``.
    # DISTINCT ARMS ≥ 2: δ is identified only against a second ability. With one arm the anchor
    # pins θ and δ collapses to that arm's own hit pattern in two values, so every later θ in
    # the cycle restates whether round 0 happened to get the sample right, on a scale where the
    # origin sits at 0.000 by construction. That is exactly what a fresh campaign hands this
    # function, since 40 origin rows from one candidate clear any sample floor alone. One arm
    # therefore stays FLAT and re-attempts next round, once the round's own candidates are
    # banked grade-A and the fit has arms to compare.
    ruler: DeltaRuler | None = None
    if len({o.sample_id for o in obs}) >= n_min and len({o.candidate_id for o in obs}) >= 2:
        fitted, post = graduate_ruler_model(obs, enable=enable_2pl)
        # Cold below the floor: too few banked samples to trust a fitted ruler → stay flat.
        if len(post.delta) >= n_min:
            ruler = post.anchored(fitted)
            if fitted == "2PL":
                logger.info("δ ruler graduated to 2PL (%d samples fit)", len(post.delta))
    # θ_C0 THROUGH THE SAME ESTIMATOR EVERY OTHER LEVEL USES. Never hand back the JOINT fit's
    # ``post.theta[ORIGIN_ABILITY_ID]``: ``fit_rasch`` re-anchors ``mean(θ)==0`` per call, so
    # its scale is set by whichever arms were in the pool and the L4 law then differences two
    # estimators — a BIAS channel, which does not average out over a panel.
    # ``obs``, not ``origin_obs``: the deduped set carries the archive's origin rows too.
    origin_obs_all = [o for o in obs if o.candidate_id == ORIGIN_ABILITY_ID]
    entries = ruler.entries() if ruler is not None else None
    anchor = ruler.anchor_id if ruler is not None else ""
    theta = fit_theta_given_delta(origin_obs_all, entries, anchor_id=anchor)
    return ruler, theta.get(ORIGIN_ABILITY_ID)


def _given_ruler(session: Session) -> DeltaRuler | None:
    """The scale something outside this cycle already fixed, never re-derived: its OWN ledger
    (resume — a re-fit walks an archive grown since the lock), then the INSTRUMENT (L4 — the
    spawner's pooled fit, so every arm on a cell shares one δ and the origin cancels)."""
    if session.state.cycle_id:
        # A δ key names a sample only within one dataset, so an unnamed cycle has no scale to
        # read back — and still owes the refusal, which is what its rounds on disk answer.
        own = (
            session.store.campaigns.read_ruler(session.hop, dataset_name=session.dataset_name)
            if session.dataset_name
            else None
        )
        if own is not None:
            return own
        _refuse_unreproducible_rounds(session)
    mode = instrument_mode()
    return mode.ruler if mode is not None else None


def _refuse_unreproducible_rounds(session: Session) -> None:
    """Nothing on the ledger, but the rounds on disk say a warm ruler read them.

    Warmth is monotone within a cycle, so the LAST round document answers this in one read — and
    a fresh mint has no round files at all, which is the silent path. Falling through instead is
    what the fix removes: ``_calibrate_delta_ruler`` would walk an archive that has grown since the lock and
    hand back a different scale under the same cycle."""

    cycle_dir = session.store.campaigns.cycle_dir(session.hop)
    rounds = CycleLayout(CycleDir(cycle_dir)).round_files()
    if not rounds:
        return
    doc = read_json_tolerant(rounds[-1], {})
    ability = (doc or {}).get("ability")
    stamped = str(ability.get("ruler_id") or "") if isinstance(ability, dict) else ""
    if not stamped or is_flat_ruler_id(stamped):
        return
    raise RulerUnpersistedError(
        stamped, campaign_id=session.hop.campaign_id, cycle_id=session.hop.cycle_id
    )


def _origin_theta_on(
    origin_results: list[dict[str, Any]] | None,
    ruler: DeltaRuler,
    archive_obs: list[Observation],
) -> tuple[float, float] | None:
    """C0's ability on an ALREADY-anchored ruler. Restricted to the cells that ruler carries: the
    archive has grown since the lock, and reading θ over rows the scale never absorbed is the very
    thing this arc removes."""

    origin_obs = observations_from_results({ORIGIN_ABILITY_ID: list(origin_results or [])})
    obs = [
        o
        for o in dedup_observations(archive_obs, origin_obs)
        if o.candidate_id == ORIGIN_ABILITY_ID and o.sample_id in ruler.delta
    ]
    fit = fit_theta_given_delta(obs, ruler.entries(), anchor_id=ruler.anchor_id)
    return fit.get(ORIGIN_ABILITY_ID)


_FRONTIER_ABILITY_ID = "_frontier"


def _cumulative_theta(
    results: list[dict[str, Any]], ruler: DeltaRuler | None
) -> tuple[float, float] | None:
    """The θ-space peer of the cumulative composite: one virtual candidate (the frontier) fit
    against the fixed δ, so rounds land on one scale once per-round subsets drift."""

    obs = observations_from_results({_FRONTIER_ABILITY_ID: results})
    entries = ruler.entries() if ruler is not None else None
    anchor = ruler.anchor_id if ruler is not None else ""
    return fit_theta_given_delta(obs, entries, anchor_id=anchor).get(_FRONTIER_ABILITY_ID)


@dataclass
class DifficultyView:
    """One cycle's δ scale over the archive rows ``scope`` admits. ``ruler`` is ANCHORED on the
    first warm fit and grown by :meth:`calibrate` after every round, so it always covers the cells
    its θ are read on while the anchor stays put; ``None`` = still cold, θ == logit-accuracy."""

    session: Session
    n_min: int
    enable_2pl: bool
    scope: RulerScope = "dataset"
    # The δ fit renames the archive candidate carrying it to ``ORIGIN_ABILITY_ID``, so the origin
    # is ONE candidate with one θ rather than one per round subset it was re-scored against.
    origin_sp_hash: str = ""
    # The archive under ``scope`` as it stood when the cycle opened — the rows the ruler anchors on,
    # read by whoever wants cross-cycle evidence beside the cycle's own.
    observations: list[Observation] = field(default_factory=list)
    ruler: DeltaRuler | None = None

    @classmethod
    def open(
        cls,
        session: Session,
        config: CampaignConfig,
        *,
        origin_sp_hash: str,
        origin_results: list[dict[str, Any]],
        scope: RulerScope = "dataset",
    ) -> tuple[DifficultyView, tuple[float, float] | None]:
        """The view a cycle opens on, and C0's θ on it."""
        view = cls(
            session=session,
            n_min=config.optimization.elimination_n_min,
            enable_2pl=config.optimization.enable_2pl_graduation,
            scope=scope,
            origin_sp_hash=origin_sp_hash,
        )
        view.observations = view.archive()
        given = _given_ruler(session)
        if given is not None:
            view.ruler = given
            return view, _origin_theta_on(origin_results, given, view.observations)
        view.ruler, origin_theta = _calibrate_delta_ruler(
            origin_results, view.n_min, enable_2pl=view.enable_2pl, archive_obs=view.observations
        )
        return view, origin_theta

    def archive(self) -> list[Observation]:
        """The archive under ``scope``, read now."""
        session = self.session
        match self.scope:
            case "dataset":
                return build_archive_observations(
                    session.store,
                    dataset_name=session.dataset_name,
                    scorer=session.scoring.require_scorer(),
                    scorer_id=session.scoring.scorer_id,
                    sample_ids=session.scoring.require_partition().admitted_ids,
                    origin_sp_hash=self.origin_sp_hash,
                )

    @property
    def scale_id(self) -> str:
        """The ``ruler_id`` a reading taken on this view now carries."""
        if self.ruler is not None:
            return self.ruler.anchor_id
        return flat_ruler_id(self.session.scoring.scorer_id)

    @property
    def cells(self) -> set[int]:
        return set(self.ruler.delta) if self.ruler is not None else set()

    def reading(
        self, theta: tuple[float, float] | None, *, results: Sequence[Mapping[str, Any]]
    ) -> AbilityReading | None:
        return _reading(
            theta, self.ruler, objective_id=self.session.scoring.scorer_id, results=results
        )

    def frontier(self, results: list[dict[str, Any]]) -> AbilityReading | None:
        """The frontier's reading on this scale. A caller reading ability before its round is
        absorbed computes what absorb will stamp, rather than a second one."""
        return self.reading(_cumulative_theta(results, self.ruler), results=results)

    def calibrate(
        self, measured: Mapping[str, Sequence[Mapping[str, Any]]], rounds: list[RoundResult]
    ) -> bool:
        """Cold: attempt the anchoring fit, LOCK, and re-read every θ ``rounds`` banked — True
        then. Warm: EXTEND onto every cell in ``measured``.

        POSTCONDITION on return: the ruler is ``None`` (still cold) or it carries every sample_id
        in ``measured``. That is the whole contract — ``fit_theta_given_delta`` raises on a hole
        rather than defaulting it to δ=0, so a gap surfaces as a crashed cycle instead of silently
        depressing every θ downstream. Called once per round, after every cell has a grade and
        before the election that reads them.
        """

        warmed = False
        if self.ruler is None:
            # The ≥2-arm floor is satisfied the moment the round's own candidates are banked, so
            # the attempt sits BEFORE the election that needs it rather than after the round closed.
            # This relaxes the TIMING, never the rule — a one-arm pool still stays flat.
            ruler, origin_theta = _calibrate_delta_ruler(
                rounds[0].results,
                self.n_min,
                enable_2pl=self.enable_2pl,
                archive_obs=self.archive(),
            )
            if ruler is None:
                return False  # still cold — legitimate, and it re-attempts next round
            self.ruler = ruler
            self._restamp(rounds, origin_theta)
            warmed = True

        obs = observations_from_results(measured)
        if obs:
            self.ruler = extend_ruler(self.ruler, obs)
        self.persist(round_num=max(len(rounds) - 1, 0))
        return warmed

    def _restamp(self, rounds: list[RoundResult], origin_theta: tuple[float, float] | None) -> None:
        """Every θ already taken on the flat ruler, re-read on the one just locked."""
        # Round 0 carries θ twice — its own frontier and, under a θ selector, C0's row — and a
        # warm fit must move both, or the round file reports the origin at two abilities.
        origin = rounds[0]
        reading = self.reading(origin_theta, results=origin.results)
        origin.ability = reading
        if origin.stamps_theta:
            origin.candidate_scores = [
                c.model_copy(
                    update={
                        "theta": reading.theta if reading is not None else None,
                        "theta_se": reading.se if reading is not None else None,
                    }
                )
                for c in origin.candidate_scores
            ]
        # …and every L1 round that already closed: a round that closed on a flat ruler had its θ
        # fit at δ≡0, a DIFFERENT scale, and unrestamped they sit side by side in
        # ``round_levels`` for the L4 law to average. The ROUND's frontier θ only —
        # ``l1_score`` stamps no candidate θ on a cold ruler, so none can contradict this.
        cells = self.cells
        frontier: list[dict[str, Any]] = []
        for rr in rounds:
            frontier = merge_known_outcomes(frontier, list(rr.results))
            if rr.round > 0:
                # Only the cells the freshly-locked ruler carries: it was anchored on the origin
                # and the archive, and a round that already walked past that is not on this scale.
                on_ruler = [r for r in frontier if int(r.get("sample_id", -1)) in cells]
                rr.ability = self.frontier(on_ruler)

    def persist(self, *, round_num: int) -> None:
        """The ruler lands on the cycle ledger BEFORE the round document that names it. A crash
        between them leaves a ruler carrying cells no round mentions, which is harmless; the
        reverse leaves a round whose θ nothing can reproduce, which is the state being removed.

        Called from run init as well as from every extension: ``Cycle.start`` locks the anchoring
        fit while the cycle still has no id to write it under, and round 0 is stamped and saved
        from that lock — so waiting for the first ``calibrate`` puts a whole round of scoring
        between the stamp and the record."""
        dataset_name = self.session.dataset_name
        if self.ruler is None or not self.session.state.cycle_id or not dataset_name:
            return
        self.session.store.campaigns.write_ruler(
            self.session.hop, self.ruler, dataset_name=dataset_name, round_num=round_num
        )
