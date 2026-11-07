# Batch 3 review: `origin/batch/2b..origin/batch/3` (w2h f9fc0e580, w2j 848d52eaa)

**Verdict: LAND after one small fix pass.** Two INTRODUCED findings, both low. Nothing a surface reads was removed, and no behaviour moved that the briefs did not name.

## Census (batch/3 tip)

- `CouponStatus.state/.held/.left`: no reader in webapp, promptpotter, tests, scripts, paper, .claude or docs. `AccountUsageTab` reads `headline/used_share/host_key_spend/own_key_spend/routes`. `AccountKeysTab` reads `routes`. `spend.ts` reads `host_key_spend`. `AccountWallet.held/.headroom/.state` stay live (`coupon.py:233`, `refused()`, `quota.py` admission).
- `LaunchRefusal.provider`: `IngestConversation.tsx:328-329` reads only `reason` and `sentence`. No e2e or CLI reader. The sentence still names the provider in both error classes, so the operator loses no information.
- openapi/TS: exactly the four properties. The two extra hunks are generator consequences: the `AllowanceState` schema goes because nothing references it now, and the TS `Budget` interface moves by first reference with a byte-identical body.
- Edits stay inside Owns, and each commit touches only its own arc's files.
- w2j: a campaign with `runs_with === null` always carries `caveat_row("unresolved", _UNRESOLVED)` in `lines`: `hierarchy.py:217` when it has a line course, `:474` when it has none. `SubtreeCampaign` gets the same through `campaign_entry` (`hierarchy_subtree.py:378`), and `SummaryBlock.tsx:30` renders it on the card. No information is lost. The card now renders only served copy, as `webapp/CLAUDE.md` and `components/CLAUDE.md` require. `git grep "did not resolve" -- webapp/app webapp/components` returns nothing. `.rowhover-note` is still used by the declared-knobs line.

## INTRODUCED

1. **`promptpotter/shared/errors.py:245, :282`: `NoApiKeyError.provider` / `HostAllowanceExhaustedError.provider` are now read by no production code.** `LaunchRefusal.of` (batch/2b `quota.py:371`) was their only reader. `refusal_words` reads `.tab` and `str()`, not `.provider`, so the commit body's "keep .provider for refusal_words" is wrong. `test_spend.py:2975` now keeps a dead attribute alive. Scenario: a later arc renames or reshapes the error, the gate stays green on a field nothing serves, and REST/CLI's served `details["provider"]` goes unasserted. Invariant: no field without a reader. Fix: delete both `self.provider` lines, and have the test assert `box_paid.value.details["provider"] == "openai"`, the served envelope field.
2. **`docs/specs/code-debt-cleanup.md:172` is stale.** It still says "`LaunchRefusal` is served with `reason`, `provider` and `sentence`". Scenario: the M6 census plans to carry a `provider` into the Refusal value, but that field no longer exists. Fix: change it to "`reason` and `sentence`". The ledger is in w2h's Owns.

## PRE-EXISTING (rows to file)

- `launch_refusal` names two concepts: `quota.py::launch_refusal`, also served as `DraftBuild.launch_refusal`, is the coupon's refusal, and `phases.py:277` `RunState.launch_refusal` is a cycle-busy string read at `launching.py:141`. Rename the phases one. **Rides with:** M6.

## Stale lines

- `docs/specs/code-debt-cleanup.md:172` (finding 2).
- The w2h commit body's claim "error classes keep .provider for refusal_words" (finding 1).
- Neither commit body ends with the brief's `Proof:` / `Cut:` / `Rows:` trailer.
- Root `CLAUDE.md`: none.
