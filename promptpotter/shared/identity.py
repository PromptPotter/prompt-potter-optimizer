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

from collections.abc import Mapping
from dataclasses import dataclass, field

from promptpotter.domain.identity import Issuer, TenantId, UserId, safe_name


@dataclass(frozen=True)
class IdentityContext:
    """Frozen identity carrier — see module docstring for field semantics."""

    user_id: UserId
    tenant_id: TenantId
    issuer: Issuer | None = None
    claims: Mapping[str, object] = field(default_factory=dict)
    capabilities: frozenset[str] = field(default_factory=frozenset)


def default_identity(tenant_id: str = "default") -> IdentityContext:
    """Stage-0 single-operator identity. ``--tenant`` CLI flag overrides ``tenant_id``.

    Stage 1 (M12 OIDC client) replaces this factory at the resolver seam
    (`presentation/api/deps.py::resolve_identity`); no other call site changes.
    """
    return IdentityContext(
        user_id=UserId("default"),
        tenant_id=TenantId(safe_name(tenant_id)),
        issuer=None,
        claims={},
        capabilities=frozenset(),
    )


__all__ = ["IdentityContext", "default_identity"]
