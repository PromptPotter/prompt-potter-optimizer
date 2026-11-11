# The access model — four boundaries

Each trust boundary, its kind and where it is enforced, by symbol. The decisions behind each are [`../decisions.md`](../decisions.md) § 0002 to § 0005; box setup and the admin bot [`../../deploy-linux/README.md`](../../deploy-linux/README.md).

| Boundary | Kind | Enforced by | Failure response |
|---|---|---|---|
| **host-admin ↔ user** | Authorization (host privilege) | the operator-admin channel only (chat-id lock) — no API-side capability | channel: ignored |
| **owner ↔ delegate** | Authorization (capability) | one dispatcher gate (`require_capability` over `CommandKind.capability`) | 404 (existence-hiding) |
| **user ↔ user** | Tenancy (data isolation) | structural directory rooting + one `owned_campaign` rule | 404 |
| **loop ↔ everything** | OS privilege | systemd-hardened unit (kernel-enforced) | process denied (EACCES / cgroup) |

**A dataset read belongs to no boundary.** `promptpotter/assets/datasets/` is install content; private data belongs in the tenant. Never add a `datasets.*` capability: `infrastructure/store/dataset_access.py::resolve_dataset` is a resolver, not a gate.

## host-admin ↔ user — host privilege

**Host-admin acts only through the operator-admin channel** (`presentation/admin_bot.py` — the blocklist, `/grant`, `/revoke`, `/spend`, `/trail`), outbound-only. **No `/commands/{kind}` verb is admin-only**; `HOST_ADMIN_EMAIL` → `default_claim.py::claim_default` answers only which tenant the terminal resolves, and cross-tenant reads (`jobs/install_spend.py::read_install_spend`) are never an inbound route.

`set-sample-lookahead` spends the box's shared key, so it sits at its own rung, `campaign.lookahead` ([`../decisions.md`](../decisions.md) § 0005), withheld without withholding the run. Browser-only — [`../../promptpotter/presentation/CLAUDE.md`](../../promptpotter/presentation/CLAUDE.md) § Sample look-ahead.

## owner ↔ delegate — authorization

One definition, `shared/identity.py::CAMPAIGN_CAP_BY_NAME`, from which the delegable set derives; the owner set adds the account's own provider keys, which no grant carries. An `active` user holds the full owner set, a `blocked` one none; the local operator the full set; a delegate an attenuated subset of its delegator's RESOLVED set, so blocking the delegator blocks it, clamped at read (`infrastructure/identity/grants.py::resolve_effective_capabilities`). Enforced at `require_capability` in the dispatcher; the one-level rule and no account as its own delegate at `grants.py::grant_principal`.

## user ↔ user — tenancy

**Isolation is structural.** `build_stores` (`infrastructure/store/stores.py`) roots every leaf store at `projects_root / tenant_id` and the caches at `Stores.archive_home`; the served API builds only from the session (`deps.py::resolve_identity`). `SHARED_ARCHIVE` is the one deliberate crossing ([`../decisions.md`](../decisions.md) § 0008), the host's alone to compact or purge. Today `tenant_id == user_id`.

**The tenant directory is the owner:** `stores.py::owned_campaign` returns the campaign iff it exists in the caller's tenant, else `NotFoundError`, and no manifest names an owner; the terminal's `--tenant X` acts as user X (`shared/identity.py::default_identity`).

**A block pack is the install's or one account's, and only an account's is written** (`config/paths.py::block_packs_root` read-only; `Stores.block_packs` per account; a name both hold is refused). **All writes** flow through one `CommandDispatcher`, stamping `issued_by` (`shared/identity.py::Principal`) on a `CommandRecord` filed on the account's command log.

## loop ↔ everything — OS privilege

- A run is a separate OS process from the API (`jobs/launcher/run_job.py::spawn_job` hands the identity and held ceiling over stdin), a child of the API's unit — same OS user, `.env`, keys and hardening.
- Terminal and server hold the same machine slot (`jobs/launcher/admission.py::admit_and_hold`), a machine-wide OS lock released only by proving its producer's lock gone (`jobs/interlock.py`).
- The hardened unit (`deploy-linux/install-service.sh`) bounds the API and its runs (`KillMode=process`); a set `DATA_DIR` keeps the service from rewriting its own source, venv and `.env`; the bot's secrets ride `BOT_ENV_FILE`.
- Every tenant-controlled path is inert: the scoring formula AST-allowlisted (`application/scoring/formula/compiler.py`), YAML `safe_load`ed, slugs regex-validated, the provider registry closed.

