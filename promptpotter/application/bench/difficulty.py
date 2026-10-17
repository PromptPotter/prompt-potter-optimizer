from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, NamedTuple

from promptpotter.application.initialization.session import Session
from promptpotter.application.intelligence.hard_sample_archive import build_archive_observations
from promptpotter.application.intelligence.rasch import (
    ORIGIN_ABILITY_ID,
    Observation,
    dedup_observations,
    extend_ruler,
    fit_theta,
    graded_response,
    graduate_ruler_model,
    observations_from_results,
)
from promptpotter.domain.results import RoundResult, merge_known_outcomes
from promptpotter.domain.ruler import (
    AbilityReading,
    CellScopes,
    DeltaRuler,
    RulerScope,
    flat_ruler_id,
    theta_caveat,
)
from promptpotter.domain.scoring import NO_CELLS, CellSheet, GradedCell
from promptpotter.infrastructure.store.archive_queries import as_of_moment, campaign_fenced
from promptpotter.shared.measurement_context import instrument_mode

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.infrastructure.store.read_model import Moment
    from promptpotter.shared.measurement_context import InstrumentMode

logger = logging.getLogger(__name__)

__all__ = ["DifficultyView", "StampedReading", "calibrate_delta_ruler", "session_ruler_scope"]


class StampedReading(NamedTuple):
    ability: AbilityReading | None
    unlinked: int
    pinned_share: float | None


def _reading(
    theta: tuple[float, float] | None,
    ruler: DeltaRuler | None,
    *,
    objective_id: str,
    results: Iterable[GradedCell],
    scopes: CellScopes,
) -> StampedReading:
    """The SOLE stamping site: θ, its scale and its caveat are minted together so none can disagree."""
    if theta is None:
        return StampedReading(None, 0, None)
    cells = {cell.key for cell in results if cell.scored}
    band = ruler.band_span(cells) if ruler is not None else None
    round_span = band[0] if band is not None else None
    ruler_span = ruler.delta_span if ruler is not None else None
    calibration = ruler.calibration_model if ruler is not None else None
    unlinked = ruler.unlinked(cells) if ruler is not None else 0
    pinned = ruler.pinned_share(cells) if ruler is not None else None
    ability = AbilityReading(
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
            unlinked=unlinked,
            scopes=scopes.spanned(cells),
            pinned_share=pinned,
        ),
    )
    return StampedReading(ability, unlinked, pinned)


def _cycle_history(rounds: list[RoundResult]) -> list[Observation]:
    groups: list[list[Observation]] = []
    for rr in rounds:
        groups.append(observations_from_results(rr.all_candidate_results))
        groups.append(observations_from_results(rr.reference_results))
        if rr.opt_sp is not None:
            groups.append(observations_from_results({rr.opt_sp.id: rr.results}))
    return dedup_observations(*groups)


def _with_origin(origin_results: CellSheet, archive_obs: list[Observation]) -> list[Observation]:
    origin_obs = observations_from_results({ORIGIN_ABILITY_ID: origin_results})
    # Argument order is the rule: the cycle's own origin rows win the cells both hold.
    return dedup_observations(archive_obs, origin_obs)


def _origin_theta(obs: list[Observation], ruler: DeltaRuler | None) -> tuple[float, float] | None:
    """Never the JOINT fit's ``post.theta[ORIGIN_ABILITY_ID]``: ``fit_rasch`` re-anchors per call."""
    origin = {o.key: o.response for o in obs if o.candidate_id == ORIGIN_ABILITY_ID}
    return fit_theta(origin, ruler.entries() if ruler is not None else None)


