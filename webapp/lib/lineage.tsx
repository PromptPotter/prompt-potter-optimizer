"use client";

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
import { useAuthGate } from "@/lib/auth-context";
import { useDebounced } from "@/lib/hooks/useDebounced";
import { usePoll } from "@/lib/hooks/usePoll";
import { encodeCyclePath, rootCycleId, type CyclePath } from "@/lib/ids";
import { useRevalidation } from "@/lib/revalidate";
import { useSelection } from "@/lib/SelectionContext";

// One cadence for every subscribed tree. The server validator is the subtree's ledger mtime,
// which bumps the moment a candidate is minted, so a tick sees an in-flight candidate at its
// start and costs a 304 the rest of the time — no proxy for "the dashboard moved" is needed.
const POLL_MS = 5000;

// Short label per preset lens for the mask tag.
// The abort half comes from the served vocabulary. This list was the SECOND hand-authored copy —
// three of four variants, and abbreviating two of the labels, so the same lens read as "No ε-elim"
// in the chip and "No ε-elimination" in the picker that set it.
const LENS_LABELS: Record<string, string> = {
  "score:accuracy": "Accuracy",
  ...Object.fromEntries(
    Object.entries(ABORT_LENS_LABELS).map(([variant, label]) => [`abort:${variant}`, label]),
  ),
};

export interface CampaignTree {
  // The root course of the requested path, whole. Null until the first fetch resolves.
  root: LineageNode | null;
  loaded: boolean;
  // The fetch resolved but FAILED — load-bearing, and distinct from `loaded` with no rows:
  // a course whose tree couldn't be read is not a course that never ran.
  failed: boolean;
}

const EMPTY: CampaignTree = { root: null, loaded: false, failed: false };

export interface ViewedLineage {
  // THE served genealogy, rooted at the viewed campaign's root course.
  tree: LineageNode | null;
  // The tree's address index (`indexLineage`), built once per served tree — the surfaces'
  // per-render lookups ride this instead of re-walking with `candidatesAtPath`.
  index: LineageIndex;
  // The PRESET lens dropdown value: "" (off), "score:<formula>", or "abort:<variant>".
  // The scoring-mask panel overrides it as the master when open.
  lens: string;
  setLens: (lens: string) => void;
  // A mask actually produced a divergence (drives the red tag) + its short label;
  // whether the scoring-mask panel is the active master (preset dropdown overridden).
  maskActive: boolean;
  maskLabel: string;
  scoringMaskActive: boolean;
}

interface LineageData {
  entries: Map<string, CampaignTree>;
  // Ref-counted interest per key. A key with no subscribers is not polled — that is what
  // keeps a collapsed sidebar free, and what stops a dead campaign's loop from outliving
  // the row that opened it.
  subscribe: (key: string, path: CyclePath, opts?: TreeFetchOpts) => () => void;
  // The viewed campaign's bare address and its mask-carrying key, so a consumer that names
  // the same tree by address can ride the viewed entry (see `useLineageTree`).
  viewedAddr: string | null;
  viewedKey: string | null;
}

// Two contexts, one provider: `entries` changes identity every time ANY subscribed tree
// lands, and the viewed-only surfaces (the bars, the forest, the mask tag) must not
// re-render for a sidebar row's refetch. They read the second context, whose value moves
// only when the viewed tree or its mask state does.
const LineageDataContext = createContext<LineageData | null>(null);
const ViewedLineageContext = createContext<ViewedLineage | null>(null);

