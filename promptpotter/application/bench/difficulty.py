from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, NamedTuple

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
from promptpotter.domain.results import RoundResult, measured_cells, merge_known_outcomes
from promptpotter.domain.ruler import (
    AbilityReading,
    DeltaRuler,
    flat_ruler_id,
    is_flat_ruler_id,
    theta_caveat,
)
from promptpotter.domain.scoring import NO_CELLS, CellSheet, GradedCell
from promptpotter.infrastructure.store.archive_queries import memory_scoped
from promptpotter.shared.errors import RulerUnpersistedError
from promptpotter.shared.measurement_context import instrument_mode

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig

logger = logging.getLogger(__name__)

__all__ = ["DifficultyView", "RulerScope", "StampedReading", "calibrate_delta_ruler"]

RulerScope = Literal["dataset", "campaign"]


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
) -> StampedReading:
    """The SOLE stamping site: θ, its scale and its caveat are minted together so none can disagree."""
    if theta is None:
        return StampedReading(None, 0, None)
    cells = list(measured_cells(results))
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
    origin = {o.sample_id: o.response for o in obs if o.candidate_id == ORIGIN_ABILITY_ID}
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
    if len({o.sample_id for o in obs}) >= n_min and len({o.candidate_id for o in obs}) >= 2:
        fitted, post = graduate_ruler_model(obs, enable=enable_2pl)
        if len(post.delta) >= n_min:
            ruler = post.anchored(fitted)
            if fitted == "2PL":
                logger.info("δ ruler graduated to 2PL (%d samples fit)", len(post.delta))
    return ruler, _origin_theta(obs, ruler)


def _given_ruler(session: Session) -> DeltaRuler | None:
    """A scale already fixed is never re-derived: the cycle's OWN ledger first, then the INSTRUMENT."""
    if session.state.cycle_id:
        # A δ key names a sample only within one dataset; an unnamed cycle still owes the refusal.
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
    """Falling through re-fits on an archive grown since the lock: a different scale, same cycle."""

    rounds = session.store.campaigns.standing_rounds(session.hop).rounds
    if not rounds:
        return
    # Warmth is monotone within a cycle, so the LAST standing round answers in one read.
    ability = rounds[max(rounds)].close.ability
    stamped = (ability.ruler_id or "") if ability is not None else ""
    if not stamped or is_flat_ruler_id(stamped):
        return
    raise RulerUnpersistedError(
        stamped, campaign_id=session.hop.campaign_id, cycle_id=session.hop.cycle_id
    )


def _cumulative_theta(
    results: Sequence[GradedCell], ruler: DeltaRuler | None
) -> tuple[float, float] | None:
    responses = {cell.ruler_key: graded_response(cell) for cell in results if cell.scored}
    return fit_theta(responses, ruler.entries() if ruler is not None else None)


@dataclass
class DifficultyView:
    session: Session
    n_min: int
    enable_2pl: bool
    scope: RulerScope = "dataset"
    origin_sp_hash: str = ""
    # The archive as it stood when the cycle OPENED, never re-read.
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
        scope: RulerScope,
    ) -> tuple[DifficultyView, tuple[float, float] | None]:
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
            return view, _origin_theta(_with_origin(origin_results, view.observations), given)
        view.ruler, origin_theta = calibrate_delta_ruler(
            origin_results, view.n_min, enable_2pl=view.enable_2pl, archive_obs=view.observations
        )
        return view, origin_theta

    def archive(self) -> list[Observation]:
        session = self.session
        if self.scope == "campaign" and not memory_scoped():
            raise RuntimeError("a campaign-scoped ruler read with no controlled arm's fence bound")
        return build_archive_observations(
            session.store,
            dataset_name=session.dataset_name,
            scorer=session.scoring.require_scorer(),
            sample_ids=session.scoring.require_partition().admitted_ids,
            origin_sp_hash=self.origin_sp_hash,
        )

    @property
    def scale_id(self) -> str:
        if self.ruler is not None:
            return self.ruler.anchor_id
        return flat_ruler_id(self.session.scoring.require_scorer().id)

    @property
    def cells(self) -> set[int]:
        return set(self.ruler.delta) if self.ruler is not None else set()

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
        )

    def frontier(self, results: Sequence[GradedCell]) -> StampedReading:
        return self._stamp(_cumulative_theta(results, self.ruler), results)

    def calibrate(
        self, measured: Mapping[str, CellSheet], rounds: list[RoundResult]
    ) -> list[RoundResult] | None:
        """After every cell is graded and BEFORE the election; the re-read rounds only where a fit locked now."""

        reread: list[RoundResult] | None = None
        if self.ruler is None:
            ruler, origin_theta = calibrate_delta_ruler(
                rounds[0].results,
                self.n_min,
                enable_2pl=self.enable_2pl,
                archive_obs=self.archive(),
            )
            if ruler is None:
                return None
            self.ruler = ruler
            reread = self._reread(rounds, origin_theta)

        self.ruler = extend_ruler(
            self.ruler, observations_from_results(measured), history=_cycle_history(rounds)
        )
        self.persist(round_num=max(len(rounds) - 1, 0))
        return reread

    def _reread(
        self, rounds: list[RoundResult], origin_theta: tuple[float, float] | None
    ) -> list[RoundResult]:
        # Round 0 carries θ twice — its frontier and C0's row — and a warm fit must move both.
        origin = rounds[0]
        reading = self.reading(origin_theta, results=origin.results)
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
        # A round closed on a flat ruler had θ fit at δ≡0, a DIFFERENT scale the L4 law would average.
        cells = self.cells
        frontier: list[GradedCell] = []
        for rr in rounds:
            frontier = merge_known_outcomes(frontier, rr.results)
            if rr.round > 0:
                on_ruler = [cell for cell in frontier if cell.ruler_key in cells]
                reread.append(rr.model_copy(update={"ability": self.frontier(on_ruler).ability}))
        return reread

    def persist(self, *, round_num: int) -> None:
        """Must land BEFORE the round file that names it: the reverse leaves a θ nothing can reproduce."""
        dataset_name = self.session.dataset_name
        if self.ruler is None or not self.session.state.cycle_id or not dataset_name:
            return
        self.session.store.campaigns.write_ruler(
            self.session.hop, self.ruler, dataset_name=dataset_name, round_num=round_num
        )
