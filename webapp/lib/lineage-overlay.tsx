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

import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import { fetchCampaignLineage } from "@/lib/api";
import type { CampaignLineageResponse, LineageDivergence } from "@/lib/api/types";
import { liveCandidateId } from "@/lib/candidate-label";
import { useFitnessState } from "@/components/whatif/fitness-store";
import { formulaFromWeights } from "@/components/whatif/fitness-bars";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useDebounced } from "@/lib/hooks/useDebounced";
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
  // Each candidate's fitness under the active `score:` lens — served, never recomputed
  // here (R-36). Key `{cycle_id}::{candidate_id|liveId}`, the SAME candidate identity the
  // lineage tree and fitness bars resolve by. Empty without a `score:` lens (e.g. What-If
  // closed), so a consumer reads null and falls back to its default series.
  lensValueByCandidate: ReadonlyMap<string, number>;
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
  // Lens is a campaign-level view choice; reset it when the campaign changes so a
  // preset / What-If selection can't leak across campaigns (the provider now lives
  // at the shell root, persisting across cycle + tab switches).
  const [prevCampaign, setPrevCampaign] = useState(campaignId);
  if (campaignId !== prevCampaign) {
    setPrevCampaign(campaignId);
    setLens("");
  }
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
  // same-campaign cycle switch returns identical data. `lensParam`/`samplesParam`
  // ARE deps: they change the served overlay. Refetch on any mutation (fork,
  // cleanup, lifecycle) via the shared revalidation seam.
  const reval = useRevalidation();
  // Revalidate on the SAME signal the live header moves on — a round closing or a
  // phase flip — so the tree never lags the 2 s dashboard poll. A quiescent stretch
  // changes no key, so it makes no request; this provider sits under
  // CycleStreamProvider, so `dash` is available here.
  const { dash, dashRound, runPhaseResolved } = useDashboard();
  const dashChangeKey = `${dash?.rounds?.length ?? 0}:${dashRound ?? -1}:${runPhaseResolved ?? ""}`;

  const [data, setData] = useState<CampaignLineageResponse | null>(null);
  // Query identity = campaign + lens + samples. A change is a DIFFERENT served
  // body (different URL), so drop the prior tree in the same render before the
  // refetch lands (render-phase guarded reset, webapp/CLAUDE.md).
  const queryKey = `${campaignId ?? ""}|${lensParam ?? ""}|${samplesKey}`;
  const [prevQueryKey, setPrevQueryKey] = useState(queryKey);
  if (queryKey !== prevQueryKey) {
    setPrevQueryKey(queryKey);
    setData(null);
  }

  // Last-Modified validator, keyed to the query identity so it's only reused
  // within the same body — a new query (campaign/lens/samples) fetches fresh.
  const lastModifiedRef = useRef<{ key: string; value: string | null }>({ key: "", value: null });
  useEffect(() => {
    if (!campaignId) return;
    const ac = new AbortController();
    let cancelled = false;
    const ims = lastModifiedRef.current.key === queryKey ? lastModifiedRef.current.value : null;
    fetchCampaignLineage(campaignId, lensParam, samplesParam, ims, ac.signal)
      .then((res) => {
        if (cancelled) return;
        lastModifiedRef.current = { key: queryKey, value: res.lastModified ?? ims };
        // 304 keeps the current tree — a quiescent revalidation costs nothing.
        if (res.kind === "ok") setData(res.data);
      })
      .catch(() => {
        // Transient/aborted — keep the last good tree rather than blanking it.
      });
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [campaignId, queryKey, lensParam, samplesParam, tick, reval, dashChangeKey]);

  // Served overlay → lookup structures keyed by `{cycle_id}::r{round}`.
  const divergenceByKey = useMemo(() => {
    const m = new Map<string, string | null>();
    for (const d of data?.divergences ?? []) m.set(d.node_key, d.alternative_candidate_id);
    return m;
  }, [data]);
  const divergentKeys = useMemo(() => new Set(data?.divergent ?? []), [data]);
  // Served per-candidate lens value → lookup keyed by `{cycle_id}::{candidate_id|liveId}`.
  // Candidate identity is resolved EXACTLY as the tree (useLineage) and the bars
  // (round-candidates) resolve it — real id, else the position-derived live id — so all
  // three surfaces key one candidate one way. Only non-null values land (a candidate the
  // formula can't score is absent, read as null downstream).
  const lensValueByCandidate = useMemo(() => {
    const m = new Map<string, number>();
    for (const cyc of data?.cycles ?? []) {
      for (const r of cyc.rounds) {
        r.candidates.forEach((cand, i) => {
          if (cand.lens_value == null) return;
          const id = cand.candidate_id || liveCandidateId(r.round, i);
          m.set(`${cyc.cycle_id}::${id}`, cand.lens_value);
        });
      }
    }
    return m;
  }, [data]);
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
      lensValueByCandidate,
    }),
    [
      data,
      lens,
      maskActive,
      maskLabel,
      showWhatIf,
      divergenceByKey,
      divergentKeys,
      lensValueByCandidate,
    ],
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
