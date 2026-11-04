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

**Host-admin acts only through the operator-admin channel** (`presentation/admin_bot.py` — the blocklist, `/grant`, `/revoke`, `/spend`, `/trail`), outbound-only. **No `/commands/{kind}` verb is admin-only**; `HOST_ADMIN_EMAIL` → `maybe_claim_default` answers only which tenant the terminal resolves, and cross-tenant reads (`jobs/install_spend.py::read_install_spend`) are never an inbound route.

`set-sample-lookahead` spends the box's shared key, so it sits at `campaign.lookahead`, its own rung in `CAMPAIGN_CAP_BY_NAME`, withheld without withholding the run; the overshoot sample steers no measurement. Browser-only — [`../../promptpotter/presentation/CLAUDE.md`](../../promptpotter/presentation/CLAUDE.md) § Sample look-ahead; a nested address arms each layer by naming it (`domain/command_kinds.py::CyclePayload.inner_refusal`).

## owner ↔ delegate — authorization

One definition, `shared/identity.py::CAMPAIGN_CAP_BY_NAME`, from which `OWNER_COMMAND_CAPABILITIES` derives. An `active` user holds the full owner set, a `blocked` one (`shared/identity.py::AccessState`) none (`middleware/oidc.py::_session_capabilities`); the local operator the full set from `default_identity`; a delegate an attenuated subset, clamped at read by `infrastructure/identity/grants.py::resolve_effective_capabilities` under `middleware/oidc.py::_delegated_identity`. Enforced at `require_capability` in the dispatcher's `_applier` (`application/commands/dispatcher.py`); the one-level rule at `grants.py::grant_principal`.

## user ↔ user — tenancy

**Isolation is structural.** `build_stores` (`infrastructure/store/stores.py`) roots every leaf store at `projects_root / tenant_id` and the caches at `Stores.archive_home`; the served API builds only from the session (`deps.py::resolve_identity`). `SHARED_ARCHIVE` is the one deliberate crossing ([`../decisions.md`](../decisions.md) § 0008), the host's alone to compact or purge. Today `tenant_id == user_id`.

**Ownership within a tenant is one rule:** `stores.py::owned_campaign` returns the campaign iff it exists and is the caller's, else `NotFoundError`; `routers/origins.py` alone is tenant-scoped (a CLI-minted campaign's owner differs from a browser session's `user_id`).

**A block pack is the install's or one account's, and only an account's is written** (`config/paths.py::block_packs_root` read-only; `Stores.block_packs` per account; a name both hold is refused). **All writes** flow through one `CommandDispatcher`, stamping `issued_by` (`shared/identity.py::Principal`) on a `CommandRecord`.

## loop ↔ everything — OS privilege

- A run is a separate OS process from the API (`jobs/launcher/run_job.py::spawn_job` hands the identity and held ceiling over stdin), a child of the API's unit — same OS user, `.env`, keys and hardening.
- Terminal and server hold the same machine slot (`jobs/launcher/admission.py::admit_and_hold`), a machine-wide OS lock released only by proving its producer's lock gone (`jobs/interlock.py`).
- The hardened unit (`deploy-linux/install-service.sh`) bounds the API and its runs (`KillMode=process`); a set `DATA_DIR` keeps the service from rewriting its own source, venv and `.env`; the bot's secrets ride `BOT_ENV_FILE`.
- Every tenant-controlled path is inert: the scoring formula AST-allowlisted (`application/scoring/formula/compiler.py`), YAML `safe_load`ed, slugs regex-validated, the provider registry closed. The launch seam: `jobs/launcher/launch.py::launch`, `jobs/launcher/head_to_head.py::admit_head_to_head`, `application/embedded_run.py`.

## The perimeter

