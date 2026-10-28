# Reading the output

While a campaign runs, three streams of text tell you what's happening: per-sample lines, round summaries, and annotation lines. Every launch also writes them to the cycle's `readout.log`, whose path it prints as `Readout:`. To watch in the browser, run `python -X utf8 -m uvicorn promptpotter.main:app --port 8001` and open <http://127.0.0.1:8001/>.

## Per-sample lines

For every sample the optimizer scores, you'll see a line like this:

```
69.9s #042 MISS [ai] io=86279/5670 c41% -> 'unknown' gt:'disproved' q:'was the hypothesis disproved?'
```

Reading left to right:

- `69.9s` — how long the backend took to answer
- `#042` — the sample's ID
- `HIT` or `MISS` — whether the answer matched the ground truth; `ERR` is a pipeline error
- `[ai]` — the pipeline node the answer came from
- `📖` (when shown) — replayed from an earlier measurement: no call, no money
- `io=86279/5670` — input / output tokens
- `c41%` — the share of the input the provider served from its own cache, at a discount
- `-> 'unknown'` — what the pipeline returned
- `gt:'disproved'` — the ground-truth answer
- `q:'...'` — the query that was asked

You can ignore these most of the time — they reassure you the campaign is moving.

## Round summaries

A round opens with its number and the distance to the next escalation, then the candidates it generates. It closes with one block:

```
ROUND 3/10                                        stall 0/3 → L2
...
  SCOREBOARD
  #   Label    Cells   Accuracy             95% CI   Composite   Ability θ     Delta
  1   C3.1     50/50      74.0%     [61.0%, 84.0%]      0.7400      +0.412    +12.0%  *
  2   C3.3     14/50      57.1%     [32.0%, 79.0%]      0.5710         ---       ---  (eliminated)
  ✓ SELECTED C3.1  74.0% (vs reference 62.0%, +12.0%)
  lift vs reference: +0.120 [+0.031, +0.209] on 50 shared  |  clears 0
```

What to read:

- **`stall 0/3 → L2`** — rounds with no improvement, and how many bring in the next layer:
  - **L1** — the normal generate-score-critique loop
  - **L2** — engaged when L1 stalls; it re-aims which evidence L1 looks at and how widely it explores
  - **L3** — engaged when L2 also stalls; it replans the strategy
- **`Cells`** — read it first: how many samples the candidate answered of those asked. A candidate cut at `14/50` is not comparable to one at `50/50`.
- **`✓ SELECTED`** or **`· HELD`** — which candidate won, or that the best so far stands. The winner is picked on ability (θ), not on the accuracy column.
- **`lift vs reference`** — the winner's gain over its parent, with its interval. `spans 0` means the round did not really separate them.

Candidates are labelled `C0` for the origin and `C{round}.{n}` after it.

## Annotation lines (⚠ / ↳ / ✂)

When the optimizer finds something notable during a round, it surfaces it as a two-line annotation:

```
⚠ <what was found, in data terms>
  ↳ <what the system is doing about it>
```

A candidate cut short says why, starting `✂`:

```
✂ eliminated q18/28  p_best=3.2% < eps=15%  vs C3.2 (of 4 priors)
✂ broken (degradation) q3/4  75% degraded  (llm_only:reasoning_budget_exhausted)
```

`eliminated` means more samples were not worth buying: it could no longer win. `broken` means the pipeline itself was failing (see [Troubleshooting](05-troubleshooting.md)).

You don't need to do anything — the optimizer has already handled it. These lines exist so you can audit its judgment. **A `⚠` without a `↳` is a bug**; report it.

## Where the final results land

Each run is a cycle under `.promptpotter/`. When it finishes:

- `export.json` — the winning prompt and configuration, with its provenance
- `log.md` — a per-round digest
- `python -m promptpotter bench` — scores the winner on held-out samples, the headline number
- `python -m promptpotter export --tier package` — the winner as a folder another agent opens

## Stopping and resuming

The first Ctrl+C pauses: everything already measured is kept. A second force-quits. `python -m promptpotter resume` continues from the last finished round; `resume --from N` rewinds to round N.

Next: [Troubleshooting](05-troubleshooting.md).
