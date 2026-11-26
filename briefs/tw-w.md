# tw-w: the offline world is hosted and priced as the real one (cloud, Sonnet)

`briefs/common.md` binds you (read BASE FIRST), with ONE override: the base is `origin/land/tw-xjp` at
`<BASE-W>` = `90986d76e`. That is `land/test-world`'s `af224d3bd` plus tw-x, tw-j and tw-p, integrated. Replace the ancestor check with
`git merge-base --is-ancestor 9cd30b5a7 <BASE-W>`. Equals-main runs against `<BASE-W>`'s offline home.
Push to `arc/tw-w`, one commit per link, with the full gate green at the tip. Two chains, tw-j2 and
tw-c, start on your tip, so stay small: the world, and every workaround it makes dead.

**NOT yours:** chain tw-q runs beside you (`briefs/tw-q.md`) and owns production, `tests/conftest.py`
and `tests/CLAUDE.md` § The world. You edit none of them.

**`tests/CLAUDE.md` binds whole.** tw-p brought the suite's wall and end RSS under the rework's start,
and you keep them there.

## Why
`scripts/offline_world.py` is the world every offline leg and the pytest suite run on
(`tests/conftest.py`, `tests/factories.py` and seven test files import it). It differs from the real
one in two ways (`out/tw-x-report` § Rows to file, rows 1 and 2):
1. **No Groq host.** Every candidate record pins `route_order ('Groq',)`, and the world's OpenRouter
   lists no Groq endpoint, so a steered fork is unpriced under a dollar cap. Tests clear the pin in the
   fork's `pipeline_overlay` or list a "Groq" endpoint by hand. At `af224d3bd` plus tw-x:
   `git grep -c route_order -- tests/` reads test_archive 3, test_connectors 2, test_identity 9,
   test_spend 3, and `Groq` appears in test_archive and test_spend.
2. **Usage costs 0.** Every cell is priced at 0, so spend ratios, capped arms, θ's positive quote and a
   resumed cycle's spent ceiling have no world door. Tests hand-make a cost
   (`completion(..., usage={..."cost": 0.01})`; `git grep -cE '"cost": ?[0-9.]' -- tests/` reads
   test_scoring 2, test_spend 3).

## Links
1. **The world serves the host its LLM names.** Its OpenRouter endpoint listing carries the host every
   record pins, read off the production table that pins it (never a second copy of the name). Each
   test that clears `route_order` or lists a host by hand stops doing so.
2. **Every usage the world answers carries a cost**, priced off the model and the tokens it reports, by
   the rule the production meter reads. A test that needs a specific price states it through the world's
   door for it, never by hand-building a usage. Each hand-made cost goes.

## Proof
- Equals-main against `<BASE-W>`: decisions UNMOVED on every leg, or each moved leg named with the cap
  its new bills crossed. Bills move by design: say so per leg. A re-key of
  `promptpotter/assets/behaviour_keys.json` is expected; regenerate it once and name old → new.
- Before → after (`<BASE-W>`, tip), for the suite and each file you touched: lines, tests, wall, and the
  pytest process's RSS at start and end; `scripts/offline_run.py`'s wall and child max RSS.
- The two greps above read 0 at the tip, or each survivor is named with the fact it carries.
- Full gate green at the tip.

## Owns
`scripts/offline_world.py`, `scripts/offline_run.py`, `tests/world.py`,
`promptpotter/assets/behaviour_keys.json`, and the test hunks the greps above name. In production, nothing: a price or a host the production table
cannot answer is a row.

**Report:** common.md's FINAL REPORT on `out/tw-w-report`.
