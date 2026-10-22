from __future__ import annotations

from typing import Annotated

from pydantic import ConfigDict, Field

from promptpotter.domain.phases import StopReason
from promptpotter.domain.strict_model import StrictModel

__all__ = ["InnerCellFacts"]


class InnerCellFacts(StrictModel):
    """Carried BESIDE the measurement, never as ``OuterSampleProxies`` fields the formula could score."""

    model_config = ConfigDict(frozen=True)

    origin_level: float | None = Field(
        description="The origin's θ on the search split's level series; null where "
        "`series_reason` names why"
    )
    final_lift: float | None = Field(
        description="The last round's level less the origin's; null where `series_reason` names why"
    )
    peak_lift: float | None = Field(
        description="The best round's level less the origin's; null where `series_reason` names why"
    )
    mean_round_delta: Annotated[float, Field(ge=-4.0, le=4.0)] | None = Field(
        description="The mean level less the origin's, the last level held forward to the round "
        "budget; bounded for plausibility only, logit abilities differing without bound; null "
        "where `series_reason` names why"
    )
    series_reason: str | None = Field(
        description="Why the level series differences nothing; null where it does"
    )
    mean_parent_level_se: float | None = Field(
        description="The mean SE of the levels past the origin: this arm's own half of a paired "
        "difference, the shared origin level cancelling; a precision, never a penalty"
    )
    rounds_ran: int = Field(description="The rounds the inner cycle closed past its origin")
    round_budget: int = Field(description="The round budget, never below the rounds it ran")
    stop_reason: StopReason = Field(description="Why the inner cycle stopped")
    origin_headroom: float | None = Field(
        description="The share of origin rows still missed: 0 is a cell no arm can lift, however "
        "sharply it reads"
    )
    bench_origin_level: float | None = Field(
        description="The origin's headline on the held-out rows"
    )
    bench_final_lift: float | None = Field(
        description="The last standing selection's held-out headline less the origin's"
    )
    sent_usd: float | None = Field(
        description="Dollars sent, cumulative across attempts; reporting only, billed per call "
        "and entering no ledger"
    )
    tokens: int | None = Field(
        description="Tokens used, cumulative across attempts; reporting only, as `sent_usd`"
    )