- **One public port behind Cloudflare Tunnel**, outbound-only; uvicorn binds `127.0.0.1` with `--proxy-headers --forwarded-allow-ips=127.0.0.1`; TermNorm binds `127.0.0.1:8000`, never tunneled.
- **AuthN:** OIDC, RS256 pinned, `iss`/`aud`/`exp`/`iat`/`sub` checked; an `email` only where the issuer vouched for it (`identity/verifier.py` requires `email_verified`; `identity/github.py` reads the verified list only). Missing session → 401 at `deps.py::resolve_identity`; the cookie is httponly / secure / samesite=lax, opaque.
- **Every router is mounted behind its gate** (`main.py::API_ROUTERS`); public is declared there by `None`: `/health`, the sign-in flow (`auth.py::auth_flow_router`), `/docs` + `/openapi.json` (`main.py::_contract`) and the static webapp.
- **Sign-up is open and signing up is the grant.** A stranger is bounded by the host coupon (`Settings.HOST_COUPON`, or the account's `grant.json`), composed at `quota.py::admit_launch` and at every send's key (`coupon.py::keyed_client`, [`../decisions.md`](../decisions.md) § 0003). `oidc.py::resolve_access_state` answers `blocked` only for a revoked email.
- **Accounts are bounded: `Settings.MAX_ACCOUNTS`**, counted off disk (`user_store.py::count_accounts`) where an account opens (`promptpotter/application/account.py::arrive`), under one OS lock; an existing account always signs in and the host always opens one.
- **The first web sign-in RENAMES `.promptpotter/projects/default/` to `.promptpotter/projects/{user_id}/`** (`identity/default_claim.py`).
- **Coupon exemption is one definition with two readings** — `identity/default_claim.py::_is_host`, off a live identity (`is_host`) or a directory walk (`is_host_tenant_dir`). **Who may claim the box is DECLARED** — `auth.py::_is_declared_host_admin` reads `Settings.HOST_ADMIN_EMAIL`, and `HOST_ADMIN_ISSUER` where set.
- **One route opens to a token instead of a session:** `/api/v1/site-records/{table}` (`deps.py::require_site_records_token`, constant-time; unset → 404), outside every tenant.
- **CORS answers `Settings.ALLOWED_ORIGINS` alone** (`settings.py::Settings._exact_origins`); response headers are `presentation/api/middleware/headers.py::ResponseHeadersMiddleware`. PP↔TermNorm: a shared bearer token plus TermNorm's IP allowlist ([`backend-integration.md`](backend-integration.md) § Connection security).

## What bounds resource use

The scarce things — the provider's rate limit and the host's wallet — are external, so cost is bounded by admission, not by any machine-resource reading.

| Gate | Set by | Adapts? |
|---|---|---|
| Campaigns admitted at once | `Settings.MACHINE_RUN_CAPACITY` | lowered under provider back-pressure (`report_throttle_stall` → `resolve_run_capacity`), never raised past the ceiling |
| Share of the provider's 60 s window | derived per call (`infrastructure/llm/send_pacing.py`) | least-served tenant next |
| Campaigns ONE person may hold | `user.json::max_concurrent_cycles` (host, or `set-concurrent-cycles` on an account's own key) | no |
| What the box's keys may ever pay for an account | the host coupon, both units; its own keys are not metered | no |
| Accounts the box holds | `Settings.MAX_ACCOUNTS` (`accounts_full`) | no |
| Requests one principal may send, a free verb's included | `quota.py::refuse_request_rate` at each request's one entry (`deps.py::open_caller` on a session mount, `host_surface.py::HostSession` for a host): a `command_kinds.py::ReadKind` row draws a read bucket sized above the webapp's polls, every other row the write one; the host exempt (429 `rate_limited`) | no |

A throttle is logged, never banked (`quota.py::RateLimitedError`). **The first gate WAITS; the third refuses.** A full machine queues the launch (`JobRegistry.request_slot`), drained least-served-first, bounded by `Settings.QUEUE_MAX_WAIT_S`; `cancel-queued-run` withdraws a launch, only by the principal that launched it (`shared/identity.py::acting_principal`). `machine-status` names only the caller's slots unless `MACHINE_STATUS_SCOPE=all`. The memory guard is the unit's `MemoryMax`.
