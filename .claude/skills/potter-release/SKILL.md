---
name: potter-release
description: >-
  Cutting a PromptPotter release: version bump, release commit, tag, GitHub release, PyPI and the
  box deploy. Use whenever the user says release, cut, ship a version, tag, publish to PyPI, or
  names a version like v0.8.18. Holds the ORDER and the lessons that cost a release; the commands
  themselves live in the files each step names.
---

# Cutting a release

**The order is the protocol. The box comes before the tag.** PyPI cannot be taken back, so a
version names a commit that already runs live, and whatever the box finds is fixed before the
version exists.

## 1. Prepare

- [ ] Draft the notes in `.scratch/release-vX.Y.Z.md`, which `gh release create --notes-file` reads.
- [ ] Bump the version in `pyproject.toml`, `uv.lock` (the version hunk only), `CITATION.cff`, the `README.md` badge and the `CHANGELOG.md` entry.
- [ ] Count the commits since the last tag by type, and quote the same count everywhere it appears.

## 2. Commit and gate the COMMITTED tree

- [ ] Stage by path or by hunk; other sessions share this tree. Cut hunk patches **with context**:
      a zero-context `git apply --cached --unidiff-zero` places a hunk by line number alone and can
      land a block inside the wrong statement without any error.
- [ ] Gate a worktree at the release commit, never the working tree, which holds other sessions' edits:
      `CI=1 python scripts/gate.py` there, plus `python scripts/gate.py --release`.
- [ ] A rewrite of an unpushed commit changes its hash, so re-check every hash `CHANGELOG.md` cites.

## 3. Push `main` and wait for CI

- [ ] `git push origin main`, then wait for CI on that commit to go green.

## 4. Deploy `main` to the box and verify there (`/potter-box`)

- [ ] Back up the data root first: `tar -C /var/lib -czf ~/backups/promptpotter-data-pre-vX.Y.Z-<stamp>.tar.gz promptpotter`.
- [ ] A BREAKING record shape: load a copy of the box data under the release code first (accounts,
      sessions, campaigns, `restamp --apply`). Only the API keys and the user list must survive; move
      whatever will not load out of the data root, and never delete it. Delete the local copy afterwards,
      because it holds live session credentials.
- [ ] Deploy `main` as `/potter-box` says, which owns the commands, the sudo and the TermNorm restart.
- [ ] Verify on the box: health on 127.0.0.1 and on the public host, both units active,
      `offer_faults()` empty for the unit's `DATASET_OFFER`, every `user.json` loads.
- [ ] Anything that fails here is fixed on `main`, and steps 2 to 4 run again.

## 5. Tag, publish, confirm

- [ ] `git tag -a vX.Y.Z -m "vX.Y.Z" <commit>` → `git push origin vX.Y.Z`.
- [ ] `gh release create vX.Y.Z --title "…" --notes-file .scratch/release-vX.Y.Z-body.md` fires
      `publish.yml`, which checks the tag against `pyproject.toml` and `CITATION.cff` before uploading.
- [ ] Confirm that `https://pypi.org/pypi/promptpotter/X.Y.Z/json` serves the version.

## 6. Close out

- [ ] Remove the verify worktree (`git worktree remove`; a long path needs `\\?\` on Windows).
- [ ] Mark the `.scratch/` notes as cut, with the final hashes.
