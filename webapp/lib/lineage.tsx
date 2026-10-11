"use client";
// RUNS are served on the live node only: an id-keyed lookup skips `superseded_by`.

import { createContext, useContext, useMemo, useState } from "react";
import { treeRead, type TreeMask, type TreeMoment } from "@/lib/api";
import { ABORT_LENS_LABELS } from "@/lib/api/types.generated";
import type { CourseNode } from "@/lib/api/types";
import { phaseIs } from "@/lib/run-phase";
import { indexLineage, type LineageIndex } from "@/lib/derivations";
import { lensOf, useScoringMask } from "@/lib/scoring-mask";
import { useScoringMaskSeed } from "@/lib/hooks/useServedCriterion";
import { useDebounced } from "@/lib/hooks/useDebounced";
import { shownData, useRead } from "@/lib/hooks/useRead";
import { encodeCyclePath, rootCycleId, type CyclePath } from "@/lib/ids";
import { useRegistry } from "@/lib/registry";
import { useSelection } from "@/lib/SelectionContext";
import { useWorkspace } from "@/lib/workspace";

const POLL_MS = 5000;

const LENS_LABELS: Record<string, string> = {
  "score:accuracy": "Accuracy",
  ...Object.fromEntries(
    Object.entries(ABORT_LENS_LABELS).map(([variant, label]) => [`abort:${variant}`, label]),
  ),
};

export interface CampaignTree {
  root: CourseNode | null;
  loaded: boolean;
  failed: boolean;
}

const EMPTY: CampaignTree = { root: null, loaded: false, failed: false };

export interface ViewedLineage {
  tree: CourseNode | null;
  index: LineageIndex;
  // "" (off), "score:<formula>", or "abort:<variant>"; the open scoring-mask panel overrides it.
  lens: string;
  setLens: (lens: string) => void;
  maskActive: boolean;
  maskLabel: string;
  scoringMaskActive: boolean;
}

interface TreeAddressing {
  viewedAddress: string | null;
  viewedMask: TreeMask | null;
  viewedMoment: TreeMoment | null;
  // Campaigns whose every `/cycles` row is terminal: read once, re-asked only by an invalidation.
  resting: ReadonlySet<string>;
}

// Two contexts: a sidebar row naming another campaign's read must not re-render per viewed-tree landing.
const TreeAddressingContext = createContext<TreeAddressing | null>(null);
const ViewedLineageContext = createContext<ViewedLineage | null>(null);

function useTree(
  path: CyclePath | null,
  mask: TreeMask | null,
  moment: TreeMoment | null,
  resting: ReadonlySet<string>,
): CampaignTree {
  const rests = path !== null && resting.has(path[0]?.campaignId ?? "");
  // No `onGone`: the dashboard read owns that verdict, and this reader may be another campaign's row.
  const read = useRead(path ? treeRead(path, mask, moment) : null, {
    auth: true,
    intervalMs: rests ? undefined : POLL_MS,
  });
  const root = shownData(read);
  const failed = read.status === "failed" && root === null;
  return useMemo(
    () => (root === null && !failed ? EMPTY : { root, loaded: root !== null, failed }),
    [root, failed],
  );
}

