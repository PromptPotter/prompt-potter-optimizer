# L1 meta-campaign — parallel-use lookup

Companion to [`potter-l1-meta-campaign`](../../.claude/skills/potter-l1-meta-campaign/SKILL.md). The skill ticks, prints a one-paragraph status, exits. This page is what to open **while the next tick is paused** — to verify or debunk what the last tick claimed. Keyed by skill phase: status output mentions `Phase 2` ⇒ jump to §4.

> Project root is `.promptpotter/projects/{project}/` where `{project}` is currently `default` (single-project today; webapp control plane will multiplex). All `campaigns/…` paths below are relative to that root.

> Skill-internal fact map: SKILL.md lines 216–227 (authoritative for what the skill reads). Drift between the skill and this page ⇒ skill wins; file an issue.

## §1 — TL;DR: the anchor set

| File | Live in `/ui`? | When |
|---|---|---|
| `.promptpotter/meta_campaigns/{prompt_id}/state.json` | no | every tick — phase, pending_*, paused, proxy_lift_corr |
| `.promptpotter/meta_campaigns/{prompt_id}/log.jsonl` | no | every tick — append-only audit (`review` / `screen` / `promote_*`) |
| `campaigns/{cycle_id}/review.md` | partial (FilesPane) | post-cycle — behavior table + L1Stats header |
| `campaigns/{cycle_id}/dashboard.json` | yes (polled 2 s) | mid-cycle — live state, current_acc, candidates |

If you only check three things, check the first three.

## §2 — Pre-tick: pause + state

Skill said `paused`, `pause_reason=…`, or `first run — fill focus_dataset`.

| Open | What you'll see |
|---|---|
| `.promptpotter/meta_campaigns/{prompt_id}/state.json` | `paused`, `pause_reason`, `phase`, `active_hash[:8]`, `mode`, `focus_dataset` |
| `.promptpotter/meta_campaigns/{prompt_id}/proposed_edits/` | unified diffs awaiting your hand. Apply (or ask Claude to), then clear `paused: true` ⇒ `false` |

Missing dir ⇒ skill has never ticked for this `prompt_id`; first invocation initialises it.

## §3 — Phase 1 — Assess (mode)

Skill prints `mode: early|plateau|bridge|portfolio`. Mode change → surfaced loudly.

| To verify | Open |
|---|---|
| `mode` is correct | `log.jsonl` — count `promote_accept` entries by `dataset` (rules: `<3` ⇒ early; last 3 lifts `<epsilon_plateau` ⇒ plateau; ≥3 accepts on ≥2 datasets ⇒ portfolio) |
| Per-dataset lift trajectory | `log.jsonl` grep `"kind":"promote_accept"` — read `final_accuracy` / `rounds_to_95` |
| `focus_dataset` is right | `state.json::focus_dataset` and `secondary_datasets` |

No need to open any cycle dir for Phase 1.

## §4 — Phase 2 — Per-cycle review

Skill writes one `review` row per newly-complete cycle and prints `round_1_verdict: healthy | degraded | broken`. Each verdict has its own debunk path.

**Common entry — open first regardless of verdict:**

| Open | What |
|---|---|
| `campaigns/{cycle_id}/index.json` | `status == "complete"`, `final.{rounds_to_95, final_accuracy}`, `fork.trigger` |
| `campaigns/{cycle_id}/review.md` | behavior table (the four seeded checks + scaffolding), L1Stats header (`yield_rate`, `top_lift_mean`, `stagnation_max`, `l2_fires`) |

### Verdict: `healthy`

Cycle flows on to Screen/Promote. Sanity-check only.

- All four behavior checks ✓ in `review.md`?
- `yield_rate ≥ 0.20` and `top_lift_mean > 0`?
- If yes, trust it — Phase 4 will pick this up next tick.

### Verdict: `degraded` (sweep cycle ⇒ auto-reject; full cycle ⇒ skill paused)

Skill ranked the top issue + wrote a proposed edit. Verify the rank first.

