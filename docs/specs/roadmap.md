# Roadmap

> **Beta.** Forward todo in execution order. Spec `Status:` lines are truth for what shipped.
>
> **Live now:** deployed at `https://app.promptpotter.dev` (Cloudflare Tunnel + systemd, OIDC + allowlist — see [`deploy-linux/`](../../deploy-linux/README.md)). Allowlist-gated but internet-reachable, and running on **one shared LLM key** from `.env` — so the sequence below is "harden a thing that's already serving users," not "prep before launch."

## Hard ordering (violate → rebuild)

Live constraints (the ones still ahead of us):

- **state-sync P3 (`GET /api/v1/live`) before any new webapp data panel** — chat state-queries, composite-fitness scatter, cross-user measurement panel. Anything built on `dashboard.json` polling is rewritten at the cutover. Data *rollups* (substrate-free) are exempt; only the rendered panel waits.
- **BYO per-user API keys — already overdue, not a future gate.** The beta is *live and serving allowlisted users on one shared `.env` key today*; every user spends the operator's quota/cost. This is a present liability, hence Lane A2.
- **HTTP-edge abuse protection scales with the allowlist.** The public surface (`/auth/*`, `/ui`, `/health`) is internet-reachable now; Cloudflare edge + allowlist + per-user `JobRegistry` quotas bound it. App-level HTTP rate-limiting (C6) becomes due the moment the allowlist is removed.

Historical (already satisfied — kept for the record):

- ~~state-sync P1 before spend reification~~ — spend shipped *first*; state-sync P1 now re-touches the `index.json` seam spend already modified (the double-touch the rule existed to prevent; <1 day, absorb it).
- ~~spend before composite P2–P4~~ — spend shipped; composite P1 (= the spend data) is satisfied.
- ~~identity Stage 0 before the 2nd connector~~ — both shipped.
- ~~control plane + identity Stage 1 before any chat write-path~~ — both shipped; the chat write-path is now **unblocked**, not gated.
- webapp hardening still built **once, multi-tenant** — identity Stage 1 is live, so any new endpoint is multi-tenant by default.

## Lane 0 — daily hygiene

Drain before feature work: [`code-debt-cleanup`](code-debt-cleanup.md) + [`state-sync-cleanup`](state-sync-cleanup.md). **state-sync P1 (Lane B1) is the do-first** (<1 day; unblocks Lane B2 / C panels).

## Sequence

The flat 17-row list this replaced was mostly *already shipped* (verdict P1, engine exit gate, spend, identity Stage 1, the control-plane command highway, the 2nd connector + config-driven lookup — see **Already shipped**). What remains is sequenced into lanes by dependency, not by milestone number. **Front priority = Lane A + the publication lane, concurrent** (they share no seam — beta web-stack vs. engine internals). Lane B then C follow.

### Lane A — beta usable for allowlisted web users, end-to-end (front)

Minimum for an external allowlisted user to log in (shipped), ingest their data, and launch a campaign without the CLI or hidden defaults. Ordered.

| # | Item | Status | Spec |
|---|---|---|---|
| A1 | React #185 post-login crash fix | **blocker — verify still reproducing** | known-issues memory |
| A2 | BYO per-user API keys | pending — **overdue: the live beta serves allowlisted users on one shared `.env` key today** (token HQ at `/auth/{quota-status,activity}` already shipped) | *needs slice spec* |
| A3 | Origin-resolution check-in | pending — kills hidden defaults + literal-column rejection so messy CSVs ingest | [`m10-origin-resolution-checkin`](m10-origin-resolution-checkin.md) |

### Lane B — foundations before any new web surface (no teardown)

Small ships (mostly <1 day). Must land before chat state-queries and new dashboard panels.

| # | Item | Status | Spec |
|---|---|---|---|
| B1 | state-sync P1 (identity collapse — absorbs the spend double-touch) | ✅ shipped 2026-05-30 | [`state-sync-cleanup`](state-sync-cleanup.md) |
| B2 | state-sync P2–P4 (per-cycle `dashboard.json`, **`GET /api/v1/live`**, sidecar delete) | ✅ shipped 2026-05-30 | [`state-sync-cleanup`](state-sync-cleanup.md) |

### Lane C — product differentiator + capability (after A + B)

| # | Item | Status | Spec |
|---|---|---|---|
| C1 | **Chat write-path** — wire the inert `ChatPane` to the *shipped* control-plane verbs; query state via `/api/v1/live` (∴ after B2/P3) | unblocked (control plane done) | [`m13-chat-first-user-web`](m13-chat-first-user-web.md) |
| C2 | Composite fitness P2–P4 (P1 = spend, done) — data rollup anytime; **scatter panel after B2/P3** | pending | [`m12-multi-connector`](m12-multi-connector.md) |
| C3 | L4 closure — inner-cycle dispatch + the L4 campaign + `proxy_lift_corr ≥ 0.6` re-validation (connector already registered) | pending | [`m12-multi-connector`](m12-multi-connector.md) |
| C4 | Cross-user measurement panel (after B2/P3) | pending | [`m13-chat-first-user-web`](m13-chat-first-user-web.md) |
| C5 | MCP server mode · user-editable `pipeline.json` in UI | pending | [`m12-plus-backlog`](m12-plus-backlog.md) |
| C6 | Public-service hardening (Docker, metrics, rate-limit, billing) — `/health` shipped; **pull rate-limit/metrics forward if the beta opens past the allowlist** | pending | [`m12-plus-backlog`](m12-plus-backlog.md) |
| C7 | Non-prompt targets + evolutionary operators · multimodal · research extensions | pending | [`m12-plus-backlog`](m12-plus-backlog.md) |

