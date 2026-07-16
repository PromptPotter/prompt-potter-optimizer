"use client";
// Tree + state for the candidates card's Forest view. Everything that is NOT
// geometry (forest-layout.ts), NOT markup (CandidatesCard/Forest), and NOT the
// shared fetch/overlay (lib/lineage-overlay) lives here: the derived forests, the
// per-candidate value overlays, the fork map, and the empty-stub cleanup
// mutation. The campaign fetch and the mask/lens divergence overlay are owned by
// `LineageOverlayProvider` and read via `useLineageOverlay()` — the SINGLE source
// both this view and the bars render. The tree is the settled (closed-round,
// origin-C0-included) structure served by `/lineage`, revalidated on the
// dashboard change-signal; the in-flight round shows in the bars + the Sequence
// dendrogram, not here (one source per data class).
//
// View state (which metric, which lanes are open) is NOT here — it belongs to the
// card as a whole and lives in `candidates-store`, so the Sequence and Forest
// views cannot disagree about which number they are painting.

import { useCallback, useMemo, useState } from "react";
import { postCleanupEmpty } from "@/lib/api";
import type { CampaignLineageCycle } from "@/lib/api";
import { liveCandidateId } from "@/lib/candidate-label";
import { accuracyBasisValue } from "@/lib/fitness";
import {
  groupByRound,
  lineageCandidates,
  primaryMetric,
  roundCandidates,
  type HeadlineMetric,
  type LineageCandidate,
} from "@/lib/derivations";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { rootCycleId } from "@/lib/ids";
import { bumpRevalidation } from "@/lib/revalidate";
import { useLineageOverlay } from "@/lib/lineage-overlay";
import { useStableContent } from "@/lib/stable";
import type { CandidateRow } from "@/lib/types";
import { setCandidatesState, useCandidatesState } from "./candidates-store";
import {
  buildTree,
  countDescendants,
  type CycleDetail,
  type CycleNode,
  type DetailByCycle,
} from "./forest-layout";

// Lineage snapshot → normalized STRUCTURE (no fitness value — that's the live
// `valueByKey` overlay). Candidate identity + label come from the shared
// `lineageCandidates` derivation, which the sidebar's candidate rows read too —
// so a candidate is named and keyed identically wherever the settled source is
// rendered. Re-grouped by round here because the lane geometry is round-shaped.
function detailFromLineage(c: CampaignLineageCycle): CycleDetail {
  const byRound = new Map<number, LineageCandidate[]>();
  for (const cand of lineageCandidates(c)) {
    const arr = byRound.get(cand.round) ?? [];
    arr.push(cand);
    byRound.set(cand.round, arr);
  }
  return {
    rounds: c.rounds.map((r) => ({
      round: r.round,
      advanced: r.advanced,
      candidates: (byRound.get(r.round) ?? []).map((cand) => ({
        candidateId: cand.candidateId,
        label: cand.label,
        isWinner: cand.isWinner,
      })),
    })),
  };
}

