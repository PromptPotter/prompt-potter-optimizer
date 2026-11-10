"""Notices this box sends out. A refusal is a non-2xx body, not an exception: ``refused`` is the one
place that reads it, so an unheard notice is never reported as heard."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from promptpotter.config.settings import settings
from promptpotter.domain.run_records import Device
from promptpotter.shared.tls import tls_context

logger = logging.getLogger(__name__)


def refused(resp: httpx.Response, what: str) -> bool:
    if resp.is_success:
        return False
    logger.warning("%s refused: HTTP %d %s", what, resp.status_code, resp.text[:300])
    return True


def post_json(
    url: str, body: dict[str, Any], *, what: str, headers: dict[str, str] | None = None
) -> bool:
    """``True`` only when the receiver took it; never a raise, so a notice cannot fail its caller."""
    try:
        with httpx.Client(timeout=10, verify=tls_context()) as client:
            resp = client.post(url, json=body, headers=headers)
    except httpx.HTTPError:
        logger.warning("%s failed to send", what, exc_info=True)
        return False
    return not refused(resp, what)


def notify_operator(text: str, *, ring: bool) -> bool:
    """Best-effort: ``False`` and never a raise, so a notice cannot fail the request behind it.

    ``ring`` is the sender's call: an alert sounds, a notice for the record arrives silently.
    """
    token = settings.ADMIN_BOT_TELEGRAM_TOKEN.get_secret_value().strip()
    chat_id = settings.ADMIN_BOT_CHAT_ID.strip()
    if not token or not chat_id:
        logger.info("Operator notice skipped (admin bot not configured): %s", text)
        return False
    return post_json(
        f"https://api.telegram.org/bot{token}/sendMessage",
        {"chat_id": chat_id, "text": text, "disable_notification": not ring},
        what="Operator notice",
    )


def forward_new_account_to_crm(
    email: str | None, name: str | None, user_id: str, account_count: int, device: Device | None
) -> bool:
    url = settings.N8N_SIGNUP_WEBHOOK_URL.get_secret_value().strip()
    if not url:
        logger.info("CRM forward skipped (N8N_SIGNUP_WEBHOOK_URL not set): %s", email or user_id)
        return False
    return post_json(
        url,
        {
            "email": email or "",
            "name": name or "",
            "use_case": "",
            "signup_source": "promptpotter-app",
            "account_count": account_count,
            "device": device or "",
        },
        what=f"CRM forward for {email or user_id}",
    )
