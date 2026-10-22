from __future__ import annotations

import logging
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING, NamedTuple, assert_never

from promptpotter.application.initialization.session import Session
from promptpotter.application.intelligence.rasch import (
    Observation,
    fit_theta,
    graded_response,
    graduate_ruler_model,
)
from promptpotter.domain.measurement_provenance import meets_grade
from promptpotter.domain.results import Concluded, InRunCells, is_floor_pinned
from promptpotter.domain.ruler import (
    AbilityReading,
    CellScopes,
    DeltaRuler,
    LevelSeries,
    RulerRow,
    RulerScope,
    RulerStamp,
    ThetaCaveat,
    flat_ruler_id,
    theta_caveat,
)
from promptpotter.infrastructure.store.archive_queries import ArmOf, CellOf, OpenAt, memory_scope
from promptpotter.infrastructure.store.read_model import derived
from promptpotter.shared.measurement_context import RoleScope

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.scoring import CellSheet, GradedCell
    from promptpotter.infrastructure.store.ruler_store import RulerStore
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)


# Not a knob: the ruler PoBB cuts with and the heatmap the operator reads must be the same scale.
_RULER_GRADE = "A"


def calibrate_delta_ruler(
    obs: list[Observation], n_min: int, *, enable_2pl: bool
) -> DeltaRuler | None:
    """``None`` is cold and reads FLAT."""
    # ≥2 ARMS: with one, the anchor pins θ and δ collapses to that arm's own hit pattern.
    if len({o.key for o in obs}) < n_min or len({o.candidate_id for o in obs}) < 2:
        return None
    fitted, post = graduate_ruler_model(obs, enable=enable_2pl)
    if len(post.delta) < n_min:
        return None
    if fitted == "2PL":
        logger.info("δ ruler graduated to 2PL (%d samples fit)", len(post.delta))
    return post.anchored(fitted)


class JudgingRuler(NamedTuple):
    ruler: DeltaRuler | None
    stamp: RulerStamp


def judging_ruler(
    store: RulerStore,
    scope: RulerScope,
    version: int,
    judged: Collection[str],
    *,
    objective_id: str,
    n_min: int,
    enable_2pl: bool,
) -> JudgingRuler:
    """The ONE anchoring fit: the tenant ruler at *version* less every row of an individual the
    reading judges, so no election reads a δ its own arms moved."""
    rows = store.rows(scope, version)
    excluded = tuple(sorted({address for address, _, _ in rows} & set(judged)))
    held = [Observation(a, key, y) for a, key, y in rows if a not in excluded]
    key = ("judging_ruler", store.path, scope, version, excluded, n_min, enable_2pl)
    # A version is a prefix of an append-only file, so its fit never moves under the key.
    ruler = (
        derived(
            key,
            sig=version,
            compute=lambda: calibrate_delta_ruler(held, n_min, enable_2pl=enable_2pl),
        )
        if held
        else None
    )
    stamp = RulerStamp(
        id=ruler.anchor_id if ruler is not None else flat_ruler_id(objective_id),
        version=version,
        scope=scope,
        excluded=excluded,
    )
    return JudgingRuler(ruler, stamp)


def restamped(
    store: RulerStore, stamp: RulerStamp, *, n_min: int, enable_2pl: bool
) -> JudgingRuler:
    """The scale a banked reading was taken on, re-fit off its stamp."""
    return judging_ruler(
        store,
        stamp.scope,
        stamp.version,
        stamp.excluded,
        objective_id=stamp.scope.scorer,
        n_min=n_min,
        enable_2pl=enable_2pl,
    )


class AbilityScale(NamedTuple):
    """What a θ is minted on: one judging fit, and the dataset each cell measures."""

    judging: JudgingRuler
    scopes: CellScopes

    def caveat(self, rows: Iterable[GradedCell]) -> ThetaCaveat | None:
        rows = list(rows)
        cells = [cell.key for cell in rows if cell.scored]
        return theta_caveat(
            self.judging.ruler,
            cells,
            scopes=self.scopes.spanned(cells),
            all_miss=is_floor_pinned(rows),
        )

    def frontier(self, results: Iterable[GradedCell]) -> AbilityReading | None:
        """The SOLE stamping site: θ, its scale and its caveat are minted together."""
        results = list(results)
        scored = [cell for cell in results if cell.scored]
        ruler = self.judging.ruler
        responses = {cell.key: graded_response(cell) for cell in scored}
        theta = fit_theta(responses, ruler.entries() if ruler is not None else None)
        # The flat scale carries every cell.
        on = [c.key for c in scored if ruler is None or c.key in ruler.delta]
        if theta is None or not on:
            return None
        band = ruler.band_span(on) if ruler is not None else None
        return AbilityReading(
            theta=theta[0],
            se=theta[1],
            ruler=self.judging.stamp,
            cells=len(on),
            ruler_span=ruler.delta_span if ruler is not None else None,
            round_span=band[0] if band is not None else None,
            calibration_model=ruler.calibration_model if ruler is not None else None,
            caveat=self.caveat(results),
            unlinked=ruler.unlinked(responses) if ruler is not None else 0,
            pinned_share=ruler.pinned_share(on) if ruler is not None else None,
        )


def session_ruler_scope(session: Session) -> RulerScope:
    return RulerScope(scorer=session.scoring.require_scorer().id, dataset=session.measured_dataset)


