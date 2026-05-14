# L1 meta-campaign - parallel-use lookup

Companion to [`potter-l1-meta-campaign`](../../.claude/skills/potter-l1-meta-campaign/SKILL.md). The skill ticks, prints a one-paragraph status, exits. This page is what to open **while the next tick is paused** - to verify or debunk what the last tick claimed.

> Project root is `.promptpotter/projects/{project}/` (currently `default`). All `campaigns/…` paths below are relative to that root.

> Skill-internal fact map: SKILL.md lines 216-227 is authoritative for what the skill reads. Drift = skill wins; file an issue.

## Setup - driving the loop

### 🧭 Roles

- Skill = strategist. Reads disk, decides, prints the next CLI.
- `optimize` = executor. Produces cycle artifacts the skill reads.
- Per SKILL.md: "Never run `optimize`. Recommend the exact invocation; the operator executes."

### The alternation

```
call /potter-l1-meta-campaign  ->  prints next CLI
operator runs that CLI         ->  cycle lands in campaigns/{cycle_id}/
call /potter-l1-meta-campaign  ->  reads new cycle, writes log row, prints next CLI
...
```

### 🚀 First-run flow

`state.json` is missing or `paused=true` until a cycle exists on disk.

| Step | Command |
|---|---|
| 1 | `python -m promptpotter optimize --config datasets/{name}/campaign.json` |
| 2 | `/potter-l1-meta-campaign` |

Step 2 reads `index.json::final.prompt_hashes.{prompt_id}` into `state.json::active_hash`, writes the first `review` row, clears `paused`.

Calling the skill before step 1 finishes just prints the pause message and exits. Running `optimize` first saves one tick.

### When to re-invoke

| Trigger | Why |
|---|---|
| Full cycle completed | Phase 2 writes the `review` verdict |
| Sweep batch completed | Phase 4 writes `screen` rows |
| You applied a `proposed_edits/` diff | Clear `paused: false` first, then tick |
| Unsure | Tick is idempotent, exits fast if nothing new |

## Reference - what to open per phase

### 📂 Files cheatsheet

| File | When |
|---|---|
| `.promptpotter/meta_campaigns/{prompt_id}/state.json` | every tick |
| `.promptpotter/meta_campaigns/{prompt_id}/log.jsonl` | every tick (audit) |
| `.promptpotter/meta_campaigns/{prompt_id}/proposed_edits/*.diff` | pending operator action |
| `campaigns/{cycle_id}/review.md` | post-cycle behavior + L1Stats |
| `campaigns/{cycle_id}/index.json` | post-cycle final + `prompt_hashes` |
| `campaigns/{cycle_id}/.runtime/cache/rounds/round_NNNN.json` | LLM I/O per round |
| `campaigns/{cycle_id}/.runtime/cache/candidates/round_NNNN.json` | OSPs per round |
| `archive/sweeps/{l1_hash}/{dataset}/*.json` | sweep results |

### Phase 1 - Assess

Mode = `early` (<3 promote_accept), `plateau` (last 3 lifts < epsilon_plateau=0.02), `bridge`, `portfolio` (3 accepts on 2 datasets). Grep `log.jsonl::promote_accept` to corroborate.

### Phase 2 - Per-cycle review

`round_1_verdict` in {healthy, degraded, broken}. Open `review.md` + `index.json`. Degraded sweep auto-rejects. Degraded full or broken pauses skill + writes `proposed_edits/{cycle_id}_{ts}.diff`; apply, clear `paused`.

### Phase 3 - Slate

Committed payloads: `datasets/{focus}/sweep/NN_*.json`. Skill drafts: `sweep/proposed/NN_*.json` (operator moves up to commit). Rules: same `from_cycle_id + from_round=1`, one prompt_id per candidate, distinct rendered templates.

### Phase 4 - Screen

`screen` verdict: winner | tied | loser | reject_health | reject_behavior. `top_lift_r1 = max(candidates.composite) - origin.composite` from `round_0001.json::nodes.l1_score`. Winner threshold: `parent + epsilon_lift` (default 0.02).

### Phase 5 - Promote

`promote_accept | promote_reject`. early/plateau: `rounds_to_95 <= parent` or `final_accuracy > parent + epsilon_lift`. bridge: both datasets win. portfolio: mean lift > 0, no per-dataset regression > epsilon_regression=0.05.

### Phase 6 - Record

`proxy_lift_corr` = Spearman over paired `screen` + `promote_*` on `prompt_hash + dataset`. `>=0.6` rung_2 (R1 alone); `0.4-0.6` rung_2 + one full-cycle confirm; `<0.4` rung_3 (R1+R2 minimum).

### 🔧 Sweep verbs

```bash
python -m promptpotter sweep time-to 66 --l1-prompt l1_v3 --dataset aime --max-rounds 10
python -m promptpotter sweep round1     --l1-prompts l1_v3,l1_v4 --dataset aime --panel-size 6
python -m promptpotter sweep round2     --from-sweep <sweep_id> --top 3
python -m promptpotter sweep rank       --dataset aime --by round1_accuracy --last 10
```

Result JSON: `archive/sweeps/{l1_hash}/{dataset}/{verb}_..._{ts}.json`. Spec: [`m10-prompt-iteration-framework.md#track-6--sweep-toolkit`](../specs/m10-prompt-iteration-framework.md#track-6--sweep-toolkit). Skill reads these for Phase 4 verdicts + Phase 6 correlation.
