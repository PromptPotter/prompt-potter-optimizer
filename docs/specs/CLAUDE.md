# docs/specs — Milestone Specs

**Read [`docs/architecture.md`](../architecture.md) first** — §0 is the shape, §0.5 the load-bearing surface. This index lists what §0 names but isn't built yet (so an AI reader knows the gap before drilling into any M-file), then where each piece lives.

## TODO — what §0 promises but doesn't ship yet

- [ ] **Multi-connector.** Pipeline-agnosticity is a §0 commitment; TermNorm is the only registered connector today. → [`m12-multi-connector.md`](m12-multi-connector.md)
- [ ] **PromptPotter-as-connector.** Self-optimization claim depends on the optimizer running on its own `optimizer_pipeline.json` — second connector + outer loop. → [`m12-multi-connector.md#track-15--promptpotter-as-connector`](m12-multi-connector.md#track-15--promptpotter-as-connector)
- [ ] **`pipeline.json` contract doc.** Pinned location is `docs/developer/pipeline-json-contract.md`; file not yet on disk. → M10 follow-up
- [ ] **Multi-objective fitness.** Accuracy + cost + time axes designed in spec; not wired. → [`m12-multi-connector.md#track-5--composite-fitness-function`](m12-multi-connector.md#track-5--composite-fitness-function)
- [ ] **L2 Imagination (5th LLM call).** Read-forward rollout; would amend §0's four-LLM-call invariant. → [`m10-prompt-iteration-framework.md#track-8--l2-imagination`](m10-prompt-iteration-framework.md#track-8--l2-imagination)
- [ ] **L2 self-diagnosis panels.** Option-set / axis-exhaustion / sample-delta / verbosity stats — L2 today reads winner only. → [`m10-prompt-iteration-framework.md#track-7--l2-self-diagnosis-surface`](m10-prompt-iteration-framework.md#track-7--l2-self-diagnosis-surface)
- [x] **L1 evidence-grounding validator.** Shipped: `evidence_grounding` is a required L1-output field; `evidence_grounding_present` behavior check + `EvidenceGrounding` lineage carry. Healing rule (`l2_unjustified_mutations`) ships with Track 4. → [`m10-prompt-iteration-framework.md#track-7--l2-self-diagnosis-surface`](m10-prompt-iteration-framework.md#track-7--l2-self-diagnosis-surface)
- [ ] **State-sync cleanup.** Five drifting state surfaces (`active_session.json`, `dashboard.json`, `index.json::campaign_id`, dir name, in-memory CLI) need to collapse into two clean ones before whitelabel. Pre-M12 foundation. → [`state-sync-cleanup.md`](state-sync-cleanup.md)
- [ ] **Webapp control plane.** Read-only ships (M11); the single-operator write surface (launch / stop / resume / fork, SSE, `Control-remote` I/O kind) is an M10 mini-milestone; the multi-user SaaS hardening (auth, multi-tenant, whitelabel) is M12. → [`m10-operator-control-loop.md`](m10-operator-control-loop.md) + [`m12-control-plane.md`](m12-control-plane.md)
- [ ] **Chat-first multi-user web.** End-state product surface: one admin self-hosts; casual web users sign in; chat is the constant control surface; install-scoped shared measurements. Spec-only, no code. → [`m13-chat-first-user-web.md`](m13-chat-first-user-web.md)
- [partial] **L3 fork authority → AlphaZero-shaped MCTS.** **(1) selection ✓** — L3 emits observation-only `fork_proposal` (operator forks manually). **(2) backpropagation** — persist round outcomes as node-stats up the lineage. **(3) UCB-rule + auto-fork** — sample-efficient ancestor pick wired into `inherit_from`. All three close the structural gaps to AlphaZero-shaped MCTS; unlock recovery from dead-end branches. → [`roadmap.md#backlog-unscheduled`](roadmap.md#backlog-unscheduled), comparison [`../research/related-work.md#comparison-to-mcts`](../research/related-work.md#comparison-to-mcts)
- [ ] **Publication benchmarks + ablation.** BBEH headline + ablation studies not yet published. → [`m11-publication-benchmarks.md`](m11-publication-benchmarks.md)
- [ ] **L4 outer loop.** Sequenced M10 partial → M11 Track 5 → M12 Track 4. → [`m10-prompt-iteration-framework.md`](m10-prompt-iteration-framework.md)
- [ ] **Spend-loop operator doc.** Define spend → compute → review → redefine not documented as workflow. → [`m11-spend-tracking.md`](m11-spend-tracking.md)
- [ ] **Hard-sample leaderboard panel.** Rasch sort exists; operator panel doesn't. → [`m11-publication-benchmarks.md`](m11-publication-benchmarks.md)
- [ ] **`/potter-run` terminal auto-spawn.** Phase −1 currently asks the operator to launch a `.bat` manually. → [`m12-plus-backlog.md`](m12-plus-backlog.md)

## Spec files + pre-reading

| Spec | Pre-reading |
|------|-------------|
| [![m12-multi-connector](https://img.shields.io/badge/m12--multi--connector-red?style=for-the-badge)](m12-multi-connector.md) | `promptpotter/infrastructure/backend.py`, `promptpotter/connectors/protocol.py`, `webapp/app/` |
| [![m12-control-plane](https://img.shields.io/badge/m12--control--plane-red?style=for-the-badge)](m12-control-plane.md) | `promptpotter/application/bootstrap/session.py`, `promptpotter/infrastructure/store/stores.py`, [`security-audit.md`](security-audit.md) |
| [![m13-chat-first-user-web](https://img.shields.io/badge/m13--chat--first--web-orange?style=for-the-badge)](m13-chat-first-user-web.md) | `webapp/app/page.tsx`, `promptpotter/presentation/cli/`, `archive/measurements/` |
| [![m12-plus-backlog](https://img.shields.io/badge/m12--plus--backlog-black?style=for-the-badge)](m12-plus-backlog.md) | — |
| [![m11-publication-benchmarks](https://img.shields.io/badge/m11--publication--benchmarks-red?style=for-the-badge)](m11-publication-benchmarks.md) — subs: [`m11-spend-tracking.md`](m11-spend-tracking.md) | `docs/research/benchmarks.md`, `datasets/{hotpotqa,gsm8k}/`, `promptpotter/application/datasets.py`, `webapp/app/` |
| [![m10-prompt-iteration-framework](https://img.shields.io/badge/m10--prompt--iteration--framework-black?style=for-the-badge)](m10-prompt-iteration-framework.md) | `promptpotter/application/optimization/{optimizer_pipeline.json,pipeline.py}`, `promptpotter/application/runner/`, `tests/test_invariants.py` |
| [![m10-operator-control-loop](https://img.shields.io/badge/m10--operator--control--loop-black?style=for-the-badge)](m10-operator-control-loop.md) | `promptpotter/application/runner/entry.py`, `promptpotter/presentation/api/routers/`, `webapp/lib/`, [`state-sync-cleanup.md`](state-sync-cleanup.md) |

## Reference (not on the TODO)

| Item | Status |
|------|--------|
| [Hard-Sample Sorter](hard-sample-sorter.md) | capability spec, unscheduled — `promptpotter/application/intelligence/{hard_sample_sorter,exploration}.py`, `docs/methods/exploration-exploitation.md` |
| [Bayesian Sample Picker](bayesian-sample-picker.md) | shipped (Phases 1–3), then partly superseded — hierarchical IRT stands; the two-objective `picker_objective` gave way to one blended decision-led objective in `adaptive_picker.py` (2026-05-21) |
| [State-Sync Cleanup](state-sync-cleanup.md) | pre-whitelabel foundation, 4 phases — `promptpotter/infrastructure/store/`, `promptpotter/infrastructure/projections/live_dashboard/`, `promptpotter/presentation/api/routers/active.py`, `webapp-react/` |
| [Security audit](security-audit.md) | first hardening pass complete 2026-05-05 — `promptpotter/application/scoring/formula/`, `promptpotter/config/log_redaction.py`, `promptpotter/application/optimization/dispatch/hub/` |
| [Code-Debt Cleanup](code-debt-cleanup.md) | tech-debt backlog, unscheduled — 5 tiers; `promptpotter/application/optimization/l1/score.py`, `dispatch/hub/injections.py`, `dispatch/llm_call.py` |
| M10 cleanup | archived → [`archive/m10-cleanup.md`](archive/m10-cleanup.md) + sub-audits |

Cross-repo: Proper Step Loop → backend repo `docs/spec/proper-step-loop.md`.
