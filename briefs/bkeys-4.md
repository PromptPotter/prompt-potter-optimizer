# bkeys-4: bkeys-3's fix pass (cloud · W1)

`briefs/common.md` binds you (read BASE FIRST), with ONE override: the base is **`origin/land/w1`
at the sha the dispatch names**. That branch is main plus addr-3 plus bkeys-3 (one combined commit
each), both reviewed: W1, the re-key window, collects there and lands on the operator's word when
all of it is green. Push to `arc/bkeys-4`.

**You are in W1** (operator 2026-11-19): every backbone shape drift lands now, while no user data
exists, batched so run data is wiped and L4 cells are voided ONCE. A treatment, behaviour-key,
landing-key or root-id move is the PURPOSE: name each old → new and the link that moved it. A
DECISION or REQUEST move in equals-main is a failure. **The operator's standard for this pass: "we
always want to go to the full perfect shape at the root"** — no transitional step, no bridge, no
"kept X, filed the root".

An independent review of bkeys-3 is `git show origin/brief/wave-5:briefs/bkeys-4.review.md` (F1–F7;
its probes are not reachable, so reproduce each F in your own probe). Where this brief and the
review differ, this brief decides: the review's "file as a row" items F4, F5, F6 are BUILT here.

## Links, in this order
1. **The browser words an estimate as Python does** (review F1, HIGH). `webapp/lib/format.ts::
   numberIn` rounds ties up (`toFixed`) and prints exact −0 as `+0.0`; `paired_reading.py` rounds
   half-even (rate 1/40: `2%` vs `3%`). **Decided:** Python's formatter is the one spec.
   `scripts/build_ts_types.py` emits a Python-worded fixture (facts → `point` / `band` / `text`)
   covering ties, −0, compact values and every unit × form, the gate re-derives it beside
   `UNIT_FORMATS`, and vitest asserts `fmtEstimate` against all of it; `numberIn` rounds half-even
   and keeps −0's sign. And (review F7): `fmtPoint` goes for `fmtMetric`; `Estimate`'s docstring
   names no browser file (it ships into the generated specs and moves `SERVED_CONTRACT` on prose);
   `format.ts`'s citations name `paired_reading.py`.
2. **The landing key covers what a load runs** (review F2, F3, HIGH). Key the package-internal import
   closure of every loading module (one import walk: if `offline_run.py::imported` is the walk, move
   it down into the package and have both call it; never a second walk). The test asserts that an
   edit to a named module OUTSIDE the walk's own output (`promptpotter.domain.kinds`, via
   `KindTable.parse`) moves `landing_key()`, so a narrower walk turns it red. The open row "The
   landing key over-keys by module" (bkeys-3's report) stays a row: over-keying costs one walk and no
   wipe; a missed key reads a stale landing.
3. **A treatment covers the code its run never reached** (review F4). Today a built-in's treatment is
   one straight offline run's trace, so code that run never reaches (the other value of a knob an L4
   arm sets, e.g. `per_round_resubset: false`; the review/author walks; heal and failure branches)
   no longer re-keys, and a banked cell is reused after that code changed. **Decided:** what the
   world ran is keyed by its behaviour, what it did not run by its source: the treatment hashes the
   fingerprint plus the source of the built-in's own package's function bodies the offline world did
   not run (`offline_run.py::traced` already measures them), regenerated with `behaviour_keys.json`
   and re-derived by the gate. Measure the extra cost of tracing the default run; if it doubles the
   offline check's wall clock, stop and report the number instead of building. **Re-test:** an edit
   to an unrun line in `optimizers/potter/` moves `treatment:potter`; a rename in a run line does
   not.
4. **A plugin keys only what is its own** (review F5): a manifest outside `PACKAGE_ROOT` hashes only
   its non-built-in members' packages plus its manifest directories; a built-in member carries its
   treatment.
5. **A round file stores facts** (review F6, and bkeys-3's own "A round file stores a served word"):
   `RoundResult` no longer dumps the computed `round_id` / `scoreboard` / `headline` (they carry the
   served word `vs_reference_name`) and drops its `extra="ignore"`; the response model serves them.
   Round files change form: W1 wipes run data once, so no reader of two shapes.

## Owns
Everything bkeys-3 owned (`optimizers/nodes.py`, `runner/inner/tasks.py`, `domain/paired_reading.py`,
`domain/results.py`, `domain/arm_reading.py`, `scripts/offline_run.py`, `scripts/offline_world.py`,
`maintenance/landing.py`, `webapp/lib/format.ts` and the estimate consumers it edited) plus
`scripts/build_ts_types.py`, the gate check beside `UNIT_FORMATS` in `scripts/gate.py`, the round's
response model and its route, their tests. Beside you runs **addr-4** (`datasets/authored.py`,
`domain/layer_fold.py`, `domain/campaign.py`, `jobs/mint.py`, `pipeline_resolve.py`, the scoring and
loader files of its identity link, `store/llm_reuse_cache.py`, `bench/llm_call.py`, the dataset and
potter yamls) and the webapp lane **webd-4** (`webapp/` CSS, `RemoteControl`, size literals in
components): do not edit theirs; touch a component only where an estimate is worded. A needed edit
in theirs goes to "Rows to file". `assets/behaviour_keys.json` and the generated specs: regenerate
by their scripts; the orchestrator regenerates once more at the landing.

## Proof
Equals-main per `common.md` against the base: per optimizer, decisions and requests UNMOVED, every
digest and key move named old → new. Full gate green, playwright included. **surface-ledger:** the
base carries provisional raises (`addr-3`, `bkeys-3`) in `scripts/complexity_ledger.py`; hunt
deletions in what you own and leave yours at what remains, its comment naming what the lines buy.
Report on `out/bkeys-4-report` (per link the commit and +added/−deleted; re-tests before/after;
digest moves; wipes; decided in-run; Rows closed; Rows to file; Symptoms).
