ARC w2u: the milestones take names. Branch `arc/w2u` off `origin/batch/4`.

Row (`docs/specs/code-debt-cleanup.md`, grep "`M13` and `M14` each name both"): M13 and M14 each name a medium arc (the `docs/specs/roadmap.md` table rows "**M13** one scoring pass", "**M14** faults") and a milestone ("§ The bench (M13)", "M14 (the preprint)" / "M14 (publication)"). Decision (orchestrator, operator-delegated 2026-11-07): the numbers stay with the medium arcs; the milestones are called **the bench** and **the preprint**, with no number.

Do: re-point every milestone reference by name, and leave every medium-arc reference as it is. Judge each hit by its sense: a table row, a sequence step, a dependency or a "Rides with:" names the arc; the bench lane, the head-to-head, the paper, the ablation rows, the stopping rule's defensibility and reviewer expectations name a milestone. Grep `\bM1[34]\b` under `docs/`, `paper/`, `.claude/`, `README.md`, `CLAUDE.md` files. Headings renamed (`## The bench (M13)` → `## The bench`, `## M14 — the preprint` → `## The preprint`) carry every `§ Heading` link to them (`gate.py --only doc-claims` checks them). Delete the row.

Owns: the files those hits sit in (docs and markdown only; no code). If a hit's sense is unclear, keep the number and list it in your report.

Named served deltas: none. Checks: the row's re-test (`git grep -nE "\bM1[34] \((publication|the preprint)\)" -- docs` is empty); `python scripts/gate.py --only doc-claims`; the full gate. Equals-main: docs only, so run `--digests` once on your tip against main's and report it equal; skip the `--against` run.

Commit: `docs: the bench and the preprint drop their arc numbers`, or close to it.
