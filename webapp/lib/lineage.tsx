// A remembered individual id resolves through `answers_for_id`, never the first row holding it.

import { useMemo } from "react";
import { subtreeRead, type SubtreeMask, type SubtreeMoment } from "@/lib/api/reads";
import type { Said, SubtreeCampaign, SubtreeCourse } from "@/lib/api/types";
import { LINEAGE_POLL_MS, MASK_LENS_DEBOUNCE_MS } from "@/lib/cadence";
import { courseAt, indexLineage, phaseIs, type LineageIndex } from "@/lib/derivations";
import { lensOf, useMaskState, type MaskState } from "@/lib/scoring-mask";
import { useCellTerms } from "@/lib/hooks/useConnector";
import { shownData, useRead } from "@/lib/hooks/useRead";
import { useRegistry } from "@/lib/hooks/useRegistry";
import type { ReadResult } from "@/lib/read-cache";
import { useSelection } from "@/lib/state/selection";
import { createModuleStore } from "@/lib/state/module-store";
import { useWorkspace } from "@/lib/workspace";

export type CampaignTree = ReadResult<SubtreeCampaign>;

// Keyed on the body it read: every reader of one tree shares one index.
const indexed = new WeakMap<SubtreeCampaign, LineageIndex>();
const NO_TREE = indexLineage(null);

export function lineageIndexOf(tree: SubtreeCampaign | null): LineageIndex {
  if (tree === null) return NO_TREE;
  let index = indexed.get(tree);
  if (!index) {
    index = indexLineage(tree);
    indexed.set(tree, index);
  }
  return index;
}

// "" (off), "score:<formula>", or "abort:<variant>", for the campaign it was picked on: a pick
// never leaks across campaigns. The open scoring-mask panel overrides it.
const lensStore = createModuleStore<{ campaignId: string | null; lens: string }>({
  campaignId: null,
  lens: "",
});

function useLens(campaignId: string | null): string {
  const { campaignId: pickedOn, lens } = lensStore.useStore();
  return pickedOn === campaignId ? lens : "";
}

// An operator's mask edit goes out once it rests; a cycle's seed goes out at once. The settle
// restarts on the SEED (`seededFor`), never the address: an address switch first shows the last
// address's mask unseeded, and resetting there would debounce the new seed like an edit.
const settling = createModuleStore<{ seed: string | null; live: string | null; settled: string | null }>(
  { seed: null, live: null, settled: null },
);
let settleTimer: ReturnType<typeof setTimeout> | undefined;

function useSettledLens(seed: string | null, live: string | null): string | null {
  const shown = settling.useStore();
  const now = settling.get();
  if (now.seed !== seed) {
    clearTimeout(settleTimer);
    settling.set({ seed, live, settled: live });
    return live;
  }
  if (now.live !== live) {
    settling.set({ live });
    clearTimeout(settleTimer);
    settleTimer = setTimeout(() => settling.set({ settled: live }), MASK_LENS_DEBOUNCE_MS);
  }
  return shown.seed === seed ? shown.settled : live;
}

interface TreeKey {
  address: string | null;
  mask: SubtreeMask | "seeding" | null;
  moment: SubtreeMoment | null;
}

// The tree's mask: `seeding` while the open mask panel's seed for the viewed address is not known,
// so an address's first tree goes out under its own seed and the campaign's last tree stands meanwhile.
export function treeMaskOf(
  { open, seededFor }: MaskState,
  address: string | null,
  lens: string | null,
  samples: readonly number[] | null,
): SubtreeMask | "seeding" | null {
  if (open && seededFor !== address) return "seeding";
  return lens || samples ? { lens, samples } : null;
}

