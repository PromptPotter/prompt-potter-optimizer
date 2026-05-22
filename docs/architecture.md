# PromptPotter Architecture

This is the **architecture reference** — the single page (plus the
load-bearing surface list in §0.5) that every PR measures against.
Extracted from `docs/specs/archive/m10-cleanup.md` (cleanup spec
complete + archived) per its Execution order step 0.

**AI assistant readers, start here.** §0 below is the **entry
point**. Read it first to know the shape of the project. It fits on
roughly one A4 page so you can grab the whole shape quickly, then
**progressively disclose detail by following the layered CLAUDE.md
tree**:

- `CLAUDE.md` (root) — onboarding pointers, project conventions,
  the §6 pre-flight gate. Carries a pointer to this file
  (`docs/architecture.md`) as the authoritative architecture
  reference.
- `promptpotter/CLAUDE.md` — package-level orientation.
- `promptpotter/application/CLAUDE.md`,
  `promptpotter/domain/CLAUDE.md`,
  `promptpotter/infrastructure/CLAUDE.md`,
  `promptpotter/presentation/CLAUDE.md` — per-layer detail (only
  load these when you actually touch that layer).
- `tests/CLAUDE.md` — test charter.

**Don't load everything upfront — that's the design.** §0 plus the
layer-CLAUDE.md you're touching is the right context for a given
task. When working in `application/optimization/`, load §0 +
`promptpotter/application/CLAUDE.md`; when touching domain types,
§0 + `promptpotter/domain/CLAUDE.md`. Each per-directory CLAUDE.md
stays consistent with §0 — when §0 changes (rename, bucket move,
new invariant), the layer files update in the same PR (the §3
collapse of `cadence/` into `escalation/` is the canonical example:
the rename PR also touches `application/CLAUDE.md`).

If a request doesn't fit a §0 bucket, that's a flag — propose an
answer that fits, or push back on the request. Don't run a checklist
before reading §0; run it after, against §0.

---

## §0 — PromptPotter on one page

**Vocabulary cheat sheet:** [`docs/glossary.md`](glossary.md) — one
line per term with the canonical implementation file. Read it before
introducing a new domain word here.

**Vocabulary note for this page.** §0 describes the architecture
**after the §2/§3/§4.5 renames in `m10-cleanup.md` have landed**
(the names that survive long-term). Where today's code uses
different symbols, the cross-walk is:

| §0 vocabulary (target / long-term) | Today's symbol (pre-cleanup) | Lands in |
|---|---|---|
| `INJECTIONS` registry, `_Injection`, `InjectionKind` | `SIGNALS`, `_Signal`, `SignalKind` | m10-cleanup §2 |
| `decide_escalation(EscalationInputs)` | split across `decide_escalation` + `firing.py` + `transitions.py`; type is `EscalationInputs` | m10-cleanup §3 step 0 + §3 |
| `ResumeCheckpoint*` records | `Decision*` records | m10-cleanup §4.5 |
| `CycleEventLog`, `DerivedView` (subscriber base) | `CycleEventLog`, `DerivedView` | m10-cleanup §4.5 |
| `compile_l*_field_catalogue` | `compile_l*_surface` | m10-cleanup §4.5 |
| `InjectionBundle` (bundle of state every prompt-fill receives) | `Bundle` | m10-cleanup §4.5 |

This page is the **target reference**. The renames in
m10-cleanup §2/§3/§4.5 are the work that makes the page literally
true in code. When a §0 claim about a symbol that doesn't exist
yet matters for an immediate decision, jump to the row above to
find today's name.

**Purpose.** Evolve a target prompt + pipeline params toward a fitness
goal by iterating LLM-driven candidate generation against a scoring
dataset.

Two architectural commitments shape every bucket on this page:

