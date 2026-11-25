# w1-rebase: W1 onto today's main (cloud · main)

`briefs/common.md` binds you. **BASE = `origin/main` at `81b3e6255`.** Push to `arc/w1-rebase`.
Keep W1's commits as they are, folding one into another only where a conflict forces it; never
squash them into one. Your spec is W1 itself, not a ledger section. THE CODE SHRINKS binds as
written: report the number.

## The work
`origin/land/w1` = `ab49e6559` is 7 commits on `3a1226a51`: W1, the keys and address half, with the
full gate green there (`git log --oneline 3a1226a51..ab49e6559`). Main moved beside it:
- `3a8ac1a75` feed-version;
- `bfbd3b009` exhibit-acts;
- `25de96eb3` the walk tap check;
- `81b3e6255` the election's `vote: overlap` and `origin_panel_cells`, banking
  `RoundWinner.paired_on`.

26 files overlap.

1. **Replay W1's commits onto 81b3e6255** (`git cherry-pick 3a1226a51..ab49e6559`). Resolve each
   conflict to both sides' intent: W1's shape is the target, and main's additions ride it.
   - A field main added lives in W1's reshaped home.
   - A test main added is rewired, never dropped.
   - Read the commit messages on both sides of each conflict before resolving it.
2. **Regenerate, don't resolve, the last commit** (`chore(keys): W1's keys, regenerated at the
   combined tip`). `python scripts/offline_run.py` at the tip rewrites
   `assets/behaviour_keys.json`; regenerate every generated spec and type by its own script. The
   in-round measuring loop (`land/measuring-loop`) lands after you and regenerates the keys again;
   take nothing from that branch.
3. **The ledger** (`scripts/complexity_ledger.py`): both sides raised ceilings. Write the tip's
   numbers, keeping both reasons.

## Owns
What W1 and main both touched, and nothing else. No new behaviour: a conflict that needs a decision
resolves to W1's shape and is named under "decided in-run".

## Proof
- **Full gate green at the tip.**
- **equals-main against main is not the bar**: W1 reshapes run data and re-keys. Instead, run
  `offline_run.py --against` with the tip against `ab49e6559`'s offline home. Name every moved
  decision, request and digest, and attribute each to main's commits (81b3e6255's vote,
  feed-version or exhibit-acts) or to the resolution.
- **Report on `out/w1-rebase-report`**: per commit, its conflicts and how each was resolved.
