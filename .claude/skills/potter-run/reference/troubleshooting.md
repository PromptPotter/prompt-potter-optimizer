# Troubleshooting

## Error Categories

CLI errors follow the pattern `[CATEGORY] message`. Categories help classify root cause:

| Category | Meaning | First Step |
|----------|---------|------------|
| `[CLIENT]` | Bad request (invalid params, missing fields) | Check command flags and config |
| `[SERVER]` | Backend error (500, timeout) | Check backend status: `curl -s {url}/status` |
| `[CONNECTION]` | Can't reach backend | Is the backend running? Check URL and port |
| `[PIPELINE]` | Pipeline execution error (node failure) | Check `campaign_log.md` for node-level details |
| `[UNKNOWN]` | Unclassified | Read full error + `campaign_output.log` |

## Stop Reason → Recovery

| Stop Reason | What Happened | What to Do |
|-------------|---------------|------------|
| `patience_exhausted` | L1 stalled, L2/L3 couldn't improve further | Normal convergence. Check results — this is usually a good outcome. |
| `perfect_score` | 100% accuracy achieved | Done. Run `results --save` to persist the winner. |
| `max_rounds` | Hit maximum round limit | May need more rounds (`max_rounds` in config) or L2/L3 intervention. |
| `interrupted` | Ctrl+C during optimization | Resume with `optimize --auto`. State was checkpointed. |
| `escalation_abort` | Backend degradation too severe for L2 to fix | Read `campaign_log.md` for degradation details. May need backend fix. |
| `l2_patience_exhausted` | L2 tried `l2_patience` times, no improvement | Consider manual task_context changes or different scan axes. |
| `l3_patience_exhausted` | All three layers exhausted | Optimization has converged. Review results for best achieved. |
| `hard_cap_reached` | Hit absolute round limit (100) | Very rare. Review if L2/L3 is cycling without progress. |
| `paused_for_review` | `--round` mode paused after L1 generate | Review candidates, then `optimize --auto` to continue. |
| `user_paused` | User sent `control --pause` | `control --resume` to continue, `control --stop` to end. |
| `user_stopped` | User sent `control --stop` | Campaign ended. Run `results` to see what was achieved. |

## Reading campaign_log.md

This is the primary diagnostic tool. It's a structured markdown log with sections per phase:

- **Init section**: baseline accuracy, active pipeline, dataset count
- **Round sections**: per-round accuracy, winner config, critique summary, L2/L3 escalation notes
- **Completion section**: final accuracy, stop reason, total rounds

Look for: accuracy trends (improving, plateauing, degrading), L2/L3 activations, degradation warnings, error counts.

## Stall Recovery Strategies

When optimization plateaus:

1. **Lower `improvement_threshold`** — Default 0.01 (1%). If near-miss improvements are being discarded, try 0.005.
2. **Increase `n_variants`** — More candidates per round = wider search. Try 7-10 instead of 5.
3. **Increase `creativity`** — Higher meta-prompt temperature. Try 0.8-0.9.
4. **Run a sensitivity scan** — Identifies which axes actually move the needle. Focus optimization on those.
5. **Refine task_context** — Manually update `task_description.md` with more specific domain knowledge, then re-run `task-context`.

## Orphan Process Detection

After interrupts or timeouts, check for leaked processes:

```bash
# Windows
tasklist | findstr python

# Linux/Mac
ps aux | grep python
```

Kill any stale `campaign_runner` processes to avoid wasting API credits.

## Backend Connectivity

If `init` or `optimize` fails with connection errors:

1. Check backend is running: `curl -s {backend_url}/status`
2. Check the URL and port match what's in `dataset.md`
3. Backend may have crashed — check its logs
4. If using a remote backend, check network/firewall
