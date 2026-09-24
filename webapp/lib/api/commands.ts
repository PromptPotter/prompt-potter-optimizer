// Every campaign-state write, posted to the closed-set `/commands/{kind}` highway. Pure I/O: the
// caller bumps `lib/revalidate.ts` once a write resolves.

import { encodeDescend, pathRoot, type CyclePath } from "../ids";
import { API } from "./client";
import { mintIdempotencyKey, throwApiError } from "./errors";
import type {
  CommandKind,
  ConfigOverrides as WireConfigOverrides,
  CycleSeed,
  OriginGateDecisionPayload,
} from "./types.generated";
import type { ArchiveReport, CommandAcceptedBody } from "./types";

// Generic over `T`: the typed routes answer with a domain object instead of the 202 envelope.
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
// Absent inherits the parent; a present value is ABSOLUTE for the fork.
export type ConfigOverrides = Partial<WireConfigOverrides>;
// The reconcile dialog's subset. The policy knobs break search comparability and are set at mint
// or by an L2/L3 `fork_proposal`, never on that dialog; `scoring` rides the scoring mask's apply.
export type RunLimitOverrides = Partial<
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
// `optimizer_narrowing` overrides the campaign's mint-time narrowing for this cycle only; absent
// inherits it unchanged.
export type OperatorForkOverride = Partial<
  Omit<CycleSeed, "origin_source" | "config_overrides">
> & { config_overrides?: ConfigOverrides };
// `keepRounds` makes it `operator_rewind` (lifts rounds 0..round-1, the terminal's `resume --rewind
// N`), which the server refuses with an `origin_prompt_fields` seed; unset is `operator_steered`.
export async function postSteerFork(
  campaignId: string,
  cycleId: string,
  round: number,
  candidateId: string,
  opts: {
    seed: OperatorForkOverride;
    steeredBy?: string;
    keepRounds?: boolean;
    pauseFirst: boolean;
  },
): Promise<CommandAcceptedBody> {
  // A steer supersedes the parent, so a fork launched beside a still-running loop races it.
  if (opts.pauseFirst) await postPauseCycle(campaignId, cycleId);
  const payload: Record<string, unknown> = {
    campaign_id: campaignId,
    cycle_id: cycleId,
    round,
    candidate_id: candidateId,
    seed: opts.seed,
  };
  if (opts.steeredBy) payload.steered_by = opts.steeredBy;
  if (opts.keepRounds) payload.keep_rounds = true;
  return postCommand("fork-cycle", payload);
}
// `""` is a real value: it clears the label back to the dataset-name fallback. Identity-neutral,
// so a rename never voids a banked origin.
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
// Archive only flips `lifecycle_status`; delete PHYSICALLY removes the tree and every inner
// sandbox (`measurements/` survives both). Both 409 while any cycle has a live producer.

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
// The one interrupt verb, idempotent: there is no stop, and resuming is `postStartRun(…, "resume")`.
// Pause state reads back from `live-state::is_paused`.
export async function postPauseCycle(
  campaignId: string,
  cycleId: string,
): Promise<CommandAcceptedBody> {
  return postCommand("pause-cycle", { campaign_id: campaignId, cycle_id: cycleId });
}
// Cuts the in-flight searchpoint's remaining samples and CONTINUES to the next candidate (not a
// stop). Marks the cycle `human_intervened`.
export async function postSkipSearchpoint(
  campaignId: string,
  cycleId: string,
): Promise<CommandAcceptedBody> {
  return postCommand("skip-searchpoint", { campaign_id: campaignId, cycle_id: cycleId });
}
// No `samples` parameter by design: the server derives the count
// (`verify.py::derive_verify_samples`), so one click cannot buy a million cells.
export async function postVerifyCandidate(
  campaignId: string,
  cycleId: string,
  label: string,
): Promise<CommandAcceptedBody> {
  return postCommand("verify-candidate", {
    campaign_id: campaignId,
    cycle_id: cycleId,
    label,
  });
}
// `cells: 1` disarms; sent unclamped, the walk clamps it to `max_cells_in_flight`. The one command
// addressed by PATH, because an inner cycle arms its own throughput. Never marks the cycle babysat.
export async function postSetSampleLookahead(
  path: CyclePath,
  cells: number,
  auto: boolean,
): Promise<CommandAcceptedBody> {
  const root = pathRoot(path);
  const descend = encodeDescend(path);
  return postCommand("set-sample-lookahead", {
    campaign_id: root.campaignId,
    cycle_id: root.cycleId,
    cells,
    auto,
    ...(descend ? { descend } : {}),
  });
}
// `rescore` re-measures the origin force-fresh and re-judges the gate in place; `proceed` overrides
// into L1; `abort` stops the cycle with `StopReason.ORIGIN_GATE`.
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
// A `0` ceiling halts at the next round boundary; an omitted one stays unchanged (the applier merges).
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
// The caller's OWN account limit — workspace-scoped, so no cycle. Refused (422) above the
// machine ceiling and on the host's key; read the result back off `/auth/quota-status`.
export async function postSetConcurrentCycles(limit: number): Promise<CommandAcceptedBody> {
  return postCommand("set-concurrent-cycles", { max_concurrent_cycles: limit });
}
// No cap args: a cap is declared at `start-checkin` or via `change-spend-budget`; a resume inherits.
export async function postStartRun(
  campaignId: string,
  cycleId: string,
  kind: "new" | "resume",
): Promise<CommandAcceptedBody> {
  return postCommand("start-run", { campaign_id: campaignId, cycle_id: cycleId, kind });
}

// A dry run unless `apply`; `purge-cold` with `apply` destroys paid measurement. Preview and apply
// return one report shape.
export async function postCompactArchive(opts: {
  mode: "compact" | "restore" | "purge-cold";
  dataset?: string;
  apply?: boolean;
}): Promise<ArchiveReport> {
  return postCommand<ArchiveReport>("compact-archive", {
    mode: opts.mode,
    ...(opts.dataset ? { dataset: opts.dataset } : {}),
    apply: opts.apply ?? false,
  });
}
