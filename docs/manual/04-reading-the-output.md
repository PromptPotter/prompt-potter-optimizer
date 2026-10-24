# Reading the output

While a campaign runs, three streams tell you what's happening: per-sample lines, round summaries, and annotation lines.

## Lifecycle at a glance

```
init       origin       round 1..N            stop
  │           │              │                    │
  ▼           ▼              ▼                    ▼
prep      score start      generate → score        winner
no LLM    prompt on full   → critique → winner    in index.json
calls     scoring slice    selection              best in log.md
                           (L2 fires on stall;
                            L3 fires on L2 stall)
```

Each round runs four steps in sequence:

| Step | What happens |
|------|--------------|
| 🧪 **Generate** | The optimizer proposes N new candidates, informed by last round's critique. |
| ⚖️ **Score** | Each candidate is scored query-by-query. Inferior candidates can be eliminated early once there's enough statistical evidence — never on fewer than `elimination_n_min` samples. |
| 📝 **Critique** | The optimizer reads the raw results and writes a structured analysis: where it failed, what to try next. |
| 🏆 **Winner** | The optimizer's selector elects the round — PromptPotter's own on difficulty-adjusted ability (θ) against its parent — the candidate this round's were mutated from — with no threshold knob ([`../methods/verdict-resolution.md`](../methods/verdict-resolution.md) § Reading a round). A winner becomes the next parent; otherwise patience ticks up. |

