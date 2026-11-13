---
name: potter-release
description: >-
  Cutting a PromptPotter release: version bump, release commit, tag, GitHub release, PyPI and the
  box deploy. Use whenever the user says release, cut, ship a version, tag, publish to PyPI, or
  names a version like v0.8.18. Holds the ORDER and the lessons that cost a release; the commands
  themselves live in the files each step names.
---

# Cutting a release

**The order is the protocol. The box comes before `main` and the tag.** Neither `main`'s history nor PyPI
can be taken back, so a version names a commit that already runs live.

## 1. Prepare, uncommitted

Prepare in a worktree off `main` and commit nothing there: the operator reads and edits the CHANGELOG
and the body in it while the last landings finish.

- [ ] Run `python scripts/gate.py --release` first: an advisory it names is fixed by a landing on `main`,
      never on the release branch.
- [ ] Rewrite `## [Unreleased]` whole, the entries landings committed included, as the release's END STATE
      seen in retrospect: what the version is now, sized like the last releases' sections, never each
      break while no user has upgraded across it nor each landing's paragraph; the commits carry the
      detail, and an entry cites its commit.
- [ ] Name every step an upgrade asks of an install: each `Box:` line in the commit bodies since the last
      tag, and each default `git diff <last tag> -- promptpotter/config/settings.py` strips or changes.
      Each is a CHANGELOG line, a Breaking clause, and a step before the box's `update.sh`.
- [ ] Write the release body from that section into a scratchpad file; check every
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

- [ ] Commit only once the last landing before the cut is on `main`, as ONE `chore(release): vX.Y.Z` on
      that tip: the CHANGELOG audit and cut, the version and the docs. No checkpoint commits, and no other
      work rides it: a gate fix or a dependency bump is a landing's. A fix CI or the box asks for is its
      own commit under it, and the release commit is rebuilt on top, never followed.
- [ ] Stage by path or by hunk, with context (never `--unidiff-zero`); other sessions share this tree.
- [ ] Gate a worktree at the release commit, never the working tree: `CI=1 python scripts/gate.py` there,
      plus `python scripts/gate.py --release`.
- [ ] A rewrite of an unpushed commit changes its hash: re-check every hash `CHANGELOG.md` cites.

## 3. Push the release branch as a draft PR to `main`, wait for CI green on it

`git push origin <commit>:refs/heads/release/vX.Y.Z`: CI runs on PRs to `main` only, and the branch's
history stays rewritable until the box has passed.

## 4. Deploy the branch to the box and verify there (`/potter-box`)

- [ ] Back up the data root: `tar -C /var/lib -czf ~/backups/promptpotter-data-pre-vX.Y.Z-<stamp>.tar.gz promptpotter`.
- [ ] `git diff <last tag> -- docs/specs/data-root.generated.json` names every survivor whose shape moved;
      each ships a migration in `MIGRATIONS` over the shape the last release wrote (`migrate.py`), which
      `update.sh` stages and proves before it writes. Run data a BREAKING shape leaves unloadable is the
      landing's own to drop; its line names each.
- [ ] Land a copy of that backup on this desk first: unpack it into the scratchpad, then in the release
      worktree `PROMPTPOTTER_HOME=<copy>/promptpotter PROMPTPOTTER_ENV_FILE=<an empty file> python -m
      promptpotter land-data --dry-run`. Each refusal is a fix on the branch, or a step the operator runs
      before `update.sh` (a store that left the data root moves out). The copy is account data: delete it
      once it proves.
- [ ] Deploy the branch as `/potter-box` says (`./update.sh release/vX.Y.Z`). Verify: health on 127.0.0.1
      and on the public host, both units active, `offer_faults()` empty for the unit's `DATASET_OFFER`,
      `update.sh`'s `landed: proven N file(s)` line. The operator tests the UI there.
- [ ] Anything that fails here is fixed on the branch; steps 2 to 4 run again.

## 5. Fast-forward `main` to the branch's tip on the operator's word, wait for CI green on it

## 6. Tag, publish, confirm

- [ ] `python scripts/gate.py --release` again, right before the tag: `publish.yml` audits the lock after the
      tag is out, and an advisory published since prep refuses the upload (v0.8.20's cost a moved tag).
- [ ] `git tag -a vX.Y.Z -m "vX.Y.Z" <commit>` → `git push origin vX.Y.Z`.
- [ ] `gh release create vX.Y.Z --title "…" --notes-file <the body file>` fires `publish.yml`, which checks
      the tag against `pyproject.toml` and `CITATION.cff` before uploading.
- [ ] Confirm `https://pypi.org/pypi/promptpotter/X.Y.Z/json` serves the version.

## 7. Close out

- [ ] Remove the verify worktree: `python scripts/sweep_worktrees.py <name> --apply`.
