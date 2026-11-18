// Not in `view-memory.tsx`: that record is per campaign, and a comparison spans several.

import { useMemo } from "react";
import type { HierarchyCampaign, HierarchyCandidate } from "@/lib/api/types";
import { channelArms, type CompareItem, type LineageIndex } from "@/lib/derivations";
import { subjectKey, subjectRoot } from "@/lib/api/reads";
import { decodeCyclePath } from "@/lib/ids";
import { createModuleStore } from "@/lib/state/module-store";

/** After a supersede the line is a fork's: the served `facts.live_cycle` names it. */
export function defaultChannel(campaign: HierarchyCampaign): string {
  const line = decodeCyclePath(campaign.facts.live_cycle)?.at(-1);
  if (!line) throw new Error(`undecodable line ${campaign.facts.live_cycle}`);
  return subjectKey("cycle", [line.campaignId, line.cycleId]);
}

// A channel IS its subject: the run it sits in is the subject's own chain (`subjectRoot`).
interface CompareSelection {
  subjects: readonly string[];
  hasCampaign: (rootCampaignId: string) => boolean;
  hasSubject: (subject: string) => boolean;
  toggleCampaign: (subject: string) => void;
  addCampaigns: (subjects: readonly string[]) => void;
  addSubject: (subject: string) => void;
  show: (subjects: readonly string[]) => void;
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
  subjects: readonly string[];
  views: Readonly<Record<string, ChannelView>>;
}>({ subjects: [], views: {} });

function edit(next: (prev: readonly string[]) => readonly string[]): void {
  const { subjects: prev, views } = store.get();
  const subjects = next(prev);
  const kept = Object.entries(views).filter(([subject]) => subjects.includes(subject));
  store.set({ subjects, views: Object.fromEntries(kept) });
}

function unviewed(opensDetail: boolean): ChannelView {
  return { picked: undefined, folds: { detail: opensDetail, map: false, setup: true } };
}

// What a channel's card, detail and map highlight and fold. The pick is read off the channel's
// served tree, so a pick the tree no longer holds highlights nothing; until the operator picks it
// is the channel's seed (`channelArms`), and a deselect shows none, never the seed again; a
// candidate channel opens on its detail fold.
export function useChannelView({ subject, reading, headline }: CompareItem, index: LineageIndex) {
  const opensDetail = reading?.kind === "candidate";
  const stored = store.useStore().views[subject];
  const actions = useMemo(() => {
    const put = (next: (now: ChannelView) => Partial<ChannelView>) => {
      const { subjects, views } = store.get();
      if (!subjects.includes(subject)) return;
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
  const selected = view.picked === undefined ? arms.seed : entry?.entry_kind === "candidate" ? entry : null;
  return { ...view, arms, selected, ...actions };
}

const actions = {
  toggleCampaign: (subject: string) =>
    edit((prev) => {
      const root = subjectRoot(subject);
      return prev.some((s) => subjectRoot(s) === root)
        ? prev.filter((s) => subjectRoot(s) !== root)
        : [...prev, subject];
    }),
  addCampaigns: (next: readonly string[]) =>
    edit((prev) => [...prev, ...next.filter((n) => !prev.some((s) => subjectRoot(s) === subjectRoot(n)))]),
  addSubject: (subject: string) => edit((prev) => (prev.includes(subject) ? prev : [...prev, subject])),
  show: (next: readonly string[]) => edit(() => next),
  replace: (from: string, to: string) =>
    edit((prev) =>
      prev.includes(to) ? prev.filter((s) => s !== from) : prev.map((s) => (s === from ? to : s)),
    ),
  remove: (subject: string) => edit((prev) => prev.filter((s) => s !== subject)),
  clear: () => edit(() => []),
};

export function useCompareSelection(): CompareSelection {
  const { subjects } = store.useStore();
  return useMemo<CompareSelection>(
    () => ({
      subjects,
      hasCampaign: (id) => subjects.some((s) => subjectRoot(s) === id),
      hasSubject: (s) => subjects.includes(s),
      ...actions,
    }),
    [subjects],
  );
}
