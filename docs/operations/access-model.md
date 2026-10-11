# The access model — four boundaries

> **The one page a security audit opens.** It names each trust boundary, the *kind*
> of boundary it is, where it is enforced (by symbol), and the one honestly-deferred
> gap. If a claim here disagrees with the code, the code wins and this page is wrong —
> fix it.

PromptPotter has four access boundaries — **host-admin > owner > delegate > loop** — and they
are **four different kinds of boundary**. Conflating them is what made the model
illegible; keeping them distinct, and never collapsing the hierarchy, is the whole design.

| Boundary | Kind | Enforced by | Failure response |
|---|---|---|---|
| **host-admin ↔ user** | Authorization (host privilege) | the operator-admin channel only (ADR-0004, chat-id lock) — no API-side capability | channel: ignored |
| **owner ↔ delegate** | Authorization (capability) | one dispatcher gate (`_require_capability_for` over `CommandKind.capability`) | 404 (existence-hiding) |
| **user ↔ user** | Tenancy (data isolation) | structural directory rooting + one `owned_campaign` ownership rule | 404 |
| **loop ↔ everything** | OS privilege | systemd-hardened unit (kernel-enforced) | process denied (EACCES / cgroup) |

> **A dataset read is not an authorization decision — it belongs to no boundary.**
> Repo `datasets/` is install content — **tracked in git**, hence already on the disk
> of anyone holding the install, so a capability over it would guard nothing while
> blanking every panel bound to such a campaign. **Ownership, not permission, is the
> split:** install content ships and is readable; private data belongs in the tenant,
> where the tenancy boundary isolates it structurally. Putting a private cut in the repo dir and then
> gating the dir is the anti-pattern — move the cut, and never add a `datasets.*` capability
> to the host-privilege boundary. `infrastructure/store/dataset_access.py::readable_dataset_dir`
> is a resolver, not a gate: tenant content first, then install content, no capability consulted.

---

## host-admin ↔ user — host privilege

The person who **runs the box** is not the same principal as a user who owns a tenant on
it, and the two must never collapse — on the team-online deployment (our default), every
signup is a user, and an ENTITLED user holds nearly all of an owner's rights. What separates a
host admin is a small, explicitly-named set, never an implicit "and also…".

**What host-admin can do arrives entirely through the operator-admin channel**
(`presentation/admin_bot.py` — the sign-in blocklist, `/grant`, `/revoke`, provider config),
which [ADR-0004](../adr/0004-operator-admin-channels.md) fixes as outbound-only and
explicitly **not** an inbound API route. **No `/commands/{kind}` verb is admin-only**, so the
person running the box presses exactly the buttons its users press.

That is a decision, not an absence, and `set-sample-lookahead` is the verb that tests it. Arming
the scoring round to hold several calls in flight spends the **box's** shared provider key and
rate bucket rather than the campaign's budget, so a user holding it can throttle every other user
to finish sooner. It sits at `campaign.lookahead` (the authorization boundary) all the same, so
that a downloaded install and a signed-up account can both press it. What bounds
the abuse is the per-account spend ceiling plus the delegate carve: it is its OWN rung in
`CAMPAIGN_CAP_BY_NAME`, so a host can withhold it from a delegate without withholding the
run. It is deliberately **not** `campaign.babysit` — babysit marks a cycle whose
measurement an operator steered, and this verb cannot steer one (the overshoot sample is
discarded precisely so the recorded rows stay identical at either depth). The ceiling is the
CONNECTOR's declaration; how far past a possible cut the walk may reach — and so how deep an
`auto` arming, which names no number, actually runs — is the stop rules'
([`candidate-elimination.md`](../methods/candidate-elimination.md)); the boundary answers only
who may press.

**It is reachable from the browser only** — no CLI verb, no config key, no dataset knob. It is
also the one command whose address may DESCEND (`payload.descend`), because the arming is not
inherited into a nested run and each layer is therefore armed by naming it. That
inverts `<entry-point-parity>` on purpose: the surfaces a capability is *absent* from are part
of its gate, since the CLI is where automation and AI assistants operate. Adding a verb "for
parity" removes the boundary (root `CLAUDE.md` § Conventions).

