# small-12b: small-12 rebased onto today's main and proven there (cloud)

`briefs/common.md` binds you (read BASE FIRST). Base: **`ee8f1e4d1`** (origin/main). `arc/small-12`
(05dd824b6, read `briefs/small-12.md` and its report on `out/small-12-report`) was built on a 4-day-old
checkout (7d5d12f7b, 61 commits behind): its gate, equals-main and digests prove nothing about main.
Rebase its two links (82ac3ab88 the election reads the round's measured θ fit; 05dd824b6 a sandboxed
mint writes no account label) onto the base, resolving each conflict toward the base's code: main
moved `jobs/mint.py`, `runner/round.py`, `scoring/selection.py`, `optimizers/nodes.py` and the three
test files under them. Push to `arc/small-12b`.

## Re-prove at the base, then at the tip
1. Every claim of small-12's report, re-run at the base: the callers of `elect_round_winner`; that the
   round path's election reads the SAME rows, parent and ruler as `measure_population`'s fit (cite
   the base's lines, not the report's); the replay's own fit; the offline `potter/l4` leg's account
   label file before (2 lines) and after (the outer's only).
2. The new fact `tests/test_identity.py::test_a_sandboxed_mint_moves_no_account_label` red at the
   base, green at the tip, holding on Windows.
3. Full `python scripts/gate.py` green, `offline_run.py --against` the base UNMOVED, `--digests` base →
   tip with each move attributed (small-12 saw `treatment:potter` and `treatment:capo-pobb` move from
   editing `potter/election.py`: confirm or correct at this base).

## Owns
small-12's Owns. NOT `tests/**` beyond that one fact and the `elect_round_winner` / `MeasuredRound`
test callers (tests-x), `promptpotter/infrastructure/llm/pricing.py`,
`promptpotter/application/jobs/{reaper,registry,machine_status}.py`, `jobs/launcher/**` (rates-4),
`webapp/**`, `hierarchy.py`.

## Report
Per `common.md`, with a Symptoms list; carry small-12's "Rows to file" forward if still true at the
base.
