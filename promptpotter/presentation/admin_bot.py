from __future__ import annotations

import logging
import sys
import time
from collections.abc import Callable
from typing import Any

import httpx

from promptpotter.application import health, notices
from promptpotter.application.account import (
    block_email,
    blocked_emails,
    grant,
    grants,
    registered_user_id,
    revoke_grant,
    trail_by_email,
    unblock_email,
)
from promptpotter.application.jobs.install_spend import read_install_spend
from promptpotter.application.jobs.quota import overrun
from promptpotter.application.maintenance.landing import land_at_entry
from promptpotter.application.power import PowerLevel
from promptpotter.application.views.trail import trail_notes, trail_rows
from promptpotter.config.logging import setup_logging
from promptpotter.config.paths import default_projects_root
from promptpotter.config.settings import settings
from promptpotter.domain.spend import RATE_PRICED_LABEL, usd_text
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
_HEALTH_EVERY_S = 1800
_POWER_EVERY_S = 60
_DIGEST_EVERY_S = 600


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
        if command == "block":
            emails = block_email(argument, actor=actor)
            verb = "Blocked"
        else:
            emails = unblock_email(argument, actor=actor)
            verb = "Unblocked"
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
    trail = trail_by_email(default_projects_root(), email)
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
    rows = read_install_spend(default_projects_root())
    if not rows:
        return "No accounts on this install."
    torn = sum(1 for r in rows if r.unreadable)
    lines = [
        f"{len(rows)} account{'' if len(rows) == 1 else 's'}, "
        f"{usd_text(sum(r.spent.total.used_usd for r in rows))} billed + "
        f"{usd_text(sum(r.spent.total.rate_priced_usd for r in rows))} {RATE_PRICED_LABEL} / "
        f"{sum(r.spent.total.used_tokens for r in rows):,} tokens"
        + (f" (excludes {torn} unreadable)" if torn else "")
    ]
    for r in rows:
        who = r.email or r.user_id
        if r.unreadable:
            lines.append(f"{who}: unreadable — {r.unreadable}")
            continue
        over = overrun(r.face, r.spent.host)
        spent = r.spent.total
        flags = []
        if r.face.usd is None:
            flags.append("host, unmetered")
        if spent.unpriced_tokens:
            flags.append(f"{spent.unpriced_tokens:,} unpriced")
        if r.spent.user.used_tokens:
            flags.append(f"{usd_text(r.spent.user.sent_usd)} on its own keys")
        if over.usd_amount or over.token_amount:
            flags.append(f"⚠ {usd_text(over.usd_amount)}/{over.token_amount:,} past its coupon")
        lines.append(
            f"{who}: {usd_text(spent.used_usd)} billed + {usd_text(spent.rate_priced_usd)} "
            f"{RATE_PRICED_LABEL} / {spent.used_tokens:,} tok · "
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
            caps_str = ", ".join(sorted(g.capabilities))
            lines.append(f"{sub} → by {g.delegated_by or '?'}: {caps_str or '(none)'}")
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
    caps = capabilities_from_names(caps_csv.split(","))
    if not caps:
        return f"Error: name at least one capability ({', '.join(sorted(CAMPAIGN_CAP_BY_NAME))})."
    grant(sub_user_id, delegator=delegator, capabilities=caps, actor=actor)
    return f"Granted {sub_user_id} [{', '.join(sorted(caps))}] as delegate of {delegator}."


class _Duties:
    """What the bot does on its own clock; none waits on a command arriving, or on polling at all."""

    def __init__(self) -> None:
        self._power = PowerLevel.MAINS
        self._roster: tuple[tuple[float, Callable[[], None]], ...] = (
            (_HEALTH_EVERY_S, self._health_and_trail),
            (_POWER_EVERY_S, self._watch_power),
            (_DIGEST_EVERY_S, notices.tell_visits_digest),
        )
        self._due = [0.0] * len(self._roster)

    def _health_and_trail(self) -> None:
        # Checked first, so the briefing carries what this check just found.
        notices.check_and_tell()
        notices.forward_trail_to_briefing()

    def _watch_power(self) -> None:
        self._power = notices.tell_power(self._power)

    def run_due(self) -> float:
        """Every duty that is due runs; the answer is the seconds until the next one."""
        for index, (every_s, duty) in enumerate(self._roster):
            if time.monotonic() >= self._due[index]:
                self._due[index] = time.monotonic() + every_s
                try:
                    duty()
                except Exception:
                    logger.exception("%s failed; the channel stays up", duty.__name__)
        return max(1.0, min(self._due) - time.monotonic())


def _process_update(update: dict[str, Any], chat_id: str, passphrase: str | None) -> None:
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
    try:
        reply = handle_command(command, argument, actor=f"telegram:{chat_id}")
    except Exception as exc:
        # The refusal is the reply: an invalid identity file, a bad argument, a crash alike.
        logger.exception("admin command /%s failed", command)
        reply = f"Error: {exc}"
    notices.answer_operator(reply)


def run_bot(token: str, chat_id: str, passphrase: str | None) -> None:
    base_url = f"https://api.telegram.org/bot{token}"
    offset: int | None = None
    duties = _Duties()
    polling = True
    # The first answer since start holds the channel again, whatever an earlier poll was refused.
    answered = False
    logger.info("Admin bot started (outbound long-poll; no inbound port).")
    with httpx.Client(
        base_url=base_url, timeout=_POLL_TIMEOUT_S + 10, verify=tls_context()
    ) as client:
        while True:
            wait_s = duties.run_due()
            if not polling:
                time.sleep(wait_s)
                continue
            try:
                params: dict[str, Any] = {"timeout": int(min(_POLL_TIMEOUT_S, wait_s))}
                if offset is not None:
                    params["offset"] = offset
                resp = client.get("/getUpdates", params=params)
                if (refused := health.telegram_refusal(resp.status_code)) is not None:
                    # No retry changes it: polling stops, the bot's own duties go on, and the
                    # refusal is its channel's standing, told now and once.
                    logger.error("getUpdates stops: %s", refused)
                    polling = False
                    notices.tell_poll(token, refused)
                    continue
                resp.raise_for_status()
                updates = resp.json().get("result", [])
                if not answered:
                    answered = True
                    notices.tell_poll(token, None)
            except httpx.HTTPError as blip:
                # The long-poll blocks only on SUCCESS: without the sleep an erroring one spins.
                logger.warning(
                    "getUpdates failed (%s); retrying in %ss",
                    type(blip).__name__,
                    _RETRY_BACKOFF_S,
                )
                time.sleep(_RETRY_BACKOFF_S)
                continue
            for update in updates:
                if not isinstance(update, dict):
                    continue
                offset = int(update["update_id"]) + 1
                try:
                    _process_update(update, chat_id, passphrase)
                except Exception:
                    # One handler's raise must not end the operator's only admin channel (decisions.md § 0004).
                    logger.exception("admin command failed; channel stays up")


def main() -> int:
    setup_logging()
    land_at_entry(every_treatment=False)
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
