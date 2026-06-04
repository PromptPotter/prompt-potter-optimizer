# Frontend Surface Contract

Minimal, dual-read spec of every user-facing surface in `webapp/` — what each
control **must do**, per state. Companion to `webapp/CLAUDE.md` (implementation
invariants) and `.impeccable.md` (brand/copy). This file owns *behavior*: the
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
  I5_console_clean:   Expected anon 401s (auth/me, backends, diagnostic-runs probed before login)
                      are swallowed to typed results by the fetch layer — not surfaced as console errors.
```

## Surfaces

### Topbar — chrome, every tab

```yaml
surface: topbar
controls:
  - id: search.analytics
    do: Disabled until analytics ships; label states "coming soon".
    status: ok
  - id: tabs.{chat,dashboard,verify}
    do: Switch the main pane. Selected tab is the only [selected] one.
    status: ok
  - id: theme.toggle
    do: Swap light<->dark register; persist to localStorage promptpotter.theme; restore on load.
    status: ok
  - id: auth.{login,signup}
    do: Open the auth modal. Rendered only when anon (I4).
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
  - id: email.continue
    do: Email beta path. EITHER wire email sign-in, OR drop the editable field and present a single
        explicit button "Ask about beta access on LinkedIn" (I3). An editable email box whose value
        is discarded is a dark-pattern smell.
    status: broken
    gap: email field accepts input but Continue is a static link to a personal LinkedIn URL, ignoring input.
  - id: legal.{privacy,terms,imprint}
    do: External links to brand legal pages; must resolve 200.
    status: ok
  - id: close
    do: Close modal, restore focus, close on ESC + backdrop.
    status: ok
```

### Sidebar — chrome, dashboard/files

```yaml
surface: sidebar
controls:
  - id: collapse
    do: Toggle collapsed/expanded; label flips Collapse<->Expand.
    status: ok
  - id: new_campaign
    do: Open the New campaign modal (see surface: new_campaign).
    status: ok
  - id: campaign_list
    do: List campaigns under Active/Archived tabs.
        anon: empty state "Sign in to see your campaigns" (resolve the 401 — I1).
        auth_empty: "No campaigns yet — start one."
    status: gap
    gap: rests on "loading…" permanently in anon (both tabs) — never resolves (violates I1).
  - id: support
    do: Always-live link to help (docs/contact). Visible in every auth state.
    status: broken
    gap: inert <div>, no handler/href — does nothing.
  - id: logout
    do: Call the logout endpoint, clear session, return to anon. Rendered ONLY when authed (I4).
    status: broken
    gap: inert <div>, no handler; also rendered while anon alongside Log in/Sign up (violates I4).
```

### Chat surface

```yaml
surface: chat
controls:
  - id: preview.toggle
    do: Show/hide the pipeline strip (input/connector/node/output). Input+output toggles move together.
    status: ok
  - id: preview.connector
    do: Probe the backend; resolve to a terminal chip state {connected, unreachable, unauthorized}.
        anon: "Sign in to connect" — never an indefinite spinner (I1).
    status: gap
    gap: rests on "probing…" forever in anon (poll self-halts after 3 tries, but the chip never resolves).
  - id: preview.node.llm
    do: Expand to model & params; "declares no configurable params" when none.
    status: ok
  - id: composer.{attach,input,send}
    do: Enabled only with an active campaign + auth; disabled otherwise.
    status: ok
  - id: settings.optimize_switch
    do: Real toggle for "Optimize prompt while using (Beta)".
    status: ok
  - id: settings.{extended_thinking,web_search,code_execution}
    do: If these are operable settings, render them as real switches with handlers. If they are a
        feature showcase for anon preview, render as content/badges, NOT faux toggle rows (I3).
    status: broken
    gap: styled like the toggle rows but are inert (no switch role, no pointer, no handler).
  - id: demo_thread
    do: Static scripted conversation shown in anon to illustrate the product. Clearly non-live.
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
  - id: fitness.score_toggle
    do: Composite <-> What-If; What-If reveals evaluator checkboxes to preview alternative scoring.
    status: ok
  - id: lineage.tree
    do: Fork+candidate cladogram from rounds[]; empty note before round 1.
    status: ok
  - id: samples
    do: Per-sample table once scoring starts; empty note otherwise.
    status: ok
  - id: optimizer.node_strip
    do: checkin/l3_plan/l2_context/l1_generate/l1_score/l1_critique nodes; click opens inspector
        (needs round_NNNN.json). Dataset node disabled until ingest. Idle when no campaign.
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
        anon: "Sign in to view verification runs" (I1/I2).
        empty: "No runs yet."
    status: broken
    gap: renders raw "Failed to load diagnostic runs: 401 /api/v1/diagnostic-runs" (violates I2).
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
        anon: "Sign in to start a campaign" + a CTA to the auth modal (I1/I2).
    status: broken
    gap: entire body is the raw string "401 /api/v1/datasets" (violates I2); modal otherwise empty in anon.
  - id: close
    do: Close, restore focus, ESC + backdrop.
    status: ok
```

## Coverage gap

The authenticated + live-campaign surface (real dashboard data, file tree, node
inspectors, Verify runs, working Log out, campaign creation) is **unverified** —
it is gated behind invite-only Google OIDC + a running campaign. The contract
above for `live`/`warming` states is specified from intent, not yet exercised.
