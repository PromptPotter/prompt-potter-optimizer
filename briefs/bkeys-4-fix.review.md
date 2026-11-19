# Review: bkeys-4 (e8183708f..c326976ca), read-only

Verdict: **land after fixes**. Links 1, 2, 4 and 5 hold their decided shape. Link 3 does not.
- **F1:** its fingerprint is a function of the absolute path of the tree that ran the leg, and the gate's offline home is shared across trees.
- **F2:** prompt prose on continuation lines of unrun code is keyed by nothing.
- **F4:** a rename on a run line re-keys whenever it touches a `def` header.
- **F3:** a gap in the brief's own shape (module- and class-level code). It is a design row for the orchestrator, not a builder miss.

F1, F2 and F4 are local to `scripts/offline_run.py` (`traced` / `_function_lines` / `unrun`) and are fixed on the branch. The rest are low.

How this was checked:
- Probes ran against `git archive` copies in `scratchpad/bk4r_6c35/`, using the repo venv with `PYTHONPATH` pinned.
- The shared scratchpad's generic `tip/` was overwritten by a sibling agent mid-review (its `docs/`, `scripts/` and `webapp/` swapped; `promptpotter/` stayed equal to the tip). Every probe was re-run in the isolated copy, with three exceptions that came from the shared `tip/` and `home_tip`:
  - the five-optimizer offline run (298 s);
  - its byte-for-byte `behaviour_keys.json` match;
  - the 123 round files.

  They stand because the run's output reproduced the tip's committed keys, `packages` map included, which only the tip's link-3/4 code writes. Potter was reproduced in isolation by the edited-copy run.
- Free offline runs on this desk (no paid call):
  - the tip, all five optimizers (`tip/` + `home_tip`, 298 s);
  - pre-link-3 `67b584e9e` (`bk4r_6c35/home_l2`, 690 s);
  - the tip with two edits, potter only (`bk4r_6c35/homeE`, 343 s).
- **Equals-main on this desk:** decisions and requests are unmoved for all five built-ins, base → tip.
  - The tip run's traces equal the pre-link-3 run's field for field (`tracecmp.py`).
  - `stable_hash([decisions, requests])` of each equals the base's committed fingerprint, which at the base is exactly that hash: capo `d1905beae72c834c`, capo-pobb `453a8047707648eb`, gepa `eebbf2590c81a81c`, levi `4f56468b534fdfd9`, potter `b7cf14069d70962c`.
- Not re-run: the full gate, vitest/tsc on the tip, playwright.

## F1 (high, confidence high on the mechanism, medium-high on the gate path): the fingerprint depends on where the leg's tree lives
- **Where:** `scripts/offline_run.py:1850-1869` (`traced`) and `:1833-1847` (`unrun`).
- **Defect:**
  - `traced` files each line under `code.co_filename`, an absolute path. `unrun` matches the absolute path of the tree now reading (`member_package_dir` → `os.path.normcase`).
  - A ran-lines file recorded by a tree at another path matches nothing. Every function line then reads "unrun", silently, with no error.
  - The gate homes the offline run in the clone's shared cache (`gate.py:1499-1501` `_OFFLINE_HOME = clone_cache() / OFFLINE_RUN`, "so a worktree's run answers for main"). Its greens are content keys that hold in every tree (`last_green.py` "a green any worktree recorded holds in every other"; `still_green`).
  - `behaviour_keys(home, names)` recomputes EVERY built-in's fingerprint, green legs included (`offline_run.py:1807-1809`, and the `if not due:` path).
- **Concrete failure:** I took the same home and the same `trace.json`, and only moved the tree that reads them:
  - read by the tree that ran it: potter's fingerprint is `79eaba9a311bda14` (the committed value), with 1506 unrun lines;
  - read by a tip copy at another path: `fd725cafd8287904`, with 3428 unrun lines.

  So a gate run in a linked worktree regenerates wrong fingerprints for every leg another tree left green. So does `.githooks/pre-push`:
  - `pre-push` runs `gate.py --push`, and `_query` keeps every default-kind check, the `landing`-tier offline run included (`gate.py:2125-2145`).
  - `_prove_tips` runs that check in `_proven_in`'s temporary `git worktree` (`:1955-1972`, `:2033`).

  Pushing main right after a local full gate on the same content is therefore that case. The run prints `wrote …behaviour_keys.json` and goes red, and it writes those keys into that tree, where a commit carries them. CI is spared only because it keeps no green (`LastGreen.open` is `None` under `CI`).
