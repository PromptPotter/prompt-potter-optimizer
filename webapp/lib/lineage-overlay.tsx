"use client";
// Record-scoped seam for the campaign lineage + its mask/lens divergence overlay.
//
// The `/lineage` overlay is a property of the served *record*, not of any one
// widget — both the lineage card (the tree) and the per-candidate fitness panel
// render it. So a single provider owns the one campaign-scoped fetch and the lens
// selection, and both surfaces read it through context. No widget publishes to a
// module global from a render effect; one fetch, one source of truth, rendered —
// never recomputed (R-36).
//
// The fetch is driven by: the lineage card's preset `lens` dropdown, the What-If
// card's live selection/weights (the `score:` formula, debounced), and the fixed
// sample-set chip (the `samples` mask). All three compose here, once.

import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { fetchCampaignLineage } from "@/lib/api";
import type { CampaignLineageResponse, LineageDivergence } from "@/lib/api/types";
import { useFitnessState } from "@/components/whatif/fitness-store";
import { formulaFromWeights } from "@/components/whatif/fitness-bars";
import { useDebounced } from "@/lib/hooks/useDebounced";
import { useFetch } from "@/lib/hooks/useFetch";
import { useRevalidation } from "@/lib/revalidate";
import { useSelection } from "@/lib/SelectionContext";

// Short label per preset lens for the mask tag.
const LENS_LABELS: Record<string, string> = {
  "score:accuracy": "Accuracy",
  "abort:epsilon_off": "No ε-elim",
  "abort:lock_in_off": "No lock-in",
  "abort:all_off": "No abort",
};

export interface LineageOverlay {
  // The whole campaign lineage response — cycles + the served divergence overlay.
  data: CampaignLineageResponse | null;
  // The PRESET lens dropdown value: "" (off), "score:<formula>", or "abort:<variant>".
  // The What-If card overrides it as the master when open.
  lens: string;
  setLens: (lens: string) => void;
  // A mask actually produced a divergence (drives the red tag) + its short label;
  // whether the What-If card is the active master (preset dropdown overridden).
  maskActive: boolean;
  maskLabel: string;
  whatifActive: boolean;
  // Marker nodes → one-step alternative candidate; the dimmed counterfactual subtree.
  divergenceByKey: ReadonlyMap<string, string | null>;
  divergentKeys: ReadonlySet<string>;
}

const LineageOverlayContext = createContext<LineageOverlay | null>(null);

export function LineageOverlayProvider({
  campaignId,
  children,
}: {
  campaignId: string | null;
  children: React.ReactNode;
}) {
  const [tick, setTick] = useState(0);
  const [lens, setLens] = useState<string>("");
  // The What-If card is the master: when open, the lineage follows its live
  // selection + weights (the SAME weighted criterion as the bars), debounced so a
  // continuous drag doesn't spam the fetch.
  const { selected: whatifSelected, weights: whatifWeights, showWhatIf } = useFitnessState();
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

  // `cycleId` is deliberately NOT a fetch dep: /lineage is campaign-scoped, so a
  // same-campaign cycle switch returns identical data. `lensParam`/`samplesKey` ARE
  // deps: they change the served overlay. Refetch on any mutation (fork, cleanup,
  // lifecycle) via the shared revalidation seam.
  const reval = useRevalidation();
  const { data } = useFetch(
    campaignId
      ? (s) => fetchCampaignLineage(campaignId, lensParam, samplesParam, s)
      : null,
    [campaignId, tick, reval, lensParam, samplesKey],
  );

  // Served overlay → lookup structures keyed by `{cycle_id}::r{round}`.
  const divergenceByKey = useMemo(() => {
    const m = new Map<string, string | null>();
    for (const d of data?.divergences ?? []) m.set(d.node_key, d.alternative_candidate_id);
    return m;
  }, [data]);
  const divergentKeys = useMemo(() => new Set(data?.divergent ?? []), [data]);
  // "Active" (red tag) only when the served overlay actually carries a divergence —
  // NOT merely because a lens/chip is requested.
  const maskActive = divergenceByKey.size > 0 || divergentKeys.size > 0;

  // Window refocus ⇒ refetch, so forks/cleanups made from another tab or the CLI
  // surface without a manual reload.
  useEffect(() => {
    const onFocus = () => setTick((t) => t + 1);
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, []);

  const value = useMemo<LineageOverlay>(
    () => ({
      data,
      lens,
      setLens,
      maskActive,
      maskLabel,
      whatifActive: showWhatIf,
      divergenceByKey,
      divergentKeys,
    }),
    [data, lens, maskActive, maskLabel, showWhatIf, divergenceByKey, divergentKeys],
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

// Per-cycle divergence facts for the round axis — which rounds of `cycleId` are a
// divergence point vs. inside the counterfactual subtree. Pure derivation over the
// served overlay; surfaces share it so they mark identically.
export function divergenceRoundsFor(
  overlay: LineageOverlay,
  cycleId: string | null,
): { points: ReadonlySet<number>; subtree: ReadonlySet<number> } {
  const points = new Set<number>();
  const subtree = new Set<number>();
  if (!cycleId) return { points, subtree };
  for (const d of overlay.data?.divergences ?? []) {
    if (d.cycle_id === cycleId) points.add(d.round);
  }
  const prefix = `${cycleId}::r`;
  for (const key of overlay.data?.divergent ?? []) {
    if (key.startsWith(prefix)) {
      const r = Number(key.slice(prefix.length));
      if (Number.isFinite(r)) subtree.add(r);
    }
  }
  return { points, subtree };
}

// Re-export so consumers needn't reach into the API types directly.
export type { LineageDivergence };
