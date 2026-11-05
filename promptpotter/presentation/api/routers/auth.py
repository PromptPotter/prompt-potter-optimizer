"""Login / callback / logout run before any session exists, so they are `auth_flow_router`'s, mounted public."""

from __future__ import annotations

import hmac
import ipaddress
import logging
import secrets
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Path, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from promptpotter.application import account, health
from promptpotter.config.paths import default_projects_root
from promptpotter.config.settings import settings
from promptpotter.domain.run_records import Device, device_of
from promptpotter.infrastructure.identity.bundle import IdentityBundle
from promptpotter.infrastructure.identity.default_claim import (
    maybe_claim_default,
    registered_user_id,
)
from promptpotter.infrastructure.identity.provider_config import (
    SUPPORTED_PROVIDERS,
    CodeExchangeError,
    SignInClient,
)
from promptpotter.infrastructure.identity.user import derive_user_id
from promptpotter.infrastructure.identity.verifier import IDTokenInvalidError
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.infrastructure.store.user_store import count_accounts
from promptpotter.presentation.admin_bot import forward_new_account_to_crm, notify_operator
from promptpotter.presentation.api.deps import (
    build_stores_from_identity,
    require_identity_bundle,
)
from promptpotter.presentation.api.middleware.oidc import (
    OWNER_DEVICE_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    identity_of_session,
    resolve_access_state,
)
from promptpotter.shared.errors import (
    NotFoundError,
)
from promptpotter.shared.identity import (
    AccessState,
    IdentityContext,
    display_name,
)

logger = logging.getLogger(__name__)

auth_flow_router = APIRouter(prefix="/auth", tags=["Auth"])

# `provider.state.nonce` of the sign-in this browser started; the callback honours no other.
OAUTH_FLOW_COOKIE_NAME = "promptpotter_oauth_flow"
_OAUTH_FLOW_TTL_S = 600
# The longest a browser keeps a cookie; each of the admin's sign-ins renews it.
_OWNER_DEVICE_TTL_S = 400 * 24 * 60 * 60


def _is_declared_host_admin(email: str | None, issuer: str | None) -> bool:
    """Unset `HOST_ADMIN_EMAIL` means nobody may: signing up is the grant, so "first one in wins" is unsafe."""
    declared = settings.HOST_ADMIN_EMAIL.strip().lower()
    if not declared or (email or "").strip().lower() != declared:
        return False
    declared_issuer = settings.HOST_ADMIN_ISSUER.strip()
    return not declared_issuer or (issuer or "").strip() == declared_issuer


def _require_provider_client(bundle: IdentityBundle, provider: str) -> tuple[str, SignInClient]:
    """The server's own name for the provider beside its client: no URL text travels on."""
    for name, client in bundle.clients.items():
        if name == provider:
            return name, client
    if provider in SUPPORTED_PROVIDERS:
        raise NotFoundError(
            f"provider {provider!r} not configured",
            code="provider_not_configured",
            details={"provider": provider},
        )
    raise NotFoundError(
        f"unknown provider {provider!r}", code="provider_unknown", details={"provider": provider}
    )


def _set_auth_cookie(
    response: Response, request: Request, *, key: str, value: str, max_age: int
) -> None:
    response.set_cookie(
        key=key,
        value=value,
        max_age=max_age,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/",
    )


def _parent_domain(host: str) -> str | None:
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        labels = host.split(".")
        return ".".join(labels[1:]) if len(labels) > 2 else None


def _mark_owner_device(response: Response, request: Request) -> None:
    """On the parent domain (`app.example.com` → `example.com`), where the public site reads it.

    Script-readable, because the site's `?self=0` takes it off. A bare name or an address keeps it on
    this host.
    """
    response.set_cookie(
        key=OWNER_DEVICE_COOKIE_NAME,
        value="1",
        max_age=_OWNER_DEVICE_TTL_S,
        domain=_parent_domain(request.url.hostname or ""),
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/",
    )


def _flow_nonce(request: Request, provider: str, state: str) -> str | None:
    """This browser's flow nonce for `provider`, iff `state` is that flow's: no other browser's login lands here."""
    started = request.cookies.get(OAUTH_FLOW_COOKIE_NAME, "")
    flow_provider, _, rest = started.partition(".")
    flow_state, _, nonce = rest.partition(".")
    if flow_provider != provider or not nonce:
        return None
    return nonce if hmac.compare_digest(flow_state.encode(), state.encode()) else None


def _redirect_with_error(code: account.SignInRefusal) -> RedirectResponse:
    """The callback is browser-navigated: raising would dump raw JSON into the tab.

    A CODE and nothing else: the access log keeps this URL, so it never carries who tried.
    """
    response = RedirectResponse(url=f"/?auth_error={code}", status_code=303)
    response.delete_cookie(OAUTH_FLOW_COOKIE_NAME, path="/")
    return response


@auth_flow_router.get("/login/{provider}")
async def login(
    request: Request,
    provider: Annotated[str, Path(pattern=r"^[a-z]+$", max_length=16)],
) -> RedirectResponse:
    name, client = _require_provider_client(require_identity_bundle(request), provider)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    response = RedirectResponse(url=client.authorize_url(state=state, nonce=nonce), status_code=307)
    _set_auth_cookie(
        response,
        request,
        key=OAUTH_FLOW_COOKIE_NAME,
        value=f"{name}.{state}.{nonce}",
        max_age=_OAUTH_FLOW_TTL_S,
    )
    return response


