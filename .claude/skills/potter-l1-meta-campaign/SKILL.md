---
name: potter-l1-meta-campaign
description: Long-running, idempotent strategist for evolving one L1-tier meta-prompt (`l1_generate`, `l1_critique`, `l2_context`, or `l3_plan` under `optimizer_pipeline.json::resolved_prompts['*/1']`). Same command every invocation — reads `.promptpotter/meta_campaigns/{prompt_id}/state.json`, advances one tick of the assess → per-cycle review → slate → screen → promote → record loop, exits. Subsumes `/potter-review` (per-cycle `healthy/degraded/broken` gate + one-edit-proposal per non-healthy cycle) and the prior `potter-dev-l1` proxy-validation skill (single-edit "did my change help?" — answered by Phase 4/5 against `active_hash` vs `parent_hash`). Invoke on cron, after each finished cycle, or when the developer says "continue the meta-campaign", "did my edit help", "next move on the L1 search", "screen the next batch", or pauses between sweeps. Distinct from `l4-improve-l1-gen` (per-round in-cycle review during a single cycle's pause). Never runs `optimize`; never applies a proposed edit — writes diffs only.
---

# potter-l1-meta-campaign — Self-similar L1 meta-prompt search

Strategist for a campaign-of-campaigns. **Recipe is fixed; state lives on disk; one invocation = one tick.** Re-invoke on a cron, a watchdog, or by hand — the skill resumes from `state.json::phase`. Disk is the truth, chat is the dashboard. Ten ticks in a row produce ten consistent decisions, not ten theories.

State files at `.promptpotter/meta_campaigns/{prompt_id}/`. One directory per `prompt_id`. **Never mix** prompt_ids in one campaign — hashes, lifts, exit gates are independent.

## Tick protocol

Each invocation walks the six phases in order. Stop at the first phase that's blocked on the operator (cycle still running, slate awaiting promotion, paused on a non-healthy cycle) or that produces a decision. **Phase 6 always runs**, even when phases 1–5 did nothing.

1. **Pause check.** If `state.json::paused == true`, log "paused" + `pause_reason` and exit. Operator clears `paused` (after applying a proposed edit, or by hand) to resume.
2. **First-run init.** If `state.json` is missing, write a default (see schema), set `phase: "assess"`, and ask the operator to fill `focus_dataset` + `active_hash` once. Exit.

### Phase 1 — Assess (always runs)

Read `state.json` + `log.jsonl`. For each `cycle_id` in `pending_screen` ∪ `pending_full`, read `campaigns/{cycle_id}/index.json` to see whether `status == "complete"`. Classify mode from log history:

| Mode | Trigger |
|---|---|
| **early** | < 3 `promote_accept` on focus dataset |
| **plateau** | last 3 `promote_accept` each gained < `epsilon_plateau` on focus-dataset `final_accuracy` |
| **bridge** | plateau active AND ≥ 1 cross-dataset confirmation pending |
| **portfolio** | ≥ 3 `promote_accept` on each of ≥ 2 datasets |

Mode change since last tick ⇒ surface loudly with the triggering cycle IDs. Mode change does not, by itself, gate further phases — record it and continue.

### Phase 2 — Per-cycle review (always runs over newly-complete cycles)

For each `cycle_id` in `pending_screen` ∪ `pending_full` whose `index.json::status == "complete"` and which has **no `review` entry yet** in `log.jsonl`:

Read L1Stats from `campaigns/{cycle_id}/review.md` header (Track 3 fields): `behavior_pass_rate`, `yield_rate`, `top_lift_mean`, `stagnation_max`, `l2_fires`, and the baseline-regression flag (round 0 best accuracy vs parent baseline).

Compute `round_1_verdict`:

| Verdict | Rule |
|---|---|
| `healthy` | `behavior_pass_rate == 1.0` AND `yield_rate ≥ 0.20` AND `top_lift_mean > 0` |
| `degraded` | exactly one ✗ behavior check OR `yield_rate < 0.20` OR `top_lift_mean ≤ 0` (and not broken) |
| `broken` | ≥ 2 ✗ behavior checks OR baseline regression on round 0 |

Append one `review` entry per cycle to `log.jsonl` (schema below). Then act on the verdict:

| Verdict + cycle kind | Action |
|---|---|
| `healthy` (either kind) | Cycle flows through to Screen/Promote. No pause. |
| `degraded` AND cycle in `pending_screen` (sweep) | Auto-reject at Screen — Phase 4 reads this `review` entry and writes `verdict: reject_health` without recomputing the rung cascade. Campaign continues; other sweep arms may still be healthy. |
| `degraded` AND cycle in `pending_full` (promotion) | **Halt.** Rank top issue → write proposed edit → set `paused = true`, `pause_reason = "degraded full cycle {cycle_id}"`. Exit. |
| `broken` (either kind) | **Halt** regardless of cycle kind. Same proposed-edit + pause behavior. |

**Top-issue rank** (M10 Track 6, first match wins):

1. Failed seeded behavior check (`context_object_honored`, `param_scope_discipline`, `not_only_param_variants`, `parse_success`)
2. Failed scaffolding check (any other ✗ in the behavior table)
3. Low yield (`yield_rate < 0.20`)
4. Flat lift (`top_lift_mean ≤ 0`)
5. L2-source-of-lineage in round 1 (`l2_fires > 0` AND the round-1 winner's `lineage.source == l2_context`)

**Proposed-edit mapping** (target prompt file ⇐ top issue; one change at a time):

| Top issue | Target | Edit shape |
|---|---|---|
| Failed `context_object_honored` | `l1_generate/1` | Strengthen the context-object honoring clause. |
| Failed `param_scope_discipline` | `l1_generate/1` | Bound param mutations until `param_unlock_round`. |
| Failed `not_only_param_variants` | `l1_generate/1` | Require ≥1 prompt-field mutation per round. |
| Failed `parse_success` | `l1_generate/1` | Tighten JSON-schema reminder + example. |
| Low yield | `l1_generate/1` | Broaden L1 creativity (loosen mutation bounds, raise diversity ask). |
| Flat lift | `l1_critique/1` | Sharpen critique signal — the bottleneck is feedback, not generation. |
| Early L2 fires | `l1_critique/1` | Same root cause — critique didn't carry enough signal for L1. |

Skill writes the proposed diff to `.promptpotter/meta_campaigns/{prompt_id}/proposed_edits/{cycle_id}_{ts}.diff` as unified-diff against the file's current content. **Skill never applies the diff** — operator applies (or asks Claude to), then clears `paused`. If multiple ✗ behavior checks exist, propose the highest-ranked only; document the others in the diff's leading comment so the operator can bundle if they choose (M10's one-change-at-a-time default is a tiebreaker, not a mandate).

### Phase 3 — Slate (gated: phase 1 ⇒ "ready for new candidates")

"Ready" means: `pending_screen` is empty AND `pending_full` is empty (or only stuck items the operator hasn't moved) AND the campaign is not in `bridge` mode awaiting confirmation.

Two sources, in order:

1. **Developer payloads** under `datasets/{focus_dataset}/sweep/NN_*.json` not yet referenced in any `log.jsonl::screen` entry ⇒ they are the slate. Done.
2. **Skill-authored.** If none, mine the most-recent finished focus cycle:
   - Harvest distinct failure modes from `campaigns/{focus_cycle_id}/rounds/round_NNNN.json::candidate_scores[*].critique`.
   - Harvest behavior-check ✗ rows from `review.md`.
   - Convert each *distinct* failure mode into one `ForkPayload` and **write it to `datasets/{focus_dataset}/sweep/proposed/NN_short_name.json`**. Cap at `slate_size` (default 6).
   - The `proposed/` subdirectory is the operator's intervention gate: skill writes there, operator moves a payload up one level (`sweep/NN_*.json`) to commit. Until then, the slate is empty.

Slate quality rules (reject silently if violated):

- Same baseline `from_cycle_id` + `from_round = 1` across the slate.
- Each candidate touches **only** this campaign's `prompt_id` (not l1+l2 simultaneously).
- No two candidates share `sha256(rendered template)`; deduplicate.

When a real slate is locked (developer-supplied or operator-promoted from `proposed/`), append the candidate filenames + resolved `prompt_hash` to `pending_screen`, set `phase: "screen"`, recommend the exact `python -m promptpotter optimize --sweep …` invocation, exit.

### Phase 4 — Screen (cheap; rung 0 + rung 2; rung 3 when proxy collapsed)

For each candidate in `pending_screen` whose sweep cycle has `status == "complete"` and a matching `review` entry from Phase 2:

If the `review` entry's `round_1_verdict ∈ {degraded, broken}` ⇒ append `screen` with `verdict: reject_health` and the cycle's top issue; drop from `pending_screen`; do not promote. Continue to the next candidate.

Otherwise (review verdict was `healthy`), compute:

| Rung | Read | Compute |
|---|---|---|
| 0 — Behavior (sanity) | `review.md` behavior table | Pass = all ✓ on the four seeded checks. Healthy-gated cycles always pass; if not, log a defect and `reject_behavior`. |
| 2 — R1 lift | `rounds/round_0000.json::candidate_scores[*].composite_fitness` + `baseline.composite_fitness` | `top_lift_r1 = max(candidate.composite) − baseline.composite`. |
| 3 — R1+R2 (only when `screen_floor == "rung_3"`) | `rounds/round_0001.json::accuracy` | Reject if round-1 accuracy regresses below round-0 (proxy floor raised by Phase 6). |

Verdict (first match wins):

```
R0 fail                                              → reject_behavior   (defect — healthy cycle should never trip R0)
R2 lift > parent + epsilon_lift                      → winner
|R2 lift − parent| ≤ epsilon_lift                    → tied              (recommend another sweep arm or replace)
R2 lift < parent − epsilon_lift                      → loser
```

Append one `screen` line per scored candidate to `log.jsonl`. Drop completed from `pending_screen`; add `winner`s to `pending_full`.

### Phase 5 — Promote (expensive; full cycles)

For each candidate in `pending_full` whose full cycle has `status == "complete"` and whose Phase-2 `review` entry was `healthy` (non-healthy full cycles already halted the loop in Phase 2; if you reach this branch for a non-healthy cycle, it's a defect — surface it):

Read `campaigns/{cycle_id}/index.json::final.{rounds_to_95, final_accuracy}` and `final.{prompt_id}_hash`.

Mode-aware verdict:

- **early / plateau:** accept if `rounds_to_95 ≤ parent.rounds_to_95` OR (parent never reached 95 % AND new one did) OR `final_accuracy > parent.final_accuracy + epsilon_lift`.
- **bridge:** above on focus dataset **AND** a paired full cycle on the secondary dataset passes the same gate under the same hash. Win + loss = reject (overfit).
- **portfolio:** mean lift > 0 across all datasets **AND** no per-dataset regression > `epsilon_regression`.

Accept ⇒ append `promote_accept`; set `state.json::parent_hash = active_hash`, `active_hash = <new hash>`. Reject ⇒ append `promote_reject`; `active_hash` stays put. Still running ⇒ leave the entry in `pending_full`, record nothing, surface "still waiting on cycle X" and exit.

### Phase 6 — Record (always runs)

1. **Recompute `proxy_lift_corr`.** From `log.jsonl`, pair each `screen` entry with the matching `promote_*` on same `prompt_hash` + `dataset`. Spearman-rank `top_lift_r1` (from screen) against `rounds_to_95` (from promote; None ranks last). Skip if `n_paired < 4`. Use `scipy.stats.spearmanr` if available, otherwise inline the rank formula.
2. **Apply the proxy threshold table.** Compare result and update `screen_floor`:

   | `proxy_lift_corr` (n ≥ 4) | `screen_floor` |
   |---|---|
   | ≥ 0.6 | `rung_2` |
   | 0.4 – 0.6 | `rung_2` + require one full-cycle confirmation per promotion |
   | < 0.4 | `rung_3` (R1 + R2 minimum) — surface loudly; this is a framework-level event |

3. **Persist.** Overwrite `state.json` with new `phase`, `pending_*`, `proxy_lift_corr`, `n_paired`, `screen_floor`, `mode`, `paused`, `pause_reason`, `updated_at`.
4. **One-paragraph status.** Print: `active_hash[:8]`, mode, last verdict, last `round_1_verdict` per pending cycle, what's pending (cycles + datasets), any proposed-edit path awaiting operator action, next operator action, the exact CLI command if one is recommended.

## State + log schemas

`state.json` (overwritten each tick):

```json
{
  "prompt_id": "l1_generate",
  "active_hash": "9b7c…",
  "parent_hash": "4f3a…",
  "focus_dataset": "termnorm",
  "secondary_datasets": ["bbeh"],
  "mode": "early",
  "phase": "screen",
  "screen_floor": "rung_2",
  "proxy_lift_corr": 0.71,
  "n_paired": 6,
  "paused": false,
  "pause_reason": null,
  "pending_screen": [{"cycle_id": "…", "candidate": "01_step_by_step_verify", "prompt_hash": "…", "dataset": "termnorm"}],
  "pending_full":   [{"cycle_id": "…", "candidate": "03_no_axes_focused_task", "prompt_hash": "…", "dataset": "termnorm"}],
  "config": {
    "slate_size": 6,
    "epsilon_lift": 0.02,
    "epsilon_plateau": 0.02,
    "epsilon_regression": 0.05,
    "yield_floor": 0.20
  },
  "updated_at": "…"
}
```

`log.jsonl` (append-only, one event per line, never mutate past lines):

```jsonl
{"ts":"…","kind":"review","cycle_id":"…","dataset":"termnorm","cycle_kind":"sweep","round_1_verdict":"healthy","behavior_pass_rate":1.0,"yield_rate":0.33,"top_lift_mean":0.05,"stagnation_max":0,"l2_fires":0,"baseline_regression":false,"top_issue":null,"proposed_edit_path":null}
{"ts":"…","kind":"review","cycle_id":"…","dataset":"termnorm","cycle_kind":"full","round_1_verdict":"degraded","behavior_pass_rate":0.75,"yield_rate":0.10,"top_lift_mean":0.02,"stagnation_max":1,"l2_fires":0,"baseline_regression":false,"top_issue":"low_yield","proposed_edit_path":".promptpotter/meta_campaigns/l1_generate/proposed_edits/cycle_abc_2026-05-11T19-12.diff"}
{"ts":"…","kind":"screen","candidate":"01_…","prompt_hash":"…","cycle_id":"…","dataset":"termnorm","verdict":"winner","top_lift_r1":0.12,"parent_top_lift_r1":0.05,"behavior_pass":true}
{"ts":"…","kind":"promote_accept","candidate":"…","prompt_hash":"…","cycle_id":"…","dataset":"…","rounds_to_95":4,"parent_rounds_to_95":5,"final_accuracy":0.97,"parent_final_accuracy":0.94,"secondary_dataset":null}
{"ts":"…","kind":"promote_reject","candidate":"…","prompt_hash":"…","cycle_id":"…","dataset":"…","reason":"final_accuracy regressed by 0.03"}
```

`review` events feed Phase 4/5 gating + status output. `screen` events feed `proxy_lift_corr`. `promote_*` events feed mode transitions. Never conflate.

## Operator-side intervention surfaces

The skill is autonomous over the loop, but every mutating step has an operator-controllable surface so re-invocation is safe:

| Surface | Effect |
|---|---|
| `state.json::paused = true` | Skill exits immediately on next tick. Default kill-switch. Phase 2 sets this automatically on non-healthy full cycles or any broken cycle. |
| `state.json::config.*` | Edit any threshold; next tick uses the new value (surfaced in status output). |
| `sweep/proposed/*.json` ⇒ move up to `sweep/` | Operator promotes skill-authored slate drafts. Until moved, the slate stays empty. |
| `proposed_edits/*.diff` ⇒ apply to target file | Operator applies a skill-proposed edit (or asks Claude to). After applying, clear `paused` to resume. |
| `log.jsonl` (append) | Manually append `promote_*` or `review` rows for runs the skill didn't authorize. The skill treats them as authoritative on next tick. |

Watch progress without invoking the skill:

```bash
tail -f .promptpotter/meta_campaigns/l1_generate/log.jsonl
cat   .promptpotter/meta_campaigns/l1_generate/state.json
ls    .promptpotter/meta_campaigns/l1_generate/proposed_edits/
```

## Fact map

| Fact | Path |
|---|---|
| Per-cycle review (header: behavior table, `round_1_verdict`, L1Stats block) | `campaigns/{cycle_id}/review.md` |
| L1Stats fields (`rounds_to_95`, `round_1_verdict`, `yield_rate`, `top_lift_mean`, `behavior_pass_rate`, `stagnation_max`, `l2_fires`) | `campaigns/{cycle_id}/review.md` header |
| Round trace (`accuracy`, `candidate_scores`, `baseline`, `critique`, `lineage.source`) | `campaigns/{cycle_id}/rounds/round_NNNN.json` |
| Cycle index (`status`, `final.{rounds_to_95, final_accuracy, *_hash}`, `fork.trigger`) | `campaigns/{cycle_id}/index.json` |
| Sweep payloads | `datasets/{name}/sweep/NN_*.json` (skill-authored drafts in `sweep/proposed/`) |
| Meta-campaign state + log | `.promptpotter/meta_campaigns/{prompt_id}/{state.json,log.jsonl}` |
| Proposed edits awaiting operator | `.promptpotter/meta_campaigns/{prompt_id}/proposed_edits/{cycle_id}_{ts}.diff` |
| Meta-prompt under iteration | `promptpotter/application/optimization/optimizer_pipeline.json::resolved_prompts['{prompt_id}/1']` |
| Framework spec (origin of thresholds + verdict rules + top-issue rank) | `docs/specs/m10-prompt-iteration-framework.md` (Tracks 1, 3, 6, 7) |

## Boundaries

- Never run `optimize`. Recommend the exact `python -m promptpotter optimize --sweep …` or `--from N` invocation; the operator executes.
- Never apply a proposed prompt edit. Skill writes the diff to `proposed_edits/`; the operator (or Claude, on operator request) applies it and clears `paused`.
- Never propose more than one edit per cycle. Highest-ranked top-issue only; bundle hints go in the diff's leading comment, not as separate diffs.
- Never mix prompt_ids in one campaign.
- Never silently change `config`. Any edit surfaces in the status line of that tick.
- Never promote on a tie (R2 margin < `epsilon_lift`).
- Never average across datasets in `bridge` mode; both must win independently.
- Never mutate past `log.jsonl` lines; append only.
- Skill-authored slate goes to `sweep/proposed/`, not `sweep/`. The operator promotes by moving the file.

## Why same-prompt-every-time

The L4 LLM-driven layer would automate this strategist. It isn't installed. This skill substitutes — reading the same artifacts L4 would read, applying the same recipe, writing decisions to disk in a format the next tick resumes from. The operator's only jobs are: execute the recommended runs, promote slate drafts from `proposed/`, apply proposed edits from `proposed_edits/`, and clear `paused` to resume after each fix.

## Replaces `/potter-review`

This skill is a strict superset of `/potter-review` (M10 Track 6). Per-cycle single-edit proposals + sweep-batch ranking both live in Phase 2 + Phase 4 here, with persistent state and an outer loop on top. If `potter-review` is still installed, it can be retired — every artifact it reads (`review.md`, round JSONs, dashboard) is read by this skill too, with the same verdict rules and the same one-edit-at-a-time discipline.
