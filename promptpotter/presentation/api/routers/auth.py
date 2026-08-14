"""``/auth/*`` — the Identity I/O kind (ADR-0002), NOT the ``/commands`` highway, so its per-user mutations ride
it directly. Login / callback / logout run pre-auth and deliberately take no ``IdentityDep``."""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, cast
from urllib.parse import quote_plus

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import Field

from promptpotter.application.jobs.quota import lifetime_ceiling_usd
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.jobs.spend import (
    iter_user_token_usage,
    record_cost_usd,
    sum_user_spend,
)
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
from promptpotter.infrastructure.identity.user import derive_user_id
from promptpotter.infrastructure.identity.verifier import IDTokenInvalidError
from promptpotter.infrastructure.store.user_store import ConsentRecord, count_accounts
from promptpotter.presentation.admin_bot import forward_new_account_to_crm, notify_operator
from promptpotter.presentation.api.deps import IdentityDep, StoresDep
from promptpotter.presentation.api.middleware.oidc import (
    SESSION_COOKIE_NAME,
    resolve_access_state,
)
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import (
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from promptpotter.shared.identity import ACCESS_BLOCKED, claim_access_state, claim_email

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/auth", tags=["Auth"])

_SUPPORTED_PROVIDERS = frozenset({"google", "github"})


class ConnectedAccount(StrictModel):
    """One OIDC provider currently bound to the active session.

    Stage-1 beta is single-account-per-user — the list is always length 1.
    The Clerk-style "connected accounts" surface in the webapp displays this
    list; multi-account linking ships post-M13.
    """

    provider: str
    email: str | None


class QuotaStatus(StrictModel):
    """Live snapshot of the abuse-limit knobs vs. usage.

    Drives the Security pane's quota card. ``*_max`` mirrors `user.json`
    so the operator can hand-edit limits; ``*_used`` is the live count
    that ``check_launch_quotas`` would gate against on the next launch.
    The spend pair is LIFETIME — spent-ever against the account's total
    ceiling — while the campaign pair stays per-day, because one is an
    allowance and the other an abuse limit.
    """

    spend_used_total_usd: float
    spend_budget_usd_total: float | None
    concurrent_running: int
    max_concurrent_cycles: int
    campaigns_today: int
    max_campaigns_per_day: int


class UserSettings(StrictModel):
    """Per-user preferences surfaced in Account → Preferences."""

    demo_mode_enabled: bool


class ActivityBucket(StrictModel):
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
    series_spend: dict[str, float] = Field(default_factory=dict)
    series_tokens: dict[str, int] = Field(default_factory=dict)
    series_requests: dict[str, int] = Field(default_factory=dict)


# The two closed sets the activity read is parameterized by. Declared HERE, on the response
# model, because the server owns them: they were a query-pattern regex plus a dict's keys, so
# the only NAMED version of either lived in the browser and could drift without a gate.
# `_WINDOW_SECONDS` below is keyed by `ActivityWindow` and is what makes the set closed.
ActivityWindow = Literal["15m", "30m", "1h", "3h", "1d", "2d", "1w", "1mo", "1y"]
ActivityGroupBy = Literal["model", "api_key"]

# Entitlement, closed server-side so the browser narrows off the generated type rather than
# re-declaring the members. `active` = signed up, which is the grant; `blocked` = the operator revoked
# this email, so the account exists and holds no capability. Resolved once at the session seam
# (`resolve_access_state`).
AccessState = Literal["active", "blocked"]


class ActivityResponse(StrictModel):
    """Time-bucketed spend / requests / tokens over the requested window."""

    window: ActivityWindow
    group_by: ActivityGroupBy
    since: float
    until: float
    buckets: list[ActivityBucket]
    # Stable label set in display order — frontend uses it to assign one
    # colour per label and iterate in the same order across all buckets.
    series_labels: list[str]
    total_spend_usd: float
    total_tokens: int
    total_requests: int


class MeResponse(StrictModel):
    """Current identity envelope. Returned by ``GET /auth/me`` only."""

    user_id: str
    tenant_id: str
    issuer: str | None
    email: str | None
    name: str | None
    provider: str | None
    connected_accounts: list[ConnectedAccount]
    available_providers: list[str]
    # RBAC permit set for this identity (sorted) — the honest permit envelope.
    # Empty for a first-time signup; the pinned developer carries the admin caps
    # (e.g. benchmark-dataset read). Server routes enforce them; the webapp reads
    # this to reflect, not to gate (the outer-loop dashboard boxes gate on data).
    capabilities: list[str]
    # Entitlement gate input, the sibling of the consent gate below: a `blocked` account is signed in
    # and holds nothing, so the webapp shows the holding screen instead of the app. Reflecting, not
    # gating — the server already refuses a blocked account's commands at the dispatcher.
    access_state: AccessState
    # Consent gate inputs. ``terms_version`` is the live required version;
    # ``terms_accepted_version`` is what this user last accepted (None = never).
    # The webapp blocks the app while the two differ. The accepted timestamp
    # stays server-side in user.json — the frontend needs only the version match.
    terms_version: str
    terms_accepted_version: str | None


def _is_declared_host_admin(email: str | None) -> bool:
    """May this sign-in claim the box? Answered from `HOST_ADMIN_EMAIL` alone. Unset means nobody may,
    which is a refusal rather than a fallback — the alternative, "first one in wins", is the thing that
    stopped being safe the moment signing up became the grant."""
    declared = settings.HOST_ADMIN_EMAIL.strip().lower()
    return bool(declared) and (email or "").strip().lower() == declared


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
    """Bounce a failed callback to the sign-in surface with a query-param error. The consent screen browser-navigates here,
    so raising would dump raw JSON into the tab — a 303 lets the React modal render an inline banner."""
    qs = f"auth_error={code}"
    if email:
        qs += f"&email={quote_plus(email)}"
    return RedirectResponse(url=f"/?{qs}", status_code=303)


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

    # The blocklist is NOT consulted here. Anyone completing OIDC gets an account and is entitled by
    # that alone; the blocklist is resolved per-request at the session seam (`resolve_access_state`),
    # which hands a blocked account an empty capability set. Rejecting at the callback is what left an
    # interested stranger with nothing but an error banner and no record we could later act on.
    user_id = derive_user_id(identity.issuer, identity.subject)
    access_state = resolve_access_state(identity.email, bundle)
    if access_state == ACCESS_BLOCKED:
        logger.info("Blocked account signed in: %s (%s)", identity.email, provider)
    elif _is_declared_host_admin(identity.email):
        # The marker is what `_session_capabilities` reads to grant ADMIN_CAPABILITIES, so WHO may
        # write it must be DECLARED. Entitlement used to stand in for that and cannot any more: now
        # that signing up entitles, an inferred claim would hand the box to whoever arrived first.
        maybe_claim_default(
            projects_root=DEFAULT_PROJECTS_ROOT,
            user_id=str(user_id),
            marker_path=bundle.paths.default_claim_marker,
        )
    elif registered_user_id(bundle.paths.default_claim_marker) is None:
        # A state the box can enter, so it says so: unclaimed AND undeclared means the terminal keeps
        # resolving the `default` tenant while every browser session resolves its own, and the two
        # workspaces drift apart in silence.
        logger.warning(
            "Sign-in by %s did not claim this box: HOST_ADMIN_EMAIL is unset, so no browser identity "
            "may write the claim marker. Terminal and browser will resolve DIFFERENT tenants until it is.",
            identity.email,
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
async def me(request: Request, identity: IdentityDep, stores: StoresDep) -> MeResponse:
    """Identity envelope + the data the account modal + consent gate need.

    `connected_accounts` is a single-entry list at Stage 1 (one provider
    per session). `available_providers` is configured-minus-connected so
    the "+ Connect account" affordance only surfaces real targets.
    `terms_*` drive the post-auth consent gate (read from `user.json`);
    `access_state` drives the entitlement gate in front of it.

    This is also where a new account first becomes real on disk, so it is where both
    new-account notices fire — the operator's and the CRM's. See the comment on
    `is_new_account`.
    """
    bundle = _require_bundle(request)
    claims = cast(dict[str, Any], identity.claims)
    email = cast(str | None, claims.get("email"))
    provider = cast(str | None, claims.get("provider"))
    access_state = cast(AccessState, claim_access_state(identity))
    name = _display_name_from(email)
    connected = [ConnectedAccount(provider=provider, email=email)] if provider else []
    configured = set(bundle.config.configured)
    available = sorted(configured - {provider}) if provider else sorted(configured)
    # `load() is None` is the single moment an account comes into being: `get_or_create` writes
    # `user.json` on the next line and every later request finds it. The notice fires HERE rather
    # than at the OIDC callback because that makes it exactly-once per account instead of once per
    # sign-in — a pending user who retries login would otherwise re-notify every time.
    is_new_account = stores.users.load() is None
    user = stores.users.get_or_create(
        user_id=str(identity.user_id),
        tenant_id=str(identity.tenant_id),
        email=email,
    )
    if is_new_account:
        who = email or f"(no email) {identity.user_id}"
        # Counted AFTER `get_or_create`, so the arriving account is inside the total the operator reads.
        # The count is the headline of both notices: with signup as the grant, "how many are on the free
        # tier" is the number that decides whether anything needs doing, and no surface held it before.
        total = count_accounts(DEFAULT_PROJECTS_ROOT)
        notify_operator(
            f"New PromptPotter account: {who}\n"
            f"Accounts now: {total}\n"
            f"Access: {access_state}" + (f"\nRevoke it with:  /block {email}" if email else "")
        )
        # Entitlement and contact record are separate questions: a blocked account is still a real
        # person worth keeping. Both notices fire regardless of `access_state`, and both are
        # best-effort — neither may fail the request that just created the account.
        forward_new_account_to_crm(
            email=email, name=name, user_id=str(identity.user_id), account_count=total
        )
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
def quota_status(request: Request, stores: StoresDep) -> QuotaStatus:
    """Live quota snapshot for the Security pane.

    Spend sums ``TokenUsageRecord`` cost across the account's WHOLE ledger via
    ``sum_user_spend`` — uncapped, so an over-budget account shows the true
    overage rather than clamping the display to the cap; concurrent + daily
    counts ride the `JobRegistry`. The served ceiling is the RESOLVED one
    (``lifetime_ceiling_usd``), never the raw nullable override, so the browser
    is not left joining a null against an install default to learn its own cap.
    """
    user = stores.users.get_or_create(
        user_id=str(stores.identity.user_id),
        tenant_id=str(stores.identity.tenant_id),
        email=claim_email(stores.identity),
    )
    job_registry: JobRegistry | None = getattr(request.app.state, "job_registry", None)
    if job_registry is None:
        raise ServiceUnavailableError(
            "job registry not initialised", code="job_registry_unavailable"
        )
    running = job_registry.list_running(user_id=user.user_id)
    today = job_registry.list_created_today(user_id=user.user_id)

    # Lifetime spend straight from the ledger — uncapped on purpose, so an over-budget
    # account reports the true overage instead of clamping to the cap (the cap itself
    # rides `spend_budget_usd_total` below).
    spent = sum_user_spend(stores=stores, since=0.0, until=datetime.now(UTC).timestamp())

    return QuotaStatus(
        spend_used_total_usd=round(spent, 6),
        spend_budget_usd_total=lifetime_ceiling_usd(user=user, stores=stores),
        concurrent_running=len(running),
        max_concurrent_cycles=user.max_concurrent_cycles,
        campaigns_today=len(today),
        max_campaigns_per_day=user.max_campaigns_per_day,
    )


@auth_router.get("/user-settings", response_model=UserSettings)
def get_user_settings(stores: StoresDep) -> UserSettings:
    """Read the current user's preferences (Account → Preferences)."""
    user = stores.users.get_or_create(
        user_id=str(stores.identity.user_id),
        tenant_id=str(stores.identity.tenant_id),
        email=claim_email(stores.identity),
    )
    return UserSettings(demo_mode_enabled=user.demo_mode_enabled)


@auth_router.patch("/user-settings", response_model=UserSettings)
def patch_user_settings(body: UserSettings, stores: StoresDep) -> UserSettings:
    """Persist a preference change. A user-account mutation (not a campaign
    command), so it rides the auth router alongside session writes rather than
    the ``/commands`` highway."""
    user = stores.users.get_or_create(
        user_id=str(stores.identity.user_id),
        tenant_id=str(stores.identity.tenant_id),
        email=claim_email(stores.identity),
    )
    stores.users.save(user.model_copy(update={"demo_mode_enabled": body.demo_mode_enabled}))
    return UserSettings(demo_mode_enabled=body.demo_mode_enabled)


class AcceptTermsBody(StrictModel):
    """The version the client is accepting — must equal the live TERMS_VERSION."""

    version: str


class TermsConsent(StrictModel):
    """Consent state echoed back after an accept (same fields the gate reads on
    ``/me``). The accepted timestamp stays server-side in ``user.json``."""

    terms_version: str
    terms_accepted_version: str | None


@auth_router.post("/accept-terms", response_model=TermsConsent)
def accept_terms(body: AcceptTermsBody, stores: StoresDep) -> TermsConsent:
    """Record the current user's acceptance of the Terms — the provable consent
    artifact the legal clauses depend on. A per-user identity mutation (like
    user-settings), not a campaign command, so it rides the auth router rather
    than the ``/commands`` highway. The accepted version must equal the live
    ``TERMS_VERSION``; a stale version is rejected so the gate re-prompts against
    current text. The timestamp is server-stamped — never trust the client clock
    for a record that has to hold up.
    """
    if body.version != TERMS_VERSION:
        raise ConflictError(
            "Terms version is out of date — reload to accept the current terms.",
            code="terms_version_stale",
            details={"expected": TERMS_VERSION, "received": body.version},
        )
    user = stores.users.get_or_create(
        user_id=str(stores.identity.user_id),
        tenant_id=str(stores.identity.tenant_id),
        email=claim_email(stores.identity),
    )
    record = ConsentRecord(version=TERMS_VERSION, accepted_at=utcnow_iso())
    stores.users.save(user.model_copy(update={"terms_accepted": record}))
    return TermsConsent(terms_version=TERMS_VERSION, terms_accepted_version=TERMS_VERSION)


@auth_router.get("/activity", response_model=ActivityResponse)
def activity(
    stores: StoresDep,
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

    for rec in iter_user_token_usage(stores=stores, since=since, until=until):
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
    """Derive the API-key axis from a model string, by the ``<provider>/<model>`` convention. A string with no slash falls
    back to ``unknown``."""
    if "/" in model:
        return model.split("/", 1)[0]
    if ":" in model:  # e.g. "groq:openai/gpt-oss-120b"
        return model.split(":", 1)[0]
    return "unknown"


__all__ = [
    "ActivityBucket",
    "ActivityResponse",
    "ConnectedAccount",
    "MeResponse",
    "QuotaStatus",
    "auth_router",
]
