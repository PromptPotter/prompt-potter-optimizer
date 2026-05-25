"""Identity-foundation Stage-0 invariants — no-drift gates #3, #4, #6.

The gates are spelled out in `docs/specs/identity-foundation.md`. Stage 0 ships
only what's checkable today (the resolver seam is in-process and the SCIM
schema isn't vendored yet); gates #1, #2, #5 are deferred until Stage 1 lands
the OIDC client + middleware.

Per `tests/CLAUDE.md`: one bundled test, one canonical case per contract — not
three parallel tests.
"""

from __future__ import annotations

import inspect
from typing import get_type_hints

from promptpotter.domain.identity import SafeName, TenantId, UserId, safe_name
from promptpotter.infrastructure.store import Stores, build_stores
from promptpotter.shared.identity import IdentityContext, default_identity

# SCIM 2.0 Core (RFC 7643) User-resource field subset we promise to mirror at
# Stage 0 — `id` and `externalId` map to `user_id`; the IdentityContext layer
# carries the OIDC-claim envelope (`claims`) and the auth-time capability set
# (`capabilities`) alongside the tenant slice (`tenant_id`). Stage 1+ extends
# this via the SCIM extension namespace, never by re-shaping these five.
_SCIM_STAGE0_FIELDS = frozenset({"user_id", "tenant_id", "issuer", "claims", "capabilities"})


def test_identity_seam_no_drift() -> None:
    """Bundled assertion of identity-foundation no-drift gates #3, #4, #6."""
    # Gate #3 — build_stores takes IdentityContext as first positional param.
    sig = inspect.signature(build_stores)
    params = list(sig.parameters.values())
    assert params, "build_stores must take at least one parameter"
    first = params[0]
    assert first.name == "identity", f"first parameter must be 'identity', got {first.name!r}"
    # ``from __future__ import annotations`` stringifies signatures — resolve via get_type_hints.
    hints = get_type_hints(build_stores)
    assert hints.get("identity") is IdentityContext, (
        f"first parameter must be IdentityContext, got {hints.get('identity')!r}"
    )

    # Gate #4 — Stores.identity is the sole source of tenant scope. The
    # convenience accessor Stores.tenant_id is a @property that delegates;
    # there must be no independent ``tenant_id`` field on the dataclass.
    field_names = {f.name for f in Stores.__dataclass_fields__.values()}
    assert "identity" in field_names, "Stores must carry an IdentityContext field"
    assert "tenant_id" not in field_names, (
        "Stores.tenant_id must be derived from .identity (no independent field)"
    )
    assert isinstance(getattr(Stores, "tenant_id", None), property), (
        "Stores.tenant_id must be a @property reading from .identity"
    )

    # Gate #6 — IdentityContext field names mirror the SCIM-mapped Stage-0
    # surface verbatim. Placeholder until `vendor/schemas/scim/` lands; once
    # the submodule is vendored, replace this set with a load of the SCIM Core
    # JSON Schema field list.
    ctx_fields = {f.name for f in IdentityContext.__dataclass_fields__.values()}
    assert ctx_fields == _SCIM_STAGE0_FIELDS, (
        f"IdentityContext field set drifted from SCIM Stage-0 map: "
        f"got {sorted(ctx_fields)}, expected {sorted(_SCIM_STAGE0_FIELDS)}"
    )

    # And the Stage-0 default constructs cleanly + threads through build_stores.
    default = default_identity()
    assert isinstance(default.tenant_id, str)  # TenantId is NewType(str)
    assert default.tenant_id == TenantId("default")
    assert default.user_id == UserId("default")
    assert default.issuer is None
    assert default.capabilities == frozenset()

    # safe_name guards path-segment use of identity slugs.
    assert safe_name("default") == SafeName("default")
    try:
        safe_name("../etc/passwd")
    except ValueError:
        pass
    else:
        raise AssertionError("safe_name must reject path-traversal slugs")
