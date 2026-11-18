# The access model — four boundaries

Each trust boundary, its kind and where it is enforced, by symbol. The decisions behind each are [`../decisions.md`](../decisions.md) § 0002 to § 0005; box setup and the admin bot [`../../deploy-linux/README.md`](../../deploy-linux/README.md).

| Boundary | Kind | Enforced by | Failure response |
|---|---|---|---|
| **host-admin ↔ user** | Authorization (host privilege) | the operator-admin channel only (chat-id lock) — no API-side capability | channel: ignored |
| **owner ↔ delegate** | Authorization (capability) | one dispatcher gate (`require_capability` over `CommandKind.capability`) | 404 (existence-hiding) |
| **user ↔ user** | Tenancy (data isolation) | structural directory rooting + one `CampaignStore.campaign` rule | 404 |
| **loop ↔ everything** | OS privilege | systemd-hardened unit (kernel-enforced) | process denied (EACCES / cgroup) |

**A dataset read belongs to no boundary.** `promptpotter/assets/datasets/` is install content; private data belongs in the tenant. Never add a `datasets.*` capability: `infrastructure/store/dataset_access.py::resolve_dataset` is a resolver, not a gate.

## host-admin ↔ user — host privilege

**Host-admin acts only through the operator-admin channel** (`presentation/admin_bot.py` — the blocklist, `/grant`, `/revoke`, `/spend`, `/trail`), outbound-only. **No `/commands/{kind}` verb is admin-only**; `HOST_ADMIN_EMAIL` → `identity/accounts.py::claim_default` answers only which tenant the terminal resolves, and cross-tenant reads (`jobs/install_spend.py::read_install_spend`) are never an inbound route.

**The host is refused on the bearer door**, `PROMPTPOTTER_AUTH=off` included: a token resolves to its account exactly as a session of it would, through the one builder both doors call (`identity/accounts.py::account_identity`: its grant, its delegated path, its access state, its host), and is then narrowed to its scopes; one whose account is the host's is a 401 at every request, so the host's exemption from every bucket (`jobs/quota.py::refuse_request_rate`) never rides a bearer, and a leaked token spends only its account's coupon. The host acts through its browser or its terminal.

`set-sample-lookahead` spends the box's shared key, so it sits at its own rung, `campaign.lookahead` ([`../decisions.md`](../decisions.md) § 0005), withheld without withholding the run, at every door.

## owner ↔ delegate — authorization

One definition, `shared/identity.py::CAMPAIGN_CAP_BY_NAME`, from which the delegable set derives; the owner set adds the account's own provider keys, its API tokens (`account.tokens`) and its own act (`account.self`: its preferences and its consent to the Terms), which no grant and no token carries. Every command kind names a capability, so the dispatcher's one check covers every act: a delegate cannot accept its delegator's Terms, and a blocked account acts on nothing. An API token is a delegate of its own account's: its capabilities are the account's ∩ its scopes, drawn from the delegable set at its mint (`application/account.py::mint_token`), so no bearer mints, lists or revokes a token or sets a key; scopes narrow commands only, and a token reads what its account reads. An `active` user holds the full owner set, a `blocked` one none; the local operator the full set; a delegate an attenuated subset of its delegator's RESOLVED set, so blocking the delegator blocks it, clamped at read (`infrastructure/identity/grants.py::resolve_effective_capabilities`). Enforced at `require_capability` in the dispatcher; the one-level rule, both ways, and no account as its own delegate at `grants.py::grant_principal`.

## user ↔ user — tenancy

**Isolation is structural.** `build_stores` (`infrastructure/store/stores.py`) roots every leaf store at `projects_root / tenant_id` and the caches at `Stores.archive_home`; the served API builds only from the caller's carrier (`deps.py::resolve_identity`). `SHARED_ARCHIVE` ([`../decisions.md`](../decisions.md) § 0008), the host's alone to compact or purge, and the host's exhibit (§ The perimeter) are the two deliberate crossings. Today `tenant_id == user_id`.

**The tenant directory is the owner:** `campaign_store/store.py::CampaignStore.campaign` returns the campaign iff it exists in the caller's tenant, else `NotFoundError`, and no manifest names an owner; the terminal's `--tenant X` acts as user X (`shared/identity.py::default_identity`).

**A block pack is the install's or one account's, and only an account's is written** (`config/paths.py::block_packs_root` read-only; `Stores.block_packs` per account; a name both hold is refused). **All writes** flow through one `CommandDispatcher`, stamping `issued_by` (`shared/identity.py::Principal`), and the API token a bearer came by (`CommandRecord.token_id`, served by the command read, so a token's acts are attributable to it while its rate stays its account's), on a `CommandRecord` filed on the account's command log, which whoever acts in the account reads whole (`command_reading.py::owned_command`): whoever may reopen a draft follows the Start on it. That widens reads only: a `cancel` acts under the rung of the command it stops (`commands/dispatcher.py::_rung`, at the one check), so a delegate cannot stop a pass or a launch it could not have filed.

## loop ↔ everything — OS privilege

