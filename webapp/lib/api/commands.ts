import { encodeDescend, pathRoot, type CyclePath } from "../ids";
import { API, send, toApiError } from "./client";
import { mintIdempotencyKey } from "./errors";
import type {
  CommandPayloads,
  CommandResponses,
  ConfigOverrides as WireConfigOverrides,
  ForkSeed as WireForkSeed,
} from "./types.generated";
import type { VerifyReading } from "./types";

export type VerifyStrategy = VerifyReading["strategy"];

// The body IS the kind's payload model, and the answer is its route's: neither is declared here.
// `commandId` names the act while it runs, so a stop can address it (`cancel-command`).
export async function postCommand<K extends keyof CommandPayloads>(
  kind: K,
  payload: CommandPayloads[K],
  commandId?: string,
): Promise<CommandResponses[K]> {
  const r = await send(`${API}/commands/${kind}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": mintIdempotencyKey(),
      ...(commandId ? { "Command-Id": commandId } : {}),
    },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw await toApiError(r);
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
export type ForkSeed = Partial<Omit<WireForkSeed, "config_overrides">> & {
  config_overrides?: ConfigOverrides;
};
// Per press, never draft state: a reopened check-in must not show a budget nobody re-entered.
export type StartCheckinOptions = Omit<CommandPayloads["start-checkin"], "draft_id">;
