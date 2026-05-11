# M10 (sub-spec): Sweep Toolkit

**Version:** 0.1.0
**Date:** 2026-05-11
**Status:** Spec — ~1 week of work, built before next live campaign
**Depends on:** [`m10-prompt-iteration-framework.md`](m10-prompt-iteration-framework.md)
**Supersedes:** L1-eval harness draft (scrapped 2026-05-11 — over-engineered for what the operator actually needs)

---

## Goal

Make L1 meta-prompt edits cheap-gradable using four small CLI verbs that wrap existing `optimize --sweep` plumbing. Cheap models make live sweeps cheap enough that a held-out replay corpus is overbuilt — each sweep IS the eval. The operator (and `potter-l1-meta-campaign`) jumps between the verbs ad-hoc; a `rank` view reads recent sweep results from disk and sorts by whatever column matters for the current question.

The M10 headline goal — `rounds_to_95 ≤ 5` — is gated on knowing whether an L1 meta-prompt edit moved the needle without burning a full cycle. The toolkit answers that in minutes for cents, not hours for dollars.

## Shape

Four verbs + one view. Nothing else.

| Verb | Question it answers |
|------|---------------------|
| `time-to N` | How many rounds / how much spend until L1-vX hits N% on dataset D? |
| `round1` | Single round on a panel of L1 variants. Per-variant accuracy + diversity + parse-fail in one shot. |
| `round2` | `round1` survivors fed back for one more round. Filter-then-deepen. |
| `slice` | Modifier that runs any of the above on a sample population (easy / hard / per-dataset). |
| `rank` | Read last N sweep results from disk; sort by column (accuracy, $/lift, round-1 proxy, round-2 proxy, parse-fail). |

The four verbs aren't a workflow. They're tools you jump between depending on the question. `rank` closes the loop — every sweep persists its result JSON, `rank` is how you compare across sweeps without re-running.

## What does NOT exist in this spec

- **No held-out eval-set.** Cheap models make live sweeps cheap enough that a frozen 30-sample mini-benchmark is overbuilt. Cross-dataset signal comes from running sweeps on different datasets, not from a single curated set.
- **No new projection layer.** No `L1EvalReplayView`, no new `DerivedView` subtype, no `ResumeCheckpointKind` extension. Sweep results land as plain JSON under `archive/sweeps/{l1_meta_prompt_hash}/{dataset}/{verb}_{timestamp}.json`. `rank` reads JSON files; that's the entire indexing strategy.
- **No mechanical diversity-as-constraint.** See deferred section below — the L2 / Critique agents already see a compact panel view and can score diversity subjectively as part of their existing output. That's a richer signal than edit-distance and rides infrastructure that already exists.
- **No replay corpus.** A sweep IS the measurement. Replaying L1-vN+1 against L1-vN's panel doesn't make sense if the panel itself changes per generation.

## Architectural envelope

Pre-flight gate from root `CLAUDE.md`:

1. **§0 bucket** — central loop (sweeps are cycles, just halt-early). No new bucket.
2. **Existing channel does this** — `optimize --sweep` is already the L1 A/B mechanism (per memory `feedback_sweep_and_proxy.md`). This spec adds verb ergonomics + result persistence on top, not a new mechanism.
3. **Names distinct** — `sweep`, `time-to`, `round1`, `round2`, `slice`, `rank` — all greppable, none overload existing PromptPotter vocabulary.
4. **No new I/O kind.** Result JSON is Persistence; sole ingress remains `CycleEventLog.append` for the underlying optimize runs. The `rank` view reads from disk like any other operator-side file inspection — no read API, no new ingress.
5. **Rides existing infrastructure** — sweep verbs are presentation-layer wrappers over `cmd_optimize` with preset flags (`--halt-at-accuracy`, `--panel-size`, `--max-rounds`). No new application-layer module.
6. **AI-accessible on disk** — every sweep writes one JSON file. Operator and `potter-l1-meta-campaign` read directly; no CLI invocation required to inspect history.
7. **§0 update** — none.
8. **Langfuse trace** — sweeps reuse the existing `observed_node("l1_generate_r{N}", ...)` seam. No new LLM call site.

## Verbs

### `time-to N`

```
python -m promptpotter sweep time-to 66 --l1-prompt l1_generate/1 --dataset aime --max-rounds 10 --max-spend 5
```

Runs an optimize cycle, halts when training-set accuracy reaches N% OR `--max-rounds` OR `--max-spend` (USD) is exceeded. Reports:

- `rounds_to_N`: int or null if not reached
- `spend_usd`: float
- `final_accuracy`: float
- `early_exit_reason`: `"target_hit" | "max_rounds" | "max_spend"`

One number per L1 variant per dataset. The headline metric.

Result JSON: `archive/sweeps/{l1_meta_prompt_hash}/{dataset}/time_to_{N}_{timestamp}.json`.

### `round1`

```
python -m promptpotter sweep round1 --l1-prompts l1_v3,l1_v4,l1_v5 --dataset aime --panel-size 6
```

One round on a panel of L1 variants. For each variant, panel of `--panel-size` candidates is generated, scored, eliminated. Reports per-variant:

- `round1_accuracy`: panel-mean accuracy
- `round1_best`: best-candidate accuracy
- `panel_size`: actual after PoBB elimination
- `parse_fail_rate`: fraction of L1 outputs that failed JSON parse
- `pipeline_params_entropy`: variability across panel choices
- `cost_usd`: this variant's spend

`diversity` is **not** computed mechanically here — see deferred section.

Result JSON: `archive/sweeps/{l1_meta_prompt_hash}/{dataset}/round1_{timestamp}.json`. One file per L1 variant in the sweep, all sharing a `sweep_id`.

