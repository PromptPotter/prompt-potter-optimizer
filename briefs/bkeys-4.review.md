# Review: bkeys-3 (d6ca65e3d..3d505568f), read-only

Verdict: **land after fixes**. Fix F1, F2 and F3 on the branch. File F4, F5 and F6 as rows.

Not re-run here: the full gate, equals-main, `offline_run.py --digests`. Probes ran against a `git archive` of the tip in the scratchpad, using the repo venv with `PYTHONPATH` pinned to it. No paid call was made.

promptpotter/ line counts, per link:
- L1: +12/−15
- L2: +15/−21
- L4: +44/−14
- Chain total: +71/−50, net +21, all from link 4's schema walk.

## F1 (high, confidence high): the browser's estimate words differ from Python's on exact ties (link 2)
- **Where:** `webapp/lib/format.ts:107-111` (`numberIn`), which `fmtPoint`/`fmtBand`/`fmtEstimate` (`format.ts:136-157`) now use on every estimate surface.
- **Defect:** `numberIn` rounds with `Number.prototype.toFixed`, which breaks an exact binary tie upward (away from zero). `paired_reading.py::_number` (`:403`) uses Python's format spec, which rounds half to even. Exact `-0` also differs: JS `(-0).toFixed(1)` gives `"0.0"` and `-0 >= 0` is true, so the browser prints `+0.0`. Python prints `-0.0`.
- **Concrete failure:** the same served estimate reads differently in the browser and on the CLI/readout/export:
  - rate level 1/40: Python `2%`, browser `3%`
  - rate level 5/40 or 1/8: Python `12%`, browser `13%`
  - rate lift 0.0125: Python `+1.2 pts`, browser `+1.3 pts`
  - tokens level 1250: Python `1.2k tok`, browser `1.3k tok`
  - exact −0 rate lift: Python `-0.0 pts`, browser `+0.0 pts`

  On a 40-cell panel, every k ≡ 1 (mod 4) hits this. Before the chain the browser printed Python's `text`/`band`/`point`, so the two agreed.
- **Why nothing catches it:** the mirror is single-source for DATA only. `build_ts_types.py` generates `UNIT_FORMATS` and the gate re-derives it (`gate.py:1624`). The ALGORITHM (scale, round, sign, compact step, band layout) is hand-copied into TS, and `format.test.ts:56-64` checks three hand-written cases with no tie, no −0 and no compact value.
- **Falsifying check:**
  - `python -c "print(format(100*(1/40),'.0f'), format(100*0.0125,'+.1f'))"` prints `2 +1.2`.
  - `node -e "console.log((100*(1/40)).toFixed(0),(100*0.0125).toFixed(1))"` prints `3 1.3`.
  - At the tip, `Estimate(value=0.125, ci_lo=None, ci_hi=None, n=None, se=None, unit="rate", form="level").text` gives `'12%'`.
- **Fix:** have `build_ts_types.py` emit a Python-worded fixture covering ties, −0 and compact values (facts → `point`/`band`/`text`) for vitest to assert `fmtEstimate` against, and make `numberIn` round half-even so it passes.

## F2 (high, confidence high): the landing key still misses a helper in another module, which is the row's own example (link 4)
- **Where:** `promptpotter/application/maintenance/landing.py:506-537` (`_ran`, `_loads`, `loading_modules`).
- **Defect:** the key covers the module of each callable that appears in a core schema, plus each reader's own module. It does not cover code those callables call in other modules. `domain/kinds.py::KindTable.parse` is the one parse behind every ledger line (`read_model.py:324 ledger_line` → `RECORDS.parse`) and every banked state part (`optimizer_state.py:94` → `STATE_KINDS.parse`). It is not in `loading_modules()`; neither are `domain/record_kinds.py` and `shared/hashing.py`.
- **Concrete failure:** an edit to `KindTable.parse` changes what loads but does not move the key, so a root landed before the edit is read as landed. I changed it to `model_validate_json(raw, strict=False)` in the scratch tip copy and `landing_key()` stayed `9375a807577db6f5`. The row's text ("a helper a reader calls (the record validators behind `ledger_line`) … can change what loads without moving the key") still holds, yet link 4's commit deletes the row as closed.
- **Falsifying check:** `"promptpotter.domain.kinds" in landing.loading_modules()` returns False.
- **Fix:** key the package-internal import closure of every loading module, using the import walk `offline_run.py::imported` already has, instead of only the modules of schema callables.
- **Cleared:** base hashed `STATE_KINDS.schemas()` and the tip drops it. A probe of every state kind's Literal/Annotated aliases by defining module found none outside `loading_modules()`, so no regression is demonstrated there today.

## F3 (medium, confidence high): the new landing test checks only the module set it builds itself (link 4)
- **Where:** `tests/test_security.py:2948-2966`.
- **Defect:** it takes its helper from `_ran`'s own output intersected with `loading_modules()`. That proves the hash reads the modules in the set, not that the set holds what a load runs. It cannot fail for any module the walk misses.
- **Concrete failure:** with F2's edit to `kinds.py`, the test still passes.
- **Falsifying check:** run the test with the `kinds.py` edit from F2. It is green.
- **Fix:** assert on a named module in another file that a survivor's or ledger's load calls (e.g. `promptpotter.domain.kinds`, via `KindTable.parse`), so a narrower walk turns the test red.

