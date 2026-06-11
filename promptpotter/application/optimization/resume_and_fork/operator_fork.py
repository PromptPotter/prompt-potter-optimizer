"""``mint_operator_fork`` — the operator-initiated fork application entry.

The control-plane seam (``CommandDispatcher``, fork-cycle) parses the inbound
command and calls this; it builds the typed :class:`ForkSpec` and delegates to
the single fork-mint primitive :func:`_mint_fork`. There is no parallel
fork-creation path — the old dispatcher-local ``_apply_fork_cycle`` (verbatim
ledger-inherit, free-string ``"operator_hitl"`` trigger) is gone.

Every operator fork is ``operator_steered``: it carries a seed (the chosen
searchpoint's evolved prompt + config + reconciled run limits — recorded, not
forbidden: operators may act, we record it). It's a clean offshoot — fresh
ledger, numbering restarts at round 1; the origin re-scores from the edited
searchpoint at bootstrap.
"""

from __future__ import annotations

from promptpotter.application.optimization.resume_and_fork.fork_siblings import _mint_fork
from promptpotter.domain.run_records import (
    UNATTRIBUTED_OPERATOR,
    CycleSeed,
    ForkSpec,
    ForkTrigger,
)
from promptpotter.infrastructure.store import Stores


def mint_operator_fork(
    *,
    stores: Stores,
    campaign_id: str,
    cycle_id: str,
    from_round: int,
    from_candidate_id: str,
    seed: CycleSeed,
    steered_by: str,
) -> str:
    """Mint an ``operator_steered`` fork off *cycle_id*; returns the new cycle id.

    *seed* (edited searchpoint + reconciled limits) is persisted to
    ``.overrides/seed.json`` and becomes the fork's origin at bootstrap.
    """
    parent_index = stores.campaigns.load(campaign_id, cycle_id) or {}
    spec = ForkSpec(
        trigger=ForkTrigger.OPERATOR_STEERED,
        reason=f"operator-steered fork from {cycle_id}",
        issued_by=steered_by or UNATTRIBUTED_OPERATOR,
        from_round=from_round,
        from_candidate_id=from_candidate_id or None,
        seed=seed,
    )
    return _mint_fork(
        stores.campaigns,
        campaign_id,
        stores.tenant_id,
        str(parent_index.get("parent_session_id", "")),
        cycle_id,
        0,
        spec,
        projects_root=stores.projects_root,
    )


__all__ = ["mint_operator_fork"]