| Top-issue claim | Corroborate in |
|---|---|
| Failed `context_object_honored` / `param_scope_discipline` / `not_only_param_variants` / `parse_success` | `review.md` behavior table — the ✗ row's "evidence" column quotes the failing candidate |
| Low yield (`<0.20`) | `review.md` L1Stats `yield_rate` + `.runtime/cache/candidates/round_0001.json` — eyeball how many candidates look distinct |
| Flat lift (`top_lift_mean ≤ 0`) | `.runtime/cache/rounds/round_0001.json::nodes.l1_score` (per-candidate composite vs origin) |
| `l2_fires > 0` AND round-1 winner from L2 | `.runtime/cache/candidates/round_0001.json[*].lineage.source == "l2_context"` |

| Then open | For |
|---|---|
| `.promptpotter/meta_campaigns/{prompt_id}/proposed_edits/{cycle_id}_{ts}.diff` | The exact edit the skill proposed. Leading comment lists any lower-rank issues if you want to bundle |
| `promptpotter/application/optimization/optimizer_pipeline.json` | Find `resolved_prompts['{prompt_id}/1']` — the file the diff applies against |

After applying, clear `state.json::paused`.

### Verdict: `broken` (always pauses the skill)

≥2 ✗ behavior checks, OR origin regression on round 1.

| To verify origin regression | Open |
|---|---|
| Round-1 best vs parent's origin | `.runtime/cache/rounds/round_0001.json::nodes.l1_score` (best candidate fitness) vs `index.json` of the **parent** cycle's `final.composite_fitness` |
| Which checks failed | `review.md` behavior table — count ✗ rows |

Same proposed-edit + un-pause workflow as `degraded`.

## §5 — Phase 3 — Slate

Skill prints `ready for new candidates — N drafts in sweep/proposed/` or `slate locked: …`.

| Open | What |
|---|---|
| `datasets/{focus_dataset}/sweep/NN_*.json` | Committed sweep payloads — these are the slate. Each one is a `ForkPayload` (origin `from_cycle_id` + L1 mutation) |
| `datasets/{focus_dataset}/sweep/proposed/NN_*.json` | Skill-authored drafts. **Operator must move** to one level up to commit. Until then, slate is empty |
| `.runtime/cache/rounds/round_NNNN.json::nodes.l1_critique` of the focus cycle | Source the skill mined for distinct failure modes |

Slate quality rules to spot-check (skill rejects silently if violated):

- All entries share `from_cycle_id` + `from_round = 1`.
- Each touches **only** the campaign's `prompt_id` (not l1+l2 together).
- No two share `sha256(rendered template)` — `cat NN_*.json | sha256sum` to dedup-check.

Recommended CLI is printed in the tick output; do not invent your own.

## §6 — Phase 4 — Screen

Skill writes one `screen` row per scored candidate. Verdicts: `winner | tied | loser | reject_health | reject_behavior`.

| Common | Open |
|---|---|
| The screen row itself | `.promptpotter/meta_campaigns/{prompt_id}/log.jsonl` — grep `"kind":"screen"` for the candidate name |
| The candidate's round-1 trace | `campaigns/{cycle_id}/.runtime/cache/rounds/round_0001.json::nodes.l1_score::candidates[*].composite_fitness` |
| Parent baseline (the `parent_top_lift_r1`) | Same path, **parent cycle** — or `state.json::parent_hash` then look up the cycle that emitted it via `log.jsonl::promote_accept.prompt_hash` |

### Verdict: `winner`

`top_lift_r1 > parent + epsilon_lift` (default `epsilon_lift = 0.02`).

- Confirm `top_lift_r1` arithmetic: `max(candidates.composite) − origin.composite` from `round_0001.json`.
- Candidate auto-flows to `pending_full`. Next tick will recommend the full-cycle CLI.

### Verdict: `tied` / `loser`

Margin too small or negative. Skill recommends another sweep arm.