- **Falsifying check:** `stable_hash([t.decisions, t.requests, offline_run.unrun("potter", home)])` for one home, from two tip copies at different paths, gives unequal results (`bk4r_6c35/pathdep.py`, measured).
- **Fix:**
  - `traced` files lines keyed by package-relative POSIX path, and `ran_lines`/`unrun` match on that.
  - `unrun` raises when a leg's ran-lines name no package file, so it never reads "nothing ran".

## F2 (high, confidence high): link 3 leaves continuation lines and indentation of unrun code unkeyed
- **Where:** `offline_run.py:1872-1884` (`_function_lines`) and `:1845-1846` (`text[at - 1].strip()`).
- **Defect:**
  - An unrun line is keyed only if `code.co_lines()` names it. A continuation line of a multi-line literal carries no bytecode; that includes every fragment after the first in an implicitly concatenated prompt string. Its text is never keyed.
  - `.strip()` drops indentation, so re-nesting an unrun statement (in or out of an `if`) keys the same text.
- **Concrete failure:** `potter/dispatch/injections/catalogues.py:181-188` (`_pool_items`) builds the RECOMBINATION POOL header L1 is sent on a run with `l1_generate.recombine > 0`. That is a knob listed in the manifest's `main:` setup knobs (`assets/optimizers/potter/pipeline.yaml:23`), and potter's default is `recombine: 0` (`:75`).
  - Lines 181-190 never run in the straight run, and lines 185-187 are in no `co_lines`.
  - Replacing "so a child invents nothing." (line 187) leaves `unrun("potter")`'s hash equal.
  - End to end, in a tip copy carrying this edit and F3's `PLAN_LENGTH_FLOOR_CHARS = 600`, `PROMPTPOTTER_HOME=… python scripts/offline_run.py --optimizer=potter` printed `unchanged promptpotter/assets/behaviour_keys.json`. Its `--digests` gives `treatment:potter` `0ba5e00b47e2b1f2`, equal to the tip's.
  - `Treatment.prompt_hashes` hashes manifest prompts, levers, schemas and call settings (`optimizer_manifest.py:563-575`), and `manifest` hashes the manifest. Neither reads this code. So `treatment:potter` stays put, and a banked L4 cell for a recombine arm is reused after its prompt changed: the review's F4, still open for prose.
  - Six multi-line string constants in wholly-unrun functions of potter's member packages have unkeyed lines. Most are docstrings; `catalogues.py:184` is a prompt.
- **Falsifying check:** in a tip copy holding potter's straight-run `ran-lines-*.json`, edit `catalogues.py:187`. `stable_hash(unrun("potter", home))` is equal before and after (`retest3.py`).
- **Fix:** key each unrun *statement* by its AST (`ast.dump` of the statement node, with its nesting path), not stripped `co_lines` text.

## F3 (high-medium, design row, not a builder miss): module- and class-level code read only on unrun lines is keyed by nothing
- **Where:** the brief decided "the source of the built-in's own package's function bodies the offline world did not run". `_function_lines` keeps function bodies only ("A module or class body runs at import").
- **Defect:** a module constant or a class body that only unrun code consumes is in neither the trace nor the unrun text. The unrun line reads its name, not its value.
- **Concrete failure:** four simple constants in potter's member packages are read only on unrun function lines (`holes.py` over the straight run's ran-lines):
  - `potter/validators.py:315 PLAN_LENGTH_FLOOR_CHARS` (read at :322/:326, neither ran). 60→600 changes which L3 plans are rejected, which is a decision on an arm that runs L3.
  - `potter/dispatch/injections/layer_state.py:347 _TERMINATE_CAPABILITY_TEXT` (read at :364 only, unrun): a directive prompt.
  - `layer_state.py:238 _SKILL_TIERS_TEXT` (read at :257 only): a prompt.
  - `potter/dispatch/bundle.py:99 RECOMBINATION_ID_CHARS` (read at :191 only).
  - Editing `PLAN_LENGTH_FLOOR_CHARS` or `_TERMINATE_CAPABILITY_TEXT` leaves `unrun("potter")`'s hash equal (`retest4.py`). The real regeneration with `PLAN_LENGTH_FLOOR_CHARS = 600` printed `unchanged …behaviour_keys.json`, and `treatment:potter` stayed `0ba5e00b47e2b1f2` (§F2's end-to-end run).
  - Pydantic field descriptions and class defaults of a model only off-run code uses are the same hole.
