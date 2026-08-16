"""``IdentityContext`` — the sole identity carrier past the resolver seam, per ADR-0002 and the
Stage-0 framing in ADR-0003. Stage 1 replaces only the resolver, never this type."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from promptpotter.domain.identity import Issuer, TenantId, UserId, safe_name

# ── Tier 1a: host-admin ────────────────────────────────────────────────────
# The sole definition of "what the HOST admin (the person who runs the box) can
# do that a tenant owner cannot". Adding an admin power = adding it to this
# frozenset; nowhere else.
#
# Most host-admin power ships through the operator-admin CHANNEL
# (`presentation/admin_bot.py`), which ADR-0004 fixes as outbound-only and NOT an
# inbound API route. This tier holds what that channel cannot express: an admin-only
# power that is a COMMAND against a running campaign.
#
# `datasets.benchmarks.read` MUST NOT come back — it gated repo `datasets/`, already
# on the disk of anyone holding the install (`infrastructure/store/dataset_access.py`).

# Host-admin rather than a campaign tier because it spends the BOX's shared provider
# rate bucket, not a campaign budget. Not `campaign.babysit` either: it taints nothing.
SCORING_SAMPLE_LOOKAHEAD_CAP = "scoring.sample_lookahead"

ADMIN_CAPABILITIES: frozenset[str] = frozenset({SCORING_SAMPLE_LOOKAHEAD_CAP})

# Control-plane command capabilities — one per privilege tier of the closed
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

# Short tier name → capability. The ONE place the ladder is enumerated: the owner
# set derives from it and the admin channel parses `/grant sub step,create`
# against it. Adding a tier = one line here, flowing to every consumer.
CAMPAIGN_CAP_BY_TIER: dict[str, str] = {
    "step": CAMPAIGN_STEP_CAP,
    "run": CAMPAIGN_RUN_CAP,
    "create": CAMPAIGN_CREATE_CAP,
    "budget": CAMPAIGN_BUDGET_CAP,
    "lifecycle": CAMPAIGN_LIFECYCLE_CAP,
    "babysit": CAMPAIGN_BABYSIT_CAP,
}

# The full command-verb set a tenant owner holds — derived from the tier map so it
# can never drift from it. Sub-principals are carved as a subset; the dispatcher
# gate enforces the carve. Kept separate from `ADMIN_CAPABILITIES` — owning a
# tenant is not running the box.
OWNER_COMMAND_CAPABILITIES = frozenset(CAMPAIGN_CAP_BY_TIER.values())


def capabilities_from_tiers(tiers: Iterable[str]) -> frozenset[str]:
    """Map short tier names to capabilities. An unknown name is a typo, rejected loudly — a silently
    dropped tier is an UNDER-grant nobody notices."""
    caps: set[str] = set()
    for tier in tiers:
        name = tier.strip().lower()
        if not name:
            continue
        if name not in CAMPAIGN_CAP_BY_TIER:
            raise ValueError(
                f"unknown capability tier {name!r}; choose from {sorted(CAMPAIGN_CAP_BY_TIER)}"
            )
        caps.add(CAMPAIGN_CAP_BY_TIER[name])
    return frozenset(caps)


PROMPTPOTTER_ADMIN_ENV = "PROMPTPOTTER_ADMIN"

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


def _admin_caps_from_env() -> frozenset[str]:
    """WHO is host-admin at Stage 0 — an env flag, sound ONLY on a single-operator box. The OIDC path
    pins admin to a registered identity: **never merge the two predicates**, the threat models differ."""
    if os.environ.get(PROMPTPOTTER_ADMIN_ENV, "").strip() == "1":
        return ADMIN_CAPABILITIES
    return frozenset()


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
        capabilities=OWNER_COMMAND_CAPABILITIES | _admin_caps_from_env(),
    )


def has_capability(identity: IdentityContext, capability: str) -> bool:
    """The one predicate for "does this identity hold *capability*", so every capability decision has one
    shape. Dataset READS are not a capability decision — see ``infrastructure.store.dataset_access``."""
    return capability in identity.capabilities


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
    "ADMIN_CAPABILITIES",
    "CAMPAIGN_BABYSIT_CAP",
    "CAMPAIGN_BUDGET_CAP",
    "CAMPAIGN_CAP_BY_TIER",
    "CAMPAIGN_CREATE_CAP",
    "CAMPAIGN_LIFECYCLE_CAP",
    "CAMPAIGN_RUN_CAP",
    "CAMPAIGN_STEP_CAP",
    "OWNER_COMMAND_CAPABILITIES",
    "SCORING_SAMPLE_LOOKAHEAD_CAP",
    "TERMINAL_IDENTITY_ID",
    "IdentityContext",
    "capabilities_from_tiers",
    "claim_access_state",
    "claim_email",
    "default_identity",
    "has_capability",
]