**Who is host-admin** is the chat-id lock on the ADR-0004 channel, and nothing else asks. The
default-claim marker (`HOST_ADMIN_EMAIL` → `maybe_claim_default`) answers only *which tenant the
terminal resolves* — never a capability, so a box with no marker is a workspace question rather
than a privilege one. What the channel reads across tenants — every account's spend and output,
`jobs/install_spend.py::read_install_spend` behind `/spend` — is never an inbound route either.

---

## owner ↔ delegate — authorization

**What a principal may do** is one definition: `CAMPAIGN_CAP_BY_NAME` in
`shared/identity.py`, from which `OWNER_COMMAND_CAPABILITIES` is *derived* so the two can
never drift. Adding a power = one line there — and it is the ONLY capability set, since host
privilege rides the ADR-0004 channel rather than a capability.

**Who holds what:** every **entitled** authenticated user owns their own tenant and holds the full
owner set (`_identity_context_from_session` → `_session_capabilities`,
`presentation/api/middleware/oidc.py`); a **pending** one owns the same tenant and holds nothing.
The single local operator gets the full set from `default_identity` (`shared/identity.py`) on the
CLI / auth-off path, where no blocklist stands. A **delegate** holds an attenuated subset.

**The capability → verb ladder, delegated sub-principals, the per-grant spend ceiling, babysat and
the bounded step verb** — owned by
[ADR-0005](../adr/0005-delegated-principals-and-capability-scoping.md) §1 and §3–§6, with what is
deferred in its §2 and §4; this page names only where each is enforced (the one-level rule at
`grants.py::grant_principal`). The verb gate is
`_require_capability_for` over `CommandKind.capability` at the dispatcher's `_record_and_apply`
(`application/commands/dispatcher.py`), answering the same 404; attenuation is clamped at read by
`infrastructure/identity/grants.py::resolve_effective_capabilities` under
`middleware/oidc.py::_delegated_identity`, so a hand-edited over-grant in the sealed
`.promptpotter/identity/grants.json` holds nothing extra.

---

## user ↔ user — tenancy

**Cross-tenant isolation is structural, not a check.** `build_stores`
(`infrastructure/store/stores.py`) roots every leaf store at `projects_root / tenant_id`, and the
content-addressed caches at `shared_root / tenant_id`; a CONSTRUCTED `Stores` cannot name another
tenant's directory. `Stores.tenant_id` is a derived property off the identity, never an independent
field. Today `tenant_id == user_id` (one tenant per operator).

**The guarantee is "no constructed `Stores` crosses a tenant", not "no read crosses a tenant"** —
and the difference is the whole of it. A second `build_stores` under a different identity crosses
freely, which is how the two deliberate cross-tenant readers work at all
(`user_store.py::count_accounts`, `quota.py::is_host_tenant_dir`); so does `--tenant <any>` from a
shell, which resolves to `default_identity` carrying `OWNER_COMMAND_CAPABILITIES`. **Through the
served API the isolation holds absolutely** — `deps.py::resolve_identity` builds only from the
session — and the local shell is the separate boundary § loop ↔ everything already concedes.

**Ownership within a tenant is one rule:** `infrastructure/store/stores.py::owned_campaign`
returns the campaign iff it exists *and* is the caller's, else raises `NotFoundError` — a missing
and a cross-owner campaign collapse to the same 404, for every read, command and launch.

**One deliberate exception — not a bug:** `routers/origins.py` is **tenant-scoped, not
owner-scoped** (documented in-code). A CLI-minted campaign is owned by the registered-developer
`user_id`, which differs from a browser OIDC session's `user_id` *within one tenant*, so
owner-filtering would hide the operator's own origins. Tenant isolation still holds.

