"use client";
// Record-scoped seam for the served lineage TREE + its mask/lens counterfactual.
//
// The tree is a property of the served *record*, not of any one widget — the
// forest, the sidebar's candidate rows and the per-candidate fitness panel all
// render it. So a single provider owns the one fetch and the lens selection, and
// every surface reads it through context. No widget publishes to a module global
// from a render effect; one fetch, one source of truth, rendered — never
// recomputed (R-36).
//
// The fetch is driven by: the lineage card's preset `lens` dropdown, the What-If
// card's live selection/weights (the `score:` formula, debounced), and the fixed
// sample-set chip (the `samples` mask). All three compose here, once.

import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import { fetchLineageTree } from "@/lib/api";
import type { LineageNode } from "@/lib/api/types";
import { nodeAt, walkCourses } from "@/lib/derivations";
import { useCandidatesState } from "@/components/candidates/candidates-store";
import { formulaFromWeights } from "@/components/candidates/fitness-bars";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useDebounced } from "@/lib/hooks/useDebounced";
import { rootCycleId, type CyclePath } from "@/lib/ids";
import { useRevalidation } from "@/lib/revalidate";
import { useSelection } from "@/lib/SelectionContext";

// Course-levels the forest expands. The tree recurses `course → candidate →
// (course | sample)`, so this reaches a campaign's forks, their forks, and the L4
// inner runs filed under any of their candidates. Each level costs one ledger scan
// per course, so it is the cost dial — not a hidden recursion.
const FOREST_DEPTH = 3;

// Short label per preset lens for the mask tag.
const LENS_LABELS: Record<string, string> = {
  "score:accuracy": "Accuracy",
  "abort:epsilon_off": "No ε-elim",
  "abort:lock_in_off": "No lock-in",
  "abort:all_off": "No abort",
};

interface LineageOverlay {
  // THE served genealogy, rooted at the campaign's root course. Nodes alternate
  // course → candidate → (course | sample) at every depth.
  tree: LineageNode | null;
  // The PRESET lens dropdown value: "" (off), "score:<formula>", or "abort:<variant>".
  // The What-If card overrides it as the master when open.
  lens: string;
  setLens: (lens: string) => void;
  // A mask actually produced a divergence (drives the red tag) + its short label;
  // whether the What-If card is the active master (preset dropdown overridden).
  maskActive: boolean;
  maskLabel: string;
  whatifActive: boolean;
}

const LineageOverlayContext = createContext<LineageOverlay | null>(null);

// Every (course, candidate) pair in the tree — the one walk the overlay maps ride.
function candidatePairs(tree: LineageNode | null): { courseId: string; cand: LineageNode }[] {
  if (!tree) return [];
  const out: { courseId: string; cand: LineageNode }[] = [];
  for (const course of walkCourses(tree)) {
    for (const cand of course.children) {
      if (cand.kind === "candidate") out.push({ courseId: course.id, cand });
    }
  }
  return out;
}