- **Pipeline-agnostic.** Any backend that publishes a `pipeline.json`
  describing its tunable parameters is optimizable. Node names,
  parameter shapes, and prompt slots all come from the backend's
  self-description — PromptPotter has zero hardcoded knowledge of the
  target system. New backend = new `pipeline.json`, no PromptPotter
  code change. The `pipeline.json` contract is **to be pinned** in
  `docs/developer/pipeline-json-contract.md` (deliverable per
  m10-cleanup §3.5; not yet on disk).
- **Two-layer searchpoints + self-optimization.** `JobSearchPoint` is
  the frozen target spec being measured (prompt + pipeline params,
  content-hashed). `OptSearchPoint` is the optimizer's own working
  state (lineage, memory, escalation history) that projects into a
  `JobSearchPoint` for scoring. PromptPotter itself runs on an
  `optimizer_pipeline.json` — same shape as a target backend's
  `pipeline.json` — so accumulated `OptSearchPoint` data is the
  dataset for **optimizing the optimizer**.

**Central loop.** One round = generate → score → critique.

- `l1_generate` produces N candidate searchpoints from the parent.
- `l1_score` runs each candidate against the dataset via the **sole
  scoring entry point** `score_search_point()`
  (`application/scoring/search_point_scorer.py:397`). PromptPotter
  has three single-place-to-extend mechanisms — exactly one entry
  for each shape: **scoring** goes through `score_search_point()`,
  **persistence** through `CycleEventLog.append`, **prompt-fill**
  through the `INJECTIONS` registry. Two efficiency mechanisms
  operate inside `l1_score`, both first-class:
  - **Candidate budget allocation (PoBB).** A candidate keeps
    accumulating samples only while there is statistical evidence it
    could still beat the leader. Otherwise it is eliminated and we
    move to the next candidate in the round. Concentrates query
    budget on candidates that might actually win.
  - **Hard-sample ordering (Rasch sort).** Samples are scored in order
    of decreasing signal-to-noise — the most discriminating samples
    first. Separates winners from losers with the fewest queries.
    The same sort drives the operator's hard-sample leaderboard for
    free, since "most discriminating" is exactly what an operator
    wants to inspect.
- `l1_critique` reads the round's outcomes and writes a structured
  critique. The critique flows into next round's `l1_generate`.

Repeat until goal hit, `max_rounds`, or escalation chooses to stop.

**Escalation (two layers, both lazy).** L2 (`l2_context`) fires when L1
stalls (or, opt-in, on the `l2_axis_yield_drought` rule when L1
stops producing axis-novel candidates) — refines `task_context`, the
framing dict that every prompt reads. L3 (`l3_plan`) fires when L2
stalls — rewrites the strategic plan. Higher layers constrain lower
ones; they don't replace them. Both can also terminate the loop (goal
reached or infinite stall). Firing decisions live in **one**
function: `decide_escalation(EscalationInputs)`, called once per
round, returning the next action via priority-sorted first-match-wins
escalation rules.

**Errors heal upward, tolerantly.** Default assumption: any single
failed measurement (validation failure on L1 output, runtime failure
mid-eval, deprecated cache entry from a transient backend hiccup) is
**innocent** — a technical issue, not the candidate's fault. We log
it, ignore it, and keep accumulating evidence on the same candidate.
A candidate is aborted only when its **`DegradationCheck`**
(`application/optimization/pobb/elimination.py:222`) fires — i.e. when its
fraction of failed measurements crosses the per-campaign
`degradation_threshold` (`campaign.json::degradation_threshold`,
e.g. `0.4` on gsm8k). Aggregated failures surface at round end and
flow upward: cadence/escalation rules route them (L1 validation
failures → L2 next round; L2 output-validator failures → L3); the
dispatch hub is the prompt-fill path each healing call goes through.

**No retry of the same (sample, candidate) pair after a technical
error** — same inputs, same error, wasted budget. The pair is dead;
move on. **No mid-round LLM diagnostic. No complex per-error
branching.** A discarded candidate is cheap: next round's
`l1_generate` produces siblings on the same axis, and any genuinely
useful direction returns naturally. Trust the loop's self-healing
(validation → L2 next round, runtime → DegradationCheck escalation)
plus passage of time over hand-coded recovery logic. The default
posture is "ignore and continue"; aborting requires evidence.

