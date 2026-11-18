# land-order: the landing stamp is ordered and its key reads bodies (cloud)

`briefs/common.md` binds you. Base: `origin/main` (3784ed33b or later). Spec:
`docs/specs/code-debt-cleanup.md` § `### batch/32 · rel`, two rows, one link each, in this order.
Run each row's Re-test at the base first; a row already closed is named, not built.

## Links
1. **The landing stamp is unordered**: the stamp carries the package version that landed (operator,
   2026-11-11: the package version is the ordering signal), and an entry from an older version refuses
   with words instead of dropping what newer code wrote. Re-test: an entry from an older version on a
   root a newer one landed refuses.
2. **`landing_key` misses a body change**: a reader's or a migration's body change moves the key.
   Re-test: edit a migration's body; `landing_key()` moves.

The row **A landing waits on every live producer on another commit…** is NOT yours (its
`deploy-linux/update.sh` hunk is a box change); leave it whole.

## Survivors
Read `store/data_root.py`'s SURVIVOR rows first. If the stamp's file is a survivor, the reshape ships a
one-shot migration under the landing (`docs/decisions.md` § 0011); otherwise nothing migrates.

## Owns
`application/maintenance/landing.py`, `infrastructure/store/data_root.py`, `config/settings.py` only
for the version read, their tests.

## Target
Net ≤ 0 `promptpotter/` lines.

## Report
Per `common.md`, plus the landing key at the base and the tip.
