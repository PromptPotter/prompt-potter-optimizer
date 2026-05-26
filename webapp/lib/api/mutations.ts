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
  if (!r.ok) {
    let msg = `${r.status} POST /commands/${kind}`;
    try {
      const body = (await r.json()) as {
        detail?: string | { message?: string; error?: string };
      };
      if (typeof body?.detail === "string") {
        msg = body.detail;
      } else if (body?.detail?.message) {
        msg = body.detail.message;
      }
    } catch {
      /* keep status-only message */
    }
    throw new Error(msg);
  }
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
