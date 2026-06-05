// Mutating endpoints — the operator's write surface. All four endpoints
// post to the closed-set command highway at `/commands/{kind}`, declared in
// `docs/specs/m12-api-openapi.yaml` and dispatched server-side by
// `CommandDispatcher` (`promptpotter/presentation/api/middleware/`). The
// dispatcher writes a `CommandRecord` to the target cycle's ledger,
// inline-applies the mutation, then writes a `CommandAckRecord`.
//
// These are pure I/O — they do NOT trigger poll revalidation themselves.
// The caller bumps `lib/revalidate.ts` after a mutation resolves so the
// workspace poll picks the change up immediately instead of on its next
// tick. The 202 body is generic (`CommandAcceptedBody` per the OpenAPI
// schema); detailed apply results (new fork id, cleanup count) flow via
// the workspace poll picking up the new on-disk state.

import { API } from "./client";
import type { UserSettings } from "./reads";
import type { CommandAcceptedBody } from "./types";

// Account → Preferences write. A user-account mutation (not a campaign
// command), so it PATCHes the auth router directly rather than `/commands`.
export async function patchUserSettings(settings: UserSettings): Promise<UserSettings> {
  const r = await fetch(`${API}/auth/user-settings`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`user-settings PATCH failed (${r.status})`);
  return (await r.json()) as UserSettings;
}

function _mintIdempotencyKey(): string {
  // crypto.randomUUID is in every browser Next.js 16 supports + Node 18+.
  return crypto.randomUUID();
}

