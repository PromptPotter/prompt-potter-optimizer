# w10-survey: four areas' unexamined modules, read against their Targets (cloud · Sonnet · docs only)

`briefs/common.md` and `briefs/w10-shared.md` bind you, except that you edit NO code: your only write is
`docs/specs/code-debt-cleanup.md`.

Read each module below whole, against its area section's **Target** in the debt doc, and give it a
verdict: "meets Target", or each deviation as a row (or a line in an existing row with that root).
1. **07 Config, pipeline, datasets**: all of `application/datasets/**` (3,332 lines, one row today) and
   `application/pipeline_resolve.py` (84 commits since 2026-10-15). Decided context: a dataset read is a
   third resolution (row filed); one typed node document every door parses.
2. **11 Served reads, views, evidence**: `application/evidence/**` (`head_to_head.py`, `comparison.py`,
   `metric_catalogue.py`, `grid.py`), `views/evidence.py`, `application/read_params.py`.
3. **09 Optimizers**: the three peers no row has read, `optimizers/capo/members.py`,
   `optimizers/levi/members.py`, `optimizers/gepa/members.py`, against "one backbone, every optimizer a
   switch; no per-optimizer driver".
4. **08 Connectors**: `connectors/harbor.py` (1,545 lines, the package's largest module): does it fit
   "one module per connector here", and what would split it along a root?

FILE: each new row at the HEAD of its area's rows (right after the section's `**Unexamined:**` line;
w10-file appends at the ends). Then rewrite that `**Unexamined:**` line to drop every module you read.
Every backticked `path::symbol` must resolve to exactly one file: qualify the path.

REPORT: one line per module: lines, verdict, rows filed (titles). Name the three largest roots you found.

DONE WHEN: every module above has a verdict · `python scripts/gate.py --only doc-claims,instruction-files`
is green.
