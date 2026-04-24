# Campaign Lifecycle

A narrative walkthrough of one complete optimization session — from `init` to `show-results` — phase by phase. Companion to [three-layer-loop.md](three-layer-loop.md), which covers the mechanics in more depth.

---

## Setup — `init`

`init` is pure preparation. It loads your starting prompt and dataset, computes a cycle identifier from them, and creates the campaign artifact directory. No backend calls happen. It is safe to Ctrl+C at this stage — nothing has been written to the backend or the evaluation archive.

After `init` completes, a pointer to the active campaign is written to the active-session file. Every subsequent command (`optimize`, `show-results`, `show-status`) reads this pointer to know which campaign it's working on.

---

## Phase 0 — Baseline

The first thing `optimize` does is score your starting prompt on the full scoring slice. This is the baseline.

Two things come out of this:

1. **A baseline accuracy number.** Every candidate in every subsequent round must beat this number to become the new best. The optimizer never regresses below the starting point.
2. **The first critique.** The critique step analyzes the baseline results — which queries hit, which missed, what patterns appear — and writes an initial analysis. Round 1 enters with real data, not a blank slate.

---

## Round N — The normal path

Each round runs four steps in sequence.

### Generate

L1 Generate proposes N candidate configurations. Each candidate is a combination of prompt fields (persona, thinking style, instruction, etc.) and pipeline parameters (thresholds, node settings, etc.). L1 reads the previous round's critique and the latest intelligence from search memory, then writes candidates designed to address what the critique flagged.

The number of candidates per round is controlled by your campaign config.

### Evaluate

Each candidate is scored query-by-query against the backend pipeline. The backend receives the candidate's prompt and parameters, processes the query, and returns a result. PromptPotter scores the result and moves to the next query.

Evaluation has an early-stopping mechanism: once enough queries have run, a paired statistical test can declare a candidate clearly inferior to the current best and stop scoring it. This keeps the campaign moving without wasting budget on candidates that obviously won't win.

### Critique

After all candidates have been evaluated, the critique step runs. It is the only place in the loop that reads raw per-query results — every hit, every miss, the exact outputs. It produces a structured analysis: what worked, what failed, what failure patterns appeared, what the optimizer should try next.

This critique is written to optimizer memory and becomes the primary signal for L1 Generate in the next round.

### Winner selection

The best-scoring candidate from the round is compared to the current best. If it beats the baseline by at least the configured improvement threshold, it becomes the new current best and the baseline advances. If no candidate beats the baseline, the round produces no winner and the patience counter increments.

---

## When L1 stalls — L2 fires

After enough consecutive rounds with no improvement, L2 Refine Context fires.

L2 reads the critique history, the current best configuration, and the historical intelligence from search memory. It adjusts two things: the task context (the domain framing fed to L1) and meta-settings (how many candidates to generate, how much to explore vs. exploit). It produces a directive — 2–3 sentences of diagnostic reasoning and action guidance — that becomes the primary signal for the next L1 round, superseding the critique.

L2 does not touch pipeline parameters directly. Its job is to reframe the search, not to prescribe specific parameter values.

After L2 fires, the patience counter resets. L1 resumes with the new framing.

---

## When L2 stalls — L3 fires

After L2-adjusted rounds also fail to improve, L3 Modify Plan fires.

L3 rewrites the strategic plan — a high-level optimization framework that is appended to every L1 Generate prompt. A new plan tells L1 to approach the search differently at a fundamental level: different axes to explore, different assumptions to question, different strategies to try.

L3 is rare. When it fires, the optimizer is stepping back to rethink from scratch.

---

## Self-healing during evaluation

Two things can go wrong during evaluation, and PromptPotter handles both without stopping the campaign. See [self-healing.md](self-healing.md) for the full picture. In short:

- **Invalid parameter (before any run).** L1 proposed a value the backend doesn't accept. The candidate is rejected before scoring and receives a synthetic score of zero. Next round, L2's directive will name the forbidden value so L1 doesn't propose it again.
- **Degraded results (during evaluation).** A candidate's configuration consistently produces low-quality results. The candidate is eliminated and the failure is pinned to that configuration. L2 steers L1 away from that region; if the pattern persists, L3 replans.

---

## Round boundary

Between rounds, three things may happen (in order):

1. **Search memory refresh.** New evaluation data from the completed round is folded into the historical intelligence store. The next round's L1 Generate has an updated view.
2. **Zero-signal filter** (off by default). Queries that always hit or always miss across a minimum number of observations carry no information for the optimizer. When the filter is enabled, these queries are moved out of the active dataset. This keeps the scoring set informative as the campaign runs.
3. **Exploration / exploitation rebalance** (off by default). A statistical model (Rasch IRT) tracks per-query difficulty. Between rounds, the scoring prefix is rebalanced: queries whose difficulty we already know are dropped (exploitation has extracted most of their signal), and queries whose difficulty is still uncertain are pulled in when measuring them would be informative enough (exploration). The prefix stays the same within a round; it only changes between rounds.

---

## Finishing

A campaign stops when any of these conditions is met:

- The round limit is reached
- Perfect accuracy is achieved
- You press Ctrl+C

The first Ctrl+C finishes the in-flight backend call and saves the current state, then stops. A second Ctrl+C force-quits immediately.

After the campaign stops, `show-results` reads the trial history and renders the best configuration found — the prompt fields, the pipeline parameters, and the accuracy it achieved. `show-status` gives a live dashboard while the campaign is running.

---

## Resuming and rewinding

If you resume a stopped campaign (`optimize` with no flags), it picks up from where it left off.

If you want to rewind — discard a bad trajectory and continue from an earlier point — use `optimize --from N`. This archives the trials from round N+1 onward and restarts from round N's state. Use this when you've edited the campaign config and want the optimizer to re-explore from a specific point.

If the scoring formula changed between runs, the optimizer detects the divergence on resume and halts with a fork hint. Fork mints a new campaign cycle from the divergence point, keeping the old cycle intact as a record of what happened under the original scorer.

Full mechanics: [scoring-and-traces.md](scoring-and-traces.md) and [../operations/rewind-and-fork.md](../operations/rewind-and-fork.md).
