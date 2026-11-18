# small-6: one empty answer is a miss, not a fatal; a cut reads its true count (cloud)

`briefs/common.md` binds you. Base: `origin/main` (3784ed33b or later). No ledger rows exist yet:
these paragraphs are the rows. One link each; file nothing you close.

## Symptom (observed on the desk, 2026-11-18)
Seat screen `banking77__1101ce` (cycle_483b13777b80, solver `google/gemma-3-4b-it`, temperature 0.0,
reasoning none). Arm C1.1 answered 17 of its first 18 rows (9 HIT). Row 18 (`#599`) came back
empty: `finish_reason=stop reasoning_chars=0`, "LLM returned empty content". The arm was cut at
once: `round_0001.json::arms.d1be672719f48a72` reads `state: cut`, `reason.kind: broken`,
`failure: {source: degradation_check, dominant_warning: llm_only:empty_response, degraded_rate:
1.0, degraded_count: 18, total_scored: 18}`. The readout printed `✂ broken (degradation) q18/18
fatal`, then `block 1/100: 1 raced · none cut · 1 survive`. Three faults, one per link.

## Links
1. **One empty answer cuts its arm as fatal**: `domain/results_health.py::classify_result` (~:166)
   files a stop-finished, reasoning-free empty terminal as the fatal `{node}:empty_response`, and
   `application/scoring/classification.py::DegradationCheck.check`'s fatal fast path eliminates on
   one sighting. The module's own law (the `classify_result` docstring): "a fatal code is
   deterministic for the whole config, so a rule that could fire on a row that answered correctly
   eliminates a good one". An empty answer is ROW-deterministic at best (17 rows of the same config
   answered). Action: an empty answer is graded (a miss, as `scoring-and-memory.md:95` says) and
   counts toward the degradation RATE like every other deprecated row; it never takes the fast
   path. Keep the fast path for what IS config-deterministic (a backend 4xx, a source-stamped
   structural advisory, a content filter: check each against the law and name it in the commit).
   Correct `docs/operations/backend-integration.md:28` and `docs/manual/05-troubleshooting.md:53`.
   **Re-test:** an arm whose 1 of 18 rows comes back empty-stop is not cut and its empty row scores
   0; an arm empty on ≥ the threshold's share is cut as `broken` by the rate.
2. **The fast path reports every row degraded**: `DegradationCheck.check` passes
   `degraded_count=n, total_scored=n`, so a cut on one row reads `degraded_rate 1.0` and the
   readout prints `q18/18`. Action: the reading carries the true count (the deprecated rows of the
   rows scored). **Re-test:** a fast-path cut after 17 clean rows reads `degraded_count 1`,
   `total_scored 18`.
3. **The block line omits a degradation cut**: `application/views/readout.py::_render_block_lines`
   counts `_block_decided` (the race's decisions) only, so an arm cut `broken` mid-block reads
   `none cut · 1 survive`. Action: the line counts every arm the block cut, whatever cut it, from
   the one place a cut is recorded (grep who fills `_block_decided` first). **Re-test:** the
   banking77 shape (one arm, cut broken at row 18) prints `cut C1.1 · 0 survive`.

## Owns
`domain/results_health.py`, `application/scoring/classification.py`, `application/views/readout.py`,
the two docs above, their tests. NOT `domain/validators.py` (the stop types: the running `l4-valid`
chain edits them), `application/runner/**`, `application/bench/**`, `presentation/cli/**`,
`application/jobs/**`. Link 1 moves elimination: run the `--against` legs and name every leg and
digest that moves, old→new.

## Target
Net ≤ 0 `promptpotter/` lines.

## Report
Per `common.md`.
