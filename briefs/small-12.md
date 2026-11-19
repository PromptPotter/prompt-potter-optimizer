# small-12: one fit of a round's abilities; a sandbox writes no account label (cloud)

`briefs/common.md` binds you. Base: **`origin/main` at dispatch**. Push to `arc/small-12`. Two rows of
`docs/specs/code-debt-cleanup.md` § wave-5 · l4 (read their full text); one commit per row at most.

## Rows
1. **"Two fits of one reading."** `runner/round.py::measure_population` fits `candidate_abilities` over
   the electable arms' raced sheets against the parent (its comment: "this is the fit
   `elect_round_winner` takes of the same rows"), and `scoring/selection.py::elect_round_winner` fits
   it again. Decision: the election takes the measured `RoundAbilities`; first prove the two fits are
   over the SAME rows, parent and ruler at every call of `elect_round_winner` (name each caller); where
   one is not, that difference is the finding: report it and keep that caller's own fit, never pass
   a fit of other rows. **Re-test:** `git grep -n "candidate_abilities(" -- promptpotter` names one
   call on the round's path; `offline_run.py --against` the base UNMOVED.
2. **"A sandbox writes account labels."** An inner mint relabels the account's dataset label from
   inside a sandbox (`jobs/mint.py:584`, `session.store.tenant_datasets.relabel`), benign only while
   `dataset_hash` covers the whole dataset. Decision: a sandboxed mint writes no account label (the
   sandbox's stores are rooted at the account for datasets: `stores.py::build_stores`'s comment says
   why; decide from that whether the label belongs to the outer's mint alone). **Re-test:** an inner
   mint leaves the account's label file untouched (the offline `--l4` leg: diff the account's label
   file before and after).

## Owns
`promptpotter/application/runner/round.py`, `promptpotter/application/scoring/selection.py`, the
callers of `elect_round_winner`, `promptpotter/application/jobs/mint.py`, and one fact for row 2 in
`tests/test_l4.py` (or the file the L4 spawn facts live in; name it). NOT `tests/**` beyond that one
fact (tests-x), `promptpotter/infrastructure/store/**`, `promptpotter/infrastructure/llm/pricing.py`,
`promptpotter/application/jobs/{reaper,registry,machine_status}.py`, `jobs/launcher/**` (rates-4),
`webapp/**`, `hierarchy.py`.

## Target
Each Re-test before and after. Full `python scripts/gate.py` green, `offline_run.py --against` the base
UNMOVED (row 1 moves nothing by construction; a moved leg is a finding, name it), `--digests`
identical or each moved digest attributed. `promptpotter` nets ≤ 0 or each added line named.

## Report
Per `common.md`, with a Symptoms list.