**All write commands** flow through one `CommandDispatcher` (`application/commands/`) — the
sole inbound writer, stamping `issued_by_user_id` and appending a `CommandRecord` to the ledger.
The read API is otherwise read-only.

---

## loop ↔ everything — OS privilege

This is the genuinely-partial boundary; the honest state:

- **CLI-launched runs** (`python -m promptpotter`) are already a **separate OS process** from the
  API.
- **Web-launched runs** (`/commands/start-run`) run **in-process** in the API worker by deliberate
  design (orphan-reaping assumes the runs it judges live in the process that judges them). So a
  web-launched loop shares the API's process, `.env`, and every provider key.
- **Both hold the same machine slot** (`jobs/launcher/admission.py::admit_and_hold`), so occupancy
  is one number whichever door a run came through. Only the JOBS DIR is shared, not the process, so
  no process may sweep what it did not start: admitting takes a machine-wide OS lock, and a slot is
  released only by proving its producer's own lock gone (`jobs/interlock.py`). That is what lets the
  terminal and the server release each other's dead slots without either being able to stamp the
  other's live campaign stopped.

**Shipped wall (3a) — the hardened service unit** (`deploy-linux/install-service.sh`): the
systemd unit drops all capabilities, `ProtectSystem=strict`, a `@system-service` syscall filter,
the kernel-protection set, and an optional `MemoryMax`. This is kernel-enforced and bounds
**both** the API and the in-process loop it hosts. The writable surface is `DATA_DIR` when set,
else `$INSTALL_DIR` — and only the first takes away the service's ability to rewrite its own
source, its venv and its `.env`, which is persistence rather than disclosure. Unset warns.

**Partly applied (3b) — the dedicated loop principal.** The `.env` **secret split** is available
now (`BOT_ENV_FILE`), the admin bot being its own unit already; which key moves and which stay in
both files is § Running it securely's placement rules. Still absent: a `promptpotter-loop` service
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

## The perimeter (every boundary that goes online)

- **One public port behind Cloudflare Tunnel** (outbound-only; no inbound router port). uvicorn
  binds `127.0.0.1` with `--proxy-headers --forwarded-allow-ips=127.0.0.1`. TermNorm binds
  `127.0.0.1:8000`, never tunneled.
- **AuthN:** Google OIDC — RS256 pinned, signature verified, `iss`/`aud`/`exp`/`iat`/`sub` checked,
  and an `email` returned only where the issuer vouched for it (`identity/verifier.py`:
  `email_verified` required, absent counts as unverified; `identity/github.py`: the verified list
  only, never the profile field). Missing session → 401 at
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
  `change-run-limits` included: it writes the cycle's standing ceiling, whose mirror the run's
  gate prefers over the admitted cap mid-flight, so an unclamped one is the way around this whole section.
  `oidc.py::resolve_access_state` (re-read live; the one derivation, which the browser reads as
  `MeResponse.access_state`) answers
  `blocked` only for an email the operator has revoked; a `blocked` account resolves to an EMPTY
  capability set, so the authorization boundary's dispatcher gate refuses its every command with the same 404 a stranger
  gets. Nothing else re-checks.
- **Who is exempt from free-tier metering is one definition with two readings** —
  `quota.py::_is_host`: the terminal, or the identity that claimed the box.
  `spends_the_hosts_own_key` reads it off a LIVE identity (no issuer); `is_host_tenant_dir` off a
  DIRECTORY walk (the un-renamed `projects/default/`), which has no session to ask. Only the
  terminal DETECTOR differs, and merging the two is what would let an identity that merely omits an
  issuer resolve as the operator — the anonymous-tier trap.
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
  constant-time compared on the TermNorm side) plus TermNorm's IP allowlist
  (`connectors/termnorm.py`, `infrastructure/backend.py`); the deploy provisions the shared secret.
- **Per-user quotas / spend caps** (`application/jobs/quota.py`) + run admission against a resolved
  machine capacity (`application/jobs/capacity.py`), which every entry point shares.

---

