# Rewind and Fork

Two distinct operator workflows for recovering from a campaign state you don't want to continue from.

- **Rewind** — stay in the same campaign; discard trials from round N+1 onward; resume at round N.
- **Fork** — mint a new campaign rooted at a divergence point; keep the old one untouched.

---

## Rewind — `optimize --from N`

Use rewind when the active campaign went down a path you don't want to keep. Maybe a bad L3 replan took the search into a dead region. Maybe you edited a piece of configuration and want to re-explore from a specific round. The `cycle_id` stays the same; you're just rolling back the history inside it.

```bash
python -m promptpotter optimize --from 2
```

This archives `trials/trial_0003.json` onward into `campaigns/{cycle_id}/archived/resumed_at_<ts>/`, rebuilds the trial index to reflect only rounds 0–2, restores the optimizer state from round 2's trial, and resumes at round 3.

**What's preserved:** the content-addressed evaluation cache. Any per-query result from the archived trials that stays identical under the new search replays from `library/dataset_runs/` without touching the backend.

**What's discarded:** the rounds after N are moved aside but not deleted — you can inspect them in the archive directory. They're no longer referenced by the live campaign.

**Editing optimizer state by hand.** To modify the campaign beyond just rewinding, open `campaigns/{cycle_id}/trials/trial_{N:04d}.json` and edit before running `optimize --from N`. Keep the `opt_search_point` block shape so it round-trips through the loader. See [`../developer/self-healing-internals.md`](../developer/self-healing-internals.md) for the schema.

---

## Fork — `python -m promptpotter fork`

Fork exists for one specific situation: the scoring formula changed, and resume detects that decisions recorded under the old scorer no longer match the rescored results under the new scorer. The optimizer stops rather than drift silently onto a path the new policy would not have chosen. You then have two choices: revert the scoring change, or commit to the new one with `fork`.

```bash
python -m promptpotter fork
```

Fork mints a new `cycle_id` rooted at the divergence point, copies the trials up to and including the divergence round into the new cycle, and records a `parent_cycle_id` pointer back to the original. The shared `dataset_runs/` trace corpus is not duplicated — both cycles read the same underlying traces, each through their own scoring ledger. The old cycle is left alone as a record of what happened under the original scorer.

From the fork point forward, the new cycle runs under the current scorer. It does not backfill decisions against the old history — those stayed in the parent.

### Why rewind is not enough

Rewind restarts a cycle from an earlier point under the same policy. Fork restarts a *new* cycle under a *different* policy. If you've changed the scoring formula, rewind would try to re-run decisions the recorded history expects to match, and halt again on the same divergence. Fork cuts the cord.

See [`../concepts/scoring-and-traces.md`](../concepts/scoring-and-traces.md) for why traces and scores are separated — that's the framework that makes fork work at all.
