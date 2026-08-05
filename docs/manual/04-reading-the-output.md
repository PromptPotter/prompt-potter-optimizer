# Reading the output

While a campaign runs, three streams tell you what's happening: per-sample lines, round summaries, and annotation lines.

## Lifecycle at a glance

```
init       origin       round 1..N            stop
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
| 🧪 **Generate** | The optimizer proposes N new candidates, informed by last round's critique. |
| ⚖️ **Evaluate** | Each candidate is scored query-by-query. Inferior candidates can be eliminated early once there's enough statistical evidence (~4+ samples). |
| 📝 **Critique** | The optimizer reads the raw results and writes a structured analysis: where it failed, what to try next. |
| 🏆 **Winner** | Round's best beats the current best by ≥ improvement threshold → new best. Otherwise patience ticks up. |

When patience runs out, an **outer loop** steps in to redirect (see [chapter 1 — When the optimizer gets stuck](01-what-is-promptpotter.md#when-the-optimizer-gets-stuck)). Self-healing also fires whenever a candidate produces invalid or broken output — full mechanics in [`../developer/self-healing-internals.md`](../developer/self-healing-internals.md).

## Per-sample lines

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
| Layer | [L1](../concepts/the-loop.md) (normal), L2 (stall recovery — rewrites framing), L3 (L2 stall — rewrites strategy). |
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

The optimizer has already handled it — these exist for audit, not to ask for input. A bare `⚠` without `↳` is a bug; report it. Full mechanics behind these annotations: [`../developer/self-healing-internals.md`](../developer/self-healing-internals.md).

## Live state ([forks and the family root](../concepts/campaign-tree.md))

- `dashboard.json` — current phase, round, candidate, accuracies, in-flight query. Full path:
  ```
  .promptpotter/projects/{tenant_id}/campaigns/{campaign_id}/cycles/{cycle_id}/dashboard.json
  ```
  The active campaign + cycle ids are in `.promptpotter/active_session.json`. Open the file directly for live state.
- **Webapp preview** — same `dashboard.json`, rendered in the browser. In a separate terminal, run:
  ```bash
  python -m uvicorn promptpotter.main:app --port 8001
  ```
  then open <http://127.0.0.1:8001/>. The page polls `dashboard.json` every 2 s. Reads `active_session.json` on load — `new` a fresh cycle ⇒ reload the page. Keep `python -m promptpotter resume` running in another terminal for live refresh.
- For a headless tail of the live run readout (per-sample HIT/MISS, round summaries), read the gitignored `.goldmine/latest.log` — the most-recent run's stdout, ANSI-stripped.

`dashboard.json` lives in the cycle's **own** dir. When you fork, the fork's dir is flat alongside its root at `campaigns/{campaign_id}/cycles/{fork_cycle_id}/`, and its `dashboard.json` lives there too — each cycle owns its stream. Tail the cycle you're actually running.

- `dashboard.json::cycle_id` stamps the cycle that owns the file.

The fork's own dir holds its per-cycle audit (`index.json`, `log.md`, `rounds/`, `.runtime/`). Open those when you want to inspect what specifically happened in one fork; tail the root for live progress.

## Where results land

- `campaigns/<cycle_id>/log.md` — rendered per-round digest (status, per-round critique / L2 brief / changes, hard-samples heatmap, final winner). Regenerated at every round-complete — so a run stopped mid-round shows the last
  round that closed, not the partial one.
- `campaigns/<cycle_id>/index.json::final` — structured form: `winner_prompt_fields`, `winner_pipeline_params`, `stop_reason`, `mode`. The cycle's best score lives top-level (`index.json::best_accuracy` / `best_round`), not inside `final`.

## Stopping

A campaign stops when: round limit reached, perfect accuracy, or Ctrl+C. First Ctrl+C cancels the in-flight call, saves everything already banked, and exits 130; second force-quits.

After it **finishes**: best config in `index.json::final` (`winner_prompt_fields` / `winner_pipeline_params`); per-round digest in `log.md`; live state in `dashboard.json`. Open these directly — there is no read CLI. Ctrl+C is a pause, not a finish: it writes no `final` and no `finished_at`, which is what keeps the cycle resumable.

## Resuming and rewinding

- `resume` — resume from latest completed round.
- `resume --from N` — rewind: archive trials after N, restart from round N's state.
- `resume --fork-on-divergence` — on scorer divergence, mint a sibling cycle from the divergence point.

Full mechanics: [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md).

Next: [Troubleshooting](05-troubleshooting.md).