## What bounds resource use — and what does not

**There is no load balancer and there is no machine-resource reading.** Replicating the process
would create neither of the two scarce things: the provider's rate limit and the host's wallet are
external and singular. So what a second campaign costs is bounded by admission, not by spreading.

Four gates, each with a different owner, and only the first two adapt on their own:

| Gate | Set by | Adapts? |
|---|---|---|
| Campaigns admitted at once | `Settings.MACHINE_RUN_CAPACITY` | Yes — lowered under provider back-pressure, never raised above the ceiling |
| Share of the provider's 60 s window | nothing — derived per call | Yes — least-served tenant next (`infrastructure/llm/send_pacing.py`) |
| Campaigns ONE person may hold | `user.json::max_concurrent_cycles` — the host, or `set-concurrent-cycles` by an account on its own key | No |
| What an account may ever spend | the free-tier ceilings above, in both units | No |

**The first gate WAITS; the third refuses.** A full machine is temporary and nobody's fault, so a
launch that finds no slot joins a queue and starts by itself (`JobRegistry.request_slot`); an
account at its own concurrency ceiling is refused, because waiting cannot change that fact. The
queue is drained **least-served-first** — the oldest waiting launch belonging to whoever has the
fewest runs going — which is the same rule the provider window uses one row up, so there is one
idea to hold rather than two. It is starvation-free by arithmetic: the third gate bounds how many
entries one account can hold, so a quiet account's launch always overtakes a busy one's.

Waiting is bounded by `Settings.QUEUE_MAX_WAIT_S`, and a launch can be withdrawn before it starts
(`cancel-queued-run`) — a queue with no way out is a trap, and `pause-cycle` cannot serve one, since
a queued mint has no cycle to write a flag into. **Only the principal that launched it may
withdraw it**, whatever capability the caller holds: the job is filed under
`acting_principal_id`, never under the account, because a delegate acts in its delegator's account
and would otherwise own every launch there. A launch refused AFTER its wait — the backend down, the
wallet short, the wait expired — writes its reason onto the job, and `machine-status` serves it
back to that same principal (`refused`).

**The one automatic signal is throttle stall, and it is gathered without configuration.** Every exit
from the rate limiter reports how long that call sat blocked (`report_throttle_stall`) — as does a
cell waiting on a machine slot another run holds (`infrastructure/backend.py::MachineSlots`) — summed
across all tasks into a rolling 60-second total; `resolve_run_capacity` reads it per admission and
stops admitting while the box is oversubscribed. It is deliberately **lagging** — it rises only once
the machine is already too busy — which is why it may only ever LOWER the operator's ceiling. That
one-directional property is what makes an automatic input safe here at all.

**It reads no CPU, no memory, no load average and no disk**, and that is a decision rather than an
omission: `os.getloadavg` is Unix-only, cgroup and `/proc` files are Linux-only, `psutil` would be a
new dependency, and a number that silently reads zero on half the machines it runs on is worse than
no number. A real machine signal, should one land, joins as one more term in the same `min`.

**So nothing in the application stops the box running out of memory.** The guard for that is
kernel-enforced and lives in the service unit — `MemoryMax` in § loop ↔ everything above — plus
keeping `MACHINE_RUN_CAPACITY` sized to the box. A campaign is mostly waiting on a provider, so
concurrency costs far more in *quota* than in RAM; size it against the provider tier first.

---

## Deploy actions — the Linux box checklist

Each is idempotent.

