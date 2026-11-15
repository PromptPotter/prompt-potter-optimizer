"""Login / callback / logout run before any session exists, so they are `auth_flow_router`'s, mounted public."""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import secrets
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Path, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from promptpotter.application import account, health
from promptpotter.domain.records.run_records import device_of
from promptpotter.infrastructure.identity.accounts import IdentityBundle
from promptpotter.infrastructure.identity.oidc import (
    SUPPORTED_PROVIDERS,
    CodeExchangeError,
    IDTokenInvalidError,
    SignInClient,
)
from promptpotter.presentation.api.deps import require_identity_bundle
from promptpotter.presentation.api.middleware.oidc import (
    OWNER_DEVICE_COOKIE_NAME,
    SESSION_COOKIE_NAME,
)
from promptpotter.shared.errors import NotFoundError

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

    state = request.query_params.get("state")
    if not state:
        return _redirect_with_error("callback_missing_params")
    # Only a flow this browser started is filed: a stranger's query writes nothing.
    nonce = _flow_nonce(request, provider, state)
    if nonce is None:
        return _redirect_with_error("state_invalid_or_expired")

    error = request.query_params.get("error")
    if error:
        return _redirect_with_error(
            await asyncio.to_thread(health.provider_refused, provider, error)
        )
    code = request.query_params.get("code")
    if not code:
        return _redirect_with_error("callback_missing_params")

    try:
        identity = await client.exchange_code(code=code, nonce=nonce)
    except (CodeExchangeError, IDTokenInvalidError) as exc:
        refused = await asyncio.to_thread(
            health.sign_in_refused, provider, str(exc), "code_exchange_failed"
        )
        return _redirect_with_error(refused)

    device = device_of(request.headers.get("sec-ch-ua-mobile"), request.headers.get("user-agent"))
    # Off the loop: an arrival and the default's claim wait on the accounts lock.
    signed = await asyncio.to_thread(account.sign_in_oidc, bundle, identity, device=device)
    background.add_task(account.ring, signed)
    if isinstance(signed, account.SignInRefused):
        return _redirect_with_error(signed.refusal)

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
