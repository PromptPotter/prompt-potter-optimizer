# The access model — four tiers, four boundaries

> **The one page a security audit opens.** It names each trust boundary, the *kind*
> of boundary it is, where it is enforced (by symbol), and the one honestly-deferred
> gap. If a claim here disagrees with the code, the code wins and this page is wrong —
> fix it.

PromptPotter has four access tiers — **host-admin > owner > delegate > loop** — and they
are **four different kinds of boundary**. Conflating them is what made the model
illegible; keeping them distinct, and never collapsing the hierarchy, is the whole design.

| Boundary | Kind | Enforced by | Failure response |
|---|---|---|---|
| **host-admin ↔ user** | Authorization (host privilege) | the operator-admin channel only (ADR-0004, chat-id lock) — no API-side capability | channel: ignored |
| **owner ↔ delegate** | Authorization (capability) | one dispatcher gate (`_require_capability_for` over `CAP_FOR_KIND`) | 404 (existence-hiding) |
| **user ↔ user** | Tenancy (data isolation) | structural directory rooting + one `load_owned` ownership rule | 404 |
| **loop ↔ everything** | OS privilege | systemd-hardened unit (kernel-enforced) | process denied (EACCES / cgroup) |

> **A dataset read is not an authorization decision — it belongs to no tier.**
> Repo `datasets/` is install content — **tracked in git**, hence already on the disk
> of anyone holding the install, so a capability over it would guard nothing while
> blanking every panel bound to such a campaign. **Ownership, not permission, is the
> split:** install content ships and is readable; private data belongs in the tenant,
> where Tier 2 isolates it structurally. Putting a private cut in the repo dir and then
> gating the dir is the anti-pattern — move the cut. (`datasets.benchmarks.read` was
> exactly that mistake and is gone; do not re-add it to Tier 1a.)

---

## Tier 1a — host-admin ↔ user: host privilege

The person who **runs the box** is not the same principal as a user who owns a tenant on
it, and the two must never collapse — on the team-online deployment (our default), every
signup is a user, and an ENTITLED user holds nearly all of an owner's rights. What separates a
host admin is a small, explicitly-named set, never an implicit "and also…".

**What host-admin can do arrives entirely through the operator-admin channel**
(`presentation/admin_bot.py` — the sign-in blocklist, `/grant`, `/revoke`, provider config),
which [ADR-0004](../adr/0004-operator-admin-channels.md) fixes as outbound-only and
explicitly **not** an inbound API route. **No `/commands/{kind}` verb is admin-only**, so the
person running the box presses exactly the buttons its users press.

That is a decision, not an absence. The one verb that used to sit here —
`set-sample-lookahead`, arming the scoring walk to hold several of a candidate's samples in
flight — spends the **box's** shared provider key and rate bucket rather than the campaign's
budget, so a user holding it can throttle every other user to finish sooner. It was
host-admin for exactly that reason, and moved to `campaign.lookahead` (Tier 1b) when the
operator chose to let a downloaded install and a signed-up account both press it. What bounds
the abuse now is the per-account spend ceiling plus the delegate carve: it is its OWN rung in
`CAMPAIGN_CAP_BY_TIER`, so a host can withhold it from a delegate without withholding the
run. It is still deliberately **not** `campaign.babysit` — babysit marks a cycle whose
measurement an operator steered, and this verb cannot steer one (the overshoot sample is
discarded precisely so the recorded rows stay identical at either depth). The ceiling and what
one press buys are the CONNECTOR's declarations; the tier answers only who may press.

**It is reachable from the browser only** — no CLI verb, no config key, no dataset knob. It is
also the one command whose address may DESCEND (`payload.descend`), because the arming is not
inherited into a nested run and each layer is therefore armed by naming it. That
inverts `<entry-point-parity>` on purpose: the surfaces a capability is *absent* from are part
of its gate, since the CLI is where automation and AI assistants operate. Adding a verb "for
parity" removes the boundary. Noted in root `CLAUDE.md` § Conventions so it is not re-litigated
as an oversight.

**Who is host-admin** is the chat-id lock on the ADR-0004 channel, and nothing else asks. The
default-claim marker (`HOST_ADMIN_EMAIL` → `maybe_claim_default`) survives it, but it now
answers only *which tenant the terminal resolves* — never a capability, so a box with no
marker is a workspace question rather than a privilege one.

