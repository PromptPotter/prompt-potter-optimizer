from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal, NewType

from promptpotter.shared.errors import NotFoundError

logger = logging.getLogger(__name__)

TenantId = NewType("TenantId", str)
UserId = NewType("UserId", str)
Issuer = NewType("Issuer", str)
SafeName = NewType("SafeName", str)

_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
_MAX_SAFE_NAME_LEN = 64


def safe_name(raw: str) -> SafeName:
    if not raw or not _SAFE_NAME_RE.match(raw) or len(raw) > _MAX_SAFE_NAME_LEN:
        raise ValueError(
            f"Invalid identity slug: {raw!r}. "
            f"Must match [a-zA-Z0-9_-]+ with length <= {_MAX_SAFE_NAME_LEN}."
        )
    return SafeName(raw)


CAMPAIGN_STEP_CAP = "campaign.step"
CAMPAIGN_RUN_CAP = "campaign.run"
CAMPAIGN_CREATE_CAP = "campaign.create"
CAMPAIGN_BUDGET_CAP = "campaign.budget"
CAMPAIGN_LIFECYCLE_CAP = "campaign.lifecycle"
CAMPAIGN_BABYSIT_CAP = "campaign.babysit"
CAMPAIGN_LOOKAHEAD_CAP = "campaign.lookahead"

CAMPAIGN_CAP_BY_NAME: dict[str, str] = {
    "step": CAMPAIGN_STEP_CAP,
    "run": CAMPAIGN_RUN_CAP,
    "create": CAMPAIGN_CREATE_CAP,
    "budget": CAMPAIGN_BUDGET_CAP,
    "lifecycle": CAMPAIGN_LIFECYCLE_CAP,
    "babysit": CAMPAIGN_BABYSIT_CAP,
    # Its own rung: it spends the BOX's shared provider rate bucket, not a campaign budget.
    "lookahead": CAMPAIGN_LOOKAHEAD_CAP,
}

OWNER_COMMAND_CAPABILITIES = frozenset(CAMPAIGN_CAP_BY_NAME.values())


def capabilities_from_names(names: Iterable[str]) -> frozenset[str]:
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


# Also the tenant dir the terminal writes (`projects/default/`) until a browser claim renames it.
TERMINAL_IDENTITY_ID = "default"

# A blocked account is authenticated with an EMPTY capability set; only the dispatcher's gate acts.
AccessState = Literal["active", "blocked"]


@dataclass(frozen=True)
class IdentityContext:
    user_id: UserId
    tenant_id: TenantId
    issuer: Issuer | None = None
    email: str | None = None
    provider: str | None = None
    access_state: AccessState = "active"
    claims: Mapping[str, object] = field(default_factory=dict)
    capabilities: frozenset[str] = field(default_factory=frozenset)


def default_identity(
    tenant_id: str = TERMINAL_IDENTITY_ID, user_id: str = TERMINAL_IDENTITY_ID
) -> IdentityContext:
    return IdentityContext(
        user_id=UserId(safe_name(user_id)),
        tenant_id=TenantId(safe_name(tenant_id)),
        issuer=None,
        claims={},
        capabilities=OWNER_COMMAND_CAPABILITIES,
    )


def has_capability(identity: IdentityContext, capability: str) -> bool:
    return capability in identity.capabilities


def require_capability(identity: IdentityContext, capability: str, *, subject: str) -> None:
    """Raises 404, never 403: ADR-0005's existence-hiding posture."""
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
    principal = identity.claims.get("principal")
    if isinstance(principal, str) and principal:
        return principal
    return str(identity.user_id)


def display_name(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    local = email.split("@", 1)[0]
    return local.replace(".", " ").replace("_", " ").title() or None


__all__ = [
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
    "AccessState",
    "IdentityContext",
    "Issuer",
    "SafeName",
    "TenantId",
    "UserId",
    "acting_principal_id",
    "capabilities_from_names",
    "default_identity",
    "display_name",
    "has_capability",
    "require_capability",
    "safe_name",
]
