// The DRAFT-CAMPAIGN wire shapes — what check-in and origin resolution exchange, with no fetch
// among them. Pure types, so nothing here can be called; `ingest.ts` is where they are used.
//
// This module is also the single owner of `lib/`'s one import from `components/`
// (`PipelineView`). That edge points the wrong way and is tolerated in exactly one file so a
// reader can see the whole of it at once rather than finding it mid-request-helper.

import type { NodeConfigParam, NodeOutputSchema } from "./types";
import type { PipelineView } from "@/components/workflow";

// M13 chat-first dataset ingest. `postIngestDataset` uploads a CSV + mints a
// durable `checkin` campaign, returning its `DraftCampaign` (`draft_id` IS the
// `campaign_id`); `postEditDraftCampaign` sparse-patches the draft (both the chat
// assistant tool-call and the panel "Apply" button ride this); `postStartCheckin`
// gates + commits + spawns the runner, flipping the check-in to `active`. Wire
// contract pinned in `docs/specs/m12-api-openapi.yaml` (`POST /datasets/ingest`,
// `POST /commands/edit-draft-campaign`, `POST /commands/start-checkin`).

// One uploaded column header's provenance tag — mirrors the server's
// `domain/origin_provenance.Provenance` StrEnum. `unset` = no value yet,
// `proposed` = an inference awaiting confirmation, `confirmed` =
// operator-stated or auto-confirmed. No field reaches mint while `unset`
// or `proposed` (the deterministic `origin_readiness` gate).
export type ProvenanceTag = "unset" | "proposed" | "confirmed";
// The backend-pipeline permission surface the new-campaign UI renders before
// commit. A draft's `pipeline_overlay` is empty until commit, so the connector
// node-config seed (TermNorm's reasoning clamp) is otherwise invisible — this
// block carries it so the UI can show the optimizer is *locked out* of certain
// params (model/provider campaign-wide; thinking above the connector floor),
// not merely that a value is the default. Server-derived per request.
export interface OptimizerLocks {
  // Connector default pipeline step list (e.g. `["llm_only"]`).
  pipeline: string[];
  // Params the optimizer may never permute on any node (`["model","provider"]`) —
  // always locked (an invariant, not a per-campaign policy).
  forbidden_axes: string[];
  nodes: Record<string, OptimizerNodeLocks>;
}
export interface OptimizerNodeLocks {
  // Effective per-node config floor (connector seed + overlay) — the active
  // value of each control, e.g. `{ reasoning_effort: "low", temperature: 0 }`.
  config: Record<string, unknown>;
  // Closed set the optimizer may permute per param. A ladder value absent here
  // renders crossed-out (optimizer locked out).
  param_allowed_values: Record<string, string[]>;
}
// A categorical input the draft's active pipeline requires beyond
// (pipeline + dataset + origin), derived server-side from the pipeline's node
// types. `kind` is the dependency family (`candidate_library` today); `node`
// names the node that raised it; `fulfilled` is whether the draft already
// carries it. The ingest UI renders the unfulfilled ones with a drop-zone so the
// operator supplies the missing input in place.
export interface PipelineDependencyWire {
  kind: string;
  node: string;
  title: string;
  hint: string;
  fulfilled: boolean;
}
// The campaign-config knobs a draft carries, as one object — materialized into
// the committed campaign.json::optimization block. Operator-facing names. A new
// knob is one property here, not a fresh field threaded through every surface.
export interface OptimizationOverridesWire {
  // Round ceiling (1–100). Smart-default 5 (the M10 prompt-iteration default).
  max_rounds: number;
  // How the reusable prompt building-block library reaches the optimizer:
  // suggest-but-may-invent (default), library-only, or no library at all.
  prompt_block_catalogue: "guidance" | "restrict" | "off";
  // Pluggable orchestration mechanism toggles (sorting/selection + early-abort
  // groups). Nested {group:{toggle:bool}}; seeded with the stock defaults.
  mechanisms: Record<string, Record<string, boolean>>;
}
export interface DraftCampaignWire {
  draft_id: string;
  slug: string;
  sample_preview: Array<{ query: string; ground_truth: string }>;
  n_samples: number;
  connector: string;
  scoring_composite: string;
  // The campaign-config knobs (round ceiling, model lock, mechanism toggles).
  optimization_overrides: OptimizationOverridesWire;
  raw_task_description: string;
  pipeline_overlay: Record<string, unknown>;
  // Header-agnostic ingest (A3 origin-resolution gate). `headers` are the
  // uploaded file's columns in order; `column_query` / `column_ground_truth`
  // are the operator-resolved input/target mapping (empty until picked);
  // `field_provenance` carries per-field provenance keyed by dotted field name
  // (`column.query`, `column.ground_truth`, `task_description`). The mint gate
  // blocks until both columns are `confirmed` and members of `headers`. Config
  // is not gated — it carries no provenance entry.
  headers: string[];
  column_query: string;
  column_ground_truth: string;
  field_provenance: Record<string, ProvenanceTag>;
  // The campaign's origin prompt — `PromptTemplate.prompt_field_dict()` shape
  // (the six string fields + optional `few_shot_examples`). Seeded by the
  // check-in decomposition or an authored dataset's prompt; operator-editable
  // before commit. Empty `{}` until the check-in fills it.
  origin_prompt_fields: Record<string, unknown>;
  // Number of entries in the dropped candidate library (0 = none yet). The full
  // list isn't sent — a library can run to tens of thousands of entries; the UI
  // needs only fulfilled-ness + size.
  candidate_library_size: number;
  // The origin's sanctioned inner-optimizer model allow-list (ticked in setup).
  // Empty = nothing sanctioned = restrictive default (any human model steer taints).
  allowed_models: string[];
  created_at: string;
  updated_at: string;
  // Connector-derived backend-pipeline permission surface; see `OptimizerLocks`.
  optimizer_locks: OptimizerLocks;
  // The draft's parsed pipeline render — graph `view` + per-node config/output
  // schema, the SAME shape `GET /datasets/{name}/pipeline` serves for a committed
  // dataset, but computed from the draft (a pre-commit check-in has no
  // `datasets/{slug}/` dir). The ingest node editor renders from these directly,
  // so it never fetches by slug (which would 404 and hang on "Loading node…").
  pipeline_view: PipelineView | null;
  node_config_schema: Record<string, NodeConfigParam[]>;
  node_output_schema: Record<string, NodeOutputSchema | null>;
  // The active pipeline's required inputs + whether each is fulfilled. Drives the
  // "drop the missing input" affordance in the ready panel.
  dependencies: PipelineDependencyWire[];
  // Server-authoritative mint-gate verdict, recomputed on every draft response
  // (the full `origin_readiness` checklist — columns, task framing, node
  // models; no individual prompt field is gated). The UI gates Start on this
  // and renders these gaps; the client never re-derives the gate (the
  // node-model half can't be mirrored faithfully and would drift).
  readiness: { complete: boolean; gaps: OriginGap[] };
}
// One origin field still blocking mint, as returned by the server's
// `origin_readiness` checklist — carried on the draft wire's `readiness.gaps`
// and on the `422 origin_incomplete` `details.gaps` array.
export interface OriginGap {
  field: string;
  reason: string;
  hint: string;
}
// Version-and-repoint an existing dataset so its name frees for new data —
// the "Replace" collision choice. Data-safe: the old data + every prior
// campaign's results are preserved under `{slug}-vN` (never overwritten); the
// freed name is then re-ingested via `postIngestDataset(file, slug)`. Wire
// contract: `docs/specs/m12-api-openapi.yaml::replaceDataset`.
// A bare acknowledgement — the archival name and the repointed/re-stamped counts are
// recorded by the migration itself (log + on-disk marker); no client reads them back.
export interface ReplaceDatasetResponse {
  slug: string;
}
export interface DraftPatch {
  slug?: string;
  connector?: string;
  scoring_composite?: string;
  raw_task_description?: string;
  pipeline_overlay?: Record<string, unknown>;
  // The active pipeline step list — the setup-panel mode toggle writes it
  // (["llm_only"] vs the full cache_lookup→…→token_matching).
  pipeline_steps?: string[];
  // Confirm the input/target column mapping. Each must be a member of the
  // draft's `headers` (server rejects with 422 otherwise); setting one flips
  // `field_provenance["column.query|ground_truth"]` to `confirmed`.
  column_query?: string;
  column_ground_truth?: string;
  // Replace the origin prompt wholesale (PromptTemplate field shape). The
  // editor sends the full object, not a sparse field patch.
  origin_prompt_fields?: Record<string, unknown>;
  // The campaign-config knobs (max_rounds / mechanisms). Sent keys are
  // shallow-merged onto the draft's current overrides server-side — send one
  // knob or several; a nested `mechanisms` replaces wholesale.
  optimization_overrides?: Partial<OptimizationOverridesWire>;
  // The origin's sanctioned model allow-list. Replaces the draft's set wholesale
  // (the checklist sends the full ticked list); [] clears it (restrictive default).
  allowed_models?: string[];
}
export interface StartCheckinResponse {
  campaign_id: string;
  cycle_id: string;
  job_id: string;
}
// Re-open a durable check-in campaign from the sidebar — its draft wire + the
// last resolver turn. Wire: `GET /campaigns/{id}/checkin`.
export interface CheckinReopenResponse {
  draft: DraftCampaignWire;
  resolution: OriginLastResolution | null;
  raised: RaisedCommand[];
}
// One operator-facing question on a `kind='ask'` turn. `field` names the
// checklist field the answer resolves so the panel applies it directly as a
// confirmed patch; `options` (when non-empty) is a closed answer set rendered
// as a picker, else the input is free text.
export interface OriginQuestion {
  field: string;
  prompt: string;
  options: string[];
}
// The resolver turn's own output, persisted to the draft `cache.json` and
// echoed on the `resolve-origin` response. Drives the check-in panel's
// assessment line, operator questions, and the ready-turn recap.
// The turn's findings are not mirrored here — they ride `raised` as clickable commands.
export interface OriginLastResolution {
  assessment: string;
  next_action: { kind: string; questions: OriginQuestion[] };
  recap: string;
}
// Set only when the resolver turn degraded — the check-in model returned an
// empty/truncated response that forced a paid repair retry, or produced nothing
// usable. Stamped on the block by `resolve_origin_turn` (a `critical` turn raises
// instead → 502). Drives the check-in panel's degradation warning + re-run.
export interface OriginDegraded {
  grade: "degraded" | "critical";
  reasons: string[];
}
// One proposal the resolver left for the operator, already shaped as the command
// a click would fire. The assistant offers; it never triggers. Derived server-side
// from the turn's findings, so the model never names a command and every payload
// is guaranteed to validate. Everything here awaits a click: a high-confidence finding
// is auto-confirmed inside the turn and never raised, so `confidence` stays server-side.
export interface RaisedCommand {
  kind: "edit-draft-campaign";
  payload: { draft_id: string; patch: DraftPatch };
  evidence: string;
}
export interface OriginResolutionBlock {
  complete: boolean;
  provenance: Record<string, string>;
  values: Record<string, unknown>;
  gaps: OriginGap[];
  last_resolution?: OriginLastResolution;
  raised?: RaisedCommand[];
  degraded?: OriginDegraded;
}
export interface ResolveOriginResponse {
  resolution: OriginResolutionBlock;
  draft: DraftCampaignWire;
}
