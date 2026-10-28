"""An account's life, written and read back.

Each fact is stamped ONCE and written to the workspace ledger before ``user.json``, so no account
and no consent exists without its record; a write that fails between the two repeats the record on
the retry, which reads as the same fact twice and never as one missing.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import NamedTuple

from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.domain.run_records import AccountEvent, AccountEventRecord, Device
from promptpotter.infrastructure.identity import blocklist, migration
from promptpotter.infrastructure.identity.grants import (
    grant_principal,
    list_grants,
    revoke_principal,
)
from promptpotter.infrastructure.identity.paths import default_identity_paths
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store.account_trail import (
    TrailLine,
    in_time_order,
    workspace_lines,
)
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.infrastructure.store.user_store import (
    AccountDir,
    ConsentRecord,
    Torn,
    User,
    read_accounts,
)
from promptpotter.shared.clock import epoch_seconds, utcnow_iso
from promptpotter.shared.identity import IdentityContext, acting_principal, default_identity


def _record(
    stores: Stores,
    event: AccountEvent,
    *,
    at: str,
    subject: str = "",
    device: Device | None = None,
) -> None:
    CycleEventLog.open_workspace(stores.base_dir).append(
        AccountEventRecord(
            event=event,
            user_id=acting_principal(stores.identity).id,
            subject=subject,
            device=device,
            timestamp=at,
        )
    )


class Arrival(NamedTuple):
    user: User
    # This call opened the account: the one moment there is somebody new to announce.
    opened: bool


def arrive(stores: Stores, *, device: Device | None) -> Arrival:
    """The ONE place an account comes into being, whichever request reaches it first."""
    existing = stores.users.load()
    if existing is not None:
        return Arrival(existing, opened=False)
    identity = stores.identity
    user = User(
        user_id=str(identity.user_id),
        tenant_id=str(identity.tenant_id),
        email=identity.email,
        created_at=utcnow_iso(),
    )
    _record(stores, "account_created", at=user.created_at, device=device)
    stores.users.save(user)
    return Arrival(user, opened=True)


def account_of(stores: Stores) -> User:
    return arrive(stores, device=None).user


def sign_in(stores: Stores, *, device: Device | None) -> Arrival:
    arrival = arrive(stores, device=device)
    _record(
        stores,
        "signed_in",
        at=utcnow_iso(),
        subject=stores.identity.provider or "",
        device=device,
    )
    return arrival


def accept_terms(stores: Stores, version: str) -> User:
    """``accepted_at`` is this server's clock, never the client's: its legal weight rests on that."""
    consent = ConsentRecord(version=version, accepted_at=utcnow_iso())
    user = account_of(stores).model_copy(update={"terms_accepted": consent})
    _record(stores, "terms_accepted", at=consent.accepted_at, subject=version)
    stores.users.save(user)
    return user


def record_refusal(stores: Stores, *, subject: str) -> None:
    """A person's attempt the server turned down: without it, a failed first try leaves no trace."""
    _record(stores, "request_refused", at=utcnow_iso(), subject=subject)


def record_upload(stores: Stores, *, filename: str, kept: Path) -> None:
    """Names the kept bytes beside the refusal that may follow, so the trail leads to the file."""
    _record(stores, "uploaded", at=utcnow_iso(), subject=f"{filename} -> uploads/{kept.name}")


class Trail(NamedTuple):
    # Whose it is: empty where no readable account carries the email asked for.
    own: frozenset[str]
    lines: list[TrailLine]
    # Workspaces that could not be read, so what they hold is missing from ``lines``.
    unreadable: list[str]


def tenant_trail(stores: Stores) -> Trail:
    return Trail(
        frozenset({acting_principal(stores.identity).id}),
        in_time_order(workspace_lines(stores.campaigns)),
        [],
    )


def _scanned(
    accounts: Iterable[AccountDir], unreadable: list[str]
) -> Iterator[tuple[AccountDir, list[TrailLine]]]:
    """A ledger that will not scan costs its own workspace and no other: it is named, then skipped."""
    for account in accounts:
        try:
            found = list(workspace_lines(CampaignStore(account.workspace)))
        except Exception:
            if (who := _who(account)) not in unreadable:
                unreadable.append(who)
            continue
        yield account, found


def _who(account: AccountDir) -> str:
    """The email, else the id: an account no provider vouched an address for still acted."""
    return (
        account.workspace.name
        if isinstance(account.user, Torn)
        else (account.user.email or account.user.user_id)
    )