- Open the **parent's** `round_0001.json` to see what the parent's best looked like.
- Eyeball the lineage `changes_description` of the failing candidate in `.runtime/cache/candidates/round_0001.json[*].lineage` — was the mutation in scope?

### Verdict: `reject_health`

Phase 2 review already flagged `degraded` / `broken`. The screen row is bookkeeping — no rung re-run.

- Cause is the matching `review` row in `log.jsonl` with same `cycle_id`. Look at §4 paths.

### Verdict: `reject_behavior` (defect)

Healthy cycle should never trip R0. If you see this, surface it — there's a contract bug.

- Compare `review.md` behavior table (all ✓?) vs the screen row's `behavior_pass`. They should agree. If not, bug.

## §7 — Phase 5 — Promote

Skill writes `promote_accept` or `promote_reject` per full cycle.

| Open | What |
|---|---|
| `campaigns/{cycle_id}/index.json::final` | `rounds_to_95`, `final_accuracy`, `prompt_hashes.{prompt_id}` |
| `state.json::active_hash / parent_hash` | After accept, `parent ← active`, `active ← new`. After reject, both unchanged |
| `log.jsonl::promote_accept` of the parent | The baseline the verdict compared against — read `rounds_to_95`, `final_accuracy` |

Mode-specific:

- **early / plateau**: accept on any of `rounds_to_95 ≤ parent`, or parent never hit 95% and new one did, or `final_accuracy > parent + epsilon_lift`.
- **bridge**: open the **secondary dataset's** matching cycle (same `prompt_hash`). Both must win — split verdict = reject.
- **portfolio**: open `index.json` for **each** dataset's full cycle. Mean lift > 0 across all + no per-dataset regression > `epsilon_regression` (default 0.05).

Skill says `still waiting on cycle X` ⇒ that cycle's `index.json::status != "complete"`. Resume or wait.

## §8 — Phase 6 — Record (proxy correlation)

Skill prints `proxy_lift_corr = X (n_paired = N), screen_floor = rung_2 | rung_3`. This decides whether R1 lift can stand on its own (cheap) or whether every screen must read R2 (expensive).

| Open | What |
|---|---|
| `state.json::proxy_lift_corr, n_paired, screen_floor` | The current trust state. `≥ 0.6` ⇒ R1 stands alone; `0.4–0.6` ⇒ R1 + one full-cycle confirm; `< 0.4` ⇒ rung_3 (R1+R2 minimum) — surface loudly |
| `log.jsonl` paired entries | Each `screen` row pairs with the matching `promote_*` on `prompt_hash` + `dataset`. Spearman-rank `top_lift_r1` vs `rounds_to_95` |

If `screen_floor == "rung_3"` and you didn't expect it ⇒ the proxy collapsed. Phase 4 will start reading round-1 (not round-0) accuracy for every candidate until the correlation recovers.

## §9 — Where are the candidates **right now**?

Most-used lookup, isolated. The skill rarely mentions this directly — but every other phase question hinges on it.

| Question | Open |
|---|---|
| Current-cycle candidates for round N | `campaigns/{cycle_id}/.runtime/cache/candidates/round_{N:04d}.json` — list of OSPs with `persona`, `instruction`, `lineage`, `task_context`, `l1_layout` |
| Per-candidate scores for round N | `campaigns/{cycle_id}/.runtime/cache/rounds/round_{N:04d}.json::nodes.l1_score::candidates[*]` — `composite_fitness`, `accuracy`, `is_winner` |
| L1 generator's I/O for round N | Same file, `nodes.l1_generate.input` / `.output` — what the LLM saw and said |
| Live state (no file open) | `dashboard.json::current_round.candidates` (polled by `/ui`) |
| Per-candidate measurements (cross-cycle) | `archive/measurements_index.json` → per-candidate `measurements/candidate_*.json` |

Note on the two `round_NNNN.json` files (same name, different dir, different content):

- `.runtime/cache/candidates/round_NNNN.json` — the **candidate list** (OSPs the optimizer produced).
- `.runtime/cache/rounds/round_NNNN.json` — the **audit trail** (LLM I/O blocks for `l1_generate`, `l1_critique`, `l1_score`, written by `AuditTrailView` on round-complete).

