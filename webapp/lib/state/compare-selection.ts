// Not in `view-memory.tsx`: that record is per campaign, and a comparison spans several.

import { useMemo } from "react";
import type { HierarchyCampaign, HierarchyCandidate } from "@/lib/api/types";
import { channelArms, type CompareItem, type LineageIndex } from "@/lib/derivations";
import { subjectKey } from "@/lib/api/reads";
import { decodeCyclePath } from "@/lib/ids";
import { createModuleStore } from "@/lib/state/module-store";

export interface CompareChannel {
  /** For an L4 seed, the OUTER campaign: an inner one is in no registry and roots no tree. */
  rootCampaignId: string;
  subject: string;
}

/** After a supersede the line is a fork's: the served `facts.line` names it. */
export function defaultChannel(campaign: HierarchyCampaign): CompareChannel {
  const line = decodeCyclePath(campaign.facts.line)?.at(-1);
  if (!line) throw new Error(`undecodable line ${campaign.facts.line}`);
  return {
    rootCampaignId: campaign.address,
    subject: subjectKey("course", [line.campaignId, line.cycleId]),
  };
}

interface CompareSelection {
  channels: readonly CompareChannel[];
  subjects: readonly string[];
  hasCampaign: (rootCampaignId: string) => boolean;
  hasSubject: (subject: string) => boolean;
  toggleCampaign: (rootCampaignId: string, subject: string) => void;
  addCampaigns: (channels: readonly CompareChannel[]) => void;
  addSubject: (channel: CompareChannel) => void;
  show: (channels: readonly CompareChannel[]) => void;
  /** IN PLACE, so the channel keeps its position and its colour. */
  replace: (from: string, to: string) => void;
  remove: (subject: string) => void;
  clear: () => void;
}

type Fold = "detail" | "map" | "setup";

// A channel's own view: which searchpoint of its tree it highlights (an address the served tree
// resolves; null, a pick of nothing; undefined, no pick yet) and which folds are open.
interface ChannelView {
  picked: string | null | undefined;
  folds: Record<Fold, boolean>;
}

// Transient: one module store, so no provider mirrors it. A channel's view lives and dies with
// the channel under its subject; a replaced subject starts unviewed.
const store = createModuleStore<{
  channels: readonly CompareChannel[];
  views: Readonly<Record<string, ChannelView>>;
}>({ channels: [], views: {} });

function edit(next: (prev: readonly CompareChannel[]) => readonly CompareChannel[]): void {
  const { channels: prev, views } = store.get();
  const channels = next(prev);
  const kept = Object.entries(views).filter(([subject]) => channels.some((c) => c.subject === subject));
  store.set({ channels, views: Object.fromEntries(kept) });
}

function unviewed(opensDetail: boolean): ChannelView {
  return { picked: undefined, folds: { detail: opensDetail, map: false, setup: true } };
}

// What a channel's card, detail and map highlight and fold. The pick is read off the channel's
// served tree, so a pick the tree no longer holds highlights nothing; until the operator picks it
// is the channel's seed (`channelArms`), and a deselect shows none, never the seed again; a
// candidate channel opens on its detail fold.
export function useChannelView({ channel, reading, headline }: CompareItem, index: LineageIndex) {
  const subject = channel.subject;
  const opensDetail = reading?.kind === "candidate";
  const stored = store.useStore().views[subject];
  const actions = useMemo(() => {
    const put = (next: (now: ChannelView) => Partial<ChannelView>) => {
      const { channels, views } = store.get();
      if (!channels.some((c) => c.subject === subject)) return;
      const now = views[subject] ?? unviewed(opensDetail);
      store.set({ views: { ...views, [subject]: { ...now, ...next(now) } } });
    };
    return {
      select: (c: HierarchyCandidate | null) => put(() => ({ picked: c?.address ?? null })),
      toggleFold: (fold: Fold) => put((now) => ({ folds: { ...now.folds, [fold]: !now.folds[fold] } })),
    };
  }, [subject, opensDetail]);
  const view = stored ?? unviewed(opensDetail);
  const arms = useMemo(() => channelArms(index, reading, headline !== null), [index, reading, headline]);
  const entry = view.picked ? index.byAddress.get(view.picked) : undefined;
  const selected = view.picked === undefined ? arms.seed : entry?.level === "candidate" ? entry : null;
  return { ...view, arms, selected, ...actions };
}

const actions = {
  toggleCampaign: (rootCampaignId: string, subject: string) =>
    edit((prev) =>
      prev.some((c) => c.rootCampaignId === rootCampaignId)
        ? prev.filter((c) => c.rootCampaignId !== rootCampaignId)
        : [...prev, { rootCampaignId, subject }],
    ),
  addCampaigns: (next: readonly CompareChannel[]) =>
    edit((prev) => [
      ...prev,
      ...next.filter((n) => !prev.some((c) => c.rootCampaignId === n.rootCampaignId)),
    ]),
  addSubject: (channel: CompareChannel) =>
    edit((prev) => (prev.some((c) => c.subject === channel.subject) ? prev : [...prev, channel])),
  show: (next: readonly CompareChannel[]) => edit(() => next),
  replace: (from: string, to: string) =>
    edit((prev) =>
      prev.some((c) => c.subject === to)
        ? prev.filter((c) => c.subject !== from)
        : prev.map((c) => (c.subject === from ? { ...c, subject: to } : c)),
    ),
  remove: (subject: string) => edit((prev) => prev.filter((c) => c.subject !== subject)),
  clear: () => edit(() => []),
};

export function useCompareSelection(): CompareSelection {
  const { channels } = store.useStore();
  return useMemo<CompareSelection>(
    () => ({
      channels,
      subjects: channels.map((c) => c.subject),
      hasCampaign: (id) => channels.some((c) => c.rootCampaignId === id),
      hasSubject: (s) => channels.some((c) => c.subject === s),
      ...actions,
    }),
    [channels],
  );
}
