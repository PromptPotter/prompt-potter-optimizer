# Frontend Surface Contract

What every user-facing surface in `webapp/` **must do**, per state. This file owns *behavior* —
the cross-cutting **invariants** and the two blocking gates, the rules a PR is measured against and
that a plausible edit would silently undo. It does **not** describe surfaces: what a control
renders is owned by the component, and a doc that copies rendered strings is a stale screenshot,
not a contract. `webapp/CLAUDE.md` owns the implementation rules, `promptpotter-web/BRAND.md` the
brand and copy register.

## State vocabulary

Every data-backed surface MUST define its behavior in each of these. A surface
sitting in a non-terminal state (a spinner that never resolves) is a contract
violation, not a cosmetic issue.

```yaml
states:
  anon:      not logged in — the public preview at /. Demo/showcase content only.
  auth_empty: 'logged in, no active campaign selected — including a brand-new account that
             has never run one. Nothing is wrong in this state, so no surface may present it
             as a failure: no error styling, no alert role, and no remediation the visitor
             cannot perform where they are standing. Same distinction `gone` draws below,
             one state earlier: collapsed into offline, it meets a first-run account with a
             critical alert telling it to go run a CLI command.'
  warming:   campaign selected, its cycle has declared no wiring yet (warming_up:true).
  live:      logged in, campaign streaming dashboard.json.
  loading:   a fetch is in flight — transient, MUST resolve to live/empty/error.
  error:     a fetch failed for a non-auth reason (5xx, network, parse).
  offline:   poll stale / server unreachable.
  gone:      'the server ANSWERED and says this address does not exist (404) — deleted
             campaign, reaped .inner/ sandbox, reset store. Terminal, not retryable.
             Distinct from offline on purpose: read as offline, a deleted campaign sends
             the operator to restart a perfectly healthy server.'
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
  I6_run_state_server_owned: '"Is anything running?" has ONE server-owned answer: the served
                      producer reading (domain/phases.py::ProducerReading — `producer.attached`
                      on the dashboard, `state.attached` on a /hierarchy course entry). A paused
                      cycle whose worker has exited is NOT attached (it stays a sidebar row wearing
                      its phase); detached never renders as running. The browser holds no set of
                      "live" phases and subtracts no clock from a served stamp — every duration
                      arrives served (`silent_for_s`, `open_for_s`). Connection loss renders as
                      connection state, never as a run phase, and never unmounts run controls.
                      Every "running" surface (the jobs dock, its phone dot, the registry''s
                      `running`) reads this one set and one shared ordering; the dock adds the
                      viewer''s own scoring passes (machine-status `mine`). A surface rendering the
                      phase reads `RUN_PHASE_INFO`, TOTAL over RunPhase, and offers the served
                      `admission` — testing `=== "running"` drops a gate-held run to an idle row.
                      `isLive` (poll.tsx) is the served `producer.appending`, false at the gate,
                      and never substitutes for attachment. Progress (ProducerState: live, idle,
                      wedged) is graded server-side beside the phase and is never a RunPhase
                      member — owned by persistence-and-state.md § Liveness.
                      I9 is the same discipline applied to node config.'
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
                      from /hierarchy and an archived campaign is absent from the active filter
                      while both are alive. Archived is not gone. The server side of this is
                      the same rule: `warming_up` means "no wiring declared yet" and a missing cycle
                      dir means GONE, and one route must never answer both with the same body.'
  I8_floor_named:     'A rendered Δ NAMES which floor it cleared, and the two floors are not
                      interchangeable. ORIGIN is C0 — the campaign root, or a fork''s branch
                      point: the served `RunStanding.vs_origin` pair, both rates on the
                      origin panel (the masthead''s BEST, ForestRows, PanelCellRow,
                      run-summary) and never either member''s own level; `origin_accuracy` on
                      the origins listing (DatasetPickList), the course's
                      `BenchStat.vs_origin` (headline-stats), and run_card.flips'' per-sample rows. PARENT is the round''s
                      own floor — the origin at round 0, the prior winner after: every
                      `vs_reference` reading, wherever it surfaces (the searchpoint drill-in,
                      OuterSignalPanel, RoundFileView, run_card''s percent pair). The engine
                      elects on the parent (architecture.md § Origin, parent, and check-in), so
                      a pane labelling a `vs_reference` value "origin" states a comparison
                      the run never made.
                      Two references may share a box only when BOTH are labelled — run_card is
                      the sanctioned case and says so at its own seam.'
  I9_pipeline_resolution_server_owned: 'What config a node RUNS in a campaign has ONE
                      server-owned answer: GET /campaigns/{id}/pipeline?at=<subject>, resolved by
                      application/pipeline_resolve.py::resolve_pipeline_for_campaign. A check-in
                      draft takes its sibling (resolve_pipeline_for_draft), carried on the draft
                      response, so StaticConnectorProvider and `CampaignFacts.runs_with`
                      (campaign_runs_with) are TRANSPORTS of one answer, never sources a row may
                      re-merge or diff. The browser never joins a VALUE from one store onto a
                      SCHEMA from another. Every param carries a served `source`
                      (domain/pipeline_schema.py::ParamSource), so a badge, fold, lock glyph or
                      "(current)" option derived from a client-side diff is the violation — a
                      resolved config carries every param, and `value !== baseValue` at the
                      addressed subject compares a number with itself. WHY an axis is shut is
                      served too (`never_axis`), never inferred from a key name. A component takes
                      IDENTITY props, never a prop that re-routes which store it reads. Two
                      deliberate boundaries: (1) a NEST is dataset-scoped until a cell runs, then
                      it is an inner campaign addressed ?at=...;in=<hop>; (2) this read is gated on
                      owned_campaign alone — a demo-mode or shared-origin read is its own
                      decision. Permission is served twice per enumerable axis: `options` the
                      MENU, `permitted` what the gate allows (`permitted_stated` says whether the
                      campaign narrowed); `narrow` REPLACES `param_allowed_values`.'
  I10_poll_is_the_transport: 'Every webapp data panel builds on `dashboard.json` polling + the
                      SSE ledger-tail — the design, not an interim seam; there is no live-state
                      endpoint. One exemption: a ONE-SHOT TOPOLOGY read may sit beside the poll
                      when the poll is its invalidation signal, not its transport.
                      `GET /campaigns/{id}/pipeline` is the only panel-feeding one (a resolution
                      cannot change without a new cycle or searchpoint, both announced by
                      `dashboard.json`). A panel whose content changes while nothing else does
                      rides the poll. `POST /campaigns/{id}/fork-preview`
                      (presentation/api/routers/campaigns/manifests.py::preview_fork_steer) sits
                      outside this rule: it is keyed on the operator''s pending gesture, which no
                      poll can invalidate.'
  I11_surface_parity: 'Whatever optimizer a campaign runs gets the same surfaces wherever the
                      method permits; where a feature cannot apply, the surface says so. A surface
                      learns the manifest from the served `optimizer` (CampaignPipelineResponse,
                      CampaignRunsWith) and which features apply from served facts (`elects_on`,
                      `run_limits.optimizer`), never from a default or a name test; the check-in
                      picks from `GET /optimizers`. What an optimizer says in its own words rides
                      its declarations — the manifest''s `phases`
                      (application/optimizer_manifest.py), its lead proposer''s `opening`
                      and its members'' `round_facts` (optimizers/nodes.py), its eliminator''s
                      `Elimination.reason` (domain/arms.py::arm_elimination) and its
                      manifest''s node `description`s — rendered as served.'
```

