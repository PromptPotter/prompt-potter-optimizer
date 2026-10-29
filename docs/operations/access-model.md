# The access model — four boundaries

> **The one page a security audit opens.** Each trust boundary, its kind, where it is enforced (by
> symbol), and the honestly-deferred gap. The code wins a disagreement. Box setup and the admin bot
> are [`../../deploy-linux/README.md`](../../deploy-linux/README.md)'s.

Four boundaries — **host-admin > owner > delegate > loop** — of **four different kinds**. Keeping
them distinct, and never collapsing the hierarchy, is the whole design.

| Boundary | Kind | Enforced by | Failure response |
|---|---|---|---|
| **host-admin ↔ user** | Authorization (host privilege) | the operator-admin channel only (chat-id lock) — no API-side capability | channel: ignored |
| **owner ↔ delegate** | Authorization (capability) | one dispatcher gate (`require_capability` over `CommandKind.capability`) | 404 (existence-hiding) |
| **user ↔ user** | Tenancy (data isolation) | structural directory rooting + one `owned_campaign` rule | 404 |
| **loop ↔ everything** | OS privilege | systemd-hardened unit (kernel-enforced) | process denied (EACCES / cgroup) |

**A dataset read belongs to no boundary.** `promptpotter/assets/datasets/` is install content,
tracked in git and already on the disk of anyone holding the install, so a capability over it guards
nothing. **Ownership, not permission, is the split:** private data belongs in the tenant. Never add a
`datasets.*` capability; `infrastructure/store/dataset_access.py::resolve_dataset` is a resolver,
not a gate.

---

## host-admin ↔ user — host privilege

The person who **runs the box** is not a user who owns a tenant on it, and the two never collapse:
on the team-online deployment every signup owns its tenant with the full owner set, so what
separates a host admin is a small, explicitly-named set.

