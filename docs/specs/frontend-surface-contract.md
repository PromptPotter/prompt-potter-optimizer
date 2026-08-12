# Frontend Surface Contract

Minimal, dual-read spec of every user-facing surface in `webapp/` — what each
control **must do**, per state. Companion to `webapp/CLAUDE.md` (implementation
invariants) and `BRAND.md` / `VOICE.md` (brand/copy). This file owns *behavior*: the
contract a PR is measured against, and the source of truth when reality drifts.

**How to read.** Humans: skim the invariants, then the `status:` tags (`ok` /
`gap` / `broken`) per control. Machines: each surface is one fenced `yaml` block;
parse `controls[]`. `do` = the contract (target behavior, already correct even
where unbuilt). `gap` = current divergence (omit when `ok`). State keys appear
only where behavior is non-obvious.

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
                      {running, gate, paused} (IN_FLIGHT_PHASES, webapp/lib/run-phase.ts). detached
                      means a dead producer (the heartbeat invariant, architecture.md §0 State +
                      persistence) and never renders as in-flight. Client-side connection loss
                      (failed poll, offline, hidden tab) is presented as connection state (offline /
                      stale affordance) and MUST NOT impersonate a run phase or unmount run controls
                      while the last-known server phase is in-flight. Every "running" surface — the
                      topbar jobs dock, the RemoteBar, workspace liveCycles — reads this one set AND
                      one shared ordering (executing before suspended). A surface that RENDERS the
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
```

## Surfaces

### Topbar — chrome, every tab

```yaml
surface: topbar
controls:
  - id: search.analytics
    do: Disabled until analytics ships; label states "coming soon".
    status: ok
  - id: tabs.{chat,dashboard,verify,files}
    do: Switch the main pane. Selected tab is the only [selected] one.
    status: ok
  - id: theme.toggle
    do: Swap light<->dark register; persist to localStorage promptpotter.theme; restore on load.
    status: ok
  - id: auth.{login,signup}
    do: Open the auth modal. Rendered only when anon (I4).
    status: ok
  - id: jobs.dock
    do: The OS-style dock of open units — Potter glyph when one unit is in flight, glyph + count
        badge when several; ABSENT when idle (absence IS the "all quiet" signal). Lists liveCycles
        (I6 membership + ordering). Ordered by WHAT NEEDS YOU, not by what is busy —
        gate > running > paused (lib/run-phase.ts::dockPriority): a gated unit makes no progress
        until the operator decides, so it leads. Both branches carry the phase class, so a held
        gate is distinguishable from a healthy run in the single-unit case too. Clicking an entry
        jumps the view to that cycle's dashboard. Reads server run_phase only — a client connection
        blip never changes the count.
    status: ok
  - id: remote_bar
    do: Play/pause/skip remote for the VIEWED cycle; mounted iff its server run_phase is in-flight
        (I6). Client connection loss dims/labels it stale — it never unmounts while the last-known
        server phase is in-flight. While the viewed path is DEEPER than one hop the controls are
        inert and say why: the command highway addresses one CycleHop with no `descend`, so an
        inner run cannot be commanded and firing anyway hit the outer cycle instead (I3).
    status: ok
  - id: remote_sample_lookahead_arm
    do: Arms sample look-ahead for the next round of scoring (2 samples in flight, ~half the
        wall clock). An ARM button, not a switch — the arming is spent by one round and the
        button unlights itself, so pressing it while lit is a cancel. Lit state reads
        dashboard.json::sample_lookahead > 1 (I6 — never a local boolean, which would stay lit
        after the round consumed it). Label carries the depth ("2×"), never colour alone. The
        title reports sample_lookahead_discards, the arming's running cost. Host-admin only: a
        non-admin identity 404s at the dispatcher, so the button is present but its press
        reports a failure rather than being hidden — the surface does not encode authority.
    status: ok
