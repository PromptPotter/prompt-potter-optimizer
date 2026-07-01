"use client";
// Inner-cycle enumeration for one L4 (`promptpotter-self`) outer cycle — the
// fan-out of inner campaigns it spawned, one per candidate×seed. Polled on a
// slow (~5 s) cadence only while the sidebar row is expanded: the live inner
// pointer advances as candidates complete, so the ● marker moves and finished
// inners flip to `terminal` + show `best_accuracy` without a restart. Keyed on
// the VIEWED outer `(campaignId, cycleId)` (a past, non-active outer cycle still
// lists its inner history — the sandbox is never torn down). Same stamp-key
// discipline as useDatasetPreview: returns empty until the first fetch for the
// current unit lands, and never bleeds one outer cycle's list into another.

import { useCallback, useState } from "react";
import { fetchInnerCycles, type CycleListEntry } from "../api";
import { useAuthGate } from "../auth-context";
import { usePoll } from "./usePoll";

export interface InnerCyclesState {
  inner: CycleListEntry[];
  // The inner sandbox's live pointer — the inner loop running right now (marked
  // ● in the sidebar), or null when none is live.
  activeInnerCampaignId: string | null;
  activeInnerCycleId: string | null;
  // False until the first fetch for the current unit resolves.
  loaded: boolean;
}

interface Loaded {
  key: string | null; // unit stamp the list was fetched for
  cycles: CycleListEntry[];
  activeCmp: string | null;
  activeCid: string | null;
}

const EMPTY: InnerCyclesState = {
  inner: [],
  activeInnerCampaignId: null,
  activeInnerCycleId: null,
  loaded: false,
};

export function useInnerCycles(
  outerCampaignId: string | null,
  outerCycleId: string | null,
  enabled: boolean,
): InnerCyclesState {
  const { authed, onAuthError } = useAuthGate();
  // \x1f (unit separator) can't collide with id characters.
  const unitKey =
    enabled && outerCampaignId && outerCycleId
      ? `${outerCampaignId}\x1f${outerCycleId}`
      : null;

  const [loaded, setLoaded] = useState<Loaded>({
    key: null,
    cycles: [],
    activeCmp: null,
    activeCid: null,
  });

  const tick = useCallback(
    async (signal: AbortSignal) => {
      if (!unitKey || !outerCampaignId || !outerCycleId) return;
      try {
        const res = await fetchInnerCycles(outerCampaignId, outerCycleId, signal);
        if (signal.aborted) return;
        setLoaded({
          key: unitKey,
          cycles: res.cycles,
          activeCmp: res.active_campaign_id,
          activeCid: res.active_cycle_id,
        });
      } catch (e) {
        if (signal.aborted) return;
        onAuthError(e);
        // Keep last-good ONLY within the same unit; a failed tick for a fresh
        // unit stamps the key with an empty list so no prior outer cycle's inner
        // rows bleed through the `fresh` guard below.
        setLoaded((prev) =>
          prev.key === unitKey
            ? { ...prev, key: unitKey }
            : { key: unitKey, cycles: [], activeCmp: null, activeCid: null },
        );
      }
    },
    [unitKey, outerCampaignId, outerCycleId, onAuthError],
  );

  usePoll(tick, {
    intervalMs: 5000,
    tickOnFocus: true,
    enabled: !!unitKey && authed,
  });

  if (!unitKey) return EMPTY;
  const fresh = loaded.key === unitKey;
  return {
    inner: fresh ? loaded.cycles : [],
    activeInnerCampaignId: fresh ? loaded.activeCmp : null,
    activeInnerCycleId: fresh ? loaded.activeCid : null,
    loaded: fresh,
  };
}
