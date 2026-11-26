from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from promptpotter.domain.results import RoundResult
    from promptpotter.infrastructure.store.campaign_store.ledger_scan import StandingRounds


def origin_only(rounds: Sequence[RoundResult], standing: StandingRounds) -> bool:
    """No round has read the origin yet: none closed past it, none walked on it."""
    return [t.round for t in rounds] == [0] and 1 not in standing.proposals
