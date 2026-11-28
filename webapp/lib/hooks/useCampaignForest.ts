import { useCallback, useEffect, useMemo, useState } from "react";
import { commands } from "@/lib/api";
import type { HierarchyCycle, SubtreeCampaign, DisplayMetric } from "@/lib/api/types";
import {
  candidatesAtPath,
  cycleAt,
  nodeOverlays,
  primaryMetric,
} from "@/lib/derivations";
import { pathOfAddress, type CyclePath } from "@/lib/ids";
import { useCommand, type CommandFailure } from "@/lib/hooks/useCommand";
import { useFiles } from "@/lib/hooks/useFiles";
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
  metric: DisplayMetric;
  // The forks cut from each of the viewed cycle's candidates, by candidate address.
  forkedFrom: ReadonlyMap<string, HierarchyCycle>;
  expanded: ReadonlySet<string>;
  onLaneActivate: (cycleKey: string) => void;
  // The lineage tree detached from the candidates bars into its own card; else paired under them.
  treeFreed: boolean;
  setTreeFreed: (freed: boolean) => void;
  revealLane: (cycleKey: string) => void;
  // What the tree draws, as the subtree read counts it (`SubtreeCampaignFacts.candidates`); null until read.
  candidates: number | null;
  viewedHasRounds: boolean;
  // The viewed fork's parent as the tree serves it (`facts.spawn.parent`); null for a root.
  forkParent: HierarchyCycle | null;
  cleanup: LineageCleanup;
  // The viewed fork while it holds no round: `delete-cycle` removes that one stub; null otherwise.
  deleteViewed: ((then: () => void) => void) | null;
  deleting: boolean;
  deleteFailure: CommandFailure | null;
}

// The campaign's forest: its lanes and `treeFreed` are view memory's alone (`CampaignView`).
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
  const { treeFreed, expandedLanes, expandedForLane } = viewFor(campaignId);
  const metric = primaryMetric(useMetrics(cycleId, displayMetric), displayMetric);

  const viewedLaneKey = useMemo(() => cycleAt(index, path)?.address ?? null, [index, path]);
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

  const setTreeFreed = useCallback(
    (freed: boolean) => recordView(campaignId, { treeFreed: freed }),
    [campaignId, recordView],
  );

  // Takes a LANE KEY (a cycle address); a raw cycle id silently never matches the layout.
  const revealLane = useCallback(
    (cycleKey: string) =>
      recordView(campaignId, { treeFreed: true, expandedLanes: [...new Set(expanded).add(cycleKey)] }),
    [expanded, campaignId, recordView],
  );

  const forkedFrom = useMemo<ReadonlyMap<string, HierarchyCycle>>(() => {
    const m = new Map<string, HierarchyCycle>();
    const own = new Set(candidatesAtPath(index, path).map((c) => c.address));
    for (const cycle of index.cycles) {
      const from = cycle.facts.from_candidate;
      if (from && own.has(from)) m.set(from, cycle);
    }
    return m;
  }, [index, path]);

  const { valueByKey } = useMemo(() => nodeOverlays(index, metric), [index, metric]);

  const viewedHasRounds = candidatesAtPath(index, path).length > 0;
  const spawn = cycleAt(index, path)?.facts.spawn ?? null;
  const forkParent = spawn ? (index.cycles.find((c) => c.address === spawn.parent) ?? null) : null;

  // No read offers the stub's delete: its reader's kinds say whether it is drawn.
  const files = useFiles(path);
  const cmd = useCommand<"cleanup-empty-cycles">("lineage-cleanup", campaignId);
  const del = useCommand<"delete-cycle">("lineage-delete", cycleId);
  // The dialog and its answer are the campaign's they were pressed on: another campaign reads neither.
  const [openOn, setOpenOn] = useState<string | null>(null);
  const [ackedOn, setAckedOn] = useState<string | null>(null);

  const cleanup: LineageCleanup = {
    open: openOn !== null && openOn === campaignId,
    failure: cmd.failure,
    cleaning: cmd.pending !== null,
    acked: ackedOn !== null && ackedOn === campaignId,
    stubCount: tree ? tree.facts.deletable_stubs : null,
    request: () => {
      cmd.clear();
      setOpenOn(campaignId);
    },
    cancel: () => {
      setOpenOn(null);
      cmd.clear();
    },
    confirm: () => {
      const rootId = tree ? pathOfAddress(tree.facts.root).at(-1)?.cycleId : undefined;
      if (!campaignId || !rootId) return;
      void cmd.run("cleanup-empty-cycles", () => commands["cleanup-empty-cycles"]({ campaign_id: campaignId, cycle_id: rootId }), () => {
        setAckedOn(campaignId);
        setOpenOn(null);
      });
    },
  };

  return {
    tree,
    valueByKey,
    metric,
    forkedFrom,
    expanded,
    onLaneActivate,
    treeFreed,
    setTreeFreed,
    revealLane,
    candidates: tree ? tree.facts.candidates : null,
    viewedHasRounds,
    forkParent,
    cleanup,
    deleteViewed:
      campaignId && cycleId && spawn && !viewedHasRounds && files("delete-cycle")
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