**Dataset reads are NOT part of this tier**, and adding them back is the regression to
watch for. `infrastructure/store/dataset_access.py` is a resolver, not a gate: tenant
content first, then install content, no capability consulted. Tier 2 is what keeps one
user's data from another's; a capability was never what did that work.

---

## Tier 1b — owner ↔ delegate: authorization

**What a principal may do** is one definition: `CAMPAIGN_CAP_BY_TIER` in
`shared/identity.py`, from which `OWNER_COMMAND_CAPABILITIES` is *derived* so the two can
never drift. Adding a power = one line there — and it is the ONLY capability set, since host
privilege rides the ADR-0004 channel rather than a capability.

**Who holds what:** every **entitled** authenticated user owns their own tenant and holds the full
owner set (`_identity_context_from_session`, `presentation/api/middleware/oidc.py`); a **pending**
one owns the same tenant and holds nothing. The single local operator gets the full set from
`default_identity` (`shared/identity.py`) on the CLI / auth-off path, where no blocklist stands. A
**delegate** holds an attenuated subset — see below.

**Command-verb authorization (ADR-0005).** Every
control-plane command requires a **tier capability** — `CAMPAIGN_{STEP,RUN,CREATE,BUDGET,LIFECYCLE,BABYSIT}_CAP`
(`shared/identity.py`, enumerated once as `CAMPAIGN_CAP_BY_TIER`). Enforcement is a
**second one-chokepoint**: `_require_capability_for` reads `CAP_FOR_KIND[kind]` at the
dispatcher's `_record_and_apply` (`command_dispatcher.py`) — the single site
every command funnels through — before applying. An import-time assert keeps `CAP_FOR_KIND`
exhaustive over the closed kind set. Same 404 posture. A first-class tenant owner holds the
full `OWNER_COMMAND_CAPABILITIES`, so single-owner installs are unaffected.

**Delegated sub-principals (ADR-0005 §1).** A user may delegate an **attenuated** slice of
their rights to a sub-principal (a friendly sub-user / an AI assistant reaching in). The grant
lives in the **sealed grant store** — `.promptpotter/identity/grants.json`, the same protected
identity zone as the blocklist, which a delegate cannot write (no self-escalation). At the OIDC
seam, `_identity_context_from_session` resolves the grant and rebinds the delegate to act
inside the delegator's tenant with `grant ∩ owner` capabilities — **attenuation is enforced at
read**, so a hand-edited over-grant is clamped, and a malformed/no-delegator grant fails secure
(own tenant, zero caps). The command audit records the delegate itself (`claims["principal"]`),
not the delegator it acts as. Provisioned only through the operator-admin channel
(`admin_bot.py`: `/grant`, `/revoke`, `/grants`); the grant writer rejects a delegator that is
itself a sub-principal (**one-level delegation**, enforced). A grant's **spend ceiling** is
enforced — `admit_launch` reads a sub-principal's declaration down to its grant before the wallet
admits or refuses that declaration whole.
The **`campaign.babysit`** cap gates a direct edit of an engine-locked value: unlocking the
model/provider axis in a `fork-cycle` seed requires it, stamps the cycle babysat, and forces
its runs to grade `C` (excluded from digest / reuse / L4). The **`campaign.step`** rung gates
`step-cycle` (advance N rounds in place then auto-pause) — a delegate with step-but-not-run can
advance bounded work but cannot fire an autonomous loop. **Deferred (honestly):** channel-scoped
grants (require unspoofable channel identity) and the babysat *subtree* model.

---

## Tier 2 — user ↔ user: tenancy

**Cross-tenant isolation is structural, not a check.** `build_stores`
(`infrastructure/store/stores.py`) roots every leaf store at `projects_root / tenant_id`; a
`Stores` object cannot name another tenant's directory, so cross-tenant reads are physically
impossible. `Stores.tenant_id` is a derived property off the identity, never an independent
field. Today `tenant_id == user_id` (one tenant per operator).

**Ownership within a tenant is one rule:** `CampaignStore.load_owned(campaign_id, owner_user_id)`
returns the campaign iff it exists *and* is owned, else `None` — a missing and a cross-owner
campaign collapse to the same 404. Its four callers (the command dispatcher's
`_load_owned_campaign`, and the campaign detail / config-map / storage read routes) keep their own
error text; only the ownership predicate lives in `load_owned`.

