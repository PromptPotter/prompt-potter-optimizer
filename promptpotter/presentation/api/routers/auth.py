"""``/auth/*`` — OIDC sign-in surface.

Seven routes:

* ``GET /auth/providers`` — list configured providers (drives the login page).
* ``GET /auth/login/{provider}`` — issue a state token, redirect to the provider's consent screen.
* ``GET /auth/callback/{provider}`` — verify the auth code, mint a server-side session, set the opaque cookie, redirect to ``/``.
* ``POST /auth/logout`` — delete the session + clear the cookie.
* ``GET /auth/me`` — current identity envelope (401 when no session).
* ``GET /auth/quota-status`` — Security pane: live quota knobs + today's usage.
* ``GET /auth/activity`` — Activity pane: time-bucketed spend / requests / tokens.

The auth router intentionally does NOT use ``IdentityDep`` for the
login / callback / providers / logout routes — those run pre-auth. Only
``/auth/me`` uses the dep, and 401 is the expected pre-sign-in answer.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime
from typing import Annotated, Any, cast
from urllib.parse import quote_plus

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from promptpotter.application.jobs import JobRegistry
from promptpotter.application.jobs.quota import effective_spend_cap_usd
from promptpotter.application.jobs.spend import (
    iter_user_token_usage,
    record_cost_usd,
    start_of_utc_day,
    sum_user_spend,
)
from promptpotter.infrastructure.identity.allowlist import check_allowlist
from promptpotter.infrastructure.identity.bundle import IdentityBundle
from promptpotter.infrastructure.identity.github import (
    GitHubTokenExchangeError,
)
from promptpotter.infrastructure.identity.google import (
    GoogleTokenExchangeError,
    ProviderIdentity,
)
from promptpotter.infrastructure.identity.migration import maybe_claim_default
from promptpotter.infrastructure.identity.user import derive_user_id
from promptpotter.infrastructure.identity.verifier import IDTokenInvalidError
from promptpotter.infrastructure.store import Stores
from promptpotter.infrastructure.store.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.presentation.api.deps import IdentityDep, StoreDep
from promptpotter.presentation.api.middleware import SESSION_COOKIE_NAME
from promptpotter.shared.errors import NotFoundError, ServiceUnavailableError

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/auth", tags=["Auth"])

_SUPPORTED_PROVIDERS = frozenset({"google", "github"})


class ProvidersResponse(BaseModel):
    """Which providers the operator has configured. Drives the login page."""

    providers: list[str]


class ConnectedAccount(BaseModel):
    """One OIDC provider currently bound to the active session.

    Stage-1 beta is single-account-per-user — the list is always length 1.
    The Clerk-style "connected accounts" surface in the webapp displays this
    list; multi-account linking ships post-M13.
    """

    provider: str
    email: str | None


class QuotaStatus(BaseModel):
    """Live snapshot of the abuse-limit knobs vs. today's usage.

    Drives the Security pane's quota card. ``*_max`` mirrors `user.json`
    so the operator can hand-edit limits; ``*_used`` is the live count
    that ``check_launch_quotas`` would gate against on the next launch.
    """

    spend_used_today_usd: float
    spend_budget_usd_daily: float | None
    concurrent_running: int
    max_concurrent_cycles: int
    campaigns_today: int
    max_campaigns_per_day: int


class UserSettings(BaseModel):
    """Per-user preferences surfaced in Account → Preferences."""

    demo_mode_enabled: bool


class ActivityBucket(BaseModel):
    """One bucket of the Activity pane's three stacked bar charts.

    ``series`` maps each colour-axis label (model name or provider slug)
    to its per-metric value in this bucket; the frontend uses it to draw
    stacked bars. ``spend_usd`` / ``tokens`` / ``requests`` are the
    bucket totals (= sum of series values), kept for empty-state checks.
    """

    ts: float  # epoch seconds at the bucket's leading edge
    spend_usd: float
    tokens: int
    requests: int
    series_spend: dict[str, float] = {}
    series_tokens: dict[str, int] = {}
    series_requests: dict[str, int] = {}


class ActivityResponse(BaseModel):
    """Time-bucketed spend / requests / tokens over the requested window."""

    window: str
    group_by: str  # "model" | "api_key"
    since: float
    until: float
    buckets: list[ActivityBucket]
    # Stable label set in display order — frontend uses it to assign one
    # colour per label and iterate in the same order across all buckets.
    series_labels: list[str]
    total_spend_usd: float
    total_tokens: int
    total_requests: int


class MeResponse(BaseModel):
    """Current identity envelope. Returned by ``GET /auth/me`` only."""

    user_id: str
    tenant_id: str
    issuer: str | None
    email: str | None
    name: str | None
    provider: str | None
    connected_accounts: list[ConnectedAccount]
    available_providers: list[str]


def _require_bundle(request: Request) -> IdentityBundle:
    bundle: IdentityBundle | None = getattr(request.app.state, "identity_bundle", None)
    if bundle is None:
        raise ServiceUnavailableError(
            "identity backend not initialised", code="identity_not_initialised"
        )
    return bundle


def _require_provider_client(bundle: IdentityBundle, provider: str) -> Any:
    if provider == "google":
        if bundle.google is None:
            raise NotFoundError(
                f"provider {provider!r} not configured",
                code="provider_not_configured",
                details={"provider": provider},
            )
        return bundle.google
    if provider == "github":
        if bundle.github is None:
            raise NotFoundError(
                f"provider {provider!r} not configured",
                code="provider_not_configured",
                details={"provider": provider},
            )
        return bundle.github
    raise NotFoundError(
        f"unknown provider {provider!r}", code="provider_unknown", details={"provider": provider}
    )


def _redirect_with_error(code: str, *, email: str | None = None) -> RedirectResponse:
    """Bounce a failed callback to the sign-in surface with a query-param error.

    Google's consent screen browser-navigates straight to /auth/callback/...;
    raising HTTPException there dumps raw JSON to the tab. Industry-standard
    fix: 303 to the app root (`/`) with `?auth_error=<code>` so the
    React modal can render a friendly inline banner.
    """
    qs = f"auth_error={code}"
    if email:
        qs += f"&email={quote_plus(email)}"
    return RedirectResponse(url=f"/?{qs}", status_code=303)


@auth_router.get("/providers", response_model=ProvidersResponse)
async def list_providers(request: Request) -> ProvidersResponse:
    bundle = _require_bundle(request)
    return ProvidersResponse(providers=list(bundle.config.configured))


@auth_router.get("/login/{provider}")
async def login(
    request: Request,
    provider: Annotated[str, Path(pattern=r"^[a-z]+$", max_length=16)],
) -> RedirectResponse:
    if provider not in _SUPPORTED_PROVIDERS:
        raise NotFoundError(
            f"unknown provider {provider!r}",
            code="provider_unknown",
            details={"provider": provider},
        )
    bundle = _require_bundle(request)
    client = _require_provider_client(bundle, provider)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    bundle.register_state(state, provider, nonce)
    if provider == "google":
        url = client.authorize_url(state=state, nonce=nonce)
    else:
        url = client.authorize_url(state=state)
    return RedirectResponse(url=url, status_code=307)


@auth_router.get("/callback/{provider}")
async def callback(
    request: Request,
    provider: Annotated[str, Path(pattern=r"^[a-z]+$", max_length=16)],
) -> RedirectResponse:
    # All failure paths in this handler redirect to /?auth_error=<code>
    # instead of raising HTTPException — this route is browser-navigated by
    # the provider's consent screen, so a JSON 4xx renders as raw text in the
    # tab. The sibling routes (/login, /providers, /me, /logout) keep raising
    # since they're called by fetch() and want JSON.
    if provider not in _SUPPORTED_PROVIDERS:
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

    pending = bundle.consume_state(state)
    if pending is None or pending.provider != provider:
        return _redirect_with_error("state_invalid_or_expired")

    try:
        if provider == "google":
            identity: ProviderIdentity = await bundle.google.exchange_code(  # type: ignore[union-attr]
                code=code, expected_nonce=pending.nonce
            )
        else:
            identity = await bundle.github.exchange_code(code=code)  # type: ignore[union-attr]
    except (GoogleTokenExchangeError, GitHubTokenExchangeError, IDTokenInvalidError) as exc:
        logger.warning("OIDC code exchange failed for %s: %s", provider, exc)
        return _redirect_with_error("code_exchange_failed")

    decision = check_allowlist(bundle.paths.allowlist, identity.email)
    if not decision.allowed:
        logger.info("Allowlist rejected %s (%s): %s", identity.email, provider, decision.reason)
        return _redirect_with_error("not_allowlisted", email=identity.email)

    user_id = derive_user_id(identity.issuer, identity.subject)
    maybe_claim_default(
        projects_root=DEFAULT_PROJECTS_ROOT,
        user_id=str(user_id),
        marker_path=bundle.paths.default_claim_marker,
    )

    session_id, _data = bundle.session_store.create(
        user_id=str(user_id),
        tenant_id=str(user_id),
        issuer=identity.issuer,
        subject=identity.subject,
        email=identity.email,
        provider=identity.provider,
    )

    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        max_age=60 * 60 * 24 * 7,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/",
    )
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


@auth_router.get("/me", response_model=MeResponse)
async def me(request: Request, identity: IdentityDep) -> MeResponse:
    """Identity envelope + the data the account modal needs.

    `connected_accounts` is a single-entry list at Stage 1 (one provider
    per session). `available_providers` is configured-minus-connected so
    the "+ Connect account" affordance only surfaces real targets.
    """
    bundle = _require_bundle(request)
    claims = cast(dict[str, Any], identity.claims)
    email = cast(str | None, claims.get("email"))
    provider = cast(str | None, claims.get("provider"))
    name = _display_name_from(email)
    connected = [ConnectedAccount(provider=provider, email=email)] if provider else []
    configured = set(bundle.config.configured)
    available = sorted(configured - {provider}) if provider else sorted(configured)
    return MeResponse(
        user_id=str(identity.user_id),
        tenant_id=str(identity.tenant_id),
        issuer=str(identity.issuer) if identity.issuer else None,
        email=email,
        name=name,
        provider=provider,
        connected_accounts=connected,
        available_providers=available,
    )


def _display_name_from(email: str | None) -> str | None:
    """Stage-1 fallback — session schema doesn't persist the OIDC ``name``
    claim yet, so the modal uses the email local-part as a placeholder."""
    if not email or "@" not in email:
        return None
    local = email.split("@", 1)[0]
    return local.replace(".", " ").replace("_", " ").title() or None


_WINDOW_SECONDS: dict[str, int] = {
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
    "3h": 3 * 60 * 60,
    "1d": 24 * 60 * 60,
    "2d": 2 * 24 * 60 * 60,
    "1w": 7 * 24 * 60 * 60,
    "1mo": 30 * 24 * 60 * 60,
    "1y": 365 * 24 * 60 * 60,
}
_N_BUCKETS = 30


@auth_router.get("/quota-status", response_model=QuotaStatus)
async def quota_status(request: Request, store: StoreDep) -> QuotaStatus:
    """Live quota snapshot for the Security pane.

    Today's spend sums ``TokenUsageRecord`` cost from the canonical ledger since
    UTC midnight (via ``effective_spend_cap_usd`` when a cap is set, else
    ``sum_user_spend`` directly); concurrent + daily counts ride the
    `JobRegistry`.
    """
    user = store.users.get_or_create(
        user_id=str(store.identity.user_id),
        tenant_id=str(store.identity.tenant_id),
        email=_claim_email(store),
    )
    job_registry: JobRegistry | None = getattr(request.app.state, "job_registry", None)
    if job_registry is None:
        raise ServiceUnavailableError(
            "job registry not initialised", code="job_registry_unavailable"
        )
    running = job_registry.list_running(user_id=user.user_id)
    today = job_registry.list_created_today(user_id=user.user_id)

    # The composer subtracts daily-spent from `daily_cap`; reuse it to
    # avoid duplicating the dashboard-walk logic, then derive used = cap - remaining.
    if user.spend_budget_usd_daily is not None:
        remaining = effective_spend_cap_usd(
            requested_cap_usd=user.spend_budget_usd_daily,
            user=user,
            stores=store,
        )
        spent = max(0.0, user.spend_budget_usd_daily - float(remaining or 0.0))
    else:
        # No daily cap → still surface today's spend from the ledger (same source
        # the cap path uses via effective_spend_cap_usd → sum_user_spend).
        spent = sum_user_spend(
            store=store, since=start_of_utc_day(), until=datetime.now(UTC).timestamp()
        )

    return QuotaStatus(
        spend_used_today_usd=round(spent, 6),
        spend_budget_usd_daily=user.spend_budget_usd_daily,
        concurrent_running=len(running),
        max_concurrent_cycles=user.max_concurrent_cycles,
        campaigns_today=len(today),
        max_campaigns_per_day=user.max_campaigns_per_day,
    )


@auth_router.get("/user-settings", response_model=UserSettings)
async def get_user_settings(store: StoreDep) -> UserSettings:
    """Read the current user's preferences (Account → Preferences)."""
    user = store.users.get_or_create(
        user_id=str(store.identity.user_id),
        tenant_id=str(store.identity.tenant_id),
        email=_claim_email(store),
    )
    return UserSettings(demo_mode_enabled=user.demo_mode_enabled)