- **Falsifying check:** as above, `PLAN_LENGTH_FLOOR_CHARS = 600`.
- **Fix (for the orchestrator to decide):** key the module- and class-level statements of the member packages by source beside the unrun statements, since they cannot be attributed to a run. Or key a module binding by source wherever every reader of it is unrun.

## F4 (medium, confidence high): a run function's `def` line counts as unrun, so a rename on a run line re-keys
- **Where:** `offline_run.py::_function_lines`.
- **Defect:** `co_lines()` gives a function's header line (its prologue). In the straight run's ran-lines, that line is almost never recorded as run even when the whole body ran (measured below), so it is keyed as unrun.
- **Concrete failure:**
  - Of 328 functions potter's straight run ran in its member packages, 221 have the def line in `_function_lines`. For 216 of those, the def line is absent from ran-lines.
  - Renaming `ctx` → `context` in `pools/members.py:162-164` (`initialize`; body lines 163 and 164 both ran) moves `unrun("potter")`'s hash (`retest2.py`).
  - The brief's re-test holds as the builder reported for a body-local rename: `demo` → `split` on run lines 163-164 leaves it equal (`retest5.py`). An edit to unrun `validators.py:322` moves it.
  - But a function, parameter or annotation rename re-keys potter. Through link 4, it also re-keys every plugin with a potter, pools or variation member.
- **Falsifying check:** `162 in ran_lines(home/"potter")[…pools/members.py]` is False while 163 and 164 are True, and the rename above moves the hash.
- **Fix:** treat a code object's header lines (`co_firstlineno` through its first body statement) as run when any body line ran. With F2's AST keying, key statements, not headers.

## F5 (low, confidence high): link 4 stores a derivable map and keys a shared package by unrelated optimizers
- **Where:** `promptpotter/application/optimizers/nodes.py:351-352,397-405`; `offline_run.py:1810-1813`.
- **Defect:**
  - `BehaviourKeys.packages` persists what `select_optimizer(bench_choice(o)).member_nodes` derives at read time.
  - A built-in member's package is keyed by the fingerprint of every built-in whose roster reaches it. `pools` and `variation` map to capo, capo-pobb and potter.
  - An unmapped built-in package raises a bare `KeyError` in `behaviour_key`, not the named `LookupError` its sibling branch gives.
- **Concrete failure:**
  - A capo-only behaviour change re-keys a plugin whose only built-in member is `pools.demo_pool`.
  - The KeyError is latent: all six `BUILTIN_PACKAGES` are mapped today (probe).
- **Falsifying check:** `BehaviourKeys.held().packages["pools"] == ["capo", "capo-pobb", "potter"]`.
- **Fix:** derive the map live in `behaviour_key` (lazy import if a cycle forbids a top one) and raise the named `LookupError`. Or name in the docstring why it is stored.

## F6 (low, confidence high): served prose still names the deleted `scoreboard` (link 5)
- **Where:** `promptpotter/domain/results.py:107` (`Election.leading`: "Rank 1 of `scoreboard`"), shipped in `docs/specs/openapi.generated.json:6653`.
- **Defect:** no stored or served `scoreboard` exists any more. The rank is `RoundView.candidates` / `RoundResult.ranked()`.
- **Falsifying check:** `git grep -n "Rank 1 of \`scoreboard\`" origin/arc/bkeys-4`.
- **Fix:** say "rank 1 of `RoundView.candidates`", regenerating the specs (W1, `SERVED_CONTRACT` moves once).

