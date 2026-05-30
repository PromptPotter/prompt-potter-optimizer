---
status: accepted
date: 2026-05-30
deciders: [maintainer]
consulted: [identity-foundation, m12-control-plane]
informed: []
relates:
  - docs/adr/0002-identity-foundation.md
  - docs/adr/0001-m12-control-plane.md
supersedes: []
superseded-by: []
tags: [security, identity, operations, self-hosting, zero-trust, admin]
---

# Operator admin channels — privileged deployment mutations stay in-zone

## Context and Problem Statement

A self-hosted PromptPotter install has a small set of **deployment-admin**
actions that are neither campaign orchestration nor end-user identity: today the
sole one is editing the OIDC sign-in allowlist (`.promptpotter/identity/allowlist.json`,
read fresh on every callback by `check_allowlist`). The operator wants to perform
these from a phone — over Telegram — without SSHing into the box.

The allowlist is **the most security-critical file in the install**: it gates
authentication itself. A mistake here is not "a campaign mis-scored" — it is
"the wrong person can sign in." So the question is not "how do we wire a Telegram
command" but **"how does an untrusted message channel safely reach a control-plane
mutation, on an install a non-expert is hosting?"** Adoption depends on the answer
being secure *by default*, not secure-if-configured-perfectly.

Two prior instincts were wrong and are worth recording so they don't recur:

1. **"Expose an admin HTTP endpoint + bearer token."** Puts a privileged mutation
   on the public internet surface, with the only credential living in a third-party
   cloud (n8n / Railway). Breach of that SaaS, or a leaked token, lets an attacker
   add themselves to the auth gate. Single factor, maximal exposure.
2. **"Ride the `POST /commands/{kind}` Control-remote highway."** A category error.
   The allowlist is the **Identity** I/O kind (architecture.md §0), not Control-remote;
   it is deployment-global, not per-tenant; the campaign command highway is
   OIDC-session-gated for *humans* and lands records on a *tenant* ledger. Forcing a
   global identity mutation through it inverts the scope and conflates two I/O kinds.

## Decision Drivers

* **Zero-trust / Purdue segmentation.** A control-plane mutation must not be
  reachable from the lowest-trust zone. The public internet and any cloud SaaS
  (Telegram, n8n) are the lowest-trust zone; the box's identity config is the
  protected zone. Cross-zone access is brokered through a controlled conduit, never
  a direct exposure.
* **Secure-by-default self-hosting.** The design a non-expert deploys with copy-paste
  steps must be the *secure* one. "Easy to host" and "easy to host securely" must be
  the same path, or adoption produces insecure installs.
* **Minimal attack surface.** Prefer adding *no* new inbound listener over adding one
  that must then be hardened (TLS, auth, rate-limit, WAF).
* **Defense in depth.** More than one independent control gates the action.
* **I/O-kind honesty.** The action is an Identity-kind write; its delivery channel is
  a deployment-ops concern. Neither is campaign Control-remote.

## Considered Options

* **A — Public admin HTTP endpoint + bearer token.** A route on `app.promptpotter.dev`
  that edits the allowlist, authed by a static token n8n holds.
* **B — Ride the campaign Control-remote highway** (`allowlist-add` / `-remove` as
  command kinds + a machine-token branch in `resolve_identity`).
* **C — n8n drives, via a brokered conduit.** Keep n8n as orchestrator, but the admin
  endpoint is reachable *only* behind a Cloudflare Access **service token** (the tunnel
  edge authenticates the cross-zone request before it reaches the app), plus the app's
  own admin token — two layers.
* **D — On-box outbound bot.** A small process on the box long-polls the message
  channel *outbound* and edits the local file directly. No inbound surface; the
  privileged action never leaves the protected zone.

## Decision Outcome

Chosen option: **D — on-box outbound bot**, generalized into the **operator-admin
channel** pattern.

An operator-admin channel is: an **in-zone deployment-side process** that reaches an
untrusted message channel **outbound only** (no inbound listener, no new public route,
nothing added behind the tunnel to attack), authenticates the operator with
**defense-in-depth** (channel token + a pinned operator identity + an optional command
passphrase), mutates **Identity-kind** state through the sanctioned writer functions,
and records each change to an **identity-zone append-only audit log**
(`allowlist_audit.jsonl`), never the campaign ledger.

First instance: a Telegram bot (`promptpotter/presentation/admin_bot.py`) run as a
systemd service, long-polling `getUpdates`, dropping any update whose `chat.id` is not
the configured operator's, supporting `/allow`, `/deny`, `/list` over the allowlist
writers in `infrastructure/identity/allowlist.py`.

