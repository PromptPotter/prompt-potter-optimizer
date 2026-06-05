# M10: Origin-Resolution Check-In

**Status:** the **deterministic gate** (sequencing steps 1–2, column-mapping scope) shipped 2026-05-30; the **LLM proposer + the closed-set checklist + the operator check-in window** (steps 3–5) shipped 2026-05-30. Extends the ingest path defined in [`m13-chat-first-user-web.md § Ingest`](m13-chat-first-user-web.md) — that `DraftCampaign` is the object this loop drives to completeness.

> **Shipped (steps 3–5, 2026-05-30) — implementation notes (divergences from the design below, recorded deliberately).**
> - **Reused the `checkin` node, not a new `origin_resolve` node** (operator steer + pre-flight gate #2 "an existing channel already does this"). The pre-cycle consultation node `checkin` was bumped to **`checkin/2`** (origin-aware) and its `CheckinOutput` extended in place with the origin block (`assessment`, `findings[{field, proposed_value, confidence, evidence}]`, `next_action{kind, questions}`, `recap`). The CLI task-decomposition path uses the same node, leaving the origin block empty. There is no `origin_resolve` node, no `OriginResolution` response model — the shape rides `CheckinOutput`.
> - **`build_origin_bundle` → `build_origin_consultation`** (`application/datasets/origin_resolve.py`): the draft origin context (headers, sample rows, current values + provenance, open gaps, operator framing) rides the LLM **`user_content`**, not new template placeholders. So `_TEMPLATE_EXTRAS["checkin"]` is unchanged (still just `consultation_instruction`) and the CLI path is untouched. Still routes through `run_optimizer_node` → `compile_prompt` → `llm_call`, wrapped in `observed_node` (gate #8).
> - **Spend rides the tenant workspace ledger** (`projects/{tenant}/.workspace/events.jsonl`) — the resolve turn sets `_CYCLE_LEDGER` to `CycleEventLog.open_workspace(...)` so `emit_token_usage` + `LLMCallRecord` land there (ADR-0003 spend-on-ledger holds pre-cycle), matching `register-backend` / `sync-backend-experiments`.
> - **Closed-set scope this slice:** the checklist gained `task_description` + the config knobs (`connector`, `scoring_composite`, `max_rounds`, `optimizer.provider`, `optimizer.model`, `backend.node_config`) on top of the columns. The config knobs **auto-confirm from our `pipeline.json` / `campaign.json` template defaults** at `create()` (deterministic) so a once-hidden default is now a visible, operator-overridable, confirmed value; `task_description` lands `unset` (the one operator-stated gap the resolver fills).
> - **The `source` sub-tag (`auto` vs `stated`) shipped 2026-05-30.** `ProvenanceSource` (`domain/origin_provenance.py`) is an axis orthogonal to `Provenance`: a valued field records *who* set it — `auto` (a template default auto-confirmed at `create()`, a deterministic column auto-detect, or a resolver finding) vs `stated` (an `edit-draft-campaign` operator patch / column pick). `DraftCampaign.sources` carries it; `resolution_block` writes a `sources: {field → auto|stated}` map into `cache.json` beside `provenance`; the wire (`DraftCampaign.sources` + `OriginResolution.sources`) and the `OriginCheckinPanel` (a small `auto` / `you set` chip beside each provenance badge) surface it. A `confirmed + auto` entry reads as "a default we picked, override if wrong"; `confirmed + stated` as "your choice". Audit-only — the gate verdict still reads `resolved`, never `sources`.
> - **CLI parity shipped 2026-05-30 — the check-in is now *core*, not a bolt-on verb.** First shipped as a standalone `promptpotter ingest <file>` verb; **folded into `new` the same day.** `new <name|file>` dispatches on `Path(arg).is_file()`: a name uses an authored `datasets/<name>/`; a **file** is ingested → origin-resolved → committed as a tenant dataset, then **falls through to the exact authored mint+loop** — one verb, one mint+run engine, the rich inline terminal display (not a detached, dashboard-only job). The file branch owns no parallel logic: parse→draft→cache is `application/datasets/ingest.py::ingest_draft` (shared with the web `POST /datasets/ingest`); the resolver auto-drives via `resolve_origin_until_gated` (bounded turns, high-confidence auto-confirm); the commit step is extracted to `launcher.py::commit_draft_to_dataset`, shared with the web `/commands/mint-campaign-from-draft`. **Enabler — the tenancy root-fix:** `init_services` resolves the dataset config dir tenant-first (`resolve_dataset_config_dir`) and now carries it on `Session.dataset_config_dir`; every dataset-file loader (`prompts.py`, `config.py`, `origin.py`, `sweep_runner.py`) reads that one resolved dir instead of recomputing a repo-relative `datasets/{name}/` path. This closed a *pre-existing* gap — ingested tenant datasets were invisible to the loop's overlay/prompt loaders — and makes ingested datasets first-class to **both** write verbs (`new <slug>` / `resume`), not just a one-shot mint. The fold *deleted* the prior `JobRegistry` foreground-await machinery + `get_task` accessor. Operator answers ride repeatable `--set field=value` (CONFIRMED + STATED before the resolver, so they seed it); residual gaps print the resolver's questions and exit non-zero — no silent default reaches mint. Satisfies the presentation-layer "single orchestration layer, three entry points" invariant.
> - **Structured `ask` answer-back loop shipped 2026-05-30.** `next_action.questions` went from `list[str]` to `list[OriginQuestion{field, prompt, options}]` (`dispatch/schemas.py`, resolved-schema regenerated). `field` names the checklist gap the answer resolves; the `OriginCheckinPanel` renders each question with a control keyed to its answer set (`questionOptions`: the resolver's `options`, or the uploaded headers for a column question, else free text) and applies the answer via `edit-draft-campaign` (`questionPatch` maps field-id → patch key; server flips the field CONFIRMED + STATED). Closes § The loop step 2 — an `ask` answer now applies directly as a confirmed patch instead of being a read-only prompt. `backend.node_config` + unknown fields yield no patch (not string-applicable), so the control is omitted.
> - **Reasoning floor/ceiling + `model_locked` are NOT operator-check-in fields (engine finding, 2026-05-30).** The spec table below labels `optimizer.reasoning_floor` / `reasoning_ceiling`, but `reasoning_effort` is *only* a backend-node param (a `pipeline.json::nodes.{name}.config` key alongside `temperature` / `max_tokens`) — the optimizer-LLM client consumes no `reasoning_effort` at all. So the reasoning floor the operator can set already rides the checklist's **`backend.node_config`** field (the backend node overlay); there is no separate optimizer-side reasoning knob to wire, and adding one would be a hidden no-op default (the anti-pattern this repo forbids). `model_locked` maps to `OptimizationConfig.forbidden_axes_strict` (a loop-policy knob, default-True today). Per operator steer: **only the backend-node config belongs in the operator check-in; the optimizer-side reasoning-escalation policy + the model-lock axis are developer-code concerns, not check-in flow.** The spec table rows for `optimizer.reasoning_floor` / `reasoning_ceiling` / `model_locked` are struck from the operator closed set on that basis — kept below for the design record only.
> - **Loop bound:** one resolver turn per `resolve-origin` request (the UI/operator drives subsequent turns); naturally bounded by interaction rather than an internal auto-spin, so no `MAX_ORIGIN_RESOLVE_TURNS` constant was needed.
> - **`resolve-origin` verb** declared in `m12-api-openapi.yaml` first, then the synchronous handler in `commands.py` (mirrors `edit-draft-campaign`); response is `{resolution, draft}`. **Frontend:** the `OriginCheckinPanel` in `IngestPane.tsx` is the operator check-in window — closed-set fields with provenance badges + a "Set up with AI" turn that surfaces the resolver's assessment/questions/recap.

> **Shipped gate (2026-05-30).** The proposer/gate split's *gate half* is live, scoped to the input/target column mapping — the field that was genuinely broken (ingest hard-required literally-named `query`/`ground_truth` columns). Parser split: `read_tabular(blob, fmt) -> Table` (header-agnostic, no literal-column gate) + `materialize_samples(table, *, query_col, ground_truth_col)` (run at commit). `DraftCampaign` gained `headers` + `column_query`/`column_ground_truth` + `resolved: dict[field, Provenance]` (`domain/origin_provenance.py`); `create()` auto-confirms only literal `query`/`ground_truth` headers (deterministic, no LLM) and otherwise leaves the mapping `unset`. Pure `origin_readiness(draft)` (`application/datasets/origin_readiness.py`) gates `mint-campaign-from-draft` with `422 origin_incomplete` (`details.gaps`); `edit-draft-campaign` carries `column_query`/`column_ground_truth` to confirm. Materialization moved to commit-time; the draft `cache.json` carries a `resolution` block (provenance + gaps) for on-disk readability. **Remaining (steps 3–4):** the `origin_resolve` LLM node + `build_origin_bundle` + `resolve-origin` verb + auto-confirm loop, and extending the checklist to the rest of the closed set (task framing, connector/scorer/round-cap/model provenance) once the proposer can justify + auto-confirm them.

**Depends on:** the slice-1 ingest path (`POST /datasets/ingest`, `DraftCampaign`, `edit-draft-campaign`, `mint-campaign-from-draft`) — all shipped. This spec adds the gate *between* ingest and mint.

## Problem

`DraftCampaign.create()` (`application/datasets/draft_campaign.py:126`) silently sets `connector`, `scoring_composite`, `max_rounds`, and `optimizer_model=""`, and `parse_csv_to_samples` (`application/datasets/csv_ingest.py:73`) hard-requires literally-named `query` / `ground_truth` columns. Both are **hidden defaults** — a campaign can mint with an origin the operator never actually specified, and a file whose columns are named `input` / `gt` is rejected outright rather than understood. This violates the project's `no-hidden-defaults` rule: every origin knob should be operator-stated, not silently assumed.

## Principle — proposer / gate split

Two parties decide "is this origin complete":

- An **LLM resolver** (`origin_resolve` node) *inspects and proposes*. It never declares done.
- A **deterministic checklist** (`origin_readiness.py`) *gates*. Mint is blocked until it passes.

Every origin field carries a provenance tag: `unset | proposed | confirmed`. **No field reaches mint while `unset` or `proposed`.** The resolver can only move a field `unset → proposed` (an inference from the data) or surface a question whose answer moves it `→ confirmed` (operator-stated). High-confidence proposals auto-promote `proposed → confirmed` (see *Auto-confirm*). The checklist — not the LLM — is the source of truth for completeness; a false `ready` from the LLM is rejected and the open gaps are fed back.

This makes the feature's job precise: **drive every origin field to `confirmed` provenance, with no silent default surviving to mint.**

## The origin-readiness checklist (closed set)

`origin_readiness(draft, headers) -> OriginReadiness{complete: bool, gaps: list[FieldGap]}`. Pure function, no I/O. Required fields, each `{value, provenance}`:

| Field | Resolved by | Floor / lock note |
|---|---|---|
| `column.query` | which uploaded header is the input | — |
| `column.ground_truth` | which uploaded header is the target | — |
| `task_description` | non-empty framing | — |
| `optimizer.provider` | optimizer LLM provider | — |
| `optimizer.model` | optimizer LLM model | — |
| ~~`optimizer.model_locked`~~ | ~~is model an operator-fixed axis~~ | **struck — not a check-in field.** Maps to `OptimizationConfig.forbidden_axes_strict` (loop policy, default-True); developer-code, not operator check-in (see notes block). |
| ~~`optimizer.reasoning_floor`~~ | ~~starting `reasoning_effort`~~ | **struck — `reasoning_effort` is a backend-node param only; the optimizer LLM consumes none.** The operator-settable floor rides `backend.node_config` below. |
| ~~`optimizer.reasoning_ceiling`~~ | ~~may L1 escalate reasoning~~ | **struck — reasoning escalation is developer-code (L1 policy), not check-in.** |
| `backend.node_config` | the pipeline node's own LLM shape — provider / model / `reasoning_effort` floor | rides `pipeline_overlay::nodes.{name}.config`; **this is where the operator-settable reasoning floor actually lives** |
| `connector` | which connector | `termnorm` only today |
| `scoring_composite` | which scorer | `exact_match` only universal scorer today |
| `max_rounds` | round cap | — |

`FieldGap` carries `{field, reason ∈ unset|proposed_unconfirmed, hint}` so the operator (and the AI reading `cache.json`) sees exactly what blocks mint.

## The `origin_resolve` node

A new optimizer node. Per-turn input bundle (assembled fresh — see *Architecture wrinkle*): uploaded `headers`, `sample_preview`, `n_samples`, current `draft` with per-field provenance, the still-open `gaps`, and the operator's latest free-text message. Structured output, registered in `OPTIMIZER_RESPONSE_MODELS`:

```
OriginResolution {
  assessment: str                                   # one-line read of current state
  findings: [{field, proposed_value, confidence: "high"|"low", evidence}]
  next_action:
    | {kind: "ask",     questions: [{field, prompt, options?}]}
    | {kind: "propose", patch: DraftPatch}           # applied as provenance=proposed
    | {kind: "ready"}                                # code re-runs checklist; false-ready rejected
}
```

`findings` must cite `evidence` from the bundle (a header name, a sample value, a stated operator preference) — speculative findings without evidence fail a behavior check, mirroring the `evidence_grounding` contract on `l1_generate`.

## The loop

1. Resolver runs → emits `next_action`.
2. `ask` → questions surface on the chat/panel; the operator's answer applies as a patch with provenance `confirmed` (operator-stated).
3. `propose` → patch applies with provenance `proposed`; **auto-confirm** promotes any field whose backing `finding.confidence == "high"` to `confirmed`; low-confidence proposals stay `proposed` and wait for an operator click.
4. After each apply, re-run `origin_readiness`. Dirty ⇒ re-run resolver with the updated gaps. Clean ⇒ enable mint.
5. `ready` is checked against `origin_readiness`, never trusted directly.

Bounded by `MAX_ORIGIN_RESOLVE_TURNS` (mirror the `MAX_AUTO_REBASES = 10` backstop). Over-cap halts the loop and surfaces the remaining `gaps` to the operator directly — no silent give-up, no silent default.

### Auto-confirm (chosen model)

High-confidence findings auto-promote `proposed → confirmed` so an obvious column match (`input` → `query`, `gt` → `ground_truth`) or a matrix-sourced reasoning floor doesn't demand a click. Low-confidence findings stay `proposed` and block mint until confirmed. The resolver assigns `confidence`; the gate enforces that only `high` may auto-promote. Every auto-confirmed field still lands in `cache.json` with `provenance: confirmed, source: auto` so the decision is auditable on disk.

## Architecture placement

- **Node prompt + response model** — template in `dispatch/llm_call/prompts.py`; model in `OPTIMIZER_RESPONSE_MODELS`; called via `run_optimizer_node` → `llm_call` (never raw `chat()`), wrapped in `observed_node` for Langfuse tracing (pre-flight gate #8).
- **Architecture wrinkle (named deliberately).** `build_bundle(cycle)` (`dispatch/hub/facade.py:127`) is **cycle-scoped**; origin-resolution runs **pre-cycle** (draft stage, no `Cycle` exists yet). So it needs its own small bundle assembler — `build_origin_bundle(draft, headers, gaps, operator_msg)` — that does **not** route through `build_bundle`/`DispatchHub.fill_l1`. It still routes through `run_optimizer_node`/`llm_call`/`observed_node` (the convention points). This is a genuine new seam, not a reuse of the L1/L2/L3 bundle path; the spec calls it out rather than pretending the cycle-scoped path fits.
- **Checklist** — `application/datasets/origin_readiness.py`, pure. `mint-campaign-from-draft` calls it and refuses (422 `origin_incomplete`, `details.gaps`) when dirty.
- **Provenance** — `DraftCampaign` gains `resolved: dict[str, Provenance]`; `create()` stops silently defaulting (values become `proposed` findings the resolver must justify, or `unset`).
- **Parser split** — `parse_csv_to_samples` splits into `read_tabular(blob, fmt) -> Table{headers, rows}` (CSV now; XLSX via the already-present `openpyxl` next) and `materialize_samples(table, mapping) -> list[Sample]`, run only once `column.*` are confirmed. The parser no longer hard-requires literal column names — column identity is the resolver's job.

## Wire deltas (declare before handlers — gate #4)

- **New Control-remote verb** `resolve-origin` — one resolver turn against a draft. Declared in `m12-api-openapi.yaml` (request: `{draft_id, message?}`; response: `OriginResolution` + post-apply `DraftCampaign`) before any handler lands.
- **Patches reuse** the existing `edit-draft-campaign` highway; no new patch verb.
- **`mint-campaign-from-draft`** gains the `422 origin_incomplete` response with `details.gaps`.
- SSE `DraftUpdatedRecord` (already on-deck in the parent spec) carries the updated provenance so chat + panel both reflect resolution state.

## On-disk (gate #6)

The checklist state — current `gaps`, per-field provenance + source (`stated` / `auto`), and the last `OriginResolution` — writes into the draft `cache.json` at `projects/{tenant}/datasets/.drafts/{draft_id}/`. An operator (or the AI) opens that file and sees exactly what blocks mint and why each resolved field was set, without running anything.

## Pre-flight gate

1. **§0 bucket** — dispatch (new optimizer node) + on-disk (draft provenance in `cache.json`) + Control-remote (`resolve-origin` verb).
2. **Existing channel?** — patches ride `edit-draft-campaign`; the LLM call rides `run_optimizer_node`/`llm_call`. Only the `resolve-origin` verb + `origin_resolve` node + checklist are new.
3. **Name distinct?** — `origin_resolve`, `origin_readiness`, `resolve-origin` — grep-clean against existing concepts.
4. **Self-describing + new I/O kind?** — names read in isolation; no new I/O kind (rides existing Persistence / Control-remote / Display). New command + SSE event declared in the wire specs first.
5. **Rides existing infra?** — yes: `DraftCampaign`, the command highway, the dispatch LLM path. Only sidecar is the pre-cycle bundle assembler, justified above.
6. **AI-readable on disk?** — yes: checklist + provenance in `cache.json`.
7. **§0 update?** — no; this is an application-layer loop, not a backbone change.
8. **Langfuse trace?** — yes: `origin_resolve` wrapped in `observed_node`.

## Operator surface (origin check-in UI)

The resolver loop is also the **first thing a new operator sees**. After their first message in the chat surface (today an unresponsive placeholder — see [`m13-chat-first-user-web.md`](m13-chat-first-user-web.md)), a check-in panel opens and walks the origin to completeness in two tiers:

1. **Required first.** The closed-checklist fields that block mint, each shown with its current value + provenance (`unset` / `proposed` / `confirmed`) and, where one exists, the proposed default the resolver inferred (e.g. `input → query`, a matrix-sourced reasoning floor). The operator confirms or corrects; high-confidence proposals are pre-checked (auto-confirm).
2. **Optional next.** Knobs that refine but don't block — e.g. a structured-output format for `llm_only` mode, the reasoning ceiling, `max_rounds` above the default. Collapsed by default so the required path stays short.

The panel reads its state from the draft `cache.json` (gaps + per-field provenance), so it never invents anything the gate doesn't already track.

### Plain-language recap (proposed — Claude's addition, review me)

Once `origin_readiness` passes, before mint the resolver emits one **plain-English recap card** — a jargon-free paragraph restating what the campaign will do ("You're optimizing a prompt that maps lab-test names to codes, starting from model X, success = exact match, up to 5 rounds"). The operator confirms *intent*, not field names. Rides the existing `origin_resolve` node (a final `ready`-turn output field) — no new infra. Fits the anti-nerdy / accessibility positioning ([`VOICE.md`](../../VOICE.md)): the operator never has to read the checklist's vocabulary to know what they approved.

## Non-goals

Multi-file / multi-sheet reconciliation in one draft (one file → one draft for now) · Google Sheets URL ingest (no file = separate fetch feature) · the resolver's persona/system-prompt tuning (rides the chat-LLM design pass in the parent spec) · auto-confirming low-confidence findings · scoring-composite synthesis beyond the registered scorers.

## Sequencing (not scheduled)

1. ✅ **Shipped 2026-05-30.** Parser split (`read_tabular` + `materialize_samples`) + drop the literal-column requirement.
2. ✅ **Shipped 2026-05-30 (column-mapping scope).** `origin_readiness` checklist + `DraftCampaign.resolved` provenance + `Provenance` enum; `422 origin_incomplete` gate wired into `mint-campaign-from-draft`; column confirmation rides `edit-draft-campaign`. (Closed-set fields beyond the column mapping join the checklist with step 3's proposer.)
3. ✅ **Shipped 2026-05-30.** Origin-aware **`checkin/2`** node (reused, not a new `origin_resolve` node) + `build_origin_consultation` + extended `CheckinOutput`. See the implementation-notes block at the top for the divergences.
4. ✅ **Shipped 2026-05-30.** `resolve-origin` verb (openapi first) + the one-turn loop + high/low-confidence auto-confirm + the expanded closed-set checklist.
5. ✅ **Shipped 2026-05-30.** Frontend `OriginCheckinPanel` in `IngestPane`: closed-set fields + per-field provenance + the "Set up with AI" resolver turn (assessment / questions / recap).
