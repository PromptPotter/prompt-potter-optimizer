# money-2: one campaign load, one limits record, the claim under the gate (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `money-2`, arc `arc/money-2`.

- **Base:** `origin/main` at `0a498a5fb` (landed 89: faults-2b, tv-2 and the typed command log). Your
  `<base sha>` in common.md is this commit. faults-2b's gate bans `import logging` in `application/`
  and `domain/`: a fault is a typed record or a served field there. A `CommandRecord.payload` is its
  kind's payload model since 89; read it typed, never as a dict.
- **Spec:** the ledger section `### batch/26 · money`, every row (6), including "A campaign's absence
  is refused in eleven spellings". Re-run each row's Re-test at your base first; a row already
  closed is deleted with its evidence in the commit body.
- **The code shrinks**: eleven load-and-refuse sites become one read, three whole-record restaters
  become patches, four spelled coupon tests become one fact. Run
  `git diff --shortstat <base> HEAD -- promptpotter` after each link.
- **Beside you:** **addr** (cloud) owns `hierarchy_subtree.py`, `application/hierarchy.py`,
  `cycle_reads.py`, `store/account_trail.py`, `layout.py`, `runner/output.py` and the webapp's
  lineage and id files. You share `infrastructure/store/campaign_store/store.py`: you touch only its
  campaign loads (`:262`, `:326`, `:526` at 5273b6adb), `read_standing_budget` and its `spend_book`
  import line; addr only its parent-counting lines. Name the file in your commit body. Delete only
  your own ledger rows.

## The census (orchestrator, at 5273b6adb; re-grep it on your base)

- Campaign loads: `jobs/launcher/admission.py::campaign_at`, `jobs/quota.py:781,859`,
  `campaign_store/store.py:262,326,526`, `mask/load.py:190`, `scoring/measurement_log.py:210`,
  `evidence/head_to_head.py:567`, `scoring/passes.py:637` (`BenchTriggerError`, unreachable),
  `initialization/scoring_scope.py:147` (`DiagnosticError`), `served_dashboard.py::fork_remainder`;
  one resume loads its campaign five times (count them on your base).
- The standing budget: `campaign_store/store.py:58` imports `standing_budget` from
  `infrastructure/llm/spend_book.py:80`; `CampaignStore.read_standing_budget`.
- The limits: `RunLimits`, `RunWiringRecord`, `ServedDashboard.overshot`, `StandingBudgetRecord`,
  its writers `change_budget`, `stand_held_budget`, `declare_round_allowance`, `declare_run_wiring`,
  and `RunLimitsProjection`.
- The claim: `identity/accounts.py::claim_default` (its `producer_attached` read under the accounts
  lock), its caller `application/account.py:256`, `admission.py::destroying`; `gate.py::_LAYERING`
  forbids `infrastructure/` importing `admission.py`.
- The coupon: `jobs/quota.py` (`launch_bound`, `admit_launch`, `_tier_bound`, `_clamped`).

## Decisions (orchestrator; build them, do not reopen them)

- **`CampaignStore.campaign(id) -> Campaign` raises `NotFoundError`**, every site reads it, and
  `campaign_at` goes; the unreachable `BenchTriggerError` and the `DiagnosticError` spelling go with
  their loads. A resume loads its campaign once and hands it down. `fork_remainder` refuses a missing
  campaign as every other read does.
- **The standing-budget read lives with the store**; the book takes it from there, and no store
  module imports `spend_book`.
- **The declared limits are one record; each change is a patch of the fields it names**, and the
  projection folds the patches in order of arrival. A standing record landing before the wiring is
  served; `stand_held_budget` keeps `pause_at_round`; the copy `declare_run_wiring` makes for the
  served view goes. A served-only fact (the overshoot count) is the served row's own field, never a
  field on the persisted declaration. A reshape of run data wipes it (count it first).
- **The default claim runs under the gate**: `application/account.py` holds `admission.py::destroying`
  for each of the terminal's campaigns around `claim_default`, and its `producer_attached` read goes.
- **Whether a launch draws the coupon is one named fact of its sends**, read at the four sites.
- **"Two carriers"** has no Action: check it states what is true at your base (the run's `RunScope`
  and the per-call node context, nothing else); if so, delete it as a description that belongs in
  `docs/developer/conventions.md` if anywhere; if a third carrier stands, report it as a row.

## Links (one commit each, in order)

1. `CampaignStore.campaign(id)` at every site; `campaign_at` goes.
2. The store's standing-budget read and the limits as one patched record.
3. The claim under `destroying`.
4. The coupon fact; the carriers row.

## Proof (beyond common.md)

- Equals-main UNMOVED on every leg; a leg that moves on the limits fold is explained by the fold's
  order alone, against `--against` of your base. Digests unmoved, or a move named with its cause.
- Each row's re-test, before and after. A pass registered on a terminal cycle refuses the host's
  claim (a test, by `tests/CLAUDE.md`).
- No paid call: every proof is offline.