This is the most secure option *and* the cheapest to self-host: no Cloudflare Access
config, no SSH conduit, no exposed endpoint — one systemd unit and three env vars.

### Consequences

* **Good** — zero new inbound attack surface; `ss -tlnp` shows no new listener. The
  auth-gate mutation never leaves the protected zone.
* **Good** — secure-by-default: the documented install *is* the hardened one.
* **Good** — the credential (bot token) lives on the box, not in a third-party cloud.
* **Good** — defense in depth: chat-id lock + bot token + optional passphrase.
* **Good** — correct I/O-kind placement; no fake command kinds, no resolver changes,
  no tenant-ledger contortion for a global file.
* **Neutral** — the audit trail is an identity-zone JSONL, separate from the campaign
  ledger (by design — Identity is not event-sourced; see ADR-0002).
* **Bad** — the bot is a long-running process to supervise (mitigated: systemd
  auto-restart, same as the uvicorn + cloudflared units).
* **Bad / residual risk** — Telegram account or bot-token compromise. Mitigated by the
  chat-id lock and the optional passphrase; the token is rotatable in `.env`.

### When option C is the right escalation

If a future admin action genuinely must be *driven* by an external orchestrator
(n8n, a CI job) rather than initiated from the box, do **not** fall back to option A.
Use option C: expose the action only behind an edge service-token broker (Cloudflare
Access) **and** an app-level token — never a bare public endpoint. The on-box channel
(D) remains the default; C is the exception that still honors the zero-trust rule.

### No-drift gate

**A privileged identity or deployment mutation is never exposed as an inbound public
route.** It is delivered by an operator-admin channel (in-zone, outbound conduit) or,
where an external driver is unavoidable, behind an edge-broker + app-token conduit
(option C). A PR adding an inbound, internet-reachable route that mutates
identity/deployment config is a block. Adding a new operator-admin action amends
architecture.md §0 (Identity kind) first, per the CLAUDE.md pre-flight Q4 sub-rule and
ADR-0002 gate #5.

### Confirmation

`tests/test_control_plane_drift.py::test_adr_anchor_files_exist` includes this ADR in
its set — every file named in the Anchors table below must exist on disk. The §0
amendment naming the operator-admin channel landed before the bot code, per the
sequencing rule.

## More Information

### Setup (operator-facing)

Full steps live in [`../operations/secure-hosting.md`](../operations/secure-hosting.md)
and [`../../deploy-linux/README.md`](../../deploy-linux/README.md). In brief: create a
bot with @BotFather (token), find your numeric `chat_id`, put
`ADMIN_BOT_TELEGRAM_TOKEN` / `ADMIN_BOT_CHAT_ID` (+ optional `ADMIN_BOT_PASSPHRASE`)
in `.env`, run `deploy-linux/install-allowlist-bot.sh`, then message the bot
`/allow you@example.com`.

### Out of scope

* **Other admin actions** (secret rotation, restart, health) — the channel pattern
  generalizes to them, but each is its own change that amends §0 when added.
* **Authorization model** — the operator is the single deployment admin; per-admin RBAC
  is post-M13 (rides ADR-0002's `capabilities`).
* **A webapp admin surface** — managing the allowlist from `/ui` would be a *human*
  OIDC-gated surface; possible later, orthogonal to this out-of-band channel.

### Cross-refs

- [`0002-identity-foundation.md`](0002-identity-foundation.md) — the Identity I/O kind;
  this ADR adds its administrative-write facet + delivery channel.
- [`0001-m12-control-plane.md`](0001-m12-control-plane.md) — the campaign Control-remote
  highway this action deliberately does **not** ride.
- [`../architecture.md`](../architecture.md) §0 — the Identity-kind amendment naming the
  operator-admin channel.

### Anchors

Every claim names a file. Path existence asserted by
`tests/test_control_plane_drift.py::test_adr_anchor_files_exist`.

| Concern | File |
|---|---|
| §0 Identity-kind amendment (admin facet + operator-admin channel) | `docs/architecture.md` |
| Allowlist writers + audit (`add_email` / `remove_email` / `list_emails`) | `promptpotter/infrastructure/identity/allowlist.py` |
| Identity-zone paths (`allowlist`, `allowlist_audit`) | `promptpotter/infrastructure/identity/paths.py` |
| On-box admin bot (first operator-admin channel) | `promptpotter/presentation/admin_bot.py` |
| systemd installer for the bot | `deploy-linux/install-allowlist-bot.sh` |
| Operator-facing secure-hosting guide | `docs/operations/secure-hosting.md` |
| Identity foundation (the kind this extends) | `docs/adr/0002-identity-foundation.md` |
