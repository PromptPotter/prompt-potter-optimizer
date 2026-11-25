# ml-8: the loop closes clean: a call only where a decision reads it; every Estimate carries its SE (cloud)

`briefs/common.md` binds you (read BASE FIRST), with ONE override: the base is
**`origin/land/combine-loop` at `83537bd39`**, which is main `5d150ac13` plus:
- the L4 chain (l4-key-a, a2, b, c);
- spend-tomb;
- the web chain;
- the measuring loop's six slices, its defaults flipped, ml-7 (one decision loop) and ml-fork
  (`unmeasured_delta` a footnote; a declined fork recorded).

Replace the ancestor check with `git merge-base --is-ancestor 5d150ac13 83537bd39`. Equals-main runs
against `83537bd39`'s offline home. Read first:
- `git show origin/out/ml-7-report:report.md`;
- `git show origin/out/ml-fork-report:report.md`;
- `git show origin/out/l4-key-c-report:report.md`.

Push to `arc/ml-8`, one commit per link, with the full gate green at each.

**The operator's standard** (2026-11-25): "rather we do things clean from begin". The release waits.
These are the loose ends inside the mechanism being landed.

## Links, in this order
1. **A call is sent only where a decision reads it.**
   - Measured at ml-6 (`fc9a2e7b5`) under `rolling`: the parent was billed for rows that no rival
     answered that round.
     - straight leg: 21 of 234 fresh cells (round 3: 18 rows, 11 fresh; round 4: 16 rows, 10 fresh);
     - crossed leg: 26 of 268;
     - the batch default before the flip: 0.
   - Count it with `git show origin/brief/wave-5:briefs/tools/parent_only.py` over each cycle's
     `.runtime/ledger.jsonl` (`sample_scored`, keyed by `sample_key`).
   - Cause: the settle rule says the parent "stands no bar", so it never settles. The hand-out
     ranks it at full nearness, so it is handed its whole budget while its rivals are cut.
   - A parent's answer matters only through the live rivals' cuts. Derive the rule in
     `docs/methods/candidate-elimination.md` (§ Who answers a block's calls, § How many calls a
     rolling decision sends), and state it there:
     - the parent settles once every live rival settles or is cut;
     - its nearness is that of the live rival nearest its bar;
     - a call carrying no information on the judging scale is never sent.
   - Every call should be one a decision reads. If the press horizon (ml-7's decision) holds
     speculative calls, bound them the same way.
   - **Re-test:** the counts above → 0.
   - **Moves:** capo-pobb (batch) UNMOVED. Name every potter move, and the fresh-cell total before →
     after.
2. **Every t-based `Estimate` carries its SE.**
   - `shared/statistics.py::paired_mean_t` computes `se_d` and returns the n instead. Its callers
     therefore build `Estimate(se=None)`. l4-key-c's Symptoms lists the sites (`scoring/paired.py`,
     `scoring/selection.py`, `scoring/passes.py`, `evidence/metric_catalogue.py`,
     `domain/arm_reading.py`); re-grep them at your base.
   - Root: the helper returns the estimate with its SE, and every caller carries it.
   - The L4 reply's `B{lift}±{se}` then prints its band, and `panel_precision` reads a real SE.
   - Delete `evidence/metric_catalogue.py::catalogue_for`'s dead `"lift"` branch (l4-key-c's row to
     file). It keys L4 on a term no connector declares.
   - **Re-tests:**
     - `git grep -n 'se=None' -- promptpotter`: the t-based sites → 0;
     - `git grep -c seed_lift -- promptpotter`: 8 → 0;
     - the offline L4 leg's reply carries `±`.
3. **Rows ml-7 and ml-fork filed inside the loop.** Each row is in `code-debt-cleanup.md` at the base,
   with its root and re-test. One commit per row if they share no file, else one:
   - **A series caveat unions every round's cells**: a level carries its own round's caveat.
   - **A fork proposed with `rebase_capability` off is dropped unsaid**: the bit decides the schema
     the L2 is asked. If the base shows the proposal must stay asked, record the drop instead, and
     say why.
   - **`Race.open_turn` is a hook one race needs**: `n_priors` is derived off the first `View`.
   - **A max-press resume replays on timing**: the resume replays the reads the run filed, whatever the
     press. Measure the base first (three runs at press max), then name the record the replay reads.
     If the root is outside the loop (a dispatch-hub ordering), report it and stop that row.

## Owns
Everything the links need. You are the only chain running.

Ledger: delete the rows you close from `docs/specs/code-debt-cleanup.md`:
- **Under `rolling`, the parent is handed calls no live rival's decision reads**;
- **The held-out lift's SE is never served**;
- **`catalogue_for` keys L4 on a term no connector declares**;
- the four rows of link 3.

Equals-main at press max: `capo/resumed` and `gepa/resumed` move on the base itself (ml-7's report).
Pin them only once link 3's last row lands; until then report them as base noise, with the counts.

## Proof
- **Equals-main against `83537bd39`:** capo, capo-pobb, gepa and levi UNMOVED.
- Name every potter and L4 move, and attribute it to its link.
- **Digests and keys:** name every move, old → new.
- **Wipes:** name any, or none.
- **Gate:** the full gate is green at every link.
- **surface-ledger:** report the net per link, and hunt deletions.
- **Report** on `out/ml-8-report`:
  - per link the commit and +added/−deleted;
  - re-tests before → after;
  - decided in-run;
  - Rows closed, Rows to file, Symptoms.