## F4 (medium, design, confidence high): a built-in's treatment no longer moves for code off the straight offline run (link 1). File as a row; not a send-back
- **Where:** `promptpotter/application/optimizers/nodes.py:386-402`.
- **Defect:** the treatment is now `stable_hash({fingerprint, world})`. The fingerprint is `stable_hash([decisions, requests])` of ONE straight run under the default flags (`offline_run.py:1803`).
  - Prompts and manifest values stay covered by `Treatment.prompt_hashes` and `Treatment.manifest`.
  - Not covered any more: Python that the straight run never reaches. That includes the other value of a knob an L4 arm sets (`potter/pipeline.yaml:47` sets `per_round_resubset: true`, so the `false` path is off-run), the `review`/`author` walks (`nodes.py:298-304`), and heal or failure branches.
- **Concrete failure:** two potters that behave differently under an arm edit share one treatment. A banked L4 cell for that arm is reused after its code changed. At base the package-source hash caught this.
- **Falsifying check:** edit a line in `optimizers/potter/` that `coverage_by_package` reports as not run, then run `offline_run.py --digests`. `treatment:potter` does not move.
- **Fix (as a row):** fingerprint each knob's non-default value as well, or keep the source hash of the function-body lines the offline world never ran (`traced` already measures them).

## F5 (low, confidence medium): plugins keep a second identity, which still hashes built-in source (link 1)
- **Where:** `nodes.py:393-396`.
- **Defect:** a manifest outside `PACKAGE_ROOT` (an authored switch over built-in members) still hashes `_files_hash` of its members' packages. Those can be `optimizers/potter/` itself, so a rename there still re-keys that manifest's cells. The branch also dropped `_files_hash(manifests)`: a data file beside a plugin manifest that neither `prompt_hashes` nor `manifest` reads no longer keys anything.
- **Concrete failure:** only if such a manifest exists. Then the row's bug survives for it.
- **Falsifying check:** for a manifest whose directory is outside `PACKAGE_ROOT` and whose members include a potter node, rename a potter symbol and compare `behaviour_key`.
- **Fix:** the fallback hashes only non-built-in members' packages plus the manifest directories, and built-in members carry their fingerprint.

## F6 (low-medium, confidence medium): the same root is left one model over in Owns (link 2)
- **Where:** `promptpotter/domain/results.py:261-316` and `arm_reading.py:410`.
- **Defect:** `RoundResult` (the round file itself) still dumps the computed `round_id`, `scoreboard` and `headline`, and reads them back on `extra="ignore"`. `headline` and `scoreboard[].reading` are `ArmReading`, which carries the SERVED word `vs_reference_name`.
- **Concrete failure:** a stored form still holds a served word, which is the shape link 2 removed from `Estimate`. `round_document_digest` excludes it (`include=RoundOutcome.model_fields`), so no key moves.
- **Falsifying check:** `"vs_reference_name" in rr.model_dump(mode="json")["headline"]` for any closed round.
- **Fix:** serve `scoreboard`/`headline` on the response model, not on the stored `RoundResult`, and drop its `extra="ignore"`.

## F7 (low): lines that need not exist (link 2)
- `fmtPoint` (`format.ts:138-142`) is `fmtMetric({unit: e.unit, lift: e.form === "lift"}, e.value)`, and its overload pairs add four lines.
- `Estimate`'s docstring (`paired_reading.py:447-450`) names a browser file. That text ships in `openapi.generated.json`, `events-asyncapi.generated.json` and `types.generated.ts`, so `SERVED_CONTRACT` moves on prose.
- **Fix:** delete `fmtPoint` in favour of `fmtMetric`, and keep the browser pointer out of the domain docstring.

## One-liners
- **Link 3 is absent.** "Two wounded legs move under load" (`code-debt-cleanup.md:603`) and "The capo offline bench turns on one seed" (`:637`) are still open at the tip. Confirm the report names the skip.
- **Stale citations, from before the chain:** `format.ts:106,114,130` cite `arm_reading.py::_number/level_text/lift_text`, which live in `paired_reading.py:403-437`. Link 2 edited the file beside them.
- **Owns:**
  - No edit to addr-3's files, store-5a's, or webapp CSS/`RemoteControl`.
  - Overlap with addr-3 is only `behaviour_keys.json` (the orchestrator regenerates it) and the ledger.
  - The 11 webapp component edits fall outside the brief's listed Owns. No file overlaps `arc/webapp-1`.
- **Cleared:**
  - No SURVIVOR/CACHE kind holds `Estimate` (a `pinned_schemas()` match was only "Estimated"), and `FrozenModel` is `extra="forbid"`. Only run data reshapes, and the wipe is named.
  - Every Python surface (CLI `measure.py`, readout `output.py`, `export.py`, `ansi.py`, `phase.py`, injections `panels.py`, `transcript.py`, `hierarchy.py`) reads the properties, so its strings are unchanged.
  - No dict read of `text`/`band`/`point` remains.
  - Every TS consumer is typed.
  - No reading is dumped into a prompt, so requests should stay unmoved.
  - `pass_role` is read (`paired.py:181`, address equality), so keeping it is right.
  - `embedded_run.py` and the TermNorm checkout read no estimate word.
