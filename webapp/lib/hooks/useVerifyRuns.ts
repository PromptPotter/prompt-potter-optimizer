"use client";
// A cycle's `verify` readings, the newest per candidate label. One read for the bar chart's
// verify series and the searchpoint's own reading, so the two cannot disagree.

import { useMemo } from "react";
import { fetchDiagnosticRuns, type DiagnosticRunRecord } from "@/lib/api";
import { readyData, useRead } from "./useRead";

const NONE: ReadonlyMap<string, DiagnosticRunRecord> = new Map();

export function useVerifyRuns(
  campaignId: string | null | undefined,
  cycleId: string | null | undefined,
  // Set only while a verdict is awaited: nothing else moves this read.
  intervalMs?: number,
): ReadonlyMap<string, DiagnosticRunRecord> {
  const resp = readyData(
    useRead(
      campaignId && cycleId
        ? {
            key: `${campaignId}\x1f${cycleId}`,
            fetch: (s) => fetchDiagnosticRuns(undefined, s),
          }
        : null,
      { surface: "diagnostic-runs", auth: true, intervalMs },
    ),
  );
  return useMemo(() => {
    if (!resp || !campaignId || !cycleId) return NONE;
    const m = new Map<string, DiagnosticRunRecord>();
    for (const r of resp.runs) {
      if (r.source_campaign !== campaignId || r.source_cycle !== cycleId) continue;
      const prior = m.get(r.source_label);
      if (!prior || r.ts > prior.ts) m.set(r.source_label, r);
    }
    return m;
  }, [resp, campaignId, cycleId]);
}
