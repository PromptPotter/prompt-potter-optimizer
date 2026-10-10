"""The operator-admin read (ADR-0004), never an inbound API route; a ``Stores`` is one tenant's, hence the root."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from promptpotter.application.jobs.quota import is_host_tenant_dir, lifetime_ceilings
from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.spend import SpendCeilings
from promptpotter.infrastructure.store.account_spend import (
    UserSpend,
    account_ledgers,
    sum_user_spend,
)
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.user_store import User, UserStore


class AccountUsage(NamedTuple):
    user_id: str
    email: str | None
    spent: UserSpend
    ceilings: SpendCeilings
    campaigns: int
    cycles: int
    # Set means the row could not be summed and every other field is a placeholder.
    unreadable: str = ""


def read_install_spend(projects_root: Path) -> list[AccountUsage]:
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
            rows.append(_account_row(user, tenant_dir))
        except Exception as exc:
            # One torn file must not blind `/spend` to every OTHER account; the dir name is the account id.
            rows.append(
                AccountUsage(
                    user_id=tenant_dir.name,
                    email=None,
                    spent=UserSpend(0.0, 0, 0),
                    ceilings=SpendCeilings(),
                    campaigns=0,
                    cycles=0,
                    unreadable=f"{type(exc).__name__}: {exc}",
                )
            )
    rows.sort(key=lambda r: (r.spent.sent_usd, r.spent.used_tokens), reverse=True)
    return rows


def _account_row(user: User, tenant_dir: Path) -> AccountUsage:
    campaigns = CampaignStore(WorkspaceDir(tenant_dir))
    return AccountUsage(
        user_id=user.user_id,
        email=user.email,
        spent=sum_user_spend(ledgers=account_ledgers(campaigns)),
        ceilings=lifetime_ceilings(user=user, spends_own_key=is_host_tenant_dir(user.user_id)),
        campaigns=len(campaigns.iter_campaign_dirs()),
        cycles=len(campaigns.iter_cycle_ledgers()),
    )


__all__ = ["AccountUsage", "read_install_spend"]
