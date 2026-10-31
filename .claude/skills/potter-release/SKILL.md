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

- [ ] The notes are staged ahead of the cut as `CHANGELOG.md` `## [Unreleased]` lines. At the cut, write the release body from them into a file outside the repo (the session scratchpad), shaped like the last release's (`gh release view`), for `gh release create --notes-file`.
- [ ] Check every capability phrase in the notes against what shipped (0.8.11 shipped "read-only API/webapp" after the control plane landed).
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
- [ ] `git diff <last tag> -- docs/specs/survivors.generated.json` names every survivor whose shape
      moved: rewrite those files on the box before the deploy, or `update.sh` refuses to land. A
      BREAKING run-data shape is `reset --all-tenants --yes` after the landing; survivors stay.
- [ ] Deploy `main` as `/potter-box` says, which owns the commands, the sudo and the TermNorm restart.
- [ ] Verify on the box: health on 127.0.0.1 and on the public host, both units active,
      `offer_faults()` empty for the unit's `DATASET_OFFER`, `update.sh`'s `held N survivor file(s)` line.
- [ ] Anything that fails here is fixed on `main`, and steps 2 to 4 run again.

## 5. Tag, publish, confirm

- [ ] `git tag -a vX.Y.Z -m "vX.Y.Z" <commit>` → `git push origin vX.Y.Z`.
- [ ] `gh release create vX.Y.Z --title "…" --notes-file <the body file>` fires
      `publish.yml`, which checks the tag against `pyproject.toml` and `CITATION.cff` before uploading.
- [ ] Confirm that `https://pypi.org/pypi/promptpotter/X.Y.Z/json` serves the version.

## 6. Close out

- [ ] Remove the verify worktree (`git worktree remove`; a long path needs `\\?\` on Windows).