## F7 (low, confidence high): link 1's fixture has no tie at 1 digit/scale 1 (`rounds`) or 4 digits (`usd`)
- **Where:** `scripts/build_ts_types.py:512-515` (`_WORDED_VALUES`).
- **Defect:** the fixture has ties for rate (0 and 1 digits), score, theta, seconds and rank (2, 3), tokens (0 digits, and compact k/M), and −0.
  - `rounds` (1 digit, scale 1) has no exact tie: it needs x.25 or x.75, and the set has neither.
  - `usd` (4 digits) has none; `1/32` is one.
- **Concrete failure:** none today. A differential probe of the ported `fixed`/`numberIn` against Python found 0 mismatches below |scaled| 1e21 across 46,016 cases (ties at every digit count, ±0, k/M boundaries, random values). At ≥1e21, `toFixed` returns exponent form, which is out of range in practice. The fixture just would not catch a regression at those digit counts.
- **Falsifying check:** `format(0.25, '.1f')` gives `'0.2'` and `(0.25).toFixed(1)` gives `0.3`. `format(1/32, '.4f')` gives `'0.0312'` and `(1/32).toFixed(4)` gives `0.0313`. Neither value is in `_WORDED_VALUES`.
- **Fix:** add `0.25`, `1 / 32` and their negatives.

## F8 (low, confidence medium): the harness re-synthesizes the dropped field under its old name (link 5)
- **Where:** `scripts/offline_run.py:1736-1740` (`_round_file`).
- **Defect:** it splices a computed `"scoreboard"` back into the round dict so `_id_map` (:1593, :1602) and `_round` (:1647) keep their dict reads. That is a relabel kept for two readers.
- **Fix:** `_id_map`/`_round` read `RoundOutcome.ranked()`. Note that a round file cannot be re-read as a `RoundResult` in either mode: `CellSheet` is an `isinstance` type, which is pre-existing. Nothing in the package reads round files back; they are write-only projections (`closed_rounds.py::_check_out`).

## F9 (low): smells, one line each
- **`EvidenceCharts.tsx:86`:** it inlines `(spec.lift ? fmtLift : fmtUnit)(spec.unit, v)`, the deleted `fmtMetric(metric, v)` respelled at its one caller. `MetricSpec.lift: bool` and `Estimate.form` are two spellings of level-vs-lift.
  - Check: `git grep -n "lift ? fmtLift" origin/arc/bkeys-4`.
  - Root: the served `MetricSpec` carries `form`. That is outside Owns, so it is a row to file.
- **Static walk vs importlib (link 2):** `import_closure` cannot see `importlib` dispatch.
  - After `landing` imports, 20 package modules are loaded but outside `loading_modules()`: `walked(projections)` → `infrastructure/projections/event_stream.py` (which registers `LedgerIndex.readers(ActivityFeed)`), `connectors.*`, `potter.members`, `pools.members`, and others.
  - No loaded shape lives in them today. Every `domain/records/*` module and the dynamically imported members' state kinds are seeded by `_ran`/`_loads`: `closure(landing)` is 298 modules, `loading_modules()` 325, and the extra 27 are those seeds, so `_ran`/`_loads` are not deletable. The ledger row parses via `ledger_line`, not the folds.
  - So nothing is missed now; it is latent. Check: diff `sys.modules` against `loading_modules()` after `import landing`.
- **Walk root (link 2, confidence medium, not reproduced in a real worktree):** `shared/import_walk.py:76-78` roots `_BASE` at the imported package.
  - Standalone `python scripts/complexity_ledger.py` in a linked worktree without `PYTHONPATH` resolves `promptpotter` through the venv's editable finder to main, and `relative_to(_BASE)` raises.
  - `last_green` used the script's own `REPO`. The gate pins `PYTHONPATH` (`gate.py:2199`), so gate runs are unaffected.
- **Interpreter dependence (link 3):** the key now depends on CPython's line table (`co_lines` + PEP 669). The 3.13 pin holds it, but a minor bump re-keys every built-in once. Worth one line in `offline-run.md`.

