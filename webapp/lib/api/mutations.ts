// Mutating endpoints — the operator's write surface. Each rides an
// existing I/O kind (Persistence's `inherit_from`, Control-local's
// `stop_check` flag-poll); none introduces a new I/O kind. See
// promptpotter/presentation/CLAUDE.md for the charter.
//
// These are pure I/O — they do NOT trigger poll revalidation themselves.
// The caller bumps `lib/revalidate.ts` after a mutation resolves so the
// workspace poll picks the change up immediately instead of on its next
// tick.

import { API } from "./client";
import type {
  CleanupEmptyResponse,
  CreateForkResponse,
  StopCycleResponse,
} from "./types";

export async function postCreateFork(
  campaignId: string,
  cycleId: string,
  round: number,
  candidateId: string,
): Promise<CreateForkResponse> {
  const r = await fetch(
    `${API}/campaigns/${encodeURIComponent(campaignId)}` +
      `/cycles/${encodeURIComponent(cycleId)}/forks`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ round, candidate_id: candidateId }),
      cache: "no-store",
    },
  );
  if (!r.ok) throw new Error(`${r.status} POST /forks`);
  return (await r.json()) as CreateForkResponse;
}

export async function postCleanupEmpty(
  campaignId: string,
  cycleId: string,
): Promise<CleanupEmptyResponse> {
  const r = await fetch(
    `${API}/campaigns/${encodeURIComponent(campaignId)}` +
      `/cycles/${encodeURIComponent(cycleId)}/cleanup-empty`,
    { method: "POST", cache: "no-store" },
  );
  if (!r.ok) {
    let msg = `${r.status} POST /cleanup-empty`;
    try {
      const body = (await r.json()) as { detail?: string };
      if (body?.detail) msg = body.detail;
    } catch {
      /* keep status-only message */
    }
    throw new Error(msg);
  }
  return (await r.json()) as CleanupEmptyResponse;
}

export async function postStopCycle(
  campaignId: string,
  cycleId: string,
): Promise<StopCycleResponse> {
  const r = await fetch(
    `${API}/campaigns/${encodeURIComponent(campaignId)}` +
      `/cycles/${encodeURIComponent(cycleId)}/stop`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
    },
  );
  if (!r.ok) throw new Error(`${r.status} POST /stop`);
  return (await r.json()) as StopCycleResponse;
}