def trail_by_email(projects_root: Path, email: str) -> Trail:
    """The operator-admin read (ADR-0004) of what one PERSON did, across every workspace on the box.

    Everything in a workspace they own, whoever did it, and their own lines in anyone else's: a
    delegate acts in its delegator's workspace. Several ids where one address signed in through more
    than one provider, since the id is per issuer.
    """
    wanted = email.strip().lower()
    accounts = read_accounts(projects_root)
    own = frozenset(
        account.user.user_id
        for account in accounts
        if not isinstance(account.user, Torn)
        and (account.user.email or "").strip().lower() == wanted
    )
    unreadable = [account.workspace.name for account in accounts if isinstance(account.user, Torn)]
    lines: list[TrailLine] = []
    for account, found in _scanned(accounts if own else (), unreadable):
        if not isinstance(account.user, Torn) and account.user.user_id in own:
            lines += found
        else:
            lines += [
                line._replace(subject=f"{account.workspace.name}:{line.subject}")
                for line in found
                if line.actor in own
            ]
    return Trail(own, in_time_order(lines), unreadable)


class AccountTrail(NamedTuple):
    who: str  # `_who`
    trail: Trail
    # Campaign id -> the dataset it optimizes, for each campaign a line names that still exists.
    datasets: dict[str, str]


def trails_since(projects_root: Path, since: float) -> tuple[list[AccountTrail], list[str]]:
    """The operator-admin read (ADR-0004) of what EVERY account did from ``since`` (epoch) on.

    One trail per workspace that has a line, whoever acted in it, then the workspaces that could
    not be read.
    """
    accounts = read_accounts(projects_root)
    unreadable = [account.workspace.name for account in accounts if isinstance(account.user, Torn)]
    trails: list[AccountTrail] = []
    for account, found in _scanned(accounts, unreadable):
        recent = [line for line in found if (epoch_seconds(line.at) or 0.0) >= since]
        if recent and not isinstance(account.user, Torn):
            store = CampaignStore(account.workspace)
            campaigns = {
                campaign_id: store.load_campaign(campaign_id)
                for campaign_id in {line.campaign_id for line in recent if line.campaign_id}
            }
            trails.append(
                AccountTrail(
                    _who(account),
                    Trail(frozenset({account.user.user_id}), in_time_order(recent), []),
                    {key: c.dataset_name for key, c in campaigns.items() if c is not None},
                )
            )
    return trails, unreadable


def registered_user_id() -> str | None:
    return migration.registered_user_id(default_identity_paths().default_claim_marker)


def registered_or_default_identity(explicit_tenant: str | None = None) -> IdentityContext:
    if explicit_tenant:
        return default_identity(tenant_id=explicit_tenant)
    uid = registered_user_id()
    return default_identity(tenant_id=uid, user_id=uid) if uid else default_identity()


def local_stores(*, tenant: str | None) -> Stores:
    return build_stores(registered_or_default_identity(tenant), projects_root=DEFAULT_PROJECTS_ROOT)


def blocked_emails() -> list[str]:
    return blocklist.list_blocked(default_identity_paths().blocklist)


def block_email(email: str, *, actor: str) -> list[str]:
    paths = default_identity_paths()
    return blocklist.block_email(
        paths.blocklist, email, actor=actor, audit_path=paths.blocklist_audit
    )


def unblock_email(email: str, *, actor: str) -> list[str]:
    paths = default_identity_paths()
    return blocklist.unblock_email(
        paths.blocklist, email, actor=actor, audit_path=paths.blocklist_audit
    )


def grants() -> dict[str, dict[str, object]]:
    return list_grants(default_identity_paths().grants)


def revoke_grant(sub_user_id: str, *, actor: str) -> bool:
    paths = default_identity_paths()
    return revoke_principal(
        paths.grants, sub_principal_user_id=sub_user_id, actor=actor, audit_path=paths.grants_audit
    )


def grant(sub_user_id: str, *, delegator: str, capabilities: frozenset[str], actor: str) -> None:
    paths = default_identity_paths()
    grant_principal(
        paths.grants,
        sub_principal_user_id=sub_user_id,
        delegated_by_user_id=delegator,
        capabilities=capabilities,
        spend_ceiling_usd=None,
        note=f"via {actor}",
        actor=actor,
        audit_path=paths.grants_audit,
    )
