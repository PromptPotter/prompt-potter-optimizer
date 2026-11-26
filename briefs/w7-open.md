# w7-open: one `Opening` per run, entered once and released by the object (cloud · Opus)

`briefs/common.md` and `briefs/w7-shared.md` bind you (read BASE FIRST). Base **`2c7dff34e`**.
Its prerequisites have landed: l4-key-b, spend-tomb, admit-3 (2fdb69b3d).

SPEC: `docs/specs/code-debt-cleanup.md` § wave-6 · arch review 2, **One `Opening` per run: admit,
claim, mint or open, observers, book, entered once and released by the object (root A)**, with every
row it names under **Closes** and the two rows that ride it: **`runner/mint.py` opens a run and mints
nothing** and **`jobs/quota.py` holds counts and rates, and the money shares sit beside the spend
book**. Read root A whole, then each named row.

DECIDED (the root's own words, built as written):
1. A request kind states only its data. `FreshMint | ExistingCycle | CheckinStart` share their eight
   repeated members through one declared shape; embedded, inner and fork are request kinds, not side
   doors.
2. One `Opening` spells admit, claim, mint or open, observers and book once; it is entered once and
   released by the object, so no start site hand-rolls a release (`release(exc` 0 in `application/`).
3. The run boundary leaves the observer builder: `RunPhaseRecord(Running)`, the inbox void, the active
   pointer and the dead bench's close move into the opening.
4. `runner/mint.py`'s four (`open_launch`, `open_cycle`, `close_init`, `onto_fork`) go into the opening;
   the file goes.
5. `quota.py`'s shares and holds (`share_campaign_ceiling`, `cycle_share`, `move_campaign_money`,
   `change_budget`, `hold_run`, `release_run`) move to the spend book's side; `quota` keeps counts and
   rates.

OWNS: `promptpotter/application/jobs/launcher/`, `promptpotter/application/jobs/{quota,registry,mint}.py`,
`promptpotter/application/runner/{mint,entry}.py`, `promptpotter/application/run_observers.py`,
`promptpotter/application/embedded_run.py`, `promptpotter/application/runner/inner/spawn.py`,
`promptpotter/application/commands/launching.py`, `promptpotter/application/initialization/session.py`,
`promptpotter/application/cycle_addressing.py`, the spend book's module (the moved shares only),
`tests/factories.py`, `tests/test_identity.py`, `tests/test_spend.py`. Import lines only in any other
file a move re-points (name each); `application/served_dashboard.py` is w7-line's beyond its imports.

LINKS (5 to 6): the request shape; the `Opening` and its release; the boundary out of the observers;
`runner/mint.py` folded; the quota split; the embedded and inner doors as request kinds.

LEAN: the opening is a consolidation, so `promptpotter/` shrinks. Re-tests: `git grep -c "release(exc" --
promptpotter/application` 0; `git ls-files promptpotter/application/runner/mint.py` empty;
`git grep -c "^def " -- promptpotter/application/jobs/quota.py` falls by the six. Run data: none, so
equals-main must show nothing moved.
