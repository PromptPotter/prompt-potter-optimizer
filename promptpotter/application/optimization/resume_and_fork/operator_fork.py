"""``mint_operator_fork`` — the operator-initiated fork application entry.

The control-plane seam (``CommandDispatcher``, fork-cycle) parses the inbound
command and calls this; it builds the typed :class:`ForkSpec` and delegates to
the single fork-mint primitive :func:`_mint_fork`. There is no parallel
fork-creation path — the old dispatcher-local ``_apply_fork_cycle`` (verbatim
ledger-inherit, free-string ``"operator_hitl"`` trigger) is gone.

No seed ⇒ ``operator_endorse`` (fork the selected searchpoint as-is). Seed
present ⇒ ``operator_steered`` (the operator edited the searchpoint + reconciled
the run limits — recorded, not forbidden: operators may act, we record it). Both
are clean offshoots — fresh ledger, numbering restarts at round 1; the origin
re-scores from the (edited) searchpoint at bootstrap.
"""

from __future__ import annotations

from promptpotter.application.optimization.resume_and_fork.fork_siblings import _mint_fork
from promptpotter.domain.run_records import ForkSeed, ForkSpec, ForkTrigger
from promptpotter.infrastructure.store import Stores


def mint_operator_fork(
    *,
    stores: Stores,
    campaign_id: str,
    cycle_id: str,
    from_round: int,
    from_candidate_id: str,
    seed: ForkSeed | None,
    steered_by: str,
) -> str:
    """Mint an operator fork off *cycle_id*; returns the new cycle id.

    *seed* present ⇒ ``operator_steered`` (edited searchpoint + reconciled
    limits, persisted to ``.overrides/seed.json``); absent ⇒ ``operator_endorse``.
    """
    steered = seed is not None
    trigger = ForkTrigger.OPERATOR_STEERED if steered else ForkTrigger.OPERATOR_ENDORSE
    parent_index = stores.campaigns.load(campaign_id, cycle_id) or {}
    spec = ForkSpec(
        trigger=trigger,
        reason=(
            f"operator-steered fork from {cycle_id}"
            if steered
            else f"operator endorse fork from {cycle_id}"
        ),
        issued_by=steered_by or "operator",
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
