# M12: Multi-Connector, Competitor Comparison, Webapp Phase 2

**Version:** 0.3.0
**Date:** 2026-05-04
**Status:** Track 1 (foundation) shipped; Tracks 2 + 3 in flight
**Depends on:** M11 (Publication Benchmarks, Ablation Studies, Webapp Read-Only)

---

## Context

M11 delivered the first benchmark results, ablations, and a read-only webapp on top of M9's hexagonal foundation and M10's tuned optimizer-prompts. M12 generalizes the connector, closes the publication with a competitor head-to-head, and upgrades the webapp from read-only browser to live control surface.

Three gaps M12 closes:

1. **Single-backend assumption.** `BackendClient` was concrete; the connector boundary now lives at `promptpotter/connectors/` (shipped in `ed95509`). A second backend is one new file under that package — but no second connector is registered yet, so the abstraction has not been exercised end-to-end.
2. **Publication lacks competitor comparison.** M11 produces PromptPotter numbers; M12 adds cited competitors (or MIPROv2 reproduction if reviewers object) for a complete main results table.
3. **Webapp is read-only.** No campaign launch, no live progress, no control.

## Tracks

### Track 1: Multi-Connector Architecture — **shipped in `ed95509`** (foundation); second connector pending

**Status:** Foundation landed. `Connector` lives at `promptpotter/connectors/protocol.py`, registry at `promptpotter/connectors/__init__.py`, TermNorm at `promptpotter/connectors/termnorm.py` with self-registration at import. `BackendClient` no longer carries TermNorm defaults — `wire_adapter` + `session` are required at construction. The four hooks bundled per `Connector` (`wire_adapter`, `session_factory`, `extract_experiment`, `resolve_ground_truth`) fold the previously-scattered `EXPERIMENT_EXTRACTORS` / `TRACE_GT_RESOLVERS` registries.

**Outstanding (Track 1 deliverables):**

1. **Second connector** — pick a real target (minimal LLM-only or a new backend). One file under `promptpotter/connectors/`, registered via `register(Connector(...))`. Bootstrap looks it up by `pipeline.json::backend_type`.
2. **Connector lookup driven by config** — `bootstrap.py:514` currently hardcodes `connectors.get("termnorm")`. Read `pipeline.json::backend_type` (already present in dataset configs) and look up by that. Same for `presentation/api.py` sites that consume `BackendConnection.backend_type`.
3. **Query parser registry** — `split_query_parts()` (in `services/backend_client.py`) is still TermNorm-shaped. With the second connector, hoist into a per-connector hook (or fold into the wire adapter — to be decided when the second connector lands).
4. **Workflow nodes** (M6 Wave 4) — outstanding from M6 closure, now unblocked by the connector boundary.
5. **Multi-tenant `TenantId` newtype** — see [`security-audit.md`](security-audit.md) § SafeName / TenantId. Lite path-validation already landed; the structural newtype migration belongs with the multi-tenant work since it touches every store.
6. **Prompt-injection Phase 2** — see [`security-audit.md`](security-audit.md) § Prompt-injection Phase 2. Starter fence on untrusted SIGNAL renderers landed; structural lint + output validators + cross-call repeat detection belong with multi-tenant rollout.


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

The launcher and control shapes are still being designed across M11–M12. Two candidate shapes are both in play and the deliverable for this track is to land *one* that satisfies both. Neither is yet sufficient on its own.

1. **Campaign configuration form** — dataset + pipeline + connector selection, `campaign.json` builder, scan variant editor. The structured-form shape: every knob the optimizer takes is editable, no ambiguity, reproducible from the JSON it produces. Strength: completeness, auditability, the obvious target for power users and reproducibility.
2. **Chat panel** (operator-staged in M11's "New Job" tab, 2026-05-07) — conversation-shaped entry. First-draft framing: drop dataset → see dataset preview → wand toggle on → quiet evolution starts; the chat is the conversation surface for that flow. Direction: wires to the existing `restructure` optimizer node (downstream of `l3_plan`) as its user-facing surface. Strength: low-friction onboarding, matches the operator's "fix a broken LLM pipeline in half a day, then it just works" launch positioning. Yet to fulfill what the configuration form covers (every knob, scan-variant editing, reproducible JSON output).
3. **Dataset preview view on drop** — when the user attaches a dataset (currently only a filename chip in M11), render a dedicated preview surface. Pairs with both shapes above.
4. **Control surface — discrete buttons vs. wand toggle.** Same dual-design tension as the launcher:
   - *Discrete pause / resume / stop buttons* — explicit, unambiguous, model-the-state. The structured shape.
   - *Wand "always-on background optimization" toggle* — operator-staged in M11. Live optimization that quietly evolves while production runs (live mode vs offline campaign). The framing the operator wants kept explicit until later versions. Yet to cover the explicit-state surface that discrete buttons give for free.
   Wave 3 wires whichever resolved shape into `POST /control` (pause / resume / stop endpoints stay; only the user-facing surface is the open question).
5. **Real-time progress dashboard** — current round, candidates, live accuracy, L1/L2/L3 status, log tail. Live update via SSE/WebSocket replaces M11's 2 s polling.
6. **Pipeline visualization is connector-driven, not connector-specific** — renders whatever the active connector's `pipeline.json` declares. Re-renders on dataset / session switch. Currently TermNorm is the only registered connector, so its 5-node shape is what appears, but the visualization itself is shape-agnostic.
7. **Polish + deployment** — production build config, Docker Compose (FastAPI + webapp), environment configuration.

**Open M11–M12 design question:** how the configuration-form completeness and the chat-panel low-friction shape converge into one launcher (and analogously, how discrete control and the wand toggle converge). M11 prototypes the chat + wand drafts; M12 Wave 3 ships the resolved shape.

**Multi-tenant hook-up:** M12 is also the right moment to activate the `TenantContext` seam shaped in M9. Auth middleware populates it; `infrastructure/store/` starts enforcing `{tenant_id}/...` path prefixes. Whitelabel becomes viable. Sidebar `Log out` lights up under this work.

---

## Wave Sequencing

```
Wave 1: ✅ Track 1 foundation — Connector + registry + TermNorm migration
        (shipped in ed95509; BackendClient connector-agnostic)

Wave 2: Track 1 (second connector + workflow nodes) + Track 3 (API extensions)
        — parallel; second connector demonstrates the boundary, API extensions unblock webapp Phase 2

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

- [x] `Connector` shape + registry shipped (`promptpotter/connectors/`, `ed95509`); TermNorm migrated; `BackendClient` connector-agnostic
- [ ] Second backend connector exists and runs a full optimization campaign end-to-end
- [ ] Bootstrap + API connector lookup driven by `pipeline.json::backend_type` (currently hardcoded "termnorm")
- [ ] Workflow nodes (M6 Wave 4) implemented
- [ ] Main results table complete with all competitors (cited or reproduced)
- [ ] Webapp can launch, monitor, and control a campaign end-to-end
- [ ] `TenantContext` enforced at `infrastructure/store/` boundary; whitelabel viable
- [ ] Publication final draft complete

## Key Existing Code

| Area | Files (post-M9 hexagonal layout) |
|------|-------|
| Connector boundary | `connectors/protocol.py` (Connector dataclass), `connectors/__init__.py` (registry) |
| TermNorm connector | `connectors/termnorm.py` (wire adapter + session + experiment extract) |
| Backend client | `infrastructure/backend.py` (connector-agnostic; wire_adapter + session required) |
| Query parsing | `services/backend_client.py::split_query_parts` (still TermNorm-shaped; per-connector hoist pending) |
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
