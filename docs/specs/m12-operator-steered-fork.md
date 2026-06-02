# M12: Operator-Steered Fork (human-in-the-loop steer)

> **Status:** Implemented. Stop → select a candidate → **Steer & fork** in the Scoring inspector ([`ScoringInspector.tsx`](../../webapp/components/dashboard/ScoringInspector.tsx) → [`control-panel/SteerForkPanel.tsx`](../../webapp/components/dashboard/control-panel/SteerForkPanel.tsx) in a `Dialog`): edit the candidate's evolved prompt + the full node config (model / params / provider, all `pipeline.json`-driven via `node_config_schema`, with the structured output shown read-only) + reconcile run limits → fork tagged `operator_steered`. Every operator fork carries a seed (the `ForkSeed` is required). Typed `ForkSpec`/`ForkSeed`/`LimitOverrides` ([`domain/run_records.py`](../../promptpotter/domain/run_records.py)); seed read once at the runner seam from `.overrides/seed.json` (`CycleOverrideMixin`); origin resolved fork-seed-first (`resolve_origin_opt_search_point`). The `fork-cycle` command **mints then launches** the fork (an implicit resume against the new cycle), so steer-and-fork continues optimizing in one gesture — minting alone left the fork seeded-but-idle when driven from the web (no manual CLI `resume` ever came). Rode the existing `fork-cycle` command + control-plane highway ([`ADR-0001`](../adr/0001-m12-control-plane.md)) — **no new verb.**

## What this covers

The operator watches a running campaign, sees a promising (or stuck) searchpoint, and wants to **steer**: stop, pick that searchpoint, edit its prompt/config, and keep optimizing **from the edited point** as a fork — without mutating the dataset origin or discarding the lineage.

The loop, in existing primitives:

```
stop-cycle (exists)
  → operator selects a searchpoint   (SelectionContext candidate selection — exists)
  → edits its prompt/config          (PipelineConfigEditor + PromptFieldsEditor, flipped mode="edit")
  → fork-continue                    (a fork seeded from the EDITED searchpoint, resumes optimizing from there)
```

This is the editable v2 of the node panel. v1 ships read-only (the panel already does); this spec is the write path it defers to.

## Why it isn't here yet (the structural gap)

`fork-cycle` exists and the ledger-inheritance machinery works (`_apply_fork_cycle` in `command_dispatcher.py`, `inherit_from` in `ledger.py`), **but a fork inherits the parent's config/prompt verbatim** — its payload is only `{round, candidate_id}` (an audit-trail fork). There is:

- **No override slot** on the fork payload for edited config/prompt.
- **No cycle-scoped config store** — config/prompt is **dataset-scoped** (`datasets/{name}/pipeline.json` + `prompts/`). Editing in place changes *every* run on that dataset, which is why the v1 panel is read-only. An edit must seed a *different run*, never mutate the origin.

The only existing "edit config+prompt then run" path is **draft → mint** — but that's a *new declaration* (new campaign identity), not a continuation of the lineage. Steering needs continuation with an override.

## Concrete gaps to close

1. **`fork-cycle` carries edits + limit overrides + provenance.** Extend `ForkCyclePayload` (schema-first in [`m12-api-openapi.yaml`](m12-api-openapi.yaml) per the pre-flight gate, *before* the applier) to accept `{from_searchpoint, pipeline_overlay, starting_prompt, limit_overrides}` plus a `steered_by` / human-provenance marker. The applier seeds the fork's origin from these rather than inheriting verbatim. **Human steering is recorded, not forbidden** — the fork is stamped as operator-steered in the ledger (provenance, queryable in lineage), so the campaign tree shows where a human intervened. This is policy-compliant: operators may act; we just record it.
2. **Cycle-scoped config/prompt override store.** A fork with steered settings needs a per-cycle seed — the fork's origin = the chosen searchpoint **+** the operator's edits. Today there is no place for that to live below the dataset. Smallest shape: write the override into the fork's own cycle dir as its declared origin overlay, read at session bootstrap; don't bolt a sidecar onto the dataset layer.
3. **Optimizer resume-from-seed.** Bootstrap the forked cycle's origin `OptSearchPoint` from the edited point and **continue** (not replay). Reuses `resume_and_fork/` + `for_session(seed_from_cycle_id=…)`, extended to seed from an *edited* point rather than inherit the parent point unchanged.
4. **Webapp affordance — unify the existing fork trigger, don't duplicate it.** The dashboard *already* has a "create fork" action on a selected searchpoint (`postCreateFork`) — it's under-engineered (bare `{round, candidate_id}`, no edit/limit step). **Extend that one**, don't add a parallel button. It grows into "Edit & fork from here": flips `PipelineConfigEditor`/`PromptFieldsEditor` from `mode="readonly"` → `mode="edit"`, adds the limit-reconciliation step, and routes `onApply` into the richer `fork-cycle` payload instead of `edit-draft-campaign`. Gated on `!isLive` (stop first). Reuses `SpendBudgetControl`'s `_postCommand` write pattern.

## Limit reconciliation at fork time

A fork inherits the parent's **stopping criteria**, but the parent already spent part of them — so the fork dialog must show *consumed vs remaining* for each limit and let the operator re-set it before continuing. Without this the operator can't tell whether the fork has room to run.

- **Round budget.** Parent `max_rounds` = 6, completed 3 → "3 of 6 rounds left." Operator can accept (continue to 6) or raise/lower. The fork's round counter continues from the parent's, so the limit is an *absolute target*, not a fresh allotment — the dialog must make that explicit ("3 left", not "6 fresh").
- **Spend budget.** Same shape for the money cap (`change-spend-budget` / `SpendBudgetControl` — `dashboard.json::spend`): spent-so-far vs cap → remaining; operator can lift the cap for the fork. Reuses the existing spend-budget write path, not a new one.
- **Other finishing criteria** (patience / PoBB confidence gate / any future stop knob) follow the same pattern: surface inherited value + how much the parent consumed, allow override at fork. Drive this off whatever the run already records as its stop state — no parallel bookkeeping.

The fork-creation dialog is therefore **edit-the-searchpoint + reconcile-the-limits** in one confirm step. The `fork-cycle` payload carries the reconciled limit overrides alongside the prompt/config edits (extends gap 1's `{from_searchpoint, pipeline_overlay, starting_prompt}` with the limit fields).

## Boundaries / non-goals

- **No dataset-origin mutation.** The dataset-scoped origin stays the recommended-derivation baseline; steering forks off it, never overwrites it. Consistent with the default-branch-from-origin rule (a knob change forks a sibling cycle).
- **No new command verb.** This is `fork-cycle` with a richer payload, not a parallel write path.
- **Not the L4 / multi-connector track.** Sits alongside [`m12-multi-connector.md`](m12-multi-connector.md) and ADR-0001, sharing the control plane; independent of L4 closure.

## Code surface (when built)

| Area | Files |
|---|---|
| Command contract | [`m12-api-openapi.yaml`](m12-api-openapi.yaml) (`ForkCyclePayload`) |
| Applier | `command_dispatcher.py` (`_apply_fork_cycle`), `ledger.py` (`inherit_from`) |
| Resume-from-seed | `application/.../resume_and_fork/`, `for_session(seed_from_cycle_id=…)` |
| Webapp write | `BackendNodeDetail.tsx` (flip `mode`), `ChatPane.tsx` / searchpoint selection, `_postCommand`/`postCreateFork` |
| Editors (reuse) | `ingest/PipelineConfigEditor.tsx`, `ingest/PromptFieldsEditor.tsx` (already unwelded; flip to `mode="edit"`) |
