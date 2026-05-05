# Troubleshooting

## Error Categories

CLI errors follow the pattern `[CATEGORY] message`. Categories help classify root cause:

| Category | Meaning | First Step |
|----------|---------|------------|
| `[CLIENT]` | Bad request (invalid params, missing fields) | Check command flags and config |
| `[SERVER]` | Backend error (500, timeout) | Check backend status: `curl -s {url}/status` |
| `[CONNECTION]` | Can't reach backend | Is the backend running? Check URL and port |
| `[PIPELINE]` | Pipeline execution error (node failure) | Check the latest `trials/trial_NNNN.json` and `.cache/rounds/round_NNNN.json` for node-level details |
| `[UNKNOWN]` | Unclassified | Read full error + `output.log` |

## Stop Reason → Recovery

| Stop Reason | What Happened | What to Do |
|-------------|---------------|------------|
| `patience_exhausted` | L1 stalled, L2/L3 couldn't improve further | Normal convergence. Check results — this is usually a good outcome. |
| `perfect_score` | 100% accuracy achieved | Done. Winner is in `index.json::final::winner_prompt_fields`. |
| `max_rounds` | Hit maximum round limit | May need more rounds (`max_rounds` in config) or L2/L3 intervention. |
| `interrupted` | Ctrl+C during optimization | Resume with `optimize`. State was checkpointed. |
| `escalation_abort` | Backend degradation too severe for L2 to fix | Read `output.log` and the latest `trials/trial_NNNN.json` for degradation details. May need backend fix. |
| `l2_patience_exhausted` | L2 tried `l2_patience` times, no improvement | Consider manual task_context changes or different scan axes. |
| `l3_patience_exhausted` | All three layers exhausted | Optimization has converged. Review results for best achieved. |
| `hard_cap_reached` | Hit absolute round limit (100) | Very rare. Review if L2/L3 is cycling without progress. |

## Reading run state

The primary diagnostic surfaces (all under `campaigns/{cycle_id}/`):

- **`dashboard.json`** — live scalar state (phase, round, candidate, baseline / best / current accuracy, in-flight query, current_round node I/O).
- **`log.md`** — rendered narrative digest, regenerated at each round-complete and at finalize. Contains status, per-round critique / L2 brief / changes, hard-samples heatmap, and final winner.
- **`index.json`** — campaign metadata + trial index + `final` block (winner, baseline, stop_reason).
- **`trials/trial_NNNN.json`** — per-round optimizer checkpoint with the L1 critique text, l2_brief, escalation state.
- **`output.log`** — append-only HIT/MISS history (raw, ungrouped, fast to tail).
- **`.cache/rounds/round_NNNN.json`** — per-round leaderboard with scores, eliminations, change descriptions, and node I/O (internal — developer artifact).

Look for: accuracy trends (improving, plateauing, degrading), L2/L3 activations, degradation warnings, error counts. Open `dashboard.json` in your editor for live state.

## Stall Recovery Strategies

When optimization plateaus:

1. **Lower `improvement_threshold`** — Default 0.01 (1%). If near-miss improvements are being discarded, try 0.005.
2. **Increase `n_variants`** — More candidates per round = wider search. Try 7-10 instead of 5.
3. **Increase `creativity`** — Higher meta-prompt temperature. Try 0.8-0.9.
4. **Refine task_context** — Manually update `task_description.md` with more specific domain knowledge, then re-run `task-context`.

## Orphan Process Detection

After interrupts or timeouts, check for leaked processes:

```bash
# Windows
tasklist | findstr python

# Linux/Mac
ps aux | grep python
```

Kill any stale `campaign_runner` processes to avoid wasting API credits.

## Active Session Issues

PromptPotter uses `.promptpotter/active_session.json` to remember the current campaign (like a browser's active tab). Every command except `init` reads from it.

| Problem | Cause | Fix |
|---------|-------|-----|
| `No active session. Run 'init' first.` | No `.promptpotter/active_session.json` exists | Run `init` with the correct `--config` for your dataset |
| `Session '{id}' not found for backend '{bid}'` | Pointer references a deleted/moved session | Re-run `init` to create a fresh session |
| `optimize` creates a new campaign instead of resuming | You ran `init` again (it always creates new) | Don't re-init — just run `optimize` directly to resume |
| Wrong `backend_id` after init | `--backend-id` not passed, defaulted to `local` | Pass `--backend-id` explicitly or let it auto-derive from `dataset_name` in the config. Fix: edit `active_session.json` to point to the correct `backend_id` and `session_id`. |

**Key rule:** `init` = new campaign. To resume, just run `optimize` (or any other command) — the active pointer handles it.

To inspect: `cat .promptpotter/active_session.json`

## Backend Connectivity

If `init` or `optimize` fails with connection errors:

1. Check backend is running: `curl -s {backend_url}/status`
2. Check the URL and port match what's in `dataset.md`
3. Backend may have crashed — check its logs
4. If using a remote backend, check network/firewall