**One deliberate exception — not a bug:** `routers/origins.py` is **tenant-scoped, not
owner-scoped** (documented in-code). A CLI-minted campaign is owned by the registered-developer
`user_id`, which differs from a browser OIDC session's `user_id` *within one tenant*, so
owner-filtering would hide the operator's own origins. Tenant isolation still holds.

**All write commands** flow through one `CommandDispatcher`
(`presentation/api/middleware/command_dispatcher/`) — the sole inbound writer, stamping
`issued_by_user_id` and appending a `CommandRecord` to the ledger. The read API is otherwise
read-only.

---

## Tier 3 — loop ↔ everything: OS privilege

This is the genuinely-partial boundary; the honest state:

- **CLI-launched runs** (`python -m promptpotter`) are already a **separate OS process** from the
  API.
- **Web-launched runs** (`/commands/start-run`) run **in-process** in the API worker by deliberate
  design (`JobRegistry` capacity-1, orphan-reaping assumes one process). So a web-launched loop
  shares the API's process, `.env`, and every provider key.

**Shipped wall (3a) — the hardened service unit** (`deploy-linux/install-service.sh`): the
systemd unit drops all capabilities, `ProtectSystem=strict`, a `@system-service` syscall filter,
the kernel-protection set, and an optional `MemoryMax`. This is kernel-enforced and bounds
**both** the API and the in-process loop it hosts. The writable surface is `DATA_DIR` when set,
else `$INSTALL_DIR` — and only the first takes away the service's ability to rewrite its own
source, its venv and its `.env`, which is persistence rather than disclosure. Unset warns.

**Partly applied (3b) — the dedicated loop principal.** The `.env` **secret split** is available
now (`BOT_ENV_FILE`): the admin bot is already its own unit, so its `ADMIN_BOT_PASSPHRASE` — the
second factor on inbound `/block` and `/grant` — leaves the API's environment, and a read of that
process stops conferring command authority. The bot's token and chat id must stay in both, since
the API sends on them too (`auth.py` on a new sign-in, `main.py` on shutdown). Still absent: a `promptpotter-loop` service
user and `ReadWritePaths` scoped to the cycle tree alone — that half only bites CLI-launched runs
until 3c, so it stays gated on 3c.

**Deferred (3c), named honestly — the web-launch split:** making the web-launched loop a separate
sandboxed process. It fights the current single-process design (a few hundred LOC + delicate
JobRegistry coordination) and guards a low-probability threat (our own optimizer code) in the
current single-operator / small-team model. **Until 3c lands, web-launched loops are bounded by 3a
only, not the full 3b wall.**

**The TRIGGER is the first time a tenant can supply anything EXECUTABLE** — a custom node, a plugin
connector, arbitrary Python. Until then the requirement is undefined, and that was audited rather
than assumed: across every tenant-controlled path into the API worker, the scoring formula is
AST-allowlisted, YAML is `safe_load`ed, slugs are regex-validated, and the provider registry is
closed and never tenant-set. Nobody can supply code, so a boundary built now is built against a
guess — and the guess decides the shape (subprocess vs trust model vs container). It also fights
L4, whose recursion spawns each inner campaign as an in-process `asyncio` task. Waiting costs
nothing **while the launch seam stays single** (`application/embedded_run.py`,
`jobs/launcher/mint_and_start.py`); let run-launch logic spread across call sites and it stops
being cheap.

The one place the loop `eval()`s an external string — the scoring formula — is fenced by an AST
allowlist (`application/scoring/formula/compiler.py`); the formula source is operator/tenant config,
not backend-supplied.

---

## The perimeter (every tier that goes online)

- **One public port behind Cloudflare Tunnel** (outbound-only; no inbound router port). uvicorn
  binds `127.0.0.1` with `--proxy-headers --forwarded-allow-ips=127.0.0.1`. TermNorm binds
  `127.0.0.1:8000`, never tunneled.
