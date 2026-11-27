# w10-seal: the account-key seal's review fixes (cloud · Opus)

`briefs/common.md` and `briefs/w10-shared.md` bind you. BASE: `origin/release/next` (it holds w10-identity's
`5e01c4a72`: `identity/sealing.py`, `wallet_store.py`, `migrations/sealed_api_keys.py`). An independent
review accepted the stdlib cipher (PRF-CTR + encrypt-then-MAC HMAC; keep it, no `cryptography` in core) and
asked for these fixes, all one root: the seal names what it protects and the key lives apart from it.

1. **HIGH — the key lives with the data.** `box.key` sits at `<data root>/identity/box.key`, so a backup or
   copied data root carries the key and the keys it seals. The key is read from outside the data root: a
   path named by one setting (`settings.py`; on the box a systemd credential, `LoadCredential=` →
   `$CREDENTIALS_DIRECTORY`), defaulting to a per-user config dir off the data root on a desk; a key path
   inside the data root is refused. Drop its SURVIVOR row. The offline world and tests name a tmp key path
   and ask nothing of a fresh clone. The `Box:` line names the operator's step (move the key, set the
   unit's credential) and one runbook line in `deploy-linux/` for backing the key up apart from the data
   root.
2. **MEDIUM — the tag binds the provider only.** A seal copied to the same provider slot in another
   account's `api_keys.json` opens there. Bind a framed (format version, tenant id, provider) and
   `added_at`.
3. **Key validation at every door**: seal and unseal refuse a key that is not 32 bytes; the migration reads
   through the one opener; `seal()` refuses the empty placeholder.
4. **A lost key is never replaced silently**: a key is created only at the landing, or when no sealed file
   exists on the install; otherwise refuse. Each seal carries a short key id (HMAC(key, "seal:id")[:8]) so
   a mismatch reads "sealed under another box key".
5. **The error names the fix**: a `SealError` kind whose remedy reads "restore the box key, or delete
   <path> and re-add the keys"; an account can remove its own key with the box key lost.
6. **Whole-file integrity**: a tag over the whole map, so a dropped or swapped entry refuses; the docstring
   says that deleting the whole file or rolling it back needs state outside it.
7. **Tests**: a flipped byte, a slot swap, an account swap, a wrong-length key, a known-answer vector
   pinning the format.

The migration stays one-shot over the shape v0.8.21 wrote (plaintext); no installed box holds the
interim sealed shape, so reshape it freely. Name in the report: rotate any provider key a pre-landing
backup holds (an operator step).

OWNS: `infrastructure/identity/**`, `infrastructure/llm/wallet_store.py`, the sealing migration,
`config/settings.py` (the one setting), `deploy-linux/` (the runbook line and the unit's credential
line: a box change, so keep that hunk in its own commit; the operator reads it before main), their tests. DONE WHEN: no key file under the data root in any test or the offline world · each
attack in 7 refuses · `python scripts/gate.py` green.
