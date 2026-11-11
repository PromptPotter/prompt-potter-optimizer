---
name: potter-release
description: >-
  Cutting a PromptPotter release: version bump, release commit, tag, GitHub release, PyPI and the
  box deploy. Use whenever the user says release, cut, ship a version, tag, publish to PyPI, or
  names a version like v0.8.18. Holds the ORDER and the lessons that cost a release; the commands
  themselves live in the files each step names.
---

# Cutting a release

**The order is the protocol. The box comes before the tag.** PyPI cannot be taken back, so a version names
a commit that already runs live.

## 1. Prepare

- [ ] Write the release body from `CHANGELOG.md` `## [Unreleased]` into a scratchpad file; check every
      capability phrase against what shipped. Outline-shaped, every bullet a complete sentence:
      - a bold one-line intro under 20 words, then the install line and the hosted link as two bullets;
      - `### 🎯 New`: up to three bold leads, each with two or three plain sub-bullets a user understands;
      - `### ⚠️ Breaking`: one plain bullet for everything an upgrade breaks;
      - `### 📋 Technical Details`: a bold lead per change, naming its identifiers; the last bullet is
        the commit count by type since the last tag;
      - a last line: `Full Changelog: <compare URL>`. No links section.
- [ ] Bump the version in `pyproject.toml`, `uv.lock` (the version hunk only), `CITATION.cff` and `CHANGELOG.md`.
- [ ] Delete each migration the last release landed on the box: its module and its `MIGRATIONS` row
      (`application/maintenance/migrate.py`).

## 2. Commit and gate the COMMITTED tree

- [ ] Stage by path or by hunk, with context (never `--unidiff-zero`); other sessions share this tree.
- [ ] Gate a worktree at the release commit, never the working tree: `CI=1 python scripts/gate.py` there,
      plus `python scripts/gate.py --release`.
- [ ] A rewrite of an unpushed commit changes its hash: re-check every hash `CHANGELOG.md` cites.

## 3. Push `main`, wait for CI green on that commit.

## 4. Deploy `main` to the box and verify there (`/potter-box`)

- [ ] Back up the data root: `tar -C /var/lib -czf ~/backups/promptpotter-data-pre-vX.Y.Z-<stamp>.tar.gz promptpotter`.
- [ ] `git diff <last tag> -- docs/specs/data-root.generated.json` names every survivor whose shape moved;
      each ships a migration in `MIGRATIONS`, which `update.sh` stages and proves before it writes. Run
      data a BREAKING shape leaves unloadable is the landing's own to drop; its line names each.
- [ ] Deploy as `/potter-box` says. Verify: health on 127.0.0.1 and on the public host, both units active,
      `offer_faults()` empty for the unit's `DATASET_OFFER`, `update.sh`'s `landed: proven N file(s)` line.
- [ ] Anything that fails here is fixed on `main`; steps 2 to 4 run again.

## 5. Tag, publish, confirm

- [ ] `git tag -a vX.Y.Z -m "vX.Y.Z" <commit>` → `git push origin vX.Y.Z`.
- [ ] `gh release create vX.Y.Z --title "…" --notes-file <the body file>` fires `publish.yml`, which checks
      the tag against `pyproject.toml` and `CITATION.cff` before uploading.
- [ ] Confirm `https://pypi.org/pypi/promptpotter/X.Y.Z/json` serves the version.

## 6. Close out

- [ ] Remove the verify worktree: `python scripts/sweep_worktrees.py <name> --apply`.