**Dispatch hub.** Every optimizer LLM call composes its prompt by the
same path: `build_bundle(cycle) → DispatchHub.fill_*(template, bundle) →
compile_prompt`. **Injections** are the named placeholder renderers
(`{{slot}} → renderer(bundle) → str`) — they inject deterministic
state into a prompt's body. One registry (`dispatch_hub.INJECTIONS`).
One `validate_template()` at module load that catches typos.
**Adding a new piece of info to a prompt is one new injection
renderer, period.** No sidecar paths, no out-of-band state mounting.

**Four entities (outermost → innermost).** PromptPotter's persisted
world is a strict containment hierarchy:

- **Workspace** — the tenant-level container and **queryable
  datastore**: every dataset, every campaign, and the shared
  `archive/` measurement store. On disk it is `projects/{tenant}/`.
- **Dataset** — the optimization target plus its config
  (`datasets/{name}/`).
- **Campaign** — one declared optimization effort: a dataset, a
  pipeline origin, context text, **and the optimizer meta-prompts it
  runs under**. A **first-class entity** and a **forest** — it holds N
  **sessions**. `campaign_id = {dataset}__{declaration_hash}`, where
  `declaration_hash` is the 12-hex hash of the *complete* declaration:
  it folds the **target** content hash (`root_content_hash` — the same
  hash that is the root cycle id) with the **optimizer** meta-prompt
  hash (`optimizer_prompt_hash` — `datasets/_optimizer/`). The id is
  **stable**: re-running `new <dataset>` on an unchanged declaration
  resolves to the **same** campaign (find-or-create), not a fresh one;
  editing a target field OR an optimizer meta-prompt mints a distinct
  campaign. The dataset is embedded so "campaigns for dataset X" is a
  prefix scan.
- **Session** — one run of `new` on a campaign's declaration. A campaign
  holds N sessions; re-running `new` on the same declaration **adds** a
  session to the existing campaign. `resume` extends the *active*
  session — it does not add one. A session's identity is its
  `session_id` (`s_xxxx`). Each session is a tree: a root cycle plus its
  fork descendants. The session root cycle id is `cycle_{hash}` for
  session 1 and `cycle_{hash}_s{N}` for session N — the `_s{N}` suffix
  only disambiguates the directory, it is **not** a sibling separator
  (`root_cycle_id` / `sibling_kind` treat `cycle_X_s2` as its own family
  root, `cycle_X_s2_fork_abc` as a fork rooted at it).
- **Cycle** — one node in a session's lineage tree: root | fork | diag
  | sweep. The operator-facing name is **Unit** — one continuous-parameter
  run; `resume` extends the current unit, each fork branches a new one
  (the webapp + docs say "unit", the on-disk / API id stays `cycle_id`).
  Identity stays `cycle_{target_hash[:12]}` (+ the `_s{N}` session-root
  suffix, `_fork_`/`_diag_`/`_sweep_` for branches) — the *target*
  content hash, distinct from the campaign's declaration hash. It keeps
  two jobs: archive cache-reuse keying and target-drift detection.
  `cycle_id` is campaign-scoped — all path resolution is
  `(campaign_id, cycle_id)`.

The **Session** is a unit of a campaign (its identity is the
`session_id`). `active_session.json` is the operator's *pointer/lens*
into the Workspace — which tenant, session, campaign, and cycle are
live. `new` resolves (find-or-create) the Campaign for the declaration
and mints a fresh Session + its root cycle inside it. `resume` follows
the pointer into the active session. `fork` mints a new cycle **inside
the same session**.

