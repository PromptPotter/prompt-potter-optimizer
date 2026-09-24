// Ingest, check-in and origin resolution: the writes that CREATE what a command later addresses.

import { API } from "./client";
import { mintIdempotencyKey, throwApiError } from "./errors";
import { postCommand } from "./commands";
import type {
  CheckinReopenResponse,
  DraftCampaignWire,
  DraftPatch,
  ReplaceDatasetResponse,
  ResolveOriginResponse,
  StartCheckinResponse,
} from "./draft-types";
import type { StartCheckinPayload } from "./types.generated";

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
  if (!r.ok) await throwApiError(r);
  return (await r.json()) as DraftCampaignWire;
}
export async function postUploadCandidateLibrary(
  draftId: string,
  file: File,
): Promise<DraftCampaignWire> {
  const form = new FormData();
  form.append("file", file);
  form.append("draft_id", draftId);
  const r = await fetch(`${API}/datasets/draft/candidate-library`, {
    method: "POST",
    headers: { "Idempotency-Key": mintIdempotencyKey() },
    body: form,
    cache: "no-store",
  });
  if (!r.ok) await throwApiError(r);
  return (await r.json()) as DraftCampaignWire;
}
export async function postBuildCandidateLibraryFromColumn(
  draftId: string,
  column: string,
): Promise<DraftCampaignWire> {
  const r = await fetch(`${API}/datasets/draft/candidate-library/from-column`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": mintIdempotencyKey(),
    },
    body: JSON.stringify({ draft_id: draftId, column }),
    cache: "no-store",
  });
  if (!r.ok) await throwApiError(r);
  return (await r.json()) as DraftCampaignWire;
}
// Mints a durable `checkin` campaign from the dataset's CURRENT committed config; nothing runs
// until `postStartCheckin`.
export async function postDraftFromDataset(name: string): Promise<DraftCampaignWire> {
  const r = await fetch(`${API}/datasets/${encodeURIComponent(name)}/draft`, {
    method: "POST",
    cache: "no-store",
  });
  if (!r.ok) await throwApiError(r);
  return (await r.json()) as DraftCampaignWire;
}
// Unlike `postDraftFromDataset`, reproduces the origin's prompt verbatim and commits with
// `campaign_origin` lineage.
export async function postDraftFromOrigin(originId: string): Promise<DraftCampaignWire> {
  const r = await fetch(`${API}/origins/${encodeURIComponent(originId)}/draft`, {
    method: "POST",
    cache: "no-store",
  });
  if (!r.ok) await throwApiError(r);
  return (await r.json()) as DraftCampaignWire;
}
export async function postReplaceDataset(slug: string): Promise<ReplaceDatasetResponse> {
  return postCommand<ReplaceDatasetResponse>("replace-dataset", { slug });
}
export async function postEditDraftCampaign(
  draftId: string,
  patch: DraftPatch,
): Promise<DraftCampaignWire> {
  return postCommand<DraftCampaignWire>("edit-draft-campaign", {
    draft_id: draftId,
    patch,
  });
}
// Not a draft field: the draft survives a reopen, while a ceiling is declared per press.
export type StartCheckinLimits = Partial<Omit<StartCheckinPayload, "campaign_id">>;

// TOTAL over the type, so a new `LaunchLimits` field is a type error here rather than a ceiling
// the browser silently stops sending.
const CEILING_KEYS: { [K in keyof Required<StartCheckinLimits>]: true } = {
  halt_at_accuracy: true,
  spend_budget_usd: true,
  token_budget: true,
};

// `campaignId` is the draft's `draft_id`. An omitted ceiling is "no ceiling of mine" (the account's
// still binds); sent sparsely so the `CommandRecord` shows which the operator declared.
export async function postStartCheckin(
  campaignId: string,
  limits: StartCheckinLimits = {},
): Promise<StartCheckinResponse> {
  const payload: Record<string, unknown> = { campaign_id: campaignId };
  for (const key of Object.keys(CEILING_KEYS) as (keyof StartCheckinLimits)[]) {
    const value = limits[key];
    if (typeof value === "number") payload[key] = value;
  }
  return postCommand<StartCheckinResponse>("start-checkin", payload);
}
export async function getCampaignCheckin(
  campaignId: string,
): Promise<CheckinReopenResponse> {
  const r = await fetch(
    `${API}/campaigns/${encodeURIComponent(campaignId)}/checkin`,
    { cache: "no-store" },
  );
  if (!r.ok) await throwApiError(r);
  return (await r.json()) as CheckinReopenResponse;
}
// High-confidence proposals auto-confirm; low-confidence ones land `proposed`.
export async function postResolveOrigin(draftId: string): Promise<ResolveOriginResponse> {
  return postCommand<ResolveOriginResponse>("resolve-origin", { draft_id: draftId });
}
