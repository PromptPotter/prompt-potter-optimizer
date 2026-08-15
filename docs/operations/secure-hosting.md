# Hosting PromptPotter securely

> **The full access model** — the three tiers, every enforcement point, and the box
> deploy checklist — is [`access-model.md`](access-model.md). This page is just the one
> admin task you repeat.

For the operator running PromptPotter on their own box (the
[Linux deploy](../../deploy-linux/README.md)): the one admin task you'll repeat —
**taking access away from an account** — and the rule that keeps it safe.

Signing up is the grant. Anyone completing OIDC gets an account that can act immediately,
bounded by a lifetime spend ceiling rather than by your approval — the contract is
[ADR-0003 § Spend](../adr/0003-spend-and-tenancy.md), the ceiling is
`Settings.FREE_TIER_SPEND_CAP_USD`. So there is no queue to work through, and the only
recurring admin action is the reverse one.

## The one rule

**A control-plane change never has an inbound door open to the internet.**

The blocklist is your front-door lock, so its edit never sits behind a public endpoint
(reachable by the whole internet and any cloud service holding a key). Instead the edit
happens **on the box**, which reaches *out* to your phone — nothing new is exposed. This
is the standard zero-trust / Purdue posture (protected zone never directly reachable from
the lowest-trust zone); full rationale in [ADR-0004](../adr/0004-operator-admin-channels.md).

## Blocking an account from Telegram

The blocklist lives at `.promptpotter/identity/blocklist.json` and is re-read on every
request, so **edits take effect instantly — no restart, no re-login**. You manage it with an
on-box admin bot that long-polls Telegram (outbound only — it opens no port).

It is a courtesy control, not a boundary: a blocked person can sign up again from another
address and land in a fresh account with a fresh ceiling. The ceiling is what actually bounds
a stranger; this is what stops one you have already met.

### One-time setup

1. **Create a bot.** Message [@BotFather](https://t.me/BotFather) on Telegram, send
   `/newbot`, follow the prompts. Copy the **bot token** it gives you.
2. **Find your chat id.** Message your new bot anything, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and read
   `message.chat.id` (a number). This locks the bot to you.
3. **Put the secrets in the env file** (on the box, `$ENV_FILE` from `deploy.config` — default
   `$INSTALL_DIR/.env`, `0600`, never committed). Every key below is a `Settings` field, so it
   resolves from the process environment *or* from the install's own `.env`
   (`config/paths.py::env_file_path`) — a laptop needs no `$ENV_FILE` at all. On a box, put them
   in `$ENV_FILE` anyway: one home is what keeps the two from drifting.
   ```bash
   ADMIN_BOT_TELEGRAM_TOKEN=123456:AA...           # from BotFather
   ADMIN_BOT_CHAT_ID=987654321                      # your numeric chat id
   ADMIN_BOT_PASSPHRASE=optional-extra-word         # optional 2nd factor (see below)
   HOST_ADMIN_EMAIL=you@example.com                 # who may claim this box (see below)
   HOST_ADMIN_ISSUER=https://accounts.google.com    # ...and via which provider
   ```

   **`ADMIN_BOT_PASSPHRASE` belongs in the BOT's file, not the app's**, once you set
   `BOT_ENV_FILE` (`deploy.config`). It is the second factor on inbound `/block` and `/grant`,
   and only the bot daemon reads it — a copy in the API's environment makes a read of that
   process into command authority. The token and chat id must stay in **both**: `auth.py`
   imports `notify_operator` and announces new sign-ins on the same bot.
   `HOST_ADMIN_EMAIL` is separate from the bot and required on a hosted box. It names the one
   sign-in allowed to write the claim marker that grants the host-admin tier. Leave it unset and
   no browser identity ever claims the box, which also leaves the terminal on the `default`
   tenant while every browser session resolves its own — the app logs a warning saying so.
4. **Install the service:**
   ```bash
   cd ~/deploy-linux
   ./install-admin-bot.sh
   ```
   This runs the bot under systemd (auto-restart, starts on boot), the same way the app
   and tunnel run.

### Daily use

Message your bot:

| You send | Effect |
|---|---|
| `/block alice@example.com` | Withdraws access. She stays signed in and keeps her account; every command she sends is refused from the next request on. |
| `/unblock alice@example.com` | Gives it back. |
| `/blocked` | Replies with everyone currently blocked. |
| `/grant <sub_user_id> step,create` | Delegates an **attenuated** sub-principal (ADR-0005): the delegate acts in your workspace holding only those capability tiers. |
| `/revoke <sub_user_id>` | Removes a delegation (the user reverts to owning only their own empty workspace). |
| `/grants` | Replies with the current delegations. |

If you set `ADMIN_BOT_PASSPHRASE`, prefix the command with it:
`my-word /block alice@example.com`. Messages from any chat id other than yours are
silently ignored.

Delegation tiers are `step, run, create, budget, lifecycle, babysit` (see the access
model). A `<sub_user_id>` is the canonical id shown in the delegate's own account modal
(`/auth/me`); the delegator is you (the registered operator). The grant lives in the
sealed `.promptpotter/identity/grants.json` a delegate cannot write, and its capabilities
are clamped to yours at every use — a grant can never exceed what you hold.

Every change is recorded to `.promptpotter/identity/blocklist_audit.jsonl` (blocks) or
`grants_audit.jsonl` (delegations) — an audit trail you can `cat` on the box.

## New accounts into your CRM (optional)

Signing in *is* signing up, and the blocklist only ever takes an account away again. So a new
account is a contact worth keeping the moment it arrives. Set one more key in the same env file
(the app service reads it through `EnvironmentFile`, exactly as the bot does):

```bash
N8N_SIGNUP_WEBHOOK_URL=https://<your-n8n>/webhook/<the-workflow's-path>
```

The app then POSTs `{email, name, use_case, signup_source, account_count}` there the first time a new account
calls `/auth/me`, and the receiving workflow logs it and writes the CRM row. Leave the key unset
and nothing is sent — the forward is best-effort and never fails a sign-in
(`admin_bot.py::forward_new_account_to_crm`).

**Copy the path from the workflow's webhook node, not from its file name** — the two drift, and a
`POST`-only webhook answers *"not registered"* to the browser GET you would naturally test it with,
so a wrong path and a live-but-unreachable one look identical. Confirm with the receiving side's
own API instead of by probing the URL.

**This does not contradict the section below.** The traffic is outbound-only and carries contact
details, never a credential: n8n cannot call back, holds no token of yours, and a breach there
reaches your mailing list, not your auth gate. The rule that stays is the direction of travel —
nothing external may *drive* an admin action.

## Why not just expose an admin endpoint?

Because that puts your front-door lock on the public internet behind a single token —
and if that token sits in a cloud tool (n8n, Zapier, CI), a breach there hands over your
auth gate. The on-box bot has no endpoint, no inbound surface, and the key never leaves
the box. If you *must* let an external tool drive an admin action, gate it behind an edge
broker (Cloudflare Access service token) **plus** an app token — never a bare public route
([ADR-0004 § "When option C is the right escalation"](../adr/0004-operator-admin-channels.md)).

## Secret hygiene checklist

- `.env` is `chmod 600` and **never committed** (it holds the bot token + API keys).
- Rotate `ADMIN_BOT_TELEGRAM_TOKEN` (re-issue via @BotFather) if it ever leaks; paste the new
  value into every env file that carries it — the app's *and* the bot's, if you split them —
  then restart both units. The unit is `promptpotter-admin-bot`; the `-allowlist-bot` this line
  named for a while is the retired pre-blocklist service.
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
