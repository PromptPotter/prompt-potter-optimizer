# M10: Origin-Resolution Check-In

**Status:** spec-only — no code shipped. Next up in the M10 ingest lane (current-beta scope). Extends the ingest path defined in [`m13-chat-first-user-web.md § Ingest`](m13-chat-first-user-web.md) — that `DraftCampaign` is the object this loop drives to completeness.

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
| `optimizer.model_locked` | is model an operator-fixed axis | maps to `PARAM_FORBIDDEN_KEYS` (`domain/search_point.py`) |
| `optimizer.reasoning_floor` | starting `reasoning_effort` | origin = conservative **floor** (optimization/CLAUDE.md) |
| `optimizer.reasoning_ceiling` | may L1 escalate reasoning ("thinking-high allowed") | floor == ceiling ⇒ pinned |
| `backend.node_config` | same provider/model/reasoning-floor shape for the pipeline node's own LLM | rides `pipeline_overlay::nodes.{name}.config` |
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

Once `origin_readiness` passes, before mint the resolver emits one **plain-English recap card** — a jargon-free paragraph restating what the campaign will do ("You're optimizing a prompt that maps lab-test names to codes, starting from model X, success = exact match, up to 5 rounds"). The operator confirms *intent*, not field names. Rides the existing `origin_resolve` node (a final `ready`-turn output field) — no new infra. Fits the anti-nerdy / accessibility positioning ([`.impeccable.md`](../../.impeccable.md)): the operator never has to read the checklist's vocabulary to know what they approved.

## Non-goals

Multi-file / multi-sheet reconciliation in one draft (one file → one draft for now) · Google Sheets URL ingest (no file = separate fetch feature) · the resolver's persona/system-prompt tuning (rides the chat-LLM design pass in the parent spec) · auto-confirming low-confidence findings · scoring-composite synthesis beyond the registered scorers.

## Sequencing (not scheduled)

1. Parser split (`read_tabular` + `materialize_samples`) + drop the literal-column requirement.
2. `origin_readiness` checklist + `DraftCampaign.resolved` provenance; wire the `422 origin_incomplete` gate into `mint-campaign-from-draft`.
3. `origin_resolve` node + `build_origin_bundle` + response model.
4. `resolve-origin` verb (openapi first) + the loop + auto-confirm.
5. Frontend: surface `gaps` + per-field provenance in the ingest panel; the resolver drives chat turns.