## Checked and holding
- **Link 1:**
  - `build_ts_types.py` writes `estimate-words.generated.json` from `Estimate.point/band/text`. The `ts-types` check (`_probe(..., _rewrote)`) reads and writes the fixture and is red on any `wrote` line, so a stale fixture fails the gate.
  - Every unit × form is covered.
  - `numberIn`: ties round half-even at every digit count and on the compact `k`/`M` step; −0 keeps its sign, unsigned levels included (0 mismatches, above).
  - `fmtPoint` is gone, `Estimate`'s docstring names no browser file, and the `format.ts` citations name `paired_reading.py`.
- **Link 2:**
  - One AST walk lives in `shared/import_walk.py`, called by `landing.py`, `gate.py::_layering`, `offline_run.py::unreached` and `complexity_ledger.py`. `last_green.py` has none.
  - The walk sees function-body and `TYPE_CHECKING` imports and enclosing `__init__`s.
  - `promptpotter.domain.kinds` is in the closure.
  - The new test passes at the tip. A seeds-only walk swapped in leaves the key unmoved on the `kinds.py` edit, so the test would go red (`narrow.py`).
  - The landing key is deterministic across OS: Windows computes `5a3e064c60067307` at `67b584e9e`, the commit's Linux value. It is equal across import orders (`landing` first vs `promptpotter.main` first).
- **Link 3:**
  - Windows reproduces the tip's Linux-generated `behaviour_keys.json` byte for byte (`unchanged …behaviour_keys.json`).
  - `--digests` on Windows gives the commit's treatments: potter `0ba5e00b47e2b1f2`, capo `6877123ca3f4a0d7`, capo-pobb `335bc01b022644ae`, gepa `a1173118061abd62`, levi `58dc0bc226167be2`.
  - Keyed paths are relative POSIX and newlines are normalised; inside one tree, nothing machine-specific enters the key.
  - Partly-run functions key their unrun lines only.
  - The set is folded into the stored fingerprint and re-derived by the offline-run check.
  - Cost: the builder's 63 → 66 s stands unrefuted. My two desk runs (tip 298 s, pre-link-3 `67b584e9e` 690 s) ran on a machine loaded by sibling agents and do not compare.
- **Link 4 (code read plus the package-map probe; no plugin manifest probed):**
  - A manifest outside `PACKAGE_ROOT` hashes its non-built-in members' top packages plus `manifest_directories` (its own plus what it extends and swaps from). Built-in members carry built-in fingerprints.
  - The built-in branch is unchanged.
  - With F4, the builder's "a potter rename leaves a potter-member plugin" holds for body-local renames only.
- **Link 5:**
  - The 123 round files the tip's offline run wrote hold no `vs_reference_name` and no `round_id`, `scoreboard` or `headline` key.
  - No reader of those keys remains in the package, scripts, tests, webapp fixtures, skills or docs.
  - `RoundView` serves `headline` and the rank (`candidates` in `ranked()` order). `RoundResult` is unserved (no spec diff in the commit).
  - `extra` falls back to `StrictModel`'s `forbid`.
  - CLI (`ansi.py`, `phase.py`), potter's `bundle.py` and the API read `ranked()`/`headline` as properties.
  - `application/embedded_run.py`, `views/render/export.py` and `domain/l4/` name neither `scoreboard`, `round_id` nor `RoundResult`: `grep -rln "scoreboard\|round_id\|RoundResult"` over them is empty.
  - The export reads the served tree (`cycle_reads.py::export_cycle`). Nothing in the package reads a round file back: they are write-only projections of `closed_round()` (`closed_rounds.py::_check_out`).
- **Ledger:** no row in `code-debt-cleanup.md` is closed or made stale by the chain, and no citation names `fmtPoint`, `_package_dir`, `PROMPTPOTTER_OFFLINE_COVERAGE` or `last_green::imported`.
- **Overlap with `arc/addr-4`:**
  - Shared files are only the generated specs, `types.generated.ts`, `behaviour_keys.json`, `complexity_ledger.py` and `tests/test_security.py`.
  - `complexity_ledger.py`: both chains rewrite the same `package_lines` comment and value, a textual conflict for the orchestrator.
  - `tests/test_security.py`: the hunks are disjoint.
