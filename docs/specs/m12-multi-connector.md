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

1. **Second connector — `promptpotter/connectors/promptpotter.py`** (PromptPotter-as-backend). Lands from M11 Track 5; M12 confirms it loads via `bootstrap.py` and runs end-to-end. The connector wraps L1 / L2 / L3 / `l1_critique` / `restructure` against `optimizer_pipeline.json`'s pinned shape (see `m10-cleanup.md` §3.5 parity test). This is the "real target" — cheapest second connector, exercises the abstraction without cross-repo burden, AND becomes the foundation for Track 4 (L4 self-optimization closure). Original wording ("pick a real target — minimal LLM-only or a new backend") superseded once M11 Track 5 ships.
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

### Track 4: L4 Self-Optimization Closure

**Problem:** §0 of `m10-cleanup.md` claims PromptPotter optimizes its own meta-prompts via `optimizer_pipeline.json`. M10 pins the contract + ships a fixture. M11 ships the connector. M12 closes the loop by actually running the outer-loop optimization that improves L1/L2/L3 prompts. This was originally parked in `m12-plus-backlog.md` as "L4 — completion"; promoted to M12 because the M11 connector lands the residual blocker, making the actual run a small step rather than a separate milestone.

**Deliverables:**

1. **Outer-loop campaign on PromptPotter dataset.** Point `python -m promptpotter optimize` at `datasets/promptpotter/` (the M10 fixture + M11 expansion of archived rounds) using the M11 PromptPotter connector. Run 5–10 rounds. The campaign optimizes PromptPotter's own L1 / L2 / L3 / `l1_critique` / `restructure` meta-prompts.
2. **`proxy_lift_corr` validation on the meta-loop.** Per `m10-prompt-iteration-framework.md`, M10 ships `proxy_lift_corr ≥ 0.6` as the L1-tuning gate. M12 Track 4 confirms the same gate holds when the optimizer is optimizing itself (i.e., the proxy reward correlates with cycle outcome on the meta-task too). If correlation breaks, that's a finding worth publishing — the framework's meta-stability as a positive or negative result.
3. **Cross-cycle digest of meta-prompt evolution.** Same `archive/measurements/` mechanism as target-task campaigns; the M10 §3.7 facade serves both. Operator can read meta-prompt history the same way they read TermNorm campaign history — no parallel infrastructure.
4. **Findings doc.** `docs/research/l4-self-optimization-results.md`: did meta-optimization improve target-task accuracy on a held-out benchmark (e.g., BBEH or HotPotQA)? What was the cost? What changed in the meta-prompts? Pairs with M12 Track 2 (publication closure).

**Why M12 not M12+:** the M11 connector + M10 fixture/contract eliminate the residual blocker. What's left is "run the loop" — same orchestration code as any other campaign, just with the PromptPotter connector. The publication value (closing the L4 story) is significant enough to belong in the headline milestone.

**Cross-ref:** `docs/specs/m10-cleanup.md` §3.5 + self-optimization fixture; `docs/specs/m11-publication-benchmarks.md` Track 5 (connector); `docs/specs/m12-plus-backlog.md` (L4-completion item removed since it's now in M12); `docs/specs/roadmap.md` updated L4 line.

### Track 3.5: Orchestrator Daemon (control plane structural shape)

**Problem:** Track 3 above describes the launcher / control surface as REST endpoints (`POST /campaigns`, `POST /control`) — that's the half-step. The structural endpoint of "control plane" is an **orchestrator daemon**: `optimize` runs as a long-lived process owning `Session` and `LoopState`; CLI / notebook / webapp all become HTTP clients of the same orchestrator. This adds a **fourth I/O kind** beyond `m10-cleanup.md` §0's three (Persistence / Display / Control-local) — call it **Control-remote**.

Track 3.5 names the daemon explicitly so M12 Track 3's launcher / control work doesn't accidentally ship a halfway-daemon (in-process-per-request orchestrator with no state coherence between requests).

**Decision option** (defer to M12 author): land Track 3.5 as a separate sub-spec `docs/specs/m12-orchestrator-daemon.md` for clarity, OR keep inline here if the section stays small. Either way, must explicitly address:

- **The fourth I/O kind ("Control-remote") and what it is and isn't allowed to do** — parallel to `m10-cleanup.md` §0's three-kind invariant. Allowed: receive control commands (start/pause/resume/stop), expose `Session`/`LoopState` snapshots, route campaign output. Not allowed: writing campaign artifacts directly (still goes through `CycleEventLog.append`); reading tracing data (still fan-out only).
- **How `Session` becomes a daemon-owned object.** Either in-process-mutable (cheap; daemon owns the lifetime) or projection-over-the-ledger (expensive; enables multi-process + trivial restart). Decide based on whether multi-process state coherence is required.
- **How CLI / notebook / webapp become HTTP clients of the same orchestrator.** Concrete: what does `python -m promptpotter optimize` do when the daemon is running? Does it spawn a daemon if none exists, or hard-fail with "start the daemon first"? Same question for notebook + webapp.
- **State authority decision** (Item 3 coupling). If the daemon needs multi-process / restart-survival, pull `m10-cleanup.md` §3.8's reconstructable-state invariant forward into a partial event-sourcing migration (Session-as-projection over ledger). The M10 invariant makes this a smaller move when needed; if not needed, mutable Session in-daemon is fine.
- **Coupling with M12 Track 1 (multi-connector) and Track 4 (L4 closure).** Daemon mode means the connector instance is daemon-owned (not per-request); the L4 closure outer-loop campaign runs as a daemon-managed long-lived job.

**Out of scope for Track 3.5:** anything Track 3 ships standalone — the launcher form, the control-button design, the dashboard chrome. Track 3.5 is purely the daemon shape; Track 3 is the user-facing UI on top.

**Why land Track 3.5 in M12 not later:** the multi-tenant work in Track 3 already touches every store boundary. Adding the daemon shape on top of in-process orchestration later would mean revisiting the same boundaries twice. Better to design Track 3 + 3.5 together so the launcher is daemon-shaped from the start.

**Note on `m10-cleanup.md` §6 question 4 sub-bullet:** that sub-bullet is the gate that catches accidental daemon prep before M12. Track 3.5 is the planned crossing of that gate — design-spec change first, code change second.

---

## Wave Sequencing

```
Wave 1: ✅ Track 1 foundation — Connector + registry + TermNorm migration
        (shipped in ed95509; BackendClient connector-agnostic)

Wave 2: Track 1 (second connector + workflow nodes) + Track 3 (API extensions)
        — parallel; second connector demonstrates the boundary, API extensions unblock webapp Phase 2

Wave 3: Track 2 (cited competitor numbers + figures) + Track 3 (launcher + live monitoring)
        — parallel; publication closes, webapp gains control

Wave 4: Track 3 (multi-tenant activation + polish + deployment) + Track 2 (MIPROv2 reproduction if needed) + Track 4 (L4 outer-loop run)
        — ship; L4 closure runs against M11 connector + M10 fixture

Wave 5: Track 3.5 (orchestrator daemon)
        — depends on Tracks 1+3+4 stabilizing; reshapes control plane around daemon ownership of Session
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
- [ ] L4 self-optimization closure (Track 4): outer-loop campaign on `datasets/promptpotter/` via the M11 connector ran end-to-end; findings doc at `docs/research/l4-self-optimization-results.md` documents whether meta-optimization improved target-task accuracy
- [ ] Orchestrator daemon (Track 3.5): control-plane shape decided (in-process-mutable Session vs Session-as-projection); daemon spec landed (sub-spec or inline); CLI / notebook / webapp client behavior defined when daemon is running

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