- **AuthN:** Google OIDC — RS256 pinned, signature verified, `iss`/`aud`/`exp`/`iat`/`sub` checked,
  and an `email` returned only where the issuer vouched for it. Missing session → 401 at
  `resolve_identity` (`deps.py`). Session cookie is
  httponly / secure / samesite=lax, opaque id (no JWT past the middleware — ADR-0002).
- **Sign-up is open AND signing up is the grant.** Anyone completing OIDC gets an account holding
  `OWNER_COMMAND_CAPABILITIES` over their own tenant. What bounds them is money, not approval: the
  free-tier lifetime ceilings (`Settings.FREE_TIER_SPEND_CAP_USD` and `FREE_TIER_TOKEN_CAP`, composed
  at `quota.py::admit_launch`). **Both units, because the USD one can go blind** — a billed
  call with no resolvable rate leaves the money total a floor, so the token ceiling is what still
  holds and the USD arm falls back to `Settings.UNPRICED_GRACE_USD`. A launch is **admitted at what
  it declares or refused**, never clamped to the remainder — a delegated sub-principal's grant is
  the one read-down, and [ADR-0005](../adr/0005-delegated-principals-and-capability-scoping.md) §5
  owns why — and holds that ceiling as a reservation
  while it runs — [ADR-0003](../adr/0003-spend-and-tenancy.md)'s D1 owns why, including the overrun
  the account never sees. Every path that sets a ceiling composes there,
  `change-spend-budget` included: it writes the file `_usd_cap` prefers over the launch-composed
  cap, so an unclamped one is the way around this whole section.
  `oidc.py::resolve_access_state` (re-read live) answers
  `blocked` only for an email the operator has revoked; a `blocked` account resolves to an EMPTY
  capability set, so Tier 1b's dispatcher gate refuses its every command with the same 404 a stranger
  gets. Nothing else re-checks.
- **Who may claim the box is DECLARED, never inferred.** The claim marker `maybe_claim_default`
  writes is what names the box's own tenant — the workspace a terminal run and a browser session
  share — and entitlement can no longer stand in front of it now
  that everyone is entitled — so `auth.py::_is_declared_host_admin` reads `Settings.HOST_ADMIN_EMAIL`,
  and `HOST_ADMIN_ISSUER` too where that is set (empty accepts any issuer, so no deployed box
  changes until an operator sets it). An email is a CLAIM a provider makes and two providers are
  wired, so the address alone would let whichever has the weaker email handling assert the declared
  one. Unset EMAIL means no browser identity ever claims the box. Inferring it would hand a
  fresh public box to whichever stranger signed in first.
- **Blocklist edits never have an inbound door** — delivered out-of-band by the on-box,
  outbound-only Telegram bot (`presentation/admin_bot.py`, ADR-0004). This is the zero-trust rule.
- **Response headers** (`main.py::SecurityHeadersMiddleware`, one middleware): `nosniff`, `X-Frame-Options:
  DENY`, `Referrer-Policy`, HSTS on https; CSP strict (`default-src 'none'`) on JSON API paths,
  frame-only on the webapp document; `Cache-Control: no-store` on `/api/v1/*`.
- **PP↔TermNorm** is authenticated with a shared bearer token (`Authorization: Bearer`,
  constant-time compared on the TermNorm side) plus TermNorm's IP allowlist. Both are already
  implemented; the deploy provisions the shared secret.
- **Per-user quotas / spend caps** (`application/jobs/quota.py`) + capacity-1 run admission.

---

## Audit map — where each claim lives

