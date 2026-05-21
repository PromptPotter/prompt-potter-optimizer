# State-Sync Cleanup — Pre-Whitelabel Foundation

Pre-M12 architecture cleanup. Collapses five drifting state surfaces
into two clean ones so the CLI, file tree, and webapp render the same
picture at all times. Required before multi-tenant whitelabel: today's
drift is masked by single-operator usage; multi-tenant amplifies it.

## Problem

Five surfaces independently encode "which cycle is real / running /
progressing." No invariant binds them, and different code paths write
each one at different times.

| # | Surface | What it claims | Who writes |
|---|---|---|---|
| 1 | `.promptpotter/active_session.json` | tenant / session / cycle pointer | mint, fork, sweep-restore |
| 2 | `dashboard.json` (per family root) | live state — cycle_id, round, best, current_acc, in_flight, … | `LiveDashboardView` projection (ledger-subscribed); now also a sidecar write from sweep-restore (added 2026-05-15 to mask one symptom) |
| 3 | `index.json::campaign_id` (per cycle dir) | durable cycle identity | `CampaignStore` at mint + round-complete |
| 4 | Filesystem directory name | implicit cycle identity | mint only |
| 5 | CLI `LiveDisplay` | derived from the ledger live | per-CLI-process, in-memory |

### Observed drift (2026-05-15 session)

- **Surface 3 ↔ 4 collision.** Operator manually renamed
  `cycle_0c7c4ceee267` → `cycle_0c7c4ceee267_pre_wave1to4` as a
  backup. `index.json::campaign_id` stayed at the old value.
  `enumerate_cycles` keys on (3), so the webapp shows two entries
  both claiming `cycle_id = cycle_0c7c4ceee267`. Left-nav indicators
  scatter across the duplicates.
- **Surface 1 ↔ 2 drift on sweep restore.** Multi-variant sweep
  retargets the active pointer per variant (1), each fork's
  projection writes its own cycle_id into the family-root
  dashboard.json (2), and the sweep-end pointer restore reverts (1)
  to the parent — but (2) still carries the last variant's
  cycle_id. Webapp polls (2) at 2 s and shows the dead variant as
  live for an arbitrary window.
- **Polling reader sees inconsistent slices.** The webapp reads (1)
  and (2) on separate requests with no consistency guarantee. The
  CLI, in-memory CLI display, and webapp can each show a different
  cycle as "current" at the same wall-clock second.

### Why patching doesn't fix it

The five surfaces are independent writers. Every multi-file mutation
(mint, fork, restore, manual rename) has to update some subset of
them in sync — and there is no transactional boundary, no validator,
and no "single source." Each new symptom gets a sidecar writer; the
sidecar fleet grows. The patch added in this session
(`rewrite_active_cycle_id` at sweep-restore) is the latest example
and is explicitly scheduled for deletion in Phase 4 below.

## Target architecture

Two surfaces, three derived views.

### Two surfaces

**`active_session.json` is ground truth for *what's running now*.**
Shape unchanged: `{tenant_id, session_id, cycle_id}`. Three call
sites write it: `mint`, `fork`, `sweep_restore`. No other code
touches it. Reading it is the only way to learn "is anything live?"

**`state.json` per cycle dir is *what that cycle's last known state
was*.** Renamed from `dashboard.json` to remove the "dashboard
implies live" connotation. Per-cycle, not per-family-root. Written
*only at teardown* — cycle stops, completes, pauses, or is
torn down by Ctrl+C. Never written during a round. Holds the final
snapshot: best, n_rounds, final composite, evaluators, stop_reason.

### Three derived views

**Live view (active cycle only).** New endpoint
`GET /api/v1/live` → reads `active_session.json`, opens that cycle's
ledger, returns the latest projection tick. No file polling, no
race, no stale slice. CLI's in-memory `LiveDisplay` already
implements the same projection — extract a shared
`derive_live_state(ledger)` helper so the webapp and CLI agree
bit-for-bit on what "live" means.

**Cycle list view.** `enumerate_cycles` walks the campaigns tree and
returns `(dir_name, state.json)` pairs. Keys on dir name only. Manual
`mv`, `cp`, `rename` — all safe. Duplicate-id collisions impossible.

**Historical state.** Webapp reads each cycle's own `state.json` for
"what did this cycle end at?" No more shared family-root file with
flipping cycle_id field.

### Identity rule

**Directory name IS the cycle id.** `index.json::campaign_id` is
removed (or kept only as a deprecated read-derived field for one
release). `CampaignStore` reads `index_path.parent.name` and never
trusts the embedded field. The `_pre_wave1to4` collision becomes
mechanically impossible — rename the dir, you renamed the cycle.

## Migration phases

Each phase ships independently and leaves the system in a strictly
better state than the prior one. Phase numbering is execution order;
review can interleave.

### Phase 1 — Identity collapse (smallest cut)

Goal: kill the `_pre_wave1to4`-class drift. Dir name becomes the
only source of identity.

Files:
- `promptpotter/infrastructure/store/campaign_store/store.py::enumerate_cycles` — key on `index_path.parent.name`, ignore `data["campaign_id"]`. Backward read path: when `campaign_id` is present and differs from dir name, log a one-line warning and trust dir name.
- `promptpotter/infrastructure/store/campaign_store/store.py` everywhere else — same rule: dir name wins.
- `index.json` writers: stop emitting `campaign_id` on new writes.
- One-shot disk migration: a `python -m promptpotter migrate state-sync-v1` command (or a startup auto-heal) that rewrites every `index.json` so `campaign_id == dir name`, and reports any pre-existing mismatches like `_pre_wave1to4`.
- `tests/test_invariants.py` — assert no production code path reads `index.json::campaign_id`.

