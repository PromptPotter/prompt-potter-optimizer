# PromptPotter Architecture

This is the **architecture reference** — the single page (plus the load-bearing surface list in §0.5) that every PR measures against.

**AI assistant readers, start here.** §0 below is the entry point: read it first to know the shape of the project, then disclose detail by following the per-layer `CLAUDE.md` tree, whose index is [`promptpotter/CLAUDE.md`](../promptpotter/CLAUDE.md). **Don't load everything upfront — that's the design.** §0 plus the one layer you are touching is the right context for a task.

**Each per-directory `CLAUDE.md` stays consistent with §0** — when §0 changes (a rename, a bucket move, a new invariant), the layer files update in the same PR.

If a request doesn't fit a §0 bucket, that's a flag — propose an answer that fits, or push back on the request. Don't run a checklist before reading §0; run it after, against §0.

---

## §0 — PromptPotter on one page

**Vocabulary cheat sheet:** [`docs/glossary.md`](glossary.md) — one
line per term with the canonical implementation file. Read it before
introducing a new domain word here.

**Purpose.** Evolve a target prompt + pipeline params toward a fitness
goal by iterating LLM-driven candidate generation against a scoring
dataset.

Two architectural commitments shape every bucket on this page:

- **Pipeline-agnostic.** Any backend that publishes a `pipeline.yaml`
  describing its tunable parameters is optimizable. Node names,
  parameter shapes, and prompt slots all come from the backend's
  self-description — PromptPotter has zero hardcoded knowledge of the
  target system. New backend = new `pipeline.yaml`, no PromptPotter
  code change. The `pipeline.yaml` contract is pinned in
  [`docs/developer/pipeline-contract.md`](developer/pipeline-contract.md).
- **Two-layer searchpoints + self-optimization.** `JobSearchPoint` is
  the frozen target spec being measured (prompt + pipeline params,
  content-hashed). `OptSearchPoint` is the optimizer's own working
  state (lineage, memory, escalation history) that projects into a
  `JobSearchPoint` for scoring. PromptPotter itself runs on a
  `promptpotter/assets/optimizer/pipeline.yaml` — same shape as a target backend's
  `pipeline.yaml` — so accumulated `OptSearchPoint` data is the
  dataset for **optimizing the optimizer**.

**Central loop.** One round = generate → score → critique.

- `l1_generate` produces N candidate searchpoints from the parent.
- `l1_score` runs each candidate against the dataset via the **sole
  scoring entry point** `score_search_point()`
  (`application/scoring/search_point_scorer.py::score_search_point`). PromptPotter
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

**Escalation (two layers, both lazy) — self-healing with a HITL escape
hatch.** L2 (`l2_context`) fires when L1 stalls — or preemptively on
`l2_axis_yield_drought` (no axis-novel candidates) or
`l1_evidence_starved` (a node failed across ~all samples) — and steers
what L1 *looks at* (`l1_layout`) and how hard it explores
(`l1_overrides`). It does **not** rewrite `task_context`: the framing is
operator-authored and frozen for the run, so no layer edits it. L3
(`l3_plan`) fires when L2 stalls — rewrites the strategic plan. Higher layers
constrain lower ones, never replace them. The split is deliberate: a
*healthy* round is L1-critique's job (analyse, mutate); a *systemic
fault* (evidence-starvation) routes to L2, which either self-heals or —
on a fault no prompt move can fix (a rate-limited enricher) — emits
`terminate_proposal`, the LLM-emitted HITL stop that halts with a
human-action request (the operator banner carries the verbatim backend
reason; the operator fixes it and `resume`s). Deterministic rules stay
*weak*: they route, never diagnose or stop. Firing lives in **one**
function: `decide_escalation(EscalationInputs)` — priority-sorted
first-match-wins, once per round.