| Concern | Look at |
|---|---|
| Entitlement (one derivation, feeds caps + the served state) | `middleware/oidc.py::resolve_access_state`; the browser reads it as `MeResponse.access_state` |
| Host privilege (no capability set — the channel IS the tier) | [ADR-0004](../adr/0004-operator-admin-channels.md) + `presentation/admin_bot.py` |
| What an authenticated session holds | `middleware/oidc.py::_session_capabilities` — the owner set, or nothing if blocked |
| Who may CLAIM the box (declared, not inferred) | `auth.py::_is_declared_host_admin` over `Settings.HOST_ADMIN_EMAIL` + `HOST_ADMIN_ISSUER` |
| Whether an email may act as an identity at all | `identity/verifier.py` (`email_verified` required; absent counts as unverified) and `identity/github.py` (the verified list only, never the profile field) |
| Who is exempt from free-tier metering (one definition, two readings) | `quota.py::_is_host` — the terminal, or the identity that claimed the box. `spends_the_hosts_own_key` reads it off a LIVE identity (no issuer); `is_host_tenant_dir` off a DIRECTORY walk (the un-renamed `projects/default/`), which has no session to ask. Only the terminal DETECTOR differs, and merging the two is what would let an identity that merely omits an issuer resolve as the operator — the anonymous-tier trap |
| What every account spent + produced (cross-tenant, ADR-0004 channel only) | `jobs/install_spend.py::read_install_spend`, rendered by `admin_bot.py`'s `/spend` — never an inbound route |
| Dataset resolution (NOT a capability gate) | `store/dataset_access.py::readable_dataset_dir` — tenant content, then install content |
| Command-verb gate (the one chokepoint) | `command_dispatcher.py::_require_capability_for` + `CAP_FOR_KIND` |
| Command tier caps (one enumeration) | `shared/identity.py::CAMPAIGN_CAP_BY_TIER`, `OWNER_COMMAND_CAPABILITIES` |
| Sealed sub-principal grant store | `infrastructure/identity/grants.py` (`.promptpotter/identity/grants.json`) |
| Delegation attenuation (enforced at read) | `grants.py::resolve_effective_capabilities`, `middleware/oidc.py::_delegated_identity` |
| Dataset visibility gateway | `infrastructure/store/dataset_access.py` |
| Tenant isolation (structural) | `infrastructure/store/stores.py::build_stores` |
| Ownership rule (one definition) | `campaign_store/store.py::load_owned` |
| Sole inbound writer | `middleware/command_dispatcher.py` |
| AuthN resolver + 401 | `deps.py::resolve_identity`, `middleware/oidc.py` |
| Out-of-band admin channel | `presentation/admin_bot.py` + [ADR-0004](../adr/0004-operator-admin-channels.md) |
| Response security headers | `main.py::SecurityHeadersMiddleware` |
| Loop OS wall (systemd) | `deploy-linux/install-service.sh` |
| PP→TermNorm credential | `connectors/termnorm.py`, `infrastructure/backend.py` |

---

## Deploy actions — the Linux box checklist

Run on the box for the next test-linux update; each is idempotent.

1. **Apply the hardened unit** — `./install-service.sh`. Then confirm it started:
   `systemctl status $APP_NAME` and the health curl. If it fails to start, the first suspects are
   `ProtectSystem=strict` (add the offending write path to `ReadWritePaths`) and `SystemCallFilter`
   (`journalctl -u $APP_NAME -e | grep -i 'signal\|syscall'`). `MemoryDenyWriteExecute` was
   deliberately **omitted** — add it only after a clean smoke test.
2. **(Optional) cgroup bound** — set `MEMORY_MAX="2G"` (or similar) in `deploy.config`, re-run
   `install-service.sh`. Turns the pp-self memory-starvation OS-kill into a clean cgroup OOM.
3. **Provision the PP↔TermNorm token.** On a **first** install `bootstrap.sh` generates the shared
   `TERMNORM_TOKEN` (and, with `BACKEND_DIR` set, writes both `.env` files). On an **already-installed
   box `bootstrap.sh` will not re-run** — its `REPO_URL` guard exits first — so set the same token in
   both `.env` files directly: `TERMNORM_TOKEN=<hex>` in PromptPotter's `.env`, and the same
   `TERMNORM_TOKEN` + `TERMNORM_REQUIRE_AUTH=true` in TermNorm's `.env`.