1. **Apply the hardened unit** — `./install-service.sh`, then `systemctl status $APP_NAME` and the health curl. If it fails to start, the first suspects are `ProtectSystem=strict` (add the offending write path to `ReadWritePaths`) and `SystemCallFilter`. `MemoryDenyWriteExecute` is deliberately **omitted** — add it only after a clean smoke test.
2. **(Optional) cgroup bound** — `MEMORY_MAX="2G"` in `deploy.config`, re-run `install-service.sh`. Turns the pp-self memory-starvation OS-kill into a clean cgroup OOM.
3. **Provision the PP↔TermNorm token.** A **first** install has `bootstrap.sh` generate the shared `TERMNORM_TOKEN`; on an already-installed box **it will not re-run** — its `REPO_URL` guard exits first — so set the same token by hand in both `.env` files, with `TERMNORM_REQUIRE_AUTH=true` on TermNorm's side.
4. **Restart BOTH services.** TermNorm to start requiring the token, and PromptPotter because it reads `TERMNORM_TOKEN` once at boot rather than per call. Verify against `127.0.0.1:8000/status`: no `Authorization` → **401**, bearer → **200**. PP's reachability probe uses a separate unauthenticated client that only checks TCP connect, so enabling auth does not break it.
5. **Confirm TermNorm's IP allowlist** (`backend-api/config/users.json`) lists PP's source IP. Co-located loopback is already there; a *remote* PP needs its IP added, or `/matches` removed from `protected_paths` to rely on the bearer token alone.
6. **Never run TermNorm's dev launcher in prod** — `start-server-py-LLMs.sh` binds `0.0.0.0:8000` where the systemd unit binds loopback.
7. **Verify no surprise listener** — `ss -tlnp` shows only loopback `:8000` / `:8001` and cloudflared's outbound; the admin bot adds **no port**.
8. **Firewall is an operator decision, deliberately not scripted.** Everything binds loopback and ingress is outbound-tunnel-only, so a host firewall is defense-in-depth with real SSH-lockout risk on a remote box. Add a default-deny-inbound rule that **preserves SSH** by hand; do not wire it into `bootstrap.sh`.

**Still open (design, not a box step):** OS-privilege **3b** (dedicated loop user + secret split) and **3c** (web-launch out-of-process) — see § loop ↔ everything for the gating.

---

## Running it securely — the one admin task you repeat

Signing up is the grant (§ The perimeter), so there is no queue to work through and the only recurring admin action is the reverse one — **taking access away**.

The free-tier ceiling is spent one **step** at a time (`Settings.FREE_TIER_LAUNCH_STEP_USD`, applied by `quota.py::_launch_step`): the offer is denominated in runs rather than in credit, so a launch declares a step instead of the whole remainder and a first campaign can no longer consume the grant a tenth one was promised. It rations the anonymous grant only — a delegated principal answers to its attenuated ceiling instead, and an account you raised by hand on `user.json` keeps what you gave it.

**The one rule: a control-plane change never has an inbound door open to the internet** — owned by [ADR-0004](../adr/0004-operator-admin-channels.md); here it means the blocklist edit happens **on the box**, which reaches *out* to your phone. If an external tool (n8n, Zapier, CI) genuinely must drive an admin action, gate it behind an edge broker (Cloudflare Access service token) **plus** an app token — never a bare public route (that ADR § "When option C is the right escalation").

### Blocking an account from Telegram

`.promptpotter/identity/blocklist.json` is re-read on every request, so **edits take effect instantly — no restart, no re-login**. An on-box admin bot long-polls Telegram (outbound only — it opens no port).

It is a courtesy control, not a boundary: a blocked person can sign up again from another address into a fresh account with a fresh ceiling. The ceiling is what bounds a stranger; this is what stops one you have already met.

