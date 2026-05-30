"""Email allowlist gate — `.promptpotter/identity/allowlist.json`.

Schema:

```json
{"emails": ["alice@example.com", "bob@example.com"]}
```

Missing file → allow-all (Stage-1 escape hatch for local dev). An empty
`emails` list explicitly denies everyone — useful for "lock the surface
while I'm setting up." Per the Phase G spec, an unmatched email returns
403 at the callback, not 404 — existence-leak applies to campaigns, not
to the OIDC seam itself (the operator wants a visible "you're not on
the list" signal).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AllowlistDecision:
    """Result of an allowlist check."""

    allowed: bool
    reason: str


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
    permitted = {str(e).strip().lower() for e in emails_raw if isinstance(e, str)}
    if not permitted:
        return AllowlistDecision(allowed=False, reason="allowlist_empty")
    if not email:
        return AllowlistDecision(allowed=False, reason="email_missing_from_claims")
    if email.strip().lower() in permitted:
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
    """Current allowlist as a normalized (lowercased, stripped) sorted list.

    Tolerant: missing / empty / malformed file → ``[]``. Editing always starts
    from a clean view; a corrupt file is overwritten by the next write.
    """
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
    return sorted({str(e).strip().lower() for e in emails_raw if isinstance(e, str) and e.strip()})


def _write_emails(path: Path, emails: list[str]) -> None:
    """Atomically write the ``{"emails": [...]}`` file (tmp + ``os.replace``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"emails": emails}, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _append_audit(
    audit_path: Path, *, action: str, email: str, actor: str, before: int, after: int
) -> None:
    """Append one change record to the identity-zone audit log."""
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {
            "ts": datetime.now(UTC).isoformat(),
            "action": action,
            "email": email,
            "actor": actor,
            "before_count": before,
            "after_count": after,
        }
    )
    with audit_path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _normalize(email: str) -> str:
    normalized = email.strip().lower()
    if not normalized:
        raise ValueError("email must be non-empty")
    return normalized


def list_emails(path: Path) -> list[str]:
    """Current allowlist as a sorted list (``[]`` if absent)."""
    return _load_emails(path)


def add_email(path: Path, email: str, *, actor: str, audit_path: Path) -> list[str]:
    """Add *email* to the allowlist; return the new sorted list. No-op if present."""
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
    """Remove *email* from the allowlist; return the new sorted list. No-op if absent."""
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


__all__ = [
    "AllowlistDecision",
    "add_email",
    "check_allowlist",
    "list_emails",
    "remove_email",
]