```

### Auth modal

```yaml
surface: auth_modal
controls:
  - id: google.oidc
    do: GET /api/v1/auth/login/google -> 307 to Google with state+nonce. redirect_uri origin MUST
        match the served origin (localhost vs 127.0.0.1 mismatch breaks the session cookie locally).
    status: ok
    gap: local redirect_uri is 127.0.0.1:8001 while preview is served on localhost:8001 — env-specific.
  - id: no_google_fallback
    do: No-Google-account path → "Open a GitHub issue to request beta access" (→ BRAND.supportUrl,
        the repo issues; whitelabel-overridable). No editable field that discards input.
    status: ok
  - id: legal.{privacy,terms,imprint}
    do: External links to brand legal pages; must resolve 200.
    status: ok
  - id: close
    do: Close modal, restore focus, close on ESC + backdrop.
    status: ok
```

### Consent gate — blocking, post-auth

```yaml
surface: consent_gate   # components/onboarding/ConsentGate.tsx, mounted in app/page.tsx
shows_when: status==='authed' AND me.terms_accepted_version !== me.terms_version
controls:
  - id: checkbox
    do: Unticked on open (no pre-tick — GDPR/FADP affirmative consent). Gates the accept button.
    status: ok
  - id: accept
    do: POST /api/v1/auth/accept-terms {version: me.terms_version} → server records the provable
        record (version + stamped timestamp) in user.json → refresh() re-probes /me → gate clears.
        Disabled until the box is ticked. A 409 (terms_version_stale) re-probes /me so the gate
        re-renders against current text.
    status: ok
  - id: legal.{terms,privacy}
    do: External links to the brand legal pages (the prose the checkbox refers to); must resolve 200.
    status: ok
invariants:
  - anon NEVER sees this (read-only preview submits no data → no consent needed) — I4.
  - NO dismiss: no ×, no backdrop-close, no ESC (a11y onClose is a no-op). Accept is the only exit.
  - the required version is server-owned (me.terms_version), never hardcoded client-side — one
    source, so a TERMS_VERSION bump re-prompts without a frontend redeploy.
