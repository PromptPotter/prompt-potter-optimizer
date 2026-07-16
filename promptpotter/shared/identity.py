"""``IdentityContext`` — the sole identity carrier past the resolver seam.

Per `docs/adr/0002-identity-foundation.md` + the Stage-0 framing in
`docs/adr/0003-spend-and-tenancy.md`. Five fields:

* :attr:`IdentityContext.user_id` — :class:`~promptpotter.domain.identity.UserId`
* :attr:`IdentityContext.tenant_id` — :class:`~promptpotter.domain.identity.TenantId`
* :attr:`IdentityContext.issuer` — :class:`~promptpotter.domain.identity.Issuer` or ``None``
* :attr:`IdentityContext.claims` — read-only ``Mapping[str, object]`` of OIDC/SCIM claims
* :attr:`IdentityContext.capabilities` — RBAC permit set (SCIM-named at Stage 1+)

Stage 0 ships :func:`default_identity` (auth-off single-operator) as the
sole construction route. Stage 1 (M12) replaces only the resolver, never
this type.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from promptpotter.domain.identity import Issuer, TenantId, UserId, safe_name

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
# gate enforces the carve.
OWNER_COMMAND_CAPABILITIES = frozenset(CAMPAIGN_CAP_BY_TIER.values())


def capabilities_from_tiers(tiers: Iterable[str]) -> frozenset[str]:
    """Map short tier names to capabilities; unknown names raise ``ValueError``.

    The admin channel uses this to turn an operator's ``step,create`` into the
    grant's capability set — an unknown tier is a typo, rejected loudly, not a
    silently-dropped (and thus under-)grant.
    """
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


@dataclass(frozen=True)
class IdentityContext:
    """Frozen identity carrier — see module docstring for field semantics."""

    user_id: UserId
    tenant_id: TenantId
    issuer: Issuer | None = None
    claims: Mapping[str, object] = field(default_factory=dict)
    capabilities: frozenset[str] = field(default_factory=frozenset)


def default_identity(tenant_id: str = "default", user_id: str = "default") -> IdentityContext:
    """Stage-0 single-operator identity factory.

    Auth-off CLI / local dev: ``--tenant`` overrides ``tenant_id``. A *registered*
    operator (one who has signed in once, recorded in the default-claim marker)
    passes ``user_id == tenant_id == their uid`` so a terminal run lands in the
    same single workspace the authenticated web reads (one tenant per operator).
    The single local operator owns their tenant, so they hold the full
    :data:`OWNER_COMMAND_CAPABILITIES` command set.
    Stage 1 (M12 OIDC client) replaces this factory at the resolver seam
    (`presentation/api/deps.py::resolve_identity`); no other call site changes.
    """
    return IdentityContext(
        user_id=UserId(safe_name(user_id)),
        tenant_id=TenantId(safe_name(tenant_id)),
        issuer=None,
        claims={},
        capabilities=OWNER_COMMAND_CAPABILITIES,
    )


def has_capability(identity: IdentityContext, capability: str) -> bool:
    """The one predicate for "does this identity hold *capability*".

    The command-verb gate (`command_dispatcher._require_capability_for`) reads
    capabilities through here, so every capability decision has one shape.
    Dataset *reads* are not a capability decision — see
    `infrastructure.store.dataset_access`.
    """
    return capability in identity.capabilities


def claim_email(identity: IdentityContext) -> str | None:
    """Best-effort read of the OIDC ``email`` claim — ``None`` when absent/non-string."""
    raw = identity.claims.get("email")
    return raw if isinstance(raw, str) else None


__all__ = [
    "CAMPAIGN_BABYSIT_CAP",
    "CAMPAIGN_BUDGET_CAP",
    "CAMPAIGN_CAP_BY_TIER",
    "CAMPAIGN_CREATE_CAP",
    "CAMPAIGN_LIFECYCLE_CAP",
    "CAMPAIGN_RUN_CAP",
    "CAMPAIGN_STEP_CAP",
    "OWNER_COMMAND_CAPABILITIES",
    "IdentityContext",
    "capabilities_from_tiers",
    "claim_email",
    "default_identity",
    "has_capability",
]
