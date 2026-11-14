"""The operator's identity files: where they live, the delegation grants and the blocklist.

Grants are sealed: the tenant's own API cannot write them, so a delegate can never self-escalate.
The blocklist is the operator's revoke, a courtesy control and no security boundary: a blocked
person signs up again from another address."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from pydantic import field_serializer

from promptpotter.config.paths import user_data_root
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.infrastructure.store.io import append_jsonl, write_json, write_lock
from promptpotter.infrastructure.store.read_model import read_file
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import FileKind, FileUnreadableError
from promptpotter.shared.identity import DELEGABLE_CAPABILITIES


@dataclass(frozen=True)
class IdentityPaths:
    root: Path

    @property
    def provider_config(self) -> Path:
        return self.root / "oidc.json"

    @property
    def blocklist(self) -> Path:
        return self.root / "blocklist.json"

    @property
    def blocklist_audit(self) -> Path:
        return self.root / "blocklist_audit.jsonl"

    @property
    def sessions_dir(self) -> Path:
        return self.root / "sessions"

    @property
    def grants(self) -> Path:
        return self.root / "grants.json"

    @property
    def grants_audit(self) -> Path:
        return self.root / "grants_audit.jsonl"


IDENTITY_DIR = "identity"


def default_identity_paths() -> IdentityPaths:
    return IdentityPaths(root=user_data_root() / IDENTITY_DIR)


logger = logging.getLogger(__name__)


class PrincipalGrant(FrozenModel):
    """An empty ``delegated_by`` is a DENIED grant: own tenant, no command caps, never an owner."""

    delegated_by: str
    capabilities: frozenset[str]
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
    """INTERSECTED with the delegator's own resolved set and with what a grant may carry: a
    hand-edited over-grant is clamped, not trusted, and a delegate never holds the account's keys."""
    return grant.capabilities & delegator_capabilities & DELEGABLE_CAPABILITIES


def list_grants(path: Path) -> dict[str, PrincipalGrant]:
    return _grants(path)


def _append_audit(audit_path: Path, **row: str | int) -> None:
    """One audit line of a grant or a block edit, stamped; written before its effect."""
    append_jsonl(audit_path, {"ts": utcnow_iso(), **row}, durable=True)


def _write(path: Path, grants: dict[str, PrincipalGrant]) -> None:
    write_json(path, GrantsFile(grants=grants).model_dump(mode="json"), durable=True)


def grant_principal(
    path: Path,
    *,
    sub_principal_user_id: str,
    delegated_by_user_id: str,
    capabilities: frozenset[str],
    note: str,
    actor: str,
    audit_path: Path,
) -> None:
    """Refuses a sub-principal AS delegator, the read-time ceiling walking one level only, and an
    account as its own delegate, which would hand it its delegator's tenant as a grant (bl4.B8)."""
    if sub_principal_user_id == delegated_by_user_id:
        raise ValueError(f"cannot delegate {delegated_by_user_id!r} to itself")
    # Read and rewritten under the file's write lock: two edits never both read it (bl2.B10). The
    # audit line lands first, so a crash leaves an intent on record, never an unrecorded effect.
    with write_lock(path):
        grants = _grants(path)
        if delegated_by_user_id in grants:
            raise ValueError(
                f"cannot delegate from {delegated_by_user_id!r}: it is itself a sub-principal "
                "(one-level delegation only)"
            )
        _append_audit(
            audit_path,
            action="grant",
            sub_principal=sub_principal_user_id,
            actor=actor,
            detail=f"by={delegated_by_user_id} caps={sorted(capabilities)}",
        )
        _write(
            path,
            {
                **grants,
                sub_principal_user_id: PrincipalGrant(
                    delegated_by=delegated_by_user_id,
                    capabilities=capabilities,
                    note=note,
                ),
            },
        )


def revoke_principal(
    path: Path, *, sub_principal_user_id: str, actor: str, audit_path: Path
) -> bool:
    with write_lock(path):
        grants = _grants(path)
        removed = sub_principal_user_id in grants
        _append_audit(
            audit_path,
            action="revoke",
            sub_principal=sub_principal_user_id,
            actor=actor,
            detail="removed" if removed else "absent",
        )
        if removed:
            _write(path, {k: v for k, v in grants.items() if k != sub_principal_user_id})
    return removed


class Blocklist(FrozenModel):
    emails: list[str]


@dataclass(frozen=True)
class BlocklistDecision:
    blocked: bool
    reason: str


def _norm_email(email: str) -> str:
    return email.strip().lower()


def _blocked(path: Path) -> list[str] | None:
    """``None`` = absent; a file that does not validate, an empty one included, raises."""
    held = read_file(path, Blocklist.model_validate_json, FileKind.STORED_CONFIG)
    return None if held is None else sorted({_norm_email(e) for e in held.emails if e.strip()})


def check_blocklist(path: Path, email: str | None) -> BlocklistDecision:
    """Invalid, an empty file included, blocks EVERYONE: absent and invalid are opposite security
    answers, and invalid is never the wider."""
    try:
        blocked = _blocked(path)
    except FileUnreadableError:
        logger.warning("blocklist.json does not validate; treating as block-all")
        return BlocklistDecision(blocked=True, reason="blocklist_invalid")
    if blocked is None:
        return BlocklistDecision(blocked=False, reason="blocklist_absent")
    if not blocked:
        return BlocklistDecision(blocked=False, reason="blocklist_empty")
    if not email:
        # No email claim: admitted, since entitlement is the default and the host coupon still bounds it.
        return BlocklistDecision(blocked=False, reason="email_missing_from_claims")
    if _norm_email(email) in blocked:
        return BlocklistDecision(blocked=True, reason="email_blocked")
    return BlocklistDecision(blocked=False, reason="email_not_blocked")


def _write_emails(path: Path, emails: list[str]) -> None:
    write_json(path, Blocklist(emails=emails).model_dump(mode="json"), durable=True)


def _normalize(email: str) -> str:
    normalized = _norm_email(email)
    if not normalized:
        raise ValueError("email must be non-empty")
    return normalized


def list_blocked(path: Path) -> list[str]:
    return _blocked(path) or []


def block_email(path: Path, email: str, *, actor: str, audit_path: Path) -> list[str]:
    normalized = _normalize(email)
    # Read and rewritten under the file's write lock: two edits never both read it. The audit line
    # lands first, so a crash leaves an intent on record, never an unrecorded effect.
    with write_lock(path):
        current = list_blocked(path)
        before = len(current)
        added = normalized not in current
        if added:
            current = sorted({*current, normalized})
        _append_audit(
            audit_path,
            action="block",
            email=normalized,
            actor=actor,
            before_count=before,
            after_count=len(current),
        )
        if added:
            _write_emails(path, current)
    return current


def unblock_email(path: Path, email: str, *, actor: str, audit_path: Path) -> list[str]:
    normalized = _normalize(email)
    with write_lock(path):
        current = list_blocked(path)
        before = len(current)
        removed = normalized in current
        if removed:
            current = [e for e in current if e != normalized]
        _append_audit(
            audit_path,
            action="unblock",
            email=normalized,
            actor=actor,
            before_count=before,
            after_count=len(current),
        )
        if removed:
            _write_emails(path, current)
    return current
