"""The browser sign-in: above admission, since the host's first one claims the terminal's tree
under the gate every destroyer holds (`jobs/launcher/admission.py::destroying`)."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from typing import NamedTuple

from promptpotter.application.account import (
    AccountCeilingError,
    Arrival,
    SignInRefusal,
    record_operator_notice,
    sign_in,
)
from promptpotter.application.jobs.launcher.admission import destroying
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.config.paths import default_projects_root
from promptpotter.config.settings import settings
from promptpotter.domain.records.run_records import Device
from promptpotter.infrastructure.identity import accounts
from promptpotter.infrastructure.identity.accounts import (
    IdentityBundle,
    derive_user_id,
    identity_of_session,
    resolve_access_state,
)
from promptpotter.infrastructure.identity.oidc import ProviderIdentity
from promptpotter.infrastructure.outbound import forward_new_account_to_crm, notify_operator
from promptpotter.infrastructure.store.layout import tenant_workspace
from promptpotter.infrastructure.store.stores import build_stores, tenant_stores
from promptpotter.infrastructure.store.user_store import count_accounts
from promptpotter.shared.errors import CycleBusyError, fault_said
from promptpotter.shared.identity import TERMINAL_IDENTITY_ID, IdentityContext, display_name


def claim_box(root: Path, *, user_id: str, email: str | None) -> str | None:
    """`accounts.py::claim_default` under `destroying` of each of the terminal's campaigns, so no
    run or pass takes a cycle of the tree it moves: ``None`` once claimed, else why it refused."""
    terminal = tenant_stores(root, tenant_workspace(root, TERMINAL_IDENTITY_ID))
    registry = JobRegistry.attach(terminal.jobs_dir)
    try:
        with ExitStack() as gated:
            for campaign_id in terminal.campaigns.list_campaign_ids():
                gated.enter_context(destroying(terminal, registry, campaign_id))
            return accounts.claim_default(projects_root=root, user_id=user_id, email=email)
    except CycleBusyError:
        return (
            f"a run holds a cycle under {terminal.base_dir}: pause it or let it end, then sign in"
        )


class SignedIn(NamedTuple):
    session_id: str
    identity: IdentityContext
    # Its `opened` is the one moment there is somebody new to announce (`ring`).
    arrival: Arrival
    device: Device | None
    # The declared host admin: its browser is marked the owner's.
    host_admin: bool
    # What the operator is rung with: a claim the host's sign-in did not take.
    rings: tuple[str, ...]


class SignInRefused(NamedTuple):
    refusal: SignInRefusal
    # What the operator is rung with: a refused sign-up, a claim the host's sign-in did not take.
    rings: tuple[str, ...]


def sign_in_oidc(
    bundle: IdentityBundle, verified: ProviderIdentity, *, device: Device | None
) -> SignedIn | SignInRefused:
    """THE browser sign-in: blocked, the box's claim, the account ceiling and the opening are
    decided here, and a session stands only for an account that does."""
    user_id = str(derive_user_id(verified.issuer, verified.subject))
    host_admin = accounts.is_declared_host(verified.email)
    root, rings = default_projects_root(), list[str]()
    # A blocked account still gets a session: the session seam empties its capabilities per request.
    blocked = resolve_access_state(verified.email, bundle) == "blocked"
    if host_admin and not blocked:
        # The terminal's tree is the box's own, so who takes it is declared, never inferred from
        # arrival order.
        refused = claim_box(root, user_id=user_id, email=verified.email)
        if refused is not None:
            rings.append(f"Your sign-in did not claim this box: {refused}")
    elif not blocked and accounts.host_user_id(root) is None:
        why = (
            "HOST_ADMIN_EMAIL's account has not signed in yet"
            if settings.HOST_ADMIN_EMAIL.strip()
            else "HOST_ADMIN_EMAIL is unset, so no browser identity is the host"
        )
        rings.append(
            f"Sign-in by {verified.email} did not claim this box: {why}. Terminal and browser "
            "resolve different tenants until it is claimed."
        )
    session_id, session = bundle.session_store.create(
        user_id=user_id,
        tenant_id=user_id,
        issuer=verified.issuer,
        subject=verified.subject,
        email=verified.email,
        provider=verified.provider,
    )
    refusal: SignInRefusal
    try:
        # Resolved as every later request resolves it, so a delegate's sign-in lands where its
        # commands do.
        identity = identity_of_session(session, bundle)
        stores = build_stores(identity, projects_root=default_projects_root())
        arrival = sign_in(stores, device=device)
        return SignedIn(session_id, identity, arrival, device, host_admin, tuple(rings))
    except AccountCeilingError as full:
        rings.append(f"Sign-up refused for {verified.email or 'no email'}: {full.message}")
        refusal = "accounts_full"
    except Exception as fault:  # noqa: BLE001 -- any fault refuses: no sign-in goes unrecorded
        # Refused rather than let through unrecorded: the trail's promise is that no sign-in is
        # missing.
        rings.append(f"A {verified.provider} sign-in could not be recorded: {fault_said(fault)[0]}")
        refusal = "signin_unavailable"
    bundle.session_store.delete(session_id)
    return SignInRefused(refusal, tuple(rings))


def ring(signed: SignedIn | SignInRefused) -> None:
    """After the response, never on it: the operator's rings, then an opened account's arrival."""
    for line in signed.rings:
        notify_operator(line, ring=True)
    if not isinstance(signed, SignedIn) or not signed.arrival.opened:
        return
    email, user_id = signed.arrival.user.email, signed.arrival.user.user_id
    # Read once the account is on disk, so the arriving account is inside the total.
    total = count_accounts(default_projects_root())
    told = notify_operator(
        f"New PromptPotter account: {email or f'(no email) {user_id}'}\n"
        f"Accounts now: {total}\n"
        f"Access: {signed.identity.access_state}\n"
        f"Device: {signed.device or 'unknown'}"
        + (f"\nRevoke it with:  /block {email}" if email else ""),
        # A stranger's arrival is the one decision this channel offers: /block it now, or not.
        ring=True,
    )
    record_operator_notice(
        build_stores(signed.identity, projects_root=default_projects_root()), told
    )
    # Fires regardless of access: a blocked account is still a contact worth keeping.
    forward_new_account_to_crm(
        email=email,
        name=display_name(email),
        user_id=user_id,
        account_count=total,
        device=signed.device,
    )
