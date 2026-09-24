"use client";
// Forest state: value overlays, fork map, empty-stub cleanup. The tree itself is `LineageProvider`'s;
// the ledger mints a candidate the moment it exists, so the in-flight round needs no stitching.

import { useCallback, useMemo, useState } from "react";
import { postCleanupEmpty } from "@/lib/api";
import type { LineageNode } from "@/lib/api";
import {
  primaryMetric,
  candidatesOf,
  countDescendants,
  nodeKeyOf,
  nodeOverlays,
  type HeadlineMetric,
} from "@/lib/derivations";
import { encodeCyclePath, rootCycleId, type CyclePath } from "@/lib/ids";
import { useCommand } from "@/lib/hooks/useCommand";
import { useViewedLineage } from "@/lib/lineage";
import { useViewMemory } from "@/lib/view-memory";
import { setCandidatesState, useCandidatesState } from "./candidates-store";

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
  tree: LineageNode | null;
  valueByKey: ReadonlyMap<string, number | null>;
  thetaByKey: ReadonlyMap<string, number | null>;
  metric: HeadlineMetric;
  // The node, not its id: a bare cycle id cannot supply `pathOf` or `nodeKeyOf`.
  forkedFrom: ReadonlyMap<string, LineageNode>;
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
  // Passed in: this hook touches no `dashboard.json`.
  electedMetric: HeadlineMetric;
  path: CyclePath | null;
}): Lineage {
  const { tree, index } = useViewedLineage();
  const { viewFor, recordView } = useViewMemory();
  const { metrics, expanded, expandedForCampaign, expandedForLane } = useCandidatesState();
  const metric = primaryMetric(metrics, electedMetric);

  const viewedKey = path ? encodeCyclePath(path) : "";
  // Null when the viewed course is a FORK: its candidates ride the parent's lane.
  const viewedLaneKey = useMemo(() => {
    const viewed = index.get(viewedKey)?.course;
    return viewed ? nodeKeyOf(viewed) : null;
  }, [index, viewedKey]);

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

  // Recorded from the handler, never from render.
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

  // Takes a LANE KEY (`nodeKeyOf`); a raw cycle id silently never matches the layout.
  const revealLane = useCallback(
    (courseKey: string) => {
      const next = new Set(expanded).add(courseKey);
      setCandidatesState({ showForest: true, expanded: next });
      recordView(campaignId, { showForest: true, expandedLanes: [...next] });
    },
    [expanded, campaignId, recordView],
  );

  const courses = useMemo(
    () => [...index.values()].flatMap((e) => (e.course ? [e.course] : [])),
    [index],
  );

  const forkedFrom = useMemo<ReadonlyMap<string, LineageNode>>(() => {
    const m = new Map<string, LineageNode>();
    // Own-path `candidates`, never `course`: the server dissolves a FORK onto its parent's timeline.
    for (const cand of index.get(viewedKey)?.candidates ?? []) {
      for (const child of cand.children) {
        if (child.kind === "course") m.set(cand.id, child);
      }
    }
    return m;
  }, [index, viewedKey]);

  const { valueByKey, thetaByKey } = useMemo(
    () => nodeOverlays(courses, metric === "composite"),
    [courses, metric],
  );

  const viewedHasRounds = (index.get(viewedKey)?.candidates.length ?? 0) > 0;
  const parentId = cycleId ? rootCycleId(cycleId) : null;
  const isInheritedSibling = parentId != null && parentId !== cycleId;

  const stubCount = useMemo(
    () =>
      courses.filter((c) => c.course_kind !== "root" && candidatesOf(c).length === 0).length,
    [courses],
  );

  const cmd = useCommand<"cleanup-empty-cycles">("lineage-cleanup");
  const [cleanupOpen, setCleanupOpen] = useState(false);
  const [cleanupAcked, setCleanupAcked] = useState(false);

  const cleanup: LineageCleanup = {
    open: cleanupOpen,
    error: cmd.failure?.message ?? null,
    cleaning: cmd.pending !== null,
    acked: cleanupAcked,
    stubCount,
    request: () => {
      cmd.clear();
      setCleanupOpen(true);
    },
    cancel: () => {
      setCleanupOpen(false);
      cmd.clear();
    },
    confirm: async () => {
      const rootId = tree?.id;
      if (!campaignId || !rootId) return;
      await cmd.run("cleanup-empty-cycles", () => postCleanupEmpty(campaignId, rootId), () => {
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
    totalDescendants: tree ? countDescendants(tree) : 0,
    viewedHasRounds,
    isInheritedSibling,
    parentId,
    cleanup,
  };
}
