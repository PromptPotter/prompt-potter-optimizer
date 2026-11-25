# l4-key-b: what an archived L4 cell holds, and what a cell's floor reads (cloud · W1)

`briefs/common.md` binds you (read BASE FIRST), with ONE override: the base is
**`origin/arc/l4-key-a2` at `080871de0`**: main `da08f4717` (W1) plus l4-key-a and l4-key-a2 (the
cell key is the seat's minted origin, laid at init; the launch probes nested backends). Replace the
ancestor check with `git merge-base --is-ancestor da08f4717 080871de0`; equals-main runs against
`080871de0`'s offline home. Read `git log --format='%h %s%n%b' da08f4717..080871de0` and
`git show origin/out/l4-key-a2-report:report.md` first. Push to `arc/l4-key-b` as ONE commit, or
several only where the full gate is green at each; a doc rides the code it describes.

Beside you run, on `land/measuring-loop`, **ml-5** and **potter-w1**. You share two files by
section: `potter/knobs.py` (ml-5 adds the `dispatch` knob; yours is `terminate_capability`'s
description alone) and `research/datasets/promptpotter-self/family.yaml` (potter-w1 re-derives the
`l2_context` share; yours is the inner measurand and ladder, and the citation below). Touch nothing
else of theirs (`optimizers/potter/`, `scoring/query_loop.py`, `runner/round.py`, `rasch.py`).

**You are in W1, the re-key window** (operator 2026-11-19): every backbone shape drift lands now,
batched so run data is wiped and L4 cells are voided ONCE. Here L4 cell scores, the estimator key
and the archive row's shape MOVING is the PURPOSE: name each old → new and the link that moved it.
**The operator's standard: "we always want to go to the full perfect shape at the root"** — no
transitional step, no bridge, no reader of two shapes, no "kept X, filed the root".

## Links, in this order (read each row's full text in the ledger at your base)
1. **`floor_reason` and the bench read different rounds** (§ Held): the spawn reads the line's spent
   rounds (`runner/bench.py::LineSpan.own`), so a fork's discarded rounds never judge "no proposal
   reached a cell". Re-test: a forked inner line whose pre-fork rounds all spoiled.
2. **The abort knob and the floor disagree** (§ Held). **Decided (the orchestrator):** an abort
   carries who owns it, and only a prompt-owned one floors. The owner is DERIVED from the run's
   recorded facts (an upstream fault the run filed before the abort), never from the abort's
   reason text; `potter/knobs.py::terminate_capability`'s description says the same. Re-test: an
   inner run aborted on a backend fault reads `floored` 0 (today 1); a prompt-owned abort reads 1.
3. **`origin_headroom`, written and never read** (row "Fields written, never read", its one W1
   field): `domain/l4/inner_facts.py` and `domain/l4/proxies.py::_origin_headroom` go. The other
   fields of that row are W2's: leave them.
4. **An archived answer carries its run's vocabulary and its run's address** (§ wave-5 · drift,
   D2) **with An archived answer's outputs are read off today's backend** (§ batch/30 · forks): ONE
   strict reshape of the archive row. An archived cell holds what was measured: no `StopReason`, no
   `floor_reason` sentence, no `InnerRunAddress`, no `filed_by`; a per-node output-key map is filed
   with the answer and a replay reads the bank, not the live declaration. **Decided (operator
   2026-11-16):** the archive reshapes STRICT; an answer that no longer fits is dropped (re-paid on
   demand, about $0.0175 a cell), never read in two shapes. Grep every reader of `filed_by` and
   `_UNSTAMPED` first.
5. **The inner measurand differs by benchmark** (§ Held). **Decided (the orchestrator):** one inner
   measurand and one ladder across seats: the L4 family declares the inner scoring and escalation
   once, on the channel that already pins the inner seat's spend axes, and no seat's own
   `campaign.scoring` discount or `escalation.l1_patience` reaches an inner run. Decide the channel
   by the fewest spellings and say why. Re-test: the three seats' resolved inner configs carry one
   `scoring` and one `escalation`; `runner/inner/tasks.py::_lever` names them.
6. **A cell's pin over-keys** (l4-key-a2's row to file): `domain/cells.py::PinnedCell` still holds
   `Seat.id`, a label, so renaming a seat moves its cell key. The pin drops the seat id. It moves L4
   cell keys, so it lands in this window. **Re-test:** renaming a seat moves no cell key.
7. **One depth rule, one door** (l4-key-a2's row): resolve now refuses a seat on a
   self-optimization dataset (`runner/inner/tasks.py`, `pipeline_config_invalid`), beside the spawn's
   depth check (`spawn.py::run_inner_cycle`, `measurement_context.py::MAX_INSTRUMENT_DEPTH`). Prove
   which one a run can reach; keep that one, delete the other, and say so in `docs/concepts/l4.md`
   § Depth.
8. **Two loose ends** (l4-key-a2's rows): `embedded_run.py` imports `maintenance.landing` at module
   scope (:38, :77), as `run_job.py` did; only an entry lands, so move it there (re-test:
   `git grep -n "maintenance.landing" -- promptpotter/application/embedded_run.py` is empty). And
   `family.yaml:25` still cites `InnerTaskSpec`: cite `domain/l4/cells.py::cell_key`.
9. **The archive is a cache no landing proves** (§ wave-5 · drift, D1), LAST, on the settled shape:
   the row's `Shape` in `store/data_root.py::_ARCHIVE_SHAPES` is its ONE strict reader, nested
   reports typed by their connector; a row that no longer loads is dropped at the landing and the
   landing says how many, by reason. Re-test: a fixture per `cell_report.inner` generation and the
   retired `source` loads or is dropped by name. The orchestrator runs the landing's dry run on a
   copy of the desk root (50 of 325 L4 outer cells raise today): say what it should read.

## Owns
`domain/l4/proxies.py`, `domain/l4/inner_facts.py`, `domain/sample.py`, `domain/scoring.py`'s
`MeasuredCell`, `application/scoring/measurement_log.py`, `infrastructure/store/measurement_archive.py`,
`archive_maintenance.py`, `infrastructure/store/data_root.py`, the archive's landing step in
`maintenance/landing.py`, `runner/bench.py`, `runner/entry.py`, `runner/inner/spawn.py`,
`runner/inner/tasks.py`, `potter/knobs.py`'s `terminate_capability`, the seats' family files
(`justlogic`, `hotpotqa`, `legalbench`) and `research/datasets/promptpotter-self/family.yaml`, their
tests, the generated specs (regenerated by their script), `assets/behaviour_keys.json` (regenerated
by its script only). Beside you run main-lane chains only; an edit outside this list goes to "Rows
to file".

## Proof
Equals-main per `common.md` against the base, every leg stated: capo, capo-pobb, gepa, levi and
potter decisions UNMOVED; the L4 leg's moved cell scores each attributed to links 1, 2 or 5;
requests unmoved; every digest, key and archive-shape move named old → new. Full gate green.
surface-ledger: hunt deletions in what you own (links 3, 4 and 6 delete). Report on
`out/l4-key-b-report` (per link the commit and +added/−deleted; re-tests before/after; digest
moves; wipes and archive drops; decided in-run; Rows closed; Rows to file; Symptoms).
