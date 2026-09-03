"""The cross-subject evidence read — what an arbitrary SET of subjects jointly says.

Path is ``/evidence`` rather than ``/campaigns/evidence``: the selection spans campaigns and
datasets, so it is scoped to neither, and a literal under ``/campaigns/`` would have to out-order
``/campaigns/{campaign_id}`` to match at all.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Query

from promptpotter.application.evidence import (
    Evidence,
    SubjectSpec,
    campaigns_on_dataset,
    parse_subject,
    subject_evidence,
)
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
    subject: Annotated[
        list[str],
        Query(
            description=(
                "Subjects to pool, one per repetition of the parameter: "
                "`campaign:<campaign_id>` (its root origin), `course:<campaign_id>/<cycle_id>` "
                "(one branch, read at its last elected winner) or "
                "`candidate:<campaign_id>/<cycle_id>/<candidate_id>` (one searchpoint). May span "
                "campaigns and datasets, which the comparability verdict then reports on.\n\n"
                "An L4 inner run is addressed like any other, plus the sandbox chain it lives "
                "in: `;in=<campaign>::<cycle>`, root-first and `~`-joined, the same codec as "
                "`?descend=` because it is the same question. A sandbox is structurally an "
                "ordinary projects tree, so every kind above resolves inside one unchanged.\n\n"
                "A MASK rides the address, `;`-separated, so the record and the "
                "counterfactual can be two channels of one read: `;samples=3,7,11` restricts "
                "every value to those samples, and `;lens=score:<formula>` (courses only) "
                "re-decides the branch's elections under another criterion and reads it at the "
                "winner that would have stood. The response carries what that changed in "
                "`subjects[].scenario`, caveat included."
            )
        ),
    ] = [],  # noqa: B006 -- FastAPI reads the default to type the query, and never mutates it
    dataset: Annotated[
        str,
        Query(
            description=(
                "Expand to one `campaign:` subject per campaign bound to this dataset, in addition "
                "to any named by `subject`. Read off each manifest, so it catches an A/B arm, a "
                "fork and a rename that a name-shaped guess would skip — which is what the browser "
                "was doing client-side, then sending one query parameter per campaign."
            )
        ),
    ] = "",
    ranking: Annotated[
        bool,
        Query(
            description=(
                "Also rank the candidate edits measured across the selection's CAMPAIGN subjects. "
                "OFF by default because it is the widest walk here: the roster half reads one "
                "document per subject, while this opens EVERY round of every campaign selected. "
                "Edits are scored on the SELECTED metric, in its units."
            )
        ),
    ] = False,
    winner_chain: Annotated[
        bool,
        Query(
            description=(
                "Also serve the branch standing behind each course / candidate subject — the "
                "winner chain from its origin to its head, each point read on its own cells. "
                "OFF by default for the same reason as `ranking`: every point past the origin "
                "opens a round document. A campaign subject serves none, having no branch."
            )
        ),
    ] = False,
    config: Annotated[
        bool,
        Query(
            description=(
                "Also serve WHAT each searchpoint is — one flat `key -> value` map over its "
                "RESOLVED node config plus its prompt fields, which is what lines two of them "
                "up against each other. OFF by default because a prompt field is the largest "
                "thing this read puts on the wire and a four-channel comparison carries four."
            )
        ),
    ] = False,
    metric: Annotated[
        str,
        Query(
            description=(
                "Which number to compare on: a key from the catalogue the response echoes "
                "back, or `expr:<formula>` composed over `metric.namespace`. Both are resolved "
                "against THIS selection: a metric no selected subject carries is not offered, "
                "and `measurand` is the seed's own lift where the cells carry one and the cell's "
                "own fitness where a cell is a sample."
            )
        ),
    ] = MEASURAND,
) -> Evidence:
    """Roster, comparability, replicates, the cell/subject/residual decomposition, what the
    selection can resolve, the run-order confound, and — under the selected metric — a merged
    interval per subject with every pairwise test. Reduced fresh from disk on each fetch
    (on-demand, not the 2 s poll); zero LLM, nothing persisted."""
    try:
        specs = [parse_subject(raw) for raw in subject]
    except ValueError as exc:
        # The `?lens=` contract: a subject this layer cannot address is the caller's mistake, and
        # it names what went wrong rather than 404-ing on a shape someone typed.
        raise BadRequestError(str(exc)) from exc
    if dataset:
        named = {s.key for s in specs}
        specs += [
            spec
            for cid in campaigns_on_dataset(stores, dataset)
            if (spec := SubjectSpec("campaign", cid)).key not in named
        ]
    try:
        return subject_evidence(
            stores,
            specs,
            include_ranking=ranking,
            include_winner_chain=winner_chain,
            include_config=config,
            metric=metric,
        )
    except (ValueError, SyntaxError) as exc:
        # Passed through unprefixed — the read says whether the METRIC or the SELECTION was the
        # problem, and an "Invalid metric:" stamp read every unmeasured campaign as a bad formula.
        raise BadRequestError(str(exc)) from exc
