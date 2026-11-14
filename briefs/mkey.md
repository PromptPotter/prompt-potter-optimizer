# mkey: an account's API tokens, one caller door for cookie and bearer, the access review (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `mkey`, arc `arc/mkey`.

- **Base:** `origin/main` at `cf5050f67`, main after faults-1 and ren have landed (81). Every refusal you add is born with its `next_acts`, by faults-1's rule. Your `<base sha>` in common.md is this commit.
- **Spec:** the ledger section `### batch/29 · mkey`, every row. Re-run each row's Re-test at your base first.
- **The code may grow here.** mkey adds a door, and that door is the substance. Every added line still answers common.md's question: it either serves the door or it goes. Hunt deletions on the way: the router's own announcement, the provider-error log, and a second identity path. Report +/− per link.
- **Beside you:** **model-lines** (cloud, same base) makes the bench's optimizer model one fact in the
  bench defaults and deletes the 22 `model:` / `provider:` lines in `assets/optimizers/*/pipeline.yaml`;
  it touches `pipeline_resolve.py`'s fold, `config_map.py`'s bench pin and those yamls, never `api/`,
  `account.py` or access. A file you both need is named in your commit body, and you touch only your
  hunk. Both of you cut rows in `code-debt-cleanup.md`: delete only your own.

## Decisions (orchestrator; build them, do not reopen them)

- **A token is an account's, named, scoped and revocable.**
  - It is minted by its own account and shown once as `pp_` + 32 random bytes, url-safe.
  - Only its SHA-256 is kept, with its id, name, scopes and `created_at`, plus `revoked_at` once revoked. No last-used write per request.
  - It lives in the account's identity files as a SURVIVOR row of `store/data_root.py::KINDS`, because who an account is survives (root `CLAUDE.md`).
  - Its scopes are named by `shared/identity.py::capabilities_from_names` and drawn from `DELEGABLE_CAPABILITIES` only. `account.keys` never rides a token. A mint asking for a capability its account lacks is refused, typed.
  - Capabilities are explicit sets: the owner holds `OWNER_COMMAND_CAPABILITIES`, and a blocked account holds the empty set. A bearer's capabilities are its account's ∩ the token's scopes, so an empty set acts on nothing, never on everything.
  - **Reads:** a token reads what its account reads. Reads are gated by tenancy (`Stores` by identity), and scopes narrow commands only. `access-model.md` says so.
  - **No token mints a token.** The three token kinds carry a new owner-only capability, `account.tokens`. It joins `OWNER_COMMAND_CAPABILITIES`, is never delegable and never a token scope, so a bearer cannot mint, list or revoke. That is the dispatcher's one capability check (`dispatcher.py:388`), with no special case by door.
- **Mint, list and revoke are commands on the existing command channel**, so every door has them: three kinds on `domain/command_kinds.py`, read by `application/`.
  - The webapp's account tab mints (it shows the plaintext once), lists and revokes.
  - No CLI verb: the CLI is the host's own machine, and the host is refused on the bearer door. `handler-tables` checks only that each kind's handler resolves.
- **One caller door.** `Authorization: Bearer pp_…` resolves where the cookie does, into one `IdentityContext` through `deps.py::resolve_identity`.
  - That context is the account's, with its capabilities narrowed to the token's scopes and a door field saying which carrier it came on.
  - The bearer draws the same rate buckets (`_drawn`), and `open_caller` is unchanged above it.
  - An unknown, revoked or malformed token is a 401 Refusal naming its next act. A request carrying both a cookie and a bearer is refused.
- **The host is refused on the bearer door**, under `PROMPTPOTTER_AUTH=off` too. Its exemption (`jobs/quota.py::refuse_request_rate`) never rides a bearer.
- **A stream re-checks its caller every tick.** `cycle_reads.py::cycle_event_frames` and `::feed_frames` re-resolve the carrier, a session or a token, and end the stream on a mismatch: a revoke, a logout or an expiry. One wrapper serves both.
- **The account's acts are the application's.**
  - The new account's announcement is an `Arrival` field that `application/account.py` acts on; `routers/auth.py::_announce_new_account` goes.
  - The provider's sign-in error and the exchange failure file one refusal through one function in `application/`. The router gains no `record_refusal` of its own.
- **One rule for who may follow a Start.** Whoever may reopen a draft may follow the Start on it: the command read lets an account in on a command filed on a subject it owns (`command_reading.py::owned_command`), and `draft_build.py::HeldStart` serves that same reading.
- **The access review is a doc change in the same chain.**
  - `docs/operations/access-model.md` gains the bearer door in § The perimeter and § host-admin.
  - `docs/developer/stable-api.md` gains a section showing mint → submit → poll → fetch with a token.
  - Name the two reported sub-threshold gaps (one-level delegation enforced one way only; `capability=None` kinds acting for blocked accounts and delegates). File each as a row; fix one only if its fix is one edit.

## Links (one commit each, in order)

1. **Tokens:** the record and its SURVIVOR row, the three command kinds, the webapp tab; the `Arrival` announcement and the one sign-in refusal.
2. **The bearer door:** `resolve_identity` reads the bearer, the host refused, the stream's per-tick re-check, and `stable-api.md`.
3. **The access review:** the Start's follow rule, `access-model.md`, and the gaps filed.

## Proof (beyond common.md)

- Equals-main UNMOVED on every leg: no run behaviour moves. A landing-key move is named with its wipe; a survivor is migrated, never wiped.
- Tests (in `tests/`, by `tests/CLAUDE.md`):
  - a minted token reads and acts within its scopes and is refused outside them;
  - a revoked token is refused at the next request, and its open stream ends within one tick;
  - a host-account token is refused;
  - a bearer with every scope cannot mint, list or revoke a token, nor set the account's keys;
  - cookie + bearer together is refused;
  - a callback with `?error=access_denied` files one refusal on the box's trail.
- `playwright` green at the tip, with a shot of the account tab's mint (the plaintext shown once) and its list.
- Each row's re-test, before and after.
