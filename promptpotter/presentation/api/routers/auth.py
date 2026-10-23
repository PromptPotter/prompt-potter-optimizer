"""Login / callback / logout run pre-auth and deliberately take no `IdentityDep`."""

from __future__ import annotations

import hmac
import logging
import secrets
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Path, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from promptpotter.application import account
from promptpotter.application.jobs.account_activity import (
    ActivityGroupBy,
    ActivityResponse,
    ActivityWindow,
    account_activity,
)
from promptpotter.application.jobs.quota import QuotaStatus, quota_status
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.config.settings import TERMS_VERSION, settings
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.identity.bundle import IdentityBundle
from promptpotter.infrastructure.identity.github import (
    GitHubTokenExchangeError,
)
from promptpotter.infrastructure.identity.google import (
    GoogleTokenExchangeError,
    ProviderIdentity,
)
from promptpotter.infrastructure.identity.migration import maybe_claim_default, registered_user_id
from promptpotter.infrastructure.identity.provider_config import SUPPORTED_PROVIDERS
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
from promptpotter.shared.identity import AccessState, IdentityContext, display_name

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/auth", tags=["Auth"])

# `provider.state.nonce` of the sign-in this browser started; the callback honours no other.
OAUTH_FLOW_COOKIE_NAME = "promptpotter_oauth_flow"
_OAUTH_FLOW_TTL_S = 600


class ConnectedAccount(StrictModel):
    """One OIDC provider bound to the active session."""

    provider: str
    email: str | None


class UserSettings(StrictModel):
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


def _require_provider_client(bundle: IdentityBundle, provider: str) -> tuple[str, Any]:
    """The server's own name for the provider beside its client: no URL text travels on."""
    if provider == "google":
        if bundle.google is None:
            raise NotFoundError(
                f"provider {provider!r} not configured",
                code="provider_not_configured",
                details={"provider": provider},
            )
        return "google", bundle.google
    if provider == "github":
        if bundle.github is None:
            raise NotFoundError(
                f"provider {provider!r} not configured",
                code="provider_not_configured",
                details={"provider": provider},
            )
        return "github", bundle.github
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


@auth_router.get("/login/{provider}")
async def login(
    request: Request,
    provider: Annotated[str, Path(pattern=r"^[a-z]+$", max_length=16)],
) -> RedirectResponse:
    name, client = _require_provider_client(_require_bundle(request), provider)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    if name == "google":
        url = client.authorize_url(state=state, nonce=nonce)
    else:
        url = client.authorize_url(state=state)
    response = RedirectResponse(url=url, status_code=307)
    _set_auth_cookie(
        response,
        request,
        key=OAUTH_FLOW_COOKIE_NAME,
        value=f"{name}.{state}.{nonce}",
        max_age=_OAUTH_FLOW_TTL_S,
    )
    return response


@auth_router.get("/callback/{provider}")
async def callback(
    request: Request,
    background: BackgroundTasks,
    provider: Annotated[str, Path(pattern=r"^[a-z]+$", max_length=16)],
) -> RedirectResponse:
    if provider not in SUPPORTED_PROVIDERS:
        return _redirect_with_error("signin_unavailable")
    bundle: IdentityBundle | None = getattr(request.app.state, "identity_bundle", None)
    if bundle is None:
        return _redirect_with_error("signin_unavailable")
    provider_client = bundle.google if provider == "google" else bundle.github
    if provider_client is None:
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
        if provider == "google":
            identity: ProviderIdentity = await bundle.google.exchange_code(  # type: ignore[union-attr]
                code=code, expected_nonce=nonce
            )
        else:
            identity = await bundle.github.exchange_code(code=code)  # type: ignore[union-attr]
    except (GoogleTokenExchangeError, GitHubTokenExchangeError, IDTokenInvalidError) as exc:
        logger.warning("OIDC code exchange failed for %s: %s", provider, exc)
        return _redirect_with_error("code_exchange_failed")

    # A blocked account still gets a session: the session seam empties its capabilities per request.
    user_id = derive_user_id(identity.issuer, identity.subject)
    access_state = resolve_access_state(identity.email, bundle)
    if access_state == "blocked":
        logger.info("Blocked account signed in: %s (%s)", identity.email, provider)
    elif _is_declared_host_admin(identity.email, identity.issuer):
        # The marker names the box's own tenant, so who writes it is declared, never inferred from arrival order.
        maybe_claim_default(
            projects_root=DEFAULT_PROJECTS_ROOT,
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
        arrival = account.sign_in(build_stores_from_identity(signed_in))
    except Exception:
        # Refused rather than let through unrecorded: the trail's promise is that no sign-in is missing.
        logger.exception("A %s sign-in could not be recorded", provider)
        bundle.session_store.delete(session_id)
        return _redirect_with_error("signin_unavailable")
    _announce_if_opened(background, arrival, signed_in)

    response = RedirectResponse(url="/", status_code=303)
    _set_auth_cookie(
        response, request, key=SESSION_COOKIE_NAME, value=session_id, max_age=60 * 60 * 24 * 7
    )
    response.delete_cookie(OAUTH_FLOW_COOKIE_NAME, path="/")
    return response


@auth_router.post("/logout")
async def logout(request: Request) -> JSONResponse:
    bundle = _require_bundle(request)
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        bundle.session_store.delete(session_id)
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


def _announce_new_account(
    *, email: str | None, name: str | None, user_id: str, access_state: AccessState
) -> None:
    who = email or f"(no email) {user_id}"
    # Read once the account is on disk, so the arriving account is inside the total.
    total = count_accounts(DEFAULT_PROJECTS_ROOT)
    notify_operator(
        f"New PromptPotter account: {who}\n"
        f"Accounts now: {total}\n"
        f"Access: {access_state}" + (f"\nRevoke it with:  /block {email}" if email else "")
    )
    # Fires regardless of `access_state`: a blocked account is still a contact worth keeping.
    forward_new_account_to_crm(email=email, name=name, user_id=user_id, account_count=total)


def _announce_if_opened(
    background: BackgroundTasks, arrival: account.Arrival, identity: IdentityContext
) -> None:
    """After the response, never on it."""
    if arrival.opened:
        background.add_task(
            _announce_new_account,
            email=identity.email,
            name=display_name(identity.email),
            user_id=str(identity.user_id),
            access_state=identity.access_state,
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
    arrival = account.arrive(stores)
    _announce_if_opened(background, arrival, identity)
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


class AcceptTermsBody(StrictModel):
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

    ``group_by`` is ``model`` (the exact model string) or ``api_key`` (who billed the call).
    """
    return account_activity(stores, window=window, group_by=group_by)