export function LineageProvider({
  campaignId,
  cycleId,
  children,
}: {
  campaignId: string | null;
  cycleId: string | null;
  children: React.ReactNode;
}) {
  const [lens, setLens] = useState<string>("");
  // This provider lives at the shell root, so a lens would otherwise leak across campaigns.
  const [prevCampaign, setPrevCampaign] = useState(campaignId);
  if (campaignId !== prevCampaign) {
    setPrevCampaign(campaignId);
    setLens("");
  }

  // Seeded here: any later, and a cycle's first tree goes out unmasked.
  useScoringMaskSeed();
  const { open: maskOpen, mask } = useScoringMask();
  const { sampleSet } = useSelection();
  const samples = useMemo(
    () => (sampleSet && sampleSet.length > 0 ? sampleSet : null),
    [sampleSet],
  );
  const liveMaskLens = useMemo(() => (maskOpen ? lensOf(mask) : null), [maskOpen, mask]);
  const maskLens = useDebounced(liveMaskLens, 250);
  const lensParam = maskOpen ? maskLens : lens || null;
  const maskLabel = maskOpen ? "Scoring mask" : samples ? "Sample set" : (LENS_LABELS[lens] ?? "");

  // Keyed on the derived root id, not `cycleId`: a same-campaign cycle switch keeps one read.
  const rootId = cycleId ? rootCycleId(cycleId) : null;
  const viewedPath = useMemo<CyclePath | null>(() => {
    if (!campaignId || !rootId) return null;
    return [{ campaignId, cycleId: rootId }];
  }, [campaignId, rootId]);
  const viewedAddress = viewedPath ? encodeCyclePath(viewedPath) : null;
  const viewedMask = useMemo<TreeMask | null>(
    () => (lensParam || samples ? { lens: lensParam, samples } : null),
    [lensParam, samples],
  );

  const { cycles } = useRegistry();
  const resting = useMemo(() => {
    const all = new Set<string>();
    const live = new Set<string>();
    for (const c of cycles) {
      all.add(c.campaign_id);
      if (!phaseIs(c.run_phase, "settled")) live.add(c.campaign_id);
    }
    return new Set([...all].filter((id) => !live.has(id)));
  }, [cycles]);

  // The offset is the viewed LEAF's, which the root-keyed tree is not addressed by.
  const { at, viewedPath: leafPath } = useWorkspace();
  const viewedMoment = useMemo<TreeMoment | null>(
    () => (at !== null && leafPath !== null ? { at, cycle: leafPath } : null),
    [at, leafPath],
  );

  const viewedTree = useTree(viewedPath, viewedMask, viewedMoment, resting).root;
  const index = useMemo(() => indexLineage(viewedTree), [viewedTree]);

  const maskActive = useMemo(() => {
    for (const { candidates } of index.values()) {
      if (candidates.some((c) => c.divergence !== null || c.divergent)) return true;
    }
    return false;
  }, [index]);

  const viewed = useMemo<ViewedLineage>(
    () => ({
      tree: viewedTree,
      index,
      lens,
      setLens,
      maskActive,
      maskLabel,
      scoringMaskActive: maskOpen,
    }),
    [viewedTree, index, lens, maskActive, maskLabel, maskOpen],
  );

  const addressing = useMemo<TreeAddressing>(
    () => ({ viewedAddress, viewedMask, viewedMoment, resting }),
    [viewedAddress, viewedMask, viewedMoment, resting],
  );

  return (
    <TreeAddressingContext.Provider value={addressing}>
      <ViewedLineageContext.Provider value={viewed}>{children}</ViewedLineageContext.Provider>
    </TreeAddressingContext.Provider>
  );
}

export function useViewedLineage(): ViewedLineage {
  const ctx = useContext(ViewedLineageContext);
  if (ctx === null) throw new Error("useLineage* must be used within a LineageProvider");
  return ctx;
}

export function useLineageTree(path: CyclePath, enabled: boolean): CampaignTree {
  const ctx = useContext(TreeAddressingContext);
  if (ctx === null) throw new Error("useLineage* must be used within a LineageProvider");
  const viewed = encodeCyclePath(path) === ctx.viewedAddress;
  return useTree(
    enabled ? path : null,
    viewed ? ctx.viewedMask : null,
    viewed ? ctx.viewedMoment : null,
    ctx.resting,
  );
}

// served: `divergence` rides the round's WINNER; `divergent` every candidate of a counterfactual round.
export function divergenceRoundsFor(
  index: LineageIndex,
  path: CyclePath | null,
): { points: ReadonlySet<number>; subtree: ReadonlySet<number> } {
  const points = new Set<number>();
  const subtree = new Set<number>();
  if (!path) return { points, subtree };
  // Addressed, not scanned: inner cycle ids repeat across sandboxes.
  const course = index.get(encodeCyclePath(path))?.course;
  for (const cand of course?.children ?? []) {
    const { round } = cand.reading.arm;
    if (cand.divergence) points.add(round);
    if (cand.divergent) subtree.add(round);
  }
  return { points, subtree };
}
