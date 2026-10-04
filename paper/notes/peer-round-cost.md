# Peer round cost — an observation, not yet a result

Head-to-head `h2h-1003`, `justlogic-d234`, 2026-10-03. Four arms, one instrument (500 search
rows, 100 held-out bench rows, target `openai/gpt-oss-20b:nitro`), one declared budget of $0.30
per arm. Every peer runs at its paper configuration. **One run, no seeds: this is a lead for the
results section, not supplementary data.**

## What was seen

**A peer's first round does not fit a small budget; potter's rounds do.** The unit of spend a
method needs before it can elect anything differs by an order of magnitude:

| Optimizer | Round 1 at paper config | Cells before the first election | Rounds closed by $0.10 incurred |
|---|---|---|---|
| potter | 3 candidates on 34 cells, eliminated early | ~100 | 5 (stopped on `max_rounds`) |
| CAPO | 14 candidates racing on up to 300 cells | up to 4,200 (the run's preflight prices it at $0.51) | 0 |
| LEVI | 4 seeds scored on the whole search pool | 2,000 | 0 |
| GEPA | 1 child on a 3-cell minibatch, then the Pareto set | 3 to 253 | 1 |

So below roughly one peer round of budget, a peer returns its origin: there is no intermediate
answer to grade. Potter returned a selection at $0.097 incurred.

**Potter's reading at that spend** (bench column, 100 held-out rows, composite fitness): origin
0.541 → selected 0.575, lift +0.034, CI [-0.027, +0.094]. **Not separable from zero** — quote it
as "a selection existed", never as a gain.

## Why this is a property of the methods, and what is ours

- CAPO's population and LEVI's calibration pass are the papers' own designs; the cost is theirs.
- GEPA's minibatch gate is 3 cells (App. E.4). On a target that sits near 40% accuracy a 3-cell
  gate is close to a coin flip, so a child is rejected on noise.

## Confounds to remove or declare before this is a claim

`h2h-1003` carried three handicaps that were our wiring and not the peers' methods, so none of its
numbers compares the methods. The wiring is fixed; `h2h-1004` is the first run under it.

1. **A peer wrote one field.** It could append to the origin's prompt and never retract it, and
   GEPA's first reflection was shown an empty instruction. A peer now reads and replaces the whole
   prompt (`paper_templates.py::prompt_text` / `rewritten`).
2. **CAPO ran without shots**, because the dataset held out no demo pool. It now holds out 50
   rows, which opens the same surface to potter and shrinks the search pool to 450.
3. **Optimizer models differed by preset** — paper-faithful, and a confound for "whose method is
   better". `h2h-1004` runs every optimizer call on `openai/gpt-6-luna`, a declared deviation
   from the LEVI and GEPA presets; LEVI's paradigm-shift node takes `max_tokens` 8000, that
   model's reasoning floor.

## `h2h-1004` — the same wiring at $0.50, and two more of ours

Bench column, 100 held-out rows, composite fitness; origin 0.541 for every arm.

| Optimizer | Selected at | Bench | Lift, 95% CI | Stopped on | Billed |
|---|---|---|---|---|---|
| GEPA | round 10 | 0.716 | +0.175 [+0.094, +0.256] | `spend_budget` | $0.483 |
| LEVI | round 2 | 0.707 | +0.166 [+0.075, +0.257] | `spend_budget` | $0.374 |
| potter | round 8 | 0.674 | +0.133 [+0.050, +0.216] | `optimizer_abort` | $0.237 |
| CAPO | round 1 | 0.632 | +0.091 [+0.016, +0.166] | `spend_budget` | $0.463 |

**Every lift excludes zero and every pair of intervals overlaps: no arm is separated from
another.** And two arms did not spend the budget they were given, both through our code:

- **Potter aborted at half its budget.** A retry that recovered was counted as a failed node, so a
  round whose every cell answered read as evidence-starved and the optimizer ended the run. A
  warning now counts only where its node did not finish (`results_health.py`).
- **LEVI stopped with about a fifth of its budget stranded.** Its refinements went out together,
  the first refusal cancelled the rest mid-flight, and a cancelled send is held at its full bound.
  A peer's fan-out now lands every send before it raises one's refusal
  (`paper_templates.py::ask_each`); CAPO's two fan-outs ride the same helper.

In accuracy, the column the bench now headlines (origin 0.430): GEPA 0.690, LEVI 0.670, potter
0.650, CAPO 0.560 — the same order, and no pair separated after Holm.

So `h2h-1004` ranks nothing either.

## `h2h-1005` — both fixes live, and three things it showed

Same instrument and budget. Bench column, 100 held-out rows, composite fitness; origin 0.541.

| Optimizer | Selected at | Bench | Lift, 95% CI | Stopped on | Billed | Incurred |
|---|---|---|---|---|---|---|
| GEPA | round 8 | 0.692 | +0.151 [+0.067, +0.235] | `spend_budget` | $0.479 | $0.517 |
| LEVI | round 2 | 0.661 | +0.120 [+0.042, +0.197] | `spend_budget` | $0.363 | $0.420 |
| potter | round 10 | 0.660 | +0.118 [+0.037, +0.200] | `converged` | $0.347 | $0.597 |
| CAPO | none | — | — | `spend_budget` | $0.479 | $0.496 |

The same passes in accuracy, the column the bench now headlines; origin 0.430 [0.332, 0.528].

| Optimizer | Bench accuracy | Lift, 95% CI | Paired against potter, 95% CI |
|---|---|---|---|
| GEPA | 0.650 | +0.220 [+0.112, +0.328] | +0.020 [−0.082, +0.122] |
| potter | 0.630 | +0.200 [+0.094, +0.306] | — |
| LEVI | 0.610 | +0.180 [+0.081, +0.279] | −0.020 [−0.113, +0.073] |
| CAPO | — | — | — |

**Again every interval overlaps and every paired interval spans zero: no arm is separated from
another**, and against `h2h-1004` each arm's bench moved by a few hundredths with no change to its
method — one run's order is noise. The read itself now withholds a verdict here: an arm of the
declared comparison is ungraded.

- **CAPO selected nothing.** The money ran out in the last block of round 1 with 11 of 12 arms
  still racing; a round the budget cuts is unwound, so no election exists to grade. In `h2h-1004`
  the same round closed at $0.463. This is item 4 below, seen from its other side: at this budget
  CAPO's result is whether its first round happens to finish.
- **LEVI still stops short.** Its refinements go out together and each is held at its bound, about
  ten times what it bills, so one is refused with $0.116 held for its siblings. A refused send is
  now asked again once they have landed (`paper_templates.py::ask_each`). Exercised in `h2h-1006`.
- **Potter stopped itself.** An arm's ceiling meters what its search incurred, replays priced, so
  the cells potter replayed from `h2h-1004` counted: $0.449 of search under the $0.50 ceiling, the
  rest of its $0.597 being the bench pass, which every arm runs outside the ceiling. It ended on
  its own patience rule with about a tenth of the ceiling unspent, where each peer ran to the
  refusal. To remove before potter's row sits beside the others: its arm is minted with the
  patience off (`--set nodes.escalation.l3_patience=null`), a declared deviation from the
  dataset's own setting.

- **Potter searched an axis no peer holds.** The dataset opens `temperature`, `max_tokens` and
  `reasoning_effort` beside the prompt, and a peer writes the prompt alone. From round 8 potter's
  candidates carried `reasoning_effort: high`: its selection reasons for about 5,000 tokens a cell
  where the origin and both peers' selections sit under 500, and its bench pass cost about seven
  times theirs. So its row measures a prompt and a paid reasoning rung together, and "the only
  difference is the optimizer" does not hold. Removed: an arm of a head-to-head now searches the
  prompt's fields alone, every other axis held at its origin value (`mint.py::_prompt_axes_only`).
  Exercised in `h2h-1006`.

Still standing, to declare beside any claim:

4. **The budget is under one CAPO round** (preflight prices it at $0.53), so CAPO is graded on
   whatever its race had elected when the money ran out.
5. **The target answers "Uncertain" about three times in four** whatever the prompt, so every
   candidate sits in a 33–50% band and a round's winner is often inside the noise.
6. **Round-level accuracy is read on moving subsets** — only the bench column and the `overlap`
   line compare like with like.
7. **A reply broken mid-read is held at the cell's whole bound**, about a hundred times what the
   cell bills, because the backend may have billed it. One such cell took about 3% of an arm's
   budget in `h2h-1004`; it lands on arms at random.
8. **One benchmark.** A three-label logic task at a low-accuracy target is one setting; a peer
   that looks weak here is not shown weak on the tasks its paper reports.

## Why a peer costs more, read off the ledgers of `h2h-1004` and `h2h-1005`

**Every arm pays the same per cell** ($0.00015–0.00019 billed). The gap is cells bought before an
election, and the optimizer's own calls are noise beside it (0.4% of CAPO's billed search, 6–7%
of the others').

| Optimizer | Fresh cells | Elections | Where the cells went |
|---|---|---|---|
| potter (`h2h-1004`, its only fresh run) | 704 | 8 | 34-cell subsets, arms cut early |
| GEPA | ~2,100 | 4 in 19 rounds | 57% on children never elected; a full Pareto pass is ~230 cells |
| LEVI | 2,251 before the first election | 2 | calibration on the whole pool |
| CAPO | 3,108 | 0 | one round; 11 of 14 arms still racing at 270 cells |

**Potter's `h2h-1005` row is not a like-for-like spend.** Its rounds 1–8 replayed `h2h-1004`
(744 cells, 24 optimizer calls, nothing billed), so "potter answers at $0.30" rests on one fresh
run.

**What was ours, not the method's:**

- **LEVI calibrated on the whole 450-row pool.** The paper calibrates on a 150-problem discovery
  set (App. I). Fixed: `proxy_css.discovery`, 150 in the preset. Exercised in `h2h-1006`.
- **GEPA's Pareto set was half the pool** (225 rows), a share the paper leaves unstated and we
  chose. Its own runs and DSPy's guidance state a row count near 50. Fixed:
  `minibatch.pareto_size`, 50 in the preset, and the parent is re-scored on the Pareto cells
  alone. Exercised in `h2h-1006`.
- **CAPO's incumbent was re-scored on the whole panel** in round 1, cells no election of its
  reads. Fixed: the selector names the cells it reads the parent on (`Selector.parent_cells`).
- **CAPO is faithful knob for knob.** Its first step is the method's cost, which its paper states.
  It can be sized to the budget only inside the ranges its ablations report (population, block
  count), and the write-up then says "sized to budget per [citation]", never "paper configuration".

## Why $0.30–0.50 separates nothing

**The instrument, not the methods.** A 100-row bench resolves a gap of about 0.10; the arms differ
by 0.02–0.04, and the same arm moves 0.02–0.06 between two runs. Only 35 of the 100 rows
discriminate at all: every selection gets the Uncertain rows right, as the origin does. Separating
0.05 at 80% power takes about 800 bench rows for one pair of selections, and about six seeds per
arm before it is a claim about a method rather than about one run.

The source holds 1,500 unused rows at these depths. Keeping this dataset's search and demo pools
exactly, the bench reaches 339 rows; beyond that the pools move or bench membership has to be
declared rather than hash-ranked.

## `h2h-1006` — a bug-hunt run, not a comparison

`justlogic-d234-held` (search 450, demo 50, bench 1,000), four arms, one seed. It never reached a
final bench pass, so it carries **no result**; what it holds is the state each arm was paused in.

| Optimizer | Search incurred | Billed | Rounds closed | Sized to budget |
|---|---|---|---|---|
| potter | $0.236 | $0.262 | 15 | `l3_patience` off |
| LEVI | $0.256 | $0.260 | 5 | paradigm-shift interval 5, two sampler temperatures |
| CAPO | $0.222 | $0.244 | 3 | population 6, 4 blocks (its Table 12 ranges) |
| GEPA | $0.082 | $0.083 | 26 | preset |

Search incurred is the column the ceiling meters; billed is the provider's.

**Four things disqualify it as a comparison, each ours:**

1. **The ceiling moved mid-run**, $0.50 to $0.30, by pause and `set-limits`.
2. **The code moved mid-run.** GEPA crashed on a δ-ruler hole (a child read on cells its parent
   skipped that round) and resumed; the root fix then changed how a round links new cells onto the
   ruler, so rounds after it would read θ on a scale the earlier rounds did not.
3. **The arms do not share an optimizer model** — each preset pins its own, so lift and dollars
   carry the model as well as the method. Which reading the bench reports is the operator's call.
4. **A changed phase view stranded all four paused cycles** — none resumes on the current code.

**What it did show.** GEPA closed 26 rounds on a third of the others' search spend once its Pareto
set was a row count, CAPO closed rounds at all (3, against none in `h2h-1004`), and LEVI reached
elections inside the budget — the three sizing fixes are now exercised live. Errored cells stayed
rare (potter 4, CAPO 10, LEVI 5, GEPA 1) and are not yet traced.

## What would make it a result

Each arm bug-free at one equal budget, each peer sized to that budget inside its paper's own
ranges, on a bench large enough to resolve the gap, then seeds; the claim then rests on the bench
column with its interval, and on budget-to-first-election as its own reported dimension.