def ruler_rows(
    stores: Stores, sheets: Iterable[tuple[str, CellSheet]], admitted: frozenset[str] | None
) -> list[RulerRow]:
    """A grade-A row on a search cell, by the archive address that answered it: a bench row and a
    replay of a weaker grade reach no ruler."""
    out: list[RulerRow] = []
    for address, sheet in sheets:
        for cell in sheet.scoreable:
            filed = None if cell.facts.answer is None else stores.archive.answer(cell.facts.answer)
            if (
                filed is not None
                and (admitted is None or cell.key in admitted)
                and meets_grade(filed.provenance, _RULER_GRADE)
            ):
                out.append((address, cell.key, graded_response(cell)))
    return out


def parent_line(rounds: Sequence[RoundResult]) -> set[str]:
    """The origin and every pick since, by archive address: the individuals each round judges against."""
    return {
        head.sp_hash
        for rr in rounds
        if rr.election is not None and rr.election.selected and (head := rr.head) is not None
    }


@dataclass
class DifficultyView:
    """The δ scale an election reads: the tenant ruler at the version its clock names, less the
    rows of the individuals it judges. A round's own cells fold in after its election."""

    session: Session
    n_min: int
    enable_2pl: bool
    judged: frozenset[str]
    judging: JudgingRuler

    @classmethod
    def open(cls, session: Session, config: CampaignConfig, *, origin: str) -> DifficultyView:
        """Round 0's view: the origin is the one individual it judges."""
        opt = config.optimization
        judged = frozenset({origin})
        n_min, enable_2pl = opt.elimination_n_min, opt.enable_2pl_graduation
        judging = _session_ruler(session, ruler_clock(session), judged, n_min, enable_2pl)
        return cls(session, n_min, enable_2pl, judged, judging)

    @property
    def ruler(self) -> DeltaRuler | None:
        return self.judging.ruler

    @property
    def stamp(self) -> RulerStamp:
        return self.judging.stamp

    def _read(self, version: int, judged: Collection[str]) -> JudgingRuler:
        return _session_ruler(self.session, version, judged, self.n_min, self.enable_2pl)

    def open_round(self, rounds: Sequence[RoundResult]) -> None:
        """At the round's open, before its draw: the parent line is what the round judges so far."""
        self.judged = frozenset(parent_line(rounds))
        self.judging = self._read(ruler_clock(self.session), self.judged)

    def judge(self, addresses: Iterable[str]) -> None:
        """At measure start, before a cell is bought: the round's arms and their parents join."""
        self.judged = self.judged | frozenset(addresses)
        self.judging = self._read(self.stamp.version, self.judged)

    def ruler_of(self, stamp: RulerStamp | None) -> DeltaRuler | None:
        """None for an unelected round."""
        if stamp is None or stamp.cold:
            return None
        return self._restamped(stamp).ruler

    def seat(self, stamp: RulerStamp) -> None:
        """Back onto the scale a banked round read, as a replay of it stands."""
        self.judged = frozenset(stamp.excluded)
        self.judging = self._restamped(stamp)

    def _restamped(self, stamp: RulerStamp) -> JudgingRuler:
        return restamped(
            self.session.store.ruler, stamp, n_min=self.n_min, enable_2pl=self.enable_2pl
        )

    def fold(self, rr: RoundResult) -> None:
        """After the election, so the round's cells reach the NEXT version and never its own."""
        session = self.session
        sheets = [
            (arm.sp_hash, rr.rows(key))
            for key, arm in rr.arms.items()
            if isinstance(arm, Concluded) and arm.sp_hash
        ]
        session.store.ruler.fold(
            session_ruler_scope(session),
            ruler_rows(session.store, sheets, session.scoring.require_partition().admitted_keys),
        )

    @cached_property
    def scopes(self) -> CellScopes:
        return CellScopes.of(self.session.bank.samples, self.session.measured_dataset)

    @property
    def scale(self) -> AbilityScale:
        return AbilityScale(self.judging, self.scopes)

    def level_series(self, lines: Sequence[Sequence[RoundResult]]) -> list[LevelSeries]:
        """ONE fit less every line's parents, so lines' levels sum; each caveats on its own cells."""
        judged = frozenset(a for rounds in lines for a in parent_line(rounds))
        scale = AbilityScale(self._read(ruler_clock(self.session), judged), self.scopes)
        return [
            LevelSeries(
                stamp=scale.judging.stamp,
                levels={rnd: scale.frontier(sheets[i]) for rnd, i in ended.items()},
                caveat=scale.caveat(cell for sheet in sheets.values() for cell in sheet),
            )
            for ended, sheets in map(_ended_on, lines)
        ]


def _ended_on(rounds: Sequence[RoundResult]) -> tuple[dict[int, str], dict[str, CellSheet]]:
    """By round, the individual it ended on, and each one's decision rows on the line."""
    origin = rounds[0].election
    assert rounds[0].round == 0 and origin is not None, "round 0 elects the origin it adopted"
    standing = origin.ended_on
    ended: dict[int, str] = {}
    for rr in rounds:
        # A round that elected nothing ended on the parent standing into it.
        if rr.election is not None:
            standing = rr.election.ended_on
        ended[rr.round] = standing
    walks = InRunCells(rounds)
    return ended, {i: walks.sheet(i, RoleScope.DECISION) for i in set(ended.values())}


def ruler_clock(session: Session) -> int:
    """The ruler version a round opening now reads under its task's memory scope."""
    match scope := memory_scope():
        case OpenAt():
            return session.store.ruler.version()
        case ArmOf(ruler_version=version) | CellOf(ruler_version=version):
            return version
        case _:
            assert_never(scope)


def _session_ruler(
    session: Session, version: int, judged: Collection[str], n_min: int, enable_2pl: bool
) -> JudgingRuler:
    return judging_ruler(
        session.store.ruler,
        session_ruler_scope(session),
        version,
        judged,
        objective_id=session.scoring.require_scorer().id,
        n_min=n_min,
        enable_2pl=enable_2pl,
    )
