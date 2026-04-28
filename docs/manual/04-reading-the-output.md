# Reading the output

While a campaign runs, three streams of text tell you what's happening: per-query lines, round summaries, and annotation lines. This page teaches you how to read each one.

## Per-query lines

For every query the optimizer scores, you'll see a line like this:

```
0.0s #042 MISS [ai]📖 -> 'unknown' gt:'disproved' q:'was the hypothesis disproved?'
```

Reading left to right:

- `0.0s` — how long the backend took to answer
- `#042` — the query's sample ID (a positional index; not every dataset assigns one)
- `HIT` or `MISS` — whether the answer matched the ground truth under the active scorer
- `[ai]📖` — an icon tagging where the answer came from (e.g. a cached result, a fresh LLM call, a cache lookup)
- `-> 'unknown'` — what the pipeline returned
- `gt:'disproved'` — the ground-truth answer
- `q:'...'` — the query that was asked (truncated in long runs)

You can ignore these most of the time — they're verbose but reassure you the campaign is moving. When something goes wrong (see annotation lines below) the per-query lines are where you'll look.

## Round summaries

At the end of every round, PromptPotter prints a structured summary. The exact format depends on your entry point, but it contains roughly this information:

```
ROUND 3 COMPLETE
  Winner:   C4/8 — 62% (+4% vs prev best)
  Layer:    L1     Patience: 0/3
  Queries:  50     Cache hit: 78%

CRITIQUE: ...
NEXT: continue L1
```

What to read:

- **Winner** — which candidate (out of how many) had the best score, and how much it improved over the previous best. If nothing beat the previous best, this line will say there was no winner.
- **Layer** — which optimization layer is currently active:
  - **L1** — the normal generate-evaluate-critique loop
  - **L2** — engaged when L1 hasn't improved for several rounds; it rewrites the search framing
  - **L3** — engaged when L2 also hasn't helped; it rewrites the whole strategic plan
- **Patience** — how many consecutive no-improvement rounds have passed. When it hits the configured max, the optimizer escalates to the next layer.
- **Queries** — how many queries were scored this round. The cache hit percentage tells you how often a prior evaluation answered the question instead of a fresh backend call.
- **CRITIQUE** — 2–4 lines of the optimizer's own analysis: what worked, what didn't, what to try next round.

## Annotation lines (⚠ / ↳)

When the optimizer finds something notable during a round, it surfaces it as a two-line annotation:

```
⚠ <what was found, in data terms>
  ↳ <what the system is doing about it>
```

Examples:

```
⚠ llm_only.model = 'gpt-4o' not in allowed set
  ↳ scored 0; L2 directive will name this value

⚠ candidate degraded on 3/4 queries; eliminated early
  ↳ L2 will steer next round away from this region
```

The top line is a fact. The bottom line is the response. You don't need to do anything — the optimizer has already handled it. These annotations exist so you can audit the optimizer's judgment, not to ask you for input.

**Don't see a `↳` line?** That's a bug. Every `⚠` should pair with an action. If you find a bare warning, report it.

## Where the final results land

When a campaign finishes (or you stop it), the best-scoring candidate is recorded in the campaign directory under `.promptpotter/`. Open the artifacts directly:

- `campaigns/<cycle_id>/log.md` — the rendered per-round digest, regenerated at every round-complete and at finalize. Contains status, per-round critique / L2 directive / changes, hard-samples heatmap, and final winner.
- `campaigns/<cycle_id>/index.json::final` — the structured form: `winner_prompt_fields`, `winner_pipeline_params`, `best_accuracy`, `baseline_accuracy`, `stop_reason`.

The same per-query data renders live to the terminal during `optimize`, so you usually see the run unfold without needing to open files.

## Where the live state lands (forks and the family root)

For monitoring a run in progress, three files carry the live observability stream:

- `dashboard.json` — current phase, round, candidate, accuracies, and the in-flight query.
- `output.log` — append-only HIT/MISS history, raw and tail-friendly.
- `phase_events.jsonl` — one structured JSON record per phase event.

These three live in **one place per cycle family**: the **root cycle's** dir (the cycle with no `parent_cycle_id`). When you fork a campaign with `optimize --fork-on-divergence`, the fork's own dir nests under the root at `campaigns/{root_cycle_id}/forks/{cycle_id}/`, but its dashboard / output.log / phase_events stay at the root. So one place to tail covers the whole family — no chasing dirs as forks happen.

Inside the stream, you can tell which fork is currently active:
- `dashboard.json::cycle_id` always names the active fork.
- `output.log` gets a `=== FORK <id> from round N (parent: ...) ===` banner inline at each cutover.
- `phase_events.jsonl` records each carry a `cycle_id` field — useful when post-processing.

The fork's *own* dir (`campaigns/{root_cycle_id}/forks/{cycle_id}/`) holds its per-cycle audit (`index.json`, `log.md`, `trials/`, `candidates/`, `rounds/`, etc.). Open those when you want to inspect what specifically happened in one fork; open the root's telemetry when you want to watch live progress.

Next: [Troubleshooting](05-troubleshooting.md).
