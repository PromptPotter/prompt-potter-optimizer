"use client";

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import {
  activeRead,
  campaignsRead,
  cyclesRead,
  type CampaignSummary,
  type CycleListEntry,
  type LifecycleFilter,
} from "./api";
import { buildForest, type OriginGroup } from "./derivations";
import { shownData, useRead, type ReadFailure } from "./hooks/useRead";
import { dockPriority } from "./run-phase";

interface Registry {
  cycles: CycleListEntry[];
  campaigns: CampaignSummary[];
  forest: OriginGroup[];
  runningCycles: CycleListEntry[];
  cyclesLoaded: boolean;
  // False from a filter change until the read for the new filter lands.
  campaignsLoaded: boolean;
  failure: ReadFailure | null;
  lifecycleFilter: LifecycleFilter;
  setLifecycleFilter: (f: LifecycleFilter) => void;
}

const RegistryContext = createContext<Registry | null>(null);

const REGISTRY_INTERVAL_MS = 10000;
// Matches the dashboard's live beat, so a CLI-minted cycle is followed without the registry's lag.
const POINTER_INTERVAL_MS = 2000;

const NO_CYCLES: CycleListEntry[] = [];
const NO_CAMPAIGNS: CampaignSummary[] = [];

export function RegistryProvider({ children }: { children: ReactNode }) {
  const [lifecycleFilter, setLifecycleFilter] = useState<LifecycleFilter>("active");
  const cyclesResult = useRead(cyclesRead(), { auth: true, intervalMs: REGISTRY_INTERVAL_MS });
  const campaignsResult = useRead(campaignsRead(lifecycleFilter), {
    auth: true,
    intervalMs: REGISTRY_INTERVAL_MS,
  });

  const cycles = shownData(cyclesResult)?.cycles ?? NO_CYCLES;
  const landed = shownData(campaignsResult)?.campaigns ?? null;
  const [lastCampaigns, setLastCampaigns] = useState<CampaignSummary[]>(NO_CAMPAIGNS);
  if (landed !== null && landed !== lastCampaigns) setLastCampaigns(landed);
  const campaigns = landed ?? lastCampaigns;

  const forest = useMemo(() => buildForest(campaigns, cycles), [campaigns, cycles]);
  const runningCycles = useMemo(
    () =>
      cycles
        .filter((c) => c.producer_attached)
        .sort((a, b) => dockPriority(a.run_phase) - dockPriority(b.run_phase)),
    [cycles],
  );

  const failure =
    cyclesResult.status === "failed"
      ? cyclesResult.failure
      : campaignsResult.status === "failed"
        ? campaignsResult.failure
        : null;
  const cyclesLoaded = cyclesResult.status === "ready" || cyclesResult.status === "failed";
  const campaignsLoaded = landed !== null;

  const value = useMemo<Registry>(
    () => ({
      cycles,
      campaigns,
      forest,
      runningCycles,
      cyclesLoaded,
      campaignsLoaded,
      failure,
      lifecycleFilter,
      setLifecycleFilter,
    }),
    [
      cycles,
      campaigns,
      forest,
      runningCycles,
      cyclesLoaded,
      campaignsLoaded,
      failure,
      lifecycleFilter,
    ],
  );
  return <RegistryContext.Provider value={value}>{children}</RegistryContext.Provider>;
}

export function useRegistry(): Registry {
  const v = useContext(RegistryContext);
  if (!v) throw new Error("useRegistry must be called inside <RegistryProvider>");
  return v;
}

export interface ActivePointer {
  campaignId: string | null;
  cycleId: string | null;
  // "No active session" is null ids on a 200 (`active.py::get_active_session`), never this.
  failure: ReadFailure | null;
}

// The tenant's LATEST launch, not the live set.
export function useActivePointer(): ActivePointer {
  const read = useRead(activeRead(), { auth: true, intervalMs: POINTER_INTERVAL_MS });
  const active = shownData(read);
  const campaignId = active?.campaign_id || null;
  const cycleId = active?.cycle_id || null;
  const failure = read.status === "failed" ? read.failure : null;
  return useMemo(
    () => ({ campaignId, cycleId, failure }),
    [campaignId, cycleId, failure],
  );
}

export function useCampaign(campaignId: string | null): CampaignSummary | null {
  const { campaigns } = useRegistry();
  return campaigns.find((c) => c.campaign_id === campaignId) ?? null;
}

export function useCycleEntry(campaignId: string | null, cycleId: string | null): CycleListEntry | null {
  const { cycles } = useRegistry();
  if (!campaignId || !cycleId) return null;
  return cycles.find((c) => c.campaign_id === campaignId && c.cycle_id === cycleId) ?? null;
}