**`unit_kind` taxonomy.** An operator-facing label, computed
server-side from `(sibling_kind, fork_trigger)`, used by the webapp
sidebar: `session` (a session root run — `resume` extends it),
`divergent_resume` (a `resume --fork-on-divergence` branch),
`user_fork` (any operator-initiated branch — HITL fork, diagnostic,
sweep — these three fold into one kind), `l3_fork` (reserved for L3
auto-forking; not emitted yet).

**Three data scopes — campaign / dataset / workspace.** The
Workspace datastore is queryable at three named, consistently-used
scopes: **campaign** (one campaign's own cycles — the campaign dir),
**dataset** (every campaign for one dataset — `archive/` filtered by
`dataset_name`), **workspace** (everything, all datasets — the whole
`archive/`). The same three names are used by the archive query API,
the heatmap artifacts, the `scope` API param, and the webapp toggle,
so the operator always distinguishes "this campaign" vs "this
dataset" vs "everything" identically.

**State + persistence.** Three entry points (CLI, notebook, webapp)
share **one** orchestration layer and **one** set of data types — no
per-entry-point copies. **Three I/O kinds** the orchestrator reads or
writes through, each with its own ingress: (1) **Persistence** — the
sole writer is per-cycle `CycleEventLog.append`. Operator-initiated
HITL collapses into this ingress: `inherit_from(parent, offset)` mints
a fork at any chosen ledger offset (the operator picks the offset
through the webapp's lineage inspector; pre-M12 forks endorse the
parent's typed state unchanged, the substitute-typed-edit path is M12
work). (2) **Display** — ledger subscribers (`LiveDisplay`,
`LiveDashboardView`, `AuditTrailView`); read-only, never write
campaign artifacts. (3) **Control-local** — `stop_check` on
`Session`; signals the loop to exit, writes nothing. The webapp's
"Stop run" button rides this kind by writing a `.runtime/stop.flag`
file the running loop polls via `stop_check`; the API route writing
the flag is an explicitly-sanctioned mutation listed in
`promptpotter/presentation/CLAUDE.md`. M12's orchestrator daemon will
add a fourth (Control-remote) on the same persistence ingress. Adding a new I/O kind requires
amending §0 first; the pre-flight gate (CLAUDE.md Q4 sub-rule)
blocks code that introduces one without §0 backing. Hexagonal layer
separation is enforced by
`tests/test_invariants.py::test_no_unexpected_runtime_layer_violations`
(plus `test_cycle_does_not_import_prompt_surface`) so data types
stay free of I/O and the orchestrator can be reused without dragging
a backend client along. SearchPoint types are
**immutable**: once created, their fields can't change. That makes
their content hash a trustworthy identity, which is what lets
`--from N` resume a campaign with different hyperparameters and
`--fork-on-divergence` cleanly mint a sibling at the first hash
mismatch. One per-cycle `CycleEventLog.append` is the sole persistence
ingress; resume + fork ride dedicated checkpoint records on the
ledger. Display and observability subscribe to the ledger as
read-only views — never write campaign artifacts of their own.
**Single-writer invariant on the ledger** (pinned by
`tests/test_invariants.py::test_no_direct_artifact_writes_outside_stores`
+ `test_artifact_sets_are_disjoint_and_well_formed`): any module
besides the ledger writing to `events.jsonl`, or any projection
writing outside its declared allowlist, fails the tests. **The MeasurementArchive (the
other persistence layer, see "Measurement archive" below) does not
have this invariant today** — there are 13 raw call sites;
m10-cleanup §3.7 adds the facade that brings the archive under the
same single-writer discipline. Together, the §0 single-writer pin
(here) + §3.7 facade + §3.8 reconstructable-state invariant capture
event-sourcing's reasoning-clarity gain without the cost of
replay-on-every-read; see m10-cleanup "Rejected approaches" for why
we don't go further.

**Everything material lives on disk, in human-readable form.** The
project file tree IS the operator's primary interface — `campaign.json`,
`dashboard.json`, `index.json`, `log.md`, per-round caches, the ledger
itself. The webapp polls the same files; the CLI emits transient logs
but every fact also lands on disk. This is **so an AI assistant working
alongside the operator can read project state directly from the file
tree** — no copy-paste from CLI output, no asking the operator to
re-run with different verbosity. If a fact matters, it's a file
someone (or something) can open. Constraint, not feature: forbids the
lazy alternative (stdout-only logging, in-memory-only cross-round
state) without adding complexity.

The on-disk layout makes the four-entity model literal. Under each
tenant, `campaigns/{campaign_id}/` is the Campaign directory:
`campaign.json` (manifest — `dataset_name, label, created_at, status,
root_cycle_id, root_content_hash, backend_id, config`), `log.md`
(campaign digest — covers every session, its forks, and its rounds),
`hard_samples.json` (campaign-scope heatmap), and `cycles/{cycle_id}/`
holding **every** cycle — all N session roots and every fork, diag, and
sweep — **all flat** — sibling kind and sweep batch id are `index.json`
metadata, not directory nesting. A flat `cycles/` store keyed by
`parent_cycle_id` scales as the fork tree grows; nested fork-of-fork
directories do not. `dashboard.json` is **per-session**: it lives in the
session's root cycle dir (`cycles/{session_root}/dashboard.json`) and is
shared by that session's forks (a fork's family root is its session
root). A campaign therefore carries N independent live `dashboard.json`
streams — one per session — never one shared stream. Each `dashboard.json`
self-stamps its own `(campaign_id, cycle_id, session_id)`; the webapp drops
a polled payload whose stamp doesn't match the unit it asked for, so a
freshly minted session never renders the prior one's data. Each campaign is a
standalone dashboard: the operator understands a campaign from
`campaign.json` + `log.md` plus the per-session `dashboard.json`
streams, without descending into per-cycle round detail.
`archive/` stays a peer of `campaigns/` — dataset-scoped,
cross-campaign by design (see "Measurement archive" below).

