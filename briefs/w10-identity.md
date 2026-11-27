# w10-identity: area 13 to its Target — key encryption, the account deleter, an access read (cloud · Opus)

`briefs/common.md` and `briefs/w10-shared.md` bind you. SPEC: the debt doc's `## 13 · Identity, accounts,
access` section. Target: OIDC verified at the API trust boundary, tokens never past it; privileged identity
changes ride the operator-admin channel; a cross-user campaign is a 404; who an account is (user, API keys,
tokens) is a survivor.

1. **`api_keys.json` holds account keys in plaintext at rest**: decided, encrypted under a box key. A
   SURVIVOR: ship the one-shot migration under the landing; the box key's source and the operator's step
   to set it go in the commit body as a `Box:` line. The offline world and the desk need a key path that
   asks nothing of a fresh clone.
2. **A bulletin dismissal has no account deleter to ride**: build the account deleter (no code deletes an
   account today) on the operator-admin channel; its one write drops the account's dismissals and every
   survivor row the account owns. Say which rows.
3. **An access read, READ-ONLY for grants**: read `application/account.py` and
   `infrastructure/identity/grants.py` against the Target (who may edit grants and whether a delegate is
   its own tenant are the operator's open questions: do not change grants; file what you find). Also
   re-test the roadmap's anon item: `shared/identity.py::IdentityContext.host` reads a missing issuer as
   the box operator.
4. **Census:** `account.py`, `identity/oidc.py`, `identity/grants.py`, `identity/accounts.py`,
   `application/app_visits.py`, plus area 13's recovered items. The area grew +202 / −24 since v0.8.21
   with three rows.

OWNS: `infrastructure/identity/**`, the user, trail, pack, visit and pointer stores,
`application/account.py`, `app_visits.py`, `bulletins.py`, `exhibit.py`, `shared/identity.py`, and their
tests (`tests/test_identity.py`, the identity parts of `tests/test_security.py`). Not yours: `domain/**`
otherwise, `application/jobs/**`, `application/commands/**`.

DONE WHEN: one `api_keys.json` written by a test holds no key in clear · an account deletion drops its
dismissals in the same write · the access read's findings are rows or "meets Target", each named.

Security findings: describe the class and the fix; never a working exploit in a doc or report.
