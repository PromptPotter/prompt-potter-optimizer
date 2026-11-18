# spend-4: the client, head-to-head, cap-line and memo rows that wait on the spend tests (cloud)

`briefs/common.md` binds you. Base: `origin/main` (with bundle C: panel-rule-2, admit-3,
factory-keys). The rows are in `docs/specs/code-debt-cleanup.md`; quote each row's title in the
commit that closes it and delete it. One link per row, in this order; a row that reads false at its
line is reported, not built. admit-3 cut links 1–2 only because `tests/test_spend.py` was another
chain's: each row now names its unblock.

## Links
1. **A closed `BackendClient` reopens its pool on next use**: the row's Action and unblock.
   **Re-test:** the row's.
2. **A head-to-head on a bank with no `dataset_split` runs blind**: `arm_plans` refuses after the spend
   refusals, naming the missing split; the row's unblock rewires the tests. **Re-test:** a head-to-head
   mint on a bank with no held-out rows refuses.
3. **A campaign's cap count joins every cycle's bills to one cycle's other parts**
   (`served_dashboard.py::campaign_count`). **Re-test:** the row's.
4. **The door test reads `campaign_count`, not a door**: through `current_caps` under a held, appending
   cycle (`held_cycle` moves into `tests/factories.py`). **Re-test:** the row's.
5. **A process-global outage memo leaks across tests** (`infrastructure/llm/pricing.py::_ROUTE_OUTAGE`):
   find the send that escapes `offline` first (the root), then key the memo where a test's world
   resets it. **Re-test:** the row's.

## Owns
`infrastructure/backend.py`, `application/jobs/mint.py`, `served_dashboard.py`,
`infrastructure/llm/pricing.py`, `tests/test_spend.py`, `tests/test_security.py`, `tests/test_identity.py`,
`tests/factories.py`, `tests/conftest.py`, generated specs/types. NOT `application/embedded_run.py`,
`presentation/teleprompter.py`, `application/maintenance/**`, `infrastructure/store/dataset_access.py`,
`runner/inner/**`, `connectors/**`, `scripts/complexity_ledger.py` (the `small-9` chain). A file outside
Owns the root needs: report it.

## Target
Net ≤ 0 `promptpotter/` lines. Run the `--against` legs and `--digests`; name every leg and digest
that moves, old→new. A run-data shape that moves: count the wipe and name it.

## Report
Per `common.md`.
