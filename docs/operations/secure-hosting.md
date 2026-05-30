# Hosting PromptPotter securely

This page is for the operator running PromptPotter on their own box (the
[Linux deploy](../../deploy-linux/README.md)). It covers the one security-critical
admin task you'll do repeatedly — **managing who can sign in** — and the rule that
keeps the whole thing safe.

## The one rule

**A control-plane change never has an inbound door open to the internet.**

The sign-in allowlist (who may log into your install) is the most sensitive thing you
control: editing it is editing your front-door lock. So we never put that edit behind a
public web endpoint where the whole internet — and any cloud service holding a key —
can reach it. Instead, the edit happens **on the box**, and the box reaches *out* to
your phone. Nothing new is exposed; nothing new can be attacked from outside.

This is the standard zero-trust / Purdue-model posture: the protected zone (your box)
is never directly reachable from the lowest-trust zone (the public internet, a chat
app, a cloud automation tool). The full rationale is
[ADR-0004](../adr/0004-operator-admin-channels.md); you don't need to read it to host
safely.

## Managing the allowlist from Telegram

The allowlist lives at `.promptpotter/identity/allowlist.json` and is re-read on every
sign-in, so **edits take effect instantly — no restart**. You manage it with an on-box
admin bot that long-polls Telegram (outbound only — it opens no port).

### One-time setup

1. **Create a bot.** Message [@BotFather](https://t.me/BotFather) on Telegram, send
   `/newbot`, follow the prompts. Copy the **bot token** it gives you.
2. **Find your chat id.** Message your new bot anything, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and read
   `message.chat.id` (a number). This locks the bot to you.
3. **Put the secrets in `.env`** (on the box, in `$INSTALL_DIR/.env`, `0600`, never
   committed):
   ```bash
   ADMIN_BOT_TELEGRAM_TOKEN=123456:AA...           # from BotFather
   ADMIN_BOT_CHAT_ID=987654321                      # your numeric chat id
   ADMIN_BOT_PASSPHRASE=optional-extra-word         # optional 2nd factor (see below)
   ```
4. **Install the service:**
   ```bash
   cd ~/deploy-linux
   ./install-allowlist-bot.sh
   ```
   This runs the bot under systemd (auto-restart, starts on boot), the same way the app
   and tunnel run.

### Daily use

Message your bot:

| You send | Effect |
|---|---|
| `/allow alice@example.com` | Adds the email to the allowlist (she can now sign in). |
| `/deny alice@example.com` | Removes the email (she can no longer sign in). |
| `/list` | Replies with the current allowlist. |

If you set `ADMIN_BOT_PASSPHRASE`, prefix the command with it:
`my-word /allow alice@example.com`. Messages from any chat id other than yours are
silently ignored.

Every change is recorded to `.promptpotter/identity/allowlist_audit.jsonl` (who, what,
when) — an audit trail you can `cat` on the box.

## Why not just expose an admin endpoint?

Because that would put your front-door lock on the public internet, secured by a single
token — and if that token sits in a cloud automation tool (n8n, Zapier, a CI job), a
breach there hands an attacker your auth gate. The on-box bot avoids this entirely:
no endpoint, no inbound surface, the key never leaves your box. If you ever *must* let
an external tool drive an admin action, do it behind an edge broker
(Cloudflare Access service token) **plus** an app token — never a bare public route.
See [ADR-0004 § "When option C is the right escalation"](../adr/0004-operator-admin-channels.md).

## Secret hygiene checklist

- `.env` is `chmod 600` and **never committed** (it holds the bot token + API keys).
- Rotate `ADMIN_BOT_TELEGRAM_TOKEN` (re-issue via @BotFather) if it ever leaks; paste
  the new value into `.env` and `sudo systemctl restart promptpotter-allowlist-bot`.
- Don't stack Cloudflare Access in front of the app *and* the OIDC gate — that's a
  double-gate; pick one. (The bot is independent of either.)
- Verify no surprise listener after install: `ss -tlnp` should show **no new port** for
  the bot — it's outbound-only.

## See also

- [Linux deploy](../../deploy-linux/README.md) — the full install (systemd + Cloudflare
  Tunnel + OIDC + allowlist).
- [ADR-0004 — operator admin channels](../adr/0004-operator-admin-channels.md) — the
  threat model and the design decision.
- [ADR-0002 — identity foundation](../adr/0002-identity-foundation.md) — the OIDC gate
  the allowlist sits in front of.
