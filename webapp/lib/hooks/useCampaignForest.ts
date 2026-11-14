import { useCallback, useEffect, useMemo, useState } from "react";
import { commands } from "@/lib/api";
import type { HierarchyCourse, SubtreeCampaign, DisplayMetric } from "@/lib/api/types";
import {
  candidatesAtPath,
  courseAt,
  nodeOverlays,
  primaryMetric,
} from "@/lib/derivations";
import { pathOfAddress, rootCycleId, type CyclePath } from "@/lib/ids";
import { useCommand, type CommandFailure } from "@/lib/hooks/useCommand";
import { useViewedLineage } from "@/lib/state/lineage";
import { useViewMemory } from "@/lib/state/view-memory";
import { useMetrics } from "@/lib/state/candidates";

interface LineageCleanup {
  open: boolean;
  failure: CommandFailure | null;
  cleaning: boolean;
  acked: boolean;
  // Null until the subtree is read: the listing does not count them.
  stubCount: number | null;
  request: () => void;
  cancel: () => void;
  confirm: () => void;
}

interface CampaignForest {
  tree: SubtreeCampaign | null;
  valueByKey: ReadonlyMap<string, number | null>;
  thetaByKey: ReadonlyMap<string, number | null>;
  metric: DisplayMetric;
  // The forks cut from each of the viewed course's candidates, by candidate address.
  forkedFrom: ReadonlyMap<string, HierarchyCourse>;
  expanded: ReadonlySet<string>;
  onLaneActivate: (courseKey: string) => void;
  showForest: boolean;
  setShowForest: (open: boolean) => void;
  revealLane: (courseKey: string) => void;
  totalDescendants: number;
  viewedHasRounds: boolean;
  isInheritedSibling: boolean;
  parentId: string | null;
  cleanup: LineageCleanup;
  // The viewed fork while it holds no round: `delete-cycle` removes that one stub; null otherwise.
  deleteViewed: ((then: () => void) => void) | null;
  deleting: boolean;
  deleteFailure: CommandFailure | null;
}

// The campaign's forest: its lanes and `showForest` are view memory's alone (`CampaignView`).
export function useCampaignForest({
  campaignId,
  cycleId,
  path,
  displayMetric,
}: {
  campaignId: string | null;
  cycleId: string | null;
  displayMetric: DisplayMetric;
  path: CyclePath | null;
}): CampaignForest {
  const { tree, index } = useViewedLineage();
  const { viewFor, recordView } = useViewMemory();
  const { showForest, expandedLanes, expandedForLane } = viewFor(campaignId);
  const metric = primaryMetric(useMetrics(cycleId, displayMetric), displayMetric);

  const viewedLaneKey = useMemo(() => courseAt(index, path)?.address ?? null, [index, path]);
  // The viewed lane opens once per visit; the latch keeps a manual collapse from being undone.
  const opening = viewedLaneKey !== null && viewedLaneKey !== expandedForLane ? viewedLaneKey : null;
  const expanded = useMemo<ReadonlySet<string>>(
    () => new Set(opening ? [...expandedLanes, opening] : expandedLanes),
    [expandedLanes, opening],
  );
  useEffect(() => {
    if (opening) recordView(campaignId, { expandedLanes: [...expanded], expandedForLane: opening });
  }, [opening, expanded, campaignId, recordView]);

  const onLaneActivate = useCallback(
    (key: string) => {
      const next = new Set(expanded);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      recordView(campaignId, { expandedLanes: [...next] });
    },
    [expanded, campaignId, recordView],
  );

  const setShowForest = useCallback(
    (open: boolean) => recordView(campaignId, { showForest: open }),
    [campaignId, recordView],
  );

  // Takes a LANE KEY (a course address); a raw cycle id silently never matches the layout.
  const revealLane = useCallback(
    (courseKey: string) =>
      recordView(campaignId, { showForest: true, expandedLanes: [...new Set(expanded).add(courseKey)] }),
    [expanded, campaignId, recordView],
  );

  const forkedFrom = useMemo<ReadonlyMap<string, HierarchyCourse>>(() => {
    const m = new Map<string, HierarchyCourse>();
    const own = new Set(candidatesAtPath(index, path).map((c) => c.address));
    for (const course of index.courses) {
      const from = course.facts.from_candidate;
      if (from && own.has(from)) m.set(from, course);
    }
    return m;
  }, [index, path]);

  const { valueByKey, thetaByKey } = useMemo(() => nodeOverlays(index, metric), [index, metric]);

  const viewedHasRounds = candidatesAtPath(index, path).length > 0;
  const parentId = cycleId ? rootCycleId(cycleId) : null;
  const isInheritedSibling = parentId != null && parentId !== cycleId;

  const cmd = useCommand<"cleanup-empty-cycles">("lineage-cleanup");
  const del = useCommand<"delete-cycle">("lineage-delete", { scope: cycleId });
  const [cleanupOpen, setCleanupOpen] = useState(false);
  const [cleanupAcked, setCleanupAcked] = useState(false);

  const cleanup: LineageCleanup = {
    open: cleanupOpen,
    failure: cmd.failure,
    cleaning: cmd.pending !== null,
    acked: cleanupAcked,
    stubCount: tree ? tree.facts.deletable_stubs : null,
    request: () => {
      cmd.clear();
      setCleanupOpen(true);
    },
    cancel: () => {
      setCleanupOpen(false);
      cmd.clear();
    },
    confirm: () => {
      const rootId = tree ? pathOfAddress(tree.facts.root).at(-1)?.cycleId : undefined;
      if (!campaignId || !rootId) return;
      void cmd.run("cleanup-empty-cycles", () => commands["cleanup-empty-cycles"]({ campaign_id: campaignId, cycle_id: rootId }), () => {
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
    showForest,
    setShowForest,
    revealLane,
    totalDescendants: Math.max(index.courses.length - 1, 0),
    viewedHasRounds,
    isInheritedSibling,
    parentId,
    cleanup,
    deleteViewed:
      campaignId && cycleId && isInheritedSibling && !viewedHasRounds
        ? (then) =>
            void del.run(
              "delete-cycle",
              () => commands["delete-cycle"]({ campaign_id: campaignId, cycle_id: cycleId }),
              then,
            )
        : null,
    deleting: del.pending !== null,
    deleteFailure: del.failure,
  };
}
