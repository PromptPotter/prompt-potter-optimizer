# Evidence to publish: the first runs, and what each must leave behind

Written 2026-10-11 from the site rebuild. The site's `/benchmarks` page now shows a protocol and a
roster and says plainly that no optimized result is published. This is the shortest list of runs
that turns it into evidence. The rules it rests on are [`benchmarks.md`](benchmarks.md) and
[`bbeh-comparison/README.md`](bbeh-comparison/README.md); nothing here overrides them.

## The runs, in order

| # | Run | Who it convinces, and why |
|---|---|---|
| 1 | `justlogic/d234-held`, potter alone. First `noise-floor --k 3` on the origin, then one full campaign, then `bench`. | **The sceptic.** Synthetic, so no contamination. Balanced labels, so no shortcut. The score is read on rows the search never saw, and the noise floor is printed beside the gain. Cheapest model, so anyone can repeat it. |
| 2 | The same dataset as a head-to-head: `new --arm` for potter and capo, then gepa and levi. One model, one held-out set, one budget. | **The researcher comparing optimizers.** This is the first table where only the optimizer differs. CAPO first, as the roadmap already says. |
| 3 | `gsm8k`: one measured origin pass at the pinned model, then a campaign scored on 200 or more test rows. | **The researcher and the buyer.** Everyone knows GSM8K, and CAPO's published 78.1 to 93.7 is the number people will hold ours against. Its origin has since been measured saturated at the bar, so the solver this run needs is [`benchmarks.md`](benchmarks.md) § Order of use's GSM8K row. |
| 4 | `sealqa/longseal-12`, all 254 tasks, skill opened, judge-graded. | **The practitioner building agents.** The paper gives a no-skill figure and a ceiling, so the result lands between two numbers we did not choose. |
| 5 | `lca-termnorm`, one campaign on the multi-step pipeline. | **The practitioner with a real pipeline, and the buyer.** It is the only run where settings move beside wording, on a business task. Publish only if the rows may be shown; record the row count, which `README.md` does not. |

Two cheap additions if time allows: re-measure the BBEH origin on the model `dataset.yaml::pipeline` now pins,
so the 28% stops carrying a caveat; and the "L1 only" arm against the full ladder on run 1, which is
config only and answers the first question a sceptic asks.

## What every run must leave behind

Commit one folder per run under `paper/evidence/<date>-<dataset>/`, so the site can link files on
`main`:

- `export.json` (the `bench` block: `bench_size`, `scorer_id`, `origin`, `selected`, `vs_origin`,
  `cost`, `status`; plus `dataset_hash`, `treatment`, `stop_reason`, `tool_version`)
- `index.json` (`final.rounds_to_separable`, `final.rounds_to_improved`)
- `dashboard.json` (`spend.total_used_usd`, `spend.total_tokens_used`, `spend_by_round`, `rounds[]`)
- `rounds/round_000N.json` and `hard_samples.json` (intervals per candidate, ability, per-sample rows)
- `result.json` (wall clock per launch), the `dataset.yaml` used, the exact
  commands, and the `noise-floor` output.

The site then adds one entry to `src/data/results.ts` with `status: 'measured'` and the link to that
`export.json`. It prints nothing a file does not state.

## The minimum honest report, per run

Dataset and cut (name and `dataset_hash`). Model, provider and settings. Rows searched on and rows
held out, with the split seed. Origin score and optimized score, both on the held-out rows, with the
interval and what it is. The noise floor from repeated origin passes. Rounds run, and
`rounds_to_separable`. Cost in dollars (with the price date), tokens, calls and wall clock. How many
times the held-out rows had been read before (`reads_before`). Seeds, and how many repeats. A run
done once says "one run".

## What a head-to-head needs

The same model for every arm, stamped from the wire and not from a constant. The same held-out rows
and one grader. The budget matched in spent tokens and reported as an axis, not hidden. Every arm's
defaults stated, and each peer run its own way once as the uncontrolled reference. Paired
differences per row with an interval. Rows where we lose stay in the table.

## What not to publish

- Any figure from fewer than 200 rows as a result, and any tuning reading at all
  ([`benchmarks.md`](benchmarks.md) § Protocol).
- In-campaign fitness, the round banner or the dashboard headline: they are read on the rows that
  picked the winner ([`benchmarks.md`](benchmarks.md) § The winner's own number).
- A BBEH comparison before the model and the grader are the same on both sides.
- Numbers from two cuts or two models in one row, or beside a figure from someone else's paper
  without saying so.
- A gain smaller than the noise floor, as anything but "no measurable change".
- Anything from `promptpotter-self/e2e`, which is a fixture.
- The figures the site removed on 2026-10-11 (listed under REMOVED in the site's `results.ts`).
  None has a file behind it.