**Entry-point scope rules.** The primary notebook
(`notebooks/optimization_campaign.ipynb`) is a thin UI shell — every
non-display code cell calls into `application/` (no orchestration
logic, no scoring, no LLM calls authored in the notebook).
Verifiable via `tests/test_invariants.py` extension: notebook
cells whitelist-imports from `application/` + `presentation/views/`
only. Additional notebooks
(currently `notebooks/bbeh_potter.ipynb`) are **work-in-progress** —
kept but not part of the documented entry-point surface. Mark them
WIP in cell-1 markdown so a reader knows status at a glance. The
webapp (`webapp/`) renders read-only views over `dashboard.json`
plus a file-tree view; any panel reading from a disk file we don't
already commit to writing is aspirational and out of M10. The `init`
command + `/potter-run` slash command sit in `presentation/` and
orchestrate one-time onboarding (TermNorm download, dataset
conversion, API key prompts) — load-bearing for the operator's first
run; audit for accumulated cruft but don't delete the underlying
mechanism. Future webapp panels arrive as M11/M12 sub-specs, not
silent additions.

**Tracing, Langfuse-shaped, lightweight by default.** Optimizer LLM
calls and backend matches emit structured events in
**Langfuse-compatible shape** (spans / traces / metadata) — wrapped
via the `observed_node()` context manager. Every optimizer LLM call
site (`l1_generate`, `l1_critique`, `l2_context`, `l3_plan`) is
wrapped. Events serialize to local JSONL under
`langfuse/events.jsonl` (verified path:
`infrastructure/tracing/file_sink.py:67`) — no Langfuse instance, no
MLflow server, no external dependency required. When Langfuse
credentials are present in `.env`, the same events also stream to
Langfuse cloud. **MLflow** is a peer optional sink, **off by
default** — wired via `infrastructure/tracing/mlflow_sink.py`,
gated on `settings.MLFLOW_ENABLED`. The integration is dormant
unless an operator flips the flag; the import path stays alive so
enabling it requires no code change. The Langfuse schema is the
**orientation point**: even with no external sink wired, events
conform to it, so importing later (or swapping in a different
backend) is configuration, not refactoring. Tracing is fan-out
only — the optimizer never reads it, so it can never become
load-bearing for the loop.

