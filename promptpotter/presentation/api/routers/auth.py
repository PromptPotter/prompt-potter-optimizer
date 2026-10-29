"""Login / callback / logout run before any session exists, so they are `auth_flow_router`'s, mounted public."""

from __future__ import annotations

import hmac
import logging
import secrets
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Path, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import SecretStr

from promptpotter.application import account, health
from promptpotter.application.jobs.account_activity import (
    ActivityGroupBy,
    ActivityResponse,
    ActivityWindow,
    account_activity,
)
from promptpotter.application.jobs.coupon import (
    AccountKey,
    CouponStatus,
    coupon_status,
    put_account_key,
    remove_account_key,
)
from promptpotter.application.jobs.quota import QuotaStatus, quota_status
from promptpotter.config.paths import default_projects_root
from promptpotter.config.settings import TERMS_VERSION, settings
from promptpotter.domain.run_records import Device, device_of
from promptpotter.domain.strict_model import RequestBody, StrictModel
from promptpotter.infrastructure.identity.bundle import IdentityBundle
from promptpotter.infrastructure.identity.migration import maybe_claim_default, registered_user_id
from promptpotter.infrastructure.identity.provider_config import (
    SUPPORTED_PROVIDERS,
    CodeExchangeError,
    SignInClient,
)
from promptpotter.infrastructure.identity.user import derive_user_id
from promptpotter.infrastructure.identity.verifier import IDTokenInvalidError
from promptpotter.infrastructure.store.user_store import count_accounts
from promptpotter.presentation.admin_bot import forward_new_account_to_crm, notify_operator
from promptpotter.presentation.api.deps import (
    IdentityDep,
    JobRegistryDep,
    StoresDep,
    build_stores_from_identity,
)
from promptpotter.presentation.api.middleware.oidc import (
    SESSION_COOKIE_NAME,
    identity_of_session,
    resolve_access_state,
)
from promptpotter.shared.errors import (
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from promptpotter.shared.identity import (
    CAMPAIGN_BUDGET_CAP,
    AccessState,
    IdentityContext,
    display_name,
    require_capability,
)

logger = logging.getLogger(__name__)

auth_flow_router = APIRouter(prefix="/auth", tags=["Auth"])
auth_router = APIRouter(prefix="/auth", tags=["Auth"])

# `provider.state.nonce` of the sign-in this browser started; the callback honours no other.
OAUTH_FLOW_COOKIE_NAME = "promptpotter_oauth_flow"
_OAUTH_FLOW_TTL_S = 600


class ConnectedAccount(StrictModel):
    """One OIDC provider bound to the active session."""

    provider: str
    email: str | None


class UserSettings(RequestBody):
    """The caller's per-user preferences."""

    demo_mode_enabled: bool


class MeResponse(StrictModel):
    """The caller's identity envelope, with the provider and consent state the account and consent gates read."""

    user_id: str
    tenant_id: str
    issuer: str | None
    email: str | None
    name: str | None
    provider: str | None
    connected_accounts: list[ConnectedAccount]
    available_providers: list[str]
    capabilities: list[str]
    access_state: AccessState
    terms_version: str
    terms_accepted_version: str | None


def _is_declared_host_admin(email: str | None, issuer: str | None) -> bool:
    """Unset `HOST_ADMIN_EMAIL` means nobody may: signing up is the grant, so "first one in wins" is unsafe."""
    declared = settings.HOST_ADMIN_EMAIL.strip().lower()
    if not declared or (email or "").strip().lower() != declared:
        return False
    declared_issuer = settings.HOST_ADMIN_ISSUER.strip()
    return not declared_issuer or (issuer or "").strip() == declared_issuer


def _require_bundle(request: Request) -> IdentityBundle:
    bundle: IdentityBundle | None = getattr(request.app.state, "identity_bundle", None)
    if bundle is None:
        raise ServiceUnavailableError(
            "identity backend not initialised", code="identity_not_initialised"
        )
    return bundle


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


def _flow_nonce(request: Request, provider: str, state: str) -> str | None:
    """This browser's flow nonce for `provider`, iff `state` is that flow's: no other browser's login lands here."""
    started = request.cookies.get(OAUTH_FLOW_COOKIE_NAME, "")
    flow_provider, _, rest = started.partition(".")
    flow_state, _, nonce = rest.partition(".")
    if flow_provider != provider or not nonce:
        return None
    return nonce if hmac.compare_digest(flow_state.encode(), state.encode()) else None


def _redirect_with_error(code: str) -> RedirectResponse:
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
    name, client = _require_provider_client(_require_bundle(request), provider)
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
    if access_state == "blocked":
        logger.info("Blocked account signed in: %s (%s)", identity.email, provider)
    elif _is_declared_host_admin(identity.email, identity.issuer):
        # The marker names the box's own tenant, so who writes it is declared, never inferred from arrival order.
        maybe_claim_default(
            projects_root=default_projects_root(),
            user_id=str(user_id),
            marker_path=bundle.paths.default_claim_marker,
        )
    elif registered_user_id(bundle.paths.default_claim_marker) is None:
        logger.warning(
            "Sign-in by %s did not claim this box: HOST_ADMIN_EMAIL is unset, so no browser identity "
            "may write the claim marker. Terminal and browser will resolve DIFFERENT tenants until it is.",
            identity.email,
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
            notify_operator, f"Sign-up refused for {identity.email or 'no email'}: {full.message}"
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
    return response


@auth_flow_router.post("/logout")
async def logout(request: Request) -> JSONResponse:
    bundle = _require_bundle(request)
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
        f"Device: {device or 'unknown'}" + (f"\nRevoke it with:  /block {email}" if email else "")
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


@auth_router.get("/me", response_model=MeResponse)
def me(
    request: Request, background: BackgroundTasks, identity: IdentityDep, stores: StoresDep
) -> MeResponse:
    """The caller's identity envelope; ``available_providers`` is the configured ones minus the connected one."""
    bundle = _require_bundle(request)
    email, provider, access_state = identity.email, identity.provider, identity.access_state
    name = display_name(email)
    connected = [ConnectedAccount(provider=provider, email=email)] if provider else []
    configured = set(bundle.config.configured)
    available = sorted(configured - {provider}) if provider else sorted(configured)
    # Opened at sign-in as a rule; here for an open box, which has none, and for a session that outlived its files.
    device = _device(request)
    arrival = account.arrive(stores, device=device)
    _announce_if_opened(background, arrival, identity, device)
    user = arrival.user
    return MeResponse(
        user_id=str(identity.user_id),
        tenant_id=str(identity.tenant_id),
        issuer=str(identity.issuer) if identity.issuer else None,
        email=email,
        name=name,
        provider=provider,
        connected_accounts=connected,
        available_providers=available,
        capabilities=sorted(identity.capabilities),
        access_state=access_state,
        terms_version=TERMS_VERSION,
        terms_accepted_version=user.terms_accepted.version if user.terms_accepted else None,
    )


@auth_router.get("/quota-status", response_model=QuotaStatus)
def get_quota_status(job_registry: JobRegistryDep, stores: StoresDep) -> QuotaStatus:
    """The caller's live quota: usage is unclamped, and the ceilings are the resolved ones, never the raw nullable overrides."""
    return quota_status(stores=stores, job_registry=job_registry)


@auth_router.get("/user-settings", response_model=UserSettings)
def get_user_settings(stores: StoresDep) -> UserSettings:
    """The current user's preferences."""
    return UserSettings(demo_mode_enabled=account.account_of(stores).demo_mode_enabled)


@auth_router.patch("/user-settings", response_model=UserSettings)
def patch_user_settings(body: UserSettings, stores: StoresDep) -> UserSettings:
    """Persist a preference change for the current user."""
    user = account.account_of(stores)
    stores.users.save(user.model_copy(update={"demo_mode_enabled": body.demo_mode_enabled}))
    return UserSettings(demo_mode_enabled=body.demo_mode_enabled)


class AcceptTermsBody(RequestBody):
    """The terms version the client accepts, which must equal the live ``TERMS_VERSION``."""

    version: str


class TermsConsent(StrictModel):
    """The consent state after an accept, in the fields ``/me`` reports it."""

    terms_version: str
    terms_accepted_version: str | None


@auth_router.post("/accept-terms", response_model=TermsConsent)
def accept_terms(body: AcceptTermsBody, stores: StoresDep) -> TermsConsent:
    """Record the current user's acceptance of the Terms, server-stamped.

    A version other than the live ``TERMS_VERSION`` answers 409 ``terms_version_stale``.
    """
    if body.version != TERMS_VERSION:
        raise ConflictError(
            "Terms version is out of date — reload to accept the current terms.",
            code="terms_version_stale",
            details={"expected": TERMS_VERSION, "received": body.version},
        )
    account.accept_terms(stores, TERMS_VERSION)
    return TermsConsent(terms_version=TERMS_VERSION, terms_accepted_version=TERMS_VERSION)


@auth_router.get("/activity", response_model=ActivityResponse)
def activity(
    stores: StoresDep,
    window: ActivityWindow = "1d",
    group_by: ActivityGroupBy = "model",
) -> ActivityResponse:
    """Time-bucketed spend, requests and tokens over the window, each billed call at the price it was stamped with.

    ``group_by`` is ``model`` (the exact model string) or ``api_key`` (the provider that billed
    the call, and whether the host's key or the caller's own sent it).
    """
    return account_activity(stores, window=window, group_by=group_by)


@auth_router.get("/coupon", response_model=CouponStatus)
def get_coupon(stores: StoresDep) -> CouponStatus:
    """The host's allowance for the caller on the box's keys: its face, what is left, and the keys
    the caller sends on past it. What is left is derived from the ledgers, never a counter."""
    return coupon_status(stores)


class ApiKeyBody(RequestBody):
    """One provider key, never served back: a read names its last four characters alone."""

    secret: SecretStr


@auth_router.put("/api-keys/{provider}", response_model=AccountKey)
def put_api_key(
    provider: Annotated[str, Path(max_length=64)],
    body: ApiKeyBody,
    identity: IdentityDep,
    stores: StoresDep,
) -> AccountKey:
    """Hold the caller's own key for *provider*: its sends there go out on it, past the coupon.

    An unknown provider answers 422 ``unknown_provider``; a key that is not one unbroken string 422
    ``api_key_malformed``. The provider judges the key itself, at its first send."""
    require_capability(identity, CAMPAIGN_BUDGET_CAP, subject="put api key")
    return put_account_key(stores, provider, body.secret)


@auth_router.delete("/api-keys/{provider}", status_code=204)
def delete_api_key(
    provider: Annotated[str, Path(max_length=64)], identity: IdentityDep, stores: StoresDep
) -> Response:
    """Drop the caller's key for *provider*; its next send there draws the coupon again."""
    require_capability(identity, CAMPAIGN_BUDGET_CAP, subject="delete api key")
    remove_account_key(stores, provider)
    return Response(status_code=204)
