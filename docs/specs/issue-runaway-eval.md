# Issue: Runaway Evaluation Has No Quick Kill

**Date:** 2026-04-09
**Severity:** High
**Status:** Open

## Problem

`optimize --auto` with `eval_sample_size: 200` and `n_variants: 5` fires 1000+ backend calls in rapid succession. Each call costs money (LLM inference). The user had no way to stop it:

- **Ctrl+C didn't work fast enough** — the graceful shutdown waits for in-flight calls
- **Closing the terminal window** was required
- **The backend service had to be killed separately** — orphan requests kept hitting it
- **`taskkill //F //IM python.exe`** was the only effective stop, but it kills ALL Python processes (including the backend)

## Impact

- Uncontrolled API spend — user pays for every LLM call until the process dies
- No visibility — users don't typically watch the backend terminal, so they don't see the flood of requests
- Poor developer experience — having to force-kill processes is unacceptable

## Observed Behavior

1. User runs `optimize --auto` expecting a quick test
2. Campaign runner immediately starts evaluating 200 queries × 5 candidates = 1000 backend calls
3. Each call prints output, flooding the terminal
4. Ctrl+C is caught by graceful shutdown handler, which tries to finish the current batch
5. User closes terminal — but orphan async tasks may continue briefly
6. Backend keeps processing queued requests until it's also killed

## Expected Behavior

- Before starting evaluation, print the cost estimate: `"About to evaluate {n_variants} candidates × {eval_sample_size} queries = {total} backend calls. Proceed? [Y/n]"`
- First Ctrl+C should abort within 1-2 seconds, not wait for batch completion
- A running campaign should be stoppable via `control --stop` from another terminal (this exists but is too slow)
- Consider a hard timeout on eval batches (e.g., abort after N seconds of continuous eval)

## Workaround

Emergency stop: `taskkill //F //IM python.exe` (Windows) or `pkill -9 python` (Linux/Mac). This kills ALL Python processes including the backend.

## Related

- `SKILL.md` Phase 4 now has a safety warning requiring cost confirmation before `--auto`
- `CLAUDE.md` already says "No background CLI commands" and "CLI timeouts: 30 seconds default"