def calibrate_delta_ruler(
    origin_results: CellSheet | None,
    n_min: int,
    *,
    enable_2pl: bool,
    archive_obs: list[Observation],
) -> tuple[DeltaRuler | None, tuple[float, float] | None]:
    """The ANCHORING fit; ``extend_ruler`` grows it without moving it. ``None`` is cold and reads FLAT."""

    obs = _with_origin(NO_CELLS if origin_results is None else origin_results, archive_obs)
    if not obs:
        return None, None
    # ≥2 ARMS: with one, the anchor pins θ and δ collapses to that arm's own hit pattern.
    ruler: DeltaRuler | None = None
    if len({o.key for o in obs}) >= n_min and len({o.candidate_id for o in obs}) >= 2:
        fitted, post = graduate_ruler_model(obs, enable=enable_2pl)
        if len(post.delta) >= n_min:
            ruler = post.anchored(fitted)
            if fitted == "2PL":
                logger.info("δ ruler graduated to 2PL (%d samples fit)", len(post.delta))
    return ruler, _origin_theta(obs, ruler)


def session_ruler_scope(session: Session) -> RulerScope:
    return RulerScope(scorer=session.scoring.require_scorer().id, dataset=session.measured_dataset)


class _HeldScale(NamedTuple):
    ruler: DeltaRuler | None
    # Never beside closed rounds: anchored on an archive grown since, the fit is a second scale.
    fits_now: bool


def _held_scale(session: Session, moment: Moment | None, mode: InstrumentMode | None) -> _HeldScale:
    """The cycle's OWN scale unless its spawner hands down another anchor; a round closed on any
    other scale is re-read at the next ``calibrate``."""
    own = (
        session.store.campaigns.read_ruler(
            session.hop, scope=session_ruler_scope(session), moment=moment
        )
        if session.state.cycle_id
        else None
    )
    given = mode.ruler if mode is not None else None
    if given is not None and (own is None or own.anchor_id != given.anchor_id):
        return _HeldScale(given, fits_now=False)
    if own is not None or mode is not None:
        return _HeldScale(own, fits_now=False)
    closed = bool(session.state.cycle_id) and bool(
        session.store.campaigns.standing_rounds(session.hop, moment=moment).rounds
    )
    return _HeldScale(None, fits_now=not closed)


def _off_scale(rounds: Sequence[RoundResult], scale_id: str) -> bool:
    return any(rr.ability is not None and rr.ability.ruler_id != scale_id for rr in rounds)


def _cumulative_theta(
    results: Sequence[GradedCell], ruler: DeltaRuler | None
) -> tuple[float, float] | None:
    responses = {cell.key: graded_response(cell) for cell in results if cell.scored}
    return fit_theta(responses, ruler.entries() if ruler is not None else None)


