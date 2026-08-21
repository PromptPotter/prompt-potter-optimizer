// The closed-set command highway — every campaign-state write posts to `/commands/{kind}`,
// declared in `docs/specs/m12-api-openapi.yaml` and dispatched server-side by `CommandDispatcher`
// (`promptpotter/presentation/api/middleware/`). The dispatcher writes a `CommandRecord` to the
// target cycle's ledger, inline-applies the mutation, then writes a `CommandAckRecord`.
//
// These are pure I/O — they do NOT trigger poll revalidation themselves. The caller bumps
// `lib/revalidate.ts` after a mutation resolves so the workspace poll picks the change up
// immediately instead of on its next tick. The 202 body is generic (`CommandAcceptedBody` per
// the OpenAPI schema); detailed apply results (new fork id, cleanup count) flow via the
// workspace poll picking up the new on-disk state.

import { encodeDescend, pathRoot, type CyclePath } from "../ids";
import { API } from "./client";
import { mintIdempotencyKey, throwApiError } from "./errors";
import type {
  CommandKind,
  ConfigOverrides as WireConfigOverrides,
  CycleSeed,
  OriginGateDecisionPayload,
} from "./types.generated";
import type { CommandAcceptedBody } from "./types";

// The ONE `POST /commands/{kind}` write path. Generic over the response because the
// typed routes (`edit-draft-campaign`, `start-checkin`, `resolve-origin`,
// `replace-dataset`) answer with a domain object rather than the 202 envelope — those
// four had each re-spelled this function to say so.
export async function postCommand<T = CommandAcceptedBody>(
  kind: CommandKind,
  payload: Record<string, unknown>,
): Promise<T> {
  const r = await fetch(`${API}/commands/${kind}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": mintIdempotencyKey(),
    },
    body: JSON.stringify({ kind, payload }),
    cache: "no-store",
  });
  if (!r.ok) await throwApiError(r);
  return (await r.json()) as T;
}
// The fork's `OptimizationConfig` delta — twin of the OpenAPI `ConfigOverrides`.
// Every field optional; absent inherits the parent. Values are ABSOLUTE for the
// fork (the dialog defaults rounds/spend to the parent's remaining, then the
// operator confirms the fork's own ceiling).
//
// Deliberately the RUN-LIMIT subset: the wire also carries the policy knobs
// (`per_round_resubset`, `schema_field_rename`), which invalidate search
// comparability and are set at mint or by an L2/L3 `fork_proposal` — never by a
// checkbox on a dialog whose job is "how much budget does this fork get". A
// human-set model/provider value rides the fork's `pipeline_overlay`, not here.
export type ConfigOverrides = Partial<
  Pick<
    WireConfigOverrides,
    | "max_rounds"
    | "spend_budget_usd"
    | "token_budget"
    | "l1_patience"
    | "l2_patience"
    | "l3_patience"
    | "pobb_epsilon"
  >
>;
// The edited-searchpoint origin override — twin of the OpenAPI
// `OperatorForkOverride`. Required on every operator fork (all are
// `operator_steered`). `origin_prompt_fields` is the PromptTemplate field shape;
// `pipeline_overlay` is the candidate's `nodes.*.config` value delta, carried verbatim
// and merged onto the dataset overlay at fork init. `optimizer_narrowing` carries
// per-node param LOCK edits ({node: {param_keys, param_allowed_values}}) — overrides the
// campaign's mint-time narrowing for this cycle only; absent inherits it unchanged.
export type OperatorForkOverride = Partial<
  Omit<CycleSeed, "origin_source" | "config_overrides">
> & { config_overrides?: ConfigOverrides };
// Mint an `operator_steered` fork rooted at the selected searchpoint, carrying
// the operator's edits + reconciled limits. The single fork write path.
export async function postForkCycle(
  campaignId: string,
  cycleId: string,
  round: number,
  candidateId: string,
  opts: { seed: OperatorForkOverride; steeredBy?: string },
): Promise<CommandAcceptedBody> {
  const payload: Record<string, unknown> = {
    campaign_id: campaignId,
    cycle_id: cycleId,
    round,
    candidate_id: candidateId,
    seed: opts.seed,
  };
  if (opts.steeredBy) payload.steered_by = opts.steeredBy;
  return postCommand("fork-cycle", payload);
}
// Rewrite a campaign's inner-optimizer model allow-list — the frozen
// `campaign.json::config`, the single source both the fork cap-gate and the runner's
// grade-C stamp read. Replaces the set wholesale; [] clears it (restrictive default).
// Owner-gated server-side (`campaign.lifecycle`); the optimizer never touches it.
export async function postSetAllowedModels(
  campaignId: string,
  allowedModels: string[],
): Promise<CommandAcceptedBody> {
  return postCommand("set-allowed-models", {
    campaign_id: campaignId,
    allowed_models: allowedModels,
  });
}
// Give a campaign an operator name — `campaign.json::label`, which
// `lib/names.ts::campaignDisplayName` prefers over the dataset name everywhere a
// campaign is named to a human. `""` clears it and restores that fallback, so an
// empty string is a real value here rather than a no-op. The campaign_id is NOT
// touched: it addresses the directory, the measurement cache and every bookmark.
// Owner-gated server-side (`campaign.lifecycle`); identity-neutral, so a rename
// cannot void a banked origin.
export async function postSetCampaignLabel(
  campaignId: string,
  label: string,
): Promise<CommandAcceptedBody> {
  return postCommand("set-campaign-label", { campaign_id: campaignId, label });
}
export async function postCleanupEmpty(
  campaignId: string,
  cycleId: string,
): Promise<CommandAcceptedBody> {
  return postCommand("cleanup-empty-cycles", {
    campaign_id: campaignId,
    cycle_id: cycleId,
  });
}
// Campaign lifecycle. Archive moves NOTHING — it flips `lifecycle_status`, so the
// tree stays in `campaigns/` and only the listing filter hides it (reversible via
// unarchive); delete is PHYSICAL and irreversible — the server
// defaults `keep_results` to false, so the whole campaign tree plus every L4
// inner sandbox its cycles spawned is removed. Measurements survive either way:
// `measurements/` belongs to no single campaign, so siblings still cache-hit.
//
// (This comment used to claim "deletion is never physical at this site" and name
// `try_delete_stub_cycle` as the only physical-delete path. That described a
// design the store had already left, and it is exactly the kind of reassurance
// worth distrusting on a destructive verb.)
//
// Both verbs 409 while any cycle in the campaign has a LIVE producer — pause or
// stop it first. Being the campaign you are currently looking at is NOT a
// refusal: the server releases the active pointer and proceeds.

export async function postArchiveCampaign(
  campaignId: string,
  reason?: string,
): Promise<CommandAcceptedBody> {
  const payload: Record<string, unknown> = { campaign_id: campaignId };
  if (reason) payload.reason = reason;
  return postCommand("archive-campaign", payload);
}
export async function postUnarchiveCampaign(
  campaignId: string,
): Promise<CommandAcceptedBody> {
  return postCommand("unarchive-campaign", { campaign_id: campaignId });
}
export async function postDeleteCampaign(
  campaignId: string,
  reason?: string,
): Promise<CommandAcceptedBody> {
  const payload: Record<string, unknown> = { campaign_id: campaignId };
  if (reason) payload.reason = reason;
  return postCommand("delete-campaign", payload);
}
// Pause a running cycle — the single operator-interrupt verb. Writes
// `.runtime/pause.flag`; the loop's `pause_check` sees it at the next checkpoint,
// the worker exits cleanly, and the cycle stays resumable (non-terminal). There
// is no separate "stop" / "resume-cycle": resuming is `postStartRun(…, "resume")`
// relaunching from the last completed round. Idempotent. Cycle-scoped per
// `m12-api-openapi.yaml::pauseCycle`. Pause-state reads back from
// `GET /api/v1/sessions/active/live-state::is_paused`.
export async function postPauseCycle(
  campaignId: string,
  cycleId: string,
): Promise<CommandAcceptedBody> {
  return postCommand("pause-cycle", { campaign_id: campaignId, cycle_id: cycleId });
}
// Operator early-abort of the searchpoint scoring right now: writes a one-shot
// `.runtime/skip.flag`; the loop cuts the remaining samples of the in-flight
// searchpoint, accepts the partial score, and the cycle CONTINUES to the next
// candidate (NOT a stop). The operator analog of automatic PoBB elimination.
// A manual skip marks the cycle `human_intervened` (babysat). Cycle-scoped per
// `m12-api-openapi.yaml::skipSearchpoint`.
export async function postSkipSearchpoint(
  campaignId: string,
  cycleId: string,
): Promise<CommandAcceptedBody> {
  return postCommand("skip-searchpoint", { campaign_id: campaignId, cycle_id: cycleId });
}
// Set how many of a candidate's samples the scoring walk holds in flight; `cells: 1` disarms,
// so it is a cancel rather than a second verb. The request is sent unclamped and the walk
// clamps it to the backend's ceiling (`dashboard.json::max_cells_in_flight`). It also ends on
// its own — after the round that scored under it, or the group it released, per that backend's
// `concurrency_arming`. Unlike skip it does NOT mark the cycle babysat: the in-flight
// acquisition is discarded, so the measurement is identical at any depth. Host-admin only.
//
// The one command addressed by PATH rather than by the root hop: throughput is what an inner
// run answers for itself, so an L4 inner cycle is armed by descending to it — the same
// `descend` grammar the dashboard poll uses, empty at depth 1.
// Per `m12-api-openapi.yaml::setSampleLookahead`.
export async function postSetSampleLookahead(
  path: CyclePath,
  cells: number,
): Promise<CommandAcceptedBody> {
  const root = pathRoot(path);
  const descend = encodeDescend(path);
  return postCommand("set-sample-lookahead", {
    campaign_id: root.campaignId,
    cycle_id: root.cycleId,
    cells,
    ...(descend ? { descend } : {}),
  });
}
// Raise or lower a running cycle's USD and/or token spend cap mid-flight. Writes
// `.runtime/spend_cap.json` ({max_usd, max_tokens}); the round loop's BudgetGate
// re-reads it every clean round — `0` on a ceiling halts at the next round
// boundary, raising above current usage releases. Pass `null` for a ceiling to
// leave it unchanged (the applier merges). At least one must be a number.
// Cycle-scoped command per `m12-api-openapi.yaml::changeSpendBudget`.
// Resolve a cycle blocked at the round-0 origin gate (`run_phase: gate`): the
// origin verdict was not `healthy`, so the runner is holding before L1. Writes
// `.runtime/gate_decision.json`, which the runner polls. `rescore` re-measures
// the origin force-fresh (reflecting a backend-code fix) and re-evaluates the
// gate in place; `proceed` overrides into L1; `abort` ends the cycle with
// `StopReason.ORIGIN_GATE`. Cycle-scoped per `m12-api-openapi.yaml::originGateDecision`.
export type OriginGateDecision = OriginGateDecisionPayload["decision"];
export async function postOriginGateDecision(
  campaignId: string,
  cycleId: string,
  decision: OriginGateDecision,
): Promise<CommandAcceptedBody> {
  return postCommand("origin-gate-decision", {
    campaign_id: campaignId,
    cycle_id: cycleId,
    decision,
  });
}
export async function postChangeSpendBudget(
  campaignId: string,
  cycleId: string,
  caps: { maxUsd?: number | null; maxTokens?: number | null },
): Promise<CommandAcceptedBody> {
  const payload: Record<string, unknown> = {
    campaign_id: campaignId,
    cycle_id: cycleId,
  };
  if (typeof caps.maxUsd === "number") payload.max_usd = caps.maxUsd;
  if (typeof caps.maxTokens === "number") payload.max_tokens = caps.maxTokens;
  return postCommand("change-spend-budget", payload);
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
  return postCommand("start-run", payload);
}
