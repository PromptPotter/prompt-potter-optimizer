from __future__ import annotations

import asyncio
import logging
import sys
import time
from typing import Any

import httpx

from promptpotter.application import health
from promptpotter.application.account import (
    block_email,
    blocked_emails,
    grant,
    grants,
    registered_user_id,
    revoke_grant,
    trail_by_email,
    trails_since,
    unblock_email,
)
from promptpotter.application.initialization.wiring import complete_registries
from promptpotter.application.jobs.install_spend import read_install_spend
from promptpotter.application.jobs.quota import overrun
from promptpotter.application.views.trail import trail_fields, trail_notes, trail_rows
from promptpotter.config.logging import setup_logging
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.config.settings import settings
from promptpotter.domain.run_records import Device
from promptpotter.domain.spend import RATE_PRICED_LABEL
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.identity import CAMPAIGN_CAP_BY_NAME, capabilities_from_names
from promptpotter.shared.tls import tls_context

logger = logging.getLogger(__name__)

_POLL_TIMEOUT_S = 50
_RETRY_BACKOFF_S = 5
_USAGE = (
    "Commands:\n/block <email>\n/unblock <email>\n/blocked\n"
    "/grant <sub_user_id> <capabilities>\n/revoke <sub_user_id>\n/grants\n/spend\n/trail <email>"
)
_TELEGRAM_MAX_CHARS = 4096
_TRAIL_WINDOW_H = 24
_TRAIL_FORWARD_EVERY_S = 1800


def parse_command(text: str, passphrase: str | None) -> tuple[str, str] | None:
    body = text.strip()
    if passphrase:
        prefix = passphrase.strip()
        if not body.startswith(prefix):
            return None
        body = body[len(prefix) :].strip()
    if not body.startswith("/"):
        return None
    parts = body.split(maxsplit=1)
    command = parts[0].lstrip("/").lower()
    argument = parts[1].strip() if len(parts) > 1 else ""
    return command, argument


def handle_command(command: str, argument: str, actor: str) -> str:
    if command == "blocked":
        emails = blocked_emails()
        return "Blocked:\n" + ("\n".join(emails) if emails else "(nobody)")
    if command in ("block", "unblock"):
        if not argument:
            return f"Usage: /{command} <email>"
        try:
            if command == "block":
                emails = block_email(argument, actor=actor)
                verb = "Blocked"
            else:
                emails = unblock_email(argument, actor=actor)
                verb = "Unblocked"
        except ValueError as exc:
            return f"Error: {exc}"
        return f"{verb} {argument.strip().lower()}. {len(emails)} email{'' if len(emails) == 1 else 's'} blocked."
    if command in ("grant", "revoke", "grants"):
        return _handle_grant_command(command, argument, actor)
    if command == "spend":
        return _render_install_spend()
    if command == "trail":
        return _render_trail(argument)
    return _USAGE


def _render_trail(argument: str) -> str:
    email = argument.strip()
    if not email:
        return "Usage: /trail <email>"
    trail = trail_by_email(DEFAULT_PROJECTS_ROOT, email)
    notes = trail_notes(trail)
    if not trail.own:
        return "\n".join([f"No account with email {email}.", *notes])
    rows = trail_rows(trail)

    def head(dropped: int) -> str:
        counted = f"{email}: {len(rows)} lines, UTC" + (
            f" ({dropped} earlier dropped)" if dropped else ""
        )
        return "\n".join([counted, *notes])

    room = _TELEGRAM_MAX_CHARS - len(head(len(rows)))
    kept = 0
    for row in reversed(rows):
        room -= len(row) + 1
        if room < 0:
            break
        kept += 1
    return "\n".join([head(len(rows) - kept), *rows[len(rows) - kept :]])


def _render_install_spend() -> str:
    rows = read_install_spend(DEFAULT_PROJECTS_ROOT)
    if not rows:
        return "No accounts on this install."
    torn = sum(1 for r in rows if r.unreadable)
    lines = [
        f"{len(rows)} account{'' if len(rows) == 1 else 's'}, "
        f"${sum(r.spent.used_usd for r in rows):.4f} billed + "
        f"${sum(r.spent.rate_priced_usd for r in rows):.4f} {RATE_PRICED_LABEL} / "
        f"{sum(r.spent.used_tokens for r in rows):,} tokens"
        + (f" (excludes {torn} unreadable)" if torn else "")
    ]
    for r in rows:
        who = r.email or r.user_id
        if r.unreadable:
            lines.append(f"{who}: unreadable — {r.unreadable}")
            continue
        over = overrun(r.ceilings, r.spent)
        flags = []
        if r.ceilings.usd is None:
            flags.append("host, unmetered")
        if r.spent.unpriced_tokens:
            flags.append(f"{r.spent.unpriced_tokens:,} unpriced")
        if over.usd_amount or over.token_amount:
            flags.append(f"⚠ ${over.usd_amount:.4f}/{over.token_amount:,} past ceiling")
        lines.append(
            f"{who}: ${r.spent.used_usd:.4f} billed + ${r.spent.rate_priced_usd:.4f} "
            f"{RATE_PRICED_LABEL} / {r.spent.used_tokens:,} tok · "
            f"{r.campaigns} campaigns / {r.cycles} cycles"
            + (f" · {'; '.join(flags)}" if flags else "")
        )
    return "\n".join(lines)


