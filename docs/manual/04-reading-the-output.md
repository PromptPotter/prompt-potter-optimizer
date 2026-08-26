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

A round opens with a rule naming the round and the distance to the next escalation, followed by the `GENERATE` block (the CLI draws these framed; the fields are what matter):

```
ROUND 3/10                                        stall 0/3 → L2

GENERATE
  Current best    62.0%
  Parent prompt   You are a careful reasoner…
  Candidates      5   Prior critique: from R2
  Model           openai/gpt-oss-20b
```

It closes with a scoreboard, one verdict line, and the critique:

```
  Scoreboard: C3.1=74.0% | C3.2=71.0% | C3.3=68.0%
  ✓ IMPROVED  74.0% (was 62.0%, +12.0%)  p=0.003 **  ->  next: continue
  L1 Critique: …
```

| Line | Meaning |
|------|---------|
| `ROUND 3/10` | Round number and ceiling. In lives mode the ceiling is replaced by a ♥ bank. |
| `stall 0/3 → L2` | Rounds of no improvement, and how many trigger [L2](../concepts/the-loop.md). Reads `L2 every round` when patience is 0. |
| `Prior critique` | Whether last round produced one — the input this round's candidates were built from. |
| `Scoreboard` | Each candidate's accuracy. Above three candidates this becomes a full box adding composite fitness, 95% CI and delta, with the winner marked `*`. |
| the verdict | One of `✓ IMPROVED`, `✗ NOT PROMOTED` (a positive delta blocked by significance or the sample floor — the reason is named inline) or `⚠ NO IMPROVEMENT`. |
| `(was 62.0%, +12.0%)` | The **matched-pair** origin: the origin restricted to the samples this winner actually measured. A winner that stopped before covering the panel gets no such clause, because subtracting the full-set origin from a prefix would publish lift nobody measured. |
| `p=0.003 **` | Significance of the improvement; the stars are the band. |
| `L1 Critique:` | The optimizer's own analysis, flattened to a single line. |

Candidate labels are `C0` for the origin and `C{round}.{n}` after it — so `C3.2` is the second candidate of round 3.

## Annotation lines (⚠ / ↳)

When the optimizer finds something notable, it surfaces a two-line annotation:

```
⚠ <fact, in data terms>
  ↳ <action the system is taking>
```

```
⚠ llm_only.model = 'gpt-4o'  ∉ [openai/gpt-oss-20b, groq/llama-3.3-70b, … (+5)]
  ↳ scored 0 (no backend call); L2 brief will name this value
```

A candidate cut mid-scoring says which mechanism cut it, and both forms start `✂`:

```
✂ eliminated q18/28  p_best=3.2% < eps=15%  vs C3.2 (of 4 priors)
✂ answer collapsed q6/28  one label for every sample — no measurement of ability to score
✂ degradation q3/4  75% degraded  (empty_output)
```

The first two are [PoBB](../methods/candidate-elimination.md) and mean opposite things: `eliminated` ran out of evidence — the posterior probability of being best fell below the abort threshold, so the remaining samples were not worth buying — while `answer collapsed` is a verdict, an arm that stopped answering the question. It quotes no posterior because it computed none. The third is the degradation check: the pipeline itself was breaking. A `✓ leader locked` line is PoBB stopping for the opposite reason.

The optimizer has already handled it — these exist for audit, not to ask for input. A bare `⚠` without `↳` is a bug; report it. Full mechanics behind these annotations: [`../developer/self-healing-internals.md`](../developer/self-healing-internals.md).

## Live state ([forks and the family root](../concepts/campaign-tree.md))

- **Webapp preview** — in a separate terminal, run:
  ```bash
  python -m uvicorn promptpotter.main:app --port 8001
  ```
  then open <http://127.0.0.1:8001/>. Keep `python -m promptpotter resume` running in another terminal for live refresh.
- For a headless tail of the live run readout (per-sample HIT/MISS, round summaries), read the gitignored `logs/latest.log` — the most-recent run's stdout, ANSI-stripped.

Full on-disk shape — the exact `dashboard.json` / `active_session.json` paths, fork-directory layout: [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md).

## Where results land

Best config, provenance, and per-round digest all live under the campaign's directory. Full file-by-file reference: [`../operations/persistence-and-state.md § File reference`](../operations/persistence-and-state.md#file-reference).

## Stopping

A campaign stops when: round limit reached, perfect accuracy, or Ctrl+C. First Ctrl+C cancels the in-flight call, saves everything already banked, and exits 130; second force-quits.

After it **finishes**: best config in `index.json::final` (`winner_prompt_fields` / `winner_pipeline_params`); the same winner with its provenance, in the shape another program reads, in `export.json`; per-round digest in `log.md`; live state in `dashboard.json`. Open these directly; `evidence` is the one read VERB, because a comparison ACROSS campaigns is in no single file. Ctrl+C is a pause, not a finish: it writes no `final` and no `finished_at`, which is what keeps the cycle resumable.

## Resuming and rewinding

- `resume` — resume from latest completed round.
- `resume --from N` — rewind: archive trials after N, restart from round N's state.
- `resume --fork-on-divergence` — on scorer divergence, mint a sibling cycle from the divergence point.

Full mechanics: [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md).

Next: [Troubleshooting](05-troubleshooting.md).
