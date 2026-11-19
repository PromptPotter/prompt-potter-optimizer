# addr-4-fix: the fix pass on addr-4's review (cloud · W1)

`briefs/common.md` binds you (read BASE FIRST), with ONE override: the base is **`origin/arc/addr-4`
at the sha the dispatch names** (addr-4's own tip). Add commits on top and push to `arc/addr-4`
(fast-forward, never a rewrite); your report goes on `out/addr-4-report`, replacing the old one,
with a `## Fix pass` section first. The review is `briefs/addr-4-fix.review.md`, and its probe
scripts are in `briefs/addr-4-fix.probes.md` (adapt their paths; they ran on `git archive` copies).
Same W1 window as addr-4's brief (`briefs/addr-4.md`): keys and root ids MOVING is expected, named
old → new; a DECISION move is a failure. **The operator's standard: "we always want to go to the
full perfect shape at the root"**: every finding below is BUILT, none filed.

## Fixes, one commit each (R-numbers are the review's)
1. **R1, a moved cell identity refuses a resume.** The cycle files the WHOLE identity it drew under
   (the cell identity: judge / harbor / n8n fingerprints and the L4 inner estimator), and
   `jobs/mint.py::resolve_resume`, `::_refuse_off_record` and `runner/mint.py::OriginAlreadyRun` refuse
   a moved one naming the leaf. Identity stays OUT of the origin hash. Widen the same record to close
   row "A moved capability or backend revision alone passes a resume" (§ its section; it reads
   `_revision_moved`) if it falls out of one record; if it needs TermNorm's xconn, leave it and say
   why. Fix `runner/mint.py`'s "Config-AWARE" docstring. Test: the review's
   `test_probe_identity_drift.py` refuses at your tip.
2. **R2, null means CLEAR on every layer, by one rule in the fold.** **Decided:** `budget` is in
   scope. One `Unbounded` spelling lives in `domain/` (not potter's `knobs.py`); `Budget`'s lift, the
   three potter knobs and every knob whose null meant unbounded spell it. Then `layer_fold.py::_laid`
   reads `None` as `CLEAR` (SUBSET included), and `wire_patch`'s null branch and
   `ConfigLayers.over`'s budget carve-out go. Every in-tree yaml and test that spells a null for
   "unbounded" moves to the word. If a SURVIVOR kind (`store/data_root.py`) can hold a null whose
   meaning this moves (an authored dataset's `budget` axis), ship a one-shot migration under
   `maintenance/migrations/` (§ 0011); `maintenance/landing.py` is bkeys-4's, so its registration
   line is the one shared hunk: name it. Re-tests: the review's `p1_null.py`, `p4_subset.py`,
   `p7_untyped.py` all lay CLEAR; the round-trip test gains raw-null `temperature`, `max_tokens`,
   `reasoning_effort` leaves and passes.
3. **R4, the archive is addressed by what it files.** **Decided:** the archive's address is
   `config_key`; `prompt_fields_id` is the arm's id (what it sends) and never indexes archive
   entries. `bench/difficulty.py`'s index, `archive_queries.py::bench_reads`' count and the ruler
   row's address read `config_key`; fix `ArchiveConfig.individual`'s "the archive address" docstring.
   Re-test: `p2_collide.py` keeps 2 of 2.
4. **R5, `unbounded` at every entry point.** `pipeline_schema.py::_knob_row` reads every `anyOf` arm
   (an integer with the word as an option); `OptimizerLimit.value` carries the `Unbounded` word, not
   `None`, so `dashboard_rows.py` / `LimitReconcile.tsx` show it; the CLI's `--set` takes the bare
   word as the door does. Re-tests: `p9_knobrow.py`, `p3_cli.py`.
5. **R6, the purpose is named once.** `resolve_cycle_plan` takes the config and the purpose and calls
   `run_spec` itself (a resume passes its declaration's spec part); `RunDeclaration` holds a `spec`
   instead of inheriting `RunSpec`, and `RunDeclaration.of`'s field filter goes.
6. **R7 + R8 + R9, small.** One test pins effort `"default"` and route `[]` keying as none, and
   `"default"` is normalized once in `ChatRequest.__post_init__` beside the route (the store's import
   of `openai_compat::sent_effort` goes). `teleprompter.py:126`'s `"fitness"` literal and
   `readout.py`'s fallback go (both read `defaults.yaml`); `ScorerSetup.display_metric`, no reader,
   goes. `PipelineParams.filed` is renamed for what it lays (`keyed_by` or better; grep collisions),
   and the two-judges test asserts the arm id unmoved.

**Not built, decided by the orchestrator:** R3 (a SURVIVOR install shadow of potter's manifest
holding a null): no install holds one (desk and box checked 2026-11-19), so a shadow refuses loudly at
the landing; R10 is information.

## Owns
What addr-4 owned, plus `runner/mint.py`, `jobs/mint.py`'s resume and bench-set guards, the
`PartitionRecord` / cycle record of the drawn identity, `domain/spend.py::Budget`,
`application/bench/difficulty.py`, `infrastructure/store/archive_queries.py`,
`domain/pipeline_schema.py::_knob_row`, `potter/members.py`'s limit row, `presentation/teleprompter.py`,
`views/readout.py`, `initialization/session.py`, and their tests. Beside you, **bkeys-4's fix pass** may
run (its files: `maintenance/landing.py`, `domain/l4/proxies.py`, the response models of
`RoundResult`, `webapp/lib/format.ts`): do not edit theirs beyond the one named hunk.

## Proof
Equals-main per `common.md` against the base: every leg's decisions UNMOVED; requests unmoved or
moved only by an id/nonce a fix names; every digest, key and id move named old → new. Full gate
green. surface-ledger: hunt deletions (fixes 2, 5 and 6 delete), and say what the addr-3 raise
(+39) is after this pass.
