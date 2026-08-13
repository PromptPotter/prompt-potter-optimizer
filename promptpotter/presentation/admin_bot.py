"""The operator-admin channel (ADR-0004) — long-polls Telegram OUTBOUND only and edits the allowlist + delegations. It
opens no inbound port, which is the entire point: the privileged auth-gate mutation never leaves the protected zone.

The same charter covers the one-shot outbound notices the API process fires when an account first exists
(``notify_operator``, ``forward_new_account_to_crm``): deployment-side, outbound-only, best-effort, no inbound door."""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import httpx

from promptpotter.config.logging import setup_logging
from promptpotter.infrastructure.identity.allowlist import add_email, list_emails, remove_email
from promptpotter.infrastructure.identity.grants import (
    grant_principal,
    list_grants,
    revoke_principal,
)
from promptpotter.infrastructure.identity.migration import registered_user_id
from promptpotter.infrastructure.identity.paths import IdentityPaths, default_identity_paths
from promptpotter.shared.identity import capabilities_from_tiers

logger = logging.getLogger(__name__)

_POLL_TIMEOUT_S = 50
_USAGE = (
    "Commands:\n/allow <email>\n/deny <email>\n/list\n"
    "/grant <sub_user_id> <tiers>\n/revoke <sub_user_id>\n/grants"
)


def parse_command(text: str, passphrase: str | None) -> tuple[str, str] | None:
    """Parse a message into ``(command, argument)``. When *passphrase* is set the message must START with it (the second
    factor), and it is stripped before parsing; a failed gate returns ``None``."""
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
    if command in ("grant", "revoke", "grants"):
        return _handle_grant_command(command, argument, actor, paths)
    return _USAGE


def _handle_grant_command(command: str, argument: str, actor: str, paths: IdentityPaths) -> str:
    """The delegation facet: /grants (list), /grant <sub> <tiers>, /revoke <sub>."""
    if command == "grants":
        grants = list_grants(paths.grants)
        if not grants:
            return "Delegations: (none)"
        lines: list[str] = []
        for sub, g in sorted(grants.items()):
            caps_val = g.get("capabilities")
            caps_str = ", ".join(str(c) for c in caps_val) if isinstance(caps_val, list) else ""
            lines.append(f"{sub} → by {g.get('delegated_by', '?')}: {caps_str or '(none)'}")
        return "Delegations:\n" + "\n".join(lines)
    if command == "revoke":
        if not argument:
            return "Usage: /revoke <sub_user_id>"
        removed = revoke_principal(
            paths.grants,
            sub_principal_user_id=argument.strip(),
            actor=actor,
            audit_path=paths.grants_audit,
        )
        return (
            f"Revoked {argument.strip()}." if removed else f"No delegation for {argument.strip()}."
        )
    # /grant <sub_user_id> <tiers>
    parts = argument.split(maxsplit=1)
    if len(parts) < 2:
        return "Usage: /grant <sub_user_id> <tiers>  (tiers e.g. step,create)"
    sub_user_id, tiers_csv = parts[0].strip(), parts[1]
    delegator = registered_user_id(paths.default_claim_marker)
    if delegator is None:
        return "Cannot grant: no registered operator to delegate from (sign in once first)."
    try:
        caps = capabilities_from_tiers(tiers_csv.split(","))
    except ValueError as exc:
        return f"Error: {exc}"
    if not caps:
        return "Error: name at least one tier (step, run, create, budget, lifecycle, babysit)."
    try:
        grant_principal(
            paths.grants,
            sub_principal_user_id=sub_user_id,
            delegated_by_user_id=delegator,
            capabilities=caps,
            spend_ceiling_usd=None,
            note=f"via {actor}",
            actor=actor,
            audit_path=paths.grants_audit,
        )
    except ValueError as exc:
        return f"Error: {exc}"
    return f"Granted {sub_user_id} [{', '.join(sorted(caps))}] as delegate of {delegator}."


def _send_message(client: httpx.Client, chat_id: str, text: str) -> None:
    try:
        client.post("/sendMessage", json={"chat_id": chat_id, "text": text})
    except httpx.HTTPError:
        logger.warning("sendMessage failed", exc_info=True)


def notify_operator(text: str) -> bool:
    """One-shot outbound notice on the SAME channel the bot polls (ADR-0004): same token, same chat lock,
    no inbound door. Callable from the API process, which is a different process from ``run_bot`` — this
    opens its own short-lived client rather than sharing one.

    Best-effort by contract: an unconfigured bot or a dead network returns ``False`` and never raises, so
    a notice can't fail the request that triggered it. Returns whether it was sent.
    """
    token = os.environ.get("ADMIN_BOT_TELEGRAM_TOKEN", "").strip()
    chat_id = os.environ.get("ADMIN_BOT_CHAT_ID", "").strip()
    if not token or not chat_id:
        logger.info("Operator notice skipped (admin bot not configured): %s", text)
        return False
    try:
        with httpx.Client(base_url=f"https://api.telegram.org/bot{token}", timeout=10) as client:
            client.post("/sendMessage", json={"chat_id": chat_id, "text": text})
    except httpx.HTTPError:
        logger.warning("Operator notice failed to send", exc_info=True)
        return False
    return True


def forward_new_account_to_crm(email: str | None, name: str | None, user_id: str) -> bool:
    """Announce a new account to the n8n ``signup-intake`` door, which logs it and writes the CRM row.

    Every account joins the CRM on arrival; curation happens afterwards. The alternative — a row that
    appears only when the operator taps a button — means the contact record depends on someone being at
    their phone, and an untapped signup simply never exists.

    n8n holds the Google Sheets credentials, so this announces the account rather than writing the row
    itself: one system owns that surface, and it is not this one. Same charter as ``notify_operator``
    (ADR-0004) — outbound-only, no inbound door, and best-effort by contract, so an unconfigured webhook
    or a dead network returns ``False`` and never raises. A CRM hiccup must not fail the sign-in that
    created the account. Returns whether it was sent.
    """
    url = os.environ.get("N8N_SIGNUP_WEBHOOK_URL", "").strip()
    if not url:
        logger.info("CRM forward skipped (N8N_SIGNUP_WEBHOOK_URL not set): %s", email or user_id)
        return False
    try:
        with httpx.Client(timeout=10) as client:
            client.post(
                url,
                json={
                    "email": email or "",
                    "name": name or "",
                    "use_case": "",
                    "signup_source": "promptpotter-app",
                },
            )
    except httpx.HTTPError:
        logger.warning("CRM forward failed for %s", email or user_id, exc_info=True)
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
