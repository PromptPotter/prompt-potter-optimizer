"""On-box admin bot — the first operator-admin channel (ADR-0004).

Long-polls Telegram **outbound only** and edits the local sign-in allowlist
(`.promptpotter/identity/allowlist.json`, re-read on every callback — edits are
instant). Opens no inbound port; the privileged auth-gate mutation never leaves
the protected zone. This is the secure-by-default alternative to exposing an
admin HTTP endpoint (the threat model is `docs/adr/0004-operator-admin-channels.md`).

Run as a systemd service (`deploy-linux/install-allowlist-bot.sh`) or directly::

    python -m promptpotter.presentation.admin_bot

Config (environment / `.env`):

* ``ADMIN_BOT_TELEGRAM_TOKEN`` — bot token from @BotFather (required).
* ``ADMIN_BOT_CHAT_ID`` — the numeric chat id the bot obeys; messages from any
  other chat are silently ignored (required; locks the bot to the operator).
* ``ADMIN_BOT_PASSPHRASE`` — optional command prefix as a second factor.

Commands (only from the locked chat)::

    /allow <email>   add to the allowlist
    /deny  <email>   remove from the allowlist
    /list            show the current allowlist
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import httpx

from promptpotter.config.logging import setup_logging
from promptpotter.infrastructure.identity.allowlist import add_email, list_emails, remove_email
from promptpotter.infrastructure.identity.paths import default_identity_paths

logger = logging.getLogger(__name__)

_POLL_TIMEOUT_S = 50
_USAGE = "Commands:\n/allow <email>\n/deny <email>\n/list"


def parse_command(text: str, passphrase: str | None) -> tuple[str, str] | None:
    """Parse a message into ``(command, argument)``.

    When *passphrase* is set, the message must start with it (second factor);
    the passphrase is stripped before parsing. Returns ``None`` when the text
    fails the passphrase gate or carries no recognizable command.
    """
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
    """Apply *command* to the allowlist and return the reply text."""
    paths = default_identity_paths()
    if command == "list":
        emails = list_emails(paths.allowlist)
        return "Allowlist:\n" + ("\n".join(emails) if emails else "(empty)")
    if command in ("allow", "deny"):
        if not argument:
            return f"Usage: /{command} <email>"
        try:
            if command == "allow":
                emails = add_email(
                    paths.allowlist, argument, actor=actor, audit_path=paths.allowlist_audit
                )
                verb = "Added"
            else:
                emails = remove_email(
                    paths.allowlist, argument, actor=actor, audit_path=paths.allowlist_audit
                )
                verb = "Removed"
        except ValueError as exc:
            return f"Error: {exc}"
        return f"{verb} {argument.strip().lower()}. Allowlist now has {len(emails)} entr{'y' if len(emails) == 1 else 'ies'}."
    return _USAGE


def _send_message(client: httpx.Client, chat_id: str, text: str) -> None:
    try:
        client.post("/sendMessage", json={"chat_id": chat_id, "text": text})
    except httpx.HTTPError:
        logger.warning("sendMessage failed", exc_info=True)


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
        return
    command, argument = parsed
    reply = handle_command(command, argument, actor=f"telegram:{chat_id}")
    _send_message(client, chat_id, reply)


def run_bot(token: str, chat_id: str, passphrase: str | None) -> None:
    """Outbound long-poll loop. Blocks forever (until SIGTERM / Ctrl-C)."""
    base_url = f"https://api.telegram.org/bot{token}"
    offset: int | None = None
    logger.info("Allowlist admin bot started (outbound long-poll; no inbound port).")
    with httpx.Client(base_url=base_url, timeout=_POLL_TIMEOUT_S + 10) as client:
        while True:
            try:
                params: dict[str, Any] = {"timeout": _POLL_TIMEOUT_S}
                if offset is not None:
                    params["offset"] = offset
                resp = client.get("/getUpdates", params=params)
                resp.raise_for_status()
                updates = resp.json().get("result", [])
            except httpx.HTTPError:
                logger.warning("getUpdates failed; retrying", exc_info=True)
                continue
            for update in updates:
                if not isinstance(update, dict):
                    continue
                offset = int(update["update_id"]) + 1
                _process_update(update, client, chat_id, passphrase)


def main() -> int:
    setup_logging()
    from dotenv import load_dotenv

    load_dotenv()  # best-effort: load CWD .env for local runs; systemd uses EnvironmentFile
    token = os.environ.get("ADMIN_BOT_TELEGRAM_TOKEN", "").strip()
    chat_id = os.environ.get("ADMIN_BOT_CHAT_ID", "").strip()
    passphrase = os.environ.get("ADMIN_BOT_PASSPHRASE", "").strip() or None
    if not token or not chat_id:
        logger.error(
            "ADMIN_BOT_TELEGRAM_TOKEN and ADMIN_BOT_CHAT_ID must be set "
            "(see docs/operations/secure-hosting.md)."
        )
        return 1
    try:
        run_bot(token, chat_id, passphrase)
    except KeyboardInterrupt:
        logger.info("Allowlist admin bot stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