async function _postCommand(
  kind: string,
  payload: Record<string, unknown>,
): Promise<CommandAcceptedBody> {
  const r = await fetch(`${API}/commands/${kind}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": _mintIdempotencyKey(),
    },
    body: JSON.stringify({ kind, payload }),
    cache: "no-store",
  });
  if (!r.ok) await _throwApiError(r);
  return (await r.json()) as CommandAcceptedBody;
}

// Run-limit knobs the fork reconcile dialog re-sets — twin of the OpenAPI
// `LimitOverrides`. Every field optional; absent inherits the parent. Values
// are ABSOLUTE for the fork (the dialog defaults rounds/spend to the parent's
// remaining, then the operator confirms the fork's own ceiling).
export interface LimitOverrides {
  max_rounds?: number;
  spend_budget_usd?: number | null;
  l1_patience?: number;
  l2_patience?: number | null;
  l3_patience?: number | null;
  pobb_epsilon?: number;
}

// The edited-searchpoint origin override — twin of the OpenAPI `ForkSeed`.
// Required on every operator fork (all are `operator_steered`). `starting_prompt`
// is the PromptTemplate field shape; `pipeline_overlay` is the candidate's
// `nodes.*.config` delta, carried verbatim and merged onto the dataset overlay
// at fork bootstrap.
export interface ForkSeed {
  starting_prompt?: Record<string, unknown>;
  pipeline_overlay?: Record<string, unknown>;
  limit_overrides?: LimitOverrides;
}

// Mint an `operator_steered` fork rooted at the selected searchpoint, carrying
// the operator's edits + reconciled limits. The single fork write path.
export async function postForkCycle(
  campaignId: string,
  cycleId: string,
  round: number,
  candidateId: string,
  opts: { seed: ForkSeed; steeredBy?: string },
): Promise<CommandAcceptedBody> {
  const payload: Record<string, unknown> = {
    campaign_id: campaignId,
    cycle_id: cycleId,
    round,
    candidate_id: candidateId,
    seed: opts.seed,
  };
  if (opts.steeredBy) payload.steered_by = opts.steeredBy;
  return _postCommand("fork-cycle", payload);
}

export async function postCleanupEmpty(
  campaignId: string,
  cycleId: string,
): Promise<CommandAcceptedBody> {
  return _postCommand("cleanup-empty-cycles", {
    campaign_id: campaignId,
    cycle_id: cycleId,
  });
}

export async function postStopCycle(
  campaignId: string,
  cycleId: string,
): Promise<CommandAcceptedBody> {
  return _postCommand("stop-cycle", {
    campaign_id: campaignId,
    cycle_id: cycleId,
  });
}

export async function postDeleteCycle(
  campaignId: string,
  cycleId: string,
): Promise<CommandAcceptedBody> {
  return _postCommand("delete-cycle", {
    campaign_id: campaignId,
    cycle_id: cycleId,
  });
}

// Campaign lifecycle — soft-marks `lifecycle_status` on the campaign
// manifest. Measurements survive (cross-campaign cache-hits keep working).
// Deletion is never physical at this site; `try_delete_stub_cycle` stays
// the only physical-delete path and only applies to empty stub cycles.

export async function postArchiveCampaign(
  campaignId: string,
  reason?: string,
): Promise<CommandAcceptedBody> {
  const payload: Record<string, unknown> = { campaign_id: campaignId };
  if (reason) payload.reason = reason;
  return _postCommand("archive-campaign", payload);
}

export async function postUnarchiveCampaign(
  campaignId: string,
): Promise<CommandAcceptedBody> {
  return _postCommand("unarchive-campaign", { campaign_id: campaignId });
}

export async function postDeleteCampaign(
  campaignId: string,
  reason?: string,
): Promise<CommandAcceptedBody> {
  const payload: Record<string, unknown> = { campaign_id: campaignId };
  if (reason) payload.reason = reason;
  return _postCommand("delete-campaign", payload);
}

// Mint a fresh campaign + spawn the runner in one command. The 202 returns
// once the campaign is on disk; background progress surfaces via the
// canonical ledger + dashboard.json poll.

export async function postMintCampaign(
  datasetName: string,
  opts: { haltAtAccuracy?: number; spendBudgetUsd?: number } = {},
): Promise<CommandAcceptedBody> {
  const payload: Record<string, unknown> = { dataset_name: datasetName };
  if (opts.haltAtAccuracy !== undefined) {
    payload.halt_at_accuracy = opts.haltAtAccuracy;
  }
  if (opts.spendBudgetUsd !== undefined) {
    payload.spend_budget_usd = opts.spendBudgetUsd;
  }
  return _postCommand("mint-campaign", payload);
}

// Pause / resume a running cycle. Pause writes `.runtime/pause.flag`; the
// loop's `pause_check` blocks at the next round boundary while it's present.
// Resume removes the flag; the wait-loop exits on its next tick. Both
// idempotent. Cycle-scoped per `m12-api-openapi.yaml::pauseCycle/resumeCycle`.
// Pause-state is read back from `GET /api/v1/sessions/active/live-state::is_paused`.
export async function postPauseCycle(
  campaignId: string,
  cycleId: string,
): Promise<CommandAcceptedBody> {
  return _postCommand("pause-cycle", { campaign_id: campaignId, cycle_id: cycleId });
}

export async function postResumeCycle(
  campaignId: string,
  cycleId: string,
): Promise<CommandAcceptedBody> {
  return _postCommand("resume-cycle", { campaign_id: campaignId, cycle_id: cycleId });
}

// Raise or lower a running cycle's USD spend cap mid-flight. Writes
// `.runtime/spend_cap.json`; the round loop's `spend_cap_probe` re-reads it
// every clean round — `0` halts at the next round boundary, raising above
// current spend releases the halt. Cycle-scoped command per
// `m12-api-openapi.yaml::changeSpendBudget`.
export async function postChangeSpendBudget(
  campaignId: string,
  cycleId: string,
  maxUsd: number,
): Promise<CommandAcceptedBody> {
  return _postCommand("change-spend-budget", {
    campaign_id: campaignId,
    cycle_id: cycleId,
    max_usd: maxUsd,
  });
}

export async function postStartRun(
  campaignId: string,
  cycleId: string,
  kind: "new" | "resume",
  opts: { haltAtAccuracy?: number; spendBudgetUsd?: number } = {},
): Promise<CommandAcceptedBody> {
  const payload: Record<string, unknown> = {
    campaign_id: campaignId,
    cycle_id: cycleId,
    kind,
  };
  if (opts.haltAtAccuracy !== undefined) {
    payload.halt_at_accuracy = opts.haltAtAccuracy;
  }
  if (opts.spendBudgetUsd !== undefined) {
    payload.spend_budget_usd = opts.spendBudgetUsd;
  }
  return _postCommand("start-run", payload);
}

// M13 chat-first dataset ingest. `postIngestDataset` uploads a CSV +
// returns the server-held `DraftCampaign`; `postEditDraftCampaign` sparse-
// patches the draft (both the chat assistant tool-call and the panel
// "Apply" button ride this); `postMintCampaignFromDraft` commits the
// draft to disk + spawns the runner. Wire contract pinned in
// `docs/specs/m12-api-openapi.yaml` (`POST /datasets/ingest`,
// `POST /commands/edit-draft-campaign`, `POST /commands/mint-campaign-from-draft`).

// One uploaded column header's provenance tag — mirrors the server's
// `domain/origin_provenance.Provenance` StrEnum. `unset` = no value yet,
// `proposed` = an inference awaiting confirmation, `confirmed` =
// operator-stated or auto-confirmed. No field reaches mint while `unset`
// or `proposed` (the deterministic `origin_readiness` gate).
export type ProvenanceTag = "unset" | "proposed" | "confirmed";

// Mirror of `domain/origin_provenance.ProvenanceSource`. Orthogonal to the
// provenance tag: `auto` = machine-set (template default, column auto-detect,
// or resolver finding); `stated` = the operator set it via edit-draft-campaign.
// Only valued fields carry a source. Audit-only — the gate reads `resolved`.
export type ProvenanceSource = "auto" | "stated";

// The backend-pipeline permission surface the new-campaign UI renders before
// commit. A draft's `pipeline_overlay` is empty until commit, so the connector
// node-config seed (TermNorm's reasoning clamp) is otherwise invisible — this
// block carries it so the UI can show the optimizer is *locked out* of certain
// params (model/provider campaign-wide; thinking above the connector floor),
// not merely that a value is the default. Server-derived per request.
export interface OptimizerLocks {
  // Connector default pipeline step list (e.g. `["llm_only"]`).
  pipeline: string[];
  // Params the optimizer may never permute on any node under
  // `forbidden_axes_strict` (`["model","provider"]`); empty when strict is off.
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

export interface DraftCampaignWire {
  draft_id: string;
  slug: string;
  sample_preview: Array<{ query: string; ground_truth: string }>;
  n_samples: number;
  // True iff pre-filled from an existing on-disk dataset (a demo/benchmark
  // pick) vs. a fresh upload. Drives the wizard's "pre-filled, AI check-in
  // not needed" branch — not the global `demo_mode_enabled` preference.
  derived_from_dataset: boolean;
  connector: string;
  scoring_composite: string;
  max_rounds: number;
  task_description: string;
  pipeline_overlay: Record<string, unknown>;
  optimizer_provider: string;
  optimizer_model: string;
  // Header-agnostic ingest (A3 origin-resolution gate). `headers` are the
  // uploaded file's columns in order; `column_query` / `column_ground_truth`
  // are the operator-resolved input/target mapping (empty until picked);
  // `resolved` carries per-field provenance keyed by dotted field name
  // (`column.query`, `column.ground_truth`, …). The mint gate blocks until
  // both columns are `confirmed` and members of `headers`.
  headers: string[];
  column_query: string;
  column_ground_truth: string;
  resolved: Record<string, ProvenanceTag>;
  // Per-field source (`auto` / `stated`), orthogonal to `resolved`. Only
  // valued fields appear; audit-only, never gates. See `ProvenanceSource`.
  sources: Record<string, ProvenanceSource>;
  // The campaign's starting prompt — `PromptTemplate.prompt_field_dict()` shape
  // (the six string fields + optional `few_shot_examples`). Seeded by the
  // check-in decomposition or an authored dataset's prompt; operator-editable
  // before commit. Empty `{}` until the check-in fills it.
  starting_prompt: Record<string, unknown>;
  // Whether the optimizer is barred from changing model/provider campaign-wide
  // (the `forbidden_axes_strict` knob). Default `true` (locked). Toggled in the
  // pipeline-config control panel; drives `optimizer_locks.forbidden_axes`.
  lock_model: boolean;
  created_at: string;
  updated_at: string;
  // Connector-derived backend-pipeline permission surface; see `OptimizerLocks`.
  optimizer_locks: OptimizerLocks;
}

// One origin field still blocking mint, as returned by the server's
// `origin_readiness` checklist (the `422 origin_incomplete` `details.gaps`
// array, and re-derived client-side from the draft wire to drive the panel).
export interface OriginGap {
  field: string;
  reason: string;
  hint: string;
}

export interface IngestErrorDetail {
  reason?: string;
  // On a `slug_collision` (409): the colliding dataset name + a free suggestion.
  // The chat offers "use existing {slug}" / "save as new {suggested_slug}".
  slug?: string;
  suggested_slug?: string;
  backend_type?: string;
  backend_url?: string;
  draft_id?: string;
  gaps?: OriginGap[];
}

export class IngestApiError extends Error {
  readonly status: number;
  readonly errorCode?: string;
  readonly reason?: string;
  // `slug_collision` (409): the existing dataset name + a free suggestion.
  readonly existingSlug?: string;
  readonly suggestedSlug?: string;
  readonly backendType?: string;
  readonly backendUrl?: string;
  readonly draftId?: string;
  // Populated on `origin_incomplete` (422) — the deterministic checklist's
  // still-open fields. Consumers surface these inline rather than collapse
  // them into the single `message` line.
  readonly gaps?: OriginGap[];
  constructor(
    status: number,
    message: string,
    errorCode?: string,
    detail?: IngestErrorDetail,
  ) {
    super(message);
    this.status = status;
    this.errorCode = errorCode;
    this.reason = detail?.reason;
    this.existingSlug = detail?.slug;
    this.suggestedSlug = detail?.suggested_slug;
    this.backendType = detail?.backend_type;
    this.backendUrl = detail?.backend_url;
    this.draftId = detail?.draft_id;
    this.gaps = detail?.gaps;
  }

  // Operator-facing error message. Every consumer that catches an
  // IngestApiError should render via this method instead of reimplementing
  // the per-error-code translation — the silent-failure gap in
  // BenchmarkList was downstream of that drift. Static `from` helper
  // wraps unknown errors so call sites are one line:
  //   setError(IngestApiError.toOperatorMessage(e))
  toOperatorMessage(): string {
    if (this.errorCode === "backend_unreachable") {
      const where = this.backendUrl ? ` at ${this.backendUrl}` : "";
      const what = this.backendType ? ` ‘${this.backendType}’` : "";
      return `Backend${what}${where} is not running. Start the backend and try again.`;
    }
    if (this.suggestedSlug) {
      return `${this.message} Suggested slug: ${this.suggestedSlug}.`;
    }
    return this.message;
  }

  // One-liner for ``catch`` blocks. Renders unknown errors via their
  // standard message; ``IngestApiError`` instances route through
  // ``.toOperatorMessage()``.
  static toOperatorMessage(e: unknown): string {
    if (e instanceof IngestApiError) return e.toOperatorMessage();
    return e instanceof Error ? e.message : String(e);
  }
}

async function _throwApiError(r: Response): Promise<never> {
  let message = `${r.status} ${r.statusText}`;
  let errorCode: string | undefined;
  let detail: IngestErrorDetail | undefined;
  try {
    const body = (await r.json()) as {
      detail?:
        | string
        | { error?: string; message?: string; details?: IngestErrorDetail };
    };
    if (typeof body?.detail === "string") {
      message = body.detail;
    } else if (body?.detail?.message) {
      message = body.detail.message;
      errorCode = body.detail.error;
      detail = body.detail.details;
    }
  } catch {
    /* status-only message */
  }
  throw new IngestApiError(r.status, message, errorCode, detail);
}

export async function postIngestDataset(
  file: File,
  slug?: string,
): Promise<DraftCampaignWire> {
  const form = new FormData();
  form.append("file", file);
  if (slug) form.append("slug", slug);
  const r = await fetch(`${API}/datasets/ingest`, {
    method: "POST",
    body: form,
    cache: "no-store",
  });
  if (!r.ok) await _throwApiError(r);
  return (await r.json()) as DraftCampaignWire;
}

// Open an existing dataset (demo / benchmark / owned Origin) in the setup
// wizard: the server builds a fully-prefilled DraftCampaign straight from the
// dataset's files — no browser-side CSV reconstruction, and the dataset's
// pipeline node config (backend model/provider) is preserved through commit.
// Like `postIngestDataset`, no campaign exists until the operator commits the
// returned draft via `postMintCampaignFromDraft`.
export async function postDraftFromDataset(name: string): Promise<DraftCampaignWire> {
  const r = await fetch(`${API}/datasets/${encodeURIComponent(name)}/draft`, {
    method: "POST",
    cache: "no-store",
  });
  if (!r.ok) await _throwApiError(r);
  return (await r.json()) as DraftCampaignWire;
}

// Version-and-repoint an existing dataset so its name frees for new data —
// the "Replace" collision choice. Data-safe: the old data + every prior
// campaign's results are preserved under `{slug}-vN` (never overwritten); the
// freed name is then re-ingested via `postIngestDataset(file, slug)`. Wire
// contract: `docs/specs/m12-api-openapi.yaml::replaceDataset`.
export interface ReplaceDatasetResponse {
  slug: string;
  versioned_to: string;
  repointed_campaigns: number;
  restamped_measurements: number;
}

export async function postReplaceDataset(slug: string): Promise<ReplaceDatasetResponse> {
  const r = await fetch(`${API}/commands/replace-dataset`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": _mintIdempotencyKey(),
    },
    body: JSON.stringify({ kind: "replace-dataset", payload: { slug } }),
    cache: "no-store",
  });
  if (!r.ok) await _throwApiError(r);
  return (await r.json()) as ReplaceDatasetResponse;
}

export interface DraftPatch {
  slug?: string;
  connector?: string;
  scoring_composite?: string;
  max_rounds?: number;
  task_description?: string;
  pipeline_overlay?: Record<string, unknown>;
  optimizer_provider?: string;
  optimizer_model?: string;
  // Confirm the input/target column mapping. Each must be a member of the
  // draft's `headers` (server rejects with 422 otherwise); setting one flips
  // `resolved["column.query|ground_truth"]` to `confirmed`.
  column_query?: string;
  column_ground_truth?: string;
  // Replace the starting prompt wholesale (PromptTemplate field shape). The
  // editor sends the full object, not a sparse field patch.
  starting_prompt?: Record<string, unknown>;
  // Toggle the campaign-wide model/provider lock (forbidden_axes_strict).
  lock_model?: boolean;
}

export async function postEditDraftCampaign(
  draftId: string,
  patch: DraftPatch,
): Promise<DraftCampaignWire> {
  const r = await fetch(`${API}/commands/edit-draft-campaign`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": _mintIdempotencyKey(),
    },
    body: JSON.stringify({
      kind: "edit-draft-campaign",
      payload: { draft_id: draftId, patch },
    }),
    cache: "no-store",
  });
  if (!r.ok) await _throwApiError(r);
  return (await r.json()) as DraftCampaignWire;
}

export interface MintFromDraftResponse {
  campaign_id: string;
  cycle_id: string;
  job_id: string;
}

export async function postMintCampaignFromDraft(
  draftId: string,
): Promise<MintFromDraftResponse> {
  const r = await fetch(`${API}/commands/mint-campaign-from-draft`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": _mintIdempotencyKey(),
    },
    body: JSON.stringify({
      kind: "mint-campaign-from-draft",
      payload: { draft_id: draftId },
    }),
    cache: "no-store",
  });
  if (!r.ok) await _throwApiError(r);
  return (await r.json()) as MintFromDraftResponse;
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
export interface OriginLastResolution {
  assessment: string;
  findings: Array<{
    field: string;
    proposed_value: string;
    confidence: string;
    evidence: string;
  }>;
  next_action: { kind: string; questions: OriginQuestion[] };
  recap: string;
}

export interface OriginResolutionBlock {
  complete: boolean;
  provenance: Record<string, string>;
  values: Record<string, unknown>;
  gaps: OriginGap[];
  last_resolution?: OriginLastResolution;
}

export interface ResolveOriginResponse {
  resolution: OriginResolutionBlock;
  draft: DraftCampaignWire;
}

// One origin-resolver turn: the origin-aware `checkin` node proposes
// evidence-cited values for the unresolved closed-set fields (high-confidence
// auto-confirms; low-confidence lands `proposed`). Synchronous, like
// `edit-draft-campaign`. Returns the resolver output + the post-apply draft.
export async function postResolveOrigin(draftId: string): Promise<ResolveOriginResponse> {
  const r = await fetch(`${API}/commands/resolve-origin`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": _mintIdempotencyKey(),
    },
    body: JSON.stringify({
      kind: "resolve-origin",
      payload: { draft_id: draftId },
    }),
    cache: "no-store",
  });
  if (!r.ok) await _throwApiError(r);
  return (await r.json()) as ResolveOriginResponse;
}

// Security pane sign-out. Not a command-highway POST (logout is
// auth-router-owned); writes the session-cookie clear via the server-side
// session store. On 200 the caller hard-redirects to /login.
export async function postLogout(): Promise<void> {
  const r = await fetch(`${API}/auth/logout`, {
    method: "POST",
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`${r.status} POST /auth/logout`);
}
