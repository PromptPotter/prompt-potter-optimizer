# readings-r: re-base the readings chain onto today's main

Read `briefs/common.md` first; it binds you, with the changes below.

**Base:** `fda4c2bd6` (main). **Source:** `origin/arc/readings` (`1e91deb7e`), 4 links built on
`f589cbe9a`; its report is `git show origin/out/readings-report:report.md`. Read that report whole:
its links, its proof and its rows are the spec. **You build nothing new.** You re-base its four
commits onto the base, link by link, and prove each one. Your refs: `arc/readings-r`,
`out/readings-r-report` (never push to `arc/readings`).

Since `f589cbe9a`, five landings changed the files readings touches. Read their commit messages
first: `git log --format='%h %s%n%b' f589cbe9a..fda4c2bd6`.
- **lw** (`d4dc53705`) moved webapp modules by what they are.
- **rn-conn** (`917861717`) made "connector" the one word for its concept.
- **rec** (`092418bd6`) cut fields from `live_dashboard.py` (RacingBlock, DashboardFacts, RoundBuffer)
  and folded arm acts into one record.
- **la** (`9727e0b02`) split the CLI: `commands/verify.py` is now in `commands/measure.py`,
  `commands/read.py` is in `commands/reads.py`. It also removed `complete_registries`, since the
  registries now complete at import.
- **lite** (`ef64dad7f`) gave each data-root file its own model.

## Each link
1. `git cherry-pick <link>` onto your branch (the 4 commits in order: `c46c1ce5b`, `efa25779b`,
   `e47fc7921`, `1e91deb7e`). Resolve by hunk and keep both sides' intent: main's cuts stay cut, and
   readings' change lands on what main kept.
   - A whole-arc dry merge conflicts in 10 files: the ledger, `scoring/passes.py`,
     `results_health.py`, `verification.py`, `live_dashboard.py`, `cli/commands/verify.py`
     (modify/delete: its hunks go into `measure.py`), `complexity_ledger.py`, and three webapp files.
     That list is only a map; each link meets its own subset.
2. **A clean merge proves nothing.** Grep every line the link adds (`git show <link>`) for a name
   one of the five landings removed or renamed: the old word rn-conn retired, rec's cut fields,
   `complete_registries`, and webapp import paths lw moved. Re-point each one.
3. **Generated files are regenerated, never merged by hand:** `docs/specs/openapi.generated.json`,
   `docs/specs/events-asyncapi.generated.json` and `webapp/lib/api/types.generated.ts`, each by its
   own script, as the gate names it.
4. **`scripts/complexity_ledger.py`:** don't pick a side. Take main's baseline and re-measure the
   rows the link moves. Each raise is named with its reason in the commit body.
5. **`docs/specs/code-debt-cleanup.md`:** keep main's edits and apply the link's own row edits
   (common.md's ledger rule).
6. Run `python scripts/gate.py --changed`. Keep the commit's subject, and keep its body unless a
   resolution changed what the body says. Report `git diff --shortstat` over `promptpotter/` per link.

## The proof: the per-link moves must match the original report's
Take `--digests` and an equals-main home at the base, then at each link (each link against the one
before it):
- **Links 1–2:** capo, capo-pobb, gepa and levi move readings only (θ, bands). No pick, stop or
  advance moves, and potter's decisions don't move.
- **Link 3:** potter's decisions move as the report names them. In crossed round 2 the election no
  longer crowns C2.7 (+0.624 → +0.097, held). In straight round 2 it loses its held leader.
  Requests 7/9, healed 10/14. Nothing else moves.
- **Link 4:** no decision moves anywhere. L4 doesn't move.
- **Digests:** every one moves, as the report says. Name each one base → tip at this base, since the
  numbers differ from the report's (its base was older).

**Any leg that moves beyond these stops the chain.** Push the green prefix plus `wip/readings-r`, and
report the leg and its first diverging line. Never adjust code to make a leg match.

Then run the full gate once, at the tip.

**Out of scope:**
- `docs/specs/roadmap.md`;
- the paid L4 C0 re-measure;
- any new row work.

Carry the original report's "Rows closed", "Rows to file" and "Symptoms" into yours. Mark each one
still true at your tip, and add what the re-base itself surfaces.
