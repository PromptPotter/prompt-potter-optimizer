# Entry-Point Parity: Unified Persistence Layer

**Status:** Complete | **Date:** 2026-04-04

---

Previously, persistence was an entry-point concern — the CLI produced `campaign_state.json` and `campaign_output.log` while the notebook skipped both. Now `run_optimization()` auto-creates `CampaignPersistenceEmitter`, making all three entry points (notebook, CLI, web app) produce identical artifacts.

```
Entry Points (notebook, CLI, web app)
    │  provide: display_callbacks + optional on_checkpoint
    ▼
optimization_loop.py ──auto-creates──> CampaignPersistenceEmitter
    ├── campaign_state.json   (live state)
    ├── campaign_output.log   (eval audit trail)
    └── campaign_log.md       (structured report)
```

**Three layers:**
- **Persistence** — shared, mandatory. Auto-created by `run_optimization()`. Entry points MUST NOT write campaign artifacts directly.
- **Display** — per-entry-point. Caller passes `display_callbacks: CycleCallbacks`.
- **Control** — per-entry-point, optional. `FileControlSurface` (CLI/web) or kernel interrupt (notebook).

**Key files:**
- `promptpotter/services/campaign/persistence_emitter.py` — `CampaignPersistenceEmitter`
- `promptpotter/services/campaign/control_surface.py` — `FileControlSurface`
- `promptpotter/services/campaign/callbacks.py` — `chain_callbacks()`
- `promptpotter/services/campaign/artifacts.py` — `CAMPAIGN_SESSION_ARTIFACTS` manifest
- `tests/test_artifact_parity.py` — 4 parity guard tests
