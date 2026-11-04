import { API, send, toApiError } from "./client";
import { postCommand } from "./commands";
import { mintIdempotencyKey } from "./errors";
import type { DraftCampaignWire } from "./types";

export async function postIngestDataset(
  file: File,
  slug?: string,
): Promise<DraftCampaignWire> {
  const form = new FormData();
  form.append("file", file);
  if (slug) form.append("slug", slug);
  const r = await send(`${API}/commands/ingest-upload`, {
    method: "POST",
    headers: { "Idempotency-Key": mintIdempotencyKey() },
    body: form,
  });
  if (!r.ok) throw await toApiError(r);
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
  if (!r.ok) throw await toApiError(r);
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
  if (!r.ok) throw await toApiError(r);
  return (await r.json()) as DraftCampaignWire;
}
// Opens a draft from the dataset's CURRENT config; no campaign exists until `start-checkin`.
export function postDraftFromDataset(name: string): Promise<DraftCampaignWire> {
  return postCommand("open-dataset-draft", { dataset_name: name });
}
// Unlike `postDraftFromDataset`, reproduces the origin's prompt verbatim, with `campaign_origin` lineage.
export function postDraftFromOrigin(originId: string): Promise<DraftCampaignWire> {
  return postCommand("open-origin-draft", { origin_id: originId });
}
