"""The operator-admin read (decisions.md § 0004), never an inbound API route; a ``Stores`` is one tenant's, hence the root."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.spend import NOTHING_PAID, Budget, PaidSpend
from promptpotter.infrastructure.identity.migration import is_host_tenant_dir
from promptpotter.infrastructure.llm.account_key import face_of
from promptpotter.infrastructure.store.account_spend import account_ledgers, sum_user_spend
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.user_store import Torn, User, read_accounts
from promptpotter.infrastructure.store.wallet_store import GrantStore
from promptpotter.shared.errors import LedgerUnreadableError


class AccountUsage(NamedTuple):
    user_id: str
    email: str | None
    spent: PaidSpend
    # The account's coupon face.
    ceilings: Budget
    campaigns: int
    cycles: int
    # Set means the row could not be summed and every other field is a placeholder.
    unreadable: str = ""


def read_install_spend(projects_root: Path) -> list[AccountUsage]:
    rows: list[AccountUsage] = []
    for account in read_accounts(projects_root):
        if isinstance(account.user, Torn):
            rows.append(_unreadable_row(account.workspace, account.user.why))
            continue
        try:
            rows.append(_account_row(account.user, account.workspace))
        except (OSError, LedgerUnreadableError) as exc:
            # A ledger that will not scan is the same row as a `user.json` that will not load.
            rows.append(_unreadable_row(account.workspace, f"{type(exc).__name__}: {exc}"))
    rows.sort(key=lambda r: (r.spent.total.sent_usd, r.spent.total.used_tokens), reverse=True)
    return rows


def _unreadable_row(tenant_dir: WorkspaceDir, why: str) -> AccountUsage:
    # The dir name is the account id.
    return AccountUsage(
        user_id=tenant_dir.name,
        email=None,
        spent=NOTHING_PAID,
        ceilings=Budget(),
        campaigns=0,
        cycles=0,
        unreadable=why,
    )


def _account_row(user: User, tenant_dir: WorkspaceDir) -> AccountUsage:
    campaigns = CampaignStore(tenant_dir)
    return AccountUsage(
        user_id=user.user_id,
        email=user.email,
        spent=sum_user_spend(ledgers=account_ledgers(campaigns)),
        ceilings=face_of(
            GrantStore(tenant_dir).load(),
            host=is_host_tenant_dir(user.user_id),
        ),
        campaigns=len(campaigns.iter_campaign_dirs()),
        cycles=len(campaigns.iter_cycle_ledgers()),
    )
