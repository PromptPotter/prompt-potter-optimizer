"use client";
// The served genealogy: one keyed store over `/tree`, `course -> candidate -> course` at any depth.
// Reach a node through `lineage-candidates.ts::indexLineage`, never a bare cycle_id (inner ids
// collide across sandboxes) nor a label. RUNS are served on the live node only, so an id-keyed
// lookup must skip `superseded_by` rather than rely on iteration order.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
} from "react";
import { failureKind, fetchLineageTree } from "@/lib/api";
import { ABORT_LENS_LABELS } from "@/lib/api/types.generated";
import type { LineageNode } from "@/lib/api/types";
import { reportIncident } from "@/lib/diagnostics";
import { indexLineage, type LineageIndex } from "@/lib/derivations";
import { createRegistry, type TreeFetchOpts } from "@/lib/lineage-registry";
import { lensOf, useScoringMask } from "@/components/shell/mask/scoring-mask";
import { useScoringMaskSeed } from "@/components/shell/mask/useCycleEvaluators";
import { useAuthGate } from "@/lib/auth-context";
import { useDebounced } from "@/lib/hooks/useDebounced";
import { usePoll } from "@/lib/hooks/usePoll";
import { encodeCyclePath, rootCycleId, type CyclePath } from "@/lib/ids";
import { useRevalidation } from "@/lib/revalidate";
import { useSelection } from "@/lib/SelectionContext";

// The server validator is the subtree's ledger mtime, which bumps the moment a candidate is
// minted, so no proxy for "the dashboard moved" is needed.
const POLL_MS = 5000;

const LENS_LABELS: Record<string, string> = {
  "score:accuracy": "Accuracy",
  ...Object.fromEntries(
    Object.entries(ABORT_LENS_LABELS).map(([variant, label]) => [`abort:${variant}`, label]),
  ),
};

export interface CampaignTree {
  root: LineageNode | null;
  loaded: boolean;
  // A course whose tree could not be read is not a course that never ran.
  failed: boolean;
}

const EMPTY: CampaignTree = { root: null, loaded: false, failed: false };

export interface ViewedLineage {
  tree: LineageNode | null;
  index: LineageIndex;
  // "" (off), "score:<formula>", or "abort:<variant>"; the open scoring-mask panel overrides it.
  lens: string;
  setLens: (lens: string) => void;
  maskActive: boolean;
  maskLabel: string;
  scoringMaskActive: boolean;
}

interface LineageData {
  entries: Map<string, CampaignTree>;
  // A key with no subscribers is not polled, so a dead campaign's loop cannot outlive its row.
  subscribe: (key: string, path: CyclePath, opts?: TreeFetchOpts) => () => void;
  viewedAddr: string | null;
  viewedKey: string | null;
}

// Two contexts: `entries` changes identity whenever ANY subscribed tree lands, and the viewed-only
// surfaces must not re-render for a sidebar row's refetch.
const LineageDataContext = createContext<LineageData | null>(null);
const ViewedLineageContext = createContext<ViewedLineage | null>(null);

// One function, so a subscriber and the fetcher can never disagree about what they named.
function treeKey(path: CyclePath, lens: string | null, samples: string): string {
  const addr = encodeCyclePath(path);
  return lens || samples ? `${addr}|${lens ?? ""}|${samples}` : addr;
}

