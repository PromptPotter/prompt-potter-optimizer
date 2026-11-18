# limits-ux: one row per cap, the same served rows on every entry point (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `limits-ux`, arc
`arc/limits-ux`. Also read `webapp/CLAUDE.md`, `webapp/components/CLAUDE.md`, `webapp/lib/CLAUDE.md`
and `.claude/skills/design/SKILL.md` (mobile first: design for phone width now).

- **Base:** `origin/main` at `7d5d12f7b`. Your `<base sha>`.
- **The operator's pick (2026-11-18), the shape is decided:** the run panel's "Finishing criteria"
  (`webapp/components/shell/remote/RunLimitsControl.tsx`, hosted by `RemotePanel.tsx`) becomes ONE
  row per cap, and the CLI says what the panel says. Target, at phone width:

  ```
  FINISHING CRITERIA     whichever trips first
  Spend  [ 5.00 ] USD  $0.45 of $5  ▓░░░░  9%
         campaign book · billed+priced   Uncap
  Tokens [ no cap ]     4.5M used
         campaign book
  Rounds [ 1 ]          0 of 1 done   ░░░░
         this cycle                      Uncap
  Clock  [ no cap ] min 44m elapsed
         this launch
  Hold calls at mean bill            ( )
     asked $20 → armed $10: allowance
                               [ Set caps ]

  CLI: set-limits --usd 20
    spend  armed $10 (asked $20, allowance)
           $0.45 used · campaign book
    rounds 1 · 0 done · this cycle
  ```

## What is wrong today (orchestrator, read at the base; re-verify each)
- Every cap renders twice: a read row (`lbl`/`val`) and an edit row (prefix, input, Uncap).
- Spend and tokens show `metered` usage "this cycle" (`usedText`, its own comment: "The cap is the
  campaign's; `share` is this cycle's part of it") beside the CAMPAIGN's cap, so the meter compares
  two scopes. The used figure a cap is read against must be that cap's own book.
- `lib/run-limits.ts::capText` prints "1 rounds"; the round cap shows no rounds done, the wall-clock
  cap no elapsed time of the launch.
- A 70-word hint paragraph carries each cap's scope (campaign / cycle / launch) and the allowance
  clamp; it goes: the scope is each row's quiet sub-line (notes quieter than data), the clamp a line
  shown only when the armed cap differs from the asked one.
- "Overshoot" is a label nobody can read; the control holds each call at its node's mean bill.
- Samples in flight: the depth segments 1–4 render disabled with no visible reason (only aria);
  invariant I3. Say why where the segments are, from the served refusal.
- Developer side: `set-limits` (CLI, `application/commands/limits_and_queue.py`) applies and prints
  nothing of the ARMED result, so a clamp by the account allowance (`jobs/quota.py::change_budget`
  mins the request) and the round counter's scope stay silent; today one reads
  `dashboard --json`'s `run_limits.budget` to learn it.

## Decisions (build them, do not reopen them)
- **One served reading per cap**: axis, asked, armed, used, the book it is read against (campaign /
  cycle / launch), and the clamp reason when armed < asked. It lands on the record that already
  carries `run_limits` (find it: `infrastructure/projections/live_dashboard.py`,
  `application/served_dashboard.py`), never computed in the browser (webapp CLAUDE.md: it never
  recomputes). The panel, the CLI's `set-limits` reply and `dashboard` render those rows; one
  wording table for the scope and clamp words (the repo's `Worded` tables), never spelled per surface.
- **Entry-point parity**: the reply to `set-limits` carries the armed rows on the existing command
  channel, so CLI, REST, webapp, `/potter-run` and `embedded_run.py` all see it.
- **The meter is drawn only where a cap and a same-book used figure both exist**; an uncapped axis
  shows its used figure alone.
- Reuse `components/ui/*` (no second segmented control, switch, input); a new primitive gets its RTL
  test. Regenerate TS types (`scripts/build_ts_types.py`) and specs (`scripts/build_specs.py`).

## Beside you
- **l4-runs** (cloud) owns the bench spend kind in the same projections (`live_dashboard.py`,
  `served_dashboard.py`): if you both need one of those files, name it in your commit body and touch
  only your hunk. **l4-outer** (local) owns `application/optimizers/potter/**`,
  `application/runner/inner/**`, `domain/l4/**`, `application/bench/**`: none of yours.
- You own `webapp/components/shell/remote/**`, `webapp/lib/run-limits.ts`, the remote panel's CSS,
  `application/commands/limits_and_queue.py`, the run-limits reading's model and its tests.

## Proof (beyond common.md)
- Phone-width (390 px) and desktop screenshots of the panel against a served cycle, before and after
  (the browser walk's harness, `webapp/e2e/`), committed to your `out/limits-ux-report` branch beside
  `report.md`, their paths listed in it.
- A clamp shows: on the offline world or a fixture, ask a spend cap above the allowance; the panel and
  the CLI reply both name asked → armed and the reason.
- One link is expected; split only if the served reading and the surfaces cannot land green apart.
</content>
