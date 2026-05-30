# State-Sync Cleanup — Pre-Whitelabel Foundation

**Status:** Phases 1 + 3 **shipped 2026-05-30**. Phase 1 = identity collapse; Phase 3 = `GET /api/v1/live` (additive façade). **`dashboard.json` stays live-written — the teardown-only design is rejected (reverses folder-UI §0).** Remaining: Phase 2 (optional per-cycle race-fix), Phase 4 (sidecar cleanup).

Collapse five drifting state surfaces into two so CLI, file tree, and webapp render the same picture. Required before multi-tenant whitelabel — single-operator usage masks the drift today.

## Problem

Five independent writers encode "which cycle is real / running / progressing." No invariant binds them; every mutation (mint, fork, restore, manual rename) updates a subset.

| # | Surface | Writer |
|---|---|---|
| 1 | `.promptpotter/active_session.json` | mint, fork, sweep-restore |
| 2 | `dashboard.json` (per family root) | `LiveDashboardView` projection + sweep-restore sidecar (added 2026-05-15) |
| 3 | ~~`index.json::campaign_id`~~ (removed, Phase 1) | identity is now the dir name only |
| 4 | Filesystem directory name | mint only |
| 5 | CLI `LiveDisplay` | per-CLI-process, in-memory |

Observed drift: 3↔4 collisions on manual rename (`cycle_0c7c4ceee267` ↔ `cycle_0c7c4ceee267_pre_wave1to4` claim the same `campaign_id`); 1↔2 drift on sweep restore (active pointer reverts; dashboard still shows last variant's cycle_id); the webapp reads 1+2 separately with no consistency guarantee.

## Target

**One ground-truth pointer + one live read-out.** `active_session.json` is ground truth for *what's running now* (`{tenant_id, session_id, cycle_id}`, three writers: mint / fork / sweep_restore). `dashboard.json` **stays live-written** every ledger event (debounced ≤0.25 s, synchronous flush at round boundaries).

> **Folder-UI is a [`architecture.md`](../architecture.md) §0 commitment — non-negotiable.** "Everything material lives on disk, in human-readable form; the project file tree IS the operator's primary interface — `campaign.json`, `dashboard.json`, `index.json`…" An operator (or an AI working headless) opens `dashboard.json` mid-run and sees current state. The earlier `state.json` / **teardown-only** design is **rejected**: writing the live surface only at teardown would reverse §0 and kill headless debugging. Do not re-propose it. The only legitimate change in this lane is scoping the live file **per-cycle** instead of family-root-shared, to remove the 1↔2 fork race — fully compatible with live writes.

**Convergence with [`0002-identity-foundation.md`](../adr/0002-identity-foundation.md):** the `tenant_id` field on `active_session.json` is the same `TenantId` newtype the Stage-0 `IdentityContext` carries (per [`0003-spend-and-tenancy.md`](../adr/0003-spend-and-tenancy.md)). Phase 1 of this spec touches `index.json` writers — the same files spend-and-tenancy re-touches for the `IdentityContext` reification. **Sequence Phase 1 before the spend-and-tenancy reification** to avoid double-touching the store seam. Identity-foundation Stages 1+ do not alter the on-disk identity field — the seam just gains a richer source.

**Identity rule.** Directory name **IS** the cycle id. `index.json::campaign_id` is removed; `CampaignStore` reads `index_path.parent.name`. Rename the dir, you renamed the cycle.

**Live view. ✅ Shipped 2026-05-30 (`04c94a94`).** `GET /api/v1/live` reads `active_session.json` → returns the session-family root's live `dashboard.json`. Stable façade keyed on the active pointer; new panels + chat state-reads code against it. Additive — `dashboard.json` polling is untouched. The file stays the live surface, so the façade reads it directly rather than re-deriving from the ledger.

## Phases

1. **Identity collapse. ✅ Shipped 2026-05-30.** `enumerate_cycles` + `_ids_from_index_path` already key on the directory path (`campaigns/{c}/cycles/{cy}/`), and `create()` never wrote `campaign_id`. What this phase closed: (a) the sole reader that trusted a stored id — `review.py::_render_header` read `index.json::campaign_id` (always absent → header stuck at "(unknown cycle)"); now reads the dir-injected `cycle_id`; (b) the two index writers that spread a prior blob (`CycleIndexMixin.create`, `ForkMixin.save_rebase_fork`) now strip both `cycle_id` and `campaign_id` on every write, so any file minted under an older scheme self-heals on its next touch — no throwaway migration script needed. Guarded by `tests/test_invariants.py::test_cycle_identity_is_dir_name_not_stored`. Removes the `_pre_wave1to4`-class drift.
2. **Per-cycle live state (race-fix only).** Scope the live file per-cycle instead of family-root-shared so a fork's view can't surface the parent's `cycle_id` (the 1↔2 drift). `dashboard.json` **stays live-written** — no teardown, no rename (see Target). This is the only substantive remainder; optional and independent. (If a per-cycle file is wanted, it's an additional live writer, not a replacement of the live semantics.)
3. **Live endpoint. ✅ Shipped 2026-05-30 (`04c94a94`).** `GET /api/v1/live` on `_active_router` (`active.py`) — stable façade over the live `dashboard.json`, keyed on the active pointer; `reads.ts::fetchLiveState`. Additive; polling untouched. (Originally sketched as a new `routers/live.py` + `derive_live_state(ledger)` + teardown-only; since the file stays live, the façade reads it directly. New panels code against `/api/v1/live` and stay insulated if the internals ever change.)
   - **Per-cycle runstate read (binding surface for the web controls).** `/api/v1/live` is keyed on the *active pointer*, so it answers "what's running now" but not "is the cycle I'm *viewing* paused." `GET /campaigns/{c}/cycles/{cid}/runstate` (non-cached, unlike the 304-cached dashboard route) returns `{running, paused, stop_requested, spend_cap_usd}` for the **viewed** cycle — this is what `RunControlButton` / `useRunState` bind to, so play/pause acts on the cycle on screen, not the active-pointer one, and reads honest `paused` (a paused loop emits no telemetry, so freshness alone can't tell). The single Control-local read seam is `infrastructure/runtime_flags.py` (`is_paused` / `is_stop_requested` / `read_spend_cap` off the `.runtime` flags + `is_running` derived from `dashboard.json` freshness — no new heartbeat artifact); it dedupes the spend-cap/pause reads `active.py` had inlined. Both surfaces stay: `/live` carries the active-pointer pause flag for the chat hero-pill, `runstate` carries the viewed-cycle truth for the controls.
4. **Delete the sidecars.** Delete the no-op `LiveDashboardView.log_fork` (`view.py:416`, called at `run_observers.py:382`); derive `dashboard.json::cycle_id_path` in the API at read time. NB: `save_active_pointer` (incl. the sweep-restore call at `sweep_runner.py:258`) is the **sanctioned** active-pointer writer (invariant #3) and stays. There is no `rewrite_active_cycle_id` to remove — Phase 1's identity collapse already obviated that class of patch.

## Invariants (post-migration, enforceable as `tests/test_invariants.py` checks)

1. No production code reads `index.json::campaign_id`. ✅ enforced (Phase 1).
2. `dashboard.json` has a single writer (`LiveDashboardView`) and **stays live** — present on disk after any ledger event, settling ≤0.25 s, synchronous flush at round boundaries (the `webapp/CLAUDE.md` folder-UI contract). NOT teardown-only.
3. `active_session.json` written only by `mint` / `fork` / `sweep_restore` (the writer is `save_active_pointer`; sweep-restore call site `sweep_runner.py:258`).
4. `enumerate_cycles` keys on dir name. ✅ enforced (Phase 1).
5. Webapp left-nav cycle list equals `enumerate_cycles` output.

## Out of scope

Not the control plane itself — that's [`0001-m12-control-plane.md`](../adr/0001-m12-control-plane.md) (which depends on Phases 1–3 here). Not a schema migration — only identity (`campaign_id`) is dropped.

## Whitelabel rationale

Multi-tenant amplifies every drift class: identity collisions enable cross-tenant claim of a cycle id; multi-writer files race across tenants on shared family roots; the sidecar writer fleet is unauditable.

## Status

Phases 1 + 3 shipped 2026-05-30. `dashboard.json` stays live-written (folder-UI §0). Phase 2 (per-cycle race-fix) and Phase 4 (sidecar cleanup) are independent, optional, each < 1 day — neither touches the live-write semantics.
