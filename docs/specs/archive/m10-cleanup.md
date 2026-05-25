# M10 Cleanup — restate the architecture, cut the drift

**Status: COMPLETE.** Gate-by-gate close-out in
[`m10-cleanup-results.md`](m10-cleanup-results.md); arc closed at
commit `93291910` ("M10 done"). Sister spec
(`m10-prompt-iteration-framework.md`) carries the open M10 work.

Sub-spec under M10. Sister to `m10-prompt-iteration-framework.md`.

The goal of this work is **to make drift visible at the source**. The
PromptPotter loop is simple. The codebase has accumulated parallel
mechanisms, double-named concepts, and speculative complexity that
obscure that simplicity. This doc restates the architecture on one page,
audits the current codebase + docs against it, and lists the cuts.

Future PRs measure against §0. If a change doesn't fit a bucket on this
page, the change is wrong (or the page is — but updating §0 is a
deliberate act, not an emergent accident).

**§0 + §0.5 live in [`docs/architecture.md`](../architecture.md)**
— extracted there per Execution order step 0 (landed). This spec
references §0 / §0.5 by pointer; read the architecture file first,
then come back here for §1–§6 cleanup work.

---

## §0 + §0.5 — moved to `docs/architecture.md`

Both sections are extracted to [`../architecture.md`](../architecture.md)
as the long-term architecture reference. References from §1–§6
below are by pointer.

---


## §1 — Reality audit

Walk every doc and mark each claim against §0:

- `README.md` (root)
- `CLAUDE.md` (root) + the **per-directory CLAUDE.md tree** (the
  layered-disclosure tier): `promptpotter/CLAUDE.md`,
  `promptpotter/application/CLAUDE.md`,
  `promptpotter/domain/CLAUDE.md`,
  `promptpotter/infrastructure/CLAUDE.md`,
  `promptpotter/presentation/CLAUDE.md`, `tests/CLAUDE.md`. These
  files cite symbols (`cadence/`, `Decision`, `CycleLedger`, `SIGNALS`,
  `compile_l*_surface`, etc.) that the §2/§3/§4.5 renames change —
  the audit produces the rename-cascade map (which file mentions
  which old symbol → which new symbol) so the rename PRs update them
  in lockstep.
- `docs/concepts/*.md`
- `docs/operations/*.md`
- `docs/developer/*.md`
- `docs/specs/*.md` (including sub-specs and `archive/`) — specs
  drift too; an aspirational-spec future is just as much of a lie as
  an aspirational README claim.

Tag each claim:

- `true` — present in code, fits §0. Keep.
- `aspirational` — claimed but not present, or partially present. Either
  finish the work or trim the claim. Default: trim.
- `dead` — referred to code that no longer exists, or contradicts §0.
  Delete on sight.

Output: one annotated punch-list (`docs/specs/m10-cleanup-audit.md`)
plus the trim PRs. The audit is throwaway scaffolding — the trims
are the deliverable.

The reality audit precedes §§3-4 because we need to know the full
scope of the doc churn before we start renaming files.

**Code violations of §0 surface here too.** The audit isn't only
docs-vs-claims — it's also code-vs-§0. Specifically grep for:

