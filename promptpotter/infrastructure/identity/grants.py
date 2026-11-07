"""Sealed: the tenant's own API cannot write this store, so a delegate can never self-escalate."""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import field_serializer

from promptpotter.domain.strict_model import FrozenModel, NonNegativeWireFloat
from promptpotter.infrastructure.store.io import append_jsonl, write_json
from promptpotter.infrastructure.store.read_model import read_file
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import FileKind, FileUnreadableError

logger = logging.getLogger(__name__)


class PrincipalGrant(FrozenModel):
    """An empty ``delegated_by`` is a DENIED grant: own tenant, no command caps, never an owner."""

    delegated_by: str
    capabilities: frozenset[str]
    spend_ceiling_usd: NonNegativeWireFloat | None
    note: str

    @property
    def is_denied(self) -> bool:
        return not self.delegated_by

    @field_serializer("capabilities")
    def serialize_capabilities(self, capabilities: frozenset[str]) -> list[str]:
        return sorted(capabilities)


class GrantsFile(FrozenModel):
    grants: dict[str, PrincipalGrant]


def _grants(path: Path) -> dict[str, PrincipalGrant]:
    """Absent is no grants; a file that does not validate, an empty one included, raises."""
    held = read_file(path, GrantsFile.model_validate_json, FileKind.STORED_CONFIG)
    return {} if held is None else held.grants


def read_grant(path: Path, sub_principal_user_id: str) -> PrincipalGrant | None:
    try:
        grants = _grants(path)
    except FileUnreadableError:
        # Deny-all: an invalid file promotes nobody to a delegate.
        logger.warning("grants.json does not validate; no grant is read")
        return None
    return grants.get(sub_principal_user_id)


def resolve_effective_capabilities(
    grant: PrincipalGrant, delegator_capabilities: frozenset[str]
) -> frozenset[str]:
    """INTERSECTED with the delegator's own set: a hand-edited over-grant is clamped, not trusted."""
    return grant.capabilities & delegator_capabilities


def list_grants(path: Path) -> dict[str, PrincipalGrant]:
    return _grants(path)


def _append_audit(
    audit_path: Path, *, action: str, sub_principal: str, actor: str, detail: str
) -> None:
    append_jsonl(
        audit_path,
        {
            "ts": utcnow_iso(),
            "action": action,
            "sub_principal": sub_principal,
            "actor": actor,
            "detail": detail,
        },
    )


def _write(path: Path, grants: dict[str, PrincipalGrant]) -> None:
    write_json(path, GrantsFile(grants=grants).model_dump(mode="json"))


def grant_principal(
    path: Path,
    *,
    sub_principal_user_id: str,
    delegated_by_user_id: str,
    capabilities: frozenset[str],
    spend_ceiling_usd: float | None,
    note: str,
    actor: str,
    audit_path: Path,
) -> None:
    """Refuses a sub-principal AS delegator: the read-time ceiling walks one level only."""
    grants = _grants(path)
    if delegated_by_user_id in grants:
        raise ValueError(
            f"cannot delegate from {delegated_by_user_id!r}: it is itself a sub-principal "
            "(one-level delegation only)"
        )
    _write(
        path,
        {
            **grants,
            sub_principal_user_id: PrincipalGrant(
                delegated_by=delegated_by_user_id,
                capabilities=capabilities,
                spend_ceiling_usd=spend_ceiling_usd,
                note=note,
            ),
        },
    )
    _append_audit(
        audit_path,
        action="grant",
        sub_principal=sub_principal_user_id,
        actor=actor,
        detail=f"by={delegated_by_user_id} caps={sorted(capabilities)}",
    )


def revoke_principal(
    path: Path, *, sub_principal_user_id: str, actor: str, audit_path: Path
) -> bool:
    grants = _grants(path)
    removed = sub_principal_user_id in grants
    if removed:
        _write(path, {k: v for k, v in grants.items() if k != sub_principal_user_id})
    _append_audit(
        audit_path,
        action="revoke",
        sub_principal=sub_principal_user_id,
        actor=actor,
        detail="removed" if removed else "absent",
    )
    return removed