// The viewed campaign's key carries its mask; every other key is the bare address. Kept in
// one function so a subscriber and the fetcher can never disagree about what they named.
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
  // Lens is a campaign-level view choice; reset it when the campaign changes so a preset /
  // scoring-mask selection can't leak across campaigns (this provider lives at the shell root,
  // persisting across cycle + tab switches).
  const [prevCampaign, setPrevCampaign] = useState(campaignId);
  if (campaignId !== prevCampaign) {
    setPrevCampaign(campaignId);
    setLens("");
  }

  // The scoring-mask panel is the master: when open, the tree follows its live mask (the SAME
  // criterion as the bars), debounced so a continuous drag doesn't spam the fetch.
  const { open: maskOpen, mask } = useScoringMask();
  // The fixed sample-set chip is ALSO a mask: re-score accuracy over only those ids
  // backend-side. Serialized to a stable string so a same-content set doesn't re-fire.
  const { sampleSet } = useSelection();
  const samplesParam = useMemo(
    () => (sampleSet && sampleSet.length > 0 ? sampleSet : null),
    [sampleSet],
  );
  const samplesKey = samplesParam ? samplesParam.join(",") : "";
  const liveMaskLens = useMemo(() => (maskOpen ? lensOf(mask) : null), [maskOpen, mask]);
  const maskLens = useDebounced(liveMaskLens, 250);
  // One lens value drives the fetch: the scoring mask's own when its panel is master, else the
  // preset dropdown (already a `score:`/`abort:` value). `lensOf` is the single place either
  // spelling is minted.
  const lensParam = useMemo(() => {
    if (maskOpen) return maskLens;
    return lens || null;
  }, [maskOpen, maskLens, lens]);
  const maskLabel = maskOpen
    ? "Scoring mask"
    : samplesParam
      ? "Sample set"
      : (LENS_LABELS[lens] ?? "");

  // The viewed tree is rooted at the campaign's ROOT course, and the server's recursion
  // reaches every fork below it — so selecting a fork must not re-root (and re-fetch) it.
  // The memo keys on the derived root id, not `cycleId`: a same-campaign cycle switch must
  // keep the array's IDENTITY too, or the self-subscribe effect below re-runs, drops the
  // key's refcount to zero, and the registry deletes the body + ETag it just fetched.
  const rootId = cycleId ? rootCycleId(cycleId) : null;
  const viewedPath = useMemo<CyclePath | null>(() => {
    if (!campaignId || !rootId) return null;
    return [{ campaignId, cycleId: rootId }];
  }, [campaignId, rootId]);
  const viewedAddr = viewedPath ? encodeCyclePath(viewedPath) : null;
  const viewedKey = viewedPath ? treeKey(viewedPath, lensParam, samplesKey) : null;

  const [entries, setEntries] = useState<Map<string, CampaignTree>>(() => new Map());

  // The registry is EXTERNAL state read through `useSyncExternalStore`, because its writers
  // are mount/unmount effects — setState from an effect body is the cascading-render
  // pattern React rejects. Mechanics + invariants: `lib/lineage-registry.ts`.
  // Last subscriber left: drop the body along with the subscription. Keeping it would grow
  // unboundedly as the operator browses campaigns, and the next open refetches in one tick.
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

  // The provider self-subscribes the viewed key: it owns the masks, so no consumer can
  // name that key correctly on its own. The spec it latches is what the tick fetches.
  useEffect(() => {
    if (!viewedKey || !viewedPath) return;
    return subscribe(viewedKey, viewedPath, { lens: lensParam, samples: samplesParam });
  }, [viewedKey, viewedPath, subscribe, lensParam, samplesParam]);

  // ONE loop, but every subscribed key runs and skips on its own — a tree slower than the
  // interval delays nothing but itself. Each replays its own ETag, so a quiescent campaign
  // costs one conditional request per interval and no body. What to fetch is the key's own
  // latched spec — the tick never infers it from whose key this is.
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
        // 304 — the body we hold is still current; nothing to re-render, and the etag
        // we sent is what proved it, so it stays untouched.
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
        // 404 — this tree's address does not exist. Retire the key so the tick
        // stops asking; a transient failure keeps retrying, which is why the
        // classification and not the bare catch decides. Whether the VIEW moves
        // is not this poll's call: the dashboard read owns that verdict, and a
        // subscriber here may be some other campaign's sidebar row.
        if (failureKind(e) === "gone") registry.markGone(key);
        // A failed tick invalidates the validator: the next attempt must ask for a
        // full body rather than 304 into a tree it never received.
        registry.setEtag(key, null);
        setEntries((prev) => {
          if (!registry.spec(key)) return prev;
          // Keep a last-good body; report FAILED only when there is nothing to keep.
          // `loaded` with no tree would put a fetch error where a measurement goes.
          if (prev.get(key)?.loaded) return prev;
          const next = new Map(prev);
          next.set(key, { ...EMPTY, failed: true });
          return next;
        });
      }
    },
    // Mask changes reach the poll as a re-keyed subscription (a registry version bump),
    // never through this callback's identity.
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
  // The ONE walk over the viewed tree. Every candidate appears under its own address
  // exactly once, so derivations over "all candidates" iterate the index too.
  const index = useMemo(() => indexLineage(viewedTree), [viewedTree]);

  // "Active" (red tag) only when the served tree actually carries a divergence — NOT merely
  // because a lens/chip is requested.
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

// The VIEWED campaign's tree plus its mask state — the surfaces that render the
// counterfactual (the bars, the forest, the mask tag).
export function useViewedLineage(): ViewedLineage {
  const ctx = useContext(ViewedLineageContext);
  if (ctx === null) throw new Error("useLineage* must be used within a LineageProvider");
  return ctx;
}

// ANY campaign's tree, by address. Subscribing is what starts its poll; `enabled` false
// (a collapsed sidebar row) subscribes to nothing and fetches nothing.
export function useLineageTree(path: CyclePath, enabled: boolean): CampaignTree {
  const { entries, subscribe, viewedAddr, viewedKey } = useLineageData();
  const addr = encodeCyclePath(path);
  // An address naming the VIEWED campaign rides its mask-carrying entry instead of opening
  // a second fetch of the same tree — a masked body is a strict superset (module header),
  // so everything an address-only consumer reads is present either way.
  const key = enabled ? (addr === viewedAddr && viewedKey ? viewedKey : addr) : null;

  useEffect(() => {
    if (key === null) return;
    // Rebuild the path from the key's own address so the effect doesn't depend on the
    // caller's array identity (`path` is rebuilt every render).
    return subscribe(key, path);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, subscribe]);

  if (key === null) return EMPTY;
  const entry = entries.get(key);
  if (!entry) return EMPTY;
  return { root: entry.root, loaded: entry.loaded, failed: entry.failed };
}

// Per-course divergence facts for the round axis — which rounds of `path` are a divergence
// point vs. inside the counterfactual subtree. Pure derivation over the served tree;
// surfaces share it so they mark identically. The marker rides the round's WINNER (the
// candidate the lens would have replaced), and every candidate of a counterfactual round
// carries `divergent`.
export function divergenceRoundsFor(
  index: LineageIndex,
  path: CyclePath | null,
): { points: ReadonlySet<number>; subtree: ReadonlySet<number> } {
  const points = new Set<number>();
  const subtree = new Set<number>();
  if (!path) return { points, subtree };
  // Addressed, not scanned: a bare cycle id names more than one course (inner ids repeat
  // across sandboxes), and `find` would answer with whichever came first.
  const course = index.get(encodeCyclePath(path))?.course;
  for (const cand of course?.children ?? []) {
    if (cand.round == null) continue;
    if (cand.divergence) points.add(cand.round);
    if (cand.divergent) subtree.add(cand.round);
  }
  return { points, subtree };
}
