import { API, send } from "./client";
import { mintIdempotencyKey, throwApiError } from "./errors";
import { postCommand } from "./commands";
import type {
  CheckinReopenResponse,
  DatasetReplaced,
  DraftCampaignWire,
  DraftPatch,
  ResolveOriginResponse,
  StartCheckinResponse,
} from "./types";
import type { LaunchLimits, StartCheckinPayload } from "./types.generated";

export async function postIngestDataset(
  file: File,
  slug?: string,
): Promise<DraftCampaignWire> {
  const form = new FormData();
  form.append("file", file);
  if (slug) form.append("slug", slug);
  const r = await send(`${API}/datasets/ingest`, {
    method: "POST",
    body: form,
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
  const r = await send(`${API}/datasets/draft/candidate-library`, {
    method: "POST",
    headers: { "Idempotency-Key": mintIdempotencyKey() },
    body: form,
  });
  if (!r.ok) await throwApiError(r);
  return (await r.json()) as DraftCampaignWire;
}
export async function postBuildCandidateLibraryFromColumn(
  draftId: string,
  column: string,
): Promise<DraftCampaignWire> {
  const r = await send(`${API}/datasets/draft/candidate-library/from-column`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": mintIdempotencyKey(),
    },
    body: JSON.stringify({ draft_id: draftId, column }),
  });
  if (!r.ok) await throwApiError(r);
  return (await r.json()) as DraftCampaignWire;
}
// Mints a durable `checkin` campaign from the dataset's CURRENT config; nothing runs until `postStartCheckin`.
export async function postDraftFromDataset(name: string): Promise<DraftCampaignWire> {
  const r = await send(`${API}/datasets/${encodeURIComponent(name)}/draft`, {
    method: "POST",
  });
  if (!r.ok) await throwApiError(r);
  return (await r.json()) as DraftCampaignWire;
}
// Unlike `postDraftFromDataset`, reproduces the origin's prompt verbatim, with `campaign_origin` lineage.
export async function postDraftFromOrigin(originId: string): Promise<DraftCampaignWire> {
  const r = await send(`${API}/origins/${encodeURIComponent(originId)}/draft`, {
    method: "POST",
  });
  if (!r.ok) await throwApiError(r);
  return (await r.json()) as DraftCampaignWire;
}
export async function postReplaceDataset(slug: string): Promise<DatasetReplaced> {
  return postCommand<DatasetReplaced>("replace-dataset", { slug });
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
// Per press, never draft state: a reopened check-in must not show a budget nobody re-entered.
export type StartCheckinOptions = Partial<
  Omit<StartCheckinPayload, "campaign_id" | "limits">
> & { limits?: Partial<LaunchLimits> };

const START_KEYS: { [K in keyof Required<StartCheckinOptions>]: true } = {
  limits: true,
  diag: true,
  backend_url: true,
  backend_id: true,
};

// `campaignId` is the draft's `draft_id`; sparse, so the `CommandRecord` shows what the operator declared.
export async function postStartCheckin(
  campaignId: string,
  options: StartCheckinOptions = {},
): Promise<StartCheckinResponse> {
  const payload: Record<string, unknown> = { campaign_id: campaignId };
  for (const key of Object.keys(START_KEYS) as (keyof StartCheckinOptions)[]) {
    const value = options[key];
    if (value !== undefined && value !== null) payload[key] = value;
  }
  return postCommand<StartCheckinResponse>("start-checkin", payload);
}
export async function getCampaignCheckin(
  campaignId: string,
): Promise<CheckinReopenResponse> {
  const r = await send(`${API}/campaigns/${encodeURIComponent(campaignId)}/checkin`);
  if (!r.ok) await throwApiError(r);
  return (await r.json()) as CheckinReopenResponse;
}
export async function postResolveOrigin(draftId: string): Promise<ResolveOriginResponse> {
  return postCommand<ResolveOriginResponse>("resolve-origin", { draft_id: draftId });
}
