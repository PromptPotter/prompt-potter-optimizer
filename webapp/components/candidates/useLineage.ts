"use client";
// State for the candidates card's Forest view. Everything that is NOT geometry
// (forest-layout.ts), NOT markup (CandidatesCard/Forest), and NOT the shared
// fetch/overlay (lib/lineage) lives here: the per-candidate value overlays,
// the fork map, and the empty-stub cleanup mutation. The tree itself and the
// mask/lens counterfactual are owned by `LineageProvider` and read via
// `useViewedLineage()` — the SINGLE source both this view and the bars render.
//
// **The structure is the served tree, and nothing else** — no source-by-cycle-role
// merge. The tree rides the LEDGER, which mints a candidate the moment it exists, so
// the in-flight round is already in it and there is nothing to stitch. The live
// dashboard stays what it is: the per-sample VALUE overlay (`valueByKey`), painted at
// render so a value tick never re-runs the layout memo.
//
// View state (which metric, which lanes are open) is NOT here — it belongs to the
// card as a whole and lives in `candidates-store`, so the Sequence and Forest
// views cannot disagree about which number they are painting.

import { useCallback, useMemo, useState } from "react";
import { postCleanupEmpty } from "@/lib/api";
import type { LineageNode } from "@/lib/api";
import {
  primaryMetric,
  roundCandidates,
  candidatesOf,
  countDescendants,
  nodeKeyOf,
  type HeadlineMetric,
} from "@/lib/derivations";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { encodeCyclePath, rootCycleId, type CyclePath } from "@/lib/ids";
import { bumpRevalidation } from "@/lib/revalidate";
import { useViewedLineage } from "@/lib/lineage";
import { useViewMemory } from "@/lib/view-memory";
import type { CandidateRow } from "@/lib/types";
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
  // Per-candidate fitness, keyed by the candidate's ADDRESS (`nodeKeyOf`) — the live
  // value overlay painted onto nodes. NOT part of the geometry (so a value tick never
  // re-lays-out the tree). Carries the percent metric the operator selected;
  // θ rides `thetaByKey`. `undefined` for a node with no value yet.
  valueByKey: ReadonlyMap<string, number | null>;
  // Same-key overlay of difficulty-adjusted ability θ — painted into node tooltips so a
  // θ-elected winner shown below a higher-accuracy sibling is explainable in place.
  thetaByKey: ReadonlyMap<string, number | null>;
  // The card's primary metric — what a node LABEL shows. The bars can plot
  // several metrics at once; a node paints one number, so it takes the first
  // selected in canonical order.
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
  // THE write path for `showForest`: applies it and records it in one call, so no toggle
  // site can set the store without the memory write (a missed record re-seeds stale state
  // on the next campaign switch).
  setShowForest: (open: boolean) => void;
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
}: {
  campaignId: string | null;
  cycleId: string | null;
  // The VIEWED course's address. `cycleId` alone cannot name a node: inner ids repeat
  // across sibling sandboxes, so every tree lookup and every overlay key rides this.
  path: CyclePath | null;
}): Lineage {
  // The shared tree from the single fetch both views render (R-36). The mask/lens
  // fields are NOT re-exposed here — the card and `Forest` read the counterfactual
  // off the nodes themselves, so this hook owns only the value/fork/cleanup state.
  const { tree, index } = useViewedLineage();
  // Per-campaign view memory — seeds the expand state below, and records what the operator
  // changes here so the next visit opens the same way.
  const { viewFor, recordView } = useViewMemory();
  // The in-view cycle's live dashboard — the same source the bars read, so the
  // active cycle's node values can't disagree with them.
  const { dash } = useDashboard();
  const { metrics, expanded, expandedForCampaign, expandedForLane } = useCandidatesState();
  const metric = primaryMetric(metrics);

  // The active (in-view) cycle's live candidate rows from dashboard.json —
  // includes the in-flight round's per-sample value movement.
  const liveRows = useMemo<CandidateRow[]>(
    () => (cycleId && dash ? roundCandidates(dash) : []),
    [cycleId, dash],
  );

  // The viewed course's address, and the prefix every live row's key is built from —
  // `nodeKeyOf` is `{encoded path}|{id}`, and a live row's candidate sits on this path.
  const viewedKey = path ? encodeCyclePath(path) : "";
  // The viewed course's LANE key. Null before the tree lands, and null when the viewed
  // course is a FORK — a fork is not a node, its candidates ride the parent's lane.
  const viewedLaneKey = useMemo(() => {
    const viewed = index.get(viewedKey)?.course;
    return viewed ? nodeKeyOf(viewed) : null;
  }, [index, viewedKey]);

  // Render-phase expand reset (React's sanctioned adjust-state-on-prop-change).
  // A campaign switch resets to the clean view (the in-view lane expanded); selecting
  // another fork ensure-expands it. The `expandedForLane` latch is what stops a manual
  // collapse of the in-view lane from being re-expanded on the next render.
  //
  // Keyed on the LANE, which only the tree can name — so a campaign switch clears here
  // and the default expansion settles on the render the tree lands, rather than being
  // guessed from `cycleId` (which is not a lane key: inner ids repeat).
  //
  // On a campaign switch the seed is what the operator LEFT expanded here (view memory,
  // lane keys — the same `nodeKeyOf` space `forest-layout::layout` matches), unioned with
  // the in-view lane. Read during render via `useSyncExternalStore`, so the restore and the
  // reset commit together and no frame shows the un-restored state.
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

  // Per-candidate percent-metric overlay, keyed by `nodeKeyOf`. The tree
  // serves `composite_fitness` per candidate, so settled/sibling courses honor the
  // composite selection on the SAME basis as the active cycle — one tree, one basis,
  // nothing recomputed client-side. θ is a separate overlay (`thetaByKey`) since it
  // is a logit, not a percent. Deliberately NOT content-stabilized: it updates every
  // poll, but only painted node text reads it.
  const usesComposite = metric === "composite";
  const valueByKey = useMemo<ReadonlyMap<string, number | null>>(() => {
    const m = new Map<string, number | null>();
    // Accuracy view: every node paints what IT measured. The winner used to paint the
    // round's "cumulative frontier" instead — a pool of rows scored by different
    // configurations — so the spine read higher than anything the run had measured, and
    // a bar's height disagreed with the node text beneath it. One basis now: `accuracy`.
    for (const course of courses) {
      for (const cand of candidatesOf(course)) {
        const value = usesComposite
          ? cand.composite_fitness
          : cand.accuracy ?? null;
        m.set(nodeKeyOf(cand), value);
      }
    }
    // The in-view course's values track the 2 s poll, so its in-flight round moves
    // in step with the bars rather than waiting on the tree's revalidation.
    if (cycleId && dash) {
      for (const row of liveRows) {
        const value = usesComposite
          ? row.composite ?? null
          : row.accuracy ?? null;
        m.set(`${viewedKey}|${row.candidate_id}`, value);
      }
    }
    return m;
  }, [courses, cycleId, dash, liveRows, usesComposite, viewedKey]);

  // Parallel overlay carrying each candidate's difficulty-adjusted ability θ, same key shape
  // as `valueByKey`. Painted into the node tooltip so a θ-elected winner shown below a
  // higher-accuracy sibling is explainable on the node itself. `null` where there's no
  // election fit (in-flight / eliminated).
  const thetaByKey = useMemo<ReadonlyMap<string, number | null>>(() => {
    const m = new Map<string, number | null>();
    for (const course of courses) {
      for (const cand of candidatesOf(course)) m.set(nodeKeyOf(cand), cand.theta);
    }
    if (cycleId && dash) {
      for (const row of liveRows) m.set(`${viewedKey}|${row.candidate_id}`, row.theta);
    }
    return m;
  }, [courses, cycleId, dash, liveRows, viewedKey]);

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
    totalDescendants: tree ? countDescendants(tree) : 0,
    viewedHasRounds,
    isInheritedSibling,
    parentId,
    cleanup,
  };
}
