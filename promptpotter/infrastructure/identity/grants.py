"""Sealed: the tenant's own API cannot write this store, so a delegate can never self-escalate."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from promptpotter.infrastructure.store.io import append_jsonl, write_json
from promptpotter.shared.clock import utcnow_iso

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PrincipalGrant:
    """An empty ``delegated_by`` is a DENIED grant: own tenant, no command caps, never an owner."""

    sub_principal: str
    delegated_by: str
    capabilities: frozenset[str]
    spend_ceiling_usd: float | None
    note: str

    @property
    def is_denied(self) -> bool:
        return not self.delegated_by


def _denied(sub_principal: str) -> PrincipalGrant:
    return PrincipalGrant(
        sub_principal=sub_principal,
        delegated_by="",
        capabilities=frozenset(),
        spend_ceiling_usd=None,
        note="",
    )


def _load_grants_raw(path: Path) -> dict[str, object] | None:
    """``None`` = absent, nobody is a delegate; malformed is ``{}``, deny-all, so no lookup promotes."""
    if not path.is_file():
        return None
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("grants.json is not valid JSON; treating as deny-all")
        return {}
    grants = data.get("grants") if isinstance(data, dict) else None
    return grants if isinstance(grants, dict) else {}


def read_grant(path: Path, sub_principal_user_id: str) -> PrincipalGrant | None:
    grants = _load_grants_raw(path)
    if not grants or sub_principal_user_id not in grants:
        return None
    entry = grants.get(sub_principal_user_id)
    if not isinstance(entry, dict):
        return _denied(sub_principal_user_id)
    delegated_by = entry.get("delegated_by")
    if not isinstance(delegated_by, str) or not delegated_by:
        logger.warning("grant for %s names no delegator; denying", sub_principal_user_id)
        return _denied(sub_principal_user_id)
    caps_raw = entry.get("capabilities")
    caps = (
        frozenset(c for c in caps_raw if isinstance(c, str))
        if isinstance(caps_raw, list)
        else frozenset()
    )
    ceiling = entry.get("spend_ceiling_usd")
    return PrincipalGrant(
        sub_principal=sub_principal_user_id,
        delegated_by=delegated_by,
        capabilities=caps,
        spend_ceiling_usd=float(ceiling) if isinstance(ceiling, int | float) else None,
        note=str(entry.get("note", "")),
    )


def resolve_effective_capabilities(
    grant: PrincipalGrant, delegator_capabilities: frozenset[str]
) -> frozenset[str]:
    """INTERSECTED with the delegator's own set: a hand-edited over-grant is clamped, not trusted."""
    return grant.capabilities & delegator_capabilities


def _load_grants_for_edit(path: Path) -> dict[str, dict[str, object]]:
    grants = _load_grants_raw(path)
    if not grants:
        return {}
    return {k: v for k, v in grants.items() if isinstance(v, dict)}


def list_grants(path: Path) -> dict[str, dict[str, object]]:
    return _load_grants_for_edit(path)


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
    if read_grant(path, delegated_by_user_id) is not None:
        raise ValueError(
            f"cannot delegate from {delegated_by_user_id!r}: it is itself a sub-principal "
            "(one-level delegation only)"
        )
    grants = _load_grants_for_edit(path)
    grants[sub_principal_user_id] = {
        "delegated_by": delegated_by_user_id,
        "capabilities": sorted(capabilities),
        "spend_ceiling_usd": spend_ceiling_usd,
        "note": note,
    }
    write_json(path, {"grants": grants})
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
    grants = _load_grants_for_edit(path)
    removed = grants.pop(sub_principal_user_id, None) is not None
    if removed:
        write_json(path, {"grants": grants})
    _append_audit(
        audit_path,
        action="revoke",
        sub_principal=sub_principal_user_id,
        actor=actor,
        detail="removed" if removed else "absent",
    )
    return removed


__all__ = [
    "PrincipalGrant",
    "grant_principal",
    "list_grants",
    "read_grant",
    "resolve_effective_capabilities",
    "revoke_principal",
]
