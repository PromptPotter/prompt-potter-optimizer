"use client";
// Data + state for the campaign lineage card. Everything that is NOT geometry
// (layout.ts) and NOT markup (FamilyTree/Forest) lives here: the one campaign
// fetch, the per-cycle expand set, the live-dashboard overlay, the derived
// forests + natural width, and the empty-stub cleanup mutation. FamilyTree
// consumes this and renders — it owns no fetch and no derived state of its own.
//
// The mental model is three files, one per concern:
//   layout.ts    pure geometry   (tree → SVG coordinates)
//   useLineage   data + state    (fetch, expand set, overlay, cleanup)  ← here
//   FamilyTree   view            (card + viewport + Forest, presentational)

import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchCampaignLineage, postCleanupEmpty } from "@/lib/api";
import type { CampaignLineageCycle } from "@/lib/api";
import type { DashboardSnapshot } from "@/lib/poll";
import { candidateLabel, liveCandidateId } from "@/lib/candidate-label";
import { rootCycleId, sessionIndexOf } from "@/lib/ids";
import { bumpRevalidation, useRevalidation } from "@/lib/revalidate";
import { useFetch } from "@/lib/hooks/useFetch";
import { useExpandedDashboards } from "@/lib/hooks/useExpandedDashboards";
import { useStableContent } from "@/lib/stable";
import { roundCandidatesByRound } from "@/lib/derivations/round-candidates";
import {
  COL_W,
  LEFT_PAD,
  RIGHT_PAD,
  buildTree,
  countDescendants,
  layout,
  type CycleDetail,
  type CycleNode,
  type DetailByCycle,
} from "./layout";

// Lineage snapshot → normalized detail. Candidates already arrive sorted by
// rank; the display index drives the short "C{r}.{n}" label so it matches the
// live (dashboard.json) labels for the active cycle.
function detailFromLineage(c: CampaignLineageCycle): CycleDetail {
  return {
    rounds: c.rounds.map((r) => ({
      round: r.round,
      candidates: r.candidates.map((cand, i) => ({
        candidateId: cand.candidate_id || liveCandidateId(r.round, i),
        label: candidateLabel(r.round, i),
        accuracy: cand.accuracy,
        isWinner: cand.is_winner,
      })),
    })),
  };
}

// dashboard.json → normalized detail for the in-view cycle. Rides the same
// per-round candidate derivation every other live surface uses.
function detailFromDash(dash: DashboardSnapshot): CycleDetail {
  const byRound = roundCandidatesByRound(dash);
  const rounds = [...byRound.keys()]
    .filter((r) => r > 0)
    .sort((a, b) => a - b)
    .map((round) => ({
      round,
      candidates: (byRound.get(round) ?? []).map((row) => ({
        candidateId: row.candidate_id,
        label: row.label,
        accuracy: row.accuracy,
        isWinner: row.is_winner,
      })),
    }));
  return { rounds };
}

// Empty-stub cleanup — one campaign-wide modal mutation. Stubs accumulate
// because fork-creation paths mint the cycle dir BEFORE the first round runs;
// an interrupt between dir-mint and first-round leaves an empty-row fork.
export interface LineageCleanup {
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
  // One cladogram per session root, each with its own fork tree.
  forests: { rootId: string; tree: CycleNode }[];
  detailByCycle: DetailByCycle;
  expanded: ReadonlySet<string>;
  // In-place expand/collapse toggle for one cycle's lane (pure view state —
  // never changes the dashboard's selected cycle).
  onLaneActivate: (cycleId: string) => void;
  // Natural px width of the widest forest — fed to the card so it sizes to the
  // tree (the viewport's overflow hides this width from CSS).
  naturalWidth: number;
  multiSession: boolean;
  totalDescendants: number;
  // Empty-state facts for the in-view cycle.
  viewedHasRounds: boolean;
  isInheritedSibling: boolean;
  parentId: string | null;
  cleanup: LineageCleanup;
  // Scoring-mask lens + the served divergence overlay it produced.
  mask: string | null;
  setMask: (mask: string | null) => void;
  divergenceByKey: ReadonlyMap<string, string | null>;
  divergentKeys: ReadonlySet<string>;
}

