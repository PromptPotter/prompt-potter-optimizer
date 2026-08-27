"use client";
// WHICH CHANNELS the Compare tab is reading — held here rather than inside the pane, because the
// pane is not the only surface that writes it: a searchpoint is picked where it is being looked
// at, and a selection owned by the tab would be minted fresh every time the operator navigated
// back to it.
//
// A channel is a TOP-LEVEL CAMPAIGN plus the point inside it currently being read. The campaign is
// carried beside the address rather than parsed back out of it: the subject grammar is the
// server's, and the one place the browser spells it is `lib/api/reads.ts`. Nothing here PARSES an
// address; `defaultChannel` mints one, and it mints it through that speller.
//
// TOP-LEVEL is the load-bearing word. A channel anchored on an L4 seed addresses an inner
// campaign, and an inner campaign is in no registry and roots no tree — so carrying THAT id here
// left the card unable to name its campaign or draw a map, which is every inner channel.
//
// Picking a campaign lands on its WINNER — the branch that answers for it, read at the winner its
// last election crowned. The origin, and every other searchpoint, is one click away in the card's
// own lineage map; landing there instead would open on the number the run started from, which is
// the one thing nobody opens a comparison to see.
//
// Deliberately NOT in `view-memory.tsx`: this is a per-visit working set, and that record is
// scoped to one campaign while a comparison spans several by construction.

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { subjectKey } from "./api/reads";

export interface CompareChannel {
  /** The TOP-LEVEL campaign this channel lives under — the one the registry lists and the one
   *  its tree is rooted at, which for an L4 seed is the outer campaign, not the seed. */
  rootCampaignId: string;
  /** The subject address this channel currently reads at. Opaque here. */
  subject: string;
}

/** A campaign's default channel: the branch that ANSWERS for it, which the server then reads at
 *  the winner its last election crowned. The answering cycle is not always the root's own —
 *  after a supersede cut the line continues on a fork, so `sidebar/grouping.ts::buildForest`
 *  resolves it and every surface that offers a campaign reads the same one. */
export function defaultChannel(campaignId: string, answeringCycleId: string): CompareChannel {
  return { rootCampaignId: campaignId, subject: subjectKey("course", [campaignId, answeringCycleId]) };
}

interface CompareSelection {
  channels: readonly CompareChannel[];
  /** The addresses, for the read. Derived, so nothing holds a second list to keep in step. */
  subjects: readonly string[];
  hasCampaign: (rootCampaignId: string) => boolean;
  hasSubject: (subject: string) => boolean;
  /** Tick a campaign on at its default point, or drop every channel it owns. */
  toggleCampaign: (rootCampaignId: string, subject: string) => void;
  /** Add campaigns that are not already on the board, each at its default point. */
  addCampaigns: (channels: readonly CompareChannel[]) => void;
  /** Put one channel on a specific point — the dashboard's "compare this searchpoint". */
  addSubject: (channel: CompareChannel) => void;
  /** Re-point one channel IN PLACE, so it keeps its position and its colour. */
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