4. **Restart BOTH services** — TermNorm to start requiring the token, and PromptPotter because it
   reads `TERMNORM_TOKEN` once at boot (`settings` is loaded at startup, not per call):
   `sudo systemctl restart termnorm && sudo systemctl restart promptpotter`. Verify with a GET to
   `127.0.0.1:8000/status`: **no** `Authorization` → **401**; `-H "Authorization: Bearer <token>"` →
   **200**. (PP's `/status` reachability probe uses a separate unauthenticated client but only checks
   TCP connect, so enabling auth doesn't break it.)
5. **Confirm TermNorm's IP allowlist** (`backend-api/config/users.json`) lists PromptPotter's source
   IP — `127.0.0.1` is already there, so co-located loopback is fine; a *remote* PP needs its IP
   added, or remove `/matches` from `protected_paths` and rely on the bearer token alone.
6. **Do not run TermNorm's dev launcher in prod** — `start-server-py-LLMs.sh` binds `0.0.0.0:8000`;
   the systemd unit binds loopback. (Pinning the dev script to `127.0.0.1` is a later TermNorm-side
   cleanup.)
7. **Verify no surprise listener** — `ss -tlnp` shows only loopback `:8000` / `:8001` and
   cloudflared's outbound; nothing on a routable interface.
8. **Firewall (operator decision, not scripted)** — everything already binds loopback and ingress is
   outbound-tunnel-only, so a host firewall is defense-in-depth with real SSH-lockout risk on a
   remote box. If you want it, add a conservative default-deny-inbound rule that **preserves SSH** by
   hand — do not wire it into `bootstrap.sh`.

**Still open (design, not a box step):** tier-3 **3b** (dedicated loop user + secret split) and
**3c** (web-launch out-of-process) — see the Tier-3 section above for the honest gating.

---

## Running it securely — the one admin task you repeat

Signing up **is** the grant. Anyone completing OIDC gets an account that can act immediately, bounded by `Settings.FREE_TIER_SPEND_CAP_USD` rather than by your approval ([ADR-0003](../adr/0003-spend-and-tenancy.md) § Spend). There is no queue to work through, so the only recurring admin action is the reverse one — **taking access away**.

That ceiling is spent one **step** at a time (`Settings.FREE_TIER_LAUNCH_STEP_USD`, applied by `quota.py::_launch_step`): the offer is denominated in runs rather than in credit, so a launch declares a step instead of the whole remainder and a first campaign can no longer consume the grant a tenth one was promised. It rations the anonymous grant only — a delegated principal answers to its attenuated ceiling instead, and an account you raised by hand on `user.json` keeps what you gave it.

**The one rule: a control-plane change never has an inbound door open to the internet.** The blocklist is the front-door lock, so its edit never sits behind a public endpoint. The edit happens **on the box**, which reaches *out* to your phone — nothing new is exposed. Exposing an admin endpoint instead would put that lock on the public internet behind a single token, and a token living in a cloud tool (n8n, Zapier, CI) turns a breach there into your auth gate. If an external tool genuinely must drive an admin action, gate it behind an edge broker (Cloudflare Access service token) **plus** an app token — never a bare public route ([ADR-0004](../adr/0004-operator-admin-channels.md) § "When option C is the right escalation").

### Blocking an account from Telegram

`.promptpotter/identity/blocklist.json` is re-read on every request, so **edits take effect instantly — no restart, no re-login**. An on-box admin bot long-polls Telegram (outbound only — it opens no port).

It is a courtesy control, not a boundary: a blocked person can sign up again from another address into a fresh account with a fresh ceiling. The ceiling is what bounds a stranger; this is what stops one you have already met.

**One-time setup.** Create a bot via [@BotFather](https://t.me/BotFather) (`/newbot`) and copy the token. Message the bot, then read `message.chat.id` from `https://api.telegram.org/bot<TOKEN>/getUpdates` — that number locks the bot to you. Put the secrets in the env file (on a box, `$ENV_FILE` from `deploy.config` — default `$INSTALL_DIR/.env`, `0600`, never committed). Every key is a `Settings` field, so it resolves from the process environment *or* the install's own `.env` (`config/paths.py::env_file_path`); a laptop needs no `$ENV_FILE` at all, but on a box use it anyway — one home is what keeps the two from drifting.

```bash
ADMIN_BOT_TELEGRAM_TOKEN=123456:AA...           # from BotFather
ADMIN_BOT_CHAT_ID=987654321                      # your numeric chat id
ADMIN_BOT_PASSPHRASE=optional-extra-word         # optional 2nd factor
HOST_ADMIN_EMAIL=you@example.com                 # who may claim this box
HOST_ADMIN_ISSUER=https://accounts.google.com    # ...and via which provider
```

**`ADMIN_BOT_PASSPHRASE` belongs in the BOT's file, not the app's**, once `BOT_ENV_FILE` is set (`deploy.config`). It is the second factor on inbound `/block` and `/grant`, and only the bot daemon reads it — a copy in the API's environment makes a read of that process into command authority. Token and chat id must stay in **both**: the API announces new sign-ins (`auth.py`) and its own shutdown (`main.py`) on the same bot, and without them `notify_operator` returns False and logs — the bot keeps answering commands, so nothing reports the loss. `install-admin-bot.sh` warns when a split leaves them out. `HOST_ADMIN_EMAIL` is separate and required on a hosted box; it names the one sign-in allowed to write the claim marker granting the host-admin tier. Leave it unset and no browser identity ever claims the box, which also leaves the terminal on the `default` tenant while every browser session resolves its own — the app logs a warning saying so. Then `cd ~/deploy-linux && ./install-admin-bot.sh` runs the bot under systemd, the same way the app and tunnel run.

**Daily use** — message your bot:

| You send | Effect |
|---|---|
| `/block alice@example.com` | Withdraws access. She stays signed in and keeps her account; every command she sends is refused from the next request on. |
| `/unblock alice@example.com` | Gives it back. |
| `/blocked` | Replies with everyone currently blocked. |
| `/grant <sub_user_id> step,create` | Delegates an **attenuated** sub-principal (ADR-0005): the delegate acts in your workspace holding only those capability tiers. |
| `/revoke <sub_user_id>` | Removes a delegation (the user reverts to owning only their own empty workspace). |
| `/grants` | Replies with the current delegations. |

With `ADMIN_BOT_PASSPHRASE` set, prefix **every** message with it — it is not a session, so a bare `/spend` is refused like any other: `my-word /block alice@example.com`. A refused message and one that never arrived look identical in Telegram; `journalctl -u <service>-admin-bot` is where they differ (`Ignoring message (N chars, gated=…)`). Messages from any chat id other than yours are ignored the same way. Delegation tiers are the ladder above; a `<sub_user_id>` is the canonical id shown in the delegate's own account modal (`/auth/me`). The grant lives in the sealed `.promptpotter/identity/grants.json` a delegate cannot write, and its capabilities are clamped to yours at every use — a grant can never exceed what you hold. Every change is recorded to `blocklist_audit.jsonl` or `grants_audit.jsonl` beside it, an audit trail you can `cat` on the box.

### New accounts into your CRM (optional)

Signing in *is* signing up, so a new account is a contact worth keeping the moment it arrives. Set `N8N_SIGNUP_WEBHOOK_URL=https://<your-n8n>/webhook/<path>` in the same env file and the app POSTs `{email, name, use_case, signup_source, account_count}` there the first time a new account calls `/auth/me`. Leave it unset and nothing is sent — the forward is best-effort and never fails a sign-in (`admin_bot.py::forward_new_account_to_crm`).

**Copy the path from the workflow's webhook node, not from its file name** — the two drift, and a `POST`-only webhook answers *"not registered"* to the browser GET you would naturally test it with, so a wrong path and a live-but-unreachable one look identical. Confirm with the receiving side's own API rather than by probing the URL.

This does not contradict the one rule: the traffic is outbound-only and carries contact details, never a credential. n8n cannot call back and holds no token of yours, so a breach there reaches your mailing list, not your auth gate. What stays fixed is the direction of travel — nothing external may *drive* an admin action.

### Secret hygiene

- `.env` is `chmod 600` and **never committed** (it holds the bot token + API keys).
- Rotate `ADMIN_BOT_TELEGRAM_TOKEN` (re-issue via @BotFather) if it leaks; paste the new value into every env file carrying it — the app's *and* the bot's, if you split them — then restart both units. The unit is `promptpotter-admin-bot`; the `-allowlist-bot` this line named for a while is the retired pre-blocklist service.
- Don't stack Cloudflare Access in front of the app *and* the OIDC gate — that's a double-gate; pick one. (The bot is independent of either.)
- Verify no surprise listener after install: `ss -tlnp` should show **no new port** for the bot — it is outbound-only.

---

## See also

- [ADR-0002](../adr/0002-identity-foundation.md) — identity foundation (OIDC, RLS staging).
- [ADR-0003](../adr/0003-spend-and-tenancy.md) — spend + tenancy.
- [ADR-0004](../adr/0004-operator-admin-channels.md) — the operator-admin channel threat model.
- [`backend-integration.md`](backend-integration.md) § Connection security — the PP↔TermNorm wire.
