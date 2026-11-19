# bkeys-3 report: what a key reads

Base `d6ca65e3d` (`origin/land/w5i`, per the brief; not an ancestor of main). `origin/main` is dated Thu Nov 19 11:12:29 2026 +0100. Arc `arc/bkeys-3` at `3d505568f`. `wip/bkeys-3` holds the history from before the fold, so read the arc.

## Links (`promptpotter/` +added/−deleted)
1. `2c857c5d8` treatment = fingerprint: +12/−15. Re-test: the row records five treatments moving on a rename. After: renaming `_key_phrases` → `_phrases_keyed` in `potter/behavior_checks.py` leaves `treatment:potter` at `94510a0d`.
2. `1019d86b6` Estimate holds facts: +15/−21, plus the browser's `fmtPoint`, `fmtBand` and `fmtEstimate` and 11 consumer files. Re-test: before, `Estimate.model_dump` carried `band`, `point` and `text`. After, it holds `value, ci_lo, ci_hi, n, se, unit, form`.
3. **Not built.** It does not reproduce at the base. A full proof run beside two busy pytests read 20/20 UNMOVED, and gepa+levi beside 8 CPU burners were UNMOVED (4 cores). ts-score is not joined because the world did not change. Suspects are under Symptoms.
4. `3d505568f` the landing key hashes the code a load runs: +44/−14. Re-test: before, the key hashed reader bodies only. After, a new test edits a module a survivor's load runs, and `landing_key()` moves.

LEAN: +71/−50 in all, so net **+21**. Link 4's schema walk is the fix itself. Links 1 and 2 are net deletions.

## Gate and equals-main
- Full gate: 29/30 green. Playwright is red on `responsive.spec.ts` landscape-844 (controls under `div.remote-control`), which is the open row "A control sits under the remote-control bar at landscape-844". The base is red on the same spec (2 failed).
- The full gate ran before the `pass_role` restore. After it, `gate --changed` showed 23 checks green.
- Equals-main, all 20 legs:
  - **requests UNMOVED**.
  - **decisions** moved only by `Estimate`'s `band`, `point` and `text`. Stripped of those, all 20 equal the base.

## Digest moves (base → tip)
- estimator 5df363ab → b4977aa1
- capo d527b2fb → d8609cf9
- capo-pobb ef89a1cf → d4bd26e8
- gepa d463fc8d → 4ab5872b
- levi b2cdc89a → 658fcadc
- potter add44758 → 5fc23089

Link 1 moved the treatments because the source and manifest hashes left them. Link 2 re-keyed `behaviour_keys.json` because the words left the trace:
- capo 3b9694d1 → d1905bea
- capo-pobb 8b19d09a → 453a8047
- gepa 043327a7 → eebbf259
- levi b5c6ae4e → 4f56468b
- potter 6a5c6c3b → b7cf1406
- estimator f252b1e3 → a7741259

## Wipes
- Run data: ledger records and round files change form (W1).
- Every banked L4 cell is voided once.
- Every root lands again once, because its landing key moves.

## Decided in-run
- A manifest outside the package has no fingerprint, so its members' code stands in for it.
- Architecture §0.5 is reworded to match the brief's root.
- Words are made where they print: as Python properties, and in the browser from the generated `UNIT_FORMATS` mirror. No serialization context was added. This touched webapp files that the brief did not list, and no other chain owns them.
- `MemberAddress.pass_role` is **kept**. The first build deleted it, and capo-pobb's `vs_reference.state` moved `read` → `same_individual`, because `read_pair` compares addresses. The fix was folded into link 2.
- The bkeys-2 spelling half was not built. The row is cut to that half.

## Rows closed
- **A rename re-keys every banked L4 cell's treatment**
- **Served words are written to the ledger, and two digests read them: a banked proposal's and the behaviour keys'**
- **A reader's helper body is not keyed**

## Rows to file
- **A round file stores a served word**: `arm_reading.py::ArmReading.vs_reference_name` is a `@computed_field` dumped into each round file's `scoreboard` and `headline` (`results.py::RoundResult`'s computed fields, read back on `extra="ignore"`). Root: the round file holds facts; the round read words them. **Re-test:** a `RoundResult` dump holds no `vs_reference_name`.
- **The landing key over-keys by module**: `landing.py::loading_modules` names 86 modules, so any code edit in one of them re-walks every root at entry (cost: one walk, no wipe). Root: key the functions a load reaches, not their modules. **Re-test:** an edit to an unreached function in `domain/paired_reading.py` leaves `landing_key()` unchanged.

## Symptoms
- `scripts/offline_world.py:602-613`: `unslept` replaces only the backend's `held_wait`. `openai_compat.py:483` still sleeps on the wall clock in the offline world. Suspected root of link 3.
- `infrastructure/store/read_model.py:518-528`: a `derived_file_stale` warning on lock contention (`LOCK_TIMEOUT`) depends on load. Another suspect for link 3.
- `webapp/lib/format.ts:113,129`: these comments cite `arm_reading.py::level_text` and `lift_text`, which live in `paired_reading.py`.
