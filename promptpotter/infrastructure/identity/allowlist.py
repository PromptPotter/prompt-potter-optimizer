"""Email allowlist. Missing file → allow-all (local-dev escape hatch); an EMPTY list denies everyone. An unmatched
email is 403 at the callback, not 404 — existence-hiding covers campaigns, not the OIDC seam itself."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from promptpotter.infrastructure.store.io import append_jsonl, write_json
from promptpotter.shared.clock import utcnow_iso

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AllowlistDecision:
    allowed: bool
    reason: str


def _norm_email(email: str) -> str:
    """Canonical email form: stripped + lowercased. The ONE normalizer, so the membership test and the stored form cannot
    drift apart."""
    return email.strip().lower()


def check_allowlist(path: Path, email: str | None) -> AllowlistDecision:
    """Allowlist gate. Missing file → allow; empty list → deny; otherwise membership."""
    if not path.is_file():
        return AllowlistDecision(allowed=True, reason="allowlist_absent")
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return AllowlistDecision(allowed=True, reason="allowlist_absent")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("allowlist.json is not valid JSON; treating as deny-all")
        return AllowlistDecision(allowed=False, reason="allowlist_invalid")
    emails_raw = data.get("emails") if isinstance(data, dict) else None
    if not isinstance(emails_raw, list):
        return AllowlistDecision(allowed=False, reason="allowlist_invalid")
    permitted = {_norm_email(e) for e in emails_raw if isinstance(e, str)}
    if not permitted:
        return AllowlistDecision(allowed=False, reason="allowlist_empty")
    if not email:
        return AllowlistDecision(allowed=False, reason="email_missing_from_claims")
    if _norm_email(email) in permitted:
        return AllowlistDecision(allowed=True, reason="email_permitted")
    return AllowlistDecision(allowed=False, reason="email_not_permitted")


# ---------------------------------------------------------------------------
# Administration — the Identity-kind write facet (ADR-0004).
#
# These are the sanctioned mutators behind the operator-admin channel
# (`presentation/admin_bot.py`). They edit the same `{"emails": [...]}` file
# `check_allowlist` reads, atomically, and append one audit line per change to
# the identity-zone `allowlist_audit.jsonl` — never the campaign ledger.
# ---------------------------------------------------------------------------


def _load_emails(path: Path) -> list[str]:
    """The allowlist as a normalized sorted list. Tolerant — missing, empty or malformed all yield ``[]`` — so editing
    always starts from a clean view and a corrupt file is overwritten by the next write."""
    if not path.is_file():
        return []
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("allowlist.json is not valid JSON; treating as empty for edit")
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


def list_emails(path: Path) -> list[str]:
    return _load_emails(path)


def add_email(path: Path, email: str, *, actor: str, audit_path: Path) -> list[str]:
    normalized = _normalize(email)
    current = _load_emails(path)
    before = len(current)
    if normalized not in current:
        current = sorted({*current, normalized})
        _write_emails(path, current)
    _append_audit(
        audit_path, action="add", email=normalized, actor=actor, before=before, after=len(current)
    )
    return current


def remove_email(path: Path, email: str, *, actor: str, audit_path: Path) -> list[str]:
    normalized = _normalize(email)
    current = _load_emails(path)
    before = len(current)
    if normalized in current:
        current = [e for e in current if e != normalized]
        _write_emails(path, current)
    _append_audit(
        audit_path,
        action="remove",
        email=normalized,
        actor=actor,
        before=before,
        after=len(current),
    )
    return current


__all__ = ["add_email", "check_allowlist", "list_emails", "remove_email"]