**Errors heal upward, tolerantly.** Default assumption: any single
failed measurement (validation failure on L1 output, runtime failure
mid-eval, deprecated cache entry from a transient backend hiccup) is
**innocent** — a technical issue, not the candidate's fault. We log
it, ignore it, and keep accumulating evidence on the same candidate.
A candidate is aborted only when its **`DegradationCheck`**
(`application/optimization/pobb/checks.py::DegradationCheck`) fires — i.e. when its
fraction of failed measurements crosses the per-campaign
`degradation_threshold` (`campaign.yaml::degradation_threshold`,
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
same path: `build_bundle(cycle) → DispatchHub.fill(template, layout, bundle)
→ compile_prompt` — one fill path for every optimizer node. **Injections** are the named placeholder renderers
(`{{slot}} → renderer(bundle) → str`) — they inject deterministic
state into a prompt's body. One registry (`dispatch_hub.INJECTIONS`).
One `validate_template()` at module load that catches typos.
**Adding a new piece of info to a prompt is one new injection
renderer, period.** No sidecar paths, no out-of-band state mounting.

**Everything that reaches a model is bounded where it is PRODUCED** —
an LLM-written field at its parse boundary (`dispatch/schemas.py`:
`max_length` plus a truncating validator), operator-authored framing
at its mint-time `check_budget`, a derived view at its render cap. A
bound at the *composition* site could only choose which half the model
sees, so a composed prompt over its node's ceiling
(`OPTIMIZER_PROMPT_BUDGET_CHARS`; `prompt_chars` on the ledger is the
measurement) is a **report that a producer bound failed**, never the
place to fix it. Two corollaries are easy to miss: input length is a
quality tax and not only a bill — every model degrades as its input
grows — and **the response JSON Schema is prompt text**, riding
`response_format` on every call, so its field names and `description`
prose are bounded on the same rule.

**Four entities (outermost → innermost).** PromptPotter's persisted
world is a strict containment hierarchy:

- **Workspace** — the tenant-level container and **queryable
  datastore**: every user-uploaded dataset, every campaign, and the
  shared `archive/` measurement store. On disk it is
  `projects/{tenant}/`.
- **Dataset** — the optimization target plus its config. Two
  first-class tiers, served by one read path: (a) **user-uploaded**
  datasets at `projects/{tenant}/datasets/{slug}/` — the
  Workspace ⊃ Dataset containment, identity-scoped, tenant-private;
  (b) **install-global benchmarks** at repo-root `datasets/{name}/` — install-scoped, read-only, admin-visible only via the `GET /datasets` list endpoint identity filter. Which names exist is `datasets/` itself; never enumerate them here.
  The two tiers serve different purposes (operator
  benchmarking vs tenant work); both share the `pipeline.yaml` /
  `campaign.yaml` / `task_description.md` shape.
  This tier holds **datasets only** — the optimizer's own pipeline is install content under the package (`config/paths.py::optimizer_assets_root`), never a target in this tier. A checkout resolves benchmark definitions from `datasets/`; a wheel ships the same definitions as install content and resolves them there. Either way the tier is **read-only**, so a benchmark's materialized rows are not kept in it: they are the operator's, and `readable_dataset_rows` resolves them from the tenant tree (`store/dataset_access.py`).
  **One resolution
  seam:** `readable_dataset_dir` picks the dir (tenant slug first,
  repo benchmark second) once at init and stamps it on
  `Session.dataset_config_dir`; every downstream dataset-file loader
  (node overlay, starting prompts, origin prompt, sweep dir) reads that
  resolved dir — none recompute a repo-relative `datasets/{name}/` path.
  So an ingested tenant dataset is first-class to the whole loop, not
  just to the mint that created it.
- **Campaign** — one declared optimization effort: a dataset, a
  pipeline origin, context text, **and the optimizer prompts it
  runs under**. A **first-class entity** and a **cycle tree** — root
  + its fork/diag/sweep descendants. `campaign_id = {dataset}__{rand6_hex}`,
  minted fresh per `new` invocation by `mint_campaign_id` — each `new`
  produces a distinct campaign regardless of declaration. The
  declaration is recorded as *properties* on `campaign.json`, never as
  the id: `root_content_hash` (resume's config-drift check) and
  `optimizer_prompt_hash` (an audit join key — optimizer drift is asked
  per ROUND, where it can name one and fork at it).
  The dataset is embedded so "campaigns for dataset X" is a prefix scan.
- **Cycle** — one node in a campaign's lineage tree: root | fork | diag
  | sweep. The operator-facing name is **Unit** — one continuous-parameter
  run; `resume` extends the current unit, each fork branches a new one
  (the webapp + docs say "unit", the on-disk / API id stays `cycle_id`).
  Identity stays `cycle_{target_hash[:12]}` (+ `_fork_`/`_diag_`/`_sweep_`
  for branches) — the *target* content hash, content-addressed. It keeps
  two jobs: archive cache-reuse keying and target-drift detection.
  `cycle_id` is campaign-scoped — all path resolution is
  `(campaign_id, cycle_id)`. Path helpers:
  `promptpotter/infrastructure/store/layout.py`.

**A campaign has one root cycle — there is no Session tier.** A campaign
owns a root cycle plus its fork/diag/sweep descendants, and that is the
whole containment story — `campaign → cycle → fork` would be two tiers
wearing three names. What survives is *not* an entity:

- **`Session`** (`application/initialization/session.py`) — the in-process
  **wiring object**: stores, LLM clients, connectors, the resolved
  `dataset_config_dir`. It is services + identity, not a persisted tier.
- **`active_session.json`** — the operator's *pointer/lens* into the
  Workspace: which tenant, campaign, and cycle are live.
- **`unit_kind: "session"`** — the sidebar's label for a *root cycle*
  (`campaign_store/store.py::_unit_kind`). A label, not a container.

`new` mints a fresh Campaign + root cycle; `resume` follows the pointer;
`fork` mints a sibling cycle inside the same campaign. Two `new` calls on
an unchanged declaration get distinct `campaign_id`s but share their root
cycle id (content-addressed) and origin score (the dataset-scoped archive
cache-hits every sample) — cross-campaign evidence pooling on a declaration
rides the `measurements/` layer, not campaign identity.

**`unit_kind` taxonomy.** An operator-facing label, computed
server-side from `(sibling_kind, fork_trigger)`, used by the webapp
sidebar: `session` (a session root run — `resume` extends it),
`divergent_resume` (a `resume --fork-on-divergence` branch),
`user_fork` (any operator-initiated branch — HITL fork, diagnostic,
sweep — these three fold into one kind), `auto_rebase` (an automatic
L2/L3-rebase branch; fork trigger `l2_rebase` / `l3_rebase`).

**Three data scopes — campaign / dataset / workspace.** The
Workspace datastore is queryable at three named, consistently-used
scopes: **campaign** (one campaign's own cycles — the campaign dir),
**dataset** (every campaign for one dataset — `archive/` filtered by
`dataset_name`), **workspace** (everything, all datasets — the whole
`archive/`). The same three names are used by the archive query API,
the heatmap artifacts, the `scope` API param, and the webapp toggle,
so the operator always distinguishes "this campaign" vs "this
dataset" vs "everything" identically.

**State + persistence.** The entry points (CLI, webapp; a WIP notebook)
share **one** orchestration layer and **one** set of data types — no
per-entry-point copies. **Five I/O kinds** the orchestrator reads or
writes through, each with its own ingress: (1) **Persistence** — the
sole writer is per-cycle `CycleEventLog.append`. Operator-initiated
HITL collapses into this ingress: `inherit_from(parent, offset)` mints
a fork at any chosen ledger offset (the operator picks the offset
through the webapp's lineage inspector and may edit the forked
searchpoint's prompt + node config + limits — the operator-steered
fork). Workspace-scoped commands without any cycle target
(`register-backend`, `sync-backend-experiments`) write to a sibling
**workspace `CycleEventLog`** at `projects/{tenant}/.workspace/events.jsonl`
— same shape, same single-writer discipline, identity-bound by the
tenant prefix. Per-cycle ledgers stay canonical for any command that
targets a campaign or cycle; campaign lifecycle commands ride the
campaign's root-cycle ledger. (2) **Display** — ledger subscribers (`LiveDisplay`,
`LiveDashboardView`, `AuditTrailView`); read-only, never write
campaign artifacts. **Run-state is owned state, not a freshness
guess.** The runner *declares* its control phase — `running` /
`paused`, plus `terminal` at finalize — onto the ledger
as a `control` `PhaseRecord`, and `LiveDashboardView` projects it to
`dashboard.json::run_phase` (the `RunPhase` vocabulary,
`domain/phases.py`). Every surface reads that one value; the only
reader-side computation is `derive_run_phase`
(`infrastructure/runtime_flags.py`), used by both the cycle list and
the reaper's staleness check (`_is_dead` — no second "is it running?"
derivation), which composes lifecycle (terminal, from
`index.json::finished_at`) with the control flags, the runner's own
declaration (`paused` / `gate` — a Ctrl+C declares `paused` without ever
writing a flag), and `dashboard.json` freshness (falling back to
`index.json`'s mtime only while no dashboard has been written yet, e.g. a
just-minted cycle)
*only* to split `running` from `detached`. Two liveness invariants back that
split: **every producer await that can exceed `RUN_FRESH_S` rides
the in-flight heartbeat** (`dispatch/llm_call/heartbeat.py` —
optimizer LLM calls, L4 inner-cycle awaits, and the backend scoring
query), so a stale dashboard means a *dead* producer, never a quiet
one — and a cycle that stops ON PURPOSE says so at the moment it does
(a `supersede` cut retires its parent `REBASED` right at the cut,
`CampaignStore.mark_superseded`), so the reaper never has to interpret
a deliberate silence; and the **liveness reaper** (`application/jobs/reaper.py`) is
the single write-side reconciler stamping proven-dead cycles
`TERMINAL` (`producer_vanished`) — the registry `on_reap` for API
jobs plus a periodic sweep (roots include the flat `.inner/`
sandboxes; never boot-one-shot, and it skips a tick after a
detected machine-sleep so a woken producer's first heartbeat always
lands before judgment). Sleep is detected in ONE place
(`shared/clock.py::sleep_measuring_suspend` — wall overshoot, never
the monotonic clock, whose behaviour across a suspend is
platform-dependent); the sweep and the L4 inner-sample wall-clock
deadline are its two consumers, so a slept machine can neither reap a
live producer nor fabricate a deadline blowout. Every reap path funnels through the one
guard seam `CampaignStore.mark_producer_vanished`, which never
stamps a paused, check-in, or already-terminal cycle and delegates
the write to `mark_finished`. `detached` is therefore always a dead
producer: webapp in-flight membership is exactly {`running`,
`gate`, `paused`}, and client-side connection loss is a
presentation state, never a run phase. The terminal reason maps onto its
display label + outcome class exactly once, through the single
`STOP_REASON_INFO` table (which in turn drives `index.json::status`,
`JobStatus`, and the webapp label). **Pause is the single
operator-interrupt — there is no separate "stop".** A pause exits the
worker cleanly at the next checkpoint but leaves the cycle
**non-terminal and resumable** (no `finished_at`); "resume" is the
`start-run`/`resume` launcher relaunching from the last completed round,
not an in-place unpause. So "the loop stopped" never means "the work is
done": only a user-specified target threshold (e.g. 90%) is an autonomous
*completion*; `max_rounds` / budget caps are configured-limit halts the
operator reviews and may bump+resume. A truly authoritative "done" is a
human mark — a verb deliberately not built yet; "discard" is
archive/delete, a separate axis. (3) **Control-local** — `pause_check` on
`Session`; signals the loop to exit, writes nothing. The webapp's
"Pause run" button rides this kind by writing a `.runtime/pause.flag`
file the running loop polls via `pause_check`; the API route writing
the flag is an explicitly-sanctioned mutation listed in
`promptpotter/presentation/CLAUDE.md`. Its siblings are `skip.flag` and
`sample_lookahead.flag` — the same shape (write, poll, consume) for cutting a
searchpoint and for arming the scoring walk's second in-flight sample. (4) **Control-remote** —
HTTP-ingressed mutations authored by signed-in operators or
signed-in clients. Every command is appended to the canonical
per-cycle `.runtime/ledger.jsonl` as a `CommandRecord` by a sole
`CommandDispatcher` at the FastAPI seam (kwargs-only `emit_command`,
ContextVar-scoped identity + cycle); it applies the mutation inline and
acknowledges via a sibling `CommandAckRecord` on the same ledger
(kwargs-only `emit_command_ack`). **One writer for both halves** — never
split the ack onto a second subscriber; the dispatcher doing both is
simpler than the split.

Runtime FLAGS are the separate mechanism, and the one the runner really
does read: `pause` / `skip` / `spend_cap` are polled at the next sample
boundary (`infrastructure/runtime_flags.py`), not subscribed.
Outbound, no projection writes SSE frames at all — `CycleLedgerTail`
tails the on-disk ledger directly (cross-process) and fans out
`ProjectionEnvelope` frames over SSE. Identity scope rides the
existing cycle-dir tenant prefix; commands and acks carry no
per-record `tenant_id`. The closed inbound command set is declared
in `docs/specs/m12-api-openapi.yaml` (OpenAPI 3.1); the closed
outbound event set is declared in
`docs/specs/m12-events-asyncapi.yaml` (AsyncAPI 3.0); adding a
command or event kind requires updating the YAML first, in its own
PR. The permanent system-networking contract is
`docs/adr/0001-m12-control-plane.md`. (5) **Identity** — OIDC
verification at the API trust boundary. `presentation/api/middleware/oidc.py`
(Stage 1) verifies an inbound ID Token against the issuer's JWKS and
populates `IdentityContext` for the downstream resolver. Mutates no
campaign state, is not a ledger subscriber, does not signal the loop
— it is the gate establishing who the subsequent Control-remote call
is *from*. Tokens are verified at this boundary and never appear past
it (ADR-0002 gate #2 — review-enforced; no standing test). Stage 0 (auth-off, single
operator) is the degenerate case: `default_identity()` substitutes
for the middleware. Permanent contract:
`docs/adr/0002-identity-foundation.md`. This kind also
**administers the gate**: editing who may sign in (`allowlist.json`)
or the provider config is an identity-config *write*, distinct from
campaign state and never on the campaign ledger. Privileged identity
or deployment mutations ride an **in-zone operator-admin channel** — a
deployment-side companion (e.g. an on-box bot) that reaches an
untrusted message channel *outbound*, exposing no inbound surface to a
low-trust zone; audited in the identity zone (`allowlist_audit.jsonl`).
They never become Control-remote commands and never an inbound public
route — the Purdue/zero-trust rule that a control-plane mutation is not
reachable from the lowest-trust zone. Permanent contract:
`docs/adr/0004-operator-admin-channels.md`. Adding a new I/O kind requires
amending §0 first; the pre-flight gate (root `CLAUDE.md` § Pre-flight gate,
"New I/O kind → amend §0 first") blocks code that introduces one without
§0 backing. Hexagonal layer
separation is a structural invariant (fails loud at import; see
`tests/CLAUDE.md`) so data types stay free of I/O and the orchestrator can be
reused without dragging a backend client along. A **concept-first re-hierarchy** (slicing this
layer cut into per-concept vertical packages) was investigated and
**rejected**: the recurring multi-directory fix signature is the inherent
footprint of changing the central state spine — a flow that is correctly
layered, not a defect to carve away. The cut stays; don't re-propose
(analysis in `git log`). SearchPoint types are
**immutable**: once created, their fields can't change. That makes
their content hash a trustworthy identity, which is what lets
`--from N` resume a campaign with different hyperparameters and
`--fork-on-divergence` cleanly mint a sibling at the first hash
mismatch. One per-cycle `CycleEventLog.append` is the sole persistence
ingress; resume + fork ride dedicated checkpoint records on the
ledger. Display and observability subscribe to the ledger as
read-only views — never write campaign artifacts of their own.
**Single-writer invariant on the ledger** (fails loud — an out-of-allowlist
write shows up in the file tree; see `tests/CLAUDE.md`): any module
besides the ledger writing to the per-cycle `.runtime/ledger.jsonl`, or any
projection writing outside its declared allowlist, is drift. The
MeasurementArchive (the other persistence layer, see "Measurement archive"
below) is under the same discipline via the **`store/archive_views.py`
facade** — the free-function read/write surface (`measurements_for_sample`,
`reusable_results`, `record_measurement_run`, `reindex_measurements`, …)
every consumer goes through. One raw call site remains, a narrow
dataset-lifecycle operation that predates the facade
(`datasets/dataset_replace.py::restamp_dataset`); a second consumer is drift.
Together the two pins capture event-sourcing's reasoning-clarity gain
without paying replay-on-every-read.

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

**Read surfaces form exactly two clusters — split by cadence, not by
reader — over a third internal one.** The split is physical in the
cycle-dir layout: (1) **Live** — `dashboard.json`, the one churning file
carrying now-state; (2) **Settled** — the rest of the cycle-dir top level
(`index.json` = lean per-round digest + topology, `rounds/round_NNNN.json`
= deep audit, `log.md` / `review.md` / `hard_samples.json`), written at
boundaries and stable to read. Everything under **`.runtime/`** (the
`ledger.jsonl` ledger SoT — `events.jsonl` is the *workspace*-scoped
sibling only — projection caches, PoBB streams, control flags)
is the third, **internal** cluster — machinery, not a read-out. The
live/settled divide is **cadence, not audience**: both the webapp *and* a
human read across both clusters — the webapp polls `dashboard.json` live
yet opens `index.json` / round files on drill-in, and a human can tail
`dashboard.json`. So **the data the two read clusters share is by design,
NOT redundancy to collapse** — `index.json::rounds` (settled) and
`dashboard.json::rounds` (live) are the same facts at two cadences for two
reading modes, exactly the multi-projection read model the single-writer
ledger fans out to. When a *consumer* reads the wrong cadence (a live view
reading the settled file, or vice-versa), **repoint the consumer** — never
retire the other surface. A projection is dead only when **no** reader, the
human file-tree included, consults it; "the webapp no longer needs it" is
not "no one needs it." This is the read-side corollary of the single-writer
ledger: many readers, two read cadences, one source.

**The file tree is read-out, not write-in.** `dashboard.json`,
`campaign.json`, `index.json`, `round_NNNN.json`, the ledger — all are
projections written by sole-writer subscribers under the single-writer
invariant (pinned above). Operator hand-edits to these files are not
the input channel; the next ledger event overwrites them. Operator
input flows through the **Control** kinds only: Control-local
(`.runtime/{pause,skip,sample_lookahead}.flag`, polled per checkpoint) and Control-remote
(HTTP → `CommandRecord` on the ledger → runner subscriber → `CommandAckRecord`).
The early "folder-UI" workflow of just opening files was — and remains —
a read-out workflow; writes have always landed via the running loop.

The on-disk layout makes the four-entity model literal. Under each
tenant, `campaigns/{campaign_id}/` is the Campaign directory:
`campaign.json` (manifest — `dataset_name, label, created_at,
root_cycle_id, root_content_hash, backend_id, config`; identity + config
+ lifecycle intent only — run state is owned per-cycle by
`index.json::status` and derived on read for campaign surfaces), `log.md`
(campaign digest — covers every session, its forks, and its rounds),
`hard_samples.json` (campaign-scope heatmap), and `cycles/{cycle_id}/`
holding **every** cycle — all N session roots and every fork, diag, and
sweep — **all flat** — sibling kind and sweep batch id are `index.json`
metadata, not directory nesting. A flat `cycles/` store keyed by
`parent_cycle_id` scales as the fork tree grows; nested fork-of-fork
directories do not. `dashboard.json` is **per-cycle**: every cycle (root,
fork, diag, sweep) owns its live file in its own dir
(`cycles/{cycle_id}/dashboard.json`), stamped with its own `cycle_id`. A
fork's view never surfaces the parent's id; a fork seeds its prior
trajectory from the parent's on-disk file (state-sync Phase 2). Each
`dashboard.json` self-stamps its own `(campaign_id, cycle_id, session_id)`;
the webapp drops a polled payload whose stamp doesn't match the unit it asked
for, so a freshly minted cycle never renders another's data. Each campaign is a
standalone dashboard: the operator understands a campaign from
`campaign.json` + `log.md` plus the per-cycle `dashboard.json`
streams, without descending into per-cycle round detail.
`archive/` stays a peer of `campaigns/` — dataset-scoped,
cross-campaign by design (see "Measurement archive" below).

**Entry-point scope rules.** A notebook is a thin UI shell — every
non-display code cell calls into `application/` (no orchestration
logic, no scoring, no LLM calls authored in the notebook).
Convention (not CI-enforced — the structural scan was cut; see
`tests/CLAUDE.md`): notebook cells import from `application/` +
`presentation/views/` only. The one surviving notebook
(`notebooks/bbeh_potter.ipynb`) is **work-in-progress** — kept but not
part of the documented entry-point surface. Mark it WIP in cell-1
markdown so a reader knows status at a glance. The
webapp (`webapp/`) ships — served read-only at the root, chat as the
first tab — rendering views over `dashboard.json` plus a file-tree
view; a panel that reads a disk file we don't already commit to
writing needs that write committed first. The `init`
command + `/potter-run` slash command sit in `presentation/` and
orchestrate one-time onboarding (TermNorm download, dataset
conversion, API key prompts) — load-bearing for the operator's first
run; audit for accumulated cruft but don't delete the underlying
mechanism. New webapp panels arrive as ordinary sub-specs, not
silent additions.

**Tracing, Langfuse-shaped, lightweight by default.** Optimizer LLM
calls and backend matches emit structured events in
**Langfuse-compatible shape** (spans / traces / metadata) — wrapped
via the `observed_node()` context manager. Every optimizer LLM call
site is wrapped — all **five** registered nodes (`l1_generate`,
`l1_critique`, `l2_context`, `l3_plan`, `checkin`), not just the four
loop layers: `checkin` runs *around* the loop in both its modes
(`task_context.py::decompose_prompt_fields` for CLI `new`,
`origin_resolve.py::resolve_origin_turn` for web ingest), and each
binds a cycle ledger so the call is billed to the campaign it seeds.
An enumeration that stops at the loop layers is how an unwrapped,
unbilled call gets written. Events serialize to local JSONL under
`langfuse/events.jsonl` (`infrastructure/tracing/file_sink.py::_log_event`)
— no Langfuse instance, no MLflow server, no external dependency required.

**A nexus to the operator's existing observability stack — a core
capability, not a stub.** Many teams already run an observability
instance; PromptPotter drops straight into it. When **Langfuse cloud**
credentials are present in `.env`, the same events also stream there —
point PromptPotter at an existing cloud project and it becomes the
optimizer's trace store, zero schema work. **MLflow** is the on-machine
peer: an operator already running a local MLflow server flips
`settings.MLFLOW_ENABLED` and per-round runs land there (wired via
`infrastructure/tracing/mlflow_sink.py`). Both are **directly
supported, off by default** — if a team already has the infra, hooking
it up is a *drop-in upgrade* (flip a flag / add `.env` creds), never a
code change, which is exactly why both sink paths stay import-alive even
while dormant. The Langfuse schema is the **orientation point**: even
with no external sink wired, events conform to it, so importing later
(or swapping in a different backend) is configuration, not refactoring.
Tracing is fan-out only — the optimizer never reads it, so it can never
become load-bearing for the loop.

**Measurement archive (the actual database).** Beyond the per-cycle
ledger, a cross-cycle persistence layer lives at
`measurements/runs/{run_id}.jsonl` — an append-only log per run,
content-addressed by `JobSearchPoint.content_hash`, indexed by
`measurements/index.jsonl`.
Each row is `(sample × config → outcome)`, stamped with
`dataset_name` and `campaign_id` so the store answers all three data
scopes from one query path: **campaign** (`campaign_id=…`),
**dataset** (`dataset_name=…`), **workspace** (no filter). The
archive is the Workspace datastore — a peer of `campaigns/`, never
siloed into a campaign dir. **Cross-cycle, cross-session,
cross-tenant.** The on-disk format is human-readable
(operator can `cat` a row); programmatic reads go through two
retrieval views (`measurements_for_sample()`,
`measurements_for_config(predicate)`) — both behind the
`store/archive_views.py` facade. Cache reuse (skip backend calls when a
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

- **PoBB elimination** (`application/optimization/pobb/checks.py`) —
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
- **`injection_source_digest` inside `_identity_config`**
  (`dispatch/injections/registry.py` → `connectors/promptpotter.py`) —
  the injection renderers compose most of every optimizer prompt, so
  their text is L4 measurement identity. AST-normalized, so a comment
  or a reflow costs nothing while a panel's prose, its `char_cap` or
  its render condition voids the banked origins. One caller, looks
  droppable; dropping it compares candidates against a stale origin
  and says nothing.
- **`pipeline.yaml` contract** for connector self-description — the
  backend's API surface to PromptPotter. Don't simplify "because
  TermNorm is the only consumer today."
- **Hexagonal layer separation** (fails loud at import; see `tests/CLAUDE.md`)
  — without the discipline, the three entry points drift.
- **Resume + fork-on-divergence mechanism** — load-bearing for
  `--from N` and `--fork-on-divergence`. The symbols are
  `ResumeCheckpointRecord` / `ResumeCheckpointKind` (`domain/run_records.py`);
  vocabulary lives in `docs/glossary.md`.
- **Campaign as a first-class entity** —
  `campaign.json` manifest, the `campaigns/{campaign_id}/` directory
  with `log.md` + `hard_samples.json` at its root and
  `cycles/{cycle_id}/` flat below (the session root plus every fork /
  diag / sweep). `campaign_id = {dataset}__{rand6_hex}` is minted fresh
  per `new` invocation by `mint_campaign_id`; the declaration rides
  `campaign.json` as `root_content_hash` (resume's config-drift check)
  + `optimizer_prompt_hash` (audit join key), never deriving the id. `dashboard.json` is per-cycle, at
  `cycles/{cycle_id}/dashboard.json`. Cross-campaign evidence
  pooling on the same declaration rides the dataset-scoped
  `measurements/` layer, so two `new` calls on an unchanged
  declaration get distinct `campaign_id`s, share their root cycle id
  (content-addressed) and origin score (cache-served), and diverge
  from round 1 onward. The four-entity hierarchy (Workspace / Dataset
  / Campaign / Cycle, with Session a unit of a Campaign) and the three
  data scopes (campaign / dataset / workspace) are §0 invariants — a
  cleanup PR cannot collapse Campaign back into the root cycle.
- **Per-cycle `CycleEventLog` + `DerivedView` dispatch** — the
  persistence backbone. No second ingress, ever.
- **Control-remote highway** — the `CommandRecord` / `CommandAckRecord`
  / `ProjectionEnvelope` triple riding the canonical `.runtime/ledger.jsonl` via
  sole `CommandDispatcher` (inbound AND ack), `CycleLedgerTail` reading
  the ledger directly (outbound SSE, no writer). The closed inbound +
  outbound sets live in `docs/specs/m12-api-openapi.yaml` and
  `docs/specs/m12-events-asyncapi.yaml`; the permanent contract is
  `docs/adr/0001-m12-control-plane.md`. Cleanup PRs cannot collapse
  commands into a parallel queue, drop the YAML-first rule, or remove
  the sole-writer invariants — every M12-onward interactive surface
  rides this highway.
- **Hard-sample sorter (Rasch)**
  (`application/intelligence/hard_sample_sorter.py`) + the leaderboard
  it powers — first-class per §0.
- **`RoundResult.results` duplicating `all_candidate_results[winner_id]`**
  (`domain/results.py`) — deriving either from the other silently corrupts
  the difficulty ruler. `exploration.build_observations` flattens
  `all_candidate_results` across every round with no round filter and no
  dedup, so a retained incumbent — one lineage id held across k no-winner
  rounds — would contribute each observation k+1 times to a subset-invariant
  fit. `all_candidate_results` means "measured in THIS round".
- **`l1_signal_catalogue` + `pipeline_param_catalogue` + `prompt_block_catalogue`
  injections**
  (`application/optimization/dispatch/injections/catalogues.py`) — the
  discoverability scaffolding: the cross-slot rule L2 reads to write
  `l1_layout` (its vocabulary is on that field's own schema),
  the param menu L1 reads, and the reusable prompt-field blocks L1
  recombines (the one channel handing L1 prompt MATERIAL rather than
  statistics about material), the surface the pre-flight gate's
  reuse-before-adding rule leans on. Don't drop "because nobody calls it
  from production code today."
- **The `new` verb + `/potter-run` onboarding flow** — operator's
  first-run path; cruft-audit yes, mechanism delete no.
- **`new`-verb decomposition into `task_context`** — the one-time
  `checkin` LLM call that seeds the campaign when `new <name>`
  first sees a dataset. Don't fold into `l1_generate`.
- **Origin, parent, and check-in — the start definitions the whole loop
  depends on.** Say "origin", never "baseline":
  - **Origin = the starting configuration = C0.** One word, one thing. In
    program evolution an individual **is** a configuration: the origin
    resolves to an `OptSearchPoint` (`resolve_origin_opt_search_point`,
    `application/origin.py`) — the same type every candidate is — so "the
    config the loop starts from" and "C0, the first candidate" are one
    statement, not two. For a fork it is the point the fork branches *from*.
    Scoring it yields its **measurement** (round 0, `origin_accuracy`, via
    `establish_campaign_origin`) — what you get by measuring C0, never a
    rival sense of the word.
  - **The origin arrives incomplete; check-in completes it and gates it.**
    The operator supplies what they have (a pipeline, some prompt fields);
    it is not a whole origin until the **required inputs** that pipeline
    declares are resolved — query/target column map, answer space, dataset
    binding, and any node-type-raised dependency like a `candidate_source`
    node's candidate library. Origin is therefore **per-pipeline**:
    different backends require different inputs. Once it clears both gates
    below, it is the **parent of round 1's candidates** — round 0 is not
    something C0 parents; round 0 *is* C0, measured.
  - **Origin is the parent at offset 0.** The general relation is *parent* —
    the individual a candidate was mutated from, scored over the samples that
    candidate touched so the diff is matched (`RoundParent`,
    `domain/results.py`; built by `rescore_parent`, which labels it with the
    parent individual's own label — `cycle.rounds[-1].label`).
    At round 0 the parent is the origin; after that it is the prior winner.
    **Reserve "origin" for offset 0 and the fork point; everywhere else say
    parent.** Two names for one relation is how this word drifted before.
  - **Check-in** = the **process that produces a complete origin** from a raw
    upload. One LLM resolver node (`application/datasets/origin_resolve.py`)
    *proposes* the column map, the decomposed Layer-1 prompt fields (incl. an
    `answer_format` satisfying the **scorer's** extraction contract — the
    chosen matcher, not the backend, reads the final answer:
    `scoring/formula/matchers.py::EXTRACTION_NOTES`, e.g. `exact_match` reads
    the last bolded span), and the 7-field `task_context`; a deterministic,
    no-LLM **readiness gate** (`origin_readiness.py`) *gates* — mint is
    blocked until query + ground_truth + framing + answer-space are all
    CONFIRMED. The check-in **nudges the operator** (the ingest UI surfaces
    each open gap + unfulfilled pipeline dependency) until the spec is
    complete, then it's stored as the per-pipeline origin under
    `projects/{tenant}/datasets/{slug}/`. Dependencies (e.g. a candidate
    library) are dropped in place here and committed alongside the origin,
    not chased at init.
  - **Two gates, because completeness ≠ scoreability.** The readiness gate is
    *static* — it proves the required fields are present (incl. a non-empty
    `answer_format` whenever the scorer extracts a label, `_check_commit_format`),
    not that the prompt actually scores. Extractability is empirical
    (prompt × model × scorer matcher), so the second gate is the **round-0
    origin gate**: a floor that grades `critical` (e.g. all-`NO_RESULT`, a
    PP-owned health signal in `domain/results_health.py`) halts before L1
    instead of being optimized. **Resolver and operator collaborate across
    both gates** — iterating the pipeline choice, the `answer_format`, and the
    required starting values — until the origin both passes readiness *and*
    runs scoreable. Only then does the loop proceed.
  - The line: **origin IS C0 — the first candidate, or the point a fork
    branches from; check-in is the resolver+gate that produces it; every
    later round compares against its *parent*, which is the origin only at
    offset 0.** Forward plan: [`specs/roadmap.md`](specs/roadmap.md)
    § Origin-resolution check-in.
- **`MeasurementArchive` (`measurements/runs/{run_id}.jsonl` +
  `measurements/index.jsonl` index + retrieval views
  `measurements_for_sample()` / `measurements_for_config()`)** — the
  actual cross-cycle database. Per §0 it's a separate persistence
  layer from the ledger; never collapse the two.
- **Per-dataset configs in `datasets/{name}/`** (`pipeline.yaml`,
  `campaign.yaml`, `prompts/{node}.yaml`,
  `task_context.yaml`, `dataset.md`, `task_description.md`) — the operator's primary
  interface for adding a new dataset. Configs are the source of truth —
  no parallel default ladders elsewhere.
  A cleanup PR cannot move a default into PromptPotter code; if a
  setting needs a default, it goes in the dataset's config file.
- **`Evaluator` class + `evaluators` field + `all_evaluators()`
  registry + `materialize_*_values`** — the **only** sanctioned use
  of "eval" vocabulary in the codebase. A future "rename eval to
  score" cleanup PR must not touch these — they're domain language,
  not a coincidence.
- **`scripts/render_review.py` + `scripts/smoke_campaign.py`** —
  operator-facing CLI helpers (per-cycle review renderer; smoke
  test harness). Audit during cleanup §1 for accumulated cruft, but
  don't delete the underlying scripts without operator confirmation.
- **`score_search_point()` gateway**
  (`application/scoring/search_point_scorer.py::score_search_point`) — sole scoring
  ingress. Sibling to `CycleEventLog.append` and `INJECTIONS`. Don't
  add a second scoring entry path "for convenience."
- **Composite-fitness resolution chain** — **fitness is never one fixed
  number; always ask "under which formula?"** It is formula-relative — the
  **active** formula the run actually used; a **what-if** preview when the
  operator re-weights the evaluators; a **lens** that re-projects the lineage
  under an alternative criterion to show where rankings diverge
  (`lib/lineage.tsx`); a **replay** that re-scores the whole cycle under a new
  config — and mode-relative (`measured`, the samples that round actually ran,
  vs `all`, the full dataset). Two values appear in the data:
  `composite_fitness` (the score under the active formula, **served already
  resolved** — with no active formula the default per-round formula is plain
  accuracy, so it **equals** `accuracy` and readers take it verbatim) and
  `accuracy` (the plain correctness rate, formula-independent). Per-sample
  difficulty is a *separate* view, not a fitness formula (the hard-sample
  sorter bullet above). The chain is produced + resolved at three
  single-writer choke points. `compute_composite_fitness`
  (`application/scoring/metrics.py`) is the sole writer of
  `composite_fitness`; with no active per-round formula it degrades to
  accuracy **at compute time** via `_default_round_scorer`
  (`application/scoring/formula/round_scorer.py`), so the served field is
  never a sentinel — the only manufactured value is a real `0.0` for a
  validation-failed candidate. `display_fitness` (`domain/rendering.py`)
  is the **one** canonical resolved value every display + ranking site
  reads — `composite_fitness` when present (the honest `0.0` is kept),
  accuracy only on genuine `None`; `display_rank_key` is its
  argmax-over-candidates form, and **is not the election** — that is
  `elect_round_winner`'s Rasch θ-lift, which no aggregate reproduces.
  Alternative formulas (what-if, the
  `score:<formula>` lens, replay) never recompute in the consumer — they
  re-project from the stored evaluator namespace via
  `value_with_mask_applied` (`metrics.py`) and are **served** — every
  score, active or alternative, is backend-computed and the webapp never
  recomputes. Don't
  add a second composite-or-accuracy resolution; route through
  `display_fitness`. **A cycle's "best" deliberately has two bases:** the
  *winner export* (and the L2/L3 stall comparator) argmaxes cumulative
  `composite_fitness` — the optimizer's actual objective
  (`cycle.py::absorb_round` / `replay_priors`; `escalation/firing.py`, which
  now compares θ alongside composite) — while the index/dashboard
  `best_round` headline argmaxes cumulative `accuracy`, the familiar
  formula-independent number (`campaign_store/store.py::_apply_best`).
  Forcing them to agree would make the deployed winner stop optimizing the
  configured composite; don't "fix" one basis to the other.
- **`observed_node()` context manager** — the trace-emission seam
  every optimizer LLM call wraps. Cutting it removes Langfuse-shape
  compatibility (the Tracing bucket's foundation collapses).
- **`promptpotter/assets/optimizer/pipeline.yaml`** — the self-optimization claim in §0
  depends on this file having the same shape as a backend
  `pipeline.yaml`. Drift (special-case fields, parallel registries)
  invalidates the claim.

§0.5 is binary: surface is either load-bearing (named above, can't
be cut) or it isn't. Items needing a load-bearing-or-drop decision are
tracked in `docs/specs/code-debt-cleanup.md`, not in this list.
(The MLflow + Langfuse sinks are
**resolved as kept** — the observability-nexus drop-in is a core
capability, not an audit candidate; see the Tracing paragraph above.)

When in doubt about an item already in the list above: file a
one-line "kept because" note in the PR rather than cutting silently.