def _handle_grant_command(command: str, argument: str, actor: str) -> str:
    if command == "grants":
        held = grants()
        if not held:
            return "Delegations: (none)"
        lines: list[str] = []
        for sub, g in sorted(held.items()):
            caps_val = g.get("capabilities")
            caps_str = ", ".join(str(c) for c in caps_val) if isinstance(caps_val, list) else ""
            lines.append(f"{sub} → by {g.get('delegated_by', '?')}: {caps_str or '(none)'}")
        return "Delegations:\n" + "\n".join(lines)
    if command == "revoke":
        if not argument:
            return "Usage: /revoke <sub_user_id>"
        removed = revoke_grant(argument.strip(), actor=actor)
        return (
            f"Revoked {argument.strip()}." if removed else f"No delegation for {argument.strip()}."
        )
    parts = argument.split(maxsplit=1)
    if len(parts) < 2:
        return "Usage: /grant <sub_user_id> <capabilities>  (e.g. step,create)"
    sub_user_id, caps_csv = parts[0].strip(), parts[1]
    delegator = registered_user_id()
    if delegator is None:
        return "Cannot grant: no registered operator to delegate from (sign in once first)."
    try:
        caps = capabilities_from_names(caps_csv.split(","))
    except ValueError as exc:
        return f"Error: {exc}"
    if not caps:
        return f"Error: name at least one capability ({', '.join(sorted(CAMPAIGN_CAP_BY_NAME))})."
    try:
        grant(sub_user_id, delegator=delegator, capabilities=caps, actor=actor)
    except ValueError as exc:
        return f"Error: {exc}"
    return f"Granted {sub_user_id} [{', '.join(sorted(caps))}] as delegate of {delegator}."


def _send_message(client: httpx.Client, chat_id: str, text: str) -> None:
    try:
        resp = client.post("/sendMessage", json={"chat_id": chat_id, "text": text})
    except httpx.HTTPError:
        logger.warning("sendMessage failed", exc_info=True)
        return
    # Telegram refuses in the BODY, not the transport: a 400 is no `httpx.HTTPError`.
    if resp.status_code != 200:
        logger.warning("sendMessage refused: HTTP %d %s", resp.status_code, resp.text[:300])


def notify_operator(text: str) -> bool:
    """Best-effort: ``False`` and never a raise, so a notice cannot fail the request behind it."""
    token = settings.ADMIN_BOT_TELEGRAM_TOKEN.get_secret_value().strip()
    chat_id = settings.ADMIN_BOT_CHAT_ID.strip()
    if not token or not chat_id:
        logger.info("Operator notice skipped (admin bot not configured): %s", text)
        return False
    try:
        with httpx.Client(
            base_url=f"https://api.telegram.org/bot{token}", timeout=10, verify=tls_context()
        ) as client:
            resp = client.post("/sendMessage", json={"chat_id": chat_id, "text": text})
    except httpx.HTTPError:
        logger.warning("Operator notice failed to send", exc_info=True)
        return False
    # A refusal is a non-200 body, not an exception; True on one reports an unheard notice as heard.
    if resp.status_code != 200:
        logger.warning("Operator notice refused: HTTP %d %s", resp.status_code, resp.text[:300])
        return False
    return True


def check_health_and_tell() -> list[health.Checked]:
    """Every entry point's health check, each change of state already told. Blocking."""
    checked = asyncio.run(health.check(DEFAULT_PROJECTS_ROOT))
    for one in checked:
        if (said := health.notice(one)) is not None:
            notify_operator(said)
    return checked


def forward_new_account_to_crm(
    email: str | None, name: str | None, user_id: str, account_count: int, device: Device | None
) -> bool:
    url = settings.N8N_SIGNUP_WEBHOOK_URL.get_secret_value().strip()
    if not url:
        logger.info("CRM forward skipped (N8N_SIGNUP_WEBHOOK_URL not set): %s", email or user_id)
        return False
    try:
        with httpx.Client(timeout=10, verify=tls_context()) as client:
            client.post(
                url,
                json={
                    "email": email or "",
                    "name": name or "",
                    "use_case": "",
                    "signup_source": "promptpotter-app",
                    "account_count": account_count,
                    "device": device or "",
                },
            )
    except httpx.HTTPError:
        logger.warning("CRM forward failed for %s", email or user_id, exc_info=True)
        return False
    return True


