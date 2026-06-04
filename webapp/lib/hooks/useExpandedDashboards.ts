"use client";
// Live dashboards for a small, operator-chosen set of cycles — the EXPANDED
// non-selected lanes in the lineage tree. This is the ONE webapp capability the
// folder UI can't represent (many cycles' live state at once), so it's web-only
// by design.
//
// Kept computationally minimal — it adds NO new sync surface:
//   - polls ONLY the cycle ids passed in (the lanes the operator expanded),
//     so cost scales with what's open, not with campaign size;
//   - reuses the per-cycle dashboard.json channel with If-Modified-Since, so an
//     idle / finished cycle costs a bodyless 304;
//   - pauses while the tab is hidden (usePoll) and self-halts on a 401.
// The SELECTED cycle is already polled by useCycleStream — never pass it here.

import { useRef, useState } from "react";
import { fetchDashboardConditional } from "@/lib/api";
import type { DashboardSnapshot } from "@/lib/poll";
import { usePoll } from "@/lib/hooks/usePoll";
import { useAuthGate } from "@/lib/auth-context";

export function useExpandedDashboards(
  campaignId: string | null,
  cycleIds: readonly string[],
  intervalMs = 2000,
): Map<string, DashboardSnapshot> {
  const [snapshots, setSnapshots] = useState<Map<string, DashboardSnapshot>>(
    () => new Map(),
  );
  // Per-cycle Last-Modified so each tick can 304 cheaply.
  const lastModRef = useRef<Map<string, string | null>>(new Map());
  const { authed, onAuthError } = useAuthGate();

  const tick = async (signal: AbortSignal): Promise<void> => {
    if (!campaignId || cycleIds.length === 0) return;
    const results = await Promise.all(
      cycleIds.map(async (cid) => {
        try {
          const res = await fetchDashboardConditional(
            campaignId,
            cid,
            lastModRef.current.get(cid) ?? null,
            signal,
          );
          return { cid, res };
        } catch (e) {
          onAuthError(e);
          return null;
        }
      }),
    );

    // Carry forward still-expanded snapshots; apply fresh ones; drop the rest.
    const next = new Map<string, DashboardSnapshot>();
    for (const cid of cycleIds) {
      const prior = snapshots.get(cid);
      if (prior) next.set(cid, prior);
    }
    let changed = next.size !== snapshots.size; // a cycle was collapsed away
    for (const r of results) {
      if (!r) continue;
      const { cid, res } = r;
      if (res.lastModified) lastModRef.current.set(cid, res.lastModified);
      if (res.kind === "ok") {
        next.set(cid, res.data as DashboardSnapshot);
        changed = true;
      }
    }
    // Prune Last-Modified for cycles no longer expanded.
    for (const cid of [...lastModRef.current.keys()]) {
      if (!cycleIds.includes(cid)) lastModRef.current.delete(cid);
    }
    if (changed) setSnapshots(next);
  };

  usePoll(tick, {
    intervalMs,
    pauseWhenHidden: true,
    enabled: authed && !!campaignId && cycleIds.length > 0,
    // Fire an immediate tick when the expanded set changes (e.g. a fork was
    // just opened) instead of waiting up to one interval.
    revalidateOn: cycleIds.length,
  });

  return snapshots;
}
