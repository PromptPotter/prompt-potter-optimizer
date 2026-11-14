# coupon: the host sets an account's coupon and starts it afresh; the coupon's quota faults (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `coupon`, arc `arc/coupon`.

- **Base:** `origin/main` at `dcc98fd75`: main with faults-1 and webb-5b landed. Every refusal you add
  carries its `next_acts`, by faults-1's rule. Your `<base sha>` in common.md is this commit.
- **The code shrinks**, judged as common.md says: the verb is new substance; the quota faults' fixes
  hunt deletions (a second fold of the bill, a lookup that answers runs it should not). Run
  `git diff --shortstat <base> HEAD -- promptpotter` after each link.
- **Spec:** the ledger rows "No verb sets an account's coupon or starts it afresh" (`### batch/31 ·
  small`), and under `### batch/26 · money` only "set-limits in a launch's admission window answers
  500" and "Four quota faults from a code review". Re-run each Re-test first, at your base. The other
  money rows are not yours.

## Decisions (orchestrator; build them, do not reopen them)

- **The host sets a coupon on the host-admin channel.** Census: `wallet_store.py::GrantStore.save` has
  no caller, so `grant.json` is hand-written today. `application/account.py` holds the host-admin
  verbs (`block_email`, `grant`, `revoke_grant`) and `presentation/admin_bot.py` is their door. Add
  one verb there, `set_coupon`, naming the account, the face per unit, and whether it starts afresh;
  the bot spells it as it spells `/grant`. No new door; the account's own `/auth/coupon` read serves
  the result unchanged in shape.
- **Afresh is a window, never a move of the bill.** `charges.jsonl` stays whole (it is the bill). The
  coupon's drawn amount folds the charges at or after `CouponGrant.granted_at`; the account's lifetime
  spend (`coupon_status`'s `host_key_spend`) still folds them all. `set_coupon` keeps `granted_at` and
  afresh moves it to now. Find the one fold the coupon reads (`wallet.py`, `account_spend.py`) and
  give it the window there, not at each reader.
- **`granted_at` is required once it is read.** Today it defaults to `utcnow_iso` at load, so a grant
  file without it would read as granted this instant and never age. The default goes. The grant row
  is `Fate.RUN` (`data_root.py:331`): a grant file without it follows that fate at landing. Name the
  landing-key move and the fate in your report; do not add a migration for a RUN row.
- **set-limits answers the job's limits or a typed 409 inside a launch's admission window.**
  `quota.py::change_budget` reads `money_on(…).holds[job.job_id]` for any `holds_spend` job, a
  STARTING one not yet admitted included, so the lookup raises `KeyError`. Root: the run lookup it
  reads answers admitted runs only; fix it there, never with a `.get` at this site.
- **The four reported quota faults: verify each first** with a falsifying check at its current line
  (the row's line numbers are stale). Build each that reproduces at its root; name each that does not
  as not reproduced, with the check. One of them (`coupon_status` folds the bill twice) may already
  fold once at your base: say so.

## Links (one commit each, in order)

1. The verb: `set_coupon` on `application/account.py` and the bot; the coupon's window off
   `granted_at`; `granted_at` required.
2. The quota faults: set-limits in the admission window, then each reported fault that reproduced.

## Beside you

Name every shared file in its link's commit body.

- **narr-3** (cloud, same base) gives the acts facts and the run's ledger one door. It owns
  `jobs/launcher/admission.py::admitted`, `served_dashboard.py`, `runner/mint.py` and
  `run_observers.py`. If a quota fix reaches one of them, keep your hunk to the quota line and name
  the file.
- **ren** waits for both of you.

## Proof (beyond common.md)

- Equals-main UNMOVED on every leg, behaviour keys unchanged. A landing-key move is named with the
  rows it reshapes.
- Tests (in `tests/`, by `tests/CLAUDE.md`): a set coupon is served by `/auth/coupon`; afresh leaves
  `charges.jsonl` byte-identical and serves a full coupon with lifetime spend unchanged; a grant file
  without `granted_at` does not load; set-limits on a STARTING job answers limits or 409, never 500;
  one test per reported fault that reproduced.
- Each row's re-test, before and after.
