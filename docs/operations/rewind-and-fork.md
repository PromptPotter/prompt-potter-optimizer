# Rewind and Fork

Three operator workflows over the same fork primitive.

- **Rewind** — stay in the same campaign; discard trials from round N+1 onward; resume at round N.
- **Fork on divergence** — mint a sibling campaign rooted at a scoring divergence; keep the old one untouched.
- **Sweep batch** — author N candidate L1-surface overrides, mint N siblings under one root, run a 2-round sweep on each. Used for breadth-first comparison of L1 prompt hypotheses.

For the conceptual picture (cycles as a tree, what rides on it vs. what doesn't), see [`../concepts/fork-tree-and-sweep.md`](../concepts/fork-tree-and-sweep.md).

---

## Rewind — `optimize --from N`

Use rewind when the active campaign went down a path you don't want to keep. Maybe a bad L3 replan took the search into a dead region. Maybe you edited a piece of configuration and want to re-explore from a specific round. The `cycle_id` stays the same; you're just rolling back the history inside it.

```bash
python -m promptpotter optimize --from 2
```

This archives `trials/trial_0003.json` onward into `campaigns/{cycle_id}/archived/resumed_at_<ts>/`, rebuilds the trial index to reflect only rounds 0–2, restores the optimizer state from round 2's trial, and resumes at round 3.

**What's preserved:** the content-addressed measurement archive. Any per-query result from the archived trials that stays identical under the new search replays from `library/measurements/` without touching the backend.

**What's discarded:** the rounds after N are moved aside but not deleted — you can inspect them in the archive directory. They're no longer referenced by the live campaign.

**Editing optimizer state by hand.** To modify the campaign beyond just rewinding, open `campaigns/{cycle_id}/trials/trial_{N:04d}.json` and edit before running `optimize --from N`. Keep the `opt_search_point` block shape so it round-trips through the loader. See [`../developer/self-healing-internals.md`](../developer/self-healing-internals.md) for the schema.

---

## Fork — `optimize --fork-on-divergence`

Fork exists for one specific situation: the scoring formula changed, and resume detects that decisions recorded under the old scorer no longer match the rescored results under the new scorer. The optimizer stops rather than drift silently onto a path the new policy would not have chosen. You then have two choices: revert the scoring change, or commit to the new one by rerunning with `--fork-on-divergence`.

```bash
python -m promptpotter optimize --fork-on-divergence
```

When this flag is set and resume detects a divergence, the run mints a new `cycle_id` rooted at the divergence point, copies the trials before the divergent round into the new cycle, records a `parent_cycle_id` pointer back to the original, retargets the active session pointer at the new cycle, and continues — re-running the divergent round under the current scorer. The shared `library/measurements/` archive is not duplicated — both cycles read the same underlying measurements, each through their own scoring ledger. The old cycle is left alone as a record of what happened under the original scorer.

### What lands where after a fork

The fork dir nests under its family root: `campaigns/{root_cycle_id}/forks/{cycle_id}/`. All forks of a family — even forks-of-forks — live flat under the root's `forks/` subdir regardless of lineage depth, so finding any descendant is one directory listing. After fork-on-divergence:

- **Live telemetry — `dashboard.json`, `output.log` — stays at the family root** (`campaigns/{root_cycle_id}/`, the cycle with no `parent_cycle_id`). Forks share one continuous stream so `tail dashboard.json` covers the whole family. `output.log` gets a `=== FORK <id> from round N (parent: …) ===` banner at the cutover; `dashboard.json::cycle_id` always names the currently active fork.
- **Per-cycle audit — `index.json`, `log.md`, `trials/`, `langfuse/`, `prompts/`, plus `.cache/candidates/` + `.cache/rounds/` for internal resume state — lives in the fork's own dir** under `forks/`. The parent's audit stays frozen as the historical record up to the divergence point. The fork's audit starts with the survivor trials copied at fork-mint and grows as new rounds complete.

To monitor a forked run, point your editor at `campaigns/{root_cycle_id}/dashboard.json` (the root, not the fork). To inspect what specifically happened in one fork, open `campaigns/{root_cycle_id}/forks/{cycle_id}/index.json` / `log.md` / `trials/`.

**Pre-existing flat-layout fork dirs** (from before this layout) are auto-migrated into the new nested structure on the next `optimize` run — `CampaignStore.__init__` runs an idempotent scan that moves any top-level `*_fork_*` directory into its root's `forks/`. After the first run on a tree, the scan is a no-op.

Without the flag, divergence halts so you can review the diagnostic and decide. There is no separate `fork` subcommand — fork is just a continuation flag on the next `optimize` call.

### Why rewind is not enough

Rewind restarts a cycle from an earlier point under the same policy. Fork restarts a *new* cycle under a *different* policy. If you've changed the scoring formula, rewind would try to re-run decisions the recorded history expects to match, and halt again on the same divergence. Fork cuts the cord.

See [`../concepts/scoring-and-traces.md`](../concepts/scoring-and-traces.md) for why traces and scores are separated — that's the framework that makes fork work at all.

---

## Sweep batch — `optimize --sweep` with payloads under `datasets/{name}/sweep/`

Sweep is the breadth-first version of `optimize --sweep`: instead of running one cheap-trial cycle on the active OSP, it runs N cheap-trial siblings under one parent, each starting from a different operator-authored override. It's the workhorse for narrowing down candidate L1 prompts before promoting one to a full multi-round campaign.

**The protocol per fork:** baseline (cache-hit after the first fork), one full scored round, one generation-only round (L1 emits variants but doesn't score them), halt with `SWEEP_COMPLETE`. The leaderboard already pairs sweep cycles with their full counterparts via `proxy_lift_corr` once at least 4 paired branches exist.

### Authoring a sweep payload

One JSON file per candidate under `datasets/{name}/sweep/`. The schema (`SweepPayload`) is the four L1-surface fields L2 already mutates, plus a `reason` label:

```json
{
  "reason": "step-by-step directive",
  "directive": "Reason step-by-step in <thinking> tags before producing the final answer.",
  "l1_section_overrides": {"axes_l1": false},
  "l1_section_overrides_text": {"task_context": "BBEH targets multi-step deliberation; variants should explore decomposition + verification."},
  "l1_template_override": null
}
```

Every field is optional; `reason` defaults to empty string. The Pydantic model is `extra='forbid'` — a typo in a key name (e.g. `directve`) raises `ValidationError` at parse time, before any fork mints. Field meanings:

| Field | Effect on L1 |
|-------|--------------|
| `directive` | Stamped onto `OptSearchPoint.l2_directive`; rendered in L1's meta-prompt as the primary signal. |
| `l1_section_overrides` | Per-section visibility toggles for L1's prompt. Keys are `L1GenerateField` names; `false` gates a section off. |
| `l1_section_overrides_text` | Per-section text replacements. Keys are `L1GenerateField` names; values replace that section's rendered output. |
| `l1_template_override` | Whole-body replacement for L1-generate's `problem_description` template. Should contain `{{l2_directive}}` so the directive still flows through. |

The override fields are the same ones L2 writes when it fires — sweep just lets the operator stage one without firing L2. See [`../concepts/l1-generate-surface.md`](../concepts/l1-generate-surface.md) for what each L1 section contains.

### Running the batch

```bash
python -m promptpotter init --backend-url http://127.0.0.1:8000 --config datasets/bbeh/campaign.json
python -m promptpotter optimize --sweep
```

`--sweep` with no payloads under `datasets/{name}/sweep/` falls through to single-cycle sweep mode (today's behavior, backwards compatible). With payloads present, the runner:

1. Parses every `*.json` under `datasets/{name}/sweep/` (sorted by filename for deterministic order).
2. For each payload: mints a fork from the active root cycle at round 1; the FORK_CUT decision in the parent's ledger carries `data.fork.sweep_payload = <payload>` and `data.fork.source_file = <name>.json`.
3. Stamps the payload's L1-surface fields onto the fork's starting OSP.
4. Runs round 1 scored + round 2 generation-only + halt on the fork.
5. After all forks complete, restores the active session pointer to the root cycle.

### Reading the results

The post-hoc renderers handle comparison. Each fork produces:

- `campaigns/{root}/forks/{fork_id}/trials/trial_0001.json` — round 1 scored. The `opt_search_point` block carries the payload's overrides (so a future resume reconstructs the same starting state).
- `campaigns/{root}/forks/{fork_id}/trials/trial_0002.json` — `status: "generation_only"`, no `composite` / `accuracy`.
- `campaigns/{root}/forks/{fork_id}/review.md` — per-fork review including the round-1 verdict and behavior-check checklist.
- `campaigns/{root}/forks/{fork_id}/index.json::final.mode == "sweep"`.

For the side-by-side comparison:

```bash
python scripts/ppot_review.py --sweep
```

Sweep view groups branches by parent root, sorts by `round_1_top_lift` desc, and once at least 4 paired (sweep, full) branches exist for the same `l1_generate_hash`, reports `proxy_lift_corr` in the footer. See [`../specs/m10-prompt-iteration-framework.md`](../specs/m10-prompt-iteration-framework.md) for the headline metrics this view computes.

### What sweep is for, and what it isn't

Sweep is screening, not validation. A prompt that wins a sweep batch should be promoted to a full multi-round `optimize` run — the round-1 signal predicts full-cycle outcome only as well as `proxy_lift_corr` says it does, which the framework measures and reports rather than assumes.

Sweep is for the L1-surface overrides, not for pipeline or scoring changes. Both are intentionally absent from `SweepPayload` — pipeline changes belong to the M12 connector layer; scoring changes already have `--fork-on-divergence` as their fork driver. If a future hypothesis can't be expressed with the four available fields, that's a signal the framework wants a different layer above sweep, not a wider payload.

A sweep batch is one operator command, no race conditions: forks run sequentially because the active session pointer and the parent ledger don't tolerate concurrent mints. For tens of payloads on a slow pipeline this is the practical bound; the parallelization comes with M12's connector work.