```

### Sidebar — chrome, every view

```yaml
surface: sidebar
controls:
  - id: collapse
    do: Toggle collapsed/expanded; label flips Collapse<->Expand.
    status: ok
  - id: resize
    do: Drag the right-edge handle (or ←/→ when focused) to set sidebar width;
        clamped [160,480], persisted, default 200. Hidden when collapsed or in
        the mobile drawer.
    status: ok
  - id: filter
    do: Header sliders button opens a popover with the Active/Archived segment +
        a type-to-filter dataset picker; a dot marks a non-default filter and a
        summary line in the body clears it. Dataset picker shown only with 2+ datasets.
    status: ok
  - id: new_campaign
    do: On the chat tab, reset the thread in place to its empty first-run state
        (no modal). On any other tab, open the New campaign modal (see surface:
        new_campaign).
    status: ok
  - id: campaign_list
    do: List the forest as alternating COURSE → CANDIDATE tiers, at any depth.
        ORIGIN == C0, said ONCE. The campaign row and its ROOT course are one row (a
        campaign mints exactly one root; drawn apart, a fork-less campaign rendered as
        two rows with one score). The merge STOPS there — a root course's C0 stays an
        ordinary candidate row, first in its list. Folding it in too left the origin's
        own measurements with nowhere to live, and badged every course `C0`, which says
        nothing: every course is its own origin.
        Campaign chrome (⋯ menu, size hover) sits on the course row — archiving is a
        campaign verb.
        A course lists what it produced — `C0`, then `C1.1`, `C1.2`, … from the round
        trajectory (`/tree`, one conditional fetch per campaign; lazy on course-open,
        since every campaign wears a course row).
        A FORK IS A SIBLING COURSE, beside the candidates — not inside the one it was cut
        from. Borrowing an origin is not being contained by it, and nesting buried the
        fork a level too deep. The cut is a BADGE (`from C0`), never the name (it sits
        beside that candidate, so the name would double the neighbouring row); the label
        is its id tail. Only a steered cut names `from_candidate_id`; a round-level cut
        (divergence/rebase/sweep/diag) wears no badge rather than claim a candidate.
        A FORK WEARS NO C0 ROW — its badge already names the origin it borrowed, and it
        replays rather than re-derives it, so the row would restate the row above it and
        hold nothing.
        At L4 a candidate opens the inner campaigns that measured it — the recursion, and
        what makes a root's C0 the home of the origin's own runs. One candidate has as
        many as the panel has cells, identical but for `spawned_by.task`
        (`{dataset}/seed-N`), so an inner run wears its TASK; its candidate is the row it
        hangs under. A run only nests under a candidate row that EXISTS: `C0` on a fork,
        or a label whose round never closed, has none, so the run sits directly on the
        course rather than rendering nowhere at all.
        `won` needs a CONTESTED round — round 0 runs one candidate, so C0 beats nobody
        and wears no badge.
        NO per-tier framing: the tree is its indent rail and its labels. Boxing/colouring
        the tiers nested boxes inside boxes; the visual design is deferred, not half-done.
        A candidate has a row whether or not it spawned anything (a cache-hit candidate
        ran nothing). Filing is `spawned_by` only — never by order.
        The ● dot is PER-FOREST: green = this cycle's own `run_phase`; accent = its
        store's active pointer. Each depth resolves its own pointer (the session up top,
        the inner loop in a sandbox), so an inner row's dot answers for the sandbox — a
        global pointer names no cycle down there and the dot never lit.
        Clicking a candidate INSPECTS it, never navigates: a candidate is a tier, not a
        path hop, so it has no address. The row drives its COURSE's path plus the shared
        candidate axis (`setSelectionForCandidate` — the channel a fitness bar uses), and
        the inspector / samples / round axis follow it.
        Active/Archived + dataset narrow via the filter popover.
        anon: "Sign in to see your campaigns." (SignInPrompt).
        auth_empty: "No campaigns yet — start one."
    status: ok
  - id: support
    do: Always-live link to help. Visible in every auth state.
    status: ok   # supportUrl overridable via NEXT_PUBLIC_SUPPORT_URL
  - id: logout
    do: Call the logout endpoint, clear session, return to /login. Rendered ONLY when authed (I4).
    status: ok   # the same verb also lives in Account → Security (see surface: account)
  - id: account_button
    do: Topbar button (aria-label "Open account") opening AccountModal. Rendered ONLY when
        authed (I4) — see surface: account.
    status: ok
  - id: campaign_menu_toggle
    do: Mobile-only (aria-label "Open campaign menu") — opens the sidebar drawer.
    status: ok
