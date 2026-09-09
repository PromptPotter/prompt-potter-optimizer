// The DRAFT-CAMPAIGN wire shapes — what check-in and origin resolution exchange, with no fetch
// among them. Pure types, so nothing here can be called; `ingest.ts` is where they are used.
//
// This module is also the single owner of `lib/`'s one import from `components/`
// (`PipelineView`). That edge points the wrong way and is tolerated in exactly one file so a
// reader can see the whole of it at once rather than finding it mid-request-helper.

import type {
  ModelCapability,
  NodeConfigParam,
  NodeOutputSchema,
  NodeReach,
} from "./types";
import type { PipelineView } from "@/components/workflow";

// M13 chat-first dataset ingest: upload + mint a durable `checkin` campaign (`draft_id` IS the
// `campaign_id`), sparse-patch the draft, then gate + commit + spawn. Wire contract pinned in
// `docs/specs/api-openapi.yaml`.

// One uploaded column header's provenance tag — mirrors the server's
// `domain/origin_provenance.Provenance` StrEnum. `unset` = no value yet,
// `proposed` = an inference awaiting confirmation, `confirmed` =
// operator-stated or auto-confirmed. No field reaches mint while `unset`
// or `proposed` (the deterministic `origin_readiness` gate).
export type ProvenanceTag = "unset" | "proposed" | "confirmed";
// A categorical input the draft's pipeline requires beyond (pipeline + dataset + origin),
// derived server-side from the node types. The ingest UI gives each unfulfilled one a drop-zone
// so the operator supplies it in place.
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
  // First 10 parsed rows, keyed by the RAW upload headers — NOT projected through
  // `column_query`/`column_ground_truth`, which are "" until the operator confirms them.
  // Render against `headers`; the mapping decorates the columns, it does not select them.
  sample_preview: Array<Record<string, string>>;
  n_samples: number;
  connector: string;
  scoring_composite: string;
  // The campaign-config knobs (round ceiling, model lock, mechanism toggles).
  optimization_overrides: OptimizationOverridesWire;
  raw_task_description: string;
  pipeline_overlay: Record<string, unknown>;
  // Header-agnostic ingest: the uploaded columns in order, the operator-resolved input/target
  // mapping, and per-field provenance keyed by dotted field name. The mint gate blocks until both
  // columns are `confirmed` and members of `headers`; config is not gated.
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
  created_at: string;
  updated_at: string;
  // The pipeline this draft actually runs — its own choice (preserved on reuse) over the
  // connector default. What a node's axes are and who may move them is `node_config_schema`
  // below and nowhere else; a second permission projection beside it could only disagree.
  active_steps: string[];
  // The draft's parsed pipeline render — graph `view` + per-node config/output
  // schema + the reach summed over those rows, the SAME shapes
  // `GET /campaigns/{id}/pipeline` serves for a committed campaign, but computed from the
  // draft (a pre-commit check-in has no `datasets/{slug}/` dir). The ingest node editor
  // renders from these directly, so it never fetches by slug (which would 404 and hang on
  // "Loading node…").
  pipeline_view: PipelineView | null;
  node_config_schema: Record<string, NodeConfigParam[]>;
  node_output_schema: Record<string, NodeOutputSchema | null>;
  reach: Record<string, NodeReach>;
  // Whether the ACTIVE chain is one node. Served for the same reason `reach` is: the browser can
  // only count DECLARED nodes, and a check-in declares its connector's whole pipeline.
  is_single_node: boolean;
  // WHERE the schema above came from, because an empty axis set has two very different
  // causes. `backend` = the service's own declaration was read at check-in, so
  // `movable_by` is authoritative. `local` = an in-process connector, whose manifest IS
  // the declaration. `unreachable` = the probe failed, and NOTHING here can be read as a
  // lock the operator set — an editor drawing padlocks off this state is asserting a
  // permission nobody chose.
  schema_source: "backend" | "local" | "unreachable";
  // What each model on the menu ACCEPTS and costs, keyed by model id — resolved server-side
  // through the operator's hand-authored override, then the per-tenant provider snapshot.
  // Keyed by model, not folded into the `reasoning_effort` row, so switching models re-answers
  // the ladder with no round-trip. `reasoning_efforts: null` is UNKNOWN and must never render
  // as unsupported: an absent answer shown as "no" silently deletes a real search axis.
  model_capabilities: Record<string, ModelCapability>;
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
// Version-and-repoint a dataset so its name frees for new data — the "Replace" collision choice.
// Data-safe: the old data and every prior campaign's results are preserved under `{slug}-vN`,
// never overwritten. Wire contract: `docs/specs/api-openapi.yaml::replaceDataset`.
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
  // Why the resolver turn came back thin — a paid repair retry after an empty or
  // truncated first response (`origin_resolve.py::_degraded_cause`). A cause and no
  // grade: a turn producing nothing usable raises → 502, so it never reaches here.
  degraded_cause?: string;
}
export interface ResolveOriginResponse {
  resolution: OriginResolutionBlock;
  draft: DraftCampaignWire;
}
