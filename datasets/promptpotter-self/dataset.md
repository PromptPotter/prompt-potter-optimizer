# PromptPotter-self — Optimizer-of-the-Optimizer

## What this is

A self-referential dataset: outer PromptPotter optimizes the **optimizer prompts**
that drive the inner PromptPotter cycle. Connector boundary:
`promptpotter/connectors/promptpotter.py`. Spec:
[`docs/specs/l4-outer-loop.md`](../../docs/specs/l4-outer-loop.md).
Concept: [`docs/concepts/optimizer-of-the-optimizer.md`](../../docs/concepts/optimizer-of-the-optimizer.md).

Each outer "sample" is one entry in `inner_tasks.yaml`: it mints and runs a full
inner PromptPotter campaign on **`justlogic`** (high-depth logic reasoning — chosen
because the inner model is far from what prompting can get out of it there, so the
inner loop has room to climb and outer candidates score differently. No target
score is declared: the panel says what an inner cycle may SPEND, never what it is
expected to REACH). Each inner run reports a vector of proxy
measurement (`domain/l4/proxies.py::OuterSampleProxies`): `mean_round_delta`, the mean
over rounds of the ability the inner search ADOPTED, minus its origin, in logits on one
difficulty-adjusted ruler. It averages that staircase rather than reading its last step
because the mean measured a 26% quieter instrument on the banked 39-cell panel while
ranking the same arms (r = +0.941) — and because it rewards lifting early.

The outer scoring formula in `campaign.yaml::scoring` re-anchors that one term into
[0,1] and nothing else. It used to compose four factors over eight emitted proxies;
a 39-cell panel then measured each, and only this one discriminated between
optimizer prompts — the quality terms carried more SEED variance than arm variance while
holding authority over the ordering. The rationale lives on `OuterSampleProxies`.

## Status

The recursion is SHIPPED & live-validated; status + remaining work live in ONE
place — [`docs/specs/l4-outer-loop.md`](../../docs/specs/l4-outer-loop.md)
§ Finish line.

## Files

Knob values live in the files, not here — read them there.

- `pipeline.yaml` — optimizer prompt node schema (`l1_generate` / `l1_critique` /
  `l2_context` / `l3_plan`) + the outer optimizer's model/provider.
- `campaign.yaml` — outer campaign config: the composite scoring formula,
  `optimizer_set: "self_optimizing"`, the USD budget (`token_budget` is `null` on purpose —
  see Cost shape).
- `inner_tasks.yaml` — the outer "samples": the seed-pinned `justlogic` bank +
  the `inner_benchmark_config` ladder (a REQUIRED file; all four ladder keys
  must be present or the inner cycle is unscoreable).
- `task_description.md` / `task_context.yaml` — outer L1 framing.
- The outer optimizer prompt *rewrites* live in `promptpotter/assets/optimizer/sets/self_optimizing.yaml`
  (selected by `optimizer_set: "self_optimizing"`), not in a local `prompts/` dir.

## Run

```
python -m promptpotter new promptpotter-self
```

Actively supervise every run (2-minute ticks; never fire-and-wait) — see
[`docs/specs/l4-outer-loop.md`](../../docs/specs/l4-outer-loop.md)
§ Running & supervising.

## Cost shape

Cost is **geometric, not additive** — the factors multiply. One outer round,
worst case before PoBB elimination prunes:

```
inner campaigns/round = (1 origin + n_variants) × n_inner_tasks
inner rounds/campaign ≤ max_inner_rounds   (the lives brake may stop sooner)
```

(plug in the current values from `campaign.yaml` + `inner_tasks.yaml`). The
origin is measured once and cached across outer rounds, so steady state after
round 0 is `n_variants × n_inner_tasks` fresh inner campaigns/round, and PoBB
elimination prunes trailing candidates before all tasks complete — actual <
worst case. The **USD budget governs** (`spend_budget_usd`); `token_budget` is
`null` on purpose — the inner-spend rollup counts real inner tokens onto the
outer ledger, so a token cap would trip after a couple of inner campaigns while
USD budget remains (see l4-outer-loop § Live-run learnings).

**An absolute dollar total is not quotable yet.** How many inner rounds each
campaign actually runs depends on the `justlogic` inner origin→target gap, and that
origin is currently **unmeasured** on this engine (the prior reading is void —
pre-2026-07-10-reset and pre-seed-determinism). Re-measure it (`noise-floor --k 3`)
before sizing a real run.

**Do not thin below the floor.** The θ winner-election needs **≥6 inner tasks** to
crown a winner on signal rather than noise (l4-outer-loop § Live-run learnings), and
the shipped task count sits just above that — cut samples or rounds to save money,
not tasks.
