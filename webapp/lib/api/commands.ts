import { encodeDescend, pathRoot, type CyclePath } from "../ids";
import { API, send } from "./client";
import { mintIdempotencyKey, throwApiError } from "./errors";
import type {
  Budget,
  CommandKind,
  ConfigOverrides as WireConfigOverrides,
  CycleSeed,
  ParamIntent,
  RunMode,
  StartRunPayload,
} from "./types.generated";
import type { ArchiveReport, CommandAcceptedBody, VerifyReading } from "./types";

export type VerifyStrategy = VerifyReading["strategy"];

export async function postCommand<T = CommandAcceptedBody>(
  kind: CommandKind,
  payload: Record<string, unknown>,
): Promise<T> {
  const r = await send(`${API}/commands/${kind}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": mintIdempotencyKey(),
    },
    body: JSON.stringify({ kind, payload }),
  });
  if (!r.ok) await throwApiError(r);
  return (await r.json()) as T;
}
function cycleAddress(path: CyclePath): Record<string, unknown> {
  const root = pathRoot(path);
  const descend = encodeDescend(path);
  return {
    campaign_id: root.campaignId,
    cycle_id: root.cycleId,
    ...(descend ? { descend } : {}),
  };
}
// Absent inherits the parent; a present value is ABSOLUTE for the fork.
export type ConfigOverrides = Partial<WireConfigOverrides>;
// The reconcile dialog's subset: the policy knobs break search comparability, so it sets none.
export type RunLimitOverrides = Partial<Pick<WireConfigOverrides, "budget" | "nodes">>;
// `node_narrowing` overrides the campaign's mint-time narrowing for this cycle only; absent inherits it.
export type ForkSeed = Partial<
  Omit<CycleSeed, "origin_source" | "config_overrides" | "optimizer_narrowing">
> & { config_overrides?: ConfigOverrides; node_narrowing?: Record<string, ParamIntent[]> };
// `keepRounds` is `operator_rewind`, which the server refuses with an `origin_prompts` seed.
export async function postSteerFork(
  path: CyclePath,
  round: number,
  candidateId: string,
  opts: {
    seed: ForkSeed;
    keepRounds?: boolean;
  },
): Promise<CommandAcceptedBody> {
  const payload: Record<string, unknown> = {
    ...cycleAddress(path),
    round,
    candidate_id: candidateId,
    seed: opts.seed,
  };
  if (opts.keepRounds) payload.keep_rounds = true;
  return postCommand("fork-cycle", payload);
}
// `""` is a real value: it clears the label back to the dataset-name fallback.
export async function postSetCampaignLabel(
  campaignId: string,
  label: string,
): Promise<CommandAcceptedBody> {
  return postCommand("set-campaign-label", { campaign_id: campaignId, label });
}
export async function postCleanupEmpty(path: CyclePath): Promise<CommandAcceptedBody> {
  return postCommand("cleanup-empty-cycles", cycleAddress(path));
}
// Archive only flips `lifecycle_status`; delete removes the tree (`measurements/` survives both).

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
// The one interrupt verb: there is no stop, and resuming is `postStartRun`.
export async function postPauseCycle(path: CyclePath): Promise<CommandAcceptedBody> {
  return postCommand("pause-cycle", cycleAddress(path));
}
// Not a stop: the run continues to the next candidate, and the cycle is marked `human_intervened`.
export async function postSkipSearchpoint(path: CyclePath): Promise<CommandAcceptedBody> {
  return postCommand("skip-searchpoint", cycleAddress(path));
}
// Answers when the passes have FINISHED, not when they start.
export async function postGradeBench(path: CyclePath): Promise<CommandAcceptedBody> {
  return postCommand("grade-bench", cycleAddress(path));
}
// `samples` omitted is the server's count (`verify.py::derive_verify_samples`); a larger one is refused.
export async function postVerifyCandidate(
  path: CyclePath,
  candidateId: string,
  opts: { strategy: VerifyStrategy; samples: number | null },
): Promise<CommandAcceptedBody> {
  return postCommand("verify-candidate", {
    ...cycleAddress(path),
    candidate_id: candidateId,
    strategy: opts.strategy,
    ...(opts.samples != null ? { samples: opts.samples } : {}),
  });
}
// `cells: 1` disarms; sent unclamped, the walk clamps it to `max_cells_in_flight`.
export async function postSetSampleLookahead(
  path: CyclePath,
  cells: number,
  auto: boolean,
): Promise<CommandAcceptedBody> {
  return postCommand("set-sample-lookahead", { ...cycleAddress(path), cells, auto });
}
// A named axis binds (`0` halts at the next round boundary), a named `null` lifts it, an unnamed one is unchanged.
export async function postChangeRunLimits(
  path: CyclePath,
  budget: Budget,
): Promise<CommandAcceptedBody> {
  return postCommand("change-run-limits", { ...cycleAddress(path), budget });
}
// The caller's own account limit; the result reads back off `/auth/quota-status`.
export async function postSetConcurrentCycles(limit: number): Promise<CommandAcceptedBody> {
  return postCommand("set-concurrent-cycles", { max_concurrent_cycles: limit });
}
export type StartRunOptions = Partial<Pick<StartRunPayload, "budget" | "step_rounds">> & {
  mode?: Partial<RunMode>;
};

export async function postStartRun(
  path: CyclePath,
  options: StartRunOptions = {},
): Promise<CommandAcceptedBody> {
  return postCommand("start-run", { ...cycleAddress(path), ...options });
}

// A dry run unless `apply`; `purge-cold` with `apply` destroys paid measurement.
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
