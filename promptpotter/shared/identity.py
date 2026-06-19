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

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from promptpotter.domain.identity import Issuer, TenantId, UserId, safe_name

# Capability gate for repo-root ``datasets/`` (install-global benchmarks).
# Granted to Stage-0 callers when ``PROMPTPOTTER_ADMIN=1``; granted at
# Stage 1 per OIDC claim. Web tenants never receive it by default — that
# is why ``/api/v1/datasets`` no longer leaks the developer's locally
# cached benchmarks to first-time signups.
BENCHMARKS_READ_CAP = "datasets.benchmarks.read"

PROMPTPOTTER_ADMIN_ENV = "PROMPTPOTTER_ADMIN"


@dataclass(frozen=True)
class IdentityContext:
    """Frozen identity carrier — see module docstring for field semantics."""

    user_id: UserId
    tenant_id: TenantId
    issuer: Issuer | None = None
    claims: Mapping[str, object] = field(default_factory=dict)
    capabilities: frozenset[str] = field(default_factory=frozenset)


def _admin_caps_from_env() -> frozenset[str]:
    """Stage-0 admin capabilities when ``PROMPTPOTTER_ADMIN=1`` is set.

    Sound ONLY on the single-operator Stage-0 box (auth-off CLI / local dev),
    where "the box's operator" and "the identity" are the same principal. The
    multi-user OIDC path MUST NOT use this — a process-wide flag there would
    grant the capability to every signup. There, admin is pinned to the
    registered-developer identity (`oidc.py`, per ADR-0004 secure-by-default).
    """
    if os.environ.get(PROMPTPOTTER_ADMIN_ENV, "").strip() == "1":
        return frozenset({BENCHMARKS_READ_CAP})
    return frozenset()


def default_identity(tenant_id: str = "default", user_id: str = "default") -> IdentityContext:
    """Stage-0 single-operator identity factory.

    Auth-off CLI / local dev: ``--tenant`` overrides ``tenant_id``. A *registered*
    operator (one who has signed in once, recorded in the default-claim marker)
    passes ``user_id == tenant_id == their uid`` so a terminal run lands in the
    same single workspace the authenticated web reads (one tenant per operator).
    ``PROMPTPOTTER_ADMIN=1`` augments capabilities with :data:`BENCHMARKS_READ_CAP`.
    Stage 1 (M12 OIDC client) replaces this factory at the resolver seam
    (`presentation/api/deps.py::resolve_identity`); no other call site changes.
    """
    return IdentityContext(
        user_id=UserId(safe_name(user_id)),
        tenant_id=TenantId(safe_name(tenant_id)),
        issuer=None,
        claims={},
        capabilities=_admin_caps_from_env(),
    )


def claim_email(identity: IdentityContext) -> str | None:
    """Best-effort read of the OIDC ``email`` claim — ``None`` when absent/non-string."""
    raw = identity.claims.get("email")
    return raw if isinstance(raw, str) else None


__all__ = [
    "BENCHMARKS_READ_CAP",
    "PROMPTPOTTER_ADMIN_ENV",
    "IdentityContext",
    "claim_email",
    "default_identity",
]