- Per-error retry of `(sample, candidate)` pairs (violates "no retry
  of the same pair after a technical error").
- Single-failure aborts of a candidate (violates "tolerant by default
  — abort only after ~3–4 failures pile up").
- Sidecar error projections / sidecar prompt-fill paths (violates
  "no out-of-band state mounting; one ledger ingress, one
  `INJECTIONS` registry").
- In-round LLM calls outside `l1_generate` / `l1_critique` (violates
  "no mid-round LLM diagnostic").
- Optimizer code that **reads** tracing data (violates "tracing is
  fan-out only" — state must reach the optimizer via the ledger,
  never via the trace mirror).
- Hardcoded backend names, node names, or parameter names in
  `application/` or `domain/` — anything that isn't sourced from a
  `pipeline.json` or `optimizer_pipeline.json` (violates
  "pipeline-agnostic; zero hardcoded target-system knowledge").
  TermNorm-specific switches belong only in
  `promptpotter/connectors/termnorm.py`; the connector boundary is
  the only place where backend identity is named.
- State that crosses round boundaries living **only in memory** with
  no disk mirror, or material facts surfaced **only via stdout/CLI
  logging** with no file write (violates "everything material lives
  on disk, in human-readable form" — the AI-accessibility principle).
- **Write-side data duplication between the ledger and the projection
  outputs** — the same fact written by both `CycleLedger.append`
  and a projection's own file write. Pick one source per fact: either
  the ledger is the truth and the projection is a derived view
  (regenerable on demand), or the projection is the truth and there's
  no ledger entry. "Both write the same thing" is drift. The audit
  table maps every projection-written field → its ledger event (or
  marks it ledger-only / projection-only). Output:
  `docs/specs/m10-cleanup-ledger-vs-projections.md`.
- **Hexagonal layer leaks** — `domain/**/*.py` files importing from
  `promptpotter.application.*` or `promptpotter.infrastructure.*`
  (excluding `TYPE_CHECKING`-gated imports, which are legitimate).
  Today: zero confirmed runtime leaks; the
  `domain/sample.py` → `SampleIndex` import is `TYPE_CHECKING`-gated
  and OK. Strengthen `tests/test_invariants.py` (in `test_no_unexpected_runtime_layer_violations`) to grep `domain/`
  explicitly so future leaks fail loud.
- **`observed_node()` coverage on every optimizer LLM call.** Each
  of `l1_generate`, `l1_critique`, `l2_context`, `l3_plan` wrapped.
  Today: `l1_critique`
  (`application/optimization/l1_critique.py::run_l1_critique`) is
  unwrapped while the other three are wrapped. Either wrap or
  document why not (per §6 question 8).
- **MeasurementArchive direct access outside the §3.7 facade.** Once
  the facade lands, `store.archive.*` calls outside it are drift.
  Today: 13 such call sites
  (`infrastructure/tracing/replay.py`, `presentation/api.py`,
  `presentation/writers.py`,
  `application/bootstrap/scoring_context.py`,
  `application/scoring/search_point_scorer.py`,
  `application/optimization/elevation.py`,
  `application/intelligence/indexes/axis.py`); §3.7 migrates them.

**Webapp + API + writers audit (extension of the docs walk).** §1
also audits surface that lives outside `promptpotter/` but is part
of the operator's interface:

- `webapp/components/dashboard/` — list every `.tsx`. For each:
  what does it render, which `dashboard.json` field does it read?
  Specific decisions: `ChatPane.tsx`, `WorkflowCanvas.tsx` — keep
  (load-bearing) or drop?
- `presentation/api.py` — list every FastAPI route. Specific
  decisions: `/datasets/{name}/preview` — load-bearing (today's
  hard-sample leaderboard data path) or aspirational?
- `presentation/writers.py` — `refresh_tenant_leaderboards()` writes
  `runs.md`, `individuals.md`, `hard_samples.md`, `README.md` to
  `archive/`. Decision: load-bearing operator surface or
  aspirational?
- `infrastructure/tracing/mlflow_sink.py` — conditionally wired
  alternate sink. Decision: name in §0 alongside Langfuse, or drop?
- `l1_critique_text` rendered prompt — only in
  `langfuse/events.jsonl`, not on disk via the ledger. Per §0
  ("everything material lives on disk") — pick a side: ledger it or
  document trace-only.

Each violation lands as either a code-trim PR alongside the doc trim,
or as a follow-up issue if the fix is non-trivial.

**OptSearchPoint field audit.** `OptSearchPoint` has 22 fields
(14 own + 8 inherited from `PromptTemplate`'s eight prompt-fields).
Each field must:

- Map to a §0 bucket (lineage / L2 surface / L3 surface / memory /
  scoring projection).
- Have a documented writer (which layer sets it).
- Have a documented reader (which prompt or which `application/`
  function consumes it).
- Survive copy_memory_to semantics correctly (in `MEMORY_FIELDS` if
  it should persist across L2/L3 transitions; not in if it
  shouldn't).

Fields without a bucket, or without both a writer and a reader, are
drift. Output: a per-field table at
`docs/specs/m10-cleanup-osp-fields.md`; drop the orphans in §4.

**Free-deliverable verification.** §0 claims the hard-sample leaderboard
is a free side-deliverable. Audit:

- Does the dashboard exist today? Where (file path / webapp panel /
  notebook cell)?
- If it surfaces in the webapp, which `dashboard.json` field does it
  read?
- If it doesn't exist: build it now as part of M10 (it IS supposed
  to be free — Rasch sort already produces the data) **or** trim
  the §0 "free side-deliverables" claim. No "aspirational claims in
  §0."

Apply the same verification to every other "free" claim §0 makes
(today: only the hard-sample leaderboard).

**Self-optimization fixture.** §0 claims accumulated `OptSearchPoint`
data is "the dataset for optimizing the optimizer." `load_potter_traces`
(`application/datasets/datasets.py:559`) already emits the rows. M10
adds the on-disk artifact that makes the claim concrete:

- Build a tiny golden trace-replay fixture under `datasets/promptpotter/`
  containing `dataset.md`, a minimal `task_description.md`, and one or
  two archived rounds of `(round_context → next_brief → score_delta)`
  rows. Sibling shape to `datasets/termnorm/` and `datasets/gsm8k/`.
- Goal: the data shape exists on disk **before** any consumer (the M11
  PromptPotter-as-backend connector is the first real consumer). M10
  pays the cost of the fixture; M11 builds the connector against it
  without designing the data shape from scratch.
- This is the M10 deliverable that promotes "self-optimization" from
  an aspirational §0 bullet to a verifiable artifact. The M11/M12 work
  (connector + outer-loop run) lands against this fixture.

---

## §2 — Canonical dispatch hub (and the signal → injection rename)

Goal: `dispatch_hub.py` is the **only** file someone opens to answer
"what info flows into prompt X."

Deliverables:

- **Rename the registry from "signal" to "injection"** — it is
  domain-specific language for "deterministic state injected into a
  prompt slot," and it kills the name collision with the
  escalation-side former-"signal" terminology in one stroke. Code
  changes:
  - `dispatch_hub.SIGNALS` → `dispatch_hub.INJECTIONS`
  - `_Signal` → `_Injection`
  - `SignalKind` → `InjectionKind` (values stay:
    `MEASUREMENT` / `DERIVED` / `TRACE` / `DIRECTIVE`)
  - `_r_*` renderer functions stay (they're already named after what
    they render, not after the kind)
  - Cascade through every doc: `docs/developer/dispatch-hub.md`,
    `docs/developer/README.md`, root `CLAUDE.md`, etc.
- **One injection table per prompt** in
  `docs/developer/dispatch-hub.md`: rows = injection names; columns =
  which prompts consume it. Each cell is ✓ or blank. Replaces the
  current narrative. Anyone reading the table knows in 10 seconds
  what L1_critique sees vs. what L2 sees.
- **`INJECTIONS` docstring** at the top of `dispatch_hub.py` covers
  the four kinds and the rule: "to add an input to any prompt, add an
  injection here. Anything else is drift."
- **`validate_template` stays** as the typo catcher at module load.

Non-goals: don't change the rendering pipeline, don't introduce new
injection kinds. Just rename + make the canon discoverable.

---

## §3 — Bundle escalation, kill "cadence" + "signals" collision

**Step 0 — build `decide_escalation()` as the actual single
function.** Today the decision logic is split across:

- `application/optimization/cadence/evaluator.py::evaluate_round` —
  post-round rule-engine match.
- `application/optimization/escalation/firing.py` — `escalate_l2`
  + `_run_transition` strategy dispatch.
- `application/optimization/escalation/state.py` — `NextAction` enum.
- `application/optimization/transitions.py::run_layer_transition` —
  shared LLM-call template for L2/L3 fires.

The §0 architecture calls for **one** function:
`decide_escalation(EscalationInputs) -> EscalationDecision`. That
function does not exist today. Build it first by routing
`evaluate_round`'s body + `firing.py`'s strategy dispatch behind a
single entry point. THEN do the rename pass below. Otherwise the
rename moves around drift instead of fixing it. (Step 0 is reversible
— strictly additive; existing call sites can be migrated one at a
time.)

Move + rename (after step 0 lands):

- `application/optimization/cadence/` → folded into the existing
  `application/optimization/escalation/` directory. The `cadence/`
  directory disappears.
  - `cadence/rules.py` → `escalation/rules.py`
  - `cadence/evaluator.py` → folded into `escalation/state.py` or a
    sibling `escalation/decide.py`
  - `SignalInputs` → `EscalationInputs`
  - `evaluate_round` → `decide_escalation`
  - `DEFAULT_ROUND_RULES` → `DEFAULT_ESCALATION_RULES`
  - `CadenceRule` → `EscalationRule`
- Update root `CLAUDE.md` backbone table: drop the `cadence` row;
  the existing `EscalationFSM` row absorbs the rule-engine description.
- Update every doc reference (output of §1).

Behavior is unchanged. Same priority-sorted first-match-wins. Same
opt-in `l2_axis_yield_drought` rule. The word "cadence" disappears
from the codebase.

---

## §3.5 — Pin the `pipeline.json` contract

The backend's API surface to PromptPotter. Today it's implicitly
defined by what TermNorm publishes — fine while there's one
connector, dangerous as soon as a second arrives. Pin it explicitly
**before M12 onboards a second connector**, not after.

Deliverable: a one-page contract doc at
`docs/developer/pipeline-json-contract.md` that a new connector
author reads first.

Required content:

- **Top-level required fields:** `name`, `nodes` (list of
  `PipelineNode`), `optimizer` (sub-object).
- **`PipelineNode` required fields:** `name`, `type`, `runtime`,
  `short_circuit`, `node_type` (`"candidate_source"` |
  `"ranker"` | `"enricher"` | `"cache"` | `""`), `description`.
  Optional: `prompt_meta`, `output_schema`, `input_schema`.
- **`optimizer` sub-object required fields:** `param_keys` (list of
  wire names), `observation_mappings`, `langfuse_type`.
- **Strict parsing.** `parse_pipeline_response()` must reject
  unknown top-level keys (Pydantic `extra="forbid"`). No
  silent-default forgiveness — the contract is the contract.
- **No optional-with-PromptPotter-defaults fields.** Either the
  field is required and connector-supplied, or it's optional and
  PromptPotter ignores it absent. The "TermNorm doesn't supply X
  so PromptPotter assumes Y" pattern is what makes a second
  connector painful — kill it before it grows.
- **One example pipeline.json** (TermNorm's, sanitized) with every
  field annotated.
- **`optimizer_pipeline.json` parity.** `optimizer_pipeline.json`
  loads through the **same** parser as a backend's `pipeline.json`.
  Deliverable: a parity test
  (`tests/test_optimizer_pipeline_parity.py`) that loads both files
  via `parse_pipeline_response()` and asserts both succeed under
  `extra="forbid"`. If the optimizer pipeline ever drifts in shape
  from a backend pipeline (special-case fields, parallel registries,
  ad-hoc keys), the test fails. Pinning this in M10 means §0's
  self-optimization claim ("PromptPotter runs on
  `optimizer_pipeline.json`, same shape as a target backend's
  `pipeline.json`") becomes verifiable, not just asserted — and the
  M11/M12 PromptPotter-as-backend connector (a Connector wrapping
  L1/L2/L3 against `optimizer_pipeline.json`) becomes the cheapest
  second connector instead of a refactor.

Side benefit: pinning the contract makes §6 pre-flight question 1
("which §0 bucket does this belong to?") answerable for new
connector work — the answer is "pipeline-agnostic; it goes in
`promptpotter/connectors/{name}.py` against the documented
contract."

---

## §3.6 — Simplify resume + fork-on-divergence implementation

The mechanism is load-bearing (the user runs `--from N` regularly,
`--fork-on-divergence` is the safety net). The **implementation** is
spread thin and badly bundled: `Decision` / `DecisionKind` /
`DECISION_GATING` records, `DecisionRecord` in `cycle.py`,
`inherit_from()` on `CycleLedger`, `forks/` / `diag/` / `sweeps/`
directory layout, `parent_cycle_id` back-pointers, fork-directory
path helpers in `infrastructure/store/paths.py` + `domain/cycle_paths.py`,
replay-vs-archival gating, divergence detection in `runner.py`,
sibling-mint logic. Touches at least seven files.

When fork-on-divergence breaks (and it does), debugging is "open five
files and reverse-engineer the dance." That's drift in implementation
even though the mechanism is clean on paper.

**Goal: bundle the entire resume + fork machinery into one module.**
Indicative target:

```
application/optimization/resume_and_fork/
  records.py        # the *Record types, kinds, gating policy
  resume.py         # replay decisions, detect divergence
  fork.py           # mint sibling, inherit from parent, path helpers
  __init__.py       # public surface (3-5 functions, max)
```

Cleanup steps (one PR each):

1. **Map the current footprint.** Grep every file that touches
   `Decision*`, `inherit_from`, `parent_cycle_id`, `forks/`,
   `--fork-on-divergence`, `--from`, divergence detection. List in
   `docs/specs/m10-cleanup-fork-audit.md`. This is §3.6's §1.
2. **Move records into the new module.** No semantic change; only
   imports shift. Renames (`Decision*` → `ResumeCheckpoint*`) ride
   along since they're already in §4.5.
3. **Move resume + divergence logic in.** Today it lives partly in
   `runner.py` and partly in `cycle.py`. Consolidate.
4. **Move fork-mint + path helpers in.** Today scattered across
   `infrastructure/store/paths.py`, `domain/cycle_paths.py`,
   `application/optimization/cycle.py`. Consolidate.
5. **Drop the surrounding scaffolding** (re-exports, intermediate
   helpers) that exists only to bridge the scattered pieces.

**Lands early in the execution order** because:

- Many other cleanup steps touch files that contain fork code
  (`runner.py`, `cycle.py`, `paths.py`); consolidating fork first
  means those later passes operate on smaller, focused files.
- The §4.5 renames operate on the consolidated module rather than
  five scattered ones.

Keep the existing test suite for resume + fork (`tests/test_rescore_and_fork.py`)
green throughout — it's the regression net for "did we break the
mechanism while consolidating its files."

Out of scope for §3.6: changing the fork SEMANTICS (when a fork
mints, what counts as divergence, what `--from N` skips). Pure
file-layout consolidation + scaffold removal.

---

## §3.7 — MeasurementArchive facade

**Problem.** Per §0, the MeasurementArchive is "the actual
database" (cross-cycle persistence layer at
`archive/measurements/{run_id}.json`). Per §0.5, it's load-bearing.
But operationally it has **13 raw `store.archive.*` call sites** with
no gated entry:

- `infrastructure/tracing/replay.py:131,152,240,267,322`
- `presentation/api.py:876`
- `presentation/writers.py:135,136`
- `application/bootstrap/scoring_context.py:300`
- `application/scoring/search_point_scorer.py:430,488`
- `application/optimization/elevation.py:234`
- `application/intelligence/indexes/axis.py:305,317`

Multiple readers + multiple writers + no facade = the same
event-sourcing-without-an-API problem `dispatch_hub` already solved
for prompts and `CycleLedger` solved for events. The "DB core" claim
in §0 is operationally aspirational until this lands.

**Deliverable.** A facade module at
`application/scoring/archive_views.py` (name during PR — could also
be `application/scoring/archive.py`) exposing exactly:

- `measurements_for_sample(sample_id) -> list[Measurement]` (read)
- `measurements_for_config(predicate) -> list[Measurement]` (read)
- `archive_writer.record(measurement)` (sole write)

Migrate every call site above to the facade. After: any
`store.archive.*` call outside the facade is drift, **and** any
`archive = session.store.archive` (or equivalent local-alias) binding
is also drift — `application/optimization/elevation.py` uses that
alias today, so a naive `store\.archive\.` grep misses the followup
calls. The §1 grep target widens to:
`grep -rE "(store|self|cls)\.archive\.|=\s*\S+\.store\.archive\b" promptpotter/`.
Both patterns must match only the facade module.

**Lands alongside §3.6.** Same shape of work — consolidate scattered
access into one module. Both are reversible (additive — they
introduce a new entry point that callers opt into one at a time).

**Out of scope for §3.7:** changing the on-disk archive format,
content-addressing scheme, or retrieval semantics. Pure access-path
consolidation.

---

## §3.8 — Reconstructable state invariant

**Goal.** `Session` and `LoopState` are **reconstructable from the
ledger alone** — no in-memory-only state that the ledger can't
reproduce. This is the cheap half of event-sourcing's benefit
(reasoning clarity, no hidden state) without the expensive half
(replay-on-every-read, mutation-as-event-append). Pairs with §0's
single-writer invariant: §0 says nothing writes outside the ledger;
§3.8 says nothing the ledger can't reproduce lives only in memory.

**Deliverable.** A `tests/test_reconstructable_state.py` that:

1. Runs a partial cycle (≤2 rounds, no LLM calls — fixture-driven).
2. Snapshots `Session` + `LoopState` field-by-field
   (`asdict`/`model_dump`).
3. Truncates the in-memory objects to defaults (or builds a fresh
   `Session` with only the wiring + identity).
4. Replays the cycle's `events.jsonl` through the existing ledger
   subscribers + `Session` reconstruction path.
5. Asserts equivalence on every field except a documented allowlist
   (e.g., wiring fields like `backend_client` whose identity is
   reconstructed from the bootstrap config, not from ledger events).

Each field that fails the test is one of:

- (a) **A bug.** The ledger event is missing data needed to
  reconstruct the field. Fix: add the missing field to the
  appropriate event record. The single-writer invariant (§0) means
  there's exactly one place that writes the event — easy to find.
- (b) **Genuinely derivable on demand.** The field is a function of
  other ledger-reconstructable state. Fix: convert the field to a
  `@property` over ledger state, drop it from the in-memory object.
- (c) **Wiring/identity, not state.** Add to the documented
  allowlist (e.g., `backend_client`, `tenant_id`, `obs`). Each
  allowlist entry needs a one-line "why exempt" comment.

**Not in scope for §3.8:** replacing mutable fields with
projections, killing in-memory state, replay-on-every-read
performance changes. Those are M12 daemon-spec decisions (see
"Rejected approaches" + Item 2 daemon coupling). §3.8 only asserts
the invariant exists; it doesn't restructure the implementation.

**Lands alongside §3.7.** Same risk shape — additive test that
either passes today (in which case the invariant is ratified) or
surfaces hidden writers / hidden state that need fixing one at a
time. Each fix is its own small PR.

**Why this matters for M12.** If the M12 daemon needs multi-process
or restart-survival state coherence, "convert mutable Session to a
projection" becomes a much smaller step when the reconstructable
invariant already holds. If the daemon doesn't need that, the
invariant still pays for itself in reasoning clarity (no hidden
state surprises a future reader).

---

## §4 — Drop unused surface

Each item below ships with a one-line "why dropped." Don't relitigate.

- **`domain/decision_trace.py`** — narrative-only data class introduced
  for a mid-round LLM diagnostic that no longer exists. Delete the file,
  the PoBB writer hooks, and `RoundResult.decision_traces`. Note:
  separate system from the resume-checkpoint records under §4.5
  (those stay).
- **`decision_trace_summary` injection** + its renderer in
  `dispatch_hub.py` + the `{{decision_trace_summary}}` slot in the
  L1_critique template. Why: with `DecisionTrace` gone, no source data;
  L1_critique already sees per-candidate stats via `diagnostics`.
- **`infrastructure/projections/signals.py` (SignalsProjection)** + its
  binding in `build_run_observers`. Why: no consumer asked for it;
  observability layer ahead of demand.
- **`LiveDashboardProjection._absorb_rule_fired` + `recent_rules` +
  `current_signals` dashboard fields**. Why: same — nothing reads them
  outside the webapp panels we're also dropping.
- **`webapp/components/dashboard/SignalsPanel.tsx` +
  `StuckDiagnosis.tsx`** + their imports in `DashboardPane.tsx` + their
  CSS in `globals.css`. Why: no operator is using these; can be
  rebuilt against future demand (data is reproducible from the ledger
  if needed).
- **`.runtime/signals.jsonl`** path references in docs. Already gone
  with the projection — clean the references.
- **`langfuse/prompts/` rendered-prompt mirror** — the per-render
  on-disk copy of compiled optimizer prompts. Why dropped: duplicated
  by the audit projection's per-round node I/O (`l1_generate`,
  `l1_critique`, etc. payloads include the rendered prompt). One
  source per fact (per the §1 ledger-vs-projections audit). Keep
  `langfuse/events.jsonl` — that's the tracing surface (different
  bucket).
- **Zero-signal sample filter** (off-by-default round-boundary
  pruning that mutates `datasets/{name}.json::excluded`). Why
  dropped: off by default, not currently used, adds a sanctioned
  writer to the dataset config file. Reintroduce as a sub-spec if
  the need re-emerges.
- **Scoring-set evolution** (off-by-default in-memory mutator on
  `session.scoring.scoring_set`). Why dropped: same — off by
  default, not currently exercised. Reintroduce later if needed.
- **`scoring_steer.json` mid-campaign hot-swap** (`session.round_scorer`
  recompile on `campaigns/{cycle_id}/scoring_steer.json` change).
  Why dropped: specialty mechanism the loop runs without; the
  scorer can be set once at init and stay. Reintroduce later if a
  use case justifies the file-watcher complexity.

After these drops, root CLAUDE.md's "Round-boundary scoring-set
mutations — two sanctioned writers" section becomes empty and gets
removed in §1's doc trim. Same for any backbone-table row referring
to scoring-set mutation.

**Decide-during-audit drops** (items §1 surfaced; default skeptical
of keeping; commit to drop or keep when the audit closes):

- `webapp/components/dashboard/ChatPane.tsx`,
  `WorkflowCanvas.tsx` — keep only if §1 confirms an active operator
  use case. (Not in the same situation as
  `ProgressCard`/`LiveStateCard`, which were verified non-redundant
  and stay.)
- `infrastructure/tracing/mlflow_sink.py` — drop unless §0 names
  MLflow alongside Langfuse explicitly (today it doesn't).
- `l1_critique_text` rendered prompt currently only in
  `langfuse/events.jsonl` — pick a side per "everything material
  lives on disk." Either ledger it (write to a per-round audit
  artifact) or document the trace-only carve-out in §0.

Note: `/file-content` API endpoint does NOT exist (verified during
planning). `/datasets/{name}/preview` DOES exist
(`presentation/api.py:834`) and is today's hard-sample leaderboard
data path — see §0.5 conditional-load-bearing list.

Net result: one bundled escalation package, the typed `INJECTIONS`
registry + `validate_template` kept, plus the `axis_memory` injection
(cross-round axis-effectiveness digest — the one genuinely-new prompt
input from the recent arc that earns its keep).

---

## §4.5 — Rename ambiguous domain types so they self-describe

**Principle:** a name should explain itself without cross-file lookup.
"Decision" / "DecisionKind" / "DECISION_GATING" violate this — read in
isolation, they tell you nothing about what they record or why. The
mechanism is load-bearing (resume + fork-on-divergence); the names are
not.

Renames (target names indicative — pick the final ones during the PR,
optimizing for "I understand it without opening another file"):

- `Decision` → `ResumeCheckpoint` (records a per-round commitment so
  resume can replay and `--fork-on-divergence` can mint a sibling at
  the first mismatch).
- `DecisionKind` → `ResumeCheckpointKind`.
- `DECISION_GATING` → `RESUME_CHECKPOINT_GATING`.
- `DecisionRecord` (in `application/optimization/cycle.py`) →
  `ResumeCheckpointRecord`.
- `CycleLedger` → `CycleEventLog` (or `CycleJournal` — pick during
  PR). The chosen name should make "append-only event log per cycle"
  obvious from the symbol alone.
- `ProjectionBase` → `DerivedView` (or `LedgerSubscriber` — pick
  during PR). Subclasses become e.g. `LiveDashboardView`,
  `AuditTrailView`, `PoBBStreamView`. "Projection" carries no
  meaning to a reader who hasn't seen event-sourcing patterns.
- `compile_l1_surface` / `compile_l2_surface`
  (`application/optimization/pipeline.py`) →
  `compile_l1_field_catalogue` / `compile_l2_field_catalogue` —
  consistent with `l1_signal_catalogue` / `pipeline_param_catalogue`
  already used in `dispatch_hub.py`. The codebase already has the
  word "catalogue" for "menu of named things"; "surface" was a
  parallel coinage. Pick one (catalogue) and use it everywhere.

Cascade through `infrastructure/ledger.py`, all projection consumers,
`domain/run_records.py`, the resume + fork tests, root `CLAUDE.md`'s
backbone table.

Anything §1 surfaces with the same problem (a name that requires
opening the file to understand) lands here too.

**Committed renames** (verified during planning — `CycleSlice` and
`RoundDigest` already earn their names per their docstrings; only
`Bundle` is generic enough to require opening the class body):

- `Bundle` (`application/optimization/dispatch_hub.py`) →
  **`InjectionBundle`**. Chosen because it lines up with the
  `SIGNALS → INJECTIONS` rename in §2 (the bundle carries
  injection-renderer inputs); makes "bundle of state every
  prompt-fill call gets" obvious from the symbol alone. §0's
  vocabulary table (top of §0) reflects this choice.

`CycleSlice` (frozen snapshot of cycle state for renderers) and
`RoundDigest` (one round's post-scoring readouts) are self-describing
per their docstrings — leave them.

This is one codebase-wide rename pass. Naming is the cheapest part of
design; fixing it once saves every future reader (including the
operator) the cross-file lookup.

---

## §4.6 — Test suite cull

Two passes, in order.

**Pass 1: drop tests for dropped features.** Already implicit from
§4 — make explicit. Targets:

- Tests asserting `DecisionTrace` shape / behavior.
- Tests asserting the `decision_trace_summary` injection wiring.
- Tests asserting `SignalsProjection` writes / `dashboard.json`
  `recent_rules` / `current_signals` contents.
- Tests asserting webapp `SignalsPanel` / `StuckDiagnosis`
  rendering.
- Tests referencing any cadence-rule names / paths that don't
  survive §3.

**Pass 2: find tests that contradict §0.** Tests are spec-in-code;
contradicting tests are drift too. Specifically:

- Tests asserting "candidate aborts on first `validation_failure`"
  (contradicts errors-heal-tolerantly).
- Tests asserting per-`(sample, candidate)` retry behavior
  (contradicts no-retry).
- Tests asserting in-round LLM calls outside `l1_generate` /
  `l1_critique`.
- Tests that depend on `Decision` / `DecisionKind` exact names
  (rename per §4.5).
- Tests asserting prompt-fill paths outside `dispatch_hub`
  (sidecar paths — contradicts dispatch-hub canon).

**Pass 3: bucket-map every surviving test.** Per `tests/CLAUDE.md`
ceiling (≤200 tests, ≤15 files). Every kept test must point at a
named §0 invariant. Output: per-test-file table at
`docs/specs/m10-cleanup-test-map.md` mapping each test → the §0
bucket it guards. Tests that don't map to a bucket are over-coverage;
either rewrite them to guard a real invariant or drop.

---

## §4.7 — Dependency audit

Walk `pyproject.toml` `[all,dev]` extras. Each dep is complexity that
ships with every install. For each:

- Is it imported in code that survives §4?
- Is it used by tests that survive §4.6?
- Is it transitively pulled in by a kept dep?

Drop any dep consumed only by dropped features. Likely targets to
investigate (subject to grep): chains around the dropped
observability surface; AI-tooling deps that were aspirational
(jupyterlab if not actively used; MCP-related deps if dev-only and
not wired in).

Output: lean `[all,dev]` extras + a one-line `# why kept` comment
next to each remaining dep in `pyproject.toml`. Future PRs adding a
dep have to add the why-kept comment in the same diff — pre-flight
question 6 territory.

Side benefit: smaller `pip install` time, smaller Docker layer,
smaller attack surface.

---

## §5 — Codify §0 into per-file invariants

§0 in prose isn't enough — drift happens because no individual file
calls out its role. §5 ships **per-file docstrings** at the top of
each Python file participating in §0. This is **distinct from the
per-directory CLAUDE.md tier** (which lives at `application/`,
`domain/`, etc., and progressively discloses cross-file context for
that layer). Per-file docstrings are file-local invariants
("out-of-bounds: this file MUST NOT do X"); per-directory CLAUDE.md
is layer-level orientation. Both stay consistent with §0; they
serve different jobs.

**The eight §0 buckets** (counted from the bold section headers
below "Purpose"): central loop, escalation, errors heal upward,
dispatch hub, state + persistence, everything material on disk,
tracing, measurement archive. (The two architectural commitments at
the top of §0 — pipeline-agnostic, two-layer searchpoints +
self-optimization — are commitments shaping the buckets, not
buckets themselves.)

For each file participating in those eight buckets, add a top-of-file
docstring of the form:

```
This file participates in {central loop | escalation | errors-heal |
dispatch | state/persistence | on-disk | tracing | archive} as
{specific role}.

Out-of-bounds: {what this file MUST NOT do}.
```

Concrete examples:

- `application/optimization/l1.py` — central loop / orchestrator.
  Out-of-bounds: must not write campaign artifacts; must not call LLMs
  outside `l1_generate` / `l1_critique` paths.
- `application/optimization/escalation/state.py` — escalation /
  decision authority. Out-of-bounds: must not mutate `OptSearchPoint`
  fields outside the documented escalation surface.
- `application/optimization/dispatch_hub.py` — dispatch / single info
  ingress to prompts. Out-of-bounds: must not be bypassed; no prompt
  site may read state directly without going through `build_bundle`.
- `infrastructure/projections/*.py` — observability / read-only
  ledger subscribers. Out-of-bounds: must not write campaign artifacts
  beyond their declared allowlist (already enforced by
  `tests/test_invariants.py::test_no_direct_artifact_writes_outside_stores` + `test_artifact_sets_are_disjoint_and_well_formed`).
- `presentation/cli/campaign_runner.py` — entry-point / CLI shell.
  Out-of-bounds: must not implement business logic; must call into
  `application/`. The thin-shell rule from §0's entry-point scope
  has its failing-assertion site here.
- `presentation/api.py` — entry-point / read-only API.
  Out-of-bounds: must not write campaign artifacts; must not expose
  mutating endpoints beyond the explicitly-sanctioned ones (e.g.
  `/datasets/{name}/preview` is read-only by design).
- `presentation/views/*.py` and `presentation/writers.py` —
  entry-point / display formatters + markdown writers.
  Out-of-bounds: must not perform I/O outside the file-tree-readable
  surface; markdown writes go only to documented paths
  (`archive/runs.md`, `archive/individuals.md`,
  `archive/hard_samples.md` if `refresh_tenant_leaderboards()`
  survives §1 audit).

When a future PR drifts (e.g., adds a sidecar campaign writer outside
projections, or a second prompt-fill path outside `dispatch_hub`, or
adds business logic to a CLI command), the relevant file's docstring
is the failing assertion: the change either respects the docstring or
rewrites it deliberately. No more silent drift.

---

## §6 — Pre-flight gate for future feature work

Before adding any new concept (class, projection, injection, prompt,
field, dict, file), the PR description answers:

1. **Which §0 bucket does this belong to?** If "none of them," stop —
   either §0 is incomplete (update it deliberately) or this is the
   wrong PR.
2. **Does an existing channel already do this?** Default answer: yes.
   Search before adding.
3. **Is the name distinct from every existing concept in the codebase?**
   Grep first. Two `signals` was avoidable; the next collision is too.
4. **Is the name self-describing without opening another file?** Read
   the name in isolation. If it could mean three different things
   (`Decision`, `Bundle`, `Signal`), rename now — naming is cheap, the
   alternative is every future reader paying for it.
   - **Sub-rule: are you adding a new I/O kind?** §0 names three:
     Persistence (`CycleLedger.append`), Display (ledger
     subscribers), Control-local (`stop_check`). M12's orchestrator
     daemon will add a fourth (Control-remote). If your code
     introduces a NEW I/O kind beyond those, that's an
     architecture-spec change, not a feature change — **stop and
     amend §0 first**, then write the code. Catches accidental
     daemon prep before M12, accidental sidecar writers, accidental
     out-of-band control channels.
5. **Can this ride existing infrastructure (ledger, INJECTIONS,
   OptSearchPoint, dispatch hub) without adding a sidecar?** Default:
   yes.
6. **Can the AI/operator read this fact from a file without running
   the CLI?** If the new code surfaces something material only via
   stdout, only via in-memory state, or only via "ask me to re-run
   with --verbose," it violates the AI-accessibility principle.
   Material facts land on disk in human-readable form.
7. **Does §0 need updating to mention this?** If yes, that's a separate
   PR landing first. Code that requires §0 to drift cannot land before
   §0 has been updated.
8. **Does this code emit a Langfuse-shape trace event for any new
   LLM call or backend match?** If yes, wrap the call site with
   `observed_node()`. Today's `l1_critique` is unwrapped — exactly
   the drift this question prevents. New unwrapped LLM calls are an
   automatic block.

If any answer is "I don't know" or "kind of," the PR doesn't land.

---

## Rejected approaches

Considered during M10 architecture restate, not adopted. Recorded
here so a future reader (or a future PR proposal) doesn't
re-litigate without context.

- **Full event-sourcing — kill mutable `Session` / `LoopState`,
  reconstruct or project on every read.** Considered, not adopted.
  Resume already works (see `feedback_resume_fix`); reasoning
  clarity is captured by the §3.8 reconstructable-state invariant
  at a fraction of the cost; the performance + test-ergonomics cost
  of full migration is large for marginal additional gain. M12
  daemon work *may* pull a partial migration (Session-as-projection
  over the ledger) if multi-process state coherence demands it; the
  M10 single-writer (§0) + reconstructable-state (§3.8) invariants
  make that move a smaller step when it comes. Until then, mutable
  in-memory `Session` is fine.
- **Orchestrator daemon + thin clients in M10/M11.** Considered for
  early prep, not adopted. The daemon is structurally what M12
  webapp Phase 2 (control plane) IS — premature factoring in M10
  would create a fourth I/O kind without a spec change. M10 only
  adds the §6 question 4 sub-bullet that catches accidental drift
  toward this; the actual daemon spec lands as M12 work.
- **PromptPotter-as-backend connector in M10.** Considered (the
  data shape and shape parity are pinned in M10), not built in M10.
  Connector work belongs in M11 alongside ablation/connector-boundary
  work; M10 only ships the prep (§3.5 parity test, §1 fixture) so
  M11 can land the connector quickly.

---

## Execution order

**Ordering principle: reversible work first, irreversible work last.**
Renames are mechanically reversible (one more rename pass); doc
additions are pure additions; drops require git revert + verification
of nothing-broke-subtly. Land in increasing risk order so a mid-cleanup
rollback is cheap.

0. **Move §0 prose into `docs/architecture.md`** as its own
   declared, long-term file. Root `CLAUDE.md` gets a one-line
   pointer ("for the architecture reference, read
   `docs/architecture.md` first"). This separates the architecture
   reference from the transient cleanup arc — readers no longer
   filter §0 through "this is in a cleanup spec, so maybe it's
   aspirational." The per-directory CLAUDE.md tree
   (`promptpotter/CLAUDE.md`, `promptpotter/application/CLAUDE.md`,
   `promptpotter/domain/CLAUDE.md`,
   `promptpotter/infrastructure/CLAUDE.md`,
   `promptpotter/presentation/CLAUDE.md`, `tests/CLAUDE.md`) stays
   in place as the **layered-disclosure tier** — these files
   already exist (or land alongside this PR) and progressively
   disclose detail per layer. When renames in §2/§3/§4.5 land, the
   relevant per-directory CLAUDE.md updates in the same PR (e.g.
   §3 collapsing `cadence/` into `escalation/` updates
   `application/CLAUDE.md`). The vocabulary table at the top of
   §0 stays inline. Once moved, m10-cleanup.md §1–§6 references
   §0 by pointer (`docs/architecture.md`) instead of carrying it
   inline.
1. §0 (now living in `docs/architecture.md` per step 0) reviewed
   against today's code — every claim either present-tense-true
   or parked in the vocabulary table as "lands in §X."
2. §0.5 load-bearing list reviewed and approved by operator — so
   future cuts can't accidentally cut a load-bearing surface.
3. §1 reality audit — produces the trim punch-list (docs + code +
   spec docs + OSP fields + free-deliverable verification +
   ledger-vs-projections duplication map). **Free-deliverable
   verification (hard-sample leaderboard data path) lands early
   within §1**, so §0's "free deliverable" claim isn't aspirational
   by the time §6 gate references §0.
4. §3.5 pin the `pipeline.json` contract — additive doc, no code
   change. Lands first because §6 question 1 references it.
5. **§3.6 consolidate resume + fork-on-divergence into one module**
   — lands early because many later passes touch files that
   currently host scattered fork code. Bundling first means those
   later passes operate on smaller, focused files.
5b. **§3 step 0 — build `decide_escalation()`** as the actual
    single function (currently split across `cadence/evaluator.py`,
    `escalation/firing.py`, `escalation/state.py`, `transitions.py`).
    Reversible (additive entry point). Lands before the rename pass
    in §6 so the renames operate on the consolidated function rather
    than four scattered pieces.
5c. **§3.7 build the MeasurementArchive facade**
    (`application/scoring/archive_views.py` or `archive.py`).
    Migrate the 13 raw `store.archive.*` call sites to the facade.
    Reversible (additive entry point). Lands before §4.5 renames
    for the same reason as 5b.
5d. **§3.8 land the reconstructable-state invariant test**
    (`tests/test_reconstructable_state.py`). Reversible (additive
    test). Either passes today (invariant ratified) or surfaces
    hidden writers / hidden state that get fixed one at a time as
    follow-up PRs. Pairs with §0's single-writer invariant pin.
6. §4.5 ambiguous-name renames (one codebase-wide pass — reversible
   if needed; operates on the consolidated modules from §3.6 + 5b
   + 5c).
7. §2 dispatch-hub canonicalization including SIGNALS → INJECTIONS
   rename — reversible.
8. §3 escalation bundling (rename + move — reversible).
9. §4 drops (irreversible without git revert — land after renames
   so the rename PRs aren't entangled with deletions).
10. §4.6 test suite cull (paired with §4 — drop test files in the
    same PR as the feature drop).
11. §4.7 dependency audit (paired with §4 — drop deps in the same
    PR as the feature drop, where possible).
12. §5 per-file invariants once the file layout + names are final.
13. §6 pre-flight gate goes into root `CLAUDE.md` as the last act —
    it depends on §0 being authoritative (now at
    `docs/architecture.md` per step 0), which depends on everything
    above being done. Root `CLAUDE.md` ends up with: pointer to
    `docs/architecture.md`, the §6 gate, project conventions, and
    pointers into the per-directory CLAUDE.md tree.

Each step is a small, reviewable PR. No mega-merge.

## Out of scope

- M10's prompt-iteration framework deliverables (those live in
  `m10-prompt-iteration-framework.md`).
- The resume + fork-on-divergence **semantics** — what counts as
  divergence, when a fork mints, what `--from N` skips. §3.6
  consolidates the implementation; semantics stay.
- Backend (TermNorm) changes.
- M11 / M12 surface area.

## Definition of done

### Qualitative gates (binary)

- §0 prose lives in **`docs/architecture.md`** as its own declared,
  long-term file — single source of truth for the architecture.
  Root `CLAUDE.md` carries a pointer to it as the authoritative
  reference; per-directory CLAUDE.md files
  (`promptpotter/CLAUDE.md`, `promptpotter/application/CLAUDE.md`,
  `promptpotter/domain/CLAUDE.md`,
  `promptpotter/infrastructure/CLAUDE.md`,
  `promptpotter/presentation/CLAUDE.md`, `tests/CLAUDE.md`)
  progressively disclose detail per layer and stay consistent with
  §0 (when §0 changes, the relevant layer file updates in the same
  PR).
- §0.5 load-bearing list lives in **`docs/architecture.md`**
  alongside §0 (or in a sibling `docs/load-bearing.md` if the
  authors prefer separation), with a pointer from root `CLAUDE.md`.
- The word "cadence" returns zero matches in
  `grep -ri cadence promptpotter/ docs/`.
- The word "DecisionTrace" returns zero matches outside `git log`.
- `dispatch_hub.SIGNALS` is renamed to `dispatch_hub.INJECTIONS`;
  `_Signal` / `SignalKind` follow. Zero matches for the old
  `dispatch_hub.SIGNALS` symbol in code.
- `Decision` / `DecisionKind` / `DECISION_GATING` are renamed to
  self-describing names (e.g. `ResumeCheckpoint*`). Zero matches for
  the old symbols in code.
- `CycleLedger` / `ProjectionBase` are renamed
  (e.g. `CycleEventLog` / `DerivedView`). Zero matches for the old
  symbols in code.
- `compile_l1_surface` / `compile_l2_surface` are renamed to
  `compile_l1_field_catalogue` / `compile_l2_field_catalogue`. Zero
  matches for the old names in code.
- `application/optimization/resume_and_fork/` (or the chosen module
  name) exists; resume + fork code consolidated there per §3.6.
- `decide_escalation()` exists as the sole entry point for
  post-round escalation decisions per §3 step 0.
  `grep -ri "evaluate_round\|run_layer_transition" promptpotter/`
  matches only `decide_escalation()` callers (or zero matches if
  the helpers are inlined).
- `application/scoring/archive_views.py` (or chosen module name)
  exists; all 13 `store.archive.*` call sites migrated per §3.7.
  Both the direct grep
  (`grep -ri "store\.archive\." promptpotter/`) AND the alias grep
  (`grep -rE "=\s*\S+\.store\.archive\b" promptpotter/`) match only
  the facade module.
- `tests/test_reconstructable_state.py` exists per §3.8 and passes;
  any field requiring an exemption from the equivalence check has
  a one-line "why exempt" comment in the allowlist.
- `tests/test_optimizer_pipeline_parity.py` exists per §3.5 and
  asserts both `pipeline.json` and `optimizer_pipeline.json` parse
  via `parse_pipeline_response()` under `extra="forbid"`.
- `datasets/promptpotter/` exists with `dataset.md`,
  `task_description.md`, and one or two archived rounds of
  trace-replay fixture data per §1's self-optimization fixture
  deliverable.
- `tests/test_invariants.py::test_no_unexpected_runtime_layer_violations` includes an explicit grep that fails
  on any non-`TYPE_CHECKING` `domain/` import of `application/` or
  `infrastructure/`.
- Every optimizer LLM call site in
  `application/optimization/*.py` is wrapped in `observed_node()`
  (zero unwrapped sites; today `l1_critique` is unwrapped — must be
  fixed before close).
- `dispatch_hub.py` has the four-kind docstring; `dispatch-hub.md` has
  the per-prompt injection table.
- `docs/developer/pipeline-json-contract.md` exists and pins the
  contract per §3.5.
- Every file under `application/optimization/` and
  `infrastructure/projections/` has a §5-shaped docstring.
- Root `CLAUDE.md` carries the §6 pre-flight checklist.
- Docs from §1 audit are trimmed; no `aspirational` or `dead` claims
  remain in `README.md`, `CLAUDE.md`, `docs/`, or `docs/specs/`.

### Measurable targets (numbers)

Record origin before §1 lands; record final after §4.7. Both go in
a results doc (`docs/specs/m10-cleanup-results.md`) so "are we done"
has a number.

| Metric | Origin (date / value) | Target | Final |
|---|---|---|---|
| LOC under `promptpotter/` | TBD | −20% | TBD |
| Files under `promptpotter/` | TBD | −15% | TBD |
| Tests collected (`pytest --collect-only -q`) | 199 | ≤180 | TBD |
| Test files under `tests/` | 15 | ≤12 | TBD |
| Concepts in `docs/architecture.md` backbone table (was: root `CLAUDE.md`) | ~11 | ≤8 | TBD |
| Components in `webapp/components/` | TBD | −25% | TBD |
| Top-level docs (`docs/concepts/` + `docs/operations/` + `docs/developer/`) | TBD | as audit decides | TBD |
| Specs in `docs/specs/` (excl. `archive/`) | TBD | as audit decides | TBD |
| `pyproject.toml` `[all,dev]` extras count | TBD | −10% | TBD |
| `OptSearchPoint` field count | 22 (14 own + 8 inherited from `PromptTemplate`) | ≤18 own (the 8 inherited stay; cut at least 4 own fields, each survivor mapped to a §0 bucket) | TBD |

Targets are operator-set during §0 approval. The point isn't to hit
the exact percentage — it's to make "we cut bloat" measurable
instead of a feeling, so a future review can confirm the work
landed.
