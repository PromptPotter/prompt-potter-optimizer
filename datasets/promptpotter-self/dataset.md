# PromptPotter-self — Optimizer-of-the-Optimizer

## What this is

A self-referential dataset: outer PromptPotter optimizes the **meta-prompts**
that drive the inner PromptPotter cycle. Connector boundary:
`promptpotter/connectors/promptpotter.py`. Spec:
[`docs/specs/l4-outer-loop.md`](../../docs/specs/l4-outer-loop.md).
Concept: [`docs/concepts/optimizer-of-the-optimizer.md`](../../docs/concepts/optimizer-of-the-optimizer.md).

Each outer "sample" is one entry in `inner_tasks.json`: it mints and runs a full
inner PromptPotter campaign on **`justlogic`** (high-depth logic reasoning — chosen
because the inner model is far from what prompting can get out of it there, so the
inner loop has room to climb and outer candidates score differently. No target
score is declared: the panel says what an inner cycle may SPEND, never what it is
expected to REACH). Each inner run reports a vector of proxy
measurements (`domain/l4/proxies.py::OuterSampleProxies`) — among them
`after_N_rounds_delta` (how far the inner search climbed above where it started, on
one difficulty-adjusted ability ruler), `first_round_delta`, `delta_per_dollar`, and
the `cleanliness` / `diversity_health` health terms.

The outer scoring formula in `campaign.json::scoring` composes a weighted subset
(currently `after_N_rounds_delta` alone as the lift core — `first_round_delta` is held
out as largely collinear with it — gated by `cleanliness × diversity` and scaled by
`delta_per_dollar`). Operators iterate on the formula as evidence
accumulates — there's no single "right" weighting; the proxies serve different
stages of the development → calibration → publication arc.

## Status

The recursion is SHIPPED & live-validated; status + remaining work live in ONE
place — [`docs/specs/l4-outer-loop.md`](../../docs/specs/l4-outer-loop.md)
§ Finish line.

## Files

Knob values live in the files, not here — read them there.

- `pipeline.json` — meta-prompt node schema (`l1_generate` / `l1_critique` /
  `l2_context` / `l3_plan`) + the outer optimizer's model/provider.
- `campaign.json` — outer campaign config: the composite scoring formula,
  `optimizer_set: "meta"`, the USD budget (`token_budget` is `null` on purpose —
  see Cost shape).
- `inner_tasks.json` — the outer "samples": the seed-pinned `justlogic` bank +
  the `inner_benchmark_config` ladder (a REQUIRED file; all four ladder keys
  must be present or the inner cycle is unscoreable).
- `task_description.md` / `task_context.json` — outer L1 framing.
- The outer meta-prompt *rewrites* live in `datasets/_optimizer_meta/prompts.json`
  (selected by `optimizer_set: "meta"`), not in a local `prompts/` dir.

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

(plug in the current values from `campaign.json` + `inner_tasks.json`). The
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
