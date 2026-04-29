# M12: Multi-Connector, Competitor Comparison, Webapp Phase 2

**Version:** 0.2.0
**Date:** 2026-04-28
**Status:** Planned
**Depends on:** M11 (Publication Benchmarks, Ablation Studies, Webapp Read-Only)

---

## Context

M11 delivered the first benchmark results, ablations, and a read-only webapp on top of M9's hexagonal foundation and M10's tuned optimizer-prompts. M12 generalizes the connector, closes the publication with a competitor head-to-head, and upgrades the webapp from read-only browser to live control surface.

Three gaps M12 closes:

1. **Single-backend assumption.** `BackendClient` is still concrete. Six chokepoints (4, 5, 7, 10, 11, 12, 13) remain from M6. A second backend would require copy-paste today.
2. **Publication lacks competitor comparison.** M11 produces PromptPotter numbers; M12 adds cited competitors (or MIPROv2 reproduction if reviewers object) for a complete main results table.
3. **Webapp is read-only.** No campaign launch, no live progress, no control.

## Tracks

### Track 1: Multi-Connector Architecture (ConnectorProtocol)

**Problem:** `BackendClient` is still concrete. The remaining M6 chokepoints are: query parsing (4), evaluation routing (5), registration (7), cache key derivation (10), observation mappings (11), step config translation (12), pipeline discovery (13). Workflow nodes (M6 Wave 4) also land here.

**Deliverables:**

1. **`ConnectorProtocol`** — abstract interface in `domain/connector.py` (or `application/connectors/protocol.py` — decided during the track). Covers: pipeline discovery, step config translation, matches/evaluation, query parsing, observation mapping.
2. **Connector registry** — `CONNECTORS` registry keyed by connector name, similar to `DATASET_LOADERS`. Adding a backend = one registry entry.
3. **TermNorm connector** — existing `BackendClient` becomes `TermNormConnector` implementing the protocol. Zero behavior change.
4. **Query parser registry** — `split_query_parts()` moves into a per-connector registry; TermNorm's parser is one entry.
5. **Second connector** — a second backend (candidate: a minimal LLM-only connector or a new backend we stand up) demonstrates the abstraction runs end-to-end through a full optimization campaign.


### Track 2: Competitor Comparison (Publication Closure)

**Problem:** M11 filled PromptPotter's rows in the main results table. Competitor rows are still empty.

**Competitive landscape:**

| System | Origin | Approach | Key Strength |
|--------|--------|----------|-------------|
| DSPy / MIPROv2 | Stanford, 2024 | Bayesian optimization over instructions + few-shot demos | Largest community, full framework |
| GEPA | 2025 (now in DSPy) | Reflective prompt evolution, tree of candidates | +12% over MIPROv2 on AIME-2025 |
| Promptomatix | Salesforce, 2025 | Meta-prompt + DSPy compiler, cost-aware | Competitive at lower cost |
| adv-CoT | 2025 | Adversarial generator-discriminator | +4.44% on GPT-3.5-turbo across 12 reasoning datasets |
| PromptWizard | Microsoft | Critique-guided generation (PromptPotter's inspiration) | Cost-efficient, strong on single-LLM tasks |

**Deliverables:**

1. **Cited numbers** — all competitors filled in the main results table with paper references. Clearly labeled "cited" vs "ours".
2. **MIPROv2 reproduction (optional, defensive)** — if reviewers object to cited-only comparison, reproduce MIPROv2 locally on HotPotQA via its well-packaged library. Use same model + same dataset split as M11 Track 1.
3. **Cost/efficiency scatter plot** — optimizer LLM calls vs accuracy gain, positioning PromptPotter against Promptomatix and PromptWizard.
4. **Final paper draft** — results section complete end to end.

**Note:** Different models and hardware across papers weaken direct comparison. Same datasets + metrics where possible. MIPROv2 is the easiest local reproduction if challenged.

### Track 3: Webapp Phase 2 (Launcher + Live Monitoring)

**Problem:** M11's webapp is read-only. Users can browse campaigns but can't start or control them.

**API extensions:**

- `POST /api/v1/backends/{id}/campaigns` — start new campaign
- `POST /api/v1/backends/{id}/campaigns/{id}/control` — pause / resume / stop
- `GET /api/v1/backends/{id}/campaigns/{id}/state` — live state polling
- WebSocket or SSE endpoint for real-time round progress

**Webapp views:**

1. **Campaign configuration form** — dataset + pipeline + connector selection, `campaign.json` builder, scan variant editor.
2. **Real-time progress dashboard** — current round, candidates, live accuracy, L1/L2/L3 status, log tail.
3. **Control panel** — pause / resume / stop buttons wired to the control surface.
4. **Polish + deployment** — production build config, Docker Compose (FastAPI + webapp), environment configuration.

**Multi-tenant hook-up:** M12 is also the right moment to activate the `TenantContext` seam shaped in M9. Auth middleware populates it; `infrastructure/store/` starts enforcing `{tenant_id}/...` path prefixes. Whitelabel becomes viable.

---

## Wave Sequencing

```
Wave 1: Track 1 (ConnectorProtocol + registry + TermNorm migration)
        — foundation; other tracks easier once abstraction exists

Wave 2: Track 1 (second connector + workflow nodes) + Track 3 (API extensions)
        — parallel; second connector demonstrates protocol, API extensions unblock webapp Phase 2

Wave 3: Track 2 (cited competitor numbers + figures) + Track 3 (launcher + live monitoring)
        — parallel; publication closes, webapp gains control

Wave 4: Track 3 (multi-tenant activation + polish + deployment) + Track 2 (MIPROv2 reproduction if needed)
        — ship
```

## Entry Criteria

- M11 exit gate passed
- Stable benchmark numbers for PromptPotter in `docs/research/benchmarks.md`
- Webapp read-only views live

## Exit Criteria

- [ ] Second backend connector exists and runs a full optimization campaign end-to-end
- [ ] `ConnectorProtocol` + registry documented; all 7 remaining M6 chokepoints resolved
- [ ] Workflow nodes (M6 Wave 4) implemented
- [ ] Main results table complete with all competitors (cited or reproduced)
- [ ] Webapp can launch, monitor, and control a campaign end-to-end
- [ ] `TenantContext` enforced at `infrastructure/store/` boundary; whitelabel viable
- [ ] Publication final draft complete

## Key Existing Code

| Area | Files (post-M9 hexagonal layout) |
|------|-------|
| Backend client | `infrastructure/backend.py` |
| Query parsing | `infrastructure/backend.py::split_query_parts` |
| Pipeline discovery | `infrastructure/backend.py::fetch_pipeline` |
| Tenant seam | `domain/tenant.py` + `Session.tenant` (M9 shaped, M12 enforced) |
| FastAPI API | `presentation/api.py` |
| Webapp | `webapp/` (M11 MVP) |

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Second connector is a toy | Abstraction looks theoretical | Pick a real target (new backend or meaningful LLM-only connector) |
| MIPROv2 reproduction cost | Full benchmark re-run | Only if reviewers object; use M11 infrastructure |
| Webapp control surface races | Pause/resume during in-flight L1 batches | Reuse `FileControlSurface` + graceful interrupt from Parity milestone |
| Multi-tenant activation breaks existing data | Path prefix changes orphan legacy campaigns | Migration plan before activation; default tenant for legacy |
| Publication gets stuck on model version | Cited numbers use different models | Document exact model version in reproducibility manifest |