When patience runs out, an **outer loop** steps in to redirect (see [chapter 1 — When the optimizer gets stuck](01-what-is-promptpotter.md#when-the-optimizer-gets-stuck)). Self-healing also fires whenever a candidate produces invalid or broken output — full mechanics in [`../developer/self-healing-internals.md`](../developer/self-healing-internals.md).

## Per-sample lines

```
69.9s #042 MISS [ai] io=86279/5670 c41% -> 'unknown' gt:'disproved' q:'was the hypothesis disproved?'
```

| Token | Meaning |
|-------|---------|
| `69.9s` | Backend latency |
| `#042` | Sample ID (positional; not all datasets assign one) |
| `HIT` / `MISS` | Match against ground truth under active scorer; `MISS 3/8` adds the ground truth's rank among the candidates. `ERR` = pipeline error, `DEPR` = deprecated sample, `UNSC` = the formula could not grade it |
| `[ai]` | Pipeline node the row terminated at (shown on a multi-node pipeline or a replay) |
| `📖` | **We** replayed this from the measurement archive — no backend call, no money |
| `io=86279/5670` | Input / output tokens (`~` prefix = a chars/4 estimate) |
| `c41%` | **The provider** served 41% of the input off its own prompt-prefix cache, at a discount; `c?` = the provider did not report it. Never shown beside `📖`: a replay reached no provider, so it has no discount to report |
| `-> 'unknown'` | Pipeline output |
| `gt:'disproved'` | Ground truth |
| `q:'...'` | Query (truncated in long runs) |

Mostly ignorable — they reassure you the campaign is moving. When something goes wrong, look here.

## Round summaries

A round opens with a rule naming the round and the distance to the next escalation, followed by the `GENERATE` block (the CLI draws these framed; the fields are what matter):

```
ROUND 3/10                                        stall 0/3 → L2

GENERATE
  Parent accuracy 62.0%
  Parent prompt   You are a careful reasoner…
  Candidates      5   Prior critique: from R2
  Model           openai/gpt-oss-20b
```

Scoring opens under a `MEASURE` rule naming the manifest's measurement node. The election says
only who it picked — `✓ selected C3.1` — and the round closes with the verdict, printed once as
one block:

```
  SCOREBOARD
  #   Label    Cells   Accuracy             95% CI   Composite   Ability θ     Delta
  1   C3.1     50/50      74.0%     [61.0%, 84.0%]      0.7400      +0.412    +12.0%  *
  2   C3.2     50/50      71.0%     [57.0%, 82.0%]      0.7100      +0.287     +9.0%
  3   C3.3     14/50      57.1%     [32.0%, 79.0%]      0.5710         ---       ---  (eliminated)
  ✓ SELECTED C3.1  74.0% (vs reference 62.0%, +12.0%)
  why: …
  lift vs reference: +0.120 [+0.031, +0.209] on 50 shared  |  clears 0
  overlap (origin panel of 50: C0 62.0% (n=50)  →  C3.1 74.0% (n=50))
```

| Line | Meaning |
|------|---------|
| `ROUND 3/10` | Round number and ceiling. In lives mode the ceiling is replaced by a ♥ bank. |
| `stall 0/3 → L2` | Rounds of no improvement, and how many trigger [L2](../concepts/the-loop.md). Reads `L2 every round` when patience is 0. |
| `Prior critique` | Whether last round produced one — the input this round's candidates were built from. |
| `SCOREBOARD` | One row per candidate, the selected arm marked `*`, an arm cut short tagged with how it ended; `Ability θ` reads `---` where the optimizer's selector fits none for that arm. **Read `Cells` first**: it is the cells the arm answered of the cells the round asked of it, and every rate to its right is read over it — so an arm cut at `14/50` is not comparable to one at `50/50`. |
| `lift vs reference` | The selected arm's lift with its interval, on the cells it shares with its reference. `spans 0` means the round did not separate the arm from its reference, whatever the verdict line's point estimate reads. |
| `overlap` | The best-so-far line (C0, then each new best) on the origin panel's cells all of it has answered — the one line two rounds can be differenced on. |
| the verdict | `✓ SELECTED <label>` or `· HELD` (the best-so-far stands). Its headline reads `θ` instead of accuracy where the run displays ability on a fitted ruler. The accuracy on it never decided the round — the optimizer's selector did — so a `why:` line beneath states its reason whichever way it went. A `⚠ θ caveat` line under it names a round or arm whose θ is not comparable ([`../methods/verdict-resolution.md`](../methods/verdict-resolution.md) § Reading a round). |
| `(vs reference 62.0%, +12.0%)` | The **matched-pair** reference — the individual this arm's lift is read against (`lift_reference`) — restricted to the samples the arm actually measured. An arm that stopped before covering its reference's panel gets no such rate, because subtracting a full-set rate from a prefix would publish lift nobody measured. |

Candidate labels are `C0` for the origin and `C{round}.{n}` after it — so `C3.2` is the second candidate of round 3.

## Annotation lines (⚠ / ↳)

When the optimizer finds something notable, it surfaces a two-line annotation:

```
⚠ <fact, in data terms>
  ↳ <action the system is taking>
```

```
⚠ llm_only.model = 'gpt-4o'  ∉ [openai/gpt-oss-20b, openai/gpt-oss-120b, deepseek/deepseek-v4-flash (+5)]
  ↳ scored 0 (no backend call); the next l1_generate reads it in l1_wounds
```

A candidate cut mid-scoring says which mechanism cut it, and both forms start `✂`:

```
✂ eliminated q18/28  p_best=3.2% < eps=15%  vs C3.2 (of 4 priors)
✂ answer collapsed q6/28  one label for every sample — no measurement of ability to score
✂ broken (degradation) q3/4  75% degraded  (llm_only:reasoning_budget_exhausted)
```

The first two are [PoBB](../methods/candidate-elimination.md) and mean opposite things: `eliminated` ran out of evidence — the posterior probability of being best fell below the abort threshold, so the remaining samples were not worth buying — while `answer collapsed` is a verdict, an arm that stopped answering the question. It quotes no posterior because it computed none. The third is the degradation check: the pipeline itself was breaking, and the last field is the dominant `{node}:{code}` ([Troubleshooting § Degradation warnings](05-troubleshooting.md#degradation-warnings-on-many-candidates)). A `✓ leader locked` line is PoBB stopping for the opposite reason.

The optimizer has already handled it — these exist for audit, not to ask for input. A bare `⚠` without `↳` is a bug; report it. Full mechanics behind these annotations: [`../developer/self-healing-internals.md`](../developer/self-healing-internals.md).

## Live state ([forks and the family root](../operations/persistence-and-state.md))

- **Webapp preview** — in a separate terminal, run:
  ```bash
  python -X utf8 -m uvicorn promptpotter.main:app --port 8001
  ```
  then open <http://127.0.0.1:8001/>. Keep `python -m promptpotter resume` running in another terminal for live refresh.
- For a headless tail of the live run readout (per-sample HIT/MISS, round summaries), read the cycle's `readout.log` — written by every launch, ANSI-stripped, the path printed as `Readout:` at a terminal launch and listed in the browser's Files tree. The gitignored `logs/latest-readout-path.txt` names the newest terminal launch's.

Full on-disk shape — the exact `dashboard.json` / `active_session.json` paths, fork-directory layout: [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md).

### It moves the pipeline, not just the words

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/runcard-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/runcard-light.png">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/runcard-dark.png" alt="The run card in the chat thread: trend per round, spend, which prompt fields and node parameters the best candidate changed, and how many samples flipped against the origin" width="100%">
  </picture>
</p>

Model, temperature, node thresholds, the output schema — whatever your `pipeline.yaml` declares tunable — are part of the individual, so they mutate and are selected jointly with the prompt fields. The run card says which prompt fields **and which node parameters** the best candidate changed, what it cost, and — on the cells both were measured on — how many samples it now gets right, and wrong, against the origin.

### The budget goes where candidates separate

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/leaderboard-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/leaderboard-light.png">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/leaderboard-dark.png" alt="The hard-sample leaderboard: samples ranked by information gain, each with its fitted difficulty and how often it has been graded" width="100%">
  </picture>
</p>

Samples everyone aces or everyone fails are noise, and paying for them buys nothing. The hard-sample leaderboard orders the dataset by how much each sample tells you about which candidate is better, with a fitted difficulty δ beside it, and scoring goes preferentially to those. Measurements are content-addressed: a configuration already scored on a sample is replayed (`📖`), never re-bought — and the replay still shows its full cost on the ledger.

<table>
<tr>
<td width="50%" valign="top">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/cell-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/cell-light.png">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/cell-dark.png" alt="One measured cell opened: prediction, ground truth, the exact prompt sent to the node, and whether it was replayed from the archive" width="100%">
  </picture>
  <br>
  <b>Every number opens down to the call.</b> One cell = one candidate on one sample: prediction, ground truth, the exact input each node received, tokens, seconds — and whether it was paid for or replayed from the archive.
</td>
<td width="50%" valign="top">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/candidates-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/candidates-light.png">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/candidates-dark.png" alt="The candidates chart: every candidate of the campaign with accuracy, ability and overlap, intervals drawn, and a served caveat above it" width="100%">
  </picture>
  <br>
  <b>Every candidate, with its interval.</b> Accuracy, ability θ and the shared-cell overlap for each individual across all generations, with the lineage beneath. The caveat above the chart is served by the backend, not decided in the browser.
</td>
</tr>
</table>

## Where results land

Best config, provenance, and per-round digest all live under the campaign's directory. Full file-by-file reference: [`../operations/persistence-and-state.md § File reference`](../operations/persistence-and-state.md#file-reference).

## Stopping

A campaign stops when: round limit reached, perfect accuracy, or Ctrl+C. First Ctrl+C pauses: it saves everything already banked and exits 130, cancelling a sent call only where cancelling stops what it bills (`Connector.cancel_stops_billing`) and otherwise letting it land; second force-quits.

After it **finishes**: the optimizer's declared pick in `index.json::final` (`result_round` / `result_prompt_fields` / `result_pipeline_params`); the same winner with its provenance, in the shape another program reads, in `export.json` (`python -m promptpotter export --tier package` wraps it as a skill another agent opens); per-round digest in `log.md`; live state in `dashboard.json`. The headline — the score on the held-out bench set — stays empty, its `status` reading `not_asked`, until you run `python -m promptpotter bench` (`bench_trigger: manual`, the default). Open these directly; the read verbs are for what no file holds — `evidence` (a comparison ACROSS subjects), `cycles` (which runs are live), `machine-status` (the queue) and `trail` (what an account did). Ctrl+C is a pause, not a finish: the cycle is declared `paused`, never `terminal`, which is what keeps it resumable.

## Resuming and rewinding

- `resume` — resume from latest completed round.
- `resume --from N` — rewind: archive trials after N, restart from round N's state.
- `resume --fork-on-divergence` — on scorer divergence, mint a sibling cycle from the divergence point.

Full mechanics: [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md).

Next: [Troubleshooting](05-troubleshooting.md).
