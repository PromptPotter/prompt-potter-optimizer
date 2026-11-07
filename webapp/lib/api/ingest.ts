import { API, send, toApiError } from "./client";
import { postCommand } from "./commands";
import { mintIdempotencyKey } from "./errors";
import type { DraftCampaignWire } from "@/lib/api/types";

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
// A JSON body carries bytes base64 (`EditDraftPatch.candidate_library_file`).
async function base64Of(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000)
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}
export async function postUploadCandidateLibrary(
  draftId: string,
  file: File,
): Promise<DraftCampaignWire> {
  const blob = await base64Of(file);
  return postCommand("edit-draft-campaign", {
    draft_id: draftId,
    patch: { candidate_library_file: { filename: file.name, blob } },
  });
}
export function postBuildCandidateLibraryFromColumn(
  draftId: string,
  column: string,
): Promise<DraftCampaignWire> {
  return postCommand("edit-draft-campaign", {
    draft_id: draftId,
    patch: { candidate_library_column: column },
  });
}
// Opens a draft from the dataset's CURRENT config; no campaign exists until `start-checkin`.
export function postDraftFromDataset(name: string): Promise<DraftCampaignWire> {
  return postCommand("open-dataset-draft", { dataset_name: name });
}
// Unlike `postDraftFromDataset`, reproduces the origin's prompt verbatim, with `campaign_origin` lineage.
export function postDraftFromOrigin(originId: string): Promise<DraftCampaignWire> {
  return postCommand("open-origin-draft", { origin_id: originId });
}
