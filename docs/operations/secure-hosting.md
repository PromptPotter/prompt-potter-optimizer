# Hosting PromptPotter securely

> **The full access model** — the three tiers, every enforcement point, and the box
> deploy checklist — is [`access-model.md`](access-model.md). This page is just the one
> admin task you repeat.

For the operator running PromptPotter on their own box (the
[Linux deploy](../../deploy-linux/README.md)): the one admin task you'll repeat —
**managing who can sign in** — and the rule that keeps it safe.

## The one rule

**A control-plane change never has an inbound door open to the internet.**

The allowlist is your front-door lock, so its edit never sits behind a public endpoint
(reachable by the whole internet and any cloud service holding a key). Instead the edit
happens **on the box**, which reaches *out* to your phone — nothing new is exposed. This
is the standard zero-trust / Purdue posture (protected zone never directly reachable from
the lowest-trust zone); full rationale in [ADR-0004](../adr/0004-operator-admin-channels.md).

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
| `/grant <sub_user_id> step,create` | Delegates an **attenuated** sub-principal (ADR-0005): the delegate acts in your workspace holding only those capability tiers. |
| `/revoke <sub_user_id>` | Removes a delegation (the user reverts to owning only their own empty workspace). |
| `/grants` | Replies with the current delegations. |

If you set `ADMIN_BOT_PASSPHRASE`, prefix the command with it:
`my-word /allow alice@example.com`. Messages from any chat id other than yours are
silently ignored.

Delegation tiers are `step, run, create, budget, lifecycle, babysit` (see the access
model). A `<sub_user_id>` is the canonical id shown in the delegate's own account modal
(`/auth/me`); the delegator is you (the registered operator). The grant lives in the
sealed `.promptpotter/identity/grants.json` a delegate cannot write, and its capabilities
are clamped to yours at every use — a grant can never exceed what you hold.

Every change is recorded to `.promptpotter/identity/allowlist_audit.jsonl` (allowlist) or
`grants_audit.jsonl` (delegations) — an audit trail you can `cat` on the box.

## Why not just expose an admin endpoint?

Because that puts your front-door lock on the public internet behind a single token —
and if that token sits in a cloud tool (n8n, Zapier, CI), a breach there hands over your
auth gate. The on-box bot has no endpoint, no inbound surface, and the key never leaves
the box. If you *must* let an external tool drive an admin action, gate it behind an edge
broker (Cloudflare Access service token) **plus** an app token — never a bare public route
([ADR-0004 § "When option C is the right escalation"](../adr/0004-operator-admin-channels.md)).

## Secret hygiene checklist

- `.env` is `chmod 600` and **never committed** (it holds the bot token + API keys).
- Rotate `ADMIN_BOT_TELEGRAM_TOKEN` (re-issue via @BotFather) if it ever leaks; paste
  the new value into `.env` and `sudo systemctl restart promptpotter-allowlist-bot`.
- Don't stack Cloudflare Access in front of the app *and* the OIDC gate — that's a
  double-gate; pick one. (The bot is independent of either.)
- Verify no surprise listener after install: `ss -tlnp` should show **no new port** for
  the bot — it's outbound-only.

## See also

- [Access model](access-model.md) — the three tiers, every enforcement point, the deploy checklist.
- [Linux deploy](../../deploy-linux/README.md) — the full install (systemd + Cloudflare
  Tunnel + OIDC + allowlist).
- [ADR-0004 — operator admin channels](../adr/0004-operator-admin-channels.md) — the
  threat model and the design decision.
- [ADR-0002 — identity foundation](../adr/0002-identity-foundation.md) — the OIDC gate
  the allowlist sits in front of.
