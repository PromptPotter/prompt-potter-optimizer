"use client";
// State for the candidates card's Forest view. Everything that is NOT geometry
// (forest-layout.ts), NOT markup (CandidatesCard/Forest), and NOT the shared
// fetch/overlay (lib/lineage-overlay) lives here: the per-candidate value overlays,
// the fork map, and the empty-stub cleanup mutation. The tree itself and the
// mask/lens counterfactual are owned by `LineageOverlayProvider` and read via
// `useLineageOverlay()` — the SINGLE source both this view and the bars render.
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
import { accuracyBasisValue } from "@/lib/fitness";
import {
  primaryMetric,
  roundCandidates,
  walkCourses,
  type HeadlineMetric,
} from "@/lib/derivations";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { rootCycleId } from "@/lib/ids";
import { bumpRevalidation } from "@/lib/revalidate";
import { useLineageOverlay } from "@/lib/lineage-overlay";
import type { CandidateRow } from "@/lib/types";
import { setCandidatesState, useCandidatesState } from "./candidates-store";
import { candidatesOf, countDescendants } from "./forest-layout";

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
  // Per-candidate fitness, keyed `{cycleId}::{candidateId}` — the live value
  // overlay painted onto nodes. NOT part of the geometry (so a value tick never
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
  // `candidate_id` → the course hanging off it (in-view cycle only). Drives the ⑂
  // mark in the Sequence view, whose click frees the hierarchy into the Forest.
  forkedFrom: ReadonlyMap<string, string>;
  expanded: ReadonlySet<string>;
  // In-place expand/collapse toggle for one course's lane (pure view state —
  // never changes the dashboard's selected cycle).
  onLaneActivate: (cycleId: string) => void;
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
}: {
  campaignId: string | null;
  cycleId: string | null;
}): Lineage {
  // The shared tree from the single fetch both views render (R-36). The mask/lens
  // fields are NOT re-exposed here — the card and `Forest` read the counterfactual
  // off the nodes themselves, so this hook owns only the value/fork/cleanup state.
  const { tree } = useLineageOverlay();
  // The in-view cycle's live dashboard — the same source the bars read, so the
  // active cycle's node values can't disagree with them.
  const { dash } = useDashboard();
  const { metrics, expanded, expandedForCampaign, expandedForCycle } = useCandidatesState();
  const metric = primaryMetric(metrics);

  // The active (in-view) cycle's live candidate rows from dashboard.json —
  // includes the in-flight round's per-sample value movement.
  const liveRows = useMemo<CandidateRow[]>(
    () => (cycleId && dash ? roundCandidates(dash) : []),
    [cycleId, dash],
  );

  // Render-phase expand reset (React's sanctioned adjust-state-on-prop-change).
  // A campaign switch resets to the clean view (the in-view cycle expanded);
  // selecting another fork ensure-expands it. The `expandedForCycle` latch is
  // what stops a manual collapse of the in-view lane from being re-expanded on
  // the next render.
  if (campaignId !== expandedForCampaign) {
    setCandidatesState({
      expanded: cycleId ? new Set([cycleId]) : new Set(),
      expandedForCampaign: campaignId,
      expandedForCycle: cycleId,
    });
  } else if (cycleId && cycleId !== expandedForCycle) {
    setCandidatesState({
      expanded: new Set(expanded).add(cycleId),
      expandedForCycle: cycleId,
    });
  }

  const onLaneActivate = useCallback((cid: string) => {
    const next = new Set(expanded);
    if (next.has(cid)) next.delete(cid);
    else next.add(cid);
    setCandidatesState({ expanded: next });
  }, [expanded]);

  const courses = useMemo(() => (tree ? walkCourses(tree) : []), [tree]);

  // Which candidates of the IN-VIEW course a child course hangs off. The Sequence
  // view has no lanes to show one in — it plots one course — so it marks the
  // candidate the course was cut from and offers the jump into the Forest, where the
  // sibling actually has somewhere to be drawn.
  const forkedFrom = useMemo<ReadonlyMap<string, string>>(() => {
    const m = new Map<string, string>();
    const viewed = courses.find((c) => c.id === cycleId);
    if (!viewed) return m;
    for (const cand of candidatesOf(viewed)) {
      for (const child of cand.children) {
        if (child.kind === "course") m.set(cand.id, child.id);
      }
    }
    return m;
  }, [courses, cycleId]);

  // Per-candidate percent-metric overlay, keyed `{cycleId}::{candidateId}`. The tree
  // serves `composite_fitness` per candidate, so settled/sibling courses honor the
  // composite selection on the SAME basis as the active cycle — one tree, one basis,
  // nothing recomputed client-side. θ is a separate overlay (`thetaByKey`) since it
  // is a logit, not a percent. Deliberately NOT content-stabilized: it updates every
  // poll, but only painted node text reads it.
  const usesComposite = metric === "composite";
  const valueByKey = useMemo<ReadonlyMap<string, number | null>>(() => {
    const m = new Map<string, number | null>();
    // Accuracy view: the WINNER (lineage spine) paints the round's cumulative
    // frontier — the cross-round-comparable series the trend plots — so the spine
    // reads as honest progress, not the per-round subset swing. Losers keep their
    // own subset score.
    for (const course of courses) {
      for (const cand of candidatesOf(course)) {
        const value = usesComposite
          ? cand.composite_fitness
          : accuracyBasisValue(cand.is_winner, cand.cumulative_accuracy, cand.accuracy);
        m.set(`${course.id}::${cand.id}`, value);
      }
    }
    // The in-view course's values track the 2 s poll, so its in-flight round moves
    // in step with the bars rather than waiting on the tree's revalidation.
    if (cycleId && dash) {
      for (const row of liveRows) {
        const value = usesComposite
          ? row.composite ?? null
          : accuracyBasisValue(row.is_winner, row.cumulative_accuracy, row.accuracy);
        m.set(`${cycleId}::${row.candidate_id}`, value);
      }
    }
    return m;
  }, [courses, cycleId, dash, liveRows, usesComposite]);

  // Parallel overlay carrying each candidate's difficulty-adjusted ability θ, same key shape
  // as `valueByKey`. Painted into the node tooltip so a θ-elected winner shown below a
  // higher-accuracy sibling is explainable on the node itself. `null` where there's no
  // election fit (in-flight / eliminated).
  const thetaByKey = useMemo<ReadonlyMap<string, number | null>>(() => {
    const m = new Map<string, number | null>();
    for (const course of courses) {
      for (const cand of candidatesOf(course)) m.set(`${course.id}::${cand.id}`, cand.theta);
    }
    if (cycleId && dash) {
      for (const row of liveRows) m.set(`${cycleId}::${row.candidate_id}`, row.theta);
    }
    return m;
  }, [courses, cycleId, dash, liveRows]);

  // Empty-state facts for the in-view cycle (distinguishes an inherited fork
  // from a fresh cycle waiting for round 1).
  const viewedHasRounds = useMemo(() => {
    const viewed = courses.find((c) => c.id === cycleId);
    return viewed ? candidatesOf(viewed).length > 0 : false;
  }, [courses, cycleId]);
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
    totalDescendants: tree ? countDescendants(tree) : 0,
    viewedHasRounds,
    isInheritedSibling,
    parentId,
    cleanup,
  };
}