// Live dashboard rows → the SAME normalized structure, for the in-view active
// cycle. Sourced from `roundCandidates(dash)` (the bars' source) so the active
// cycle's tree includes the in-flight round and can't disagree with them.
// Structure only — value rides `valueByKey`.
function detailFromRows(rows: CandidateRow[]): CycleDetail {
  return {
    rounds: [...groupByRound(rows).entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([round, cands]) => ({
        round,
        // A live round has advanced once a candidate is flagged winner; the
        // in-flight (not-yet-elected) round reads as not-yet-advanced, which is
        // correct — it's the lane's last round, so it never mischains a successor.
        advanced: cands.some((c) => c.is_winner),
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
  // One cladogram per session root, each with its own fork tree.
  forests: { rootId: string; tree: CycleNode }[];
  detailByCycle: DetailByCycle;
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
  // `candidate_id` → the cycle forked from it (in-view cycle only). Drives the ⑂
  // mark in the Sequence view, whose click frees the hierarchy into the Forest.
  forkedFrom: ReadonlyMap<string, string>;
  expanded: ReadonlySet<string>;
  // In-place expand/collapse toggle for one cycle's lane (pure view state —
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
  // Shared campaign lineage from the single fetch both views render (R-36). The
  // mask/lens overlay fields (lens, divergence, …) are NOT re-exposed here — the
  // card and `Forest` read them straight from `useLineageOverlay()`, so this hook
  // owns only the tree/fork/cleanup state.
  const { data } = useLineageOverlay();
  // The in-view cycle's live dashboard — the same source the bars read, so the
  // active cycle's lineage detail (structure + values) can't disagree with them.
  const { dash } = useDashboard();
  const { metrics, expanded, expandedForCampaign, expandedForCycle } = useCandidatesState();
  const metric = primaryMetric(metrics);

  // The active (in-view) cycle's live candidate rows from dashboard.json —
  // includes the in-flight round. Computed once and shared by the structure
  // build and the value overlay below.
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

  // The campaign's root cycles — one cladogram each. A campaign mints exactly
  // one root today; the list shape survives so lineage stays id-ordered.
  const rootCycleIds = useMemo(
    () =>
      (data?.cycles ?? [])
        .filter((c) => c.sibling_kind === "root")
        .map((c) => c.cycle_id)
        .sort(),
    [data],
  );

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

  // Which candidates of the IN-VIEW cycle a sibling was cut from. The Sequence
  // view has no lanes to show a fork in — it plots one cycle — so it marks the
  // candidate the fork left from and offers the jump into the Forest, where the
  // sibling actually has somewhere to be drawn.
  const forkedFrom = useMemo<ReadonlyMap<string, string>>(() => {
    const m = new Map<string, string>();
    if (!cycleId) return m;
    for (const c of data?.cycles ?? []) {
      if (c.immediate_parent_cycle_id !== cycleId) continue;
      if (!c.fork_from_candidate_id) continue;
      m.set(c.fork_from_candidate_id, c.cycle_id);
    }
    return m;
  }, [data, cycleId]);

  // Per-candidate percent-metric overlay, keyed `{cycleId}::{candidateId}`.
  // `/lineage` serves `composite_fitness` per candidate (verbatim from the
  // dashboard round summary), so settled/sibling cycles honor the composite
  // selection on the SAME basis as the active cycle — one tree, one basis,
  // nothing recomputed client-side. θ is a separate overlay (`thetaByKey`)
  // since it is a logit, not a percent. Deliberately NOT content-stabilized:
  // it updates every poll, but only painted node text reads it.
  const usesComposite = metric === "composite";
  const valueByKey = useMemo<ReadonlyMap<string, number | null>>(() => {
    const m = new Map<string, number | null>();
    // Accuracy view: the WINNER (lineage spine) paints the round's cumulative
    // frontier — the cross-round-comparable series the trend plots — so the spine
    // reads as honest progress, not the per-round subset swing. Losers keep their
    // own subset score.
    for (const c of data?.cycles ?? []) {
      for (const r of c.rounds) {
        r.candidates.forEach((cand, i) => {
          const id = cand.candidate_id || liveCandidateId(r.round, i);
          const value = usesComposite
            ? cand.composite_fitness ?? null
            : accuracyBasisValue(cand.is_winner, r.cumulative_accuracy, cand.accuracy);
          m.set(`${c.cycle_id}::${id}`, value);
        });
      }
    }
    if (cycleId && dash) {
      for (const row of liveRows) {
        const value = usesComposite
          ? row.composite ?? null
          : accuracyBasisValue(row.is_winner, row.cumulative_accuracy, row.accuracy);
        m.set(`${cycleId}::${row.candidate_id}`, value);
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
    const rootId = rootCycleIds[0];
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
    metric,
    forkedFrom,
    expanded,
    onLaneActivate,
    totalDescendants: forests.reduce((n, f) => n + countDescendants(f.tree), 0),
    viewedHasRounds,
    isInheritedSibling,
    parentId,
    cleanup,
  };
}
