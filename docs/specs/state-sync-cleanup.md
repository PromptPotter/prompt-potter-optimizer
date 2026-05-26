# State-Sync Cleanup — Pre-Whitelabel Foundation

Collapse five drifting state surfaces into two so CLI, file tree, and webapp render the same picture. Required before multi-tenant whitelabel — single-operator usage masks the drift today.

## Problem

Five independent writers encode "which cycle is real / running / progressing." No invariant binds them; every mutation (mint, fork, restore, manual rename) updates a subset.

| # | Surface | Writer |
|---|---|---|
| 1 | `.promptpotter/active_session.json` | mint, fork, sweep-restore |
| 2 | `dashboard.json` (per family root) | `LiveDashboardView` projection + sweep-restore sidecar (added 2026-05-15) |
| 3 | `index.json::campaign_id` (per cycle dir) | `CampaignStore` at mint + round-complete |
| 4 | Filesystem directory name | mint only |
| 5 | CLI `LiveDisplay` | per-CLI-process, in-memory |

Observed drift: 3↔4 collisions on manual rename (`cycle_0c7c4ceee267` ↔ `cycle_0c7c4ceee267_pre_wave1to4` claim the same `campaign_id`); 1↔2 drift on sweep restore (active pointer reverts; dashboard still shows last variant's cycle_id); the webapp reads 1+2 separately with no consistency guarantee.

## Target

**Two surfaces.** `active_session.json` is ground truth for *what's running now* (`{tenant_id, session_id, cycle_id}`, three writers: mint / fork / sweep_restore). `state.json` per cycle dir is *what that cycle's last known state was* — renamed from `dashboard.json`, per-cycle, written **only at teardown**.

**Convergence with [`0002-identity-foundation.md`](../adr/0002-identity-foundation.md):** the `tenant_id` field on `active_session.json` is the same `TenantId` newtype the Stage-0 `IdentityContext` carries (per [`0003-spend-and-tenancy.md`](../adr/0003-spend-and-tenancy.md)). Phase 1 of this spec touches `index.json` writers — the same files spend-and-tenancy re-touches for the `IdentityContext` reification. **Sequence Phase 1 before the spend-and-tenancy reification** to avoid double-touching the store seam. Identity-foundation Stages 1+ do not alter the on-disk identity field — the seam just gains a richer source.

**Identity rule.** Directory name **IS** the cycle id. `index.json::campaign_id` is removed; `CampaignStore` reads `index_path.parent.name`. Rename the dir, you renamed the cycle.

**Live view.** `GET /api/v1/live` reads `active_session.json`, opens that cycle's ledger, returns `derive_live_state(ledger)`. Shared helper between webapp and CLI's `LiveDisplay`. No file polling, no race.

## Phases

1. **Identity collapse.** `enumerate_cycles` keys on `index_path.parent.name`; `index.json` writers stop emitting `campaign_id`; one-shot disk migration rewrites every `index.json` and reports mismatches. Removes the `_pre_wave1to4`-class drift.
2. **Rename `dashboard.json` → `state.json`, per-cycle, end-state only.** `live_dashboard/` module renamed to `live_state/` (or `cycle_state/`); `_persist()` only fires on teardown (`drain()`). Forks each get their own `state.json` at teardown. Removes the family-root-shared file.
3. **Live endpoint.** New `presentation/api/routers/live.py`; new `application/scoring/live_state.py::derive_live_state(events) → LiveStateView`. Webapp replaces `dashboard.json` polling with `/api/v1/live`.
4. **Delete the sidecars.** `rewrite_active_cycle_id` (the 2026-05-15 patch) + its sweep-restore callers in `cli/commands/sweep.py:540` and `application/sweep/sweep_runner.py:257`; `LiveDashboardView.log_fork` folded into teardown writes; `dashboard.json::cycle_id_path` derived in API at read time.

## Invariants (post-migration, enforceable as `tests/test_invariants.py` checks)

1. No production code reads `index.json::campaign_id`.
2. `state.json` written only by `LiveStateView.drain()`.
3. `active_session.json` written only by `mint` / `fork` / `sweep_restore`.
4. `enumerate_cycles` keys on dir name.
5. Webapp left-nav cycle list equals `enumerate_cycles` output.

## Out of scope

Not the control plane itself — that's [`m10-operator-control-loop.md`](m10-operator-control-loop.md) (which depends on Phases 1–3 here). Not a schema migration — only identity (`campaign_id`) is dropped.

## Whitelabel rationale

Multi-tenant amplifies every drift class: identity collisions enable cross-tenant claim of a cycle id; multi-writer files race across tenants on shared family roots; the sidecar writer fleet is unauditable.

## Status

Phase 1 unblocks immediately. Phases 2–4 are independent ships, each < 1 day. The 2026-05-15 `rewrite_active_cycle_id` patch stays in until Phase 2 lands — masking one symptom, not fixing the cause.
