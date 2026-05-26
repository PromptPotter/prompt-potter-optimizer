# docs/specs — Specs Index

**Read [`docs/architecture.md`](../architecture.md) first** — §0 is the shape, §0.5 the load-bearing surface.

## Live

| Spec | What |
|---|---|
| [verdict-resolution](verdict-resolution.md) | The single statistical model behind both the live adaptive queue mechanism and the persisted `hard_samples_*.json` ranking. Drops `explore_weight`. Its predecessor lives at [`archive/bayesian-sample-picker.md`](archive/bayesian-sample-picker.md). |

## Forward direction

[roadmap.md](roadmap.md) is the front door. Specs below describe direction-of-travel, not chapter-and-verse implementation:

- **[identity foundation](identity-foundation.md)** — **load-bearing front door for the multi-tenancy cluster.** OIDC (wire) + RLS (data) contracts; three-stage staging (single-operator → OIDC-client SaaS → OIDC-provider giant). Every spec below is a consumer.
- M10: [prompt-iteration framework](m10-prompt-iteration-framework.md) · [operator control loop](m10-operator-control-loop.md)
- M11: [publication benchmarks](m11-publication-benchmarks.md)
- [spend tracking](spend-and-tenancy.md) — first consumer of identity-foundation; lands the Stage-0 `IdentityContext` reification with spend as payload
- M12: [multi-connector](m12-multi-connector.md) · [control plane](m12-control-plane.md) (lights up identity-foundation Stage 1 — OIDC client)
- M13: [chat-first user web](m13-chat-first-user-web.md) (Install / User / Project nouns mapped onto OIDC claims; Stage 2 considered)
- [state-sync cleanup](state-sync-cleanup.md) (pre-whitelabel foundation; sequence Phase 1 before spend-and-tenancy reification) · [m12+ backlog](m12-plus-backlog.md)

## Reference (capability specs, not on the roadmap)

| Spec | Status | What |
|---|---|---|
| [code-debt-cleanup](code-debt-cleanup.md) | REFERENCE | Known bloat hotspots; Tier 0 shipped, Tiers 1–4 unscheduled. Public-release polish arc tracked at the bottom of the same file. |

## Archive

[`archive/`](archive/) holds done + superseded specs. Recent moves:

- [hard-sample-sorter](archive/hard-sample-sorter.md) — δ_s leaderboard Phase 1 shipped; phases 2–3 captured in [`m12-plus-backlog.md`](m12-plus-backlog.md).
- [security-audit](archive/security-audit.md) — first hardening pass complete 2026-05-05; deferred items embedded in [`m11-publication-benchmarks.md`](m11-publication-benchmarks.md) (endpoint hardening) and [`m12-control-plane.md`](m12-control-plane.md) (tenancy + auth).

Plus M0–M10 cleanup arcs, webapp display-source unification, bayesian-sample-picker, dispatch-prompt-budget, rasch-validation-plan, and M9 / M11 / M12 ancestors. Read for historical context.

Cross-repo: Proper Step Loop → backend repo `docs/spec/proper-step-loop.md`.
