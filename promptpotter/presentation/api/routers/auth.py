"""Login / callback / logout run before any session exists, so they are `auth_flow_router`'s, mounted public."""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import logging
import secrets
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Path, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from promptpotter.application import account, health
from promptpotter.config.paths import default_projects_root
from promptpotter.domain.records.run_records import Device, device_of
from promptpotter.infrastructure.identity.bundle import IdentityBundle
from promptpotter.infrastructure.identity.provider_config import (
    SUPPORTED_PROVIDERS,
    CodeExchangeError,
    SignInClient,
)
from promptpotter.infrastructure.identity.verifier import IDTokenInvalidError
from promptpotter.infrastructure.outbound import forward_new_account_to_crm, notify_operator
from promptpotter.infrastructure.store.user_store import count_accounts
from promptpotter.presentation.api.deps import (
    build_stores_from_identity,
    require_identity_bundle,
)
from promptpotter.presentation.api.middleware.oidc import (
    OWNER_DEVICE_COOKIE_NAME,
    SESSION_COOKIE_NAME,
)
from promptpotter.shared.errors import (
    NotFoundError,
)
from promptpotter.shared.identity import AccessState, IdentityContext, display_name

logger = logging.getLogger(__name__)

auth_flow_router = APIRouter(prefix="/auth", tags=["Auth"])

# `provider.state.nonce` of the sign-in this browser started; the callback honours no other.
OAUTH_FLOW_COOKIE_NAME = "promptpotter_oauth_flow"
_OAUTH_FLOW_TTL_S = 600
# The longest a browser keeps a cookie; each of the admin's sign-ins renews it.
_OWNER_DEVICE_TTL_S = 400 * 24 * 60 * 60


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


def _exchange_refused(provider: str, exc: Exception) -> account.SignInRefusal:
    # Nobody signed in yet, so the refusal is the box's own to keep.
    box = build_stores_from_identity(account.registered_or_default_identity())
    account.record_refusal(box, subject=f"sign-in through {provider}: {exc}")
    # No retry of the user's mends a provider the health check found refusing this server.
    broken = health.is_broken(default_projects_root(), health.signin_probe(provider))
    return "signin_unavailable" if broken else "code_exchange_failed"


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
        refusal = await asyncio.to_thread(_exchange_refused, provider, exc)
        return _redirect_with_error(refusal)

    device = device_of(request.headers.get("sec-ch-ua-mobile"), request.headers.get("user-agent"))
    # Off the loop: an arrival and the default's claim wait on the accounts lock.
    signed = await asyncio.to_thread(account.sign_in_oidc, bundle, identity, device=device)
    for ring in signed.rings:
        background.add_task(notify_operator, ring, ring=True)
    if isinstance(signed, account.SignInRefused):
        return _redirect_with_error(signed.refusal)
    if signed.opened:
        _announce(background, signed.identity, device)

    response = RedirectResponse(url="/", status_code=303)
    _set_auth_cookie(
        response,
        request,
        key=SESSION_COOKIE_NAME,
        value=signed.session_id,
        max_age=60 * 60 * 24 * 7,
    )
    response.delete_cookie(OAUTH_FLOW_COOKIE_NAME, path="/")
    if signed.host_admin:
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


def _announce(
    background: BackgroundTasks, identity: IdentityContext, device: Device | None
) -> None:
    """After the response, never on it."""
    background.add_task(
        _announce_new_account,
        email=identity.email,
        name=display_name(identity.email),
        user_id=str(identity.user_id),
        access_state=identity.access_state,
        device=device,
    )
