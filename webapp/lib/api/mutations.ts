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
import type { CommandAcceptedBody } from "./types";

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

export async function postCreateFork(
  campaignId: string,
  cycleId: string,
  round: number,
  candidateId: string,
): Promise<CommandAcceptedBody> {
  return _postCommand("fork-cycle", {
    campaign_id: campaignId,
    cycle_id: cycleId,
    round,
    candidate_id: candidateId,
  });
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

export interface DraftCampaignWire {
  draft_id: string;
  slug: string;
  sample_preview: Array<{ query: string; ground_truth: string }>;
  n_samples: number;
  connector: string;
  scoring_composite: string;
  max_rounds: number;
  task_description: string;
  pipeline_overlay: Record<string, unknown>;
  optimizer_provider: string;
  optimizer_model: string;
  created_at: string;
  updated_at: string;
}

export interface IngestErrorDetail {
  reason?: string;
  suggested_slug?: string;
  backend_type?: string;
  backend_url?: string;
  draft_id?: string;
}

export class IngestApiError extends Error {
  readonly status: number;
  readonly errorCode?: string;
  readonly reason?: string;
  readonly suggestedSlug?: string;
  readonly backendType?: string;
  readonly backendUrl?: string;
  readonly draftId?: string;
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
    this.suggestedSlug = detail?.suggested_slug;
    this.backendType = detail?.backend_type;
    this.backendUrl = detail?.backend_url;
    this.draftId = detail?.draft_id;
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

export interface DraftPatch {
  slug?: string;
  connector?: string;
  scoring_composite?: string;
  max_rounds?: number;
  task_description?: string;
  pipeline_overlay?: Record<string, unknown>;
  optimizer_provider?: string;
  optimizer_model?: string;
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

// Security pane sign-out. Not a command-highway POST (logout is
// auth-router-owned); writes the session-cookie clear via the server-side
// session store. On 200 the caller hard-redirects to /ui/login.
export async function postLogout(): Promise<void> {
  const r = await fetch(`${API}/auth/logout`, {
    method: "POST",
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`${r.status} POST /auth/logout`);
}