**Parallel lane — publication (front, concurrent with Lane A).** The engine exit gate (`rounds_to_95`) is shipped, so this is now *running experiments + write-up*, not engineering: BBEH + ablations run now; competitor numbers + L4 results wait on **C3**. → [`m11-publication-benchmarks`](m11-publication-benchmarks.md)

**Far-horizon (unscheduled).** Synthetic dataset from one hold-out ([`synthetic-data`](synthetic-data.md)) · AlphaEvolve code-harness · L3 fork authority → AlphaZero MCTS.

## Permanent contracts (constitutions, not steps)

- **Identity foundation** — OIDC wire + PostgreSQL RLS; three-stage staging. → [`ADR-0002`](../adr/0002-identity-foundation.md)
- **Spend + tenancy** — `TokenUsageRecord` on the canonical ledger via `emit_token_usage`. → [`ADR-0003`](../adr/0003-spend-and-tenancy.md)
- **Control plane** — Control-remote I/O kind; closed in/out sets ([`m12-api-openapi.yaml`](m12-api-openapi.yaml) + [`m12-events-asyncapi.yaml`](m12-events-asyncapi.yaml)). → [`ADR-0001`](../adr/0001-m12-control-plane.md)

## Captured — pending triage

- **Export / copy from dashboard** — one-click "copy" on the optimizer box (winning prompt + state); first slice of broader export. Not specced.
- **Origin check-in plain-language recap** — folded into [`m10-origin-resolution-checkin`](m10-origin-resolution-checkin.md) § Operator surface; pending review.
- **Webapp perf** — SWR/TanStack migration (needs vitest harness first), virtualize `HardSamplesTable`, strip redundant memos under React Compiler.

## Already shipped

Verified against code on 2026-05-30 (the prior 17-row sequence listed much of this as pending):

- **Engine.** Verdict-resolution P1 — `explore_weight` / `model_information_gain` / `predictive_hit_prob` removed (`c714bffd`). Engine exit gate — `rounds_to_95` computed (`application/optimization/l1/stats.py`, `runner/entry.py`).
- **Spend reification (= composite P1).** `emit_token_usage`, `TokenUsageRecord`, `LiveDashboardView._handle_token_usage`, `spend_total_used_usd` — all wired. → [`ADR-0003`](../adr/0003-spend-and-tenancy.md).
- **Identity Stage 1.** OIDC middleware mounted (`main.py:89`); real Google + GitHub code exchange (`infrastructure/identity/`); server-side sessions; allowlist; `/auth/{login,callback,me,logout,quota-status,activity}`; per-user quotas (`UserStore`, `user.json`). *Stage 0.5 caveat:* OIDC wire is live but RLS / SCIM multi-tenant data isolation is not yet enforced. → [`ADR-0002`](../adr/0002-identity-foundation.md).
- **Control-plane command highway.** `CommandDispatcher` + `routers/commands.py` + SSE `EventStreamView`; verb sets `LifecycleKind` / `CycleScopedKind` (fork/stop/pause/resume/change-spend-budget/start-run/…) / `WorkspaceBackendKind`. → [`ADR-0001`](../adr/0001-m12-control-plane.md).
- **Connector boundary + 2nd connector.** `termnorm` + `promptpotter` (self/L4) both registered; lookup config-driven via `pipeline.json::backend_type` (`bootstrap/wiring.py`), not hardcoded. Only the inner-cycle dispatch path for the L4 *run* remains (Lane C3).
- **Token HQ.** `/auth/quota-status` + `/auth/activity` (per-user spend/tokens/requests). `/health` endpoint (`main.py:115`).
- Onboarding lockout ([archived](archive/m10-onboarding-lockout.md)) · Ingest Slice 1 (CSV, `DraftCampaign`, `POST /datasets/ingest`, `mint-campaign-from-draft`, `TenantDatasetStore`, `IngestPane`) · webapp read-only surface (`/ui`).

Reference: [`code-debt-cleanup`](code-debt-cleanup.md) (living debt backlog).

## Non-functional requirements

| Requirement | Target |
|---|---|
| Single evaluation (500 items) | < 10 min |
| Full run (5 iters × 500 items) | < 60 min |
| Project store per campaign | < 10 MB |
| LLM providers | OpenAI-compatible (Groq default) |
| Python | 3.13 |
| Crash recovery | incremental `.partial.jsonl`; resume cache-hits prior |