export function LineageOverlayProvider({
  campaignId,
  cycleId,
  children,
}: {
  campaignId: string | null;
  cycleId: string | null;
  children: React.ReactNode;
}) {
  const [tick, setTick] = useState(0);
  const [lens, setLens] = useState<string>("");
  // Lens is a campaign-level view choice; reset it when the campaign changes so a
  // preset / What-If selection can't leak across campaigns (the provider now lives
  // at the shell root, persisting across cycle + tab switches).
  const [prevCampaign, setPrevCampaign] = useState(campaignId);
  if (campaignId !== prevCampaign) {
    setPrevCampaign(campaignId);
    setLens("");
  }
  // The What-If card is the master: when open, the tree follows its live
  // selection + weights (the SAME weighted criterion as the bars), debounced so a
  // continuous drag doesn't spam the fetch.
  const { selected: whatifSelected, weights: whatifWeights, showWhatIf } = useCandidatesState();
  // The fixed sample-set chip is ALSO a mask: re-score accuracy over only those ids
  // backend-side. Serialized to a stable string so a same-content set doesn't re-fire.
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
  // One lens value drives the fetch: the What-If `score:` formula when its card is
  // master, else the preset dropdown (already a `score:`/`abort:` value).
  const lensParam = useMemo(() => {
    if (showWhatIf) return whatifFormula ? `score:${whatifFormula}` : null;
    return lens || null;
  }, [showWhatIf, whatifFormula, lens]);
  const maskLabel = showWhatIf
    ? "What-If"
    : samplesParam
      ? "Sample set"
      : (LENS_LABELS[lens] ?? "");

  // The tree is rooted at the campaign's ROOT course, and its own recursion reaches
  // every fork below it — so selecting a fork must not re-root (and re-fetch) it.
  // `rootCycleId` is a pure string derivation, so a same-campaign cycle switch
  // resolves to the same root and the same query key: `cycleId` stays out of the
  // fetch identity exactly as it did when this read was campaign-scoped.
  const rootCycle = cycleId ? rootCycleId(cycleId) : null;

  const reval = useRevalidation();
  // Revalidate on the SAME signal the live header moves on — a round closing or a
  // phase flip — so the tree never lags the 2 s dashboard poll. A quiescent stretch
  // changes no key, so it makes no request; this provider sits under
  // CycleStreamProvider, so `dash` is available here.
  const { dash, dashRound } = useDashboard();
  const dashChangeKey = `${dash?.rounds.length ?? 0}:${dashRound ?? -1}:${dash?.run_phase ?? ""}`;

  const [tree, setTree] = useState<LineageNode | null>(null);
  // Query identity = root course + lens + samples. A change is a DIFFERENT served
  // body (different URL), so drop the prior tree in the same render before the
  // refetch lands (render-phase guarded reset, webapp/CLAUDE.md).
  const queryKey = `${campaignId ?? ""}|${rootCycle ?? ""}|${lensParam ?? ""}|${samplesKey}`;
  const [prevQueryKey, setPrevQueryKey] = useState(queryKey);
  if (queryKey !== prevQueryKey) {
    setPrevQueryKey(queryKey);
    setTree(null);
  }

  // Last-Modified validator, keyed to the query identity so it's only reused
  // within the same body — a new query (campaign/lens/samples) fetches fresh.
  const lastModifiedRef = useRef<{ key: string; value: string | null }>({ key: "", value: null });
  useEffect(() => {
    if (!campaignId || !rootCycle) return;
    const ac = new AbortController();
    let cancelled = false;
    const ims = lastModifiedRef.current.key === queryKey ? lastModifiedRef.current.value : null;
    fetchLineageTree(
      [{ campaignId, cycleId: rootCycle }],
      { lens: lensParam, samples: samplesParam, depth: FOREST_DEPTH },
      ims,
      ac.signal,
    )
      .then((res) => {
        if (cancelled) return;
        lastModifiedRef.current = { key: queryKey, value: res.lastModified ?? ims };
        // 304 keeps the current tree — a quiescent revalidation costs nothing.
        if (res.kind === "ok") setTree(res.data);
      })
      .catch(() => {
        // Transient/aborted — keep the last good tree rather than blanking it.
      });
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [campaignId, rootCycle, queryKey, lensParam, samplesParam, tick, reval, dashChangeKey]);

  const pairs = useMemo(() => candidatePairs(tree), [tree]);

  // "Active" (red tag) only when the served tree actually carries a divergence —
  // NOT merely because a lens/chip is requested.
  const maskActive = useMemo(
    () => pairs.some(({ cand }) => cand.divergence !== null || cand.divergent),
    [pairs],
  );

  // Window refocus ⇒ refetch, so forks/cleanups made from another tab or the CLI
  // surface without a manual reload.
  useEffect(() => {
    const onFocus = () => setTick((t) => t + 1);
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, []);

  const value = useMemo<LineageOverlay>(
    () => ({
      tree,
      lens,
      setLens,
      maskActive,
      maskLabel,
      whatifActive: showWhatIf,
    }),
    [tree, lens, maskActive, maskLabel, showWhatIf],
  );

  return (
    <LineageOverlayContext.Provider value={value}>{children}</LineageOverlayContext.Provider>
  );
}

export function useLineageOverlay(): LineageOverlay {
  const ctx = useContext(LineageOverlayContext);
  if (ctx === null) {
    throw new Error("useLineageOverlay must be used within a LineageOverlayProvider");
  }
  return ctx;
}

// Per-course divergence facts for the round axis — which rounds of `cycleId` are a
// divergence point vs. inside the counterfactual subtree. Pure derivation over the
// served tree; surfaces share it so they mark identically. The marker rides the
// round's WINNER (the candidate the lens would have replaced), and every candidate
// of a counterfactual round carries `divergent`.
export function divergenceRoundsFor(
  overlay: LineageOverlay,
  path: CyclePath | null,
): { points: ReadonlySet<number>; subtree: ReadonlySet<number> } {
  const points = new Set<number>();
  const subtree = new Set<number>();
  if (!path || !overlay.tree) return { points, subtree };
  // Addressed, not scanned: a bare cycle id names more than one course (inner ids
  // repeat across sandboxes), and `find` would answer with whichever came first.
  const course = nodeAt(overlay.tree, path);
  for (const cand of course?.children ?? []) {
    if (cand.round == null) continue;
    if (cand.divergence) points.add(cand.round);
    if (cand.divergent) subtree.add(cand.round);
  }
  return { points, subtree };
}