### `round2`

```
python -m promptpotter sweep round2 --from-sweep <sweep_id> --top 3
```

Reads a prior `round1` sweep result, takes top-K survivors by accuracy, runs one more round on each. Cheaper than 5 full rounds, richer than 1 — round-1 winners that stall at round 2 are exposed cheaply.

Adds `round2_accuracy`, `round2_lift` (vs round1), `cumulative_cost_usd` per variant.

Result JSON: `archive/sweeps/{l1_meta_prompt_hash}/{dataset}/round2_{timestamp}.json`.

### `slice`

```
python -m promptpotter sweep round1 --l1-prompts l1_v3,l1_v4 --dataset aime --slice hard
python -m promptpotter sweep round1 --l1-prompts l1_v3,l1_v4 --dataset aime --slice easy
```

`--slice` is a modifier on any of the above verbs. Restricts the sample population:

- `--slice hard`: top quartile by `SampleProfile` difficulty
- `--slice easy`: bottom quartile
- `--slice all` (default): full training set
- `--slice samples=S1,S2,...`: explicit sample IDs

Free variance reduction. Same panel, three slices, three readouts. Surfaces "L1-vN is great on easy, collapses on hard" without needing a held-out set.

Result JSON gains a `slice` field; same path with `_slice_{name}` suffix.

### `rank`

```
python -m promptpotter sweep rank --dataset aime --by round1_accuracy --last 10
python -m promptpotter sweep rank --dataset aime --by cost_per_lift --last 10
python -m promptpotter sweep rank --dataset aime --by rounds_to_66
```

Reads the last N sweep JSON files for a dataset, prints a sorted table. Columns include every field the verbs emit + derived `cost_per_lift` (lift over baseline / spend).

Pure read-side. No persistence of its own. Operator's "what should I try next" lookup.

## Result JSON format

Single shape across verbs, only some fields populated per verb:

```json
{
  "sweep_id": "sw_20260511_142233",
  "verb": "round1",
  "timestamp": "2026-05-11T14:22:33Z",
  "l1_meta_prompt_hash": "abc123",
  "l1_meta_prompt_label": "l1_v3",
  "dataset": "aime",
  "slice": "all",
  "panel_size": 6,
  "round1_accuracy": 0.71,
  "round1_best": 0.83,
  "round2_accuracy": null,
  "round2_lift": null,
  "rounds_to_target": null,
  "early_exit_reason": null,
  "parse_fail_rate": 0.04,
  "pipeline_params_entropy": 0.62,
  "diversity_l2_score": null,
  "cost_usd": 0.34,
  "final_accuracy": 0.71,
  "notes": ""
}
```

One field exists as `null` placeholder for the deferred work below.

## Deferred — L2 rates diversity

L2 already receives a compact panel view as part of its existing prompt. It is well-positioned to assign a subjective diversity score (1-5 or 0.0-1.0) with one sentence of rationale, with **no extra LLM call** — it rides into L2's existing response JSON. L2 is the right agent (not Critique): diversity is a panel-shape question and L2 is the panel-aware layer (refines `task_context` after seeing how the population behaved). Critique's job is per-candidate failure analysis, not population-shape.

Why this beats mechanical edit-distance / embedding similarity:

- Captures *semantic* diversity (two prompts using different framings score differently than two prompts that share framing but vary one word)
- Free — no new call, no new infrastructure
- Already has the right context (sees task, sees prior panel)
- L2's read is exactly what the operator would do by hand

Spec change at that time:
- Add `diversity` field to L2 response schema only
- Populate `diversity_l2_score` in sweep result JSON when present
- Add `--by diversity_l2_score` to `rank`

Not in scope for this spec — slot in after the four verbs ship and the operator confirms which sweep results need diversity context. Tracked separately, lands as a small follow-on.

## Sequence

Roughly 5 working days. Order is incremental — each verb is usable before the next lands.

| Day | Work |
|-----|------|
| 1 | `sweep time-to` — wrap `cmd_optimize` with `--halt-at-accuracy`, `--max-spend`. Result JSON writer. |
| 2 | `sweep round1` — panel of L1 variants, per-variant JSON. Parse-fail + pipeline-params entropy computed in-process. |
| 3 | `sweep round2` — reads prior sweep, filters survivors, re-runs. Add cumulative cost tracking. |
| 4 | `sweep slice` — sample-population modifier across all three verbs. Use existing `SampleProfile` difficulty quartiles. |
| 5 | `sweep rank` — read-side table view. Bonus: a couple of derived columns (`cost_per_lift`). |

If day 5 slips a day, fine — the four verbs are usable without `rank`; `rank` is ergonomics, not capability.

## Done when

- All four verbs runnable on at least one dataset (aime).
- One real meta-campaign iteration recorded: pick an L1 edit, run `time-to`, decide. Result JSON readable by `potter-l1-meta-campaign`.
- `rank` prints a sortable table of the iteration's sweeps + at least one historical baseline run.
- Operator has used the toolkit to commit to (or reject) one L1-gen meta-prompt edit without a full live campaign.

## Out of scope

- L1 meta-prompt decomposition (splitting overloaded `l1_generate` into sub-prompts) — separate spec, written after the toolkit ships and the operator can measure whether decomposition helps.
- Critique→generate "telephone game" tightening (L1 quoting the failure it's responding to) — small feature commit referencing this spec.
- PoBB loser-snapshot projection — follow-on feature commit.
- 95%-in-5 → cold-start metric reframing — campaign.json schema decision, recorded in the parent M10 spec, not here.
- Mechanical diversity-as-constraint — see deferred section; L2/Critique-agent diversity ratings supersede this.
