# w7-spend: the stop record banks spend facts; the read serves `MeteredSpend` (cloud · Opus)

`briefs/common.md` and `briefs/w7-shared.md` bind you, with ONE override to BASE FIRST: your base is
**`origin/land/w7-wipe` at `637a469a3`** (land/w7 7b3e5e293 plus w7-res, which already WIPES run data
and waits on a deploy). This chain's wipe rides the same deploy. It is not on main, so skip the
is-ancestor check and report `git log -1 origin/land/w7-wipe`.

SPEC: `docs/specs/code-debt-cleanup.md` § wave-7, **`MeteredSpend` is banked, so its words cannot be
served**, read whole, plus w7-web's report (`git show origin/out/w7-web-report:report.md`), which cut it.

DECIDED:
1. The stop record (`run_records.py`, `StopSpend.spent`) banks the `SpendRollup` facts, never the
   served `MeteredSpend` shape. Every read serves `MeteredSpend` with its words (headline, metered
   buckets, replay share, rate-priced text) composed in `application/`.
2. The browser composers go: `spend.ts::spendHeadline`, `meteredBucketsLine`, `replayShareLine` and
   `ratePricedText` read the served words. Re-test: `git grep -n "export function" --
   webapp/lib/derivations/spend.ts` names no composer.
3. Run data: a reshape of a banked record wipes. Regenerate `LANDING_KEY`, name the wipe in the report,
   and touch no survivor (the account's charges hold its spend).
4. Parity: the CLI's spend lines read the same served words (CLAUDE.md § entry-point parity).

OWNS: `run_records.py`'s stop record, the spend projections and served shapes (`domain/` spend models,
the reads that serve spend), `webapp/lib/derivations/spend.ts` and its readers, the CLI's spend lines,
and their tests. Not yours: `pipeline_resolve.py`, `initialization/**`, `runner/output.py`,
`run_observers.py` (w7-res2 runs beside you on the same base), `identity/**`, the bot,
`views/readout.py` (w7-rows), `jobs/launcher/**` (w7-open2), `scripts/`.

LEAN: the browser's composers move to the engine, so the webapp shrinks and `promptpotter/` grows only
by the served words. Equals-main: decisions and requests unchanged across every leg. Digests and
behaviour keys: name any move old → new. A spend record is not hashed into a treatment, so none is
expected.
