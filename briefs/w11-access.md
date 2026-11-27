# w11-access: area 13, round 2: the access read's rows, fail-open first (cloud · Opus)

`briefs/common.md`, `briefs/w10-shared.md` and `briefs/w11-shared.md` bind you. SPEC: `briefs/map.md`
§ 13 and the debt doc's `## 13` section. Target: OIDC is verified at the API trust boundary and tokens
never pass it; privileged identity changes ride the operator-admin channel; a cross-user campaign is a
404; who an account is (user, API keys, tokens) is a survivor.

Links, each re-tested first:
1. **Grants fail open**, first. Both halves are reads that fail open, and neither waits on the open
   delegation question (decision 25): a corrupt or unreadable grant refuses, never reads as "granted".
2. **One token row that fails validation blocks every bearer on the install**
   (`identity/tokens.py::_tokens`): a bad row is that row's refusal, and the rest still resolve.
3. **An ID token naming several audiences is accepted without its authorized party**
   (`identity/oidc.py::verify_id_token`): require `azp` to be our client wherever `aud` names more than one.
4. **The delegated identity reads the default data root, not the install's**
   (`accounts.py::_delegated_identity` / `_token_identity`).
5. **The box's own terminal opens no account's key** (w10-seal's row).
6. **A new account's notice leaves no trace.** `outbound.py::notify_operator` logs only a skip or a
   failure, so the operator cannot tell whether a sign-up reached him. On 2026-11-26 a stranger signed up
   and he does not know whether he was rung. Record each operator notice's outcome (sent, refused or
   skipped) as a fact the operator can read through the existing admin channel or the host's bulletins,
   never a log line alone. You may edit `notify_operator` in `outbound.py` (w11-connectors owns the rest
   of that file).
7. **RM374:** does `IdentityContext.host` still read a missing issuer as the box operator now that
   `issuer` is gone? Re-test it, and close it or file it.

Census: area 13 lists no Unexamined module after w10-identity. Re-read `grants.py` and `account.py`
against the Target once links 1–4 land, and give each a verdict.

OWNS: `infrastructure/identity/**`, `application/account.py`, `app_visits.py`, `bulletins.py`,
`exhibit.py`, `shared/identity.py`, the user, trail, pack, visit and pointer stores, and
`outbound.py::notify_operator`.

DONE WHEN: each of links 1–5 has a test through the API door that refuses as its row says · a sign-up
in the offline world leaves a readable notice outcome · keys: name any move (none expected).