**One-time setup.** Create a bot via [@BotFather](https://t.me/BotFather), read your `message.chat.id` off `getUpdates` to lock the bot to you, put the keys in the env file, then `./install-admin-bot.sh` runs it under systemd like the app and tunnel. Every key is a `Settings` field resolving from the process environment *or* the install's own `.env` (`config/paths.py::env_file_path`).

```bash
ADMIN_BOT_TELEGRAM_TOKEN=123456:AA...           # from BotFather
ADMIN_BOT_CHAT_ID=987654321                      # your numeric chat id
ADMIN_BOT_PASSPHRASE=optional-extra-word         # optional 2nd factor
HOST_ADMIN_EMAIL=you@example.com                 # who may claim this box
HOST_ADMIN_ISSUER=https://accounts.google.com    # ...and via which provider
```

Three placement rules:

- **`ADMIN_BOT_PASSPHRASE` belongs in the BOT's file, not the app's**, once `BOT_ENV_FILE` is split out. It is the second factor on inbound `/block` and `/grant`, and only the bot daemon reads it — a copy in the API's environment turns a read of that process into command authority.
- **Token and chat id must stay in BOTH files.** The API sends on the same bot (`auth.py` on a new sign-in, `main.py` on shutdown), and without them `notify_operator` returns False and logs while the bot keeps answering commands — so nothing reports the loss. `install-admin-bot.sh` warns when a split leaves them out.
- **`HOST_ADMIN_EMAIL` is required on a hosted box** — it names the one sign-in allowed to write the claim marker (§ The perimeter). Unset, the terminal also stays on the `default` tenant while every browser session resolves its own; the app logs a warning saying so.

**Daily use** — message your bot:

| You send | Effect |
|---|---|
| `/block alice@example.com` | Withdraws access. She stays signed in and keeps her account; every command she sends is refused from the next request on. |
| `/unblock alice@example.com` | Gives it back. |
| `/blocked` | Replies with everyone currently blocked. |
| `/grant <sub_user_id> step,create` | Delegates an **attenuated** sub-principal (ADR-0005): the delegate acts in your workspace holding only those capabilities. |
| `/revoke <sub_user_id>` | Removes a delegation (the user reverts to owning only their own empty workspace). |
| `/grants` | Replies with the current delegations. |

With `ADMIN_BOT_PASSPHRASE` set, prefix **every** message with it — it is not a session, so a bare `/spend` is refused like any other: `my-word /block alice@example.com`. A refused message and one that never arrived look identical in Telegram; `journalctl -u <service>-admin-bot` is where they differ (`Ignoring message (N chars, gated=…)`). Messages from any chat id other than yours are ignored the same way. Delegation capabilities are ADR-0005's ladder; a `<sub_user_id>` is the canonical id shown in the delegate's own account modal (`/auth/me`). Every change is recorded to `blocklist_audit.jsonl` or `grants_audit.jsonl` in `.promptpotter/identity/`, an audit trail you can `cat` on the box.

### New accounts into your CRM (optional)

Set `N8N_SIGNUP_WEBHOOK_URL` in the same env file and the app POSTs `{email, name, use_case, signup_source, account_count}` the first time a new account calls `/auth/me`. Unset, nothing is sent; the forward is best-effort and never fails a sign-in (`admin_bot.py::forward_new_account_to_crm`).

**Copy the path from the workflow's webhook node, not from its file name** — the two drift, and a `POST`-only webhook answers *"not registered"* to the browser GET you would naturally test it with, so a wrong path and a live-but-unreachable one look identical. Confirm with the receiving side's own API rather than by probing the URL.

It does not contradict the one rule: the traffic is outbound-only and carries contact details, never a credential, so a breach at n8n reaches your mailing list rather than your auth gate.

### Secret hygiene

- `.env` is `chmod 600` and **never committed** (it holds the bot token + API keys).
- Rotate `ADMIN_BOT_TELEGRAM_TOKEN` (re-issue via @BotFather) if it leaks; paste the new value into every env file carrying it — the app's *and* the bot's, if you split them — then restart both units. The bot's unit is `promptpotter-admin-bot`.
- Don't stack Cloudflare Access in front of the app *and* the OIDC gate — that's a double-gate; pick one. (The bot is independent of either.)
---

## See also

- [ADR-0002](../adr/0002-identity-foundation.md) — identity foundation (OIDC, RLS staging).
- [ADR-0003](../adr/0003-spend-and-tenancy.md) — spend + tenancy.
- [ADR-0004](../adr/0004-operator-admin-channels.md) — the operator-admin channel threat model.
- [`backend-integration.md`](backend-integration.md) § Connection security — the PP↔TermNorm wire.