```

### Chat surface

```yaml
surface: chat
controls:
  - id: preview.toggle
    do: Show/hide the hard-samples project preview (TargetPipelineHero's button,
        aria-pressed/aria-label "Show|Hide project preview"). The pipeline strip itself is
        NOT toggleable — it renders unconditionally.
    status: ok
  - id: preview.connector
    do: Resolve to a terminal chip state. No resolved backend (anon / no dataset) → "idle" +
        "no backend selected" (nothing is being probed). Resolved + probed → reachable / unreachable.
    status: ok   # idle when connector==null; "probing…" only during a real probe
  - id: preview.node.llm
    do: Expand to model & params; "declares no configurable params" when none.
    status: ok
  - id: composer.{attach,input,send}
    do: Gated on the INGEST FLOW phase, not on campaign+auth. input/send enabled only while
        flow.awaitingContext (send also needs non-empty text); attach disabled while flow.busy.
        Chat input is disabled outside ingest — selecting an active campaign does NOT enable it.
    status: ok
  - id: settings.{extended_thinking,web_search,code_execution,optimize_switch}
    do: Coming-soon features — render as a disabled ui/Switch (role=switch, aria-disabled,
        aria-label "… (coming soon)") + a muted "Soon" pill. Legibly unavailable, not faux-operable.
    status: ok   # optimize_switch is deliberately locked like its three neighbours —
                 # do not "restore" it to a live-looking toggle (I3)
  - id: welcome_illustration
    do: Empty chat (no thread yet) shows the welcome illustration, not a scripted fake
        conversation. There is no demo thread. Suppressed once ANY tail content exists —
        the live activity segment or the run card — never drawn over a bound campaign.
    status: ok
  - id: run_card
    do: Last item in the thread; renders only with a cycle bound and something measured.
        TWO untitled boxes — what the run cost and changed, then where it is in the data.
        LIVE it pins to the bottom of the scrollport and is height-capped (a tall card must
        not eat the conversation); stopped it un-pins and stays put. On the live→stopped
        edge the thread also gains ONE frozen `run` record per cycle, holding captured
        values — a `resume` must leave it saying what the finished run ended at. Every
        number is served (runSummary). The visible lift is the PERCENT pair — the shown
        candidate and matched_origin_accuracy on its own rows, "from X" absent when
        unstamped and an absent floor never drawn as 0. θ is jargon, so it rides the hover
        card behind that pair and only while `best` is shown: ability_delta is the
        incumbent's, per cycle, so captioning another candidate with it would be a lie.
        With no measured rate at all, θ takes the visible slot rather than vanishing.
        A panel CUT SHORT (scored_samples &lt; expected_samples — elimination or an
        escalation abort) trails the pair as `23/28` in the card's quietest weight, and
        the hover names it a partial panel, NOT a verdict. Silent when the panel is
        whole, and silent mid-round: expected_samples lands at round close, so this
        never doubles as a progress bar.
    status: ok
  - id: run_card.flips
    do: ONE line, and it is a partition that CLOSES — the reference NAMED, rows both sides
        measured, then origin-missed→shown-hits, then its regression twin, then the
        UNCHANGED remainder that makes them add up. The reference is stated because it is
        NOT the percent pair's: that floor is served matched_origin_accuracy, computed off
        RoundParent.results, so it is the candidate's parent (the origin at round 1, the
        prior winner after). These rows are the campaign origin — the only per-sample panel
        this app is served, since a round document's `results` carries the elected winner's
        rows when there is one. Both directions labelled in text. The denominator is
        stated once, never repeated per direction: two numerators over one total read as
        slices that fail to reach it, and the remainder is the largest group in every real
        run. Follows the searchpoint PICKER — one subject per box, the same one the label
        and the diff name; a control that moves two of three lines reads as broken, not as
        scoped. Shown searchpoint is round 0 → SILENT, because the verdict line already
        says the round elected nobody. Only the COUNTS are on the line; the ids and their
        before→after answers are in the hover card behind them, untruncated. Rows joined on
        sample_id only.
    status: ok
  - id: run_card.samples
    do: THREE rows, always, and they are a window on an axis — not a top-N list. Running,
        the axis is the declared scoring order and the cursor is the sample in flight
        (scoring now / just measured / next in line, and never the word "will" — PoBB can
        stop a candidate before the order is reached). Idle, the axis is the served
        hard_sample_rank order from rank 1. ▲/▼ slide the window one step and are DISABLED
        at the ends, never hidden; a pinned window follows the run again as soon as it is
        stepped back onto the cursor, and drops entirely when the candidate changes. The
        never/partly/always counts stack beside the rows and are themselves the control
        that opens the full table. Colour is a second carrier only — every bucket keeps its
        word, every row its label.
    status: ok
  - id: run_card.searchpoint
    do: best | latest | selected — same picker, same resolution, as the pipeline node
        detail (one hook). An UNAVAILABLE state is DROPPED, never rendered disabled, and a
        one-option group is not rendered at all (the surface's own label already names what
        is shown). `selected` appears only while a candidate is picked on another surface.
        The three absences read differently: loading / scoring-in-progress / nothing
        measured — an empty config table would read as "this program has no params".
    status: ok
