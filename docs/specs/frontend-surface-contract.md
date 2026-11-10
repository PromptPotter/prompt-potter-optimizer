# Frontend Surface Contract

What every surface in `webapp/` must do, per state: the invariants a PR is measured against and the
two blocking gates. It describes no surface — what a control renders is the component's.
`webapp/CLAUDE.md` owns implementation rules, `promptpotter-web/BRAND.md` the copy register.

## State vocabulary

Every data-backed surface defines its behavior in each state; resting in a non-terminal one is a
contract violation.

```yaml
states:
  anon:       not logged in — the public preview at /; demo content only.
  auth_empty: logged in, no campaign selected, a brand-new account included. Nothing is wrong,
              so no error styling, no alert role, no remediation the visitor cannot perform here.
  warming:    campaign selected, its cycle declares no wiring yet (warming_up:true).
  live:       campaign streaming dashboard.json.
  loading:    a fetch in flight — transient, MUST resolve to live/empty/error.
  error:      a fetch failed for a non-auth reason (5xx, network, parse).
  offline:    poll stale / server unreachable.
  gone:       the server ANSWERED 404 — deleted campaign, reaped .inner/ sandbox, reset store.
              Terminal; distinct from offline, which sends the operator to restart a healthy server.
```

## Invariants

```yaml
invariants:
  I1_state_complete:  loading resolves to one of {live, empty, error}; a 401 in anon resolves to
                      the anon empty state, never a permanent spinner.
  I2_no_raw_transport: never render "<status> <path>"; every failure maps to a typed state message.
  I3_affordance_honest: a control that looks operable IS operable; anything inert renders as content.
                      No editable input that silently discards what the user types.
  I4_auth_coherent:   anon never shows authed chrome (Log out); authed never shows anon CTAs.
  I5_no_anon_noise:   anon fires no auth-gated request beyond the auth/me probe (consumers gate on
                      useAuth().status==='authed'); the browser logs failed requests itself.
  I6_run_state_server_owned: '"Is anything running?" has ONE server-owned answer, the served
                      producer reading (domain/phases.py::ProducerReading — `producer.attached`
                      on the dashboard, `state.attached` on a /hierarchy course entry). A paused
                      cycle whose worker exited is NOT attached; detached never renders as running.
                      The browser holds no set of "live" phases and subtracts no clock from a stamp:
                      every duration arrives served (`silent_for_s`; an open call's, worded, in `held_call`). Connection loss
                      renders as connection state, never a run phase, and never unmounts run
                      controls. Every "running" surface reads this one set and one ordering; a
                      surface rendering the phase reads `RUN_PHASE_INFO` and offers the served
                      `admission` — testing `=== "running"` drops a gate-held run to idle. `isLive`
                      (poll.tsx) is the served `producer.appending`, never attachment. Progress
                      (ProducerState) is graded server-side and is no RunPhase member — owned by
                      persistence-and-state.md § Phase and liveness.'
  I7_failure_traceable: 'IDENTIFIED — the API stamps `error_id` on every error envelope and logs
                      it under the same handle (main.py::_error_response); the webapp keeps a
                      bounded incident ring (ids/codes/paths, never measurements) behind Account ->
                      Copy diagnostics. CLASSIFIED — callers branch on failureKind(err), never a bare
                      catch; `transient` is the default, so an unrecognised failure retries. TRACEABLE
                      — a surface whose address the server says is GONE stops polling it, names what
                      happened, and recovers to a live address. Detection is the address''s OWN read:
                      list membership is no existence test (an L4 inner hop is absent from
                      /hierarchy, an archived campaign from the active filter, both alive). Server
                      side: `warming_up` means no wiring yet, a missing cycle dir means GONE, and one
                      route never answers both with one body.'
  I8_floor_named:     'A rendered Δ NAMES its floor. ORIGIN is C0 — the served `RunStanding.vs_origin`
                      pair (masthead BEST, ForestRows, run-summary), `origin_accuracy`
                      on the origins listing, the course''s `facts.bench.vs_origin`. PARENT is the
                      round''s own floor — every `vs_reference` reading (searchpoint drill-in,
                      OuterSignalPanel, RoundFileView, run_card''s percent pair). The engine elects on
                      the parent (architecture.md § Origin, parent, and check-in), so a pane labelling
                      `vs_reference` "origin" states a comparison the run never made. Two references
                      share a box only when BOTH are labelled (run_card).'
  I9_pipeline_resolution_server_owned: 'What config a node RUNS has ONE answer: GET
                      /campaigns/{id}/pipeline?at=<subject>
                      (application/pipeline_resolve.py::resolve_pipeline_for_campaign; a draft takes
                      resolve_pipeline_for_draft on the draft response). useConnector''s `draft`
                      and `CampaignFacts.runs_with` are TRANSPORTS of it, never sources a row may
                      re-merge or diff. Every param carries a served `source`
                      (domain/pipeline_schema.py::ParamSource); a badge, fold or lock derived from a
                      client-side diff is the violation. WHY an axis is shut is served (`never_axis`),
                      and so are whether this account may steer it (`editable`) and its `role`.
                      A component takes IDENTITY props, never a prop re-routing which store it reads.
                      A NEST is dataset-scoped until a cell runs, then an inner campaign addressed
                      ?at=...;in=<hop>; the read is gated on owned_campaign alone. Per enumerable
                      axis `options` is the MENU, `permitted` what the gate allows
                      (`permitted_stated` says whether the campaign narrowed).'
  I10_poll_is_the_transport: 'Every data panel rides `dashboard.json` polling + the SSE ledger
                      tail; there is no live-state endpoint. One exemption: a ONE-SHOT TOPOLOGY read
                      whose invalidation signal is the poll — `GET /campaigns/{id}/pipeline` is the
                      only panel-feeding one. `POST /campaigns/{id}/fork-preview`
                      (`application/read_params.py::ForkPreviewParams`) is keyed
                      on the operator''s pending gesture, which no poll can invalidate.'
  I11_surface_parity: 'Whatever optimizer a campaign runs gets the same surfaces where the method
                      permits; where a feature cannot apply, the surface says so. A surface learns
                      the manifest from the served `optimizer` and which features apply from served
                      facts (`elects_on`, `run_limits.optimizer`), never a default or a name test.
                      What an optimizer says in its own words — the manifest''s `phases`, its
                      proposer''s `opening`, its members'' `round_facts`, its eliminator''s
                      `Elimination.reason`, its node `description`s — renders as served.'
  I12_progressive_reveal: 'A view reads at a GLANCE, its PANE and its DETAIL. The glance is the viewed
                      course''s served figure named by `shell/families.tsx::FAMILIES[f].glance`,
                      drawn once, by the masthead, as that view''s door; a missing row is composed in
                      `application/hierarchy.py`, never the browser, and an open view no figure names
                      gets a bare door. Open views are columns, newest first. The stage view
                      (`lib/prefs/stage.ts`) draws the served `lift` figure and the served `climb`
                      under it, nothing else.'
```

