# Code-Debt Cleanup — Backlog

**Status:** Reference — perpetual living backlog. Tiers 0–6 + polish arcs A–E + audits 1–3 closed by 2026-05-25. Active items: TermNorm wire model (cross-repo) + three deep-indirection architectural collapses (scoped, not slated). The M13+ intentional-UI-placeholder registry is permanent reference.

**Scope is literal: code debt only.** Dead code, redundant guards,
single-caller indirections, premature optimizations that no longer
earn their keep, vibe-coded scaffolding. The default action on every
entry is **delete** (or inline, or strip) — verify-first when the
evidence isn't on disk.

**Not debt — goes elsewhere:**
- Forward-looking webapp perf / feature work → [`m12-plus-backlog.md` § Webapp Perf](m12-plus-backlog.md)
- New milestones / specs → `docs/specs/`, indexed at [`CLAUDE.md`](CLAUDE.md)
- Architectural decisions → `docs/architecture.md`

This file is the dump location for new debt as it's found. Add a bullet under **Active backlog** with enough detail that a future session can pick it up cold:
- file + line range (or symbol)
- one sentence on *why* it's debt (not "what" — the code shows what)
- proposed action (delete / inline / extract / replace / verify)
- any blockers (needs telemetry, needs a mini-spec, depends on another item)

When an item ships, delete it from the file. The file is the live
backlog, not a history log — `git log` is the history layer.

## Active backlog

Lens: **vibe-coded remainder** — LLM-autopilot residue from
AI-assisted iteration the recent polish arc didn't catch. High
confidence after verification (call sites traced + bodies read), not
"I spotted a code smell." See § Audit guidance below for the
patterns.

### This week (execution slate)

