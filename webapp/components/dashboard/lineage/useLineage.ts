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
import { useFitnessState } from "@/components/whatif/fitness-store";
import { formulaFromWeights } from "@/components/whatif/fitness-bars";
import { useDebounced } from "@/lib/hooks/useDebounced";
import { setMaskState } from "@/lib/mask-store";
import { useSelection } from "@/lib/SelectionContext";

// Short label per preset lens for the lineage card's mask tag.
const LENS_LABELS: Record<string, string> = {
  "score:accuracy": "Accuracy",
  "abort:epsilon_off": "No ε-elim",
  "abort:lock_in_off": "No lock-in",
  "abort:all_off": "No abort",
};
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
  dash,
  campaignId,
  cycleId,
}: {
  dash: DashboardSnapshot | null;
  campaignId: string | null;
  cycleId: string | null;
}): Lineage {
  const [tick, setTick] = useState(0);
  // The PRESET lens — a dropdown value: "" (realized, off), "score:<formula>"
  // (scoring swap), or "abort:<variant>" (PoBB abort switch-off). The What-If mask
  // is NOT a preset: opening the What-If card (its chip) is the master switch that
  // drives the lineage from the live evaluator selection + weights. Backend-owned
  // projection — the webapp renders served flags, never recomputes (R-36).
  const [lens, setLens] = useState<string>("");
  // The What-If card is the master: when open, the lineage follows its live
  // selection + weights (the SAME weighted criterion as the bars), so dragging a
  // weight reshapes the divergence — debounced so a continuous drag doesn't spam
  // the fetch (the bars recompute live; the overlay settles ~250 ms after).
  const {
    selected: whatifSelected,
    weights: whatifWeights,
    showWhatIf,
  } = useFitnessState();
  // The fixed sample-set (the fitness "Sample set" chip) is ALSO a mask: it
  // re-scores accuracy over only those ids backend-side, so the lineage diverges
  // wherever the subset-best ≠ the recorded winner — the same set the per-candidate
  // bars recompute over. Serialized to a stable string so a same-content set
  // doesn't re-fire the fetch.
  const { sampleSet } = useSelection();
  const samplesParam = useMemo(
    () => (sampleSet && sampleSet.length > 0 ? sampleSet : null),
    [sampleSet],
  );
  const samplesKey = samplesParam ? samplesParam.join(",") : "";
  const liveWhatifFormula = useMemo(
    () => (showWhatIf ? formulaFromWeights(whatifSelected, whatifWeights) : null),
    [showWhatIf, whatifSelected, whatifWeights],
  );
  const whatifFormula = useDebounced(liveWhatifFormula, 250);
  const [maskParam, abortParam] = useMemo(() => {
    if (showWhatIf) return [whatifFormula, null] as const;
    if (lens.startsWith("score:")) return [lens.slice(6), null] as const;
    if (lens.startsWith("abort:")) return [null, lens.slice(6)] as const;
    return [null, null] as const;
  }, [showWhatIf, whatifFormula, lens]);
  // Short label for whichever lens is REQUESTED (drives the fetch). The red tag
  // itself is gated on a divergence actually being FOUND (`maskActive`, derived
  // from the served overlay below) — not on the chip being toggled.
  const maskLabel = showWhatIf
    ? "What-If"
    : samplesParam
      ? "Sample set"
      : LENS_LABELS[lens] ?? "";
  // Refetch the campaign-wide tree the instant any mutation resolves (fork,
  // cleanup, lifecycle) — the same revalidation seam the poll loops ride.
  // `cycleId` is deliberately NOT a fetch dep: /lineage is campaign-scoped, so a
  // same-campaign cycle switch returns identical data. `lens` IS a dep: it changes
  // the served overlay.
  const reval = useRevalidation();
  const { data } = useFetch(
    campaignId
      ? (s) => fetchCampaignLineage(campaignId, maskParam, abortParam, samplesParam, s)
      : null,
    [campaignId, tick, reval, maskParam, abortParam, samplesKey],
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

  // The mask is "active" (red tag, fitness divider) only when the served overlay
  // actually carries a divergence — NOT merely because a lens/chip is requested.
  // Toggling Sample-set / What-If with no resulting divergence shows nothing.
  const maskActive = divergenceByKey.size > 0 || divergentKeys.size > 0;

  // Publish the served overlay so the per-candidate fitness can draw the same
  // divergence boundary — the lineage card is the single fetcher; other surfaces
  // render what it serves (R-36). Content-guarded in the store, so a bare poll is
  // a no-op when the divergences are unchanged.
  useEffect(() => {
    setMaskState({
      divergences: data?.divergences ?? [],
      divergent: data?.divergent ?? [],
      maskActive,
      maskLabel,
    });
  }, [data, maskActive, maskLabel]);

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
    lens,
    setLens,
    maskActive,
    maskLabel,
    whatifActive: showWhatIf,
    divergenceByKey,
    divergentKeys,
  };
}
