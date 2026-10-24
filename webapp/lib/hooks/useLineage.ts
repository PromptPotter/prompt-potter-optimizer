"use client";

import { useCallback, useMemo, useState } from "react";
import { postCommand } from "@/lib/api";
import type { HierarchyCampaign, HierarchyCourse } from "@/lib/api";
import {
  candidatesAtPath,
  courseAt,
  nodeOverlays,
  primaryMetric,
  type DisplayMetric,
} from "@/lib/derivations";
import { pathOfAddress, rootCycleId, type CyclePath } from "@/lib/ids";
import { useCommand } from "@/lib/hooks/useCommand";
import { useViewedLineage } from "@/lib/lineage";
import { useViewMemory } from "@/lib/view-memory";
import { setCandidatesState, useCandidatesState } from "@/lib/candidates-store";

interface LineageCleanup {
  open: boolean;
  error: string | null;
  cleaning: boolean;
  acked: boolean;
  stubCount: number;
  request: () => void;
  cancel: () => void;
  confirm: () => Promise<void>;
}

export interface Lineage {
  tree: HierarchyCampaign | null;
  valueByKey: ReadonlyMap<string, number | null>;
  thetaByKey: ReadonlyMap<string, number | null>;
  metric: DisplayMetric;
  // The forks cut from each of the viewed course's candidates, by individual id.
  forkedFrom: ReadonlyMap<string, HierarchyCourse>;
  expanded: ReadonlySet<string>;
  onLaneActivate: (courseKey: string) => void;
  // The only write path for `showForest`: store and view memory move together.
  setShowForest: (open: boolean) => void;
  revealLane: (courseKey: string) => void;
  totalDescendants: number;
  viewedHasRounds: boolean;
  isInheritedSibling: boolean;
  parentId: string | null;
  cleanup: LineageCleanup;
}

export function useLineage({
  campaignId,
  cycleId,
  path,
  electedMetric,
}: {
  campaignId: string | null;
  cycleId: string | null;
  electedMetric: DisplayMetric;
  path: CyclePath | null;
}): Lineage {
  const { tree, index } = useViewedLineage();
  const { viewFor, recordView } = useViewMemory();
  const { metrics, expanded, expandedForCampaign, expandedForLane } = useCandidatesState();
  const metric = primaryMetric(metrics, electedMetric);

  const viewedLaneKey = useMemo(() => courseAt(index, path)?.address ?? null, [index, path]);

  if (campaignId !== expandedForCampaign) {
    const remembered = viewFor(campaignId).expandedLanes;
    setCandidatesState({
      expanded: new Set([...remembered, ...(viewedLaneKey ? [viewedLaneKey] : [])]),
      expandedForCampaign: campaignId,
      expandedForLane: viewedLaneKey,
      showForest: viewFor(campaignId).showForest,
    });
  } else if (viewedLaneKey && viewedLaneKey !== expandedForLane) {
    setCandidatesState({
      expanded: new Set(expanded).add(viewedLaneKey),
      expandedForLane: viewedLaneKey,
    });
  }

  const onLaneActivate = useCallback(
    (key: string) => {
      const next = new Set(expanded);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      setCandidatesState({ expanded: next });
      recordView(campaignId, { expandedLanes: [...next] });
    },
    [expanded, campaignId, recordView],
  );

  const setShowForest = useCallback(
    (open: boolean) => {
      setCandidatesState({ showForest: open });
      recordView(campaignId, { showForest: open });
    },
    [campaignId, recordView],
  );

  // Takes a LANE KEY (a course address); a raw cycle id silently never matches the layout.
  const revealLane = useCallback(
    (courseKey: string) => {
      const next = new Set(expanded).add(courseKey);
      setCandidatesState({ showForest: true, expanded: next });
      recordView(campaignId, { showForest: true, expandedLanes: [...next] });
    },
    [expanded, campaignId, recordView],
  );

  const forkedFrom = useMemo<ReadonlyMap<string, HierarchyCourse>>(() => {
    const m = new Map<string, HierarchyCourse>();
    const own = new Map(candidatesAtPath(index, path).map((c) => [c.address, c]));
    for (const course of index.courses) {
      const from = own.get(course.facts.spawn?.from_candidate ?? "");
      if (from) m.set(from.facts.individual, course);
    }
    return m;
  }, [index, path]);

  const { valueByKey, thetaByKey } = useMemo(() => nodeOverlays(index, metric), [index, metric]);

  const viewedHasRounds = candidatesAtPath(index, path).length > 0;
  const parentId = cycleId ? rootCycleId(cycleId) : null;
  const isInheritedSibling = parentId != null && parentId !== cycleId;

  const cmd = useCommand<"cleanup-empty-cycles">("lineage-cleanup");
  const [cleanupOpen, setCleanupOpen] = useState(false);
  const [cleanupAcked, setCleanupAcked] = useState(false);

  const cleanup: LineageCleanup = {
    open: cleanupOpen,
    error: cmd.failure?.message ?? null,
    cleaning: cmd.pending !== null,
    acked: cleanupAcked,
    stubCount: tree?.facts.deletable_stubs ?? 0,
    request: () => {
      cmd.clear();
      setCleanupOpen(true);
    },
    cancel: () => {
      setCleanupOpen(false);
      cmd.clear();
    },
    confirm: async () => {
      const rootId = tree ? pathOfAddress(tree.facts.root).at(-1)?.cycleId : undefined;
      if (!campaignId || !rootId) return;
      await cmd.run("cleanup-empty-cycles", () => postCommand("cleanup-empty-cycles", { campaign_id: campaignId, cycle_id: rootId }), () => {
        setCleanupAcked(true);
        setCleanupOpen(false);
      });
    },
  };

  return {
    tree,
    valueByKey,
    thetaByKey,
    metric,
    forkedFrom,
    expanded,
    onLaneActivate,
    setShowForest,
    revealLane,
    totalDescendants: Math.max(index.courses.length - 1, 0),
    viewedHasRounds,
    isInheritedSibling,
    parentId,
    cleanup,
  };
}