Remaining after the 2026-05-28 unblocker arc (shipped: webapp source maps, connector revision pinning, local Dex OIDC harness at `dev/oidc-local/`, cycle fixtures + Vitest harness at `tests/fixtures/cycles/` and `webapp/lib/**/__tests__/`, React #185 render-phase fix, L2/L3-terminal empty-historical fix):

1. **TermNorm wire `model`** — cross-repo edit at `C:\Users\dsacc\OfficeAddinApps\TermNorm-excel\backend-api`. With the connector revision-pin landed (`promptpotter/connectors/protocol.py::Connector.{expected_revision, version_check}`), the TermNorm-side PR adds `model` to the per-request response + a `/version` endpoint; this repo bumps `termnorm.py::_EXPECTED_REVISION` to the new SHA and deletes the `_synth_legacy_backend_record` back-fill in `presentation/api/routers/auth.py`.

### Operator-steered-fork drift (v0.8.1 — found 2026-06-03)

The large steer-fork feature (10 phases + 4 refinements + the chat-Origin consolidation) left these. Knots 1–4 shipped in the v0.8.1 panel-fix arc: `operator_endorse` collapsed (seed now required; `OPERATOR_STEERED` is the sole operator trigger); the twin editors split + renamed (`PipelineConfigEditor` → `AllowedValuesEditor` for ingest allowed-values; `NodeConfigEditor` is the steer/value editor, now also rendering the read-only node detail schema-driven); the dashboard→chat steer routing collapsed (`ScoringInspector` opens the steer `Dialog` directly — no tab hop, no node+candidate co-presence inference); the unregistered "Substitute candidates" ghost button cut. The dead `optimizer_locks` read path went with them (`PipelineSchema.optimizer_locks()` + the served field + `cv.optimizerLocks` deleted; ingest keeps its own `derive_optimizer_locks` draft path). Remaining:

1. **Reconcile defaults snapshot `dash` at mount while the parent keeps polling.** `forkReconcileDefaults`/`LimitReconcile` freeze spend/round "remaining" via `useState(() => …)`; a long edit session shows mount-time remaining, not current. *Why debt:* latent staleness seam — intentional (avoids clobbering the operator's typed values) but undocumented, so a future reader may "fix" it into a clobber. **Action:** one-line comment affirming the snapshot is deliberate, or recompute-on-reopen. **Blocker:** none.

### Entries

### Deep indirection — scoped, not slated

The three below are architectural collapses, not sittings. Each is multi-file, has plausibly load-bearing constraints worth verifying before committing, and is sized for spec-buddy to draft a mini-spec ahead of the work. Read the entry's *load-bearing check* before assuming the collapse is free.

- **Typed-view roundtrip on every `PhaseRecord` is a back-compat shim against a constraint this project doesn't have** —
  `presentation/views/view_models.py` (404) + `presentation/views/view_ingress.py` (523) carry a 4-stage transform: `PhaseEvent` → `from_phase_event(event, ctx)` → typed `AnyView` dataclass → `view_to_wire_dict(view)` → JSON dict → embedded in `PhaseRecord.payload['view']` on the ledger → subscriber reads it back through `view_from_record(record)` (with paired `_pure_reconstruct` + `_custom_reconstruct` registries) → typed `AnyView` again. The roundtrip's purpose is to persist a typed view through JSON so future readers see the same shape — i.e. ledger-format compatibility across View-shape changes. CLAUDE.md says no back-compat: there is nothing on disk to be compatible with, and no released versions. Every View field today must be updated in three places (dataclass + `from_phase_event` arm + `_*_from_dict` reconstructor) to keep the roundtrip honest.
  **Action:** stop embedding the view in `PhaseRecord.payload['view']`. `RunCallbacks.on_phase` emits just `PhaseRecord(phase, event, round, payload={"data": event.data})`. Subscribers (`live/display.py`, `webapp` SSE) compute the rendered shape on demand from `payload["data"]` + the phase/event key — same code path, no roundtrip. Drop `view_to_wire_dict`, `view_from_record`, both reconstructor registries, the corresponding `_*_from_dict` helpers, and `from_phase_event`'s view-construction branches (keep only its `ViewContext` side effects if any survive scrutiny).
  **Load-bearing check:** confirm no subscriber today reads `payload['view']` from a *historical* record — i.e. one written by a prior version. If `EventStreamView` SSE replay leans on the embedded view, the collapse needs the subscriber to gain a render path first. Grep: `payload.get("view"` + `record["view"`.
  **Estimated delta:** ~500 LOC removed across `view_ingress.py` (most of it) + `view_models.py` (per-event dataclasses; keep the small renderer-input shapes like `ScoreEntry` if `live/display.py` still consumes them) + ~5 lines in `run_observers.py`.
  **Pattern:** serialization roundtrip whose only purpose is shape-compat — eliminated by the no-back-compat rule.

- **Two writer APIs over the canonical ledger (`RunCallbacks` ↔ `emit_*`)** —
  `application/run_observers.py::RunCallbacks` is the typed-event constructor over `CycleEventLog.append` (one method per event class, ledger held as instance state). `infrastructure/llm/models.py::emit_*` (per-call telemetry like `emit_token_usage`, `emit_command`, `emit_command_ack`) is the kwargs-only helper that reads the active ledger from `_CYCLE_LEDGER` ContextVar and appends. Both write the same `CycleRecord` discriminated union to the same `events.jsonl`. CLAUDE.md (application/CLAUDE.md "Per-call telemetry from deep async chains uses the `emit_*` shape") frames them as different *use cases* (high-frequency runner-driven vs deep-async per-call), but mechanically they're two ingresses for one channel — the ContextVar makes the instance-held ledger redundant.
  **Action:** fold `RunCallbacks` methods into `emit_*` helpers reading `_CYCLE_LEDGER`. `on_phase` becomes `emit_phase(phase, event, round, data)`; `on_round_complete` becomes `emit_round_display(...)`; etc. `build_run_observers` sets the ContextVar at runner startup (it already does, for the existing `emit_*` callers — `set_cycle_ledger`); every callsite stops carrying a `RunCallbacks` reference. The `ViewContext`-stateful pieces (round tracking, origin rolling, prompt-flat memo) either move into projection state or become explicit ContextVars alongside `_CYCLE_LEDGER` / `_CURRENT_ROUND`.
  **Load-bearing check:** the stateful `_phase_ctx: ViewContext` on `RunCallbacks` is the real architectural question — if any of its fields are *write-then-read* across phase events within the same cycle (not just stamping records with current state), the collapse needs a named home for that state. Best candidate: a `PhaseContextView` projection that subscribes to the ledger and exposes the same fields the live display reads.
  **Estimated delta:** ~80 LOC removed from `run_observers.py` (the class + its methods); ~30 LOC added across `infrastructure/llm/models.py` for the new `emit_phase` / `emit_round_display` / `emit_snapshot` helpers. Net negative ~50 LOC and one fewer ingress to reason about. Compounds with item above (the typed-view roundtrip is what `on_phase` exists to do).
  **Pattern:** two ingresses for one channel; the older one is now indistinguishable from the newer except by method-vs-function shape.

- **`from_disk_round` / `from_disk_log` rebuild the same shapes the live ingress already emits** —
  `presentation/writers.py:162-204` (`from_disk_round`) + `:234+` (`from_disk_log`) read `round_NNNN.json` + `index.json` from disk and reconstruct `RoundCompleteView` / `LogMdView`. The live path (`from_phase_event`) builds the *same* views from `PhaseEvent`. So a View's field set must be reachable from both the event stream AND from the persisted JSON — every new field needs both producers. `round_NNNN.json` is itself an `AuditTrailView` projection of the ledger, so this is: ledger → projection → JSON → reverse-projection → View. Two of those four hops exist only because the writer doesn't subscribe to the ledger itself.
  **Action:** have `write_log_md` / `write_review_md` subscribe to the ledger like other projections (or accept a streaming view from the existing audit projection), instead of reading the on-disk JSON back and reconstructing. Eliminates the disk-replay reconstructors entirely.
  **Load-bearing check:** post-cycle markdown writes today happen at runner milestones (not from a live subscriber) because the writer wants the *complete* round set, not in-flight events. Verify whether the live `AuditTrailView` already exposes a "drain to final shape on cycle end" hook — it does (`DerivedView.drain()`); the writer should be able to receive the drained snapshot directly. The only real question is whether `from_disk_log` / `from_disk_round` are also called from post-mortem analysis paths (operator opens an old cycle dir and re-renders) — `grep` says no external callers, but worth a second pass.
  **Estimated delta:** ~250 LOC removed across `writers.py` (the two reconstructors + their helpers). Largely dependent on item 1 above landing first — if the typed-view roundtrip stays, the writer either keeps the disk-replay or grows a different ledger subscription.
  **Pattern:** parallel reconstruction paths for the same shape — collapses once the writer reads the canonical source instead of its on-disk projection.

### Standing entries

- **`RunPhase.STOPPING` has a thin window for non-paused stops** —
  the runner declares `stopping` (`application/run_phase_control.py`)
  at its own cooperative checkpoints: the pause-barrier stop-check
  (`runner/loop.py`) and the two scoring stop-checks
  (`scoring/query_loop.py`, `scoring/sample_measurement.py`). For a
  *running* (non-paused) stop, the operator's `stop.flag` is only
  observed at the next scoring checkpoint, so a stop landing near a
  round boundary can jump `running → terminal(interrupted)` without a
  visible `stopping` frame. The honest single source for "stop
  requested, not yet exited" is the moment the flag is written — the
  `stop-cycle` command applier (`presentation/api/middleware/command_dispatcher.py::_apply_stop_cycle`),
  which already has ledger access (it writes the `CommandRecord`).
  **Action:** have `_apply_stop_cycle` append a `control`
  `PhaseRecord(event="stopping")` to the target cycle ledger alongside
  writing the flag, so `LiveDashboardView` projects `stopping` the
  instant the operator clicks — independent of where the runner is in
  the round. Then the three in-runner `declare_run_phase(…, STOPPING)`
  calls become redundant and can be dropped (the flag-write is the
  single declaration point).
  **Load-bearing check:** confirm the dispatcher runs in-process with
  the runner's `LiveDashboardView` subscriber (cycle-targeted commands
  are applied by `RunnerCommandSubscriber` in the runner process) so
  the appended record actually fires the projection; if the applier
  runs in a context without the live subscriber, the declaration won't
  surface until the runner next drains. Also verify the CLI Ctrl+C
  path (no command) still goes straight to `terminal(interrupted)` —
  it has no `stopping` frame by design.
  **Pattern:** control-state declared at the actor's checkpoints
  instead of at the point of intent; >2 days because the cross-process
  in-vs-out-of-runner verification is the real work.

- **TermNorm backend reports a provider slug, not a model** — backend
  `dashboard.json::spend.backend.model = "openrouter"` is the provider,
  not the actual upstream model (e.g. `mistralai/mistral-7b-instruct`).
  Without the real model on the wire, $ for backend usage cannot be
  derived from `shared.spend.lookup_rate(model)` × tokens; the
  Account modal's Activity pane back-fills $ from
  `dashboard.json::spend.backend.total_usd` instead. **Action:** wire
  TermNorm's per-request response to carry the upstream `model` string
  (cross-repo edit at the sibling backend
  `C:\Users\dsacc\OfficeAddinApps\TermNorm-excel\backend-api`). Once
  the wire carries `model`, drop the `_synth_legacy_backend_record`
  back-fill in `presentation/api/routers/auth.py`.
  **Pattern:** missing telemetry field at the wire boundary.

<!-- round_summary.py + factory.py revisit (2026-05-26): both KEEP.
  round_summary.py = named Python→Pydantic adapter
  (RoundResult → RoundSummary); inlining would push raw
  RoundSummaryCandidate(...) constructor calls into _handle_phase
  (wrong abstraction layer in a 920-line projection class).
  factory.py = resume-time disk-reconciliation; for_session docstring
  explicitly commits the classmethod to "thin assembly", and
  resolve_resume_state's stale-pointer healing (_max_round_on_disk +
  prior-state merge) is a named concern that earns its own file. -->

