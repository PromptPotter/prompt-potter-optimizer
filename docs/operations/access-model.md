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
| **host-admin ↔ user** | Authorization (host privilege) | the operator-admin channel (ADR-0004, chat-id lock) + `ADMIN_CAPABILITIES` for any API-side power | channel: ignored; API: 404 |
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

**What host-admin can do** is one definition: `ADMIN_CAPABILITIES` (`shared/identity.py`).
*Most* host-admin power still ships through the **operator-admin channel**
(`presentation/admin_bot.py` — sign-in allowlist, `/grant`, `/revoke`, provider config),
which [ADR-0004](../adr/0004-operator-admin-channels.md) fixes as outbound-only and
explicitly **not** an inbound API route. The set holds what that channel cannot express: a
host privilege that is a **command against a running campaign**.

Its one member today is **`scoring.sample_lookahead`** — arming the scoring walk to hold two
samples in flight (`/commands/set-sample-lookahead`). It sits here rather than on a campaign
tier because of *whose* resource it spends: not the campaign's budget but the **box's**
shared provider key and rate bucket, so a tenant holding it would throttle every other
tenant to finish sooner. It is deliberately **not** `campaign.babysit` either — babysit marks
a cycle whose measurement an operator steered, and this verb cannot steer one (the overshoot
sample is discarded precisely so the recorded rows stay identical at either depth).

**It is reachable from the browser only** — no CLI verb, no config key, no dataset knob. That
inverts `<entry-point-parity>` on purpose: the surfaces a capability is *absent* from are part
of its gate, since the CLI is where automation and AI assistants operate. Adding a verb "for
parity" removes the boundary. Noted in root `CLAUDE.md` § Conventions so it is not re-litigated
as an oversight.

**Who is host-admin** is *two deliberate predicates* — **never merge them** (merging
regrants admin to every OIDC signup):
- **Stage 0 (CLI / auth-off):** `shared/identity.py::_admin_caps_from_env` — the
  `PROMPTPOTTER_ADMIN=1` env flag, sound only on the single-operator box.
- **Stage 1 (web / OIDC):** `middleware/oidc.py::_session_capabilities` — the one pinned
  identity in the default-claim marker (the web analogue of ADR-0004's chat-id lock). A
  fresh box with no marker has no admin at all (secure-by-default).

**Dataset reads are NOT part of this tier**, and adding them back is the regression to
watch for. `infrastructure/store/dataset_access.py` is a resolver, not a gate: tenant
content first, then install content, no capability consulted. Tier 2 is what keeps one
user's data from another's; a capability was never what did that work.

---

## Tier 1b — owner ↔ delegate: authorization

**What a principal may do** is one definition: `CAMPAIGN_CAP_BY_TIER` in
`shared/identity.py`, from which `OWNER_COMMAND_CAPABILITIES` is *derived* so the two can
never drift. Adding a power = one line there. Kept separate from `ADMIN_CAPABILITIES`:
owning a tenant is not running the box.

**Who holds what:** every **entitled** authenticated user owns their own tenant and holds the full
owner set (`_identity_context_from_session`, `presentation/api/middleware/oidc.py`); a **pending**
one owns the same tenant and holds nothing. The single local operator gets the full set from
`default_identity` (`shared/identity.py`) on the CLI / auth-off path, where no allowlist stands. A
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
identity zone as the allowlist, which a delegate cannot write (no self-escalation). At the OIDC
seam, `_identity_context_from_session` resolves the grant and rebinds the delegate to act
inside the delegator's tenant with `grant ∩ owner` capabilities — **attenuation is enforced at
read**, so a hand-edited over-grant is clamped, and a malformed/no-delegator grant fails secure
(own tenant, zero caps). The command audit records the delegate itself (`claims["principal"]`),
not the delegator it acts as. Provisioned only through the operator-admin channel
(`admin_bot.py`: `/grant`, `/revoke`, `/grants`); the grant writer rejects a delegator that is
itself a sub-principal (**one-level delegation**, enforced). A grant's **spend ceiling** is
enforced — `effective_spend_cap_usd` folds it into `min(requested, daily_remaining, ceiling)`.
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
systemd unit drops all capabilities, `ProtectSystem=strict` (whole FS read-only except
`$INSTALL_DIR`), a `@system-service` syscall filter, the kernel-protection set, and an optional
`MemoryMax`. This is kernel-enforced and bounds **both** the API and the in-process loop it hosts.

**Not yet applied (3b) — the dedicated loop principal:** a `promptpotter-loop` service user, a
`.env` **secret split** (loop gets provider keys, *not* the admin-bot/OIDC secrets), and
`ReadWritePaths` scoped to the cycle tree only. This is what makes "loop below user" a real
boundary rather than a label — but it only bites CLI-launched runs until 3c, so it is gated on 3c.

**Deferred (3c), named honestly — the web-launch split:** making the web-launched loop a separate
sandboxed process. It fights the current single-process design (a few hundred LOC + delicate
JobRegistry coordination) and guards a low-probability threat (our own optimizer code) in the
current single-operator / small-team model. **Until 3c lands, web-launched loops are bounded by 3a
only, not the full 3b wall.** Do it when untrusted third-party pipeline code executes in-process.

The one place the loop `eval()`s an external string — the scoring formula — is fenced by an AST
allowlist (`application/scoring/formula/compiler.py`); the formula source is operator/tenant config,
not backend-supplied.

---

## The perimeter (every tier that goes online)

- **One public port behind Cloudflare Tunnel** (outbound-only; no inbound router port). uvicorn
  binds `127.0.0.1` with `--proxy-headers --forwarded-allow-ips=127.0.0.1`. TermNorm binds
  `127.0.0.1:8000`, never tunneled.
- **AuthN:** Google OIDC. Missing session → 401 at `resolve_identity` (`deps.py`). Session cookie is
  httponly / secure / samesite=lax, opaque id (no JWT past the middleware — ADR-0002).
- **Sign-up is open; the allowlist is an ENTITLEMENT gate, not a sign-in gate.** Anyone completing
  OIDC gets an account. `oidc.py::resolve_access_state` (re-read live) decides whether it holds any
  capability; a `pending` account resolves to an EMPTY set, so Tier 1b's dispatcher gate refuses its
  every command with the same 404 a stranger gets. Nothing else re-checks. The one thing held behind
  entitlement outside that set is `maybe_claim_default` — the marker it writes is what grants
  `ADMIN_CAPABILITIES`, so an unguarded claim would make the first stranger on an unclaimed box the
  host admin.
- **Admin (allowlist) edits never have an inbound door** — delivered out-of-band by the on-box,
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
| Host-admin capability set (one definition) | `shared/identity.py::ADMIN_CAPABILITIES` (`scoring.sample_lookahead`; the rest ride the ADR-0004 channel) |
| Who-is-host-admin (two predicates, never merged) | `shared/identity.py::_admin_caps_from_env`, `middleware/oidc.py::_session_capabilities` |
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

## See also

- [`secure-hosting.md`](secure-hosting.md) — the operator's repeated task: managing the sign-in
  allowlist via the on-box bot.
- [ADR-0002](../adr/0002-identity-foundation.md) — identity foundation (OIDC, RLS staging).
- [ADR-0003](../adr/0003-spend-and-tenancy.md) — spend + tenancy.
- [ADR-0004](../adr/0004-operator-admin-channels.md) — the operator-admin channel threat model.
- [`backend-integration.md`](backend-integration.md) § Connection security — the PP↔TermNorm wire.