## The perimeter

- **One public port behind Cloudflare Tunnel**, outbound-only; uvicorn binds `127.0.0.1` with `--proxy-headers --forwarded-allow-ips=127.0.0.1`; TermNorm binds `127.0.0.1:8000`, never tunneled.
- **AuthN:** OIDC, RS256 pinned, `iss`/`aud`/`exp`/`iat`/`sub` checked; an `email` only where the issuer vouched for it (`identity/verifier.py` requires `email_verified`; `identity/github.py` reads the verified list only). Missing session → 401 at `deps.py::resolve_identity`; the cookie is httponly / secure / samesite=lax, opaque.
- **Every router is mounted behind its gate** (`main.py::API_ROUTERS`); public is declared there by `None`: `/health`, the sign-in flow (`auth.py::auth_flow_router`), `/docs` + `/openapi.json` (`main.py::_contract`) and the static webapp.
- **Sign-up is open and signing up is the grant.** A stranger is bounded by the host coupon (`Settings.HOST_COUPON`, or the account's `grant.json`), composed at `quota.py::admit_launch` and at every send's key (`coupon.py::keyed_client`, [`../decisions.md`](../decisions.md) § 0003). `identity/bundle.py::resolve_access_state` answers `blocked` only for a revoked email.
- **Accounts are bounded: `Settings.MAX_ACCOUNTS`**, counted off disk (`user_store.py::count_accounts`) where an account opens (`promptpotter/application/account.py::arrive`), under one OS lock; an existing account always signs in and the host always opens one.
- **The host's first web sign-in RENAMES `.promptpotter/projects/default/` to `.promptpotter/projects/{user_id}/`**, one act under the accounts lock (`identity/default_claim.py::claim_default`); while both directories stand or a run holds a cycle under `default/` it refuses, writes no marker and rings the operator, so the next sign-in asks again.
- **Coupon exemption is one definition with two readings** — `identity/default_claim.py::is_host_tenant_dir`, stamped on a live identity where it is built (`shared/identity.py::IdentityContext.host`, read by `is_host`; a delegate's is false, whoever delegated it) or read on a directory walk. **Who may claim the box is DECLARED** — `account.py::_is_declared_host_admin` reads `Settings.HOST_ADMIN_EMAIL`, and `HOST_ADMIN_ISSUER` where set.
- **CORS answers `Settings.ALLOWED_ORIGINS` alone** (`settings.py::Settings._exact_origins`); response headers are `presentation/api/middleware/headers.py::ResponseHeadersMiddleware`. PP↔TermNorm: a shared bearer token plus TermNorm's IP allowlist ([`backend-integration.md`](backend-integration.md) § Connection security).

## What bounds resource use

The scarce things — the provider's rate limit and the host's wallet — are external, so cost is bounded by admission, not by any machine-resource reading.

| Gate | Set by | Adapts? |
|---|---|---|
| Campaigns admitted at once | `Settings.MACHINE_RUN_CAPACITY` | lowered under provider back-pressure (`report_throttle_stall` → `resolve_run_capacity`), never raised past the ceiling |
| Share of the provider's 60 s window | derived per call (`infrastructure/llm/send_pacing.py`) | least-served tenant next |
| Campaigns ONE person may hold | `user.json::max_concurrent_cycles` (hand-set in `user.json` by whoever runs the box; `set-concurrent-cycles` for the host account with the budget capability) | no |
| What the box's keys may ever pay for an account | the host coupon, both units; its own keys are not metered | no |
| Accounts the box holds | `Settings.MAX_ACCOUNTS` (`accounts_full`) | no |
| Requests one principal may send, a free verb's included | `quota.py::refuse_request_rate` at each request's one entry (`deps.py::open_caller` on a session mount, `host_surface.py::HostSession` for a host): a `command_kinds.py::ReadKind` row draws a read bucket sized above the webapp's re-asks, every other row the write one; the host exempt (429 `rate_limited`) | no |

A throttle is logged, never banked (`quota.py::RateLimitedError`). **The first gate WAITS; the third refuses.** A full machine queues the launch (`JobRegistry.request_slot`), drained least-served-first, bounded by `Settings.QUEUE_MAX_WAIT_S`; `cancel-queued` withdraws a launch, only by the principal that launched it (`shared/identity.py::acting_principal`). `machine-status` names only the caller's slots unless `MACHINE_STATUS_SCOPE=all`. The memory guard is the unit's `MemoryMax`.