## The served hierarchy

**Every surface that lists, switches, docks or heads a run renders ONE served entry shape** —
`domain/hierarchy.py::HierarchyEntry`, minted by `application/hierarchy.py` alone, read by
`GET /hierarchy` (`lib/hooks/useRegistry.ts`, the `cycles` verb), discriminated on `level`. Campaign,
course and candidate wear the envelope (`label`, `tags`, `figures`, `lines`) over typed `facts`; a round
and a step are bare nodes. A `facts` field no reader names is deleted
(`scripts/complexity_ledger.py::BASELINE` `p_served` is the ratchet). The address grammar is
[`../operations/persistence-and-state.md`](../operations/persistence-and-state.md) § The hierarchy address.

- **Facts for code, said rows for people** (`domain/said.py::Said`): `figures`, `lines` and a course's
  `climb` (the lift's cost per search dollar, per candidate tested, per worked unit) are composed once
  in `application/hierarchy.py` and rendered as served, keyed by `key`; deriving a state survives
  nowhere in `webapp/lib/derivations`.
  <!-- probe: git grep -cE "entryFigures|mastheadFigures|runWord|livesLabel" -- webapp == 0 -->
  <!-- probe: git grep -cE "HierarchyStat\b|BenchStat\b|StatKey|stats: list" -- promptpotter == 0 -->
  <!-- probe: git grep -cE "buildForest|RunGroup|OriginGroup|campaignCard|courseStats|unitDisplayName|campaignLineParts" -- webapp == 0 -->
- **One tag exception, `mint`**: code branches on a course's kind exhaustively, so it is served typed
  as `facts.mint` and worded as the tag.
  <!-- probe: git grep -cE "tagValue\(.*mint" -- webapp == 0 -->
- **One run-state derivation.** `HierarchyState.of` packages `derive_run_state`; a campaign's `state`
  IS its line course's. `RunStatus.of`'s other caller is `evidence/head_to_head.py`.
  <!-- probe: git grep -cE "HierarchyState\.of\(" -- promptpotter == 2 -->
  <!-- probe: git grep -cE "RunStatus\.of\(" -- promptpotter == 2 -->
- **A run's word is the course's; the dashboard read is run CONTROL.** `state.status` rides the course
  entry alone (`lib/lineage.tsx::useViewedCourse`); `ServedDashboard` keeps what control acts on
  (`run_phase`, `admission`, `pause`, the clocked producer, spend, limits, the walk). The listing serves
  `attached` and no clock: it answers 304 between writes, so a seconds count would freeze.
- **A campaign is always served**, with a line saying so before its `CycleIndex` exists.
- **One poll**, over every lifecycle; the sidebar's filter is layout over `facts.lifecycle`.
  <!-- probe: git grep -cE "hierarchyRead\(" -- webapp/lib/hooks/useRegistry.ts == 1 -->
- **The genealogy is the same entry, read deep** — `GET /hierarchy/{address}?lens=&samples=&at=&at_cycle=`,
  bounded at `lineage_queries.py::MAX_COURSE_DEPTH`; no second route, no client fold. Drawing is
  [`store/CLAUDE.md`](../../promptpotter/infrastructure/store/CLAUDE.md) § The lineage tree's; the webapp
  keys every row on `address`.
  <!-- probe: git grep -cE "\}/tree" -- promptpotter/presentation == 0 -->
  <!-- probe: git grep -cE "def (build_lineage_tree|family_course_entries|lensed_tree|_fold_contributions)\(" -- promptpotter == 0 -->
  <!-- probe: git grep -cE "class (ArmNode|CourseLineage|ForkStamp)\b" -- promptpotter == 0 -->
  <!-- probe: git grep -cE "nodeKeyOf|nodeAddress|ownerOfNodeAddress" -- webapp == 0 -->
- **What only the subtree reads is a subtree TYPE, never a null in the listing** — `SubtreeCampaign` /
  `SubtreeCourse` carry what the 10 s poll must not pay for.
  <!-- probe: git grep -cE "deletable_stubs" -- promptpotter/application/hierarchy.py == 0 -->
- **A course's chronology is its STEPS, in a window, never `children`** —
  `GET /hierarchy/{course}/@?limit=&before=` (`family_ray_queries.py::build_family_ray`), paged back by
  `cursor_prev`; a bare heartbeat is the gap the next step's `gap_before_s` carries.
  <!-- probe: git grep -cE "class (RayItem|RayResponse)\b" -- promptpotter == 0 -->

## The two blocking gates

```yaml
access_gate:   # components/onboarding/AccessGate.tsx — status==='authed' AND access_state==='blocked'
  - the blocklist is its one trigger (shared/identity.py::AccessState); anon never sees it (I4).
  - precedes consent_gate; reflects, never enforces — the dispatcher already refuses a blocked account.
  - NO dismiss beyond sign-out; sign-out navigates even when the logout call fails.

consent_gate:  # components/onboarding/ConsentGate.tsx — access_state==='active' AND version mismatch
  - anon never sees it (I4). Unticked on open — GDPR/FADP affirmative consent.
  - the required version is server-owned (`me.terms_version`); a 409 re-probes /me.
  - NO dismiss: accept is the only exit.
```
