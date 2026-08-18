"""The cross-campaign evidence read — what an arbitrary SET of campaigns jointly says.

Path is ``/evidence`` rather than ``/campaigns/evidence``: the selection spans campaigns and
datasets, so it is scoped to neither, and a literal under ``/campaigns/`` would have to out-order
``/campaigns/{campaign_id}`` to match at all.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Query

from promptpotter.application.evidence import Evidence, campaign_evidence
from promptpotter.presentation.api.deps import StoresDep
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router


# Tenant-scoped (the walk only sees this tenant's campaigns) and self-gating on data: an id that
# names nothing is simply absent from the roster. No capability gate and no L4 gate — an ordinary
# campaign and a self-optimizing one take exactly the same path through here.
@campaigns_router.get("/evidence", response_model=Evidence)
def get_evidence(
    stores: StoresDep,
    campaign: Annotated[
        list[str],
        Query(description="Campaign ids to pool. Repeat the parameter; may span datasets."),
    ] = [],  # noqa: B006 -- FastAPI reads the default to type the query, and never mutates it
    ranking: Annotated[
        bool,
        Query(
            description=(
                "Also rank the candidate edits measured across the selection. OFF by default "
                "because it is the only half that opens a round document past round 0: the rest "
                "reads one origin per campaign, this walks every round of every campaign."
            )
        ),
    ] = False,
) -> Evidence:
    """Roster, comparability, replicates, the cell/arm/residual decomposition, what the selection
    can resolve, and the run-order confound — reduced fresh from disk on each fetch (on-demand,
    not the 2 s poll); zero LLM, nothing persisted."""
    return campaign_evidence(stores, list(campaign), include_ranking=ranking)