export function LineageProvider({
  campaignId,
  cycleId,
  children,
}: {
  campaignId: string | null;
  cycleId: string | null;
  children: React.ReactNode;
}) {
  const { authed, onAuthError } = useAuthGate();
  const reval = useRevalidation();

  const [lens, setLens] = useState<string>("");
  // This provider lives at the shell root, so a lens would otherwise leak across campaigns.
  const [prevCampaign, setPrevCampaign] = useState(campaignId);
  if (campaignId !== prevCampaign) {
    setPrevCampaign(campaignId);
    setLens("");
  }

  // Seeded here, where the mask becomes a fetch key: seeded later, a cycle's first tree goes out
  // unmasked.
  useScoringMaskSeed();
  const { open: maskOpen, mask } = useScoringMask();
  const { sampleSet } = useSelection();
  const samplesParam = useMemo(
    () => (sampleSet && sampleSet.length > 0 ? sampleSet : null),
    [sampleSet],
  );
  const samplesKey = samplesParam ? samplesParam.join(",") : "";
  const liveMaskLens = useMemo(() => (maskOpen ? lensOf(mask) : null), [maskOpen, mask]);
  const maskLens = useDebounced(liveMaskLens, 250);
  const lensParam = useMemo(() => {
    if (maskOpen) return maskLens;
    return lens || null;
  }, [maskOpen, maskLens, lens]);
  const maskLabel = maskOpen
    ? "Scoring mask"
    : samplesParam
      ? "Sample set"
      : (LENS_LABELS[lens] ?? "");

  // Keyed on the derived root id, not `cycleId`: a same-campaign cycle switch must keep this
  // array's identity, or the self-subscribe below drops the refcount and the registry deletes the
  // body + ETag it just fetched.
  const rootId = cycleId ? rootCycleId(cycleId) : null;
  const viewedPath = useMemo<CyclePath | null>(() => {
    if (!campaignId || !rootId) return null;
    return [{ campaignId, cycleId: rootId }];
  }, [campaignId, rootId]);
  const viewedAddr = viewedPath ? encodeCyclePath(viewedPath) : null;
  const viewedKey = viewedPath ? treeKey(viewedPath, lensParam, samplesKey) : null;

  const [entries, setEntries] = useState<Map<string, CampaignTree>>(() => new Map());

  // External state because its writers are mount/unmount effects (`lib/lineage-registry.ts`).
  // The last subscriber leaving drops the body too, or it grows as the operator browses.
  const [registry] = useState(() =>
    createRegistry((key) => {
      setEntries((prev) => {
        if (!prev.has(key)) return prev;
        const next = new Map(prev);
        next.delete(key);
        return next;
      });
    }),
  );
  const subsVersion = useSyncExternalStore(
    registry.onVersionChange,
    registry.version,
    registry.version,
  );
  const subscribe = registry.subscribe;

  // Self-subscribed: this provider owns the masks, so no consumer can name the viewed key.
  useEffect(() => {
    if (!viewedKey || !viewedPath) return;
    return subscribe(viewedKey, viewedPath, { lens: lensParam, samples: samplesParam });
  }, [viewedKey, viewedPath, subscribe, lensParam, samplesParam]);

  // What to fetch is the key's own latched spec — the tick never infers it from whose key it is.
  const tick = useCallback(
    async (signal: AbortSignal, key: string) => {
      const spec = registry.spec(key);
      if (!spec) return;
      const prior = registry.etag(key);
      try {
        const res = await fetchLineageTree(spec.path, spec.opts, prior, signal);
        if (signal.aborted) return;
        // A key unsubscribed mid-flight must not be resurrected by its own response.
        if (!registry.spec(key)) return;
        if (res.kind !== "ok") return;
        registry.setEtag(key, res.validator);
        setEntries((prev) => {
          const next = new Map(prev);
          next.set(key, { root: res.data, loaded: true, failed: false });
          return next;
        });
      } catch (e) {
        if (signal.aborted) return;
        onAuthError(e);
        reportIncident(e, { surface: "lineage", address: key });
        // Retire the key, but never move the VIEW: the dashboard read owns that verdict, and a
        // subscriber here may be another campaign's sidebar row.
        if (failureKind(e) === "gone") registry.markGone(key);
        // Or the next attempt would 304 into a tree it never received.
        registry.setEtag(key, null);
        setEntries((prev) => {
          if (!registry.spec(key)) return prev;
          // Keep a last-good body; report FAILED only when there is nothing to keep.
          if (prev.get(key)?.loaded) return prev;
          const next = new Map(prev);
          next.set(key, { ...EMPTY, failed: true });
          return next;
        });
      }
    },
    // Mask changes reach the poll as a re-keyed subscription, never through this identity.
    [registry, onAuthError],
  );

  usePoll(tick, {
    intervalMs: POLL_MS,
    keys: registry.liveKeys,
    tickOnFocus: true,
    enabled: authed,
    revalidateOn: subsVersion + reval,
  });

  const viewedTree = viewedKey ? (entries.get(viewedKey)?.root ?? null) : null;
  const index = useMemo(() => indexLineage(viewedTree), [viewedTree]);

  // Only when the served tree carries a divergence — not merely because a lens is requested.
  const maskActive = useMemo(() => {
    for (const { candidates } of index.values()) {
      if (candidates.some((c) => c.divergence !== null || c.divergent)) return true;
    }
    return false;
  }, [index]);

  const viewed = useMemo<ViewedLineage>(
    () => ({
      tree: viewedTree,
      index,
      lens,
      setLens,
      maskActive,
      maskLabel,
      scoringMaskActive: maskOpen,
    }),
    [viewedTree, index, lens, maskActive, maskLabel, maskOpen],
  );

  const data = useMemo<LineageData>(
    () => ({ entries, subscribe, viewedAddr, viewedKey }),
    [entries, subscribe, viewedAddr, viewedKey],
  );

  return (
    <LineageDataContext.Provider value={data}>
      <ViewedLineageContext.Provider value={viewed}>{children}</ViewedLineageContext.Provider>
    </LineageDataContext.Provider>
  );
}

function useLineageData(): LineageData {
  const ctx = useContext(LineageDataContext);
  if (ctx === null) throw new Error("useLineage* must be used within a LineageProvider");
  return ctx;
}

export function useViewedLineage(): ViewedLineage {
  const ctx = useContext(ViewedLineageContext);
  if (ctx === null) throw new Error("useLineage* must be used within a LineageProvider");
  return ctx;
}

export function useLineageTree(path: CyclePath, enabled: boolean): CampaignTree {
  const { entries, subscribe, viewedAddr, viewedKey } = useLineageData();
  const addr = encodeCyclePath(path);
  // The VIEWED campaign rides its mask-carrying entry: a masked body is a strict superset.
  const key = enabled ? (addr === viewedAddr && viewedKey ? viewedKey : addr) : null;

  useEffect(() => {
    if (key === null) return;
    // Keyed off `key`, not the caller's `path`, which is rebuilt every render.
    return subscribe(key, path);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, subscribe]);

  if (key === null) return EMPTY;
  const entry = entries.get(key);
  if (!entry) return EMPTY;
  return { root: entry.root, loaded: entry.loaded, failed: entry.failed };
}

// The marker rides the round's WINNER (the candidate the lens would have replaced); every
// candidate of a counterfactual round carries `divergent`.
export function divergenceRoundsFor(
  index: LineageIndex,
  path: CyclePath | null,
): { points: ReadonlySet<number>; subtree: ReadonlySet<number> } {
  const points = new Set<number>();
  const subtree = new Set<number>();
  if (!path) return { points, subtree };
  // Addressed, not scanned: inner cycle ids repeat across sandboxes.
  const course = index.get(encodeCyclePath(path))?.course;
  for (const cand of course?.children ?? []) {
    if (cand.round == null) continue;
    if (cand.divergence) points.add(cand.round);
    if (cand.divergent) subtree.add(cand.round);
  }
  return { points, subtree };
}
