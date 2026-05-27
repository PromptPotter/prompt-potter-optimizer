# Roadmap

The codebase is approaching final form. The only spec under active implementation is [`verdict-resolution.md`](verdict-resolution.md). Everything else is forward direction (kept thin) or completed milestones (one-line summary + archived spec pointer).

## Active

- **Verdict resolution** — single statistical model behind both the live adaptive queue mechanism and the persisted `hard_samples_*.json` ranking. Drops `explore_weight`. Phase 2 outlines origin-relative observation weighting. → [`verdict-resolution.md`](verdict-resolution.md)

## Forward direction

- **Identity foundation — the multi-tenancy cluster's front door.** Two contracts (OIDC wire + PostgreSQL RLS data) shape the codebase so one operator today and Facebook-scale tomorrow run the same code, not a rewrite. Three-stage staging: single-operator → OIDC-client SaaS → OIDC-provider giant. Every other multi-tenant spec is a consumer. **Permanent contract** — see [`ADR-0002`](../adr/0002-identity-foundation.md).
- **M10 — prompt-iteration framework + L1 tuning.** Manual optimizer-prompt refinement; `proxy_lift_corr ≥ 0.6` gate; behavior-check registry; `rounds_to_95` headline; cross-cycle leaderboard. Mini-milestone: webapp single-operator write surface. → [`m10-prompt-iteration-framework.md`](m10-prompt-iteration-framework.md), [`m10-operator-control-loop.md`](m10-operator-control-loop.md)
- **M11 — publication benchmarks + ablation + connector smoke.** BBEH primary, HotPotQA pending saturation probe, ablation rows (L1/L1+L2/full, scan, SearchMemory, critique, zero-signal filter). PromptPotter-as-connector smoke run. → [`m11-publication-benchmarks.md`](m11-publication-benchmarks.md)
- **Spend tracking** — first consumer of identity-foundation; lands the Stage-0 `IdentityContext` reification end-to-end with spend as the proof payload. **Permanent contract** — see [`ADR-0003`](../adr/0003-spend-and-tenancy.md).
- **M12 — multi-connector + competitor comparison + L4 closure + composite fitness + control plane.** Second connector validates the boundary; cited competitor numbers; outer-loop run on `datasets/promptpotter/`; per-candidate cost/latency rollup → multi-objective formula. Control plane lights up identity-foundation Stage 1 (OIDC client). → [`m12-multi-connector.md`](m12-multi-connector.md), [`0001-m12-control-plane.md`](../adr/0001-m12-control-plane.md)
- **M13 — chat-first multi-user web.** One admin self-hosts; casual users sign in; chat is the constant control surface. Install / User / Project nouns mapped onto OIDC claims. Spec only. → [`m13-chat-first-user-web.md`](m13-chat-first-user-web.md)
- **State-sync cleanup** — collapse the five drifting state surfaces before whitelabel. Sequence Phase 1 before spend-and-tenancy reification. → [`state-sync-cleanup.md`](state-sync-cleanup.md)

## Reference (capability specs, not on the roadmap)

- [`hard-sample-sorter.md`](hard-sample-sorter.md) — δ_s leaderboard primitive
- [`code-debt-cleanup.md`](code-debt-cleanup.md) — known bloat hotspots
- [`security-audit.md`](security-audit.md) — first hardening pass + deferred items

## Completed

M0–M9 + Parity shipped — specs at `docs/specs/archive/` or git history. M10 cleanup arc complete. Webapp display-source unification, dispatch prompt-budget unit, campaign-entity rework, AI-readiness arc, mypy-strict migration all closed.

## Backlog (M12+)

Multimodal · pipeline variant comparison · web-scrape ablation · public deployment · non-prompt targets · evolutionary operators · MCP server mode · cost tracking surface · model comparison matrix · L3 fork authority → AlphaZero-shaped MCTS (selection ✓, backpropagation + selection rule unscheduled). → [`m12-plus-backlog.md`](m12-plus-backlog.md)

## Non-functional requirements

| Requirement | Target |
|---|---|
| Single evaluation (500 items) | < 10 min |
| Full run (5 iters × 500 items) | < 60 min |
| Project store per campaign | < 10 MB |
| LLM providers | OpenAI-compatible (Groq default) |
| Python | 3.13 |
| Crash recovery | incremental `.partial.jsonl`; resume cache-hits prior |
