# docs/specs — Milestone Specs

**Read [`docs/architecture.md`](../architecture.md) first** — §0 is the shape, §0.5 the load-bearing surface. This index lists what §0 names but isn't built yet (so an AI reader knows the gap before drilling into any M-file), then where each piece lives.

## TODO — what §0 promises but doesn't ship yet

- [ ] **Multi-connector.** Pipeline-agnosticity is a §0 commitment; TermNorm is the only registered connector today. → [`m12-multi-connector.md`](m12-multi-connector.md)
- [ ] **PromptPotter-as-connector.** Self-optimization claim depends on the optimizer running on its own `optimizer_pipeline.json` — second connector + outer loop. → [`m12-promptpotter-as-connector.md`](m12-promptpotter-as-connector.md)
- [ ] **`pipeline.json` contract doc.** Pinned location is `docs/developer/pipeline-json-contract.md`; file not yet on disk. → M10 follow-up
- [ ] **Multi-objective fitness.** Accuracy + cost + time axes designed in spec; not wired. → [`m12-composite-fitness.md`](m12-composite-fitness.md)
- [ ] **L2 Imagination (5th LLM call).** Read-forward rollout; would amend §0's four-LLM-call invariant. → [`m10-l2-self-diagnosis-and-imagination.md`](m10-l2-self-diagnosis-and-imagination.md)
- [ ] **L2 self-diagnosis panels.** Option-set / axis-exhaustion / sample-delta / verbosity stats — L2 today reads winner only. → [`m10-l2-self-diagnosis-and-imagination.md`](m10-l2-self-diagnosis-and-imagination.md)
- [x] **L1 evidence-grounding validator.** Shipped: `evidence_grounding` is a required L1-output field; `evidence_grounding_present` behavior check + `EvidenceGrounding` lineage carry. Healing rule (`l2_unjustified_mutations`) ships with Track 4. → [`m10-l2-self-diagnosis-and-imagination.md`](m10-l2-self-diagnosis-and-imagination.md)
- [ ] **Webapp control plane + multi-cycle.** Read-only ships (M11); control plane is M12. → [`m12-multi-connector.md`](m12-multi-connector.md)
- [ ] **Publication benchmarks + ablation.** BBEH headline + ablation studies not yet published. → [`m11-publication-benchmarks.md`](m11-publication-benchmarks.md)
- [ ] **L4 outer loop.** Sequenced M10 partial → M11 Track 5 → M12 Track 4. → [`m10-prompt-iteration-framework.md`](m10-prompt-iteration-framework.md)
- [ ] **Spend-loop operator doc.** Define spend → compute → review → redefine not documented as workflow. → [`m11-spend-tracking.md`](m11-spend-tracking.md)
- [ ] **Hard-sample leaderboard panel.** Rasch sort exists; operator panel doesn't. → [`m11-publication-benchmarks.md`](m11-publication-benchmarks.md)
- [ ] **`/potter-run` terminal auto-spawn.** Phase −1 currently asks the operator to launch a `.bat` manually. → [`m12-plus-backlog.md`](m12-plus-backlog.md)

## Spec files + pre-reading

| Spec | Pre-reading |
|------|-------------|
| [![m12-multi-connector](https://img.shields.io/badge/m12--multi--connector-red?style=for-the-badge)](m12-multi-connector.md) — subs: [`m12-promptpotter-as-connector.md`](m12-promptpotter-as-connector.md), [`m12-composite-fitness.md`](m12-composite-fitness.md), [`m12-newjob-status-bar.md`](m12-newjob-status-bar.md) | `promptpotter/infrastructure/backend.py`, `promptpotter/connectors/protocol.py`, `webapp/app/` |
| [![m12-plus-backlog](https://img.shields.io/badge/m12--plus--backlog-black?style=for-the-badge)](m12-plus-backlog.md) | — |
| [![m11-publication-benchmarks](https://img.shields.io/badge/m11--publication--benchmarks-red?style=for-the-badge)](m11-publication-benchmarks.md) — subs: [`m11-webapp-minimal-preview.md`](m11-webapp-minimal-preview.md), [`m11-webapp-react-port.md`](m11-webapp-react-port.md), [`m11-spend-tracking.md`](m11-spend-tracking.md) | `docs/research/benchmarks.md`, `datasets/{hotpotqa,gsm8k}/`, `promptpotter/application/datasets.py`, `webapp/app/` |
| [![m10-prompt-iteration-framework](https://img.shields.io/badge/m10--prompt--iteration--framework-black?style=for-the-badge)](m10-prompt-iteration-framework.md) — subs: [`m10-l2-self-diagnosis-and-imagination.md`](m10-l2-self-diagnosis-and-imagination.md), [`m10-sweep-toolkit.md`](m10-sweep-toolkit.md) | `promptpotter/application/optimization/{optimizer_pipeline.json,pipeline.py,runner.py}`, `tests/test_invariants.py` |

## Reference (not on the TODO)

| Item | Status |
|------|--------|
| [Hard-Sample Sorter](hard-sample-sorter.md) | capability spec, unscheduled — `promptpotter/application/intelligence/{hard_sample_sorter,exploration}.py`, `docs/methods/exploration-exploitation.md` |
| [Security audit](security-audit.md) | first hardening pass complete 2026-05-05 — `promptpotter/application/scoring/formula/`, `promptpotter/config/log_redaction.py`, `promptpotter/application/optimization/dispatch_hub.py` |
| M10 cleanup | archived → [`archive/m10-cleanup.md`](archive/m10-cleanup.md) + sub-audits |

Cross-repo: Proper Step Loop → backend repo `docs/spec/proper-step-loop.md`.
