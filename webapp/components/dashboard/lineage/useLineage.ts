"use client";
// Tree + state for the campaign lineage card. Everything that is NOT geometry
// (layout.ts), NOT markup (FamilyTree/Forest), and NOT the shared fetch/overlay
// (lib/lineage-overlay) lives here: the per-cycle expand set, the derived forests
// + natural width, and the empty-stub cleanup mutation. The campaign fetch and the
// mask/lens divergence overlay are owned by `LineageOverlayProvider` and read via
// `useLineageOverlay()` — the SINGLE source both this card and the per-candidate
// fitness panel render. The tree is the settled (closed-round, origin-C0-included)
// structure served by `/lineage`, revalidated on the dashboard change-signal; the
// in-flight round shows in the Fitness bars, not here (one source per data class).
//
// The mental model is three files, one per concern:
//   layout.ts    pure geometry   (tree → SVG coordinates)
//   useLineage   tree + state    (expand set, forests, cleanup)  ← here
//   FamilyTree   view            (card + viewport + Forest, presentational)

import { useCallback, useMemo, useState } from "react";
import { postCleanupEmpty } from "@/lib/api";
import type { CampaignLineageCycle } from "@/lib/api";
import { candidateLabel, liveCandidateId } from "@/lib/candidate-label";
import { rootCycleId, sessionIndexOf } from "@/lib/ids";
import { bumpRevalidation } from "@/lib/revalidate";
import { useLineageOverlay } from "@/lib/lineage-overlay";
import { useStableContent } from "@/lib/stable";
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
  // Preset lens (dropdown) + the served divergence overlay any active mask produced.
  lens: string;
  setLens: (lens: string) => void;
  // A mask is driving the lineage (red tag) + its label; whether the What-If card
  // is the active master (so the preset dropdown is overridden/disabled).
  maskActive: boolean;
  maskLabel: string;
  whatifActive: boolean;
  divergenceByKey: ReadonlyMap<string, string | null>;
  divergentKeys: ReadonlySet<string>;
}

export function useLineage({
  campaignId,
  cycleId,
}: {
  campaignId: string | null;
  cycleId: string | null;
}): Lineage {
  // Shared campaign fetch + mask/lens overlay — the single source both this card
  // and the fitness panel render (R-36). Backend-owned projection; the webapp
  // renders served flags, never recomputes.
  const { data, lens, setLens, maskActive, maskLabel, whatifActive, divergenceByKey, divergentKeys } =
    useLineageOverlay();

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

  // Normalized per-cycle detail — purely the served lineage snapshot (closed
  // rounds, origin C0 included), one source for every cycle. Content-stabilized
  // so Forest's layout memo only recomputes on a real shape change.
  const detailEntries = useStableContent(
    useMemo(() => {
      const map = new Map<string, CycleDetail>();
      for (const c of data?.cycles ?? []) map.set(c.cycle_id, detailFromLineage(c));
      return [...map.entries()];
    }, [data]),
  );
  const detailByCycle: DetailByCycle = useMemo(() => new Map(detailEntries), [detailEntries]);

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

  const stubCount = useMemo(
    () =>
      (data?.cycles ?? []).filter((c) => c.rounds.length === 0 && c.sibling_kind !== "root")
        .length,
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
    lens,
    setLens,
    maskActive,
    maskLabel,
    whatifActive,
    divergenceByKey,
    divergentKeys,
  };
}
