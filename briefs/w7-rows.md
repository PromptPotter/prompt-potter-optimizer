# w7-rows: six small wave-7 rows, each as its Door states (cloud · Sonnet)

`briefs/common.md` and `briefs/w7-shared.md` bind you, with ONE override to BASE FIRST: your base is
**`origin/land/w7`'s tip** (main `2c7dff34e` plus the wave-7 landings). It is not on main yet, so skip
the is-ancestor check and report `git log -1 origin/land/w7`.

SPEC: `docs/specs/code-debt-cleanup.md` § wave-7 · what the w7 chains filed. Close these rows, one
commit each, each built exactly as its **Door** or **Root** line states (the brief decides nothing
further). Read each row whole first:
1. **`identity/accounts.py::is_host` wraps `identity.host`**: callers read `identity.host`, and the
   roadmap's citation moves with it (`docs/specs/roadmap.md` may change only for that one citation).
2. **The CLI's `bulletins` read hides which pop up**: the read's line names the mark.
3. **`claim_default` orphans the terminal's dismissals**: the claim re-keys the account's dismissals
   in its own write. Add a test: dismiss as `default`, claim, and the host does not see the bulletin again.
4. **The admin bot's account commands bypass the dispatcher**: each bot verb becomes a command kind
   through the dispatcher, as `/bulletin` already is. Re-test: the bot imports no `application.account`
   writer.
5. **The readout folds the closed rounds three times at a stop**: the projection keeps one round fold.
6. **Two served fields no door reads by name**: its `HealthResponse.service` half only. Delete the
   field, and `deploy-linux/deploy.config.example`'s mention of it with it. Leave `warming_up` alone.

OWNS: `identity/accounts.py` and its callers, `bulletin_board.py`, `application/bulletins.py`, the CLI's
bulletins command, the admin bot (`presentation/bot/**` or wherever it lives) and the command kinds it
gains, `views/readout.py`, `main.py`'s `HealthResponse`, `deploy-linux/deploy.config.example`, and the
tests of each. Not yours: `served_dashboard.py`, `live_dashboard.py`, `hierarchy.py`, the stores'
versions and `io.py` (w7-ver2), `scripts/` and `tests/test_security.py` (w7-gate2),
`commands/launching.py` and `jobs/launcher/**` (w7-open2).

LEAN: `promptpotter/` shrinks or holds. Digests, behaviour keys and equals-main are unchanged; if one
moves, stop that link and report it. Run data: none. The bulletin board is a SURVIVOR: a change to its
shape ships a migration (`docs/decisions.md` § 0011). Re-keying dismissals is a write, not a reshape.