SKILL.md elides the `.runtime/cache/` prefix when it cites "the round JSON" — both files live under it. Canonical projection writer: `promptpotter/infrastructure/projections/audit_trail.py`. If the path drifts, check there.

## §10 — Dashboard is too noisy. What's the minimum I need?

When `/ui` is overwhelming and you just want to know "is anything wrong", open these four files in tabs and ignore the rest:

1. `.promptpotter/meta_campaigns/{prompt_id}/state.json` — what phase am I in?
2. `campaigns/{latest_cycle_id}/review.md` — did the last cycle pass behavior?
3. `campaigns/{latest_cycle_id}/.runtime/cache/rounds/round_NNNN.json::nodes.l1_score` (latest N) — did the last round move the needle?
4. `campaigns/{latest_cycle_id}/dashboard.json::state` — is anything still running?

Everything else is detail. Skip until one of those four points at a problem.

## §11 — Webapp `/ui` coverage matrix

`uvicorn` on :8001 ⇒ `http://localhost:8001/ui/`. Reads `active_session.json` at load; `init` a new cycle ⇒ reload page.

| Phase question | `/ui` answers it? | Best file fallback |
|---|---|---|
| Is the meta-campaign paused? | **no** | `state.json` |
| Is the current cycle running / between rounds? | **yes** — LiveStateCard | `dashboard.json::state` |
| What's the current accuracy / best? | **yes** — HeroSummary, ProgressCard | `dashboard.json::current_acc, best` |
| Per-round trend | **yes** — TrendChart | `dashboard.json::rounds` |
| Per-sample HIT/MISS for current round | **yes** — LiveSamplesCard, EvalTable | `dashboard.json::current_round.samples` |
| Hard-sample leaderboard | **yes** — HardSamplesTable | `archive/hard_samples.md` |
| Per-candidate composite this round | partial — RawJsonCard if you point it at `round_NNNN.json` | `.runtime/cache/rounds/round_NNNN.json` |
| Behavior table (the four seeded checks) | partial — FilesPane → `review.md` | `review.md` directly |
| Meta-campaign `log.jsonl` (review/screen/promote rows) | **no** | `log.jsonl` directly |
| Proposed edit diffs | **no** | `proposed_edits/*.diff` directly |
| Slate state (`pending_screen` / `pending_full`) | **no** | `state.json` |
| Sweep result JSON (`archive/sweeps/…`) | partial — FilesPane | `archive/sweeps/{hash}/{dataset}/*.json` |

Pattern: anything that lives **inside a cycle** is usually in `/ui` (it's polling `dashboard.json` + the file tree). Anything that lives in `.promptpotter/meta_campaigns/` is **not** — open it directly.

## §12 — Sweep verbs (syntax card)

The toolkit (`promptpotter/application/sweep/toolkit.py`) — cheap A/B for L1 candidates ahead of full promotion. Full spec: [`docs/specs/m10-sweep-toolkit.md`](../specs/m10-sweep-toolkit.md).

```bash
python -m promptpotter sweep time-to 66 --l1-prompt l1_v3 --dataset aime --max-rounds 10 --max-spend 5
python -m promptpotter sweep round1   --l1-prompts l1_v3,l1_v4 --dataset aime --panel-size 6
python -m promptpotter sweep round2   --from-sweep <sweep_id> --top 3
python -m promptpotter sweep round1   --l1-prompts l1_v3      --dataset aime --slice hard
python -m promptpotter sweep rank     --dataset aime --by round1_accuracy --last 10
```

Result JSON: `archive/sweeps/{l1_meta_prompt_hash}/{dataset}/{verb}_..._{timestamp}.json` (single shape across verbs; only some fields populated per verb).

The skill reads these files when computing Phase 4 screen verdicts and Phase 6 `proxy_lift_corr`. **Don't re-run a sweep to compare — `rank` reads JSON.**
