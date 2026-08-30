"use client";
// State for the candidates card's Forest view: the per-candidate value overlays, the fork map,
// and the empty-stub cleanup mutation. Geometry is `forest-layout.ts`, markup is Forest, and the
// tree plus its mask counterfactual belong to `LineageProvider` — the SINGLE source both this
// view and the bars render.
//
// **The structure is the served tree, and nothing else**: it rides the LEDGER, which mints a
// candidate the moment it exists, so the in-flight round is already in it and there is nothing
// to stitch. View state (which metric, which lanes are open) lives in `candidates-store`.

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
import { bumpRevalidation } from "@/lib/revalidate";
import { useViewedLineage } from "@/lib/lineage";
import { useViewMemory } from "@/lib/view-memory";
import { setCandidatesState, useCandidatesState } from "./candidates-store";

// Empty-stub cleanup — one campaign-wide modal mutation. Stubs accumulate
// because fork-creation paths mint the cycle dir BEFORE the first round runs;
// an interrupt between dir-mint and first-round leaves an empty-row fork.
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
  // THE served genealogy's root course, or null before the first read lands.
  tree: LineageNode | null;
  // Per-candidate fitness, keyed by the candidate's ADDRESS (`nodeKeyOf`) — the value
  // overlay painted onto nodes. NOT part of the geometry (so a value tick never
  // re-lays-out the tree). Carries the percent metric the operator selected;
  // θ rides `thetaByKey`. `undefined` for a node with no value yet.
  valueByKey: ReadonlyMap<string, number | null>;
  // Same-key overlay of difficulty-adjusted ability θ — painted into node tooltips so a
  // θ-elected winner shown below a higher-accuracy sibling is explainable in place.
  thetaByKey: ReadonlyMap<string, number | null>;
  // The card's primary metric — what a node LABEL shows. The bars can plot several at once;
  // a node paints one, and it is the one the campaign elects on wherever that is shown.
  metric: HeadlineMetric;
  // candidate id → the child COURSE NODE hanging off it (in-view cycle only) — drives the
  // ⑂ mark in the Sequence view, whose click frees the hierarchy into the Forest. The
  // node rather than its id: the consumer needs an address (`pathOf`) and a lane key
  // (`nodeKeyOf`), and a bare cycle id can supply neither — inner ids repeat across
  // sibling sandboxes.
  forkedFrom: ReadonlyMap<string, LineageNode>;
  expanded: ReadonlySet<string>;
  // In-place expand/collapse toggle for one course's lane (pure view state —
  // never changes the dashboard's selected cycle).
  onLaneActivate: (courseKey: string) => void;
  // A write path for `showForest`: applies it and records it in one call, so no toggle
  // site can set the store without the memory write (a missed record re-seeds stale state
  // on the next campaign switch).
  setShowForest: (open: boolean) => void;
  // The other one — reveal the forest with a lane already open, which is the whole of the ⑂
  // click. Here rather than at the call site because `showForest` and `expanded` have to move
  // together with ONE memory record; the caller that spelled both writes itself was a second
  // write path past the pairing this hook exists to guarantee.
  revealLane: (courseKey: string) => void;
  totalDescendants: number;
  // Empty-state facts for the in-view cycle.
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
  // Served `dash.headline_metric` — passed in rather than read here, because this hook
  // deliberately touches no `dashboard.json`.
  electedMetric: HeadlineMetric;
  // The VIEWED course's address. `cycleId` alone cannot name a node: inner ids repeat
  // across sibling sandboxes, so every tree lookup and every overlay key rides this.
  path: CyclePath | null;
}): Lineage {
  // The shared tree from the single fetch both views render. The mask/lens
  // fields are NOT re-exposed here — the card and `Forest` read the counterfactual
  // off the nodes themselves, so this hook owns only the value/fork/cleanup state.
  const { tree, index } = useViewedLineage();
  // Per-campaign view memory — seeds the expand state below, and records what the operator
  // changes here so the next visit opens the same way.
  const { viewFor, recordView } = useViewMemory();
  const { metrics, expanded, expandedForCampaign, expandedForLane } = useCandidatesState();
  const metric = primaryMetric(metrics, electedMetric);

  // The viewed course's address — what every lookup into the tree index is keyed by.
  const viewedKey = path ? encodeCyclePath(path) : "";
  // The viewed course's LANE key. Null before the tree lands, and null when the viewed
  // course is a FORK — a fork is not a node, its candidates ride the parent's lane.
  const viewedLaneKey = useMemo(() => {
    const viewed = index.get(viewedKey)?.course;
    return viewed ? nodeKeyOf(viewed) : null;
  }, [index, viewedKey]);

  // Render-phase expand reset (React's sanctioned adjust-state-on-prop-change), keyed on the
  // LANE, which only the tree can name — `cycleId` is not a lane key, since inner ids repeat.
  // The `expandedForLane` latch stops a manual collapse being re-expanded next render. The seed
  // is view memory unioned with the in-view lane, read during render so restore and reset commit
  // together and no frame shows the un-restored state.
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

  // Toggling a lane both applies it and remembers it — recorded from the HANDLER, never
  // from render, so a re-render can't write storage.
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

  // `expanded` holds LANE KEYS (`nodeKeyOf` = `{encoded path}|{id}`), which is what
  // `forest-layout::layout` matches on. A raw cycle id here grows the set a key the layout
  // can never match, and the click then opens an unexpanded forest in silence.
  const revealLane = useCallback(
    (courseKey: string) => {
      const next = new Set(expanded).add(courseKey);
      setCandidatesState({ showForest: true, expanded: next });
      recordView(campaignId, { showForest: true, expandedLanes: [...next] });
    },
    [expanded, campaignId, recordView],
  );

  // Every course, root first — the index preserves the walk's DFS order.
  const courses = useMemo(
    () => [...index.values()].flatMap((e) => (e.course ? [e.course] : [])),
    [index],
  );

  // Which candidates of the IN-VIEW course a child course hangs off. The Sequence
  // view has no lanes to show one in — it plots one course — so it marks the
  // candidate the course was cut from and offers the jump into the Forest, where the
  // sibling actually has somewhere to be drawn.
  const forkedFrom = useMemo<ReadonlyMap<string, LineageNode>>(() => {
    const m = new Map<string, LineageNode>();
    // The entry's own-path `candidates`, never its `course` — a FORK has no course node
    // (the server dissolves it onto the parent's timeline), so a course lookup answers
    // `undefined` and this map would come back empty for exactly the case it serves.
    for (const cand of index.get(viewedKey)?.candidates ?? []) {
      for (const child of cand.children) {
        if (child.kind === "course") m.set(cand.id, child);
      }
    }
    return m;
  }, [index, viewedKey]);

  // The node overlays. The tree serves `composite_fitness` per candidate, so settled and
  // sibling courses honor the composite selection on the SAME basis as the active cycle —
  // one tree, one basis, nothing recomputed client-side.
  const { valueByKey, thetaByKey } = useMemo(
    () => nodeOverlays(courses, metric === "composite"),
    [courses, metric],
  );

  // Empty-state facts for the in-view cycle (distinguishes an inherited fork
  // from a fresh cycle waiting for round 1). Own-path candidates, not a course lookup —
  // a fork has no course node, and one that produced candidates must not read as empty.
  const viewedHasRounds = (index.get(viewedKey)?.candidates.length ?? 0) > 0;
  const parentId = cycleId ? rootCycleId(cycleId) : null;
  const isInheritedSibling = parentId != null && parentId !== cycleId;

  const stubCount = useMemo(
    () =>
      courses.filter((c) => c.course_kind !== "root" && candidatesOf(c).length === 0).length,
    [courses],
  );

  // Empty-stub cleanup mutation + its modal state.
  const [cleanupOpen, setCleanupOpen] = useState(false);
  const [cleanupError, setCleanupError] = useState<string | null>(null);
  const [cleaning, setCleaning] = useState(false);
  const [cleanupAcked, setCleanupAcked] = useState(false);

  const confirmCleanup = useCallback(async () => {
    const rootId = tree?.id;
    if (!campaignId || !rootId) return;
    setCleaning(true);
    setCleanupError(null);
    try {
      await postCleanupEmpty(campaignId, rootId);
      setCleanupAcked(true);
      setCleanupOpen(false);
      bumpRevalidation();
    } catch (err) {
      setCleanupError((err as Error).message);
    } finally {
      setCleaning(false);
    }
  }, [campaignId, tree]);

  const cleanup: LineageCleanup = {
    open: cleanupOpen,
    error: cleanupError,
    cleaning,
    acked: cleanupAcked,
    stubCount,
    request: useCallback(() => {
      setCleanupError(null);
      setCleanupOpen(true);
    }, []),
    cancel: useCallback(() => {
      setCleanupOpen(false);
      setCleanupError(null);
    }, []),
    confirm: confirmCleanup,
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