export function useLineage({
  dash,
  campaignId,
  cycleId,
}: {
  dash: DashboardSnapshot | null;
  campaignId: string | null;
  cycleId: string | null;
}): Lineage {
  const [tick, setTick] = useState(0);
  // The scoring-mask lens (an alternative scoring formula, e.g. "accuracy"); null
  // = off. When set, the lineage fetch carries it and the response includes the
  // divergence overlay. Backend-owned projection — the webapp only renders the
  // served flags, never recomputes scores (R-36).
  const [mask, setMask] = useState<string | null>(null);
  // Refetch the campaign-wide tree the instant any mutation resolves (fork,
  // cleanup, lifecycle) — the same revalidation seam the poll loops ride.
  // `cycleId` is deliberately NOT a fetch dep: /lineage is campaign-scoped, so a
  // same-campaign cycle switch returns identical data. `mask` IS a dep: it changes
  // the served overlay.
  const reval = useRevalidation();
  const { data } = useFetch(
    campaignId ? (s) => fetchCampaignLineage(campaignId, mask, s) : null,
    [campaignId, tick, reval, mask],
  );

  // Served divergence overlay → lookup structures keyed by `{cycle_id}::r{round}`.
  // divergenceByKey: marker nodes → the one-step alternative candidate (or null).
  // divergentKeys: the counterfactual descendant subtree to render dimmed.
  const divergenceByKey = useMemo(() => {
    const m = new Map<string, string | null>();
    for (const d of data?.divergences ?? []) m.set(d.node_key, d.alternative_candidate_id);
    return m;
  }, [data]);
  const divergentKeys = useMemo(() => new Set(data?.divergent ?? []), [data]);

  // Independent per-cycle expand state — one unified tree where every cycle
  // opens its intra-cycle candidate cladogram in place, any number at once.
  // Ephemeral (no persistence). A campaign switch resets to the clean view (the
  // in-view cycle expanded); selecting another fork ensure-expands it.
  const [expanded, setExpanded] = useState<Set<string>>(() =>
    cycleId ? new Set([cycleId]) : new Set(),
  );
  const [prevCampaign, setPrevCampaign] = useState(campaignId);
  const [prevCycle, setPrevCycle] = useState(cycleId);
  if (campaignId !== prevCampaign) {
    setPrevCampaign(campaignId);
    setPrevCycle(cycleId);
    setExpanded(cycleId ? new Set([cycleId]) : new Set());
  } else if (cycleId !== prevCycle) {
    setPrevCycle(cycleId);
    if (cycleId) {
      setExpanded((prev) => (prev.has(cycleId) ? prev : new Set(prev).add(cycleId)));
    }
  }

  const onLaneActivate = useCallback((cid: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(cid)) next.delete(cid);
      else next.add(cid);
      return next;
    });
  }, []);

  // The campaign's session roots — one cladogram per session, ordered by session.
  const rootCycleIds = useMemo(() => {
    const roots = (data?.cycles ?? [])
      .filter((c) => c.sibling_kind === "root")
      .map((c) => c.cycle_id);
    roots.sort((a, b) => sessionIndexOf(a) - sessionIndexOf(b));
    return roots;
  }, [data]);

  // Other EXPANDED lanes (not the selected one) follow their OWN live
  // dashboard.json — the web-only multi-cycle view. The selected cycle already
  // rides the `dash` prop, so it's excluded here.
  const expandedOthers = useMemo(
    () => [...expanded].filter((cid) => cid !== cycleId),
    [expanded, cycleId],
  );
  const liveDashboards = useExpandedDashboards(campaignId, expandedOthers);

  // Normalized per-cycle detail. Base = lineage snapshot; each expanded lane is
  // overlaid with its live dashboard when that snapshot carries data (so a
  // warming/empty poll never wipes the snapshot). Source-by-cycle, not a
  // per-field merge. Content-stabilized so Forest's layout memo only recomputes
  // on a real shape change.
  const detailEntries = useStableContent(
    useMemo(() => {
      const map = new Map<string, CycleDetail>();
      for (const c of data?.cycles ?? []) map.set(c.cycle_id, detailFromLineage(c));
      const overlay = (cid: string | null, snap: DashboardSnapshot | null): void => {
        if (!cid || !snap) return;
        const live = detailFromDash(snap);
        if (live.rounds.length > 0) map.set(cid, live);
        else if (!map.has(cid)) map.set(cid, live);
      };
      overlay(cycleId, dash);
      for (const [cid, snap] of liveDashboards) overlay(cid, snap);
      return [...map.entries()];
    }, [data, cycleId, dash, liveDashboards]),
  );
  const detailByCycle: DetailByCycle = useMemo(
    () => new Map(detailEntries),
    [detailEntries],
  );

  // One cladogram per session — every session root renders, including a lone
  // root with no forks (its single lane carries the intra-cycle view).
  const forests = useMemo(() => {
    if (!data) return [];
    return rootCycleIds
      .map((rootId) => {
        const tree = buildTree(rootId, data.cycles);
        return tree ? { rootId, tree } : null;
      })
      .filter((f): f is { rootId: string; tree: CycleNode } => f !== null);
  }, [data, rootCycleIds]);

  // Natural px width of the widest session forest — surfaced so the card can
  // size to the tree (the viewport's overflow:auto hides this width from CSS).
  const naturalWidth = useMemo(() => {
    let w = 0;
    for (const f of forests) {
      const { maxCol } = layout(f.tree, detailByCycle, expanded);
      const fw = LEFT_PAD + (maxCol + 1) * COL_W + RIGHT_PAD;
      if (fw > w) w = fw;
    }
    return w;
  }, [forests, detailByCycle, expanded]);

  // Empty-state facts for the in-view cycle (distinguishes an inherited fork
  // from a fresh cycle waiting for round 1).
  const viewedDetail = cycleId ? detailByCycle.get(cycleId) : undefined;
  const viewedHasRounds = (viewedDetail?.rounds.length ?? 0) > 0;
  const parentId = cycleId ? rootCycleId(cycleId) : null;
  const isInheritedSibling = parentId != null && parentId !== cycleId;

  // Window refocus ⇒ re-fetch, so forks/cleanups made from another tab or the
  // CLI surface without a manual reload.
  useEffect(() => {
    const onFocus = () => setTick((t) => t + 1);
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, []);

  const stubCount = useMemo(
    () =>
      (data?.cycles ?? []).filter(
        (c) => c.rounds.length === 0 && c.sibling_kind !== "root",
      ).length,
    [data],
  );

  // Empty-stub cleanup mutation + its modal state.
  const [cleanupOpen, setCleanupOpen] = useState(false);
  const [cleanupError, setCleanupError] = useState<string | null>(null);
  const [cleaning, setCleaning] = useState(false);
  const [cleanupAcked, setCleanupAcked] = useState(false);

  const confirmCleanup = useCallback(async () => {
    if (!campaignId || rootCycleIds.length === 0) return;
    setCleaning(true);
    setCleanupError(null);
    try {
      await postCleanupEmpty(campaignId, rootCycleIds[0]);
      setCleanupAcked(true);
      setCleanupOpen(false);
      setTick((t) => t + 1);
      bumpRevalidation();
    } catch (err) {
      setCleanupError((err as Error).message);
    } finally {
      setCleaning(false);
    }
  }, [campaignId, rootCycleIds]);

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
    forests,
    detailByCycle,
    expanded,
    onLaneActivate,
    naturalWidth,
    multiSession: rootCycleIds.length > 1,
    totalDescendants: forests.reduce((n, f) => n + countDescendants(f.tree), 0),
    viewedHasRounds,
    isInheritedSibling,
    parentId,
    cleanup,
    mask,
    setMask,
    divergenceByKey,
    divergentKeys,
  };
}