// The viewed tree's key off the mask store, which no dashboard landing moves. Keyed on the
// campaign, not the cycle: a same-campaign cycle switch keeps one read.
function useViewedTreeKey(maskState: MaskState): TreeKey {
  const { open: maskOpen, mask, seededFor } = maskState;
  const { viewed, at } = useWorkspace();
  const viewedAddress = viewed?.address ?? null;
  const { sampleSet } = useSelection();
  const lens = useLens(viewed?.root.campaignId ?? null);
  // Every reader names the same seed, seeding or not: the viewed address once seeded, else null.
  const seed = seededFor === viewedAddress ? viewedAddress : null;
  const cellTerms = useCellTerms();
  const maskLens = useSettledLens(seed, maskOpen ? lensOf(mask, cellTerms) : null);
  const lensParam = maskOpen ? maskLens : lens || null;
  const samples = sampleSet && sampleSet.length > 0 ? sampleSet : null;
  return {
    address: viewed?.root.campaignId ?? null,
    mask: treeMaskOf(maskState, viewedAddress, lensParam, samples),
    // The offset is the viewed LEAF's, which the campaign-keyed subtree is not addressed by.
    moment: at !== null && viewed !== null ? { at, cycle: viewed.path } : null,
  };
}

// No `onGone`: the dashboard read owns that verdict, and this reader may be another campaign's row.
// A campaign the listing has every course settled in reads once, re-asked only by an invalidation.
function useTree(key: TreeKey | null): CampaignTree {
  const { started } = useRegistry();
  const address = key?.address ?? null;
  const rests = started.some(
    (e) =>
      e.address === address &&
      e.children.length > 0 &&
      e.children.every((c) => phaseIs(c.state.status.run_phase, "settled")),
  );
  return useRead(
    key && address
      ? subtreeRead(address, key.mask, key.moment, rests ? null : LINEAGE_POLL_MS)
      : null,
  );
}

interface ViewedLineage {
  tree: SubtreeCampaign | null;
  treeRead: CampaignTree;
  index: LineageIndex;
  lens: string;
  setLens: (lens: string) => void;
  maskActive: boolean;
  // The served line naming what re-read this tree's stats; null on the realized record.
  readUnder: Said | null;
  scoringMaskActive: boolean;
}

const masked = new WeakMap<LineageIndex, boolean>();

function maskActiveOf(index: LineageIndex): boolean {
  let active = masked.get(index);
  if (active === undefined) {
    active = index.candidates.some((c) => c.facts.divergence !== null || c.facts.divergent);
    masked.set(index, active);
  }
  return active;
}

export function useViewedLineage(): ViewedLineage {
  const { viewed } = useWorkspace();
  const campaignId = viewed?.root.campaignId ?? null;
  const leafPath = viewed?.path ?? null;
  const mask = useMaskState();
  const maskOpen = mask.open;
  const treeRead = useTree(useViewedTreeKey(mask));
  const tree = shownData(treeRead);
  const index = lineageIndexOf(tree);
  const lens = useLens(campaignId);
  return useMemo(
    () => ({
      tree,
      treeRead,
      index,
      lens,
      setLens: (next: string) => lensStore.set({ campaignId, lens: next }),
      maskActive: maskActiveOf(index),
      readUnder: courseAt(index, leafPath)?.lines.find((l) => l.key === "read_under") ?? null,
      scoringMaskActive: maskOpen,
    }),
    [tree, treeRead, index, lens, campaignId, leafPath, maskOpen],
  );
}

// The viewed leaf's course entry: its `state` is the one word and next step every surface reads.
export function useViewedCourse(): SubtreeCourse | null {
  const { index } = useViewedLineage();
  const path = useWorkspace().viewed?.path ?? null;
  return useMemo(() => courseAt(index, path), [index, path]);
}

// A campaign's tree by its address: the viewed campaign's under the viewed mask and moment.
export function useLineageTree(address: string, enabled: boolean): CampaignTree {
  const { viewed } = useWorkspace();
  const viewedKey = useViewedTreeKey(useMaskState());
  const key = !enabled
    ? null
    : viewed?.root.campaignId === address
      ? viewedKey
      : { address, mask: null, moment: null };
  return useTree(key);
}