@auth_flow_router.get("/callback/{provider}")
async def callback(
    request: Request,
    background: BackgroundTasks,
    provider: Annotated[str, Path(pattern=r"^[a-z]+$", max_length=16)],
) -> RedirectResponse:
    bundle: IdentityBundle | None = getattr(request.app.state, "identity_bundle", None)
    client = bundle.clients.get(provider) if bundle is not None else None
    if bundle is None or client is None:
        return _redirect_with_error("signin_unavailable")

    error = request.query_params.get("error")
    if error:
        logger.warning("OIDC callback error for %s: %s", provider, error)
        return _redirect_with_error("provider_returned_error")

    state = request.query_params.get("state")
    code = request.query_params.get("code")
    if not state or not code:
        return _redirect_with_error("callback_missing_params")

    nonce = _flow_nonce(request, provider, state)
    if nonce is None:
        return _redirect_with_error("state_invalid_or_expired")

    try:
        identity = await client.exchange_code(code=code, nonce=nonce)
    except (CodeExchangeError, IDTokenInvalidError) as exc:
        logger.warning("OIDC sign-in through %s refused: %s", provider, exc)
        # Nobody signed in yet, so the refusal is the box's own to keep.
        box = build_stores_from_identity(account.registered_or_default_identity())
        account.record_refusal(box, subject=f"sign-in through {provider}: {exc}")
        # No retry of the user's mends a provider the health check found refusing this server.
        broken = health.is_broken(default_projects_root(), health.signin_probe(provider))
        return _redirect_with_error("signin_unavailable" if broken else "code_exchange_failed")

    # A blocked account still gets a session: the session seam empties its capabilities per request.
    user_id = derive_user_id(identity.issuer, identity.subject)
    access_state = resolve_access_state(identity.email, bundle)
    host_admin = _is_declared_host_admin(identity.email, identity.issuer)
    if access_state == "blocked":
        logger.info("Blocked account signed in: %s (%s)", identity.email, provider)
    elif host_admin:
        # The marker names the box's own tenant, so who writes it is declared, never inferred from arrival order.
        maybe_claim_default(
            projects_root=default_projects_root(),
            user_id=str(user_id),
            marker_path=bundle.paths.default_claim_marker,
        )
    elif registered_user_id(bundle.paths.default_claim_marker) is None:
        why = (
            "HOST_ADMIN_EMAIL's account has not signed in yet"
            if settings.HOST_ADMIN_EMAIL.strip()
            else "HOST_ADMIN_EMAIL is unset, so no browser identity may write the claim marker"
        )
        logger.warning(
            "Sign-in by %s did not claim this box: %s. Terminal and browser will resolve DIFFERENT "
            "tenants until it is claimed.",
            identity.email,
            why,
        )

    session_id, session = bundle.session_store.create(
        user_id=str(user_id),
        tenant_id=str(user_id),
        issuer=identity.issuer,
        subject=identity.subject,
        email=identity.email,
        provider=identity.provider,
    )
    try:
        # Resolved as every later request resolves it, so a delegate's sign-in lands where its commands do.
        signed_in = identity_of_session(session, bundle)
        device = _device(request)
        arrival = account.sign_in(build_stores_from_identity(signed_in), device=device)
    except account.AccountCeilingError as full:
        bundle.session_store.delete(session_id)
        background.add_task(
            notify_operator,
            f"Sign-up refused for {identity.email or 'no email'}: {full.message}",
            ring=True,
        )
        return _redirect_with_error("accounts_full")
    except Exception:
        # Refused rather than let through unrecorded: the trail's promise is that no sign-in is missing.
        logger.exception("A %s sign-in could not be recorded", provider)
        bundle.session_store.delete(session_id)
        return _redirect_with_error("signin_unavailable")
    _announce_if_opened(background, arrival, signed_in, device)

    response = RedirectResponse(url="/", status_code=303)
    _set_auth_cookie(
        response, request, key=SESSION_COOKIE_NAME, value=session_id, max_age=60 * 60 * 24 * 7
    )
    response.delete_cookie(OAUTH_FLOW_COOKIE_NAME, path="/")
    if host_admin:
        _mark_owner_device(response, request)
    return response


@auth_flow_router.post("/logout")
async def logout(request: Request) -> JSONResponse:
    bundle = require_identity_bundle(request)
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        bundle.session_store.delete(session_id)
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


def _device(request: Request) -> Device | None:
    return device_of(request.headers.get("sec-ch-ua-mobile"), request.headers.get("user-agent"))


def _announce_new_account(
    *,
    email: str | None,
    name: str | None,
    user_id: str,
    access_state: AccessState,
    device: Device | None,
) -> None:
    who = email or f"(no email) {user_id}"
    # Read once the account is on disk, so the arriving account is inside the total.
    total = count_accounts(default_projects_root())
    notify_operator(
        f"New PromptPotter account: {who}\n"
        f"Accounts now: {total}\n"
        f"Access: {access_state}\n"
        f"Device: {device or 'unknown'}" + (f"\nRevoke it with:  /block {email}" if email else ""),
        # A stranger's arrival is the one decision this channel offers: /block it now, or not.
        ring=True,
    )
    # Fires regardless of `access_state`: a blocked account is still a contact worth keeping.
    forward_new_account_to_crm(
        email=email, name=name, user_id=user_id, account_count=total, device=device
    )


def _announce_if_opened(
    background: BackgroundTasks,
    arrival: account.Arrival,
    identity: IdentityContext,
    device: Device | None,
) -> None:
    """After the response, never on it."""
    if arrival.opened:
        background.add_task(
            _announce_new_account,
            email=identity.email,
            name=display_name(identity.email),
            user_id=str(identity.user_id),
            access_state=identity.access_state,
            device=device,
        )


def arrive(request: Request, background: BackgroundTasks, stores: Stores) -> None:
    """`me`'s arrival: opened at sign-in as a rule; here for an open box, which has none, and for a
    session that outlived its files."""
    device = _device(request)
    _announce_if_opened(background, account.arrive(stores, device=device), stores.identity, device)
