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
  - id: no_google_fallback
    do: No-Google-account path → "Open a GitHub issue to request beta access" (→ BRAND.supportUrl,
        the repo issues; whitelabel-overridable). No editable field that discards input.
    status: ok   # B4: email field + LinkedIn-Continue removed; GitHub-issue CTA; dead CSS pruned.
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
        anon: "Sign in to see your campaigns." (SignInPrompt).
        auth_empty: "No campaigns yet — start one."
    status: ok   # B2: anon → SignInPrompt; workspace poll gated on authed.
  - id: support
    do: Always-live link to help. Visible in every auth state.
    status: ok   # B2: <a href={BRAND.supportUrl}> → repo issues (NEXT_PUBLIC_SUPPORT_URL-overridable).
  - id: logout
    do: Call the logout endpoint, clear session, return to /login. Rendered ONLY when authed (I4).
    status: ok   # B2: <button> → postLogout()+redirect; rendered only when status==='authed'.
```

### Chat surface

```yaml
surface: chat
controls:
  - id: preview.toggle
    do: Show/hide the pipeline strip (input/connector/node/output). Input+output toggles move together.
    status: ok
  - id: preview.connector
    do: Resolve to a terminal chip state. No resolved backend (anon / no dataset) → "idle" +
        "no backend selected" (nothing is being probed). Resolved + probed → reachable / unreachable.
    status: ok   # B3: ConnectorInspector shows "idle" when connector==null; "probing…" only while a real probe is in flight.
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
    do: Coming-soon features — render as a disabled ui/Switch (role=switch, aria-disabled,
        aria-label "… (coming soon)") + a muted "Soon" pill. Legibly unavailable, not faux-operable.
    status: ok   # B4: extracted ui/Switch (locked variant); "Soon" pills.
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
        anon: "Sign in to view workspace verification runs." + Sign-in CTA (→/login).
        loading: spinner while status resolves.
        empty: "No runs yet."
        error (authed): "Couldn't load diagnostic runs — retry shortly." (never raw).
    status: ok   # B1: gated on useAuth().status; SignInPrompt; raw-401 string removed.
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
    status: ok   # B1: gated; needsAuth LoadState; raw-401 string removed.
  - id: close
    do: Close, restore focus, ESC + backdrop.
    status: ok
```

## Coverage

The authenticated + live-campaign surface (dashboard topstrip/fitness/lineage/
samples, What-If evaluator grid, scoring inspector with `round_NNNN.json`
drill-in, Verify diagnostic table, Files tree + JSON preview) is **verified** —
driven via `PROMPTPOTTER_AUTH=off` (`deps.py::resolve_identity` →
`registered_or_default_identity`, the CLI's resolver) against the operator's
real on-disk campaigns. Console clean (0 errors) across every tab; the connector
rests at a terminal `unreachable` when the backend is down; frozen units show
"UPDATED · Nh ago". No fixtures, no Docker, no spend.

Two states remain **un-exercised** (not contract gaps — just unreached here):
- `warming` (origin running, `dashboard.json` not yet written) — needs a live
  starting campaign; verify on the next real run.
- The real Google OIDC **login round-trip** — `AUTH=off` bypasses the redirect,
  so the post-login mount path is reachable only via the Dex harness
  (`dev/oidc-local/`), where it was driven end-to-end and the dashboard mounts
  clean.

The B0–B7 hardening campaign that drove the surface to this contract (anon
fires only the `auth/me` probe; full keyboard/a11y; one I5 leak in `FitnessPanel`
fixed) is shipped. Post-alpha parallel work surfaces in real use against these
same invariants: deep live-data edge cases, multi-campaign + Archived,
offline/stale, the L2/L3-terminal loading bug, whitelabel theme variants, and the
OIDC round-trip above.