**Measurement archive (the actual database).** Beyond the per-cycle
ledger, a cross-cycle persistence layer lives at
`archive/measurements/{run_id}.json` — content-addressed by
`JobSearchPoint.content_hash`, indexed by `archive/measurements.json`.
Each row is `(sample × config → outcome)`, stamped with
`dataset_name` and `campaign_id` so the store answers all three data
scopes from one query path: **campaign** (`campaign_id=…`),
**dataset** (`dataset_name=…`), **workspace** (no filter). The
archive is the Workspace datastore — a peer of `campaigns/`, never
siloed into a campaign dir. **Cross-cycle, cross-session,
cross-tenant.** The on-disk format is human-readable
(operator can `cat` a row); programmatic reads go through two
retrieval views (`measurements_for_sample()`,
`measurements_for_config(predicate)`) — both behind the m10-cleanup
§3.7 facade once it lands. Cache reuse (skip backend calls when a
matching content_hash already has measurements) and cross-run LLM
digests are **derived views over this archive** — same
single-source-of-truth pattern as ledger → derived views, but at
cross-cycle scope. **The archive is the project's actual database**;
the per-cycle ledger is the event log layered on top of it. A
cleanup PR that simplifies persistence must respect both: ledger ≠
archive, neither replaces the other.

That's it. **Eight buckets** (central loop / escalation / errors-heal
/ dispatch hub / state + persistence / on-disk / tracing / archive)
plus two architectural commitments shaping them
(pipeline-agnostic / two-layer searchpoints + self-optimization).
Anything in the codebase that doesn't fit a bucket is either drift
(delete) or a missing bucket on this page (update §0 deliberately,
then add the code).

---

## §0.5 — Load-bearing surface (do not cut)

The cleanup arc has the right energy but the wrong default. Cleanup
PRs default to "delete," but some surface is doing real work and
must not be cut by accident. Read this list **before** any cleanup
PR.

A cleanup PR that touches anything below needs an explicit case in
the PR description.

- **PoBB elimination** (`application/optimization/pobb/elimination.py`) —
  the actual abort-and-continue mechanism. §0 errors-heal-tolerantly
  depends on this.
- **DegradationCheck** mid-eval halt — the per-candidate
  technical-failure threshold. Tunable values yes; mechanism no.
- **Connector pattern** (`promptpotter/connectors/`) — the only
  sanctioned place backend identity is named. Pipeline-agnosticity
  depends on it.
- **Langfuse JSONL events + Langfuse-shape compatibility** — the
  Tracing bucket's foundation. Don't simplify the schema "because
  we don't use Langfuse cloud yet."
- **`axis_memory` injection** — the one new injection from the
  recent arc that earned its keep. Cross-round AxisIndex digest.
- **`pipeline.json` contract** for connector self-description — the
  backend's API surface to PromptPotter. Don't simplify "because
  TermNorm is the only consumer today."
- **Hexagonal layer separation test**
  (`tests/test_invariants.py::test_no_unexpected_runtime_layer_violations` + `test_cycle_does_not_import_prompt_surface`) — without it, the three entry points drift.
- **Resume + fork-on-divergence mechanism** — load-bearing for
  `--from N` and `--fork-on-divergence`. Today's symbols
  (`Decision`/`ResumeCheckpointKind`) and the post-cleanup symbols
  (`ResumeCheckpoint*`) appear in §0's vocabulary table; mechanism
  stays through the rename.
