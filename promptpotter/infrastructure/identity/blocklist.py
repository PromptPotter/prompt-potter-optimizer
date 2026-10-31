"""The operator's revoke, a courtesy control and no security boundary: a blocked person signs up again from another address."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from promptpotter.domain.strict_model import FrozenModel
from promptpotter.infrastructure.store.io import append_jsonl, read_bytes_optional, write_json
from promptpotter.shared.clock import utcnow_iso

logger = logging.getLogger(__name__)


class Blocklist(FrozenModel):
    emails: list[str]


@dataclass(frozen=True)
class BlocklistDecision:
    blocked: bool
    reason: str


def _norm_email(email: str) -> str:
    return email.strip().lower()


def _read(path: Path) -> list[str] | None:
    """``None`` = absent; raises on a file that does not validate."""
    raw = read_bytes_optional(path)
    if raw is None:
        return None
    emails = Blocklist.model_validate_json(raw).emails
    return sorted({_norm_email(e) for e in emails if e.strip()})


def check_blocklist(path: Path, email: str | None) -> BlocklistDecision:
    """Invalid blocks EVERYONE: absent and invalid are opposite security answers, and invalid is never the wider."""
    try:
        blocked = _read(path)
    except ValidationError:
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
    write_json(path, Blocklist(emails=emails).model_dump(mode="json"))


def _append_audit(
    audit_path: Path, *, action: str, email: str, actor: str, before: int, after: int
) -> None:
    append_jsonl(
        audit_path,
        {
            "ts": utcnow_iso(),
            "action": action,
            "email": email,
            "actor": actor,
            "before_count": before,
            "after_count": after,
        },
    )


def _normalize(email: str) -> str:
    normalized = _norm_email(email)
    if not normalized:
        raise ValueError("email must be non-empty")
    return normalized


def list_blocked(path: Path) -> list[str]:
    return _read(path) or []


def block_email(path: Path, email: str, *, actor: str, audit_path: Path) -> list[str]:
    normalized = _normalize(email)
    current = list_blocked(path)
    before = len(current)
    if normalized not in current:
        current = sorted({*current, normalized})
        _write_emails(path, current)
    _append_audit(
        audit_path, action="block", email=normalized, actor=actor, before=before, after=len(current)
    )
    return current


def unblock_email(path: Path, email: str, *, actor: str, audit_path: Path) -> list[str]:
    normalized = _normalize(email)
    current = list_blocked(path)
    before = len(current)
    if normalized in current:
        current = [e for e in current if e != normalized]
        _write_emails(path, current)
    _append_audit(
        audit_path,
        action="unblock",
        email=normalized,
        actor=actor,
        before=before,
        after=len(current),
    )
    return current