**Host-admin acts only through the operator-admin channel** (`presentation/admin_bot.py` — the
blocklist, `/grant`, `/revoke`, `/spend`, `/trail`), outbound-only and never an inbound API route
([decisions.md § 0004](../decisions.md#0004-operator-admin-channels)). **No `/commands/{kind}` verb
is admin-only.** Who is host-admin is the chat-id lock and nothing else; the default-claim marker
(`HOST_ADMIN_EMAIL` → `maybe_claim_default`) answers only which tenant the terminal resolves, never a
capability. Cross-tenant reads (`jobs/install_spend.py::read_install_spend`) are never an inbound
route either.

**`set-sample-lookahead` is the verb that tests it.** It spends the box's shared provider key and
rate bucket, so a holder can throttle every other user — and it still sits at `campaign.lookahead`,
its OWN rung in `CAMPAIGN_CAP_BY_NAME`, so a host can withhold it from a delegate without
withholding the run. It is not `campaign.babysit`: the overshoot sample is discarded, so it cannot
steer a measurement. What bounds the abuse is the spend ceiling plus that carve; how deep a walk
reaches is [`candidate-elimination.md`](../methods/candidate-elimination.md)'s. **It is
browser-only**, and the absence is its gate — owned by
[`presentation/CLAUDE.md`](../../promptpotter/presentation/CLAUDE.md) § Sample look-ahead. Its
address descends into a nested run (`domain/command_kinds.py::CyclePayload.inner_refusal`), because
the arming is not inherited and each layer is armed by naming it.

---

## owner ↔ delegate — authorization

**What a principal may do** is one definition, `shared/identity.py::CAMPAIGN_CAP_BY_NAME`, from
which `OWNER_COMMAND_CAPABILITIES` is derived; it is the ONLY capability set.

**Who holds what:** every authenticated user owns their own tenant. An `active` one holds the full
owner set; a `blocked` one (`shared/identity.py::AccessState`) holds an empty set
(`middleware/oidc.py::_session_capabilities`). The local operator gets the full set from
`default_identity` on the CLI / auth-off path. A **delegate** holds an attenuated subset.

**The capability ladder, delegated sub-principals, the per-grant ceiling and the bounded step verb**
— owned by [decisions.md § 0005](../decisions.md#0005-delegated-principals-and-capability-scoping).
Enforced at: `require_capability` in the dispatcher's `_applier`
(`application/commands/dispatcher.py`, the same 404); the one-level rule at
`grants.py::grant_principal`; attenuation clamped at read by
`infrastructure/identity/grants.py::resolve_effective_capabilities` under
`middleware/oidc.py::_delegated_identity`, so a hand-edited over-grant in `grants.json` holds
nothing extra.

---

## user ↔ user — tenancy

**Cross-tenant isolation is structural, not a check.** `build_stores`
(`infrastructure/store/stores.py`) roots every leaf store at `projects_root / tenant_id` and the
caches at `Stores.archive_home`; a constructed `Stores` cannot name another tenant's directory.
`SHARED_ARCHIVE` is the one deliberate crossing — a cache hit then tells one account what another
ran ([decisions.md § 0002](../decisions.md#0002-identity-foundation)) — and that home is the
host's alone to compact or purge. `Stores.tenant_id` derives from the identity; today
`tenant_id == user_id`.

**The guarantee is "no constructed `Stores` crosses a tenant", not "no read crosses a tenant".** A
second `build_stores` under another identity crosses freely — the two deliberate readers are
`user_store.py::count_accounts` and `read_install_spend` — and so does `--tenant <any>` from a
shell. **Through the served API the isolation holds absolutely** (`deps.py::resolve_identity` builds
only from the session); the shell is the boundary § loop ↔ everything concedes.

**A block pack is the install's or one account's, and only an account's is written.** The install's
(`config/paths.py::block_packs_root`) is read-only to every account, beside which each reads its own
under its tenant (`Stores.block_packs`, a `config/prompt_blocks.py::PackShelf`); a name both hold is
refused, never shadowed. `bank-snippet` writes the account's own pack alone, at the
`campaign.create` rung, so an anonymous caller (401) and a blocked one (404) write nothing, and a
`packs` knob resolves in its run's account, so naming `account` draws the run's own.

**Ownership within a tenant is one rule:** `stores.py::owned_campaign` returns the campaign iff it
exists and is the caller's, else `NotFoundError` — missing and cross-owner collapse to one 404.
**One deliberate exception:** `routers/origins.py` is tenant-scoped, not owner-scoped, because a
CLI-minted campaign's owner differs from a browser session's `user_id` within one tenant.

**All writes** flow through one `CommandDispatcher`, stamping `issued_by`
(`shared/identity.py::Principal`) on a `CommandRecord`; the read API is otherwise read-only.

---

## loop ↔ everything — OS privilege

The genuinely-partial boundary.

- **CLI and web-launched runs are separate OS processes** from the API
  (`jobs/launcher/run_job.py::spawn_job` hands the identity and held ceiling over stdin), but a web
  run is a child of the API's unit: same OS user, `.env`, provider keys and hardening — a separate
  process, not a separate principal.
- **Both hold the same machine slot** (`jobs/launcher/admission.py::admit_and_hold`). Admitting
  takes a machine-wide OS lock and a slot is released only by proving its producer's lock gone
  (`jobs/interlock.py`), so terminal and server reap each other's dead slots but never stop a live one.

**Shipped (3a) — the hardened unit** (`deploy-linux/install-service.sh`): no capabilities,
`ProtectSystem=strict`, a `@system-service` syscall filter, kernel protections, optional
`MemoryMax`; it bounds the API and the runs it spawns (`KillMode=process`). Only a set `DATA_DIR`
takes away the service's ability to rewrite its own source, venv and `.env`.

**Partly applied (3b) — the loop principal.** The `.env` secret split (`BOT_ENV_FILE`) exists. A
`promptpotter-loop` user and cycle-scoped `ReadWritePaths` are absent, gated on 3c.

**Deferred (3c) — a launched run as its own sandboxed principal.** Until it lands, web-launched
loops are bounded by 3a only. **The TRIGGER is the first time a tenant can supply anything
EXECUTABLE** — a custom node, a plugin connector, arbitrary Python. Until then a boundary is built
against a guess, and the guess decides its shape: every tenant-controlled path is audited inert (the
scoring formula AST-allowlisted in `application/scoring/formula/compiler.py`, YAML `safe_load`ed,
slugs regex-validated, the provider registry closed). Waiting is cheap **only while the launch seam
stays single** (`jobs/launcher/launch.py::launch`, plus `application/embedded_run.py`).

---

## The perimeter (every boundary that goes online)

- **One public port behind Cloudflare Tunnel**, outbound-only. uvicorn binds `127.0.0.1` with
  `--proxy-headers --forwarded-allow-ips=127.0.0.1`; TermNorm binds `127.0.0.1:8000`, never tunneled.
- **AuthN:** OIDC, RS256 pinned, `iss`/`aud`/`exp`/`iat`/`sub` checked; an `email` only where the
  issuer vouched for it (`identity/verifier.py` requires `email_verified`; `identity/github.py` reads
  the verified list only). Missing session → 401 at `deps.py::resolve_identity`. The session cookie
  is httponly / secure / samesite=lax, opaque — no JWT past the middleware.
- **Every router is mounted behind its gate** (`main.py::API_ROUTERS`), so a route added under one
  inherits it. **Public is declared there, by `None`:** `/health` and the sign-in flow
  (`auth.py::auth_flow_router`). Outside `/api/v1` only the static webapp and `/docs` +
  `/openapi.json` answer a stranger, the latter the contract the repo already publishes.
- **Sign-up is open AND signing up is the grant.** What bounds a stranger is money, not approval:
  the host coupon, what the box's keys may pay for an account over its life (`Settings.HOST_COUPON`,
  or the account's `grant.json`), composed at `quota.py::admit_launch` and at every send's key
  (`account_key.py::resolve_api_key`, [decisions.md § 0003](../decisions.md#0003-spend-and-tenancy)).
  Past it the account sends on a key of its own (`/auth/api-keys`) and the box pays nothing.
  **Both units, because the USD one can go blind** on an unpriced call
  (`Settings.UNPRICED_GRACE_USD`). A launch is admitted at its campaign's declared ceiling or
  refused, never clamped, and the coupon holds a reservation for it; a send on the box's key past
  that reservation stops the run `host_allowance`. A delegate's grant is the one read-down. **Every path that sets a ceiling composes there**, `change-run-limits` included, or it
  is the way around this section. `oidc.py::resolve_access_state` (served as
  `MeResponse.access_state`) answers `blocked` only for a revoked email.
- **Who is exempt from the coupon is one definition with two readings** —
  `identity/migration.py::_is_host`, read off a live identity (`quota.py::spends_the_hosts_own_key`)
  or a directory walk (`migration.py::is_host_tenant_dir`). Merging the two detectors would let an
  identity that omits an issuer resolve as the operator.
- **Who may claim the box is DECLARED, never inferred** — `auth.py::_is_declared_host_admin` reads
  `Settings.HOST_ADMIN_EMAIL`, and `HOST_ADMIN_ISSUER` where set, since two wired providers could
  each assert an address. Unset, no browser identity claims the box; inferring would hand a fresh
  public box to the first stranger.
- **One route opens to a token instead of a session:** `/api/v1/site-records/{table}`
  (`deps.py::require_site_records_token`, constant-time; unset → 404), touching `site_records/`
  alone, outside every tenant.
- **Blocklist edits never have an inbound door** — the on-box, outbound-only bot
  ([decisions.md § 0004](../decisions.md#0004-operator-admin-channels)).
- **CORS answers `Settings.ALLOWED_ORIGINS` alone**, exact origins; a `*`, path or pattern refuses
  to load (`settings.py::Settings._exact_origins`), since the app serves credentialed requests.
- **Response headers** — one middleware, `presentation/api/middleware/headers.py::ResponseHeadersMiddleware`.
- **PP↔TermNorm** — a shared bearer token plus TermNorm's IP allowlist
  ([`backend-integration.md`](backend-integration.md) § Connection security).

---

## What bounds resource use — and what does not

**There is no load balancer and no machine-resource reading.** The scarce things — the provider's
rate limit and the host's wallet — are external and singular, so cost is bounded by admission.

| Gate | Set by | Adapts? |
|---|---|---|
| Campaigns admitted at once | `Settings.MACHINE_RUN_CAPACITY` | Yes — lowered under provider back-pressure, never raised past the ceiling |
| Share of the provider's 60 s window | derived per call (`infrastructure/llm/send_pacing.py`) | Yes — least-served tenant next |
| Campaigns ONE person may hold | `user.json::max_concurrent_cycles` (host, or `set-concurrent-cycles` on an account's own key) | No |
| What the box's keys may ever pay for an account | the host coupon (`Settings.HOST_COUPON`, or its `grant.json`), both units; its own keys are not metered | No |
| Reads one principal may poll | `quota.py::refuse_read_rate`, a token bucket sized above the webapp's polls, at `deps.py::admit_session` (429 `rate_limited`) | No |

**The first gate WAITS; the third refuses.** A full machine queues the launch
(`JobRegistry.request_slot`), drained least-served-first — the provider window's rule, so one idea —
and starvation-free because the third gate bounds one account's entries. Waiting is bounded by
`Settings.QUEUE_MAX_WAIT_S`, and `cancel-queued-run` withdraws a launch — **only by the principal
that launched it** (`shared/identity.py::acting_principal`), since a delegate acts in its delegator's
account. A refusal after the wait is written on the job and served by `machine-status`.

**Occupancy is everyone's; who holds a slot is not, by default.** `machine-status` counts every slot
but names only the caller's; `MACHINE_STATUS_SCOPE=all` names every slot to every account.

**The one automatic signal is throttle stall** (`report_throttle_stall`, plus
`infrastructure/backend.py::MachineSlots` waits), a rolling 60 s total `resolve_run_capacity` reads
per admission. It is lagging, which is why it may only ever LOWER the ceiling. **It reads no CPU,
memory, load or disk** — a decision: those readings are platform-specific or a new dependency, and a
number that silently reads zero on half the machines is worse than none. **So nothing in the
application stops the box running out of memory**: that guard is `MemoryMax` (§ loop ↔ everything)
and a `MACHINE_RUN_CAPACITY` sized against the provider tier first.
