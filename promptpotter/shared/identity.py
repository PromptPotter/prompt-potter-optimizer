"""``IdentityContext`` — the sole identity carrier past the resolver seam, per ADR-0002 and the
Stage-0 framing in ADR-0003. Stage 1 replaces only the resolver, never this type."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from promptpotter.domain.identity import Issuer, TenantId, UserId, safe_name
from promptpotter.shared.errors import NotFoundError

logger = logging.getLogger(__name__)

# ── Host privilege is a CHANNEL, not an API capability ─────────────────────
# What the HOST admin (the person who runs the box) can do that a tenant owner cannot
# ships through the operator-admin channel (`presentation/admin_bot.py`) — the blocklist,
# `/grant`, `/revoke`, provider config — which ADR-0004 fixes as outbound-only and NOT an
# inbound API route. No `/commands/{kind}` verb is admin-only: an account on the box
# presses the same buttons the person running it does.
#
# `datasets.benchmarks.read` MUST NOT come back — it gated repo `datasets/`, already
# on the disk of anyone holding the install (`infrastructure/store/dataset_access.py`).

# Control-plane command capabilities — one per privilege level of the closed
# `/commands/{kind}` set (the dispatcher's `CAP_FOR_KIND` maps each kind to one).
# A tenant owner holds all of them over their own workspace; a delegated
# sub-principal an attenuated subset from the sealed grant store. Ladder and
# roles: ADR-0005 §§1,3-4.
CAMPAIGN_STEP_CAP = "campaign.step"
CAMPAIGN_RUN_CAP = "campaign.run"
CAMPAIGN_CREATE_CAP = "campaign.create"
CAMPAIGN_BUDGET_CAP = "campaign.budget"
CAMPAIGN_LIFECYCLE_CAP = "campaign.lifecycle"
CAMPAIGN_BABYSIT_CAP = "campaign.babysit"
CAMPAIGN_LOOKAHEAD_CAP = "campaign.lookahead"

# Short capability name → capability. The ONE place the ladder is enumerated: the owner
# set derives from it and the admin channel parses `/grant sub step,create`
# against it. Adding a capability = one line here, flowing to every consumer.
CAMPAIGN_CAP_BY_NAME: dict[str, str] = {
    "step": CAMPAIGN_STEP_CAP,
    "run": CAMPAIGN_RUN_CAP,
    "create": CAMPAIGN_CREATE_CAP,
    "budget": CAMPAIGN_BUDGET_CAP,
    "lifecycle": CAMPAIGN_LIFECYCLE_CAP,
    "babysit": CAMPAIGN_BABYSIT_CAP,
    # Spends the BOX's shared provider rate bucket rather than a campaign budget, so a host
    # with several tenants may want to withhold it — its own rung, never folded into babysit.
    "lookahead": CAMPAIGN_LOOKAHEAD_CAP,
}

# The full command-verb set a tenant owner holds — derived from the capability map so it
# can never drift from it. Sub-principals are carved as a subset; the dispatcher
# gate enforces the carve.
OWNER_COMMAND_CAPABILITIES = frozenset(CAMPAIGN_CAP_BY_NAME.values())


def capabilities_from_names(names: Iterable[str]) -> frozenset[str]:
    """Map short capability names to capabilities. An unknown name is a typo, rejected loudly — a
    silently dropped name is an UNDER-grant nobody notices."""
    caps: set[str] = set()
    for raw in names:
        name = raw.strip().lower()
        if not name:
            continue
        if name not in CAMPAIGN_CAP_BY_NAME:
            raise ValueError(
                f"unknown capability {name!r}; choose from {sorted(CAMPAIGN_CAP_BY_NAME)}"
            )
        caps.add(CAMPAIGN_CAP_BY_NAME[name])
    return frozenset(caps)


# The id the terminal identity carries in BOTH slots, and so the name of the tenant dir it writes
# (`projects/default/`) until a browser claim renames it — the only terminal marker a walk can read.
TERMINAL_IDENTITY_ID = "default"

# ── Entitlement: authenticated, but may it act? ────────────────────────────
# Completing OIDC mints an account AND entitles it — signing up IS the grant, and
# the free-tier spend ceiling is what bounds the stranger who takes it. The
# blocklist is the operator's revoke: a blocked account is a real, authenticated
# identity with an EMPTY capability set, and the dispatcher's existing gate is what
# makes that state real, so no surface needs a second check.
ACCESS_ACTIVE = "active"
ACCESS_BLOCKED = "blocked"


@dataclass(frozen=True)
class IdentityContext:
    user_id: UserId
    tenant_id: TenantId
    issuer: Issuer | None = None
    claims: Mapping[str, object] = field(default_factory=dict)
    capabilities: frozenset[str] = field(default_factory=frozenset)


def default_identity(
    tenant_id: str = TERMINAL_IDENTITY_ID, user_id: str = TERMINAL_IDENTITY_ID
) -> IdentityContext:
    """Stage-0 identity factory. A REGISTERED operator gets ``user_id == tenant_id``, so a terminal run
    lands in the same single workspace the authenticated web reads — one tenant per operator."""
    return IdentityContext(
        user_id=UserId(safe_name(user_id)),
        tenant_id=TenantId(safe_name(tenant_id)),
        issuer=None,
        claims={},
        capabilities=OWNER_COMMAND_CAPABILITIES,
    )


def has_capability(identity: IdentityContext, capability: str) -> bool:
    """The one predicate for "does this identity hold *capability*", so every capability decision has one
    shape. Dataset READS are not a capability decision — see ``infrastructure.store.dataset_access``."""
    return capability in identity.capabilities


def require_capability(identity: IdentityContext, capability: str, *, subject: str) -> None:
    """ENFORCE what :func:`has_capability` answers — the one denial, so a gated surface outside the
    command dispatcher refuses identically to one inside it. Absence raises 404, never 403: the
    existence-hiding posture of ADR-0005, which is why the message names nothing. ``subject`` names
    the act for the audit line only."""
    if has_capability(identity, capability):
        return
    logger.warning(
        "%s denied for principal %s (missing %s)",
        subject,
        acting_principal_id(identity),
        capability,
    )
    raise NotFoundError("Not found", code="not_found")


def acting_principal_id(identity: IdentityContext) -> str:
    """WHO is acting. For a delegated sub-principal (ADR-0005) this is its own ``claims["principal"]``,
    not the delegator whose tenant it acts in — so an audit trail names the real actor."""
    principal = identity.claims.get("principal")
    if isinstance(principal, str) and principal:
        return principal
    return str(identity.user_id)


def claim_email(identity: IdentityContext) -> str | None:
    raw = identity.claims.get("email")
    return raw if isinstance(raw, str) else None


def claim_access_state(identity: IdentityContext) -> str:
    """Entitlement as the web seam resolved it. Absent means this is not an OIDC session — the CLI and the
    ``PROMPTPOTTER_AUTH=off`` harness both run as the local operator, who is entitled by construction
    because no blocklist stands on that path."""
    raw = identity.claims.get("access_state")
    return raw if isinstance(raw, str) else ACCESS_ACTIVE


__all__ = [
    "ACCESS_ACTIVE",
    "ACCESS_BLOCKED",
    "CAMPAIGN_BABYSIT_CAP",
    "CAMPAIGN_BUDGET_CAP",
    "CAMPAIGN_CAP_BY_NAME",
    "CAMPAIGN_CREATE_CAP",
    "CAMPAIGN_LIFECYCLE_CAP",
    "CAMPAIGN_LOOKAHEAD_CAP",
    "CAMPAIGN_RUN_CAP",
    "CAMPAIGN_STEP_CAP",
    "OWNER_COMMAND_CAPABILITIES",
    "TERMINAL_IDENTITY_ID",
    "IdentityContext",
    "acting_principal_id",
    "capabilities_from_names",
    "claim_access_state",
    "claim_email",
    "default_identity",
    "has_capability",
    "require_capability",
]
