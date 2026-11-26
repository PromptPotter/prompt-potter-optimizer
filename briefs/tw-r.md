# tw-r: a resolved config is resolved once, by its owner (cloud, Opus)

`briefs/common.md` binds you (read BASE FIRST), with ONE override: the base is `origin/land/tw-xjp` at
`<BASE-R>` = `66e345523`, green on the operator's desk. That is `land/test-world`'s `af224d3bd` plus tw-x, tw-j, tw-p, tw-w, tw-c and tw-q, integrated.
Read `out/tw-q-report:report.md` first: its § Rows to file are your two links. Replace the ancestor
check with `git merge-base --is-ancestor 9cd30b5a7 <BASE-R>`. Equals-main runs against `<BASE-R>`'s
offline home. Push to `arc/tw-r`, one commit per link, with the full gate green at the tip.

## Why
The operator asked that the test rework bring the suite's wall and memory below where it started
(`7dce00a96`). tw-q met the memory and stopped 13 s short on the wall (70.8 → 84.1 s, in its session),
with every production frame it measured ranked. Two of them re-derive a value their caller already
holds. A world run is production running a small campaign, so each of these seconds is also paid by
every real round close and every cell a real run builds.

## Links
1. **A round close re-resolves the config it holds.** `runner/output.py::RunArtifacts._render` →
   `_render_cycle_log_md` → `scoring/measurement_log.py::cycle_hard_samples` re-runs `cycle_config` and
   `campaign_scorer` on every close (~0.4 s across the suite). The close already holds the resolved
   config (`RunArtifacts._config`) and the session's scorer. Pass what the round holds down to the
   reader that needs it, so `cycle_hard_samples` resolves nothing a caller has. Its other caller
   (`measurement_log.py:460`) supplies what it holds the same way, or keeps the one resolution it
   needs, named in the commit.
2. **`to_job_search_point` re-resolves a resolved config.** `domain/opt_search_point.py::to_job_search_point`
   calls `_resolved_config` and then `PipelineParams.with_nodes`, 8,609 calls and 2.29 s across the
   suite. Find the owner of a point's resolved params (one resolution per point, wherever the point is
   built or first resolved) and resolve there once. `domain/CLAUDE.md` binds: `PipelineParams` is
   built only by reading the laid document it dumps back to, and a child `OptSearchPoint` is made by
   `derive` / `edited` alone. A cache keyed by object identity is not the root. The id of a point is
   its content and never moves.

A speed fix decides nothing. Equals-main must read every optimizer and leg UNMOVED, the digests must be
identical, and `promptpotter/assets/behaviour_keys.json` must be unchanged. A key that moves is a
behaviour change: stop and report it.

## Owns
`domain/opt_search_point.py`, `domain/pipeline_params.py`, `domain/arms.py` (the caller of link 2),
`application/runner/output.py`, and in `application/scoring/measurement_log.py` only
`cycle_hard_samples` and its callers' hunks.

**NOT yours:** chain tw-j2 runs beside you (`briefs/tw-j2.md`). It owns `tests/`, `scripts/offline_run.py`,
`scripts/offline_world.py` and the rest of `application/scoring/**`. A test that calls a signature you
change is re-pointed in that call's hunk alone, named in your commit.

## Proof
- Per link: the frame's cumulative seconds before and after, under cProfile on the same shared world
  run, and the suite's wall at `<BASE-R>` and the tip, same session.
- `scripts/offline_run.py`'s wall at `<BASE-R>` and the tip.
- Equals-main against `<BASE-R>`: every optimizer and leg UNMOVED, digests identical.
- Full gate green at the tip.

**Report:** common.md's FINAL REPORT on `out/tw-r-report`.
