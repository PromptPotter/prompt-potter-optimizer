"use client";
// WHICH CHANNELS the Compare tab reads: a TOP-LEVEL campaign plus the point inside it. The campaign
// rides beside the address, never parsed out of it — `lib/api/reads.ts` is the one speller.
// Not in `view-memory.tsx`: that record is per campaign, and a comparison spans several.

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { subjectKey } from "./api/reads";

export interface CompareChannel {
  /** For an L4 seed, the OUTER campaign: an inner one is in no registry and roots no tree. */
  rootCampaignId: string;
  subject: string;
}

/** The branch that ANSWERS for the campaign, read at its last crowned winner. After a supersede
 *  that is a fork, so `campaign-forest.ts::buildForest` resolves the cycle for every surface. */
export function defaultChannel(campaignId: string, answeringCycleId: string): CompareChannel {
  return { rootCampaignId: campaignId, subject: subjectKey("course", [campaignId, answeringCycleId]) };
}

interface CompareSelection {
  channels: readonly CompareChannel[];
  subjects: readonly string[];
  hasCampaign: (rootCampaignId: string) => boolean;
  hasSubject: (subject: string) => boolean;
  toggleCampaign: (rootCampaignId: string, subject: string) => void;
  addCampaigns: (channels: readonly CompareChannel[]) => void;
  addSubject: (channel: CompareChannel) => void;
  /** IN PLACE, so the channel keeps its position and its colour. */
  replace: (from: string, to: string) => void;
  remove: (subject: string) => void;
  clear: () => void;
}

const Ctx = createContext<CompareSelection | null>(null);

export function CompareSelectionProvider({ children }: { children: ReactNode }) {
  const [channels, setChannels] = useState<readonly CompareChannel[]>([]);

  const toggleCampaign = useCallback((rootCampaignId: string, subject: string) => {
    setChannels((prev) =>
      prev.some((c) => c.rootCampaignId === rootCampaignId)
        ? prev.filter((c) => c.rootCampaignId !== rootCampaignId)
        : [...prev, { rootCampaignId, subject }],
    );
  }, []);
  const addCampaigns = useCallback((next: readonly CompareChannel[]) => {
    setChannels((prev) => [
      ...prev,
      ...next.filter((n) => !prev.some((c) => c.rootCampaignId === n.rootCampaignId)),
    ]);
  }, []);
  const addSubject = useCallback((channel: CompareChannel) => {
    setChannels((prev) =>
      prev.some((c) => c.subject === channel.subject) ? prev : [...prev, channel],
    );
  }, []);
  const replace = useCallback((from: string, to: string) => {
    setChannels((prev) =>
      prev.some((c) => c.subject === to)
        ? prev.filter((c) => c.subject !== from)
        : prev.map((c) => (c.subject === from ? { ...c, subject: to } : c)),
    );
  }, []);
  const remove = useCallback((subject: string) => {
    setChannels((prev) => prev.filter((c) => c.subject !== subject));
  }, []);
  const clear = useCallback(() => setChannels([]), []);

  const value = useMemo<CompareSelection>(
    () => ({
      channels,
      subjects: channels.map((c) => c.subject),
      hasCampaign: (id) => channels.some((c) => c.rootCampaignId === id),
      hasSubject: (s) => channels.some((c) => c.subject === s),
      toggleCampaign,
      addCampaigns,
      addSubject,
      replace,
      remove,
      clear,
    }),
    [channels, toggleCampaign, addCampaigns, addSubject, replace, remove, clear],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useCompareSelection(): CompareSelection {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useCompareSelection outside CompareSelectionProvider");
  return ctx;
}
