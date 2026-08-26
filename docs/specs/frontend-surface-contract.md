# Frontend Surface Contract

Minimal, dual-read spec of every user-facing surface in `webapp/` — what each
control **must do**, per state. Companion to `webapp/CLAUDE.md` (implementation
invariants) and `BRAND.md` / `VOICE.md` (brand/copy). This file owns *behavior*: the
contract a PR is measured against, and the source of truth when reality drifts.

**How to read.** This file owns the cross-cutting **invariants** and the two consent gates —
the rules a PR is measured against and that a plausible edit would silently undo. It does **not**
describe surfaces: what a control renders is owned by the component, and a doc that copies rendered
strings is a stale screenshot, not a contract. `webapp/CLAUDE.md` owns the implementation rules,
`BRAND.md` / `VOICE.md` the brand and copy register.

## State vocabulary

Every data-backed surface MUST define its behavior in each of these. A surface
sitting in a non-terminal state (a spinner that never resolves) is a contract
violation, not a cosmetic issue.

```yaml
states:
  anon:      not logged in — the public preview at /. Demo/showcase content only.
  auth_empty: logged in, no active campaign selected.
  warming:   campaign selected, origin running, dashboard.json not yet written (warming_up:true).
  live:      logged in, campaign streaming dashboard.json.
  loading:   a fetch is in flight — transient, MUST resolve to live/empty/error.
  error:     a fetch failed for a non-auth reason (5xx, network, parse).
  offline:   poll stale / server unreachable.
  gone:      'the server ANSWERED and says this address does not exist (404) — deleted
             campaign, reaped .inner/ sandbox, reset store. Terminal, not retryable.
             Distinct from offline on purpose: the absence of that distinction is what
             once reported a deleted campaign as "API unreachable, check the server is
             running", sending an operator to restart a perfectly healthy server.'
```

## Invariants (cross-cutting — the refinement directives)