@auth_router.patch("/user-settings", response_model=UserSettings)
async def patch_user_settings(body: UserSettings, store: StoreDep) -> UserSettings:
    """Persist a preference change. A user-account mutation (not a campaign
    command), so it rides the auth router alongside session writes rather than
    the ``/commands`` highway."""
    user = store.users.get_or_create(
        user_id=str(store.identity.user_id),
        tenant_id=str(store.identity.tenant_id),
        email=_claim_email(store),
    )
    store.users.save(user.model_copy(update={"demo_mode_enabled": body.demo_mode_enabled}))
    return UserSettings(demo_mode_enabled=body.demo_mode_enabled)


@auth_router.get("/activity", response_model=ActivityResponse)
async def activity(
    store: StoreDep,
    window: Annotated[str, Query(pattern=r"^(15m|30m|1h|3h|1d|2d|1w|1mo|1y)$")] = "1d",
    group_by: Annotated[str, Query(pattern=r"^(model|api_key)$")] = "model",
) -> ActivityResponse:
    """Time-bucketed spend / requests / tokens over the requested window.

    Walks every per-cycle ``.runtime/ledger.jsonl`` under the user's
    `campaigns/*/cycles/*/` and projects ``TokenUsageRecord`` rows whose
    `timestamp` lands in the window onto ``_N_BUCKETS`` evenly-spaced
    bins. ``cost_usd`` may be null on disk (Groq doesn't return wire
    cost); we fall back to ``shared.spend.lookup_rate`` × tokens so
    historical spend isn't silently zero.

    ``group_by`` selects the colour axis: ``model`` = exact model string,
    ``api_key`` = derived provider slug (``openai`` / ``groq`` /
    ``anthropic`` / ``openrouter``).
    """
    span_s = _WINDOW_SECONDS[window]
    until = datetime.now(UTC).timestamp()
    since = until - span_s
    bucket_width = span_s / _N_BUCKETS

    buckets: list[ActivityBucket] = [
        ActivityBucket(
            ts=since + i * bucket_width,
            spend_usd=0.0,
            tokens=0,
            requests=0,
            series_spend={},
            series_tokens={},
            series_requests={},
        )
        for i in range(_N_BUCKETS)
    ]
    total_spend = 0.0
    total_tokens = 0
    total_requests = 0
    label_order: dict[str, int] = {}  # insertion-order set

    for rec in iter_user_token_usage(store=store, since=since, until=until):
        idx = min(_N_BUCKETS - 1, max(0, int((rec["ts"] - since) / bucket_width)))
        b = buckets[idx]
        tokens = rec["tokens"]
        model = rec.get("model") or "unknown"
        kind = rec.get("kind") or "optimizer"
        cost = record_cost_usd(rec)
        # Tag backend rows so optimizer + backend never collide in the legend
        # even when they share a provider slug (Groq-hosted openai/gpt-oss-* +
        # OpenRouter-backed TermNorm both prefix with provider names).
        if group_by == "model":
            label = f"{model} (backend)" if kind == "backend" else model
        else:
            base = _provider_from_model(model)
            label = f"{base} (backend)" if kind == "backend" else base
        if label not in label_order:
            label_order[label] = len(label_order)
        b.series_spend[label] = b.series_spend.get(label, 0.0) + cost
        b.series_tokens[label] = b.series_tokens.get(label, 0) + tokens
        b.series_requests[label] = b.series_requests.get(label, 0) + 1
        buckets[idx] = ActivityBucket(
            ts=b.ts,
            spend_usd=b.spend_usd + cost,
            tokens=b.tokens + tokens,
            requests=b.requests + 1,
            series_spend=b.series_spend,
            series_tokens=b.series_tokens,
            series_requests=b.series_requests,
        )
        total_spend += cost
        total_tokens += tokens
        total_requests += 1

    return ActivityResponse(
        window=window,
        group_by=group_by,
        since=since,
        until=until,
        buckets=buckets,
        series_labels=list(label_order.keys()),
        total_spend_usd=round(total_spend, 6),
        total_tokens=total_tokens,
        total_requests=total_requests,
    )


def _provider_from_model(model: str) -> str:
    """Derive the API-key axis from a model string.

    Convention: most rate-keyed model strings here are ``"<provider>/<model>"``
    (e.g. ``openai/gpt-oss-120b``, ``anthropic/claude-3-5-sonnet``). A
    string with no slash falls back to ``"unknown"``.
    """
    if "/" in model:
        return model.split("/", 1)[0]
    if ":" in model:  # e.g. "groq:openai/gpt-oss-120b"
        return model.split(":", 1)[0]
    return "unknown"


def _claim_email(store: Stores) -> str | None:
    raw = store.identity.claims.get("email")
    return raw if isinstance(raw, str) else None


__all__ = [
    "ActivityBucket",
    "ActivityResponse",
    "ConnectedAccount",
    "MeResponse",
    "ProvidersResponse",
    "QuotaStatus",
    "auth_router",
]
