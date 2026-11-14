# narr-3: an act banks facts, a run's ledger has one door (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `narr-3`, arc `arc/narr-3`.

- **Base:** `origin/main` at `dcc98fd75`: main with narr-2, small-2, faults-1 and webb-5b landed. Your
  `<base sha>` in common.md is this commit.
- **The code shrinks**, judged as common.md says: pre-cut strings and a second door go. Run
  `git diff --shortstat <base> HEAD -- promptpotter` after each link.
- **Spec:** `### batch/26 · narr`, these rows only: "The typed acts still bank render text", "A run's
  ledger has two doors". Re-run each Re-test first. Not yours: "The live reads re-serve history per
  poll" and the race-tracker row (webd's fork).

## Decisions (orchestrator; build them, do not reopen them)

- **An act banks facts; its renderer words and cuts them** (narr-2's decision, finished here).
  - `ProposeOpened.parent_prompt` (a 120-char slice) goes. The act names the parent, and the renderer
    reads the prompt off the record that already holds it. If none holds it, the act keeps the prompt
    whole and the cut moves to `render/ansi.py`.
  - `ProposeClosed.columns` goes: the renderer builds the columns from `RoundProposedRecord`, the
    origin and the parent.
  - `StepOpened.title/tag/lines` and `StepClosed.headline/details` go. A member's `enter_act` /
    `exit_act` (`potter/members.py:478-509, 565-585`, `potter/escalation.py:433`) return the step's
    facts as named values, untruncated; core's renderer words them. An optimizer stays a plugin: the
    facts are generic (name → value), never a per-optimizer act class.
  - Census at dce5b7883 (re-find at your base): writers `runner/round.py:1175,1221`,
    `potter/members.py:486,505,566,581`, `potter/escalation.py:433`; the reader `render/ansi.py`
    (`:84,106,205-211,340`). A reshape of run data: wipe cycles; name the landing key old → new.
- **One door to a run's ledger: the scope.** The run's own declarations (`run_observers.py:84,175`,
  `runner/entry.py:216`, `runner/mint.py:199,261`, `inner/spawn.py:317`) append through
  `append_record` under a scope opened on the cycle they name, so a muted scope mutes them too.
  `CampaignStore.append` stays the door outside a run. `scoring_scope.py::verb_ledger` refuses a bound
  ledger of another hop than the one asked for, never yields it.
- **The admission has one answer.** `RunState.admission` (unjoined) and `admission.py::admitted`
  (joined with the campaign's refusal) answer one question twice, and `campaign_refusal: str = ""`
  lets a caller skip the join. One reader joins, with no default; `served_dashboard.py:277`'s
  description follows. (narr-2's review, a symptom.)
- `promptpotter/infrastructure/CLAUDE.md`'s record row says the one door once this lands.

## Links (one commit each, in order)

1. The acts bank facts (the wipe).
2. One door: the declarations under the scope; `verb_ledger`'s refusal; the admission's one answer.

## Beside you

Name every shared file in its link's commit body.

- **coupon** (cloud, same base) adds the host's coupon verb and fixes the coupon's quota faults: it
  owns `jobs/quota.py`, `application/account.py`, `presentation/admin_bot.py` and the wallet files
  (`wallet_store.py`, `llm/wallet.py`). If your admission answer reaches `quota.py`, keep your hunk to
  that line and name the file.
- **ren** (cloud, after both of you) renames across every file.

## Proof (beyond common.md)

- Link 1: equals-main moves `decisions.ledger.phases` only (and an `instrument_id` carrying a moved
  digest): name each moved leaf. The readout and the chat thread render every act as before (a shot
  of each, before and after).
- Link 2: equals-main UNMOVED on every leg, behaviour keys unchanged. A probe render writes no file.
- `git grep -n "campaigns.append(" -- promptpotter/application/runner promptpotter/application/run_observers.py`
  is empty. Each row's re-test, before and after.