```

### Account surface

`components/account/` — opened from the topbar account button. Holds the app's two
non-campaign mutations, so it is measured against I3/I4 like any other surface.

```yaml
surface: account
controls:
  - id: modal
    do: AccountModal opens from topbar (aria-label "Open account"); tabbed —
        Profile / Security / Preferences / Activity / About / Storage. Traps focus,
        restores on close, closes on ESC. Rendered ONLY when authed (I4).
    status: ok
  - id: preferences.demo_mode
    do: Real toggle — PATCH /auth/user-settings via patchUserSettings({demo_mode_enabled}).
        Reflect the SERVED value, never optimistic-only; on failure revert + surface the error
        (I2 — no raw transport string).
    status: ok
  - id: security.logout
    do: POST /auth/logout via postLogout, clear session, return to /login. The account modal's
        Security tab is the primary affordance; the sidebar carries the same verb (I4 — both
        rendered ONLY when authed).
    status: ok
  - id: about_unit
    do: Read brand identity from lib/brand.ts; version is SERVER-owned, read live from
        /api/v1/health — never hardcode it. Provenance renders "self-declared" until a signed
        credential exists; never render "verified" while BRAND.verification says otherwise.
    status: ok
  - id: storage_panel
    do: WorkspaceStoragePanel — resolve to live | empty | error (I1).
    status: ok
