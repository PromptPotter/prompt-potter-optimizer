"""What every account on this install has spent and produced — the operator-admin read owned by
[`0004-operator-admin-channels.md`](../../../docs/adr/0004-operator-admin-channels.md); never an
inbound API route. It takes the projects root because a ``Stores`` is scoped to one tenant."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from promptpotter.application.jobs.quota import (
    SpendCeilings,
    is_host_tenant_dir,
    lifetime_ceilings,
)
from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.infrastructure.store.account_spend import (
    UserSpend,
    account_ledgers,
    sum_user_spend,
)
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.user_store import User, UserStore


class AccountUsage(NamedTuple):
    """One account's row: who, what they spent, and what they produced. ``ceilings`` reads ``None``
    for the host, who spends their own money; ``ceilings.overrun(spent)`` is how far past it went.
    ``cycles`` counts ledger FILES, so a cycle that died before its first append is not among them.
    ``unreadable`` set means the row could not be summed and every other field is a placeholder."""

    user_id: str
    email: str | None
    spent: UserSpend
    ceilings: SpendCeilings
    campaigns: int
    cycles: int
    unreadable: str = ""


def read_install_spend(projects_root: Path, *, until: float) -> list[AccountUsage]:
    """Every account on this install, costliest first. ``user.json`` is what makes a tenant dir an
    account — a directory without one is a workspace nobody has signed into, and it is skipped."""
    rows: list[AccountUsage] = []
    if not projects_root.is_dir():
        return rows
    for tenant_dir in sorted(projects_root.iterdir()):
        if not tenant_dir.is_dir():
            continue
        try:
            user = UserStore(tenant_dir).load()
            if user is None:
                continue
            rows.append(_account_row(user, tenant_dir, until=until))
        except Exception as exc:
            # One torn file must not blind the operator to every OTHER account: a raise here is
            # `/spend` answering nothing at all, which is how the report stops being read. The
            # `user.json` READ is inside the guard for the same reason — it is a strict-model
            # validate, so a stale-schema file raises before there is a row to place. The dir name
            # is the account id (the first web sign-in renames `projects/default/` to
            # `projects/{user_id}/`), which is the one identifier still readable when `user.json`
            # is itself the torn file.
            rows.append(
                AccountUsage(
                    user_id=tenant_dir.name,
                    email=None,
                    spent=UserSpend(0.0, 0, 0),
                    ceilings=SpendCeilings(None, None),
                    campaigns=0,
                    cycles=0,
                    unreadable=f"{type(exc).__name__}: {exc}",
                )
            )
    rows.sort(key=lambda r: (r.spent.used_usd, r.spent.used_tokens), reverse=True)
    return rows


def _account_row(user: User, tenant_dir: Path, *, until: float) -> AccountUsage:
    """The host arm is :func:`is_host_tenant_dir` — the walk's reading of the same exemption the
    live gate applies, so the operator is not reported overrun on money that was always theirs."""
    campaigns = CampaignStore(WorkspaceDir(tenant_dir))
    return AccountUsage(
        user_id=user.user_id,
        email=user.email,
        spent=sum_user_spend(ledgers=account_ledgers(campaigns), since=0.0, until=until),
        ceilings=lifetime_ceilings(user=user, spends_own_key=is_host_tenant_dir(user.user_id)),
        campaigns=len(campaigns.iter_campaign_dirs()),
        cycles=len(campaigns.iter_cycle_ledgers()),
    )


__all__ = ["AccountUsage", "read_install_spend"]
