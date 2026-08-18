"use client";
// What a SET of campaigns jointly says, reduced fresh server-side. One-shot fetch (not the 2 s
// poll) — it re-runs when the selection or the ranking flag changes and at no other time.
//
// `ranking` is the operator's press, not a default: everything else opens one round-0 document
// per campaign, while the ranking walks every round of every campaign selected. Passing it as a
// dep means turning it on re-fetches once and turning it off does not re-walk.

import { useFetch } from "@/lib/hooks/useFetch";
import { fetchEvidence, type Evidence } from "@/lib/api";

export function useEvidence(
  campaignIds: readonly string[],
  ranking: boolean,
): { evidence: Evidence | null; loading: boolean; error: string | null } {
  // Sorted + joined so a re-ordered selection of the same campaigns is the SAME key — the
  // server pools them, and order is a picker artifact, not part of the question.
  const key = [...campaignIds].sort().join(",");
  const { data, loading, error } = useFetch<Evidence>(
    key ? (signal) => fetchEvidence(key.split(","), { ranking }, signal) : null,
    [key, ranking],
  );
  return { evidence: data, loading, error };
}
