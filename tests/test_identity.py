"""Identity-foundation invariants — Stage-0 gates #3/#4/#6 + Stage-1 gates #1/#2/#5.

The gates are spelled out in `docs/adr/0002-identity-foundation.md`. Stage 0
shipped #3/#4/#6 (resolver seam + tenant scope + SCIM-mapped field set).
Stage 1 adds #1 (closed provider set declared), #2 (no JWT type past
middleware), and #5 (§0 names the Identity I/O kind before code).

Per `tests/CLAUDE.md`: one bundled test per contract — not parallel tests.
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path
from typing import get_type_hints
from unittest.mock import MagicMock

from promptpotter.domain.identity import SafeName, TenantId, UserId, safe_name
from promptpotter.infrastructure.identity import (
    GitHubProviderClient,
    GoogleProviderClient,
    derive_user_id,
)
from promptpotter.infrastructure.store import Stores, build_stores
from promptpotter.presentation.api.deps import resolve_identity
from promptpotter.shared.identity import IdentityContext, default_identity

REPO_ROOT = Path(__file__).resolve().parents[1]

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


def test_stage1_identity_gates(monkeypatch) -> None:
    """Bundled assertion of Stage-1 no-drift gates #1, #2, #5."""
    # Gate #5 — §0 names the Identity I/O kind. The architecture spec lists the
    # five I/O kinds at the top of §0; the count + the literal "Identity" entry
    # must both be present before any Stage-1 code lands.
    arch = (REPO_ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    assert "Five I/O kinds" in arch, "§0 must declare five I/O kinds"
    assert "Identity" in arch, "§0 must name the Identity I/O kind"

    # Gate #1 — the closed provider set is declared as Google + GitHub. Adding
    # a provider means writing a new module under `infrastructure/identity/`
    # AND wiring it into `bundle.build_identity_bundle` + the auth router;
    # this assertion fails until then.
    assert GoogleProviderClient.__module__.endswith("identity.google")
    assert GitHubProviderClient.__module__.endswith("identity.github")

    # Gate #2 — no JWT type appears past the identity-infrastructure
    # boundary. The verifier (`infrastructure/identity/verifier.py`) is the
    # ONLY module permitted to import a JWS library. Downstream code sees
    # `IdentityContext` exclusively.
    code_root = REPO_ROOT / "promptpotter"
    allowed = {
        code_root / "infrastructure" / "identity" / "verifier.py",
        code_root / "infrastructure" / "identity" / "jwks.py",
    }
    forbidden_imports = ("import jwt", "from jwt", "import jose", "from jose")
    for path in code_root.rglob("*.py"):
        if path in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        for needle in forbidden_imports:
            assert needle not in text, (
                f"JWT import past middleware boundary in {path.relative_to(REPO_ROOT)} "
                f"(found {needle!r}); only verifier.py / jwks.py may touch JWS"
            )

    # derive_user_id is deterministic per (iss, sub) and produces a
    # safe_name-shaped slug.
    uid1 = derive_user_id("https://accounts.google.com", "1234567890")
    uid2 = derive_user_id("https://accounts.google.com", "1234567890")
    uid3 = derive_user_id("https://github.com", "1234567890")
    assert uid1 == uid2, "derive_user_id must be deterministic"
    assert uid1 != uid3, "same sub across different iss must yield different UserIds"
    assert safe_name(str(uid1)) == SafeName(str(uid1))

    # PROMPTPOTTER_AUTH=off escape hatch — resolve_identity returns the
    # default Stage-0 identity regardless of request state.
    monkeypatch.setenv("PROMPTPOTTER_AUTH", "off")
    request = MagicMock()
    request.state.identity_ctx = None
    ctx = resolve_identity(request)
    assert ctx == default_identity(), "PROMPTPOTTER_AUTH=off must return default_identity()"

    # When the env var is unset and no session is bound, resolve_identity
    # raises a 401 — that's the unauthenticated path.
    monkeypatch.delenv("PROMPTPOTTER_AUTH", raising=False)
    try:
        resolve_identity(request)
    except Exception as exc:  # FastAPI HTTPException
        assert getattr(exc, "status_code", None) == 401
    else:
        raise AssertionError("resolve_identity must raise 401 when no session bound")

    # Sanity — gate #2 file allowlist references files that actually exist.
    for path in allowed:
        assert path.is_file(), f"gate #2 allowlist references missing file: {path}"
    _ = os  # used implicitly by monkeypatch


def test_registered_developer_resolution(tmp_path: Path) -> None:
    """CLI identity: explicit --tenant > registered developer (claim marker) > default.

    Guards the one-workspace invariant — once a developer has signed in (the
    default-claim marker records their user_id), terminal runs resolve to that
    tenant instead of recreating an orphaned anonymous ``projects/default/``.
    """
    import json

    from promptpotter.infrastructure.identity import (
        registered_or_default_identity,
        registered_user_id,
    )
    from promptpotter.shared.identity import identity_for_user

    marker = tmp_path / "default_claimed.json"
    assert registered_user_id(marker) is None  # never registered
    marker.write_text(json.dumps({"user_id": "default"}), encoding="utf-8")
    assert registered_user_id(marker) is None  # literal "default" is not a registration
    marker.write_text(json.dumps({"user_id": "197ee2cf2aea7b14"}), encoding="utf-8")
    assert registered_user_id(marker) == "197ee2cf2aea7b14"

    # Explicit --tenant always wins, no marker read.
    assert registered_or_default_identity("acme").tenant_id == TenantId(safe_name("acme"))

    # A registered operator's identity is tenant == user (one workspace).
    ident = identity_for_user("197ee2cf2aea7b14")
    assert ident.tenant_id == TenantId("197ee2cf2aea7b14")
    assert ident.user_id == UserId("197ee2cf2aea7b14")
