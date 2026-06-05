# docs/specs — Specs Index

**Read [`docs/architecture.md`](../architecture.md) first** — §0 is the shape, §0.5 the load-bearing surface.

> The `Mxx-` prefixes are stable file identifiers, **not execution order.** Order
> is the phase ladder in [`roadmap.md`](roadmap.md) (Phase A solo-user loop →
> B foundation spine → C connectors/L4 → D chat web; publication a parallel lane).
> Per-spec `Status:` lines are the source of truth for what shipped.

## Live

| Spec | What |
|---|---|
| [verdict-resolution](verdict-resolution.md) | (M10, active) The single statistical model behind both the live adaptive queue mechanism and the persisted `hard_samples_*.json` ranking. Drops `explore_weight`. Predecessor at [`archive/bayesian-sample-picker.md`](archive/bayesian-sample-picker.md). |

## Permanent contracts (constitutions, not roadmaps)

Permanent specs that stay alive after their target milestone ships. Items don't archive — they get certified and the prose moves into `docs/developer/` / `docs/operations/`, with checkboxes flipping in place. The spec is the perpetual staging area; the docs layer is the certified history. The `m12` / ADR identity on these files is fixed — never rename them.

| Spec | What |
|---|---|
| [ADR-0001 m12-control-plane](../adr/0001-m12-control-plane.md) | **Permanent system-networking contract** (MADR format). Defines the Control-remote I/O kind (§0-amended), the closed inbound + outbound sets ([`m12-api-openapi.yaml`](m12-api-openapi.yaml) + [`m12-events-asyncapi.yaml`](m12-events-asyncapi.yaml)), the Profile gradient (A-E), and the 20-item security checklist every M12-onward interactive PR is measured against. Drift detector: [`tests/test_control_plane_drift.py`](../../tests/test_control_plane_drift.py). |
| [ADR-0002 identity-foundation](../adr/0002-identity-foundation.md) | **Permanent multi-tenancy front door** (MADR format). OIDC wire + PostgreSQL RLS data + SCIM 2.0 internal model; three-stage staging (Stage 0 single-operator shipped → Stage 1 OIDC-client SaaS → Stage 2 OIDC-provider giant); six no-drift gates (`tests/test_identity.py` covers #3/#4/#6 at Stage 0). Every multi-tenant downstream is a consumer. |
| [ADR-0003 spend-and-tenancy](../adr/0003-spend-and-tenancy.md) | **First payload riding the identity seam** (MADR format). Token + cost telemetry rides the canonical `events.jsonl` ledger as `TokenUsageRecord` via kwargs-only `emit_token_usage` over `_CYCLE_LEDGER` ContextVar; `LiveDashboardView._handle_token_usage` sole writer of `dashboard.json::spend`; halt probe is a clean property accessor. The template every future `emit_*` per-call telemetry follows. |
| [frontend-surface-contract](frontend-surface-contract.md) | **Permanent webapp behavior contract.** What every user-facing control must do, per auth/data state (anon/auth_empty/warming/live/loading/error/offline), as parseable per-surface YAML + five invariants (state-complete · no-raw-transport · affordance-honest · auth-coherent · no-anon-noise). The source of truth when UI reality drifts; every user-facing PR is measured against it. Companion to [`../../webapp/CLAUDE.md`](../../webapp/CLAUDE.md) (impl invariants) + [`../../BRAND.md`](../../BRAND.md) / [`../../VOICE.md`](../../VOICE.md) (brand/copy). |

## Forward direction

[roadmap.md](roadmap.md) is the front door. Specs below describe direction-of-travel, not chapter-and-verse implementation:

Execution order is the [roadmap.md](roadmap.md) sequence, not the `Mxx-` filenames. Where each spec lands:

- Beta-usability: [origin-resolution check-in](m10-origin-resolution-checkin.md) · [BYO per-user API keys](m10-byo-keys.md) (spec only) · [verdict-resolution](verdict-resolution.md) (active) · [prompt-iteration framework](m10-prompt-iteration-framework.md)
- Foundation spine: [state-sync cleanup](state-sync-cleanup.md) (P1 first — before spend reification) + the three permanent contracts above
- Web payoff + platform: [multi-connector](m12-multi-connector.md) (composite fitness, then connectors/L4) · [operator-steered fork](m12-operator-steered-fork.md) (HITL steer: stop → edit a searchpoint → fork-continue; design-only) · [chat-first user web](m13-chat-first-user-web.md) (Install/User/Project nouns on OIDC claims, Stage 2 considered)
- Expansion + parallel publication lane: [m12+ backlog](m12-plus-backlog.md) · [publication benchmarks](m11-publication-benchmarks.md) · far-horizon [synthetic-data](synthetic-data.md)

## Reference (capability specs, not on the roadmap)

| Spec | Status | What |
|---|---|---|
| [code-debt-cleanup](code-debt-cleanup.md) | REFERENCE | Perpetual living backlog of bloat hotspots; Tiers 0–6 + polish arcs A–E + audits 1–3 closed. M13+ intentional UI placeholder registry is permanent reference. |

## Archive

[`archive/`](archive/) holds done + superseded specs. Recent moves:

- [onboarding lockout](archive/m10-onboarding-lockout.md) — invite-gated front door for the live beta; shipped 2026-05-27.
- [hard-sample-sorter](archive/hard-sample-sorter.md) — δ_s leaderboard Phase 1 shipped; phases 2–3 captured in [`m12-plus-backlog.md`](m12-plus-backlog.md).
- [security-audit](archive/security-audit.md) — first hardening pass complete 2026-05-05; deferred items embedded in [`m11-publication-benchmarks.md`](m11-publication-benchmarks.md) (endpoint hardening) and [`0001-m12-control-plane.md`](../adr/0001-m12-control-plane.md) (tenancy + auth).

Plus M0–M10 cleanup arcs, webapp display-source unification, bayesian-sample-picker, dispatch-prompt-budget, rasch-validation-plan, and M9 / M11 / M12 ancestors. Read for historical context.

Cross-repo: Proper Step Loop → backend repo `docs/spec/proper-step-loop.md`.
