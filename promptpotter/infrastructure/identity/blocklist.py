"""Email blocklist. Completing OIDC ENTITLES: an account holds the owner capability set unless its email is
listed here. The free-tier spend ceiling, not this file, is what bounds a stranger — this is the operator's
revoke, and it is a courtesy control rather than a security boundary, because a blocked person can sign up
again from another address and land in a fresh account with a fresh ceiling."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from promptpotter.infrastructure.store.io import append_jsonl, write_json
from promptpotter.shared.clock import utcnow_iso

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BlocklistDecision:
    blocked: bool
    reason: str


def _norm_email(email: str) -> str:
    """Canonical email form: stripped + lowercased. The ONE normalizer, so the membership test and the stored form cannot
    drift apart."""
    return email.strip().lower()


def check_blocklist(path: Path, email: str | None) -> BlocklistDecision:
    """Blocklist gate. Missing or empty file → nobody blocked; otherwise membership.

    Malformed blocks EVERYONE, which is the same shape the allowlist used and for the same reason: absent and
    corrupt are opposite security answers, and the corrupt one may never be the wider of the two. A typo in
    this file locks the box out loudly, where an un-ban nobody ordered would be silent.
    """
    if not path.is_file():
        return BlocklistDecision(blocked=False, reason="blocklist_absent")
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return BlocklistDecision(blocked=False, reason="blocklist_absent")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("blocklist.json is not valid JSON; treating as block-all")
        return BlocklistDecision(blocked=True, reason="blocklist_invalid")
    emails_raw = data.get("emails") if isinstance(data, dict) else None
    if not isinstance(emails_raw, list):
        logger.warning("blocklist.json has no `emails` list; treating as block-all")
        return BlocklistDecision(blocked=True, reason="blocklist_invalid")
    blocked = {_norm_email(e) for e in emails_raw if isinstance(e, str)}
    if not blocked:
        return BlocklistDecision(blocked=False, reason="blocklist_empty")
    if not email:
        # An identity with no email claim cannot be matched against the list. It is admitted rather than
        # refused because entitlement is the default here — and it is bounded by the free-tier ceiling
        # exactly like every other account.
        return BlocklistDecision(blocked=False, reason="email_missing_from_claims")
    if _norm_email(email) in blocked:
        return BlocklistDecision(blocked=True, reason="email_blocked")
    return BlocklistDecision(blocked=False, reason="email_not_blocked")


# ---------------------------------------------------------------------------
# Administration — the Identity-kind write facet (ADR-0004).
#
# These are the sanctioned mutators behind the operator-admin channel
# (`presentation/admin_bot.py`). They edit the same `{"emails": [...]}` file
# `check_blocklist` reads, atomically, and append one audit line per change to
# the identity-zone `blocklist_audit.jsonl` — never the campaign ledger.
# ---------------------------------------------------------------------------


def _load_emails(path: Path) -> list[str]:
    """The blocklist as a normalized sorted list. Tolerant — missing, empty or malformed all yield ``[]`` — so editing
    always starts from a clean view and a corrupt file is overwritten by the next write."""
    if not path.is_file():
        return []
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("blocklist.json is not valid JSON; treating as empty for edit")
        return []
    emails_raw = data.get("emails") if isinstance(data, dict) else None
    if not isinstance(emails_raw, list):
        return []
    return sorted({_norm_email(e) for e in emails_raw if isinstance(e, str) and e.strip()})


def _write_emails(path: Path, emails: list[str]) -> None:
    """Atomically write the ``{"emails": [...]}`` file via the canonical seam."""
    write_json(path, {"emails": emails})


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
    return _load_emails(path)


def block_email(path: Path, email: str, *, actor: str, audit_path: Path) -> list[str]:
    normalized = _normalize(email)
    current = _load_emails(path)
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
    current = _load_emails(path)
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


__all__ = ["block_email", "check_blocklist", "list_blocked", "unblock_email"]
