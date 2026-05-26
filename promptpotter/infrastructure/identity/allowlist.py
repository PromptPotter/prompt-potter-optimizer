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
from dataclasses import dataclass
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


__all__ = ["AllowlistDecision", "check_allowlist"]
