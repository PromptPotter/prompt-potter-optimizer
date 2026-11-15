"""The host's exhibit: the one campaign an account reads outside its own tenant.

`EXHIBIT_CAMPAIGN` names it in the host's account; a signed-in, unblocked account with "Try & learn"
on (`User.demo_mode_enabled`) reads its subjects off the host's stores, built with no capability, so
nothing acts on it. A command keeps the caller's stores, where the exhibit is not found.
"""

from __future__ import annotations

from dataclasses import replace

from promptpotter.application.account import account_or_opening
from promptpotter.config.settings import settings
from promptpotter.infrastructure.identity.accounts import host_user_id
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.shared.identity import default_identity


def exhibit_stores(stores: Stores) -> Stores | None:
    """The host's stores, read-only, where this caller follows the exhibit; ``None`` where none is
    named, the host has no account yet, the caller is the host or blocked, or its switch is off."""
    host = host_user_id(stores.projects_root) if settings.EXHIBIT_CAMPAIGN else None
    if (
        host is None
        or host == stores.tenant_id
        or stores.inside
        or stores.identity.access_state == "blocked"
        or not account_or_opening(stores).demo_mode_enabled
    ):
        return None
    reader = replace(default_identity(tenant_id=host), capabilities=frozenset(), host=False)
    return build_stores(
        reader,
        projects_root=stores.projects_root,
        install_datasets_root=stores.install_datasets_root,
        research_datasets_root=stores.research_datasets_root,
        shared_root=stores.shared_root,
    )


def read_stores(stores: Stores, campaign_id: str | None) -> Stores:
    """The stores a read of *campaign_id*'s subjects answers from: the exhibit's where it names the
    exhibit and the caller follows it (`exhibit_stores`), else the caller's own."""
    if campaign_id is None or campaign_id != settings.EXHIBIT_CAMPAIGN:
        return stores
    return exhibit_stores(stores) or stores
