"""The cross-campaign evidence read — what an arbitrary SET of campaigns jointly says.

Path is ``/evidence`` rather than ``/campaigns/evidence``: the selection spans campaigns and
datasets, so it is scoped to neither, and a literal under ``/campaigns/`` would have to out-order
``/campaigns/{campaign_id}`` to match at all.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Query

from promptpotter.application.evidence import Evidence, campaign_evidence
from promptpotter.application.evidence_metrics import MEASURAND
from promptpotter.presentation.api.deps import StoresDep
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router
from promptpotter.shared.errors import BadRequestError


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
                "because it is the widest walk here: the origin half reads one round-0 document "
                "per campaign, while this opens EVERY round of every campaign selected. Edits "
                "are scored on the SELECTED metric, in its units."
            )
        ),
    ] = False,
    metric: Annotated[
        str,
        Query(
            description=(
                "Which number to compare on: a key from the catalogue the response echoes "
                "back, or `expr:<formula>` composed over `metric.namespace`. Both are resolved "
                "against THIS selection: a metric no selected campaign carries is not offered, "
                "and `measurand` is the seed's own lift where the cells carry one and the cell's "
                "own fitness where a cell is a sample."
            )
        ),
    ] = MEASURAND,
) -> Evidence:
    """Roster, comparability, replicates, the cell/arm/residual decomposition, what the selection
    can resolve, the run-order confound, and — under the selected metric — a merged interval per
    campaign with every pairwise test. Reduced fresh from disk on each fetch (on-demand, not the
    2 s poll); zero LLM, nothing persisted."""
    try:
        return campaign_evidence(stores, list(campaign), include_ranking=ranking, metric=metric)
    except (ValueError, SyntaxError) as exc:
        # The `?lens=` contract: a selection this layer cannot resolve is the caller's mistake, and
        # it names what went wrong rather than 500-ing on a formula someone typed. Passed through
        # unprefixed — the read says whether the METRIC or the SELECTION was the problem, and an
        # "Invalid metric:" stamp read every unmeasured campaign as a bad formula.
        raise BadRequestError(str(exc)) from exc