- A run is a separate OS process from the API (`jobs/launcher/run_job.py::spawn_job` hands the identity and held ceiling over stdin), a child of the API's unit — same OS user, `.env`, keys and hardening.
- Terminal and server hold the same machine slot (`jobs/launcher/admission.py::admit_and_hold`), a machine-wide OS lock released only by proving its producer's lock gone (`infrastructure/producer_lock.py::this_producer`).
- The hardened unit (`deploy-linux/install-service.sh`) bounds the API and its runs (`KillMode=process`); a set `DATA_DIR` keeps the service from rewriting its own source, venv and `.env`; the bot's secrets ride `BOT_ENV_FILE`.
- Every tenant-controlled path is inert: the scoring formula AST-allowlisted (`application/scoring/formula/compiler.py`), YAML `safe_load`ed, slugs regex-validated, the provider registry closed.

## The perimeter

- **One public port behind Cloudflare Tunnel**, outbound-only; uvicorn binds `127.0.0.1` with `--proxy-headers --forwarded-allow-ips=127.0.0.1`; TermNorm binds `127.0.0.1:8000`, never tunneled.
- **AuthN:** OIDC, RS256 pinned, `iss`/`aud`/`exp`/`iat`/`sub` checked; an `email` only where the issuer vouched for it (`identity/oidc.py::verify_id_token` requires `email_verified`; `identity/oidc.py::GitHubProviderClient` reads the verified list only). Missing session → 401 at `deps.py::resolve_identity`; the cookie is httponly / secure / samesite=lax, opaque.
- **Two carriers, one door:** a session cookie or `Authorization: Bearer pp_…`, never both on one request, resolve through `identity/accounts.py::identity_of_carrier` into one `IdentityContext` naming its carrier's kind (`carried_by`), while the credential itself rides the request's `Caller` alone, so no launched run's spec, record or served model holds one; a bearer draws its account's rate buckets as a session does. A token is its account's, named, scoped and revocable, kept as its SHA-256 (`identity/tokens.py`, a survivor row), at most `tokens.py::MAX_LIVE_TOKENS` live per account (a mint past them is refused, naming the revoke) and its newest ten revoked rows kept, so an older revoked one reads unknown; the host mints none; a malformed, unknown or revoked one is a 401 naming its next act. **A stream re-reads its carrier every tick** (`application/cycle_reads.py::cycle_event_frames`, `::feed_frames`), so a revoke, a logout or an expiry ends it within one.
- **Every mount is a row behind its gate** (`promptpotter/main.py::API_ROUTERS`), a router or an ASGI app alike; public is declared there by `None`: `/health`, the sign-in flow (`auth.py::auth_flow_router`), `/docs` + `/openapi.json` (`promptpotter/main.py::_contract`) and the static webapp, the last row.
- **The MCP door, `/mcp/`, is the host registry over Streamable HTTP** (`presentation/mcp/server.py::HttpDoor`), the stdio verb's second transport. Its gate is `deps.py::mounted_caller`: the caller a session mount resolves, by cookie or bearer, a host's bearer refused, with no rate draw of its own (each host request draws at `host_surface.py::HostSession`). A session runs as the caller that opened it, on one session manager per authority (account, token, carrier, rungs) that leaves once its sessions end; a later request on it resolving to another caller finds no session (404), one with none is a 401. DNS-rebinding protection admits only the hosts and origins of `Settings.ALLOWED_ORIGINS`, so a box naming none serves the door to no one (421). A claude.ai custom connector reaches it with the account's token as a fixed `Authorization` request header; no OAuth authorization server is built.
- **The host's exhibit is the one campaign read across tenants.** `Settings.EXHIBIT_CAMPAIGN` names a campaign in the host's account; a signed-in, unblocked account with "Try & learn" on (`User.demo_mode_enabled`) reads its hierarchy, lineage, steps, dashboard, rounds and files off the host's stores, decided once by `application/exhibit.py::read_stores` over stores whose identity holds no capability. It acts on nothing: a command keeps the caller's stores, where the exhibit is a 404 like any other account's campaign. The MCP door's sessions stay bound to their caller beside it (below), so no host reaches the exhibit but by these reads.
- **Sign-up is open and signing up is the grant.** A stranger is bounded by the host coupon (`Settings.HOST_COUPON`, or the account's `grant.json`, set by the host-admin `account.py::set_coupon`), composed at `quota.py::admit_launch` and at every send's key (`wallet.py::keyed_client`, [`../decisions.md`](../decisions.md) § 0003). `identity/accounts.py::resolve_access_state` answers `blocked` only for a revoked email.
- **Accounts are bounded: `Settings.MAX_ACCOUNTS`**, counted off disk (`user_store.py::count_accounts`) where an account opens (`promptpotter/application/account.py::arrive`), under one OS lock; an existing account always signs in and the host always opens one.
- **The host's first web sign-in RENAMES `.promptpotter/projects/default/` to `.promptpotter/projects/{user_id}/`**, one act under the accounts lock (`identity/accounts.py::claim_default`) inside the admission gate every destroyer holds (`application/browser_sign_in.py::claim_box`); while both directories stand or a run or pass holds a cycle under `default/` it refuses and rings the operator, so the next sign-in asks again; the renamed record becomes the signer's.
- **The host is the DECLARED account, one definition with two readings** — `identity/accounts.py::is_declared_host` reads `Settings.HOST_ADMIN_EMAIL` against a verified email, whichever provider verified it (each passes only a verified one): stamped on a live identity where it is built (`shared/identity.py::IdentityContext.host`, read by `is_host`; a delegate's is false, whoever delegated it), or read off an account's `user.json` on a directory walk (`is_host_account`, which also holds the terminal's own). The terminal acts as the declared account once it opened one (`host_user_id`).
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
