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
import {
  displayFitness,
  groupByRound,
  roundCandidates,
  type HeadlineMetric,
} from "@/lib/derivations";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { rootCycleId, sessionIndexOf } from "@/lib/ids";
import { bumpRevalidation } from "@/lib/revalidate";
import { useLineageOverlay } from "@/lib/lineage-overlay";
import { useStableContent } from "@/lib/stable";
import type { CandidateRow } from "@/lib/types";
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

// Lineage snapshot → normalized STRUCTURE (no fitness value — that's the live
// `valueByKey` overlay). Candidates already arrive sorted by rank; the display
// index drives the short "C{r}.{n}" label so it matches the live labels.
function detailFromLineage(c: CampaignLineageCycle): CycleDetail {
  return {
    rounds: c.rounds.map((r) => ({
      round: r.round,
      candidates: r.candidates.map((cand, i) => ({
        candidateId: cand.candidate_id || liveCandidateId(r.round, i),
        label: candidateLabel(r.round, i),
        isWinner: cand.is_winner,
      })),
    })),
  };
}

// Live dashboard rows → the SAME normalized structure, for the in-view active
// cycle. Sourced from `roundCandidates(dash)` (the fitness bars' source) so the
// active cycle's tree includes the in-flight round and can't disagree with the
// bars. Structure only — value rides `valueByKey`.
function detailFromRows(rows: CandidateRow[]): CycleDetail {
  return {
    rounds: [...groupByRound(rows).entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([round, cands]) => ({
        round,
        candidates: cands.map((c) => ({
          candidateId: c.candidate_id,
          label: c.label,
          isWinner: c.is_winner,
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
  // Per-candidate fitness, keyed `{cycleId}::{candidateId}` — the live value
  // overlay painted onto nodes. NOT part of the geometry (so a value tick never
  // re-lays-out the tree). Carries the percent metric the operator selected
  // (accuracy, or composite on the active cycle); θ rides `thetaByKey`. Active
  // cycle's values come from the live dashboard; other cycles from the settled
  // /lineage (accuracy only). `undefined` for a node with no value yet.
  valueByKey: ReadonlyMap<string, number | null>;
  // Same-key overlay of difficulty-adjusted ability θ — painted into node tooltips so a
  // θ-elected winner shown below a higher-accuracy sibling is explainable in place.
  thetaByKey: ReadonlyMap<string, number | null>;
  expanded: ReadonlySet<string>;
  // In-place expand/collapse toggle for one cycle's lane (pure view state —
  // never changes the dashboard's selected cycle).
  onLaneActivate: (cycleId: string) => void;
  // Natural px width of the widest forest — fed to the card so it sizes to the
  // tree (the viewport's overflow hides this width from CSS).
  naturalWidth: number;
  // Operator-selected headline metric for the lineage node values, seeded from
  // the served campaign default (`headlineMetricDefault`) and client-overridable
  // via `setHeadlineMetric`. θ never forced — defaults to accuracy unless the
  // campaign config says otherwise. The gate stays θ regardless of this choice.
  headlineMetric: HeadlineMetric;
  headlineMetricDefault: HeadlineMetric;
  setHeadlineMetric: (m: HeadlineMetric) => void;
  multiSession: boolean;
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
  // Shared campaign lineage from the single fetch both this card and the fitness
  // panel render (R-36). The mask/lens overlay fields (lens, divergence, …) are
  // NOT re-exposed here — `FamilyTree` and `Forest` read them straight from
  // `useLineageOverlay()`, so this hook owns only the tree/expand/cleanup state.
  const { data } = useLineageOverlay();
  // The in-view cycle's live dashboard — the same source the fitness bars read,
  // so the active cycle's lineage detail (structure + values) can't disagree
  // with them. Available here: this card renders under CycleStreamProvider.
  const { dash } = useDashboard();

  // The active (in-view) cycle's live candidate rows from dashboard.json —
  // includes the in-flight round. Computed once and shared by the structure
  // build and the value overlay below.
  const liveRows = useMemo<CandidateRow[]>(
    () => (cycleId && dash ? roundCandidates(dash) : []),
    [cycleId, dash],
  );

  // Which fitness number the operator reads on the lineage nodes. Seeded from the
  // served campaign default (CampaignConfig.headline_metric → dash.headline_metric);
  // a manual pick overrides for the session. The gate is always θ — this is pure
  // display, so θ ("ability") is offered but never the forced default.
  const headlineMetricDefault: HeadlineMetric = dash?.headline_metric ?? "accuracy";
  const [metricOverride, setMetricOverride] = useState<HeadlineMetric | null>(null);
  const headlineMetric = metricOverride ?? headlineMetricDefault;

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

  // Normalized per-cycle STRUCTURE — source-by-cycle-role: the in-view active
  // cycle from the live dashboard (so its in-flight round shows and tracks the
  // bars), every other cycle from the settled /lineage. No fitness value here
  // (that's `valueByKey`), and content-stabilized — so Forest's layout memo
  // recomputes only on a real shape change (new candidate / round / winner flip
  // / expand), never on a per-sample value tick.
  const detailEntries = useStableContent(
    useMemo(() => {
      const map = new Map<string, CycleDetail>();
      for (const c of data?.cycles ?? []) {
        map.set(
          c.cycle_id,
          c.cycle_id === cycleId && dash ? detailFromRows(liveRows) : detailFromLineage(c),
        );
      }
      return [...map.entries()];
    }, [data, cycleId, dash, liveRows]),
  );
  const detailByCycle: DetailByCycle = useMemo(() => new Map(detailEntries), [detailEntries]);

  // Per-candidate percent-metric overlay, keyed `{cycleId}::{candidateId}`. Settled
  // cycles serve accuracy only (no composite), so they paint accuracy for both the
  // accuracy and composite selections; the active cycle paints composite only when
  // `composite` is selected (else raw accuracy). θ is a separate overlay
  // (`thetaByKey`) since it is a logit, not a percent. Deliberately NOT
  // content-stabilized: it updates every poll, but only painted node text reads it.
  const usesComposite = headlineMetric === "composite";
  const valueByKey = useMemo<ReadonlyMap<string, number | null>>(() => {
    const m = new Map<string, number | null>();
    for (const c of data?.cycles ?? []) {
      for (const r of c.rounds) {
        r.candidates.forEach((cand, i) => {
          const id = cand.candidate_id || liveCandidateId(r.round, i);
          m.set(`${c.cycle_id}::${id}`, cand.accuracy);
        });
      }
    }
    if (cycleId && dash) {
      for (const row of liveRows) {
        m.set(
          `${cycleId}::${row.candidate_id}`,
          usesComposite ? displayFitness(row.composite, row.accuracy) : row.accuracy,
        );
      }
    }
    return m;
  }, [data, cycleId, dash, liveRows, usesComposite]);

  // Parallel overlay carrying each candidate's difficulty-adjusted ability θ, same key shape
  // as `valueByKey`. Painted into the node tooltip so a θ-elected winner shown below a
  // higher-accuracy sibling is explainable on the node itself. `null` where there's no
  // election fit (in-flight / eliminated).
  const thetaByKey = useMemo<ReadonlyMap<string, number | null>>(() => {
    const m = new Map<string, number | null>();
    for (const c of data?.cycles ?? []) {
      for (const r of c.rounds) {
        r.candidates.forEach((cand, i) => {
          const id = cand.candidate_id || liveCandidateId(r.round, i);
          m.set(`${c.cycle_id}::${id}`, cand.theta);
        });
      }
    }
    if (cycleId && dash) {
      for (const row of liveRows) m.set(`${cycleId}::${row.candidate_id}`, row.theta);
    }
    return m;
  }, [data, cycleId, dash, liveRows]);

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
    valueByKey,
    thetaByKey,
    headlineMetric,
    headlineMetricDefault,
    setHeadlineMetric: setMetricOverride,
    expanded,
    onLaneActivate,
    naturalWidth,
    multiSession: rootCycleIds.length > 1,
    totalDescendants: forests.reduce((n, f) => n + countDescendants(f.tree), 0),
    viewedHasRounds,
    isInheritedSibling,
    parentId,
    cleanup,
  };
}
