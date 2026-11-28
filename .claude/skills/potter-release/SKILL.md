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

Prepare on a local release branch named for the version, off `main`, in the desk checkout the operator has open in his
editor (switched to it, its upstream unset so an editor sync can never push `main`), and commit nothing
yet: he reads and edits the CHANGELOG and the body there, on the branch his editor names.

- [ ] Run `python scripts/gate.py --release` first: an advisory it names is fixed by a landing on `main`,
      never on the release branch.
- [ ] Rewrite `## [Unreleased]` whole, the entries landings committed included, as the release's END STATE
      seen in retrospect. Its reader is the operator years from now, orienting: what the release was
      about, what an upgrade broke, a few pointers in. Short: a quote line and one-line entries for
      what a user meets, the point of the release first, never
      each break while no user has upgraded across it nor each landing's paragraph, never internals a
      user never meets: the commits carry the detail, and any reader, an AI one included, derives the
      rest from them. An entry cites its commit.
- [ ] Name every step an upgrade asks of an install: each `Box:` line in the commit bodies since the last
      tag, and each default `git diff <last tag> -- promptpotter/config/settings.py` strips or changes.
      Each is a CHANGELOG line, a Breaking clause, and a step before the box's `update.sh`.
- [ ] Write the release body from that section into a scratchpad file; check every
      capability phrase against what shipped. Its reader is a user reading it cold: each lead says what
      they can now do or what changed for them, in plain words, never a slogan ("a round that measures
      where it matters") or the repo's own framing and vocabulary (loop, race, cut, arm, θ). Lead with
      the one or two changes the operator names as the release's point; the rest is refinement and
      stays short. Outline-shaped, every bullet a complete sentence:
      - a bold one-line intro under 20 words that names that point, then the install line and the hosted
        link as two bullets;
      - `### 🎯 New`: up to three bold leads, each with sub-bullets a user understands; the lead the
        release is about explains how it works and why it matters, before → after;
      - `### ⚠️ Breaking`: one plain bullet for everything an upgrade breaks;
      - `### 📋 Technical Details`: a few direct one-line bullets, each naming only what a user types (a
        flag, an env key), no outer bullet restating what is not new; the last bullet is the commit count
        by type since the last tag;
      - a last line: `Full Changelog: <compare URL>`. No links section.
- [ ] The release title (`gh release create --title`) is `vX.Y.Z/ Two Words, Two Words`, its first half the
      release's point; the operator picks it.
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
      plus `python scripts/gate.py --release`. The full gate runs once per cut: a fix under a gated
      release gates its own delta (`gate.py --changed` before its commit, `--only` each check the
      rebuild's conflicts touch), and CI on the push runs the rest.
- [ ] A rewrite of an unpushed commit changes its hash: re-check every hash `CHANGELOG.md` cites.

## 3. Push the release branch as a draft PR to `main`, wait for CI green on it, and prove the release wheel

`git push origin <commit>:refs/heads/release/vX.Y.Z`: CI runs on PRs to `main` only, and the branch's
history stays rewritable until the box has passed.

- [ ] `gh workflow run publish.yml --ref release/vX.Y.Z`, then watch it green: the dispatch runs the
      release's own build and smoke and uploads nothing. CI's wheel is API-only, so the wheel that
      carries the webapp (its package-data glob, its mount, the bundle's credential scan) is otherwise
      first built after the tag, where a failure moves the tag (v0.8.21's smoke read a mount name the
      mounts had lost).

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
- [ ] A release that wipes run data (a landing's line drops it) warns every account FIRST, before
      `update.sh`, with a bulletin that pops up until each one dismisses it. Its board is a survivor, so
      the warning outlives the wipe. Send `/post-bulletin --pops-up "<text>"` to the admin bot, or on the box
      `deploy-linux/verb.sh post-bulletin "The next deploy, at <HH:MM> UTC, wipes run data: export what
      you keep." --pops-up`. Either way, `deploy-linux/verb.sh bulletins` there reads it back. Give the accounts the time the text names.
- [ ] Deploy the branch as `/potter-box` says (`./update.sh release/vX.Y.Z`). Verify: health on 127.0.0.1
      and on the public host, both units active, `offer_faults()` empty for the unit's `DATASET_OFFER`,
      `update.sh`'s `landed: proven N file(s)` line. The operator tests the UI there.
- [ ] Once the box verifies, post the release note the same way, without `--pops-up`, so it
      waits in the list: `/post-bulletin "vX.Y.Z is live: <the release body's bold intro>"`.
- [ ] Anything that fails here is fixed on the branch; steps 2 to 4 run again, the gate on the fix's
      delta alone and the desk landing only where the fix moves a shape the landing reads.

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