## Audit guidance — what to hunt for

The bar for entries here is **high confidence after verification**,
not "I spotted a code smell." Generic-smell audits flood the backlog
with debatable items. These six patterns merit deletion, each with a
precedent from the closed arc.

### Pattern: premature optimization with apologetic docstring
Code that protects against a scenario that doesn't actually occur,
often hedged by a comment ("for perf", "cached because", "in case
the schema changes"). Verify by reading call sites + measuring
hit-rate / fire-rate. If the protected scenario provably can't
happen, or fires never/rarely on real campaigns, it's debt.
**Precedents (deleted):** `_apply_budget` shed allocator (fired
only when composed prompts exceeded 10k chars; real composed
prompts capped at ~4.7k mandatory + ~3k static = under 8k);
`catalogues.py` global pipeline-param cache (one-entry, sub-ms
render).

### Pattern: redundant double-protection
Two guards on the same condition where one strictly subsumes the
other. Verify by writing the decision boundaries (e.g. two-sided
95% CI: z=1.96 vs one-sided ε=0.05: z=1.645) and confirm one
swallows the other's legitimate cases. **Precedent (deleted):**
PoBB separability floor sitting on top of the Bayesian gate
(strictly stricter; swallowed every mid-budget abort the gate
wanted to fire).

### Pattern: single-caller indirection without architectural reason
Modules / helpers / classes consumed by exactly one caller, with no
test of their own + no layer-boundary justification. Skip splits
that cross a load-bearing layer
(`application/intelligence/ ↮ application/optimization/` per the
invariant) or have their own dedicated test in `tests/`.
**Precedents (inlined):** `l2_driver.py` + `l3_driver.py` →
`executor.py`; audit-1.C `candidate_block` + `score` + `sample` +
`pobb` → `view.py`.

### Pattern: dead exception paths / dead enum variants
Enum members + their handler arms left behind after the code path
that raised them was deleted. Verify by `grep` for every variant —
if the only references are the enum definition + handler arms with
no `raise` / construction site, the variant is debt. **Precedent
(deleted):** `StopReason.PROMPT_BUDGET` after `_apply_budget`
removal.

### Pattern: speculative API surface
Parameters accepted but never read; optional return types `X | None`
where every return is non-None; default kwargs no caller overrides;
Pydantic / dataclass fields declared but never populated. Verify by
tracing call sites + reading the body. **Precedent (deleted):**
`L1Variant.target_axis` + `.reasoning` — the docstring claimed
"persisted in the audit trail but doesn't read them at runtime,"
but l1_behavior validators substring-matched them as
peaked-axis / rebut signals. Resolved by routing both signals
through `pipeline_params_override` keys + `changes_description` +
the citation string, then deleting the fields.

### Pattern: bug blocked on operator-local context
Bug repro requires an environment, fixture, or sibling repo not in the
tree (auth-on tunnel deploy, a specific cycle dir on the maintainer's
laptop, a co-owned backend repo). Default action: **promote the
unblocker before the fix.** Build a local mock harness, check a frozen
fixture into `tests/fixtures/`, or pin the cross-repo dependency — so
the bug becomes reproducible from a clean `git clone` by any
collaborator. Then ship the fix on top.
**Precedents (this arc, 2026-05-28):**
- React #185 → local Dex OIDC harness (`dev/oidc-local/`) + production
  source maps (`webapp/next.config.ts`); the fix at
  `FitnessPanel.tsx::seededHere` then landed against the harness.
- L2/L3-terminal hang → checked-in `tests/fixtures/cycles/l2_terminal/`
  + Vitest harness at `webapp/lib/derivations/__tests__/`; the
  empty-historical fix landed against the fixture, not against the
  operator's laptop.
- TermNorm wire `model` → `Connector.expected_revision` +
  `version_check` (still pending the actual cross-repo edit, but the
  drift detector is in place so the next mismatch is caught at session
  start instead of weeks later in spend accounting).

### Pattern: vibe-coded scaffolding
Half-finished branches behind `raise NotImplementedError`, enum
variants promising dynamism the system never delivers, comments
referring to future work the project doesn't plan to build. The
root `CLAUDE.md` is explicit: "Document current state, not
half-done plans." **Verify the "future" actually isn't on the
roadmap before flagging** — `ForkTrigger.L2_REBASE` / `L3_REBASE` /
`OPERATOR_REWIND` looked like vibe-coded scaffolding behind a
`NotImplementedError` branch, but `m10-prompt-iteration-framework.md`
explicitly schedules them for wiring. They're now active backlog
("Wire rebase emission") instead of a delete candidate.

### Anti-patterns to skip
These are NOT debt — skip on sight:
- Intentional UI placeholders for M13+ (see § below)
- Per-injection `char_cap` (LLM-overrun truncation; real boundary
  guard)
- Domain vocabulary policed elsewhere (`origin` not `baseline`,
  `sample` not `query`)
- Layer-invariant splits (`application/intelligence/` ↮
  `application/optimization/`)
- ABC `@abstractmethod` / `Protocol` `...` bodies
- `from __future__ import annotations` (standard PEP 563)
- Boundary guards at external-input sites (file I/O, JSON ingest)
- Validators on user-config Pydantic models with `extra='forbid'`
- `_*` private helpers used by exactly one caller in the same file
  (intra-file decomposition isn't inter-file indirection)

### Next-round audit angles
The closed arc + the current backlog drained the obvious vibe-coded
classes. Remaining productive angles for future re-audits:
1. **`dict[str, Any]` parameter soup in hot paths** (polish-D.1
   typed `view_ingress`, but `RoundResult` / `CandidateResult` /
   `PipelineParams` payloads remain). M-sized refactor, own arc.
2. **Test charter violations** — substring assertions on rendered
   text, stub-forest regression tests, tests for trivial wrappers.
   The charter caps the suite at ≤200 collected tests; currently
   199.
3. **Stale `Field(description=...)` strings on LLM-facing schemas** —
   load-bearing per [[feedback-field-description-load-bearing]] but
   some may have drifted from current behavior.
4. **INFO/WARN-level logging for events nobody actually surfaces** —
   log noise audit.
5. **Error-raising style diverges by layer** — generic `Exception` catch in
   `application/optimization/dispatch/hub/facade.py`, bare `raise` + asserts in
   `infrastructure/store/campaign_store/cycles.py`, `HTTPException` in
   `presentation/cli/commands/new.py` for the same class of validation failure.
   An agent can't predict which to raise. M-sized standardization arc (domain
   exception + one HTTP-mapping seam), not a single fix.

## M13+ intentional UI placeholders

UI affordances the product *intentionally* ships disabled today — they
preview the M13+ chat-first UX + config-edit surface + analytics-search
surface. They are **not** scaffolding, not credibility hits, and not in
scope for any "hide non-functional controls" sweep.

| Placeholder | File | Future surface |
|---|---|---|
| Topbar search input (disabled) | `webapp/components/shell/Topbar.tsx:29` | M13+ analytics search |
| ChatPane attach + textarea + send button (disabled) | `webapp/components/chat/ChatPane.tsx:273-279` | M13+ chat-first operator UX |
| ChatPane Extended-thinking / Web-search / Code-execution toggles (`toggle locked`) | `webapp/components/chat/ChatPane.tsx:286-322` | M13+ chat-first feature toggles |
| ~~ChatPane "job-footer" — "Adjust spend / finishing criteria — wired in M12"~~ | — | **FULFILLED 2026-05-30.** The placeholder is now the live `SpendBudgetControl` (`change-spend-budget`) in the job-bar dropdown. No longer a placeholder. |
| ~~ConfigMenu — gear icon + frozen-parameters panel~~ | — | **CONSOLIDATED 2026-06-02.** Replaced by `BackendNodeDetail` (`webapp/components/dashboard/pipeline/BackendNodeDetail.tsx`), opened by clicking the backend node in `TargetPipelineHero`. Read-only config-on-the-node (model/thinking/lock + origin prompt), derived from the real `optimizer_locks` (no hardcoded map). The editable surface stays deferred to the M12 control-plane (`// M12` seam in `BackendNodeDetail`). |
| AccountModal "Update profile" button (disabled) | `webapp/components/account/AccountModal.tsx:193-200` | M13+ profile-editing surface |
| AccountModal "Remove account" menu item (disabled) | `webapp/components/account/AccountModal.tsx:251-258` | M13+ multi-provider account management |
| AccountModal "+ Connect account" button (alerts then no-ops) | `webapp/components/account/AccountModal.tsx:267-278` | M13+ multi-provider account linking |

**Rule:** any future cleanup that touches these surfaces must
distinguish *intentional placeholder* from *scaffolding text/comment*.
Milestone-reference text inside these placeholders is OK (and exempts
them from a "no M-milestone references on operator surfaces" final-grep
gate); other operator surfaces still must not leak milestone numbers.

## History

All prior tiers + the pre-public-release polish arc (Tiers 0–6 + polish
A–E + audits 1–3) closed by 2026-05-25. Done-log entries lived here and
were pruned with the arc; recover via `git log` if needed.
