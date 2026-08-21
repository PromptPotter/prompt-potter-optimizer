// Dataset ingest, draft check-in and origin resolution — the write paths that are NOT command
// verbs, because they create the thing a command would later address. Multipart uploads, the
// draft patch, the check-in start/reopen, and the origin resolve turn.

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
// Drop a candidate library onto a draft — the operator's "drop in place" for an
// unfulfilled `candidate_source` dependency. The file is parsed server-side (one
// entry per line, or the first column of a CSV/Excel); the returned draft's
// `dependencies` block reports the dependency `fulfilled`.
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
// Build a draft's candidate library from one of its OWN columns — the unified
// "build from dataset" path. When the targets already live in the data (the
// target column / the union of the dataset's category sheets), the library is
// derived server-side, no external file. Returns the updated draft wire.
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
// Open an existing dataset (demo / benchmark / owned Origin) in the setup
// flow: the server builds a fully-prefilled DraftCampaign straight from the
// dataset's files — no browser-side CSV reconstruction, and the dataset's
// pipeline node config (backend model/provider) is preserved through commit.
// Like `postIngestDataset`, this mints a durable `checkin` campaign; nothing runs
// until the operator starts it via `postStartCheckin`.
export async function postDraftFromDataset(name: string): Promise<DraftCampaignWire> {
  const r = await fetch(`${API}/datasets/${encodeURIComponent(name)}/draft`, {
    method: "POST",
    cache: "no-store",
  });
  if (!r.ok) await throwApiError(r);
  return (await r.json()) as DraftCampaignWire;
}
// Reuse a campaign-backed origin: the server builds a DraftCampaign prefilled
// with that origin's EXACT prompt fields (the root-cycle seed when it was itself
// minted from an origin, else the dataset's authored prompt) and marks it so
// committing mints with `campaign_origin` lineage. Unlike `postDraftFromDataset`
// (which opens the dataset's CURRENT committed config) this reproduces the chosen
// origin's prompt verbatim. Mints a durable `checkin` campaign; run via `postStartCheckin`.
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
// Start a durable check-in campaign: gate the origin, commit the dataset, mint +
// spawn the run, flipping `checkin` → `active`. `campaignId` is the draft's
// `draft_id` (which IS the campaign id). Same response shape the old
// mint-campaign-from-draft returned. Wire: `POST /commands/start-checkin`.
export async function postStartCheckin(
  campaignId: string,
): Promise<StartCheckinResponse> {
  return postCommand<StartCheckinResponse>("start-checkin", {
    campaign_id: campaignId,
  });
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
// One origin-resolver turn: the origin-aware `checkin` node proposes
// evidence-cited values for the unresolved closed-set fields (high-confidence
// auto-confirms; low-confidence lands `proposed`). Synchronous, like
// `edit-draft-campaign`. Returns the resolver output + the post-apply draft.
export async function postResolveOrigin(draftId: string): Promise<ResolveOriginResponse> {
  return postCommand<ResolveOriginResponse>("resolve-origin", { draft_id: draftId });
}
