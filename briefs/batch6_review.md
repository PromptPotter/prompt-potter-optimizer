# Batch 6 review (`0b2cab850..origin/batch/6`, V Z AC w2v)

**Verdict: land after one fix pass.** V, Z and AC are clean. w2v has three introduced findings.

## INTRODUCED

1. **`deploy-linux/update.sh:175`, `install-admin-bot.sh:76`** import the package from the checkout with no `PROMPTPOTTER_HOME`. On main this creates nothing (probe: `offer-extras` and `--help` leave an absent home absent). Now `__init__.py:14` runs `mkdir`.
   - Scenario: on a DATA_DIR box, every update creates or chmods `$INSTALL_DIR/.promptpotter`, a second, empty root. Invariants broken: a read-only verb writes nothing, and DATA_DIR is the only root.
   - Fix: `offer_out="$(as_unit offer-extras)"`. `as_unit` already loads `$ENV_FILE`, so the `read_env_kv` relay goes. Hand the bot probe `PROMPTPOTTER_HOME`.
2. **`promptpotter/__init__.py:14`** raises `DataRootExposedError` at import, outside `campaign_runner.main`'s `PotterError` handler. The CLI prints a traceback instead of `Error: …`, and `--json` gets no envelope.
   - Scenario: `install-admin-bot.sh:76` discards stderr, so an unchmoddable root reads as "ADMIN_BOT_MODULE does not import".
   - Fix: drop its `2>/dev/null`.
   - Placement stays: import is the one door all six processes pass. `complete_registries` misses the seven `needs_registries=False` verbs, and one of them, `close-admission`, writes.
3. **`update.sh:72-81` with `hardening.sh:29`**: a unit file skips an unknown key, but `systemd-run -p` refuses it. `ProtectProc` needs systemd 247.
   - Scenario: on Ubuntu 20.04 (245; a `bootstrap.sh` path) or EL8 (239), the units start, but every `as_unit` exits "Unknown assignment", so nothing lands.
   - Fix: state systemd ≥ 247 in the README and check it in `bootstrap.sh`.
   - The loop is otherwise right: one argv element per line, `~@privileged ~@resources` passed whole, both filter lines merged as in the unit, and `ReadWritePaths` falling back to INSTALL_DIR, as `install-service.sh` does.

Low: `private_root.py:31` tests `!= 0o700`, so it widens a 0500 root, and `:20` calls a refused 0711 root "readable by other users". Use `mode & 0o077`.

## PRE-EXISTING (row proposals)

- **`hardening.sh:32`**: the `~` in `SystemCallFilter=~@privileged ~@resources` inverts the whole line. systemd parses `~@resources` as a syscall name and ignores it, so `@resources` was never denied. Write `~@privileged @resources`. **Re-test:** `journalctl -b | grep -c 'ignoring: ~@resources'`. **Rides with:** Lane A.
- **`pricing.py:71`** binds `CACHE_PATH` to the root at import, while every other reader resolves per call. **Rides with:** M17.

## Stale lines

- `__init__.py:10`, "the root binds at import": `user_data_root()` reads the env on every call, so a home set later (`test_security.py:525`) is never held.
- `deploy-linux/README.md:353`, "`update.sh`'s verbs run under the units' own hardening block": not `offer-extras`.
- `verbs.py:746`: `offer-extras` says "Read-only", but it now creates a missing root.

## Checked clean

- **V:** no forwarder caller is left, and a `None` hop passes no closure.
- **Z:** one rule serves both the setter and `max_concurrent_cycles_writable`.
- **AC:** the old names have no holder, e2e included.
- **w2v:**
  - The error matches the `PotterError` family: a `code` and `details.path`.
  - The test sits in the leaks journey and fails without the guard.
  - The gate and CI set a home before any import.
