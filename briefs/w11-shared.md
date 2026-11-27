# wave 11: what every w11 chain shares (read beside `briefs/common.md`)

`briefs/w10-shared.md` binds you whole (the area as the unit of work, census first, nothing unfiled, the
re-key rule, BASELINE, generated files, hubs, run data, roadmap untouched, deciding in-run, commit
messages), except its BASE and its chain list, which this file replaces.

**BASE:** `origin/release/next`. Report `git log -1 origin/release/next`.

**Eight chains at once**, on disjoint files: `w10-tests` (area 14, already running), `w11-store` (02),
`w11-plugin` (09 + 03's import edges), `w11-scoring` (04), `w11-config` (07), `w11-access` (13),
`w11-connectors` (08), `w11-walk` (read-only). They land as they arrive, so another chain's landing can
move your base: rebase only on a conflict your gate shows.

**Your spec:** `briefs/map.md` § your area (Target, Deviations, Unexamined, Done when; its `L<n>` and
`RM<n>` numbers point at the doc as it stood before re-sectioning, so find a row by its bold title) and the
debt doc's `## NN` section, whose rows are yours. The operator's decisions: `briefs/decisions.md`. Every
item there is approved as its line recommends, except 17, which is dropped for good, and 25 (delegation),
which is still open: build nothing that decides who edits grants.

**`tests/` belongs to w10-tests**, which is rewriting it toward pure functions and world doors and
folding shared runs. You may add a fact for what you change, and edit a fact whose assertion your change
moves. You may not move, merge, split or delete a fact or a fixture, or restructure a module. Write a new
fact in `tests/CLAUDE.md`'s tier: a pure function where the rule is decided, else through an existing
door of `scripts/offline_world.py`, never a hand-seated record or a new shared run.

**Shared files, by name:**
- `infrastructure/store/measurement_archive.py`: w11-store's, except `ReplayFeed`, which is w11-scoring's.
- `config/paths.py`: w11-store's.
- `application/config_map.py`: w11-config's; w11-plugin re-points its import only.
- `application/bench/**`, `application/runner/**`: no owner this wave. w11-plugin re-points imports of
  the plugin contract there, and nothing else.
- The user, trail, pack, visit and pointer stores under `infrastructure/store/`: w11-access's.
- `infrastructure/llm/spend_book.py`, `infrastructure/llm/wallet.py`, `store/read_model.py`'s money fold,
  `jobs/quota.py`: area 05's next chain, so leave them all.

**No lone small commit.** A row you file, a wording fix or a doc line rides the commit of the link it
belongs to, never a commit of its own (operator).