```yaml
invariants:
  I1_state_complete:  Every data-backed surface resolves loading to one of {live, empty, error}.
                      No control may rest in a non-terminal loading/probing state. A 401 in anon
                      resolves to the anon empty state, never to a permanent spinner.
  I2_no_raw_transport: Never render a transport error to the user ("<status> <path>", e.g.
                       "401 /api/v1/datasets"). Map every failure to a typed state message.
                       Raw status+endpoint strings are a hard block.
  I3_affordance_honest: A control that looks operable IS operable. Anything inert is rendered as
                        content (showcase/badge), never styled as a button/switch/field. No editable
                        input that silently discards what the user types.
  I4_auth_coherent:   anon never shows authed-only chrome (Log out); authed never shows anon CTAs
                      (Log in / Sign up). The two control sets are mutually exclusive by auth state.
  I5_no_anon_noise:   Anon fires no auth-gated request beyond the auth/me probe (consumers gate on
                      useAuth().status==='authed'). The browser logs failed requests itself — the app
                      can't swallow that — so the cure is not firing them. The auth/me 401 is the
                      accepted floor (it's the probe that decides anon vs authed).
  I6_run_state_server_owned: '"Is anything running?" has ONE server-owned answer: run_phase ∈
                      {running, gate} (hasLiveProducer, webapp/lib/run-phase.ts). paused is NOT
                      one — the worker has exited — so a parked campaign kept the jobs dock lit and
                      destroyed its all-quiet signal; a paused cycle stays reachable as a sidebar row
                      wearing its phase. detached means a dead producer (the heartbeat invariant,
                      architecture.md §0 State + persistence) and never renders as running.
                      Client-side connection loss (failed poll, offline, hidden tab) is presented as
                      connection state (offline / stale affordance) and MUST NOT impersonate a run
                      phase or unmount run controls while the last-known server phase is running.
                      Every "running" surface — the sidebar-edge jobs dock (and its phone stand-in,
                      the app bar back-arrow dot), workspace runningCycles — reads this one set AND
                      one shared ordering (what needs you first). A surface that RENDERS the
                      phase goes through a map TOTAL over RunPhase (runPhaseLabel, runPhaseAction):
                      testing `=== "running"` renders half the vocabulary as nothing, which is how
                      a gate-held run — blocked on the operator, first in that ordering — read as
                      an idle sidebar row.
                      COROLLARY (the time-ray). run_phase provably cannot express running vs
                      WEDGED: every await outlasting RUN_FRESH_S must heartbeat (heartbeat.py
                      states the rule, four callers), so a live cycle can never go stale and a
                      wedged process reads "running" forever. Freshness proves ATTACHMENT,
                      never PROGRESS. The ray head derives `wedged` from the other input —
                      progress = a non-heartbeat ledger append — gated on the server still
                      saying `running`, and it is a DISPLAY state: nothing writes it, it is not
                      a RunPhase member, and it must not become one. `gate` is excluded from
                      the test, because the origin gate legitimately heartbeats with zero
                      progress until a human decides, and it already has a state that says so.'
  I7_failure_traceable: 'Every user-visible failure is IDENTIFIED, CLASSIFIED and TRACEABLE.
                      IDENTIFIED — the API stamps `error_id` on every error envelope and logs
                      it under the same handle (one seam, main.py::_error_response), so a
                      report quotes an id instead of a wall-clock guess; the webapp keeps a
                      bounded incident ring (ids/codes/paths only, never measurements) behind
                      Account -> Copy diagnostics. CLASSIFIED — callers branch on
                      failureKind(err), never a bare catch; `transient` is the safe default,
                      so an unrecognised failure retries rather than destroying state.
                      TRACEABLE — a surface whose address the server says is GONE stops
                      polling it, names what happened (never "server unreachable" — that is a
                      different fact), and recovers to a live address rather than resting in a
                      dead one. Detection is the address''s OWN authoritative read: list
                      membership is NOT an existence test, because an L4 inner hop is absent
                      from /cycles and an archived campaign is absent from the active filter
                      while both are alive. Archived is not gone. The server side of this is
                      the same rule: `warming_up` means "no dashboard YET" and a missing cycle
                      dir means GONE, and one route must never answer both with the same body.'
  I8_floor_named:     'A rendered Δ NAMES which floor it cleared, and the two floors are not
                      interchangeable. ORIGIN is C0 — the campaign root, or a fork''s branch
                      point: `origin_accuracy` on the campaign index (ForestRows, PanelCellRow,
                      DatasetPickList, CandidatesCard), `ability_delta` (headline-stats,
                      run-summary), and run_card.flips'' per-sample rows. PARENT is the round''s
                      own floor — the origin at round 0, the prior winner after: every
                      `matched_parent_*` field, wherever it surfaces (ScoringInspector,
                      OuterSignalPanel, RoundFileView, run_card''s percent pair). The engine
                      elects on the parent (architecture.md §0), so a pane labelling a
                      `matched_parent_*` value "origin" states a comparison the run never made.
                      Two references may share a box only when BOTH are labelled — run_card is
                      the sanctioned case and says so at its own seam.'
```

## The two blocking gates

The only surfaces with a block here, because their constraints are legal and ordering rather
than rendering — a plausible edit undoes them and nothing fails.

```yaml
access_gate:   # components/onboarding/AccessGate.tsx — status==='authed' AND access_state==='pending'
  - anon NEVER sees this (no account yet) — I4.
  - it PRECEDES consent_gate and the two are mutually exclusive: consent attaches when someone is
    about to submit data, and a pending account cannot, so asking it to accept Terms would collect
    a consent for something it may not do.
  - reflects, never enforces — the dispatcher's capability gate already refuses a pending account's
    every command (it holds the empty set), so this adds no second check.
  - NO dismiss beyond sign-out: no ×, no backdrop-close, no ESC. There is nothing to agree to.
    Sign-out navigates even when the logout call fails — a dead button on a non-dismissable
    overlay strands the user.

consent_gate:  # components/onboarding/ConsentGate.tsx — access_state==='active' AND version mismatch
  - anon NEVER sees this (read-only preview submits no data → no consent needed) — I4.
  - Unticked on open. No pre-tick — GDPR/FADP affirmative consent.
  - the required version is server-owned (`me.terms_version`), never hardcoded client-side, so a
    TERMS_VERSION bump re-prompts without a frontend redeploy. A 409 re-probes /me.
  - NO dismiss: no ×, no backdrop-close, no ESC. Accept is the only exit.
```

## Surfaces — deliberately not here

There is no per-surface block. Every one that stood here described behavior already shipped,
none of it was cited by anything, and the copies had begun to drift from the strings they
quoted. Read a surface off its component; read the rules it must satisfy off the invariants
above and `webapp/CLAUDE.md`. Recover the old blocks from `git log` if a decision inside one
is ever needed.
