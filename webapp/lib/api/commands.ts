import { encodeDescend, pathRoot, type CyclePath } from "../ids";
import { reached } from "@/lib/read-cache";
import { API, send, toApiError, versionsSaid } from "./client";
import { mintIdempotencyKey } from "./errors";
import type {
  BrowserKind,
  CommandPayloads,
  CommandResponses,
  VerifyReading,
} from "@/lib/api/types";

export type VerifyStrategy = VerifyReading["strategy"];

type JsonKind = BrowserKind & keyof CommandPayloads;

// The id a send files under, minted before it, so its `cancel` can name it while it acts.
export function mintCommandId(): string {
  return crypto.randomUUID();
}

// The body IS the kind's payload model, and the answer is its route's: neither is declared here.
function json<K extends JsonKind>(kind: K) {
  return async (payload: CommandPayloads[K], commandId?: string): Promise<CommandResponses[K]> => {
    const r = await send(`${API}/commands/${kind}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": mintIdempotencyKey(),
        ...(commandId ? { "Command-Id": commandId } : {}),
      },
      body: JSON.stringify(payload),
    });
    return acked<CommandResponses[K]>(r);
  };
}

// A command is answered once the reads it moved stand where its write left them: so "applied"
// renders the served state, and a second press meets a send still in flight, never a second filing.
async function acked<T>(r: Response): Promise<T> {
  if (!r.ok) throw await toApiError(r);
  const body = (await r.json()) as T;
  const produced = versionsSaid(r);
  if (produced !== undefined) await reached(produced);
  return body;
}

// A file goes up as the form its route reads, so it has no JSON body to type.
async function ingestUpload(file: File, slug?: string): Promise<CommandResponses["ingest-upload"]> {
  const form = new FormData();
  form.append("file", file);
  if (slug) form.append("slug", slug);
  const r = await send(`${API}/commands/ingest-upload`, {
    method: "POST",
    headers: { "Idempotency-Key": mintIdempotencyKey() },
    body: form,
  });
  return acked<CommandResponses["ingest-upload"]>(r);
}

// Every kind whose `Press` claims the browser door, each by its one sender: tsc fails a claimed
// kind with none, and a kind the browser does not claim has none to call.
export const commands = {
  "accept-terms": json("accept-terms"),
  "archive-campaign": json("archive-campaign"),
  "bank-snippet": json("bank-snippet"),
  cancel: json("cancel"),
  "cancel-queued": json("cancel-queued"),
  "set-limits": json("set-limits"),
  "cleanup-empty-cycles": json("cleanup-empty-cycles"),
  "compact-archive": json("compact-archive"),
  "delete-campaign": json("delete-campaign"),
  "delete-cycle": json("delete-cycle"),
  "discard-draft": json("discard-draft"),
  "dismiss-bulletin": json("dismiss-bulletin"),
  "edit-draft-campaign": json("edit-draft-campaign"),
  "fork-cycle": json("fork-cycle"),
  "held-out": json("held-out"),
  "ingest-upload": ingestUpload,
  "list-api-tokens": json("list-api-tokens"),
  "mint-api-token": json("mint-api-token"),
  "open-dataset-draft": json("open-dataset-draft"),
  "open-origin-draft": json("open-origin-draft"),
  "pause": json("pause"),
  "remove-api-key": json("remove-api-key"),
  "resolve-origin": json("resolve-origin"),
  "revoke-api-token": json("revoke-api-token"),
  "set-api-key": json("set-api-key"),
  "rename-campaign": json("rename-campaign"),
  "set-concurrent-cycles": json("set-concurrent-cycles"),
  "set-sample-lookahead": json("set-sample-lookahead"),
  "set-user-settings": json("set-user-settings"),
  "skip-searchpoint": json("skip-searchpoint"),
  "start-checkin": json("start-checkin"),
  "start-run": json("start-run"),
  "submit-arms": json("submit-arms"),
  "unarchive-campaign": json("unarchive-campaign"),
  "verify-candidate": json("verify-candidate"),
} satisfies Record<BrowserKind, (...sent: never[]) => Promise<unknown>>;
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
// The fork's start as sent (`CommandSent`): absent inherits the parent, a present value is ABSOLUTE.
export type ForkStart = NonNullable<CommandPayloads["fork-cycle"]["start"]>;
// The reconcile dialog's subset: the policy knobs break search comparability, so it sets none.
export type RunLimitOverrides = Pick<NonNullable<ForkStart["config_overrides"]>, "budget" | "nodes">;
// Per press, never draft state: a reopened check-in must not show a budget nobody re-entered.
export type StartCheckinOptions = Omit<CommandPayloads["start-checkin"], "draft_id">;