def forward_trail_to_briefing() -> bool:
    """What every account did in the last day, and the box's health, sent OUT to n8n.

    Sent whole each time: the receiver keeps the newest, so a missed send costs freshness only,
    and a send that stops arriving is itself the box going quiet.
    """
    url = settings.N8N_TRAIL_WEBHOOK_URL.get_secret_value().strip()
    if not url:
        return False
    trails, unreadable = trails_since(DEFAULT_PROJECTS_ROOT, time.time() - _TRAIL_WINDOW_H * 3600)
    try:
        with httpx.Client(timeout=10, verify=tls_context()) as client:
            resp = client.post(
                url,
                headers={
                    "X-Trail-Secret": settings.N8N_TRAIL_WEBHOOK_SECRET.get_secret_value().strip()
                },
                json={
                    "as_of": utcnow_iso(),
                    "window_hours": _TRAIL_WINDOW_H,
                    "accounts": [
                        {"who": account.who, "lines": trail_fields(account)} for account in trails
                    ],
                    "unreadable": unreadable,
                    "health": health.briefing(DEFAULT_PROJECTS_ROOT),
                },
            )
    except httpx.HTTPError:
        logger.warning("Trail forward failed", exc_info=True)
        return False
    if resp.status_code != 200:
        logger.warning("Trail forward refused: HTTP %d", resp.status_code)
        return False
    return True


def _process_update(
    update: dict[str, Any], client: httpx.Client, chat_id: str, passphrase: str | None
) -> None:
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return
    chat = message.get("chat")
    if not isinstance(chat, dict) or str(chat.get("id")) != chat_id:
        logger.info(
            "Ignoring message from unauthorized chat %s",
            chat.get("id") if isinstance(chat, dict) else "?",
        )
        return
    text = message.get("text")
    if not isinstance(text, str):
        return
    parsed = parse_command(text, passphrase)
    if parsed is None:
        # The text stays out of the log: it carries the passphrase attempt.
        logger.info("Ignoring message (%d chars, gated=%s)", len(text), passphrase is not None)
        return
    command, argument = parsed
    reply = handle_command(command, argument, actor=f"telegram:{chat_id}")
    _send_message(client, chat_id, reply)


def run_bot(token: str, chat_id: str, passphrase: str | None) -> None:
    base_url = f"https://api.telegram.org/bot{token}"
    offset: int | None = None
    logger.info("Admin bot started (outbound long-poll; no inbound port).")
    with httpx.Client(
        base_url=base_url, timeout=_POLL_TIMEOUT_S + 10, verify=tls_context()
    ) as client:
        forward_due = 0.0
        while True:
            if time.monotonic() >= forward_due:
                forward_due = time.monotonic() + _TRAIL_FORWARD_EVERY_S
                try:
                    # Checked first, so the briefing carries what this check just found.
                    check_health_and_tell()
                    forward_trail_to_briefing()
                except Exception:
                    logger.exception("health check or trail forward failed; channel stays up")
            try:
                params: dict[str, Any] = {"timeout": _POLL_TIMEOUT_S}
                if offset is not None:
                    params["offset"] = offset
                resp = client.get("/getUpdates", params=params)
                if resp.status_code == 409:
                    # A webhook on the same bot is a CONFIGURATION error: no retry resolves it.
                    logger.error(
                        "getUpdates refused with 409: a webhook is active on this bot, so it cannot "
                        "also be long-polled. Give the admin channel its OWN bot token."
                    )
                    return
                resp.raise_for_status()
                updates = resp.json().get("result", [])
            except httpx.HTTPError:
                # The long-poll blocks only on SUCCESS: without the sleep an erroring one spins.
                logger.warning(
                    "getUpdates failed; retrying in %ss", _RETRY_BACKOFF_S, exc_info=True
                )
                time.sleep(_RETRY_BACKOFF_S)
                continue
            for update in updates:
                if not isinstance(update, dict):
                    continue
                offset = int(update["update_id"]) + 1
                try:
                    _process_update(update, client, chat_id, passphrase)
                except Exception:
                    # One handler's raise must not end the operator's only admin channel (decisions.md § 0004).
                    logger.exception("admin command failed; channel stays up")


def main() -> int:
    setup_logging()
    complete_registries(every_treatment=False)
    token = settings.ADMIN_BOT_TELEGRAM_TOKEN.get_secret_value().strip()
    chat_id = settings.ADMIN_BOT_CHAT_ID.strip()
    passphrase = settings.ADMIN_BOT_PASSPHRASE.get_secret_value().strip() or None
    if not token or not chat_id:
        logger.error(
            "ADMIN_BOT_TELEGRAM_TOKEN and ADMIN_BOT_CHAT_ID must be set "
            "(see deploy-linux/README.md § Admin bot)."
        )
        return 1
    try:
        run_bot(token, chat_id, passphrase)
    except KeyboardInterrupt:
        logger.info("Admin bot stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
