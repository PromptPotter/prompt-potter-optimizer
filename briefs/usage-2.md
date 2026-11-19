# usage-2: a step's usage carries every reported part, unreported stays unreported (cloud)

`briefs/common.md` binds you (read BASE FIRST). Base: **`origin/wip/small-13` (`b360ae0de`)**, NOT
origin/main: it holds small-13's rows 1–2 (landed on the desk, not yet pushed) plus row 3 built and held.
Push to `arc/usage-2`, wip to `wip/usage-2`. ONE commit on top of `b360ae0de`'s parent chain: squash
row 3 into yours (rows 1–2 stay their own two commits so the desk drops them on landing).

## Decision (operator mode: made)
small-13 stopped row 3 because, alone, every offline fold's `tokens.cache_write` moves 0 → `None`.
That is the honest reading, not a bug: the re-key is PAID in this chain. Land row 3 together with its
root, the row "A step's usage drops `cache_write`" (its full text: small-13's report,
`git show origin/out/small-13-report:report.md` § Rows to file and § Symptoms).

## Rows
1. **Row 3 as built** (`wip/small-13`): `TokenAccount.reasoning` / `.cache_write` are `int | None`.
2. **StepUsage carries every part.** `domain/spend.py::StepUsage` gains `cache_write`; `from_account`
   carries every `TokenAccount` part whole (its docstring's promise). Census every reader of
   `StepUsage` and of a fold's `tokens.*`. Then the two sites that fold the unknown into 0:
   `domain/records/run_records.py::TokenUsageRecord` ("0 also means no breakdown") and
   `domain/results_health.py` (`usage.reasoning or 0`): an unreported part reads `None`, a sum over
   parts says what it does with `None`. The ledger's raw-JSON sum may keep `or 0` only if the record
   names that it is a lower bound; otherwise widen it.
3. **The offline world reports the parts** a provider reports (`scripts/offline_world.py` or wherever
   its replies are minted): a reply that reports `cache_write_tokens` / `reasoning_tokens` reports
   them, so the offline folds read real zeros where a real provider reports zeros.

## Proof
- Re-tests: a reply reporting `cache_write_tokens: 5` reaches a fold's `tokens.cache_write` as 5; one
  reporting `reasoning_tokens: 0` reaches a step's usage as 0; one reporting neither reads `None`.
- `offline_run.py --against` the base: name every moved key. Picks, elections and requests UNMOVED;
  only usage fields may move. Regenerate `behaviour_keys.json` and the specs; give old → new digests.
- Old banked `llm_call` / `token_usage` lines in the int shape still load (a widening, no wipe).
- Full gate green. Report on `out/usage-2-report` with Symptoms.
