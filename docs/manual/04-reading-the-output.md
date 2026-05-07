# Reading the output

While a campaign runs, three streams tell you what's happening: per-query lines, round summaries, and annotation lines.

## Lifecycle at a glance

```
init       baseline       round 1..N            stop
  │           │              │                    │
  ▼           ▼              ▼                    ▼
prep      score start      generate → evaluate     winner
no LLM    prompt on full   → critique → winner    in index.json
calls     scoring slice    selection              best in log.md
                           (L2 fires on stall;
                            L3 fires on L2 stall)
```

Each round runs four steps in sequence:

| Step | What happens |
|------|--------------|
| **Generate** | L1 evolves N candidates from the previous round's critique + axis-index intelligence. |
| **Evaluate** | Each candidate scored query-by-query. After `elimination_n_min=4`, PoBB can stop scoring inferior candidates early. |
| **Critique** | Reads raw per-query results. Produces structured analysis: failure clusters, what to try next. |
| **Winner** | Round's fittest beats current best by ≥ improvement threshold → new best, baseline advances. Else patience++. |

When `l1_patience` consecutive rounds bring no improvement, **L2** fires. When L2-adjusted rounds also stall, **L3** fires. Self-healing fires on validation / runtime failure regardless of patience — see [`../concepts/self-healing.md`](../concepts/self-healing.md).

Between rounds (in order): AxisIndex refresh, zero-signal filter (off by default), exploration/exploitation rebalance (on by default).

## Per-query lines

```
0.0s #042 MISS [ai]📖 -> 'unknown' gt:'disproved' q:'was the hypothesis disproved?'
```

| Token | Meaning |
|-------|---------|
| `0.0s` | Backend latency |
| `#042` | Sample ID (positional; not all datasets assign one) |
| `HIT` / `MISS` | Match against ground truth under active scorer |
| `[ai]📖` | Source tag — cached, fresh LLM call, etc. |
| `-> 'unknown'` | Pipeline output |
| `gt:'disproved'` | Ground truth |
| `q:'...'` | Query (truncated in long runs) |

Mostly ignorable — they reassure you the campaign is moving. When something goes wrong, look here.

## Round summaries

```
ROUND 3 COMPLETE
  Winner:   C4/8 — 62% (+4% vs prev best)
  Layer:    L1     Patience: 0/3
  Queries:  50     Cache hit: 78%

CRITIQUE: ...
NEXT: continue L1
```

| Field | Meaning |
|-------|---------|
| Winner | Best candidate (of N) and improvement over previous best. "No winner" line if nothing beat it. |
| Layer | L1 (normal), L2 (stall recovery — rewrites framing), L3 (L2 stall — rewrites strategy). |
| Patience | Consecutive no-improvement rounds. Hits the configured max → escalates. |
| Queries | Scored count + cache hit %. |
| CRITIQUE | 2–4 lines of optimizer's own analysis. |

## Annotation lines (⚠ / ↳)

When the optimizer finds something notable, it surfaces a two-line annotation:

```
⚠ <fact, in data terms>
  ↳ <action the system is taking>
```

```
⚠ llm_only.model = 'gpt-4o' not in allowed set
  ↳ scored 0; L2 brief will name this value

⚠ candidate degraded on 3/4 queries; eliminated early
  ↳ L2 will steer next round away from this region
```

The optimizer has already handled it — these exist for audit, not to ask for input. A bare `⚠` without `↳` is a bug; report it.

## Where results land

- `campaigns/<cycle_id>/log.md` — rendered per-round digest (status, per-round critique / L2 brief / changes, hard-samples heatmap, final winner). Regenerated every round-complete + finalize.
- `campaigns/<cycle_id>/index.json::final` — structured form: `winner_prompt_fields`, `winner_pipeline_params`, `best_accuracy`, `baseline_accuracy`, `stop_reason`.

## Live state (forks and the family root)

- `dashboard.json` — current phase, round, candidate, accuracies, in-flight query. Full path:
  ```
  .promptpotter/projects/{tenant_id}/campaigns/{root_cycle_id}/dashboard.json
  ```
  The active cycle id is in `.promptpotter/active_session.json`. Open the file directly for live state.
- **Webapp preview** — same `dashboard.json`, rendered in the browser. In a separate terminal, run:
  ```bash
  python -m uvicorn promptpotter.main:app --port 8001
  ```
  then open <http://127.0.0.1:8001/ui/>. The page polls `dashboard.json` every 2 s. Reads `active_session.json` on load — `init` a new cycle ⇒ reload the page. Keep `python -m promptpotter optimize` running in another terminal for live refresh.
- `output.log` — append-only HIT/MISS history, tail-friendly.

Both live at the **root cycle's** dir (the cycle with no `parent_cycle_id`). When you fork, the fork's own dir nests under root at `campaigns/{root_cycle_id}/forks/{cycle_id}/`, but its dashboard / output.log stay at root. One place to tail covers the whole family.

- `dashboard.json::cycle_id` always names the active fork.
- `output.log` gets a `=== FORK <id> from round N (parent: …) ===` banner inline at each cutover.

The fork's own dir holds its per-cycle audit (`index.json`, `log.md`, `rounds/`, `.runtime/`). Open those when you want to inspect what specifically happened in one fork; tail the root for live progress.

## Stopping

A campaign stops when: round limit reached, perfect accuracy, or Ctrl+C. First Ctrl+C finishes the in-flight call and saves; second force-quits.

After it stops: best config in `index.json::final::winner`; per-round digest in `log.md`; live state in `dashboard.json`. Open these directly — there is no read CLI.

## Resuming and rewinding

- `optimize` — resume from latest completed round.
- `optimize --from N` — rewind: archive trials after N, restart from round N's state.
- `optimize --fork-on-divergence` — on scorer divergence, mint a sibling cycle from the divergence point.

Full mechanics: [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md).

Next: [Troubleshooting](05-troubleshooting.md).
