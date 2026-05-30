"""Default-tenant claim — first sign-in rebinds `projects/default/`.

One-shot, atomic, irreversible. When the first OIDC user signs in:

* if `projects/default/` exists AND no claim marker present →
  rename to `projects/{user_id}/` and rewrite every
  ``campaign.json::owner_user_id`` field from ``"default"`` to the new
  user_id (so the API's owner-gated reads return them), then write the
  claim marker.
* if the claim marker is already present → ensure the ownership
  rewrite has applied (idempotent; covers users who migrated under
  earlier rename-only code).
* if `projects/default/` doesn't exist → still write the marker so the
  next user doesn't try to claim a half-state.

Marker schema (`.promptpotter/identity/default_claimed.json`):
``{"user_id": "...", "claimed_at": "<iso-8601>"}``.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from promptpotter.shared.identity import IdentityContext

logger = logging.getLogger(__name__)


def maybe_claim_default(
    *,
    projects_root: Path,
    user_id: str,
    marker_path: Path,
) -> bool:
    """Run the one-shot rebind. Returns True iff a directory rename happened.

    Ownership rewrite runs on every call (cheap; idempotent) — this catches
    users who signed in under an earlier rename-only build and ended up
    with their campaigns still owned by ``"default"``.
    """
    user_dir = projects_root / user_id
    default_dir = projects_root / "default"
    renamed = False

    if not marker_path.is_file():
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        if default_dir.is_dir() and not user_dir.exists():
            try:
                default_dir.rename(user_dir)
                renamed = True
                logger.info("Claimed default tenant: %s → %s", default_dir, user_dir)
            except OSError as exc:
                logger.warning("Failed to claim default tenant for %s: %s", user_id, exc)
        marker_path.write_text(
            json.dumps(
                {
                    "user_id": user_id,
                    "claimed_at": datetime.now(UTC).isoformat(),
                    "renamed": renamed,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    if user_dir.is_dir():
        _rewrite_campaign_ownership(user_dir / "campaigns", user_id)
    return renamed


def registered_or_default_identity(explicit_tenant: str | None = None) -> IdentityContext:
    """Resolve the CLI's identity: explicit ``--tenant`` > registered user > ``default``.

    The single entry every CLI command (``new`` / ``resume`` / ``sweep``) uses
    to decide which workspace a terminal run writes to. An explicit
    ``--tenant`` wins; otherwise a registered developer (default-claim marker)
    resolves to their own tenant so runs join the one workspace the
    authenticated web reads; otherwise anonymous ``default``.
    """
    from promptpotter.infrastructure.identity.paths import default_identity_paths
    from promptpotter.shared.identity import default_identity, identity_for_user

    if explicit_tenant:
        return default_identity(tenant_id=explicit_tenant)
    uid = registered_user_id(default_identity_paths().default_claim_marker)
    return identity_for_user(uid) if uid else default_identity()


def registered_user_id(marker_path: Path) -> str | None:
    """Return the claimed operator's ``user_id`` from the marker, or ``None``.

    The default-claim marker is the single-operator "registration" record: once
    written (first sign-in), the local developer *is* that user. The CLI reads
    this to resolve its identity to the registered operator instead of
    anonymous ``default``, so terminal runs land in the same one workspace the
    authenticated web reads. ``None`` when never registered (no marker / no
    ``user_id``) — callers fall back to :func:`default_identity`.
    """
    if not marker_path.is_file():
        return None
    try:
        uid = json.loads(marker_path.read_text(encoding="utf-8")).get("user_id")
    except (OSError, json.JSONDecodeError):
        return None
    return uid if isinstance(uid, str) and uid and uid != "default" else None


def _rewrite_campaign_ownership(campaigns_root: Path, user_id: str) -> None:
    """Rewrite every `campaign.json::owner_user_id` from ``default`` to *user_id*.

    Idempotent: campaigns already owned by *user_id* are skipped. Any
    other owner is left alone (multi-user installs may have campaigns
    that legitimately belong to a different user_id already).
    """
    if not campaigns_root.is_dir():
        return
    rewritten = 0
    for campaign_dir in campaigns_root.iterdir():
        manifest = campaign_dir / "campaign.json"
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Skipping unreadable campaign.json at %s: %s", manifest, exc)
            continue
        current = data.get("owner_user_id")
        if current == user_id or (current not in (None, "", "default")):
            continue
        data["owner_user_id"] = user_id
        tmp = manifest.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(manifest)
        rewritten += 1
    if rewritten:
        logger.info("Rebound %d campaigns to user %s", rewritten, user_id)


__all__ = ["maybe_claim_default", "registered_or_default_identity", "registered_user_id"]
