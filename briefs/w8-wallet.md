# w8-wallet: who pays a library send, and what a halt names (cloud · Opus)

`briefs/common.md` and `briefs/w8-shared.md` bind you. w8-box2 landed explicit keys: a DSPy LM and a
harbor installed agent send on `wallet.py::sending_key`'s key, the account's own first, and
`ENV_KEY_PAYER` is gone.

SPEC: `docs/specs/code-debt-cleanup.md`, plus one row w8-box2 reported, which this brief files and
closes. One commit per row:
1. **A launch on a library connector reserves the coupon whatever keys the account holds**: this
   row is not in the ledger yet. `application/jobs/quota.py::sends_on` declares a dspy or harbor
   launch's cells as `BackendCells(connector, cell_payer="host")`, and
   `wallet.py::AccountWallet.refused` words them as going on the box's key (`wallet.py:249`).
   Since box2, the payer follows the account's keys, so the reservation and the refusal sentence
   follow `sending_key`'s rule. Re-test: an account holding its own openrouter key launches a DSPy
   cell with no coupon reserved.
2. **A host-allowance halt below the face, unexplained**: re-test it first. A run halted on its own
   ceiling names that ceiling, never the allowance.

OWNS: `promptpotter/application/jobs/quota.py`, `infrastructure/llm/wallet.py`, the halt's wording
(grep `host_allowance`), and their tests. `jobs/` beyond `quota.py` is w8-launch4's (re-point only).

LEAN: `promptpotter/` shrinks. Equals-main, digests and behaviour keys unchanged. Run data: none
expected.
