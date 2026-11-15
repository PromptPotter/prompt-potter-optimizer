# mkey-fix: the landing review's fix pass on mkey (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Read `briefs/mkey.md` too: its
decisions still stand. Chain id `mkey-fix`.

- **Start from `origin/arc/mkey`** (tip `f21a02c88`, base `cf5050f67`). Do not rebase it. Add your fix as
  commits on top, then push `HEAD:refs/heads/arc/mkey-2`. Push wip to `wip/mkey-fix` as common.md says.
  The orchestrator lands `cf5050f67..arc/mkey-2` as one commit.
- **One fix pass.** It fixes the findings below and nothing else. Each one's root is decided here; build it.
  Report +/− per commit; the code may grow only by what a finding needs.
- Verified by the orchestrator at the tip; line numbers are at `f21a02c88`.

## Findings and their roots (build each; do not reopen)

1. **A token builds a second identity path** (`infrastructure/identity/accounts.py::_token_identity`).
   - It never reads the account's grant, so a token minted before its account was `/grant`ed and then
     revoked still acts as an `active` owner. `still_carried` never ends its streams either.
   - It clamps its scopes to the owner set only.
   - **Root:** one builder of who an account is. A token resolves to its account's identity exactly as a
     session of that account would: the grant read, the delegated path, the access state and the host
     check, through one function both doors call. Then it is narrowed to `token.scopes`.
   - `ApiToken.scopes` is validated `⊆ DELEGABLE_CAPABILITIES` and non-blank when the row loads, so a row
     naming `account.keys` / `account.tokens`, or a blank scope, never loads. No KeyError in
     `capability_names`.
2. **A stranger writes the box's trail** (`presentation/api/routers/auth.py::callback`'s `error` branch runs
   before `_flow_nonce`, on the ungated router, with the raw query text as the subject).
   - **Root:** the provider's refusal is filed only after the flow checks out (the state's nonce). Its words
     come from RFC 6749 §4.1.2.1's closed error set; any other value reads as one fixed word. Never echo
     the query.
   - A callback whose flow does not check out gets the existing redirect and files nothing.
   - mkey's test ("`?error=access_denied` files one refusal") now runs with a live flow.
3. **A live credential rides `IdentityContext`** (`shared/identity.py::Carrier` on `IdentityContext.carrier`).
   - `JobSpec.identity` dumps it to every launched run's stdin (`run_job.py`), and a failed validation
     prints it to the job's log.
   - **Root:** the identity keeps only the door's name. The `Carrier` (the session id or the bearer's
     plaintext) lives on `Caller`, beside what re-checks it, and the stream re-check reads it there.
   - Nothing crossing a process boundary or reaching a record holds a credential: grep `JobSpec`, every
     record and every served model.
4. **`capability=None` kinds act for anyone holding the door** (`set-user-settings`, `accept-terms`). A
   single-scope bearer, a blocked account or a delegate accepts the Terms for its person.
   - **Root, as the filed row says:** the account's own act is one owner-only capability, never delegable
     and never a token scope. It joins `OWNER_COMMAND_CAPABILITIES`, and both kinds carry it, so the
     dispatcher's one check covers them.
   - The row's re-test becomes 0. Delete the row and say in `access-model.md` what follows: a delegate
     cannot accept its delegator's Terms, and a blocked account acts on nothing.
5. **No cap on tokens** (`tokens.py`): an account mints without bound into an install-wide file every
   bearer request parses.
   - **Root:** at most 10 live tokens per account. A mint past that is refused, typed, with `next_acts`
     naming the revoke.
   - An account keeps at most its 10 newest revoked rows. Older ones are dropped by the next write; a
     dropped token reads `token_unknown`.
6. **A bearer's acts read as the owner's.** `acting_principal` turns a missing principal into the bare
   account id, so `issued_by` cannot say which token filed a command.
   - **Root:** a command filed through a bearer records the token's id on its filing. The account's command
     read serves it.
   - The rate key stays the account's. `access-model.md` says a token's acts are attributable to it.
7. **The follow rule widened a write** (`commands/cancelling.py` rides `owned_command`): a delegate holding
   `campaign.run` cancels the owner's in-flight passes.
   - **Root:** a cancel acts under the cancelled command's own rung. The caller must hold the capability the
     cancelled kind requires, at the one check. The follow rule then widens reads only, as `mkey.md` meant.
   - `access-model.md` says so.

**Nits, same pass:**
- The host's mint is refused at the mint (typed `host_bearer_refused`), never minted into a token every
  request refuses.
- `deps.py`'s "sign-in required" refusal gets its `code`. Its `next_acts` name both carriers by door: a
  browser signs in, a program sends a bearer.
- `_BEARER` is spelled once: `accounts.py` and `oidc.py` each define one, with different meanings. One goes
  or is renamed for what it holds.

## Proof (beyond common.md)

- Equals-main UNMOVED on every leg. Digests unmoved. A survivor (`tokens.json`, the user list) is never
  wiped; a shape change to `tokens.json` ships its one-shot migration under the landing (decisions § 0011).
- Tests, by `tests/CLAUDE.md`, one function per fact:
  - A token of a revoked sub-principal acts on nothing, and its open stream ends within a tick.
  - A row naming a non-delegable scope refuses to load.
  - An `?error=` callback without a live flow files nothing; with one, it files one refusal in fixed words.
  - No launched job's spec holds a credential.
  - A bearer cannot accept the Terms or set preferences.
  - The 11th live mint is refused.
  - A delegate cannot cancel a pass it could not have started.
- `playwright` green at the tip.

Report as common.md says: each finding's commit, its +/−, and its test.