## The served hierarchy

**Every surface that lists, switches, docks or heads a run renders ONE served entry shape** —
`domain/hierarchy.py::HierarchyEntry`, minted by `application/hierarchy.py` alone and read by
`GET /hierarchy` (the webapp's `lib/registry.tsx`, the `cycles` verb), discriminated on `level`.
A level a row or a card renders — campaign, course, candidate — wears the envelope (`label`,
`lede`, `caveat`, `tags`, `stats`); a round and a step are nodes (`address`, `parent`, `facts`),
since an envelope nothing renders is an empty field on every poll and every generated type. A
level that gains a row moves under `_HierarchyEntry`. A generic row, hover card, switcher entry or
dock chip reads the envelope and never opens `facts`, and a `facts` field no reader names is
deleted (`scripts/complexity_ledger.py::BASELINE` `p_served` is the ratchet). The address grammar is
[`../operations/persistence-and-state.md`](../operations/persistence-and-state.md) § The hierarchy address.

- **Tags and stats are the rendering, worded on the server, in served order**, each clause at one
  site; `stats[0]` is the row's headline. Grouping, filtering on a tag and formatting a time are
  layout; wording a number, ordering stats or deriving a state survives nowhere in
  `webapp/lib/derivations`.
  <!-- probe: git grep -cE "rounds are won on" -- promptpotter/application/hierarchy.py == 1 -->
  <!-- probe: git grep -cE "buildForest|RunGroup|OriginGroup|campaignCard|courseStats|unitDisplayName|campaignLineParts" -- webapp == 0 -->
- **One declared tag exception, `mint`**: code branches on a course's kind exhaustively and a tag's
  value is an untyped string, so the kind is served twice — typed as `facts.mint`, worded as the tag.
  <!-- probe: git grep -cE "tagValue\(.*mint" -- webapp == 0 -->
- **One run-state derivation.** `HierarchyState.of` packages `derive_run_state`, once per course and
  once for a line course with no mint record; a campaign's `state` IS its line course's.
  `RunStatus.of`'s other caller is `evidence/head_to_head.py`, whose subject is an arm.
  <!-- probe: git grep -cE "HierarchyState\.of\(" -- promptpotter == 2 -->
  <!-- probe: git grep -cE "RunStatus\.of\(" -- promptpotter == 2 -->
- **A run's word is the course's; the dashboard read is run CONTROL.** `status` and `next_step`
  ride the course entry alone (`lib/lineage.tsx::useViewedCourse`), built off the HEAD index under
  any moment or mask, so they are one value everywhere. `ServedDashboard` declares neither; it keeps
  what control acts on and only the head measures — `run_phase`, `admission`, `pause`, the clocked
  producer, spend, limits, the walk. Read under a lens, a sample set or a moment, every course
  carries a `caveat` naming which (`hierarchy_subtree.py::_read_under`). The listing serves
  `attached` and no clock: it answers 304 between writes, so a seconds count would freeze.
- **A campaign is always served**: one whose line course has no `CycleIndex` yet lists with its
  `caveat` saying so, never dropped.
- **One poll**, over every lifecycle; the sidebar's filter is layout over `facts.lifecycle`.
  <!-- probe: git grep -cE "hierarchyRead\(" -- webapp/lib/registry.tsx == 1 -->
- **The genealogy is the same entry, read deep** — `GET /hierarchy/{address}?lens=&samples=&at=&at_cycle=`,
  bounded at `lineage_queries.py::MAX_COURSE_DEPTH`; no second genealogy route, no client fold. How a
  fork, a cut and a retained individual are drawn — owned by
  [`store/CLAUDE.md`](../../promptpotter/infrastructure/store/CLAUDE.md) § The lineage tree; the webapp
  renders the served tags and keys every row on `address`.
  <!-- probe: git grep -cE "\}/tree" -- promptpotter/presentation == 0 -->
  <!-- probe: git grep -cE "def (build_lineage_tree|family_course_entries|lensed_tree|_fold_contributions)\(" -- promptpotter == 0 -->
  <!-- probe: git grep -cE "class (ArmNode|CourseLineage|ForkStamp)\b" -- promptpotter == 0 -->
  <!-- probe: git grep -cE "nodeKeyOf|nodeAddress|ownerOfNodeAddress" -- webapp == 0 -->
- **The sidebar draws campaign → course → candidate → inner campaign**, rounds flattened into the
  forest's columns, and a campaign with one course elides that course's row.
- **What only the subtree reads is a subtree TYPE, never a null in the listing** —
  `SubtreeCampaign` / `SubtreeCourse` carry what the 10 s poll must not pay for
  (`CampaignStore.deletable_stubs` scans every sibling's ledger); a listing null would read as "none".
  <!-- probe: git grep -cE "deletable_stubs" -- promptpotter/application/hierarchy.py == 0 -->
- **A course's chronology is its STEPS, in a window and never as `children`** —
  `GET /hierarchy/{course}/@?limit=&before=` (`family_ray_queries.py::build_family_ray`), its forks and
  inner runs merged by time and paged back by `cursor_prev`. A step's `parent` is its course, since one
  ledger interleaves neighbouring rounds; a bare heartbeat is no step but the gap the next one's
  `gap_before_s` carries, so a long open call never serves an empty window.
  <!-- probe: git grep -cE "class (RayItem|RayResponse)\b" -- promptpotter == 0 -->

## The two blocking gates

The only surfaces with a block here, because their constraints are legal and ordering rather
than rendering — a plausible edit undoes them and nothing fails.

```yaml
access_gate:   # components/onboarding/AccessGate.tsx — status==='authed' AND access_state==='blocked'
  - the blocklist is its one trigger (shared/identity.py::AccessState is active | blocked); anon
    NEVER sees it — I4.
  - it takes precedence over consent_gate: a blocked account cannot submit, so has nothing to
    consent to.
  - reflects, never enforces — the dispatcher's capability gate already refuses a blocked
    account's every command (it holds the empty set).
  - NO dismiss beyond sign-out: no ×, no backdrop-close, no ESC. Sign-out navigates even when the
    logout call fails — a dead button on a non-dismissable overlay strands the user.

consent_gate:  # components/onboarding/ConsentGate.tsx — access_state==='active' AND version mismatch
  - anon NEVER sees this (read-only preview submits no data → no consent needed) — I4.
  - Unticked on open. No pre-tick — GDPR/FADP affirmative consent.
  - the required version is server-owned (`me.terms_version`), never hardcoded client-side, so a
    TERMS_VERSION bump re-prompts without a frontend redeploy. A 409 re-probes /me.
  - NO dismiss: no ×, no backdrop-close, no ESC. Accept is the only exit.
```
