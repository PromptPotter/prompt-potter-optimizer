import { meRead } from "@/lib/api/reads";
import type { Filable } from "@/lib/api/types";
import type { CyclePath } from "@/lib/ids";
import { shownData, useRead } from "@/lib/hooks/useRead";
import { useLineageTree } from "@/lib/state/lineage";

// What an act files on: a cycle's path (its campaign's tree answers who reads it, the exhibit's
// follower included) or the account itself (`/auth/me`).
export type ActSubject = CyclePath | "account" | null;

// The one door to the reader's kinds (`command_kinds.py::filable`), for an act no read offers:
// read off the read that answered the subject the act files on, never the viewed cycle's.
export function useFiles(on: ActSubject): (kind: Filable) => boolean {
  const account = on === "account";
  const campaignId = on === null || account ? null : (on[0]?.campaignId ?? null);
  const me = shownData(useRead(account ? meRead() : null));
  const tree = shownData(useLineageTree(campaignId ?? "", campaignId !== null));
  const kinds = account ? me?.filable : tree?.facts.filable;
  return (kind) => kinds?.includes(kind) ?? false;
}