- **Campaign as a first-class entity, holding N sessions** —
  `campaign.json` manifest, the `campaigns/{campaign_id}/` directory
  with `log.md` + `hard_samples.json` at its root and
  `cycles/{cycle_id}/` flat below (every session root plus every
  fork). `campaign_id = {dataset}__{declaration_hash}` (the declaration
  hash folds the target hash + the optimizer meta-prompt hash) is
  stable — re-running `new` on an unchanged declaration find-or-creates
  the same campaign and adds a session; editing an optimizer meta-prompt
  mints a distinct campaign. `dashboard.json` is per-session, at
  `cycles/{session_root}/dashboard.json` — one live stream per session,
  never one per campaign. The four-entity hierarchy (Workspace /
  Dataset / Campaign / Cycle, with Session a unit of a Campaign) and
  the three data scopes (campaign / dataset / workspace) are §0
  invariants — a cleanup PR cannot collapse Campaign back into a single
  session-family or back into the root cycle.
- **Per-cycle `CycleEventLog` + `DerivedView` dispatch** — the
  persistence backbone. No second ingress, ever.
- **Hard-sample sorter (Rasch)**
  (`application/intelligence/hard_sample_sorter.py`) + the leaderboard
  it powers — first-class per §0.
- **`compile_l1_field_catalogue` / `compile_l2_field_catalogue` field catalogues**
  (`application/optimization/pipeline.py`) — the discoverability
  scaffolding for m10-cleanup §6 pre-flight question 1. Don't drop
  "because nobody calls it from production code today."
- **The `new` verb + `/potter-run` onboarding flow** — operator's
  first-run path; cruft-audit yes, mechanism delete no.
- **`new`-verb decomposition into `task_context`** — the one-time
  `checkin` LLM call that seeds the campaign when `new <name>`
  first sees a dataset. Don't fold into `l1_generate`.
- **`MeasurementArchive` (`archive/measurements/{run_id}.json` +
  `archive/measurements.json` index + retrieval views
  `measurements_for_sample()` / `measurements_for_config()`)** — the
  actual cross-cycle database. Per §0 it's a separate persistence
  layer from the ledger; never collapse the two.
- **Per-dataset configs in `datasets/{name}/`** (`pipeline.json`,
  `campaign.json`, `prompts/{node}.json`, `scan_variants.json`,
  `dataset.md`, `task_description.md`) — the operator's primary
  interface for adding a new dataset. Per root CLAUDE.md "configs
  are the source of truth — no parallel default ladders elsewhere."
  A cleanup PR cannot move a default into PromptPotter code; if a
  setting needs a default, it goes in the dataset's config file.
- **`Evaluator` class + `evaluators` field + `all_evaluators()`
  registry + `materialize_*_values`** — the **only** sanctioned use
  of "eval" vocabulary in the codebase. A future "rename eval to
  score" cleanup PR must not touch these — they're domain language,
  not a coincidence.
- **`scripts/ppot_review.py` + `scripts/smoke_campaign.py`** —
  operator-facing CLI helpers (cross-cycle leaderboard reader; smoke
  test harness). Audit during cleanup §1 for accumulated cruft, but
  don't delete the underlying scripts without operator confirmation.
- **`score_search_point()` gateway**
  (`application/scoring/search_point_scorer.py:397`) — sole scoring
  ingress. Sibling to `CycleEventLog.append` and `INJECTIONS`. Don't
  add a second scoring entry path "for convenience."
- **`observed_node()` context manager** — the trace-emission seam
  every optimizer LLM call wraps. Cutting it removes Langfuse-shape
  compatibility (the Tracing bucket's foundation collapses).
- **`optimizer_pipeline.json`** — the self-optimization claim in §0
  depends on this file having the same shape as a backend
  `pipeline.json`. Drift (special-case fields, parallel registries)
  invalidates the claim.

§0.5 is binary: surface is either load-bearing (named above, can't
be cut) or it isn't. **Items needing a load-bearing-or-drop
decision** (`refresh_tenant_leaderboards()`, `/datasets/{name}/preview`,
MLflow sink) live in m10-cleanup §1's audit deliverables, not in
this list. §1 either promotes them into the load-bearing list above
(in a follow-up PR) or §4 drops them.

When in doubt about an item already in the list above: file a
one-line "kept because" note in the PR rather than cutting silently.
