# bkeys-4-fix: the fix pass on bkeys-4's review (cloud · W1)

`briefs/common.md` binds you (read BASE FIRST), with ONE override: the base is **`origin/arc/bkeys-4`
at the sha the dispatch names** (bkeys-4's own tip). Add commits on top and push to `arc/bkeys-4`
(fast-forward, never a rewrite); your report goes on `out/bkeys-4-report`, replacing the old one,
with a `## Fix pass` section first. The review is `briefs/bkeys-4-fix.review.md`, its probe scripts `briefs/bkeys-4-fix.probes.md` (adapt their paths). Same W1 window as
`briefs/bkeys-4.md`: treatments, fingerprints and keys MOVING is expected, named old → new, and they
move ONCE in this pass (settle the shape, then regenerate); a DECISION or REQUEST move is a failure.
**The operator's standard: "we always want to go to the full perfect shape at the root"**: every
finding below is BUILT, none filed.

## Fixes (F-numbers are the review's; one commit each, or one for 1–3 if they share the walk)
1. **F1, the fingerprint is where-independent.** `offline_run.py::traced` files ran lines by
   package-relative POSIX path; `ran_lines` / `unrun` match on that; `unrun` raises when a leg's ran
   lines name no package file. Re-test: the review's `pathdep.py` (one home, two tree paths) gives
   equal fingerprints.
2. **F2 + F3 + F4, the treatment keys code by its AST, and the decided boundary.** **Decided (the
   orchestrator, F3):** what the trace cannot attribute is keyed: every statement OUTSIDE a run
   function's body (module- and class-level code: constants, pydantic fields and their
   descriptions, class defaults, docstrings, decorators, a run function's defaults) is keyed by its
   AST, and inside a function body each statement the run did not reach is keyed by its AST with its
   nesting path (`ast.dump`, no positions: formatting and comments never key, continuation lines and
   re-nesting do). A run function's header is not keyed by its name or parameter names (its behaviour
   is the trace), so a rename on run code re-keys nothing. Re-tests: `PLAN_LENGTH_FLOOR_CHARS` 60→600,
   `_TERMINATE_CAPABILITY_TEXT` and `catalogues.py:187`'s prompt text each move `treatment:potter`; the
   `pools/members.py` `ctx`→`context` rename and a body-local rename move nothing; a comment or
   re-wrap of an unrun statement moves nothing. State the offline check's wall clock before/after.
3. **F9 walk root + interpreter.** `shared/import_walk.py`'s `_BASE` is the walking script's tree, not
   the imported package's (a linked worktree without `PYTHONPATH` walks itself); one line in
   `docs/developer/offline-run.md` says the key reads CPython's line table, so a minor interpreter
   bump re-keys every built-in once.
4. **F9 importlib.** The landing key's walk sees what dynamic dispatch loads: either the walk follows
   the one registry the package dispatches by name through, or a gate check refuses an
   `importlib` dispatch the walk cannot see. Decide by the fewest spellings, say why; re-test: after
   `import landing`, `sys.modules`' package modules minus `loading_modules()` holds no module that
   defines a loaded shape (the review counts 20 today, none a shape).
5. **The landing key's entry cost** (bkeys-4's report: process entry 1.67 → 3.05 s). Bring it back to
   the base's cost or below without a second mechanism (a `derived(...)` keyed by what the walk reads
   is the existing channel), or show why it cannot be.
6. **F5.** `BehaviourKeys.packages` goes: `behaviour_key` derives the member map live from
   `select_optimizer(...).member_nodes` and raises the named `LookupError`; a shared package
   (`pools`, `variation`) is keyed by its own source, not by every built-in reaching it.
7. **F6 + F8 + F7.** `Election.leading`'s served text names `RoundView.candidates` (specs regenerated);
   `offline_run.py::_round_file`'s re-synthesized `scoreboard` goes and `_id_map` / `_round` read
   `RoundOutcome.ranked()`; the estimate-words fixture gains `0.25`, `1/32` and their negatives.
8. **F9 MetricSpec form.** The served `MetricSpec` carries the estimate's `form` (level or lift) in
   place of `lift: bool`, and `EvidenceCharts.tsx`'s inlined `(spec.lift ? fmtLift : fmtUnit)` reads
   it through the one formatter. This closes bkeys-4's own row "MetricSpec has `lift: bool` where an
   estimate says `form`" (and the landing key's "over-keys by module" row if fix 2's walk closes it;
   else say why).

## Owns
What bkeys-4 owned, plus `shared/import_walk.py`, `docs/developer/offline-run.md`, the served
`MetricSpec` and its builder, `webapp/components/compare/EvidenceCharts.tsx`. Beside you runs
**addr-4's fix pass** (its files: `jobs/mint.py`, `runner/mint.py`, `layer_fold.py`,
`domain/spend.py`, `bench/difficulty.py`, `archive_queries.py`, `pipeline_schema.py`, potter's
`members.py` / `knobs.py`, `teleprompter.py`, `views/readout.py`, `initialization/session.py`, and it may
add ONE migration registration line to `maintenance/landing.py`): do not edit theirs. Shared and
regenerated at the landing: the generated specs, `behaviour_keys.json`, `complexity_ledger.py`,
`tests/test_security.py`.

## Proof
Equals-main per `common.md` against the base: every leg's decisions and requests UNMOVED; every
treatment, fingerprint, landing key and `SERVED_CONTRACT` move named old → new, each attributed to
its fix. Full gate green. surface-ledger: hunt deletions (fixes 6 and 7 delete); name the ceiling.