@dataclass
class DifficultyView:
    session: Session
    n_min: int
    enable_2pl: bool
    # An instrument's cell reads its spawner's scale: one it fit comes out of the arms under test.
    derives: bool
    origin_sp_hash: str = ""
    # The archive as its task's memory held it when this view opened, never re-read.
    observations: list[Observation] = field(default_factory=list)
    # ``None`` = still cold, where θ is logit-accuracy.
    ruler: DeltaRuler | None = None

    @classmethod
    def open(
        cls,
        session: Session,
        config: CampaignConfig,
        *,
        origin_sp_hash: str,
        origin_results: CellSheet,
    ) -> tuple[DifficultyView, tuple[float, float] | None]:
        """At its task's moment (``as_of_moment``) the view re-opens as the cycle held it then: its
        ledger and its archive alike read up to that moment."""
        mode = instrument_mode()
        view = cls(
            session=session,
            n_min=config.optimization.elimination_n_min,
            enable_2pl=config.optimization.enable_2pl_graduation,
            derives=mode is None,
            origin_sp_hash=origin_sp_hash,
        )
        view.observations = view.archive()
        held = _held_scale(session, as_of_moment(), mode)
        if held.fits_now:
            view.ruler, origin_theta = calibrate_delta_ruler(
                origin_results,
                view.n_min,
                enable_2pl=view.enable_2pl,
                archive_obs=view.observations,
            )
            return view, origin_theta
        view.ruler = held.ruler
        return view, _origin_theta(_with_origin(origin_results, view.observations), held.ruler)

    def archive(self) -> list[Observation]:
        session = self.session
        if (session.controlled or not self.derives) and not campaign_fenced():
            raise RuntimeError(
                "an arm's or an instrument cell's δ read with no campaign fence bound"
            )
        return build_archive_observations(
            session.store,
            dataset_name=session.dataset_name,
            scorer=session.scoring.require_scorer(),
            admitted=session.scoring.require_partition().admitted_keys,
            origin_sp_hash=self.origin_sp_hash,
        )

    @cached_property
    def scopes(self) -> CellScopes:
        return CellScopes.of(self.session.samples, self.session.measured_dataset)

    @property
    def scale_id(self) -> str:
        if self.ruler is not None:
            return self.ruler.anchor_id
        return flat_ruler_id(self.session.scoring.require_scorer().id)

    def on_scale(self, results: list[GradedCell]) -> list[GradedCell]:
        """The flat scale carries every cell."""
        if self.ruler is None:
            return results
        return [cell for cell in results if cell.key in self.ruler.delta]

    def reading(
        self, theta: tuple[float, float] | None, *, results: Iterable[GradedCell]
    ) -> AbilityReading | None:
        return self._stamp(theta, results).ability

    def _stamp(
        self, theta: tuple[float, float] | None, results: Iterable[GradedCell]
    ) -> StampedReading:
        return _reading(
            theta,
            self.ruler,
            objective_id=self.session.scoring.require_scorer().id,
            results=results,
            scopes=self.scopes,
        )

    def frontier(self, results: Sequence[GradedCell]) -> StampedReading:
        return self._stamp(_cumulative_theta(results, self.ruler), results)

    def calibrate(
        self, measured: Mapping[str, CellSheet], rounds: list[RoundResult]
    ) -> list[RoundResult] | None:
        """After every cell is graded and BEFORE the election; the closed rounds re-read wherever
        one stands on another scale than this round reads on."""

        archive: list[Observation] | None = None
        if self.ruler is None and self.derives:
            archive = self.archive()
            self.ruler, _ = calibrate_delta_ruler(
                rounds[0].results, self.n_min, enable_2pl=self.enable_2pl, archive_obs=archive
            )
        reread = (
            self._reread(rounds, self.archive() if archive is None else archive)
            if _off_scale(rounds, self.scale_id)
            else None
        )
        if self.ruler is not None:
            self.ruler = extend_ruler(
                self.ruler, observations_from_results(measured), history=_cycle_history(rounds)
            )
            self.persist(round_num=max(len(rounds) - 1, 0))
        return reread

    def _reread(
        self, rounds: list[RoundResult], archive_obs: list[Observation]
    ) -> list[RoundResult]:
        # Round 0 carries θ twice — its frontier and C0's row — and a new scale must move both.
        origin = rounds[0]
        reading = self.reading(
            _origin_theta(_with_origin(origin.results, archive_obs), self.ruler),
            results=origin.results,
        )
        arms = [
            c.model_copy(
                update={
                    "theta": reading.theta if reading is not None else None,
                    "theta_se": reading.se if reading is not None else None,
                }
            )
            for c in origin.candidate_scores
        ]
        reread = [origin.model_copy(update={"ability": reading, "candidate_scores": arms})]
        # A round closed on another scale had θ fit on other δ, a scale the L4 law would average in.
        frontier: list[GradedCell] = []
        for rr in rounds:
            frontier = merge_known_outcomes(frontier, rr.results)
            if rr.round > 0:
                ability = self.frontier(self.on_scale(frontier)).ability
                reread.append(rr.model_copy(update={"ability": ability}))
        return reread

    def persist(self, *, round_num: int) -> None:
        """Must land BEFORE the round file that names it: the reverse leaves a θ nothing can reproduce."""
        if self.ruler is None or not self.session.state.cycle_id:
            return
        self.session.store.campaigns.write_ruler(
            self.session.hop,
            self.ruler,
            scope=session_ruler_scope(self.session),
            round_num=round_num,
        )