```

### Dashboard surface

```yaml
surface: dashboard
data_source: dashboard.json (poll 2s); round_NNNN.json lazy on drill-in. One source per data class.
controls:
  - id: topstrip.best_last
    do: Best/Last fitness from dashboard.json. "—" placeholders in auth_empty/warming.
    status: ok
  - id: candidates.card
    do: Fitness bars = the CHILDREN of the VIEWED lineage-tree node, the same rows the
        sidebar draws under it. The viewed node is NAVIGATION (`viewedPath` +
        `viewedCandidate`) and only the sidebar writes it; a bar click is INSPECTION
        (`SelectionContext.candidate`) and NEVER navigates - one slot for both made the
        chart its own input, so clicking a bar re-plotted it under the cursor. COURSE
        viewed - its candidates (one cycle, one `roundCandidates` spine; a fork course
        drops its borrowed C0, which lives on the parent) plus one bar per fork cut from
        it (served best, no election aggregates, a dashed dendrogram dot outside the round
        packing). L4 CANDIDATE viewed - the inner runs filed under it by `spawned_by`
        (title becomes the crumb back up a tier; dendrogram + What-If hidden, no candidate
        descent / evaluator namespace to draw). Every bar - candidate, fork or run - is a
        measured thing, so a click lights it, lights its dendrogram dot, and scopes the
        inspector/samples; none of them move the chart. The bracket dendrogram sits beneath
        the course view on the SAME x-axis.
    status: ok
  - id: lineage.forest
    do: Its OWN card, revealed by the toggle beside the dendrogram (and by a ⑂ fork mark, which
        opens it with that sibling expanded). The multi-cycle cladogram — the only surface that
        draws siblings. Empty note before round 1. Separate card because it shares no axis with
        the bars, so it must not be bound to their width.
    status: ok
  - id: candidates.metric
    do: ONE multi-select (Acc/Comp/θ) driving the bar series AND every node label in both views.
        Never empty. θ rides a right-hand axis and stays sparse (a missing θ is never a 0 bar).
    status: ok
  - id: candidates.menu
    do: The ⋯ overflow, lit while any member is active. Lens re-projects under an alternative
        criterion (served); What-If reveals evaluator checkboxes and becomes the master lens;
        Fixed sample set re-bases every bar on one set (off when the bars are courses — a run
        is not a scored row); Loaded from cache draws the dashed replayed-share line, off by
        default and NEVER disabled — a banked C0 is the usual replay, so greying out on it
        hides the case the control exists for.
    status: ok
  - id: samples
    do: Per-sample table — rendered inside the l1_score node inspector (click l1_score), not a
        standalone card. Empty note before scoring.
        PANEL MODE at L4: a self-optimizing course's samples are inner CAMPAIGNS, not scored rows —
        the round records only the cell's name (`{dataset}/seed-N`) with a null `is_hit`. Each cell
        renders as the run that measured it (phase, origin→best, opens the cycle), joined by
        `spawned_by` on `(candidate_label, task)` — never by order. TWO absences, not one: a
        cell the LIVE round hasn't named yet reads "pending" (the name lands with the round
        file, so there is nothing to join on); a NAMED cell with no stamped run reads "run not
        recorded". Conflating them accused the engine of losing provenance on every live round.
        The HIT/MISS tally + filter are HIDDEN here: nulls counted as misses read
        "HIT 0 / MISS 7" beside a 39% headline. Reads `leafIsL4` (the LEAF's backend_type), so a
        pp-self fork is in and a drilled-into inner run is out.
    status: ok
  - id: scoring.inspector
    do: Candidate drill-in. PRIMARY element is the one runnable-spec surface (NodeSurface, values
        mode, read-only) for the selected searchpoint — live round from dashboard.json, completed
        round from round_NNNN.json (no stitch); scalar stats + per-sample rows below; Steer & fork
        opens the editable twin. Loading note until the spec's round data lands.
    status: ok
  - id: optimizer.round_axis
    do: One circle per closed round + a LIVE pill, in the optimizer card's toolbar — the optimizer
        can only depict ONE round, so this is its scope, not a free-floating axis. Writes
        selection.round; the canvas, the node inspector and the samples view all follow it.
    status: ok
  - id: optimizer.node_strip
    do: checkin/l3_plan/l2_context/l1_generate/l1_score/l1_critique nodes for the VIEWED round
        (live -> dashboard.json; historical -> the audit twin, via the one useRoundNodes resolver).
        A historical round never pulses. Click opens the inspector. Idle when no campaign.
    status: ok
  - id: live_state.disclosure
    do: Collapsible raw dashboard.json + trend + score-frequency. "Waiting for first poll…" until data.
    status: ok
```

### Verify surface

```yaml
surface: verify
controls:
  - id: diagnostic_runs
    do: List diagnostic runs.
        anon: "Sign in to view workspace verification runs." + Sign-in CTA (→/login).
        loading: spinner while status resolves.
        empty: "No runs yet."
        error (authed): "Couldn't load diagnostic runs — retry shortly." (never raw).
    status: ok
```

### Files surface

```yaml
surface: files
controls:
  - id: tree
    do: Campaign file tree; clean empty state "No active campaign — pick one or start in a terminal."
    status: ok
  - id: preview_pane
    do: Render selected file (JSON formatted, .md as markdown, round files as scoreboard+table).
    status: ok
  - id: raw_dashboard_disclosure
    do: Collapsible raw dashboard.json; "Waiting for first poll…" until data.
    status: ok
```

### New campaign modal

```yaml
surface: new_campaign
controls:
  - id: body
    do: Dataset picker / ingest entry.
        anon: "Sign in to start a campaign." + Sign-in CTA (→/login).
        loading: "Loading your collection…"
        error (authed): "Couldn't load your collection — retry shortly." (never raw).
    status: ok
  - id: close
    do: Close, restore focus, ESC + backdrop.
    status: ok
```

## Coverage

The authenticated + live-campaign surface is verified against real on-disk
campaigns via the faithful harness (`PROMPTPOTTER_AUTH=off` — recipe:
[`../../webapp/CLAUDE.md`](../../webapp/CLAUDE.md) § Testing posture).

Two states remain **un-exercised** (not contract gaps — just unreached here):
- `warming` (origin running, `dashboard.json` not yet written) — needs a live
  starting campaign; verify on the next real run.
- The real Google OIDC **login round-trip** — `AUTH=off` bypasses the redirect,
  so the post-login mount path is reachable only via the Dex harness
  (`dev/oidc-local/`), where it was driven end-to-end and the dashboard mounts
  clean.

Still to validate against these invariants in real use: deep live-data edge cases,
multi-campaign + Archived, offline/stale, the L2/L3-terminal loading state, whitelabel
theme variants, and the OIDC round-trip above.