Removes the `_pre_wave1to4` confusion immediately.

### Phase 2 — Rename `dashboard.json` → `state.json`, per-cycle, end-state only

Goal: stop writing display state during rounds. The webapp's live
view goes to the ledger; the file holds only the end snapshot.

Files:
- `promptpotter/infrastructure/projections/live_dashboard/` — rename module to `live_state/` or `cycle_state/`. The projection accumulates state in-memory as today, but its `_persist()` only fires on teardown (`drain()` path) rather than every callback.
- `state.json` is written to the *cycle's own dir*, not the family root. Forks each get their own `state.json` at teardown.
- Delete `cycle_id` and `cycle_id_path` fields from `state.json` — the dir name supplies (a) identity and (b) parent walk on demand via `walk_cycle_lineage`.
- `CampaignStore` deletes any references to family-root-shared dashboards.

Per-cycle `state.json` removes the family-root-shared file entirely,
which is what makes (2) ↔ (active fork) drift possible.

### Phase 3 — Live endpoint

Goal: webapp reads live state from the ledger, not from a file.

Files:
- New `promptpotter/presentation/api/routers/live.py` — `GET /api/v1/live` reads `active_session.json`, opens the active cycle's `CycleEventLog`, returns `derive_live_state(events_tail)`.
- New `promptpotter/application/scoring/live_state.py` (or similar) — pure function `derive_live_state(events) → LiveStateView`. Shared with CLI `LiveDisplay`.
- Webapp `webapp-react/` — replace dashboard.json polling with `/api/v1/live` polling for the active cycle's live block. Per-cycle `state.json` reads stay (now correctly named).
- Live tick rate stays 2 s; the file race is gone because there is no file.

### Phase 4 — Delete the sidecars

Goal: remove every "manual write to keep things in sync" path that
Phase 2/3 made unnecessary.

Deletions:
- `rewrite_active_cycle_id` (the patch added 2026-05-15 in
  `live_dashboard/view.py`) — no longer needed; `state.json` is
  per-cycle and only written at teardown, so sweep-restore touches
  only `active_session.json`.
- The sweep-restore `rewrite_active_cycle_id` call in
  `presentation/cli/commands/sweep.py:540` and
  `application/sweep/sweep_runner.py:257`.
- `LiveDashboardView.log_fork` — folded into the teardown write of
  the prior fork + the bootstrap of the next one.
- `dashboard.json::cycle_id_path` derivation in
  `walk_cycle_lineage` — moved into the API layer at read time.

After Phase 4 the codebase has one writer per fact, one reader path
per question.

## Invariants to install in `tests/CLAUDE.md`

Post-migration, the following invariants are enforceable as tests:

1. **No production code reads `index.json::campaign_id`.** Grep for
   the literal; assert zero hits in `promptpotter/`.
2. **`state.json` is written only by `LiveStateView.drain()`.** Grep
   for `state.json` write sites; assert exactly one.
3. **No code writes `active_session.json` outside `mint`, `fork`,
   `sweep_restore`.** Three call sites; the rest are reads.
4. **`enumerate_cycles` keys on dir name.** Assert via unit test
   that an `index.json` with mismatched `campaign_id` field still
   surfaces under its dir name.
5. **Webapp left-nav cycle list equals `enumerate_cycles` output.**
   API endpoint smoke test.

## What this is NOT

- Not the control plane itself. This spec ships no mutating
  endpoint and no job runner. The webapp single-operator control
  plane — launch / stop / resume / fork, the in-process
  `JobRegistry`, SSE reactivity — builds on this cleanup and ships
  as the M10 mini-milestone
  [`m10-operator-control-loop.md`](m10-operator-control-loop.md);
  multi-tenant hardening follows in
  [`m12-control-plane.md`](m12-control-plane.md).
- Not the job runner. This spec leaves the CLI as the only loop
  driver; the in-process `JobRegistry` that lets the webapp launch
  runs is the M10 mini-milestone's Track B. Phases 1–3 here are its
  prerequisite — the live endpoint (Phase 3) is what the
  mini-milestone's SSE channels stream.
- Not a schema migration. `index.json` and `state.json` keep their
  field sets; only identity (`campaign_id`) is dropped.

## Whitelabel rationale

Multi-tenant amplifies every drift class in §Problem:

- **Identity collisions** — tenant isolation depends on the cycle id
  being unforgeable. Trusting `index.json::campaign_id` instead of
  dir name means a tenant who can write a file in their own
  campaigns dir can claim another tenant's cycle id.
- **Polling reads of multi-writer files** — a shared `dashboard.json`
  across forks already races single-tenant. Multi-tenant means
  multiple session writers on the same family root if any tenant
  forks across the boundary.
- **Sidecar writer fleet** — every drift symptom currently spawns a
  sidecar. Whitelabel can't be audited if each release adds another
  out-of-band writer.

Phase 1 alone resolves the identity-isolation risk. Phases 2–4
remove the race surfaces.

## Status

- Spec drafted 2026-05-15 after operator-observed drift in webapp
  left-nav during a multi-variant sweep cycle.
- Phase 1 unblocks immediately and is the recommended starting
  point.
- Phases 2–4 are independent ships; each one is < 1 day of focused
  work.
- Patch in `promptpotter/infrastructure/projections/live_dashboard/view.py::rewrite_active_cycle_id`
  (added 2026-05-15) stays in until Phase 2 lands; it is masking
  one symptom, not fixing the cause.
