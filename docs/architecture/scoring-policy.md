# Scoring Policy

Traces and scores are separate concerns. Understanding the split is important whenever you edit a scoring formula, resume a campaign, or try to compare runs across policy changes.

---

## Data vs. scoring policy

A trace is a record of what the pipeline did — the query, the prediction, the ground truth, how nodes ranked candidates, what timed out. A score is a judgment *over* a trace — "how good was this?" — and the answer changes with what you're optimizing for. The two belong to different worlds: the trace is a fact, the score is a policy, and conflating them is how campaigns end up silently drifting when a scoring formula is edited mid-flight.

PromptPotter keeps them separate. Traces are written once and never edited. Scores are a view, produced by applying the active scoring policy on demand.

### Traces carry a ledger of scores

Since a trace can be judged under many policies, scores are persisted as a ledger rather than a single slot. Every time a trace is evaluated, the result is written alongside the identity of the scorer that produced it. The ledger grows; past interpretations stay retrievable. Two cycles sharing the same trace corpus but running under different scorers each see their own reading of the same underlying data, without corrupting each other.

Cycle identity reflects this split. A cycle is hashed from its pipeline, prompts, and dataset — not from its scoring formula. Editing the formula doesn't mint a new cycle; the traces it produces are still addressable in the shared corpus, and their ledgers simply gain another entry.

### Rescore-on-load

The separation is enforced at one seam: whenever a trace crosses from disk into memory, it gets rescored under the currently active scorer. Fresh samples, cache hits, trial reloads, cross-campaign memory ingest — all four paths go through the same rescoring step. The `hit` and `score` fields you read at runtime are always the current policy's view, even if the trace was captured under an older one.

---

## Decision-replay and fork

### Decisions are pure functions of scored results

The optimizer's choices — which candidate wins a round, which ones get eliminated early, when to escalate from L1 to L2, when L3 replans — all derive from scored numbers. That makes them replayable: the same decision function, given freshly rescored inputs, will produce whatever outcome those inputs justify.

When a campaign commits a decision, it also records that decision — its kind, enough to re-derive it, and the outcome it reached. On resume, after rescoring prior trials under the current scorer, the optimizer walks each recorded decision and re-runs the corresponding decision function against the rescored view. If the re-run matches the record, that round stands; if it differs, that's the divergence point — the first place the current policy would have sent the campaign somewhere other than where it actually went.

At the first divergence, the campaign stops. The halt exists to prevent silent drift onto a path the current scorer no longer chooses. The user sees a concrete report — round, decision kind, recorded outcome, current outcome — and decides how to proceed.

### Two-tier decision records

Every decision record splits into two halves:

- **Flow-determining half** — `kind`, `inputs_ref`, `outcome`. This is what divergence detection looks at. `inputs_ref` stores pointers and invariants only: candidate IDs, round numbers, and gate parameters that do not depend on the active scorer. Anything that is a function of scored numbers is derived on replay from the rescored trial, never stored, because a persisted value computed under the old scorer would manufacture false divergences.

- **Archival half** — `data`. Carries everything that matters for meta-analysis but has no business in a gate: full LLM outputs, diagnostic context (p-values, stall counts, the recorded threshold under the old scorer). Replay never reads `data`. A rescore that wiggles numeric inputs but leaves the gate intact does not flip the archival payload — the split is what lets a "noisy rescore that doesn't change the flow" pass silently.

### Recorded decision kinds

Five kinds are recorded today; the first four are divergence-gated, the fifth is archive-only.

| Kind | Gate | Divergence-gated? |
|------|------|------|
| `round_winner` | Which candidate's rescored mean score beats the round baseline | Yes |
| `elimination_cut` | Wilcoxon signed-rank with Holm-Bonferroni correction | Yes |
| `l2_escalation_trigger` | Patience gate on the rolling stall count since L2's last entry | Yes for patience-triggered L2; not for degradation-triggered L2 |
| `l3_escalation_trigger` | Patience gate on the stall count since L3's last entry | Yes |
| `probe_round_commitment` | Projection of L2's LLM-output action field | No — probe is determined by L2's LLM output, invariant under pure scorer swap |

### Fork commits to the new policy

If the user wants the new scoring policy to continue, `fork` mints a new cycle rooted at the divergence point with a pointer back to its parent. Trials up to the divergence round are copied into the new tree; the shared trace data stays in place. The old cycle is left untouched. From the fork point forward, the new cycle makes its decisions under the current scorer; the old cycle remains the record of what happened under the original one.
