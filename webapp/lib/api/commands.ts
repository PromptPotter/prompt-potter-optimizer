import { encodeDescend, pathRoot, type CyclePath } from "../ids";
import { API, send } from "./client";
import { mintIdempotencyKey, throwApiError } from "./errors";
import type {
  CommandKind,
  CommandPayloads,
  CommandResponses,
  ConfigOverrides as WireConfigOverrides,
  CycleSeed,
  ParamIntent,
} from "./types.generated";
import type { VerifyReading } from "./types";

export type VerifyStrategy = VerifyReading["strategy"];

// The body IS the kind's payload model, and the answer is its route's: neither is declared here.
export async function postCommand<K extends CommandKind>(
  kind: K,
  payload: CommandPayloads[K],
): Promise<CommandResponses[K]> {
  const r = await send(`${API}/commands/${kind}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": mintIdempotencyKey(),
    },
    body: JSON.stringify(payload),
  });
  if (!r.ok) await throwApiError(r);
  return (await r.json()) as CommandResponses[K];
}
// A command is addressed like a read: the root hop, plus the `descend` tail below it.
export function cycleAddress(path: CyclePath): {
  campaign_id: string;
  cycle_id: string;
  descend?: string;
} {
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
// Per press, never draft state: a reopened check-in must not show a budget nobody re-entered.
export type StartCheckinOptions = Omit<CommandPayloads["start-checkin"], "draft_id">;
