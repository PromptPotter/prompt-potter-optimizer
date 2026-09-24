// The draft-campaign wire shapes check-in and origin resolution exchange. The single owner of
// `lib/`'s one import from `components/`; that edge is tolerated here and nowhere else.

import type {
  ModelCapability,
  NodeConfigParam,
  NodeOutputSchema,
  NodeReach,
} from "./types";
import type { PipelineView } from "@/components/workflow";

// Must match `domain/origin_provenance.Provenance`. Nothing reaches mint until `confirmed`.
export type ProvenanceTag = "unset" | "proposed" | "confirmed";
export interface PipelineDependencyWire {
  kind: string;
  node: string;
  title: string;
  hint: string;
  fulfilled: boolean;
}
// Materialized into the committed `campaign.json::optimization` block.
export interface OptimizationOverridesWire {
  max_rounds: number;
  prompt_block_catalogue: "guidance" | "restrict" | "off";
  // `l1` = no escalation at all, the L1-only ablation arm.
  escalation_ladder: "full" | "l1_l2" | "l1";
  mechanisms: Record<string, Record<string, boolean>>;
}
export interface DraftCampaignWire {
  draft_id: string;
  slug: string;
  // Keyed by the RAW upload headers, not projected through the column mapping (which is "" until
  // confirmed): render against `headers`.
  sample_preview: Array<Record<string, string>>;
  n_samples: number;
  connector: string;
  scoring_composite: string;
  optimization_overrides: OptimizationOverridesWire;
  raw_task_description: string;
  pipeline_overlay: Record<string, unknown>;
  headers: string[];
  column_query: string;
  column_ground_truth: string;
  field_provenance: Record<string, ProvenanceTag>;
  // `PromptTemplate.prompt_field_dict()` shape; `{}` until the check-in fills it.
  origin_prompt_fields: Record<string, unknown>;
  candidate_library_size: number;
  created_at: string;
  updated_at: string;
  // Who may move a node's axes is `node_config_schema`'s answer alone, never derived from this.
  active_steps: string[];
  // The shapes `/campaigns/{id}/pipeline` serves, computed from the draft: a pre-commit check-in
  // has no `datasets/{slug}/` dir, so never fetch its pipeline by slug.
  pipeline_view: PipelineView | null;
  node_config_schema: Record<string, NodeConfigParam[]>;
  node_output_schema: Record<string, NodeOutputSchema | null>;
  reach: Record<string, NodeReach>;
  // Served, not counted: the browser sees only DECLARED nodes, and a check-in declares them all.
  is_single_node: boolean;
  // `unreachable` = the backend probe failed: nothing in the schema is a lock anyone set, so an
  // editor must draw no padlock off it.
  schema_source: "backend" | "local" | "unreachable";
  // `reasoning_efforts: null` is UNKNOWN and must never render as unsupported: shown as "no", it
  // deletes a real search axis.
  model_capabilities: Record<string, ModelCapability>;
  dependencies: PipelineDependencyWire[];
  // The server's mint gate (`origin_readiness`); the client never re-derives it.
  readiness: { complete: boolean; gaps: OriginGap[] };
}
// Also the `422 origin_incomplete` body's `details.gaps`.
export interface OriginGap {
  field: string;
  reason: string;
  hint: string;
}
// The old data survives under `{slug}-vN`; the migration logs its own counts, so the ack is bare.
export interface ReplaceDatasetResponse {
  slug: string;
}
export interface DraftPatch {
  slug?: string;
  connector?: string;
  scoring_composite?: string;
  raw_task_description?: string;
  pipeline_overlay?: Record<string, unknown>;
  pipeline_steps?: string[];
  // Each must be a member of `headers` (else 422); setting one flips its provenance to `confirmed`.
  column_query?: string;
  column_ground_truth?: string;
  // Replaces wholesale, not a sparse field patch.
  origin_prompt_fields?: Record<string, unknown>;
  // Shallow-merged server-side, so a nested `mechanisms` replaces wholesale.
  optimization_overrides?: Partial<OptimizationOverridesWire>;
}
export interface StartCheckinResponse {
  campaign_id: string;
  cycle_id: string;
  job_id: string;
}
export interface CheckinReopenResponse {
  draft: DraftCampaignWire;
  resolution: OriginLastResolution | null;
  raised: RaisedCommand[];
}
// Empty `options` = a free-text answer.
export interface OriginQuestion {
  field: string;
  prompt: string;
  options: string[];
}
// The turn's findings are not here: they ride `raised` as clickable commands.
export interface OriginLastResolution {
  assessment: string;
  next_action: { kind: string; questions: OriginQuestion[] };
  recap: string;
}
// Already the command a click fires; the assistant offers, never triggers. A high-confidence
// finding is auto-confirmed inside the turn and never raised.
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
  // Set after a paid repair retry (`origin_resolve.py::_degraded_cause`); a turn with nothing
  // usable is a 502, never this.
  degraded_cause?: string;
}
export interface ResolveOriginResponse {
  resolution: OriginResolutionBlock;
  draft: DraftCampaignWire;
}
